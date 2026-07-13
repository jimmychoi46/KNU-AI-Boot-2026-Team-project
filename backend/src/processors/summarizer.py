import json
import logging
import re
from decimal import Decimal, InvalidOperation

import openai

# LLM_fn.py에 이미 구현해 둔 요약 Agent / QA Agent / 관련성 Agent / 길이 프리셋 / 안전 로그 포맷터를 그대로 재사용한다.
from LLM_fn import _summarize_agent, _qa_agent, _relevance_agent, _translate_categories, _safe_error_str, LENGTH_PRESETS

logger = logging.getLogger(__name__)


# ----------------------------------------------------
# 백엔드가 넘겨준 '정제된 뉴스 아이템 리스트'를 LLM 프롬프트용 텍스트로 변환
# (raw 네이버 API 응답이 아니라, 이미 태그/엔티티 제거·날짜 파싱까지 끝난 상태)
# ----------------------------------------------------
def _build_context(cleaned_items):
    cleaned_text = ""
    original_links = []

    for idx, item in enumerate(cleaned_items, 1):
        title = item.get("title", "")
        description = item.get("description", "")
        link = item.get("link", "")
        published_at = item.get("published_at", "")

        cleaned_text += (
            f"[{idx}번 뉴스]\n제목: {title}\n내용: {description}\n"
            f"발행일: {published_at}\n링크: {link}\n\n"
        )
        if link:
            original_links.append(link)

    return cleaned_text, original_links


# 숫자 토큰: 정수부 + 선택적 천단위 콤마(,\d{3}) + 선택적 소수부. 콤마는 '천단위 구분'일 때만
# 한 토큰으로 묶고(7,400), '3,4,5' 같은 나열은 3·4·5 개별 숫자로 분리해 잡는다(오탐 방지).
_NUM_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _canon_num(m):
    """숫자 토큰을 대조용으로 정규화해 '같은 값'을 같은 문자열로 만든다: 천단위 콤마 제거
    (7,400==7400), 양끝 점 제거, 정수는 앞자리 0 제거('07'==7 — 발행일 ISO '07'을 요약이
    '7월'로 인용해도 접지), 소수는 값 기준 정규화(3.5==3.50, 2.0==2)로 표기 차이 오탐을 막는다."""
    n = m.replace(",", "").strip(".")
    if not n:
        return None
    if "." in n:
        try:
            return format(Decimal(n).normalize(), "f")  # 3.50→3.5, 2.0→2, 10.0→10 (지수표기 방지)
        except InvalidOperation:
            return n
    return n.lstrip("0") or "0"


def _source_numbers(items):
    """원문(제목+내용+발행일)에 실제로 등장하는 숫자 토큰의 집합. 요약이 이 집합에 없는 수치를
    쓰면 환각으로 본다(콤마 무시: 7,400==7400).

    링크 무결성(_validate_links)이 '원문에 없는 링크'를 코드로 제거하듯, '원문에 없는 숫자'를
    코드로 잡는 결정적 백스톱이다(프롬프트 규칙 5-1 과 이중 방어). '부분 문자열'이 아니라 '토큰
    일치'로 대조한다 — 예전엔 원문 숫자열에 부분 문자열이 있으면 통과시켜, 실재하는 큰 수(27400)
    의 일부(740)를 지어낸 것을 놓쳤다(거짓 음성). 발행일의 날짜(연도·월·일)도 근거에 포함해,
    프롬프트에 근거로 준 발행일을 요약이 인용해도 거짓 양성이 나지 않게 한다.
    """
    nums = set()
    for it in items:
        # 발행일은 날짜(YYYY-MM-DD)까지만 근거로 삼는다. 전체 ISO 타임스탬프(…T09:00:00+09:00)를
        # 통째로 넣으면 시·분·초·시간대(00~59 임의의 두 자리 수)까지 근거 집합에 섞여, 그 값과
        # 우연히 겹치는 환각 수치를 통과시켜(거짓 음성) 백스톱이 약해진다.
        pub_date = str(it.get("published_at", ""))[:10]
        blob = f"{it.get('title', '')} {it.get('description', '')} {pub_date}"
        for m in _NUM_RE.findall(blob):
            c = _canon_num(m)
            if c:
                nums.add(c)
    return nums


def _ungrounded_numbers(text, src_numbers):
    """text 의 숫자 토큰 중 원문 숫자 집합(src_numbers)에 없는 것 — 환각 수치 후보를 반환."""
    bad = []
    for m in _NUM_RE.findall(text or ""):
        c = _canon_num(m)
        if c and c not in src_numbers:
            bad.append(m)
    return bad


def _relevant_items(keyword, items):
    """items 중 keyword 가 가리키는 '주제'에 실제로 관한 기사만 남긴다(LLM 판정).

    실패(키 없음·API 오류·형식 오류) 시엔 필터 없이 원본을 그대로 둔다(fail-open) — 관련성
    판정은 품질 개선이지 정확성 보장이 아니라, 여기서 막히면 정상 발송까지 못 하는 게 더 나쁘다.
    또 '전부 무관' 판정은 오판(모델 오작동·프롬프트 경계 케이스) 가능성이 커, 이 경우도 원본을
    유지해 빈 뉴스레터를 막는다. 무엇을 왜 뺐는지는 로그로 남긴다(조용한 누락 금지).
    """
    if not items:
        return list(items)
    try:
        keep = _relevance_agent(keyword, items)
    except Exception as exc:  # 키 없음(RuntimeError)·API 오류·JSON 파싱 오류 등 → 필터 없이 진행
        logger.warning(f"[관련성 필터] '{keyword}' 판정 실패 — 필터 없이 진행: {_safe_error_str(exc)}")
        return list(items)  # 원본 객체가 아니라 사본을 돌려줘 호출부의 in-place 변형이 원본을 오염시키지 않게 한다
    kept = [it for i, it in enumerate(items, 1) if i in keep]
    dropped = [it.get("title", "") for i, it in enumerate(items, 1) if i not in keep]
    if not kept:
        # 판정이 모두 무관이면(그 키워드에 오늘 진짜 관련 뉴스가 없음) 빈 결과를 그대로 돌려준다 —
        # 무관 기사로 채우느니 그 키워드는 이번에 안 보내는 게 낫다(파이프라인이 '뉴스 없음'으로 건너뜀).
        # 'MBTI'처럼 언급만 되는 키워드는 관련 뉴스가 아예 없는 날이 흔하다. LLM 오작동 가능성도 있어
        # 원인 파악용으로 경고를 남긴다(조용한 전량 누락 방지). 단, 판정 '실패'(위 except)와는 구분한다.
        logger.warning(f"[관련성 필터] '{keyword}' 모든 기사가 주제와 무관 → 이 키워드는 이번 발송에서 제외({len(items)}건)")
        return []
    if dropped:
        logger.info(f"[관련성 필터] '{keyword}' 주제와 무관해 {len(dropped)}건 제외: {dropped}")
    return kept


def filter_relevant(collected):
    """{query: [cleaned_item]} 에서 각 키워드 '주제'와 무관한 기사를 걸러낸다.

    수집(naver_news.collect)과 요약/저장 사이에 두는 정제 단계. 네이버 뉴스 검색은 본문 문자열을
    매칭하므로, 'LOL'처럼 중의적인 키워드(게임 '리그 오브 레전드' vs 슬랭 '웃기다')로 수집하면
    그 문자열이 슬랭·약어로 쓰인 무관 기사가 섞여 들어온다 — 이를 키워드 단위로 걸러낸다.
    키워드마다 독립 판정하며, 한 키워드의 판정 실패가 다른 키워드를 막지 않는다(_relevant_items 가 fail-open).
    입력과 같은 {query: [item]} 형태를 반환한다(호출부가 그대로 이어 쓸 수 있게).
    """
    return {keyword: _relevant_items(keyword, items) for keyword, items in collected.items()}


def translate_categories(keywords, language):
    """카테고리(구독 키워드) 태그를 language 로 번역한 {원키워드: 번역} 을 돌려준다.

    뉴스 본문은 요약 단계에서 그 언어로 나오지만, 카테고리 태그로 쓰는 키워드는 원문(예: '환율')이라
    영어 구독자 메일에도 한국어 태그가 남는다 — 이를 번역해 render 의 category_labels 로 넘긴다.
    한국어이거나(번역 불필요) 키워드가 없으면 빈 dict. 번역 실패(키 없음·API·형식 오류)도 빈 dict 로
    fail-open — 번역이 안 되면 render 가 원 키워드를 그대로 태그로 쓴다.
    """
    kws = [k for k in (keywords or []) if k]
    if not kws or language == "한국어":
        return {}
    try:
        return {k: v for k, v in _translate_categories(kws, language).items() if k in kws}
    except Exception as exc:  # 키 없음(RuntimeError)·API·JSON 오류 등 → 원 키워드 유지
        logger.warning(f"[카테고리 번역] '{language}' 실패 — 원 키워드 유지: {_safe_error_str(exc)}")
        return {}


def summarize(collected, summary_length, language):
    """정제된 뉴스를 요약·편집한다.

    [인터페이스 계약] — 구현 시 아래 입출력 형태를 지켜주세요.
        args:
            collected(dict): {query(str): [cleaned_item(dict), ...]}
                cleaned_item: {"title", "link", "description", "published_at"}
                (백엔드가 이미 태그·엔티티 제거, 날짜 파싱까지 마친 상태)
            summary_length(str): config.SUMMARY_LENGTH 중 하나 ("짧게"/"중간"/"길게")
                — 요약 문장 길이/분량을 이 값에 맞춰야 한다.
            language(str): config.LANGUAGE 중 하나 ("한국어"/"영어")
                — headline/summary 를 이 언어로 작성해야 한다.
                (원문이 한국어 뉴스라도 language="영어" 면 영어로 번역·요약)
        returns:
            dict: 렌더러(기획/데이터)가 소비할 구조. 예)
                {query(str): [{"headline": str, "topic": str, "topic_summary": str, "link": str}, ...]}

    백엔드는 구독자마다 다른 summary_length/language 조합에 대해 이 함수를 별도로 호출하고,
    결과를 그 조합 전용으로 저장한다(같은 기사도 조합마다 다른 요약이 남는다).

    내부 동작: query별로 [요약 Agent] -> [편집/QA Agent] -> [링크 무결성 필터링]을 거쳐
    이슈 단위 결과를 인터페이스가 요구하는 '행(row) 단위' 리스트로 펼쳐서 반환한다.
    """
    if summary_length not in LENGTH_PRESETS:
        logger.warning(f"알 수 없는 summary_length '{summary_length}' → '중간'으로 대체합니다.")
        summary_length = "중간"
    sentence_range = LENGTH_PRESETS[summary_length]

    result = {}

    for query, items in collected.items():
        news_context, original_links = _build_context(items)
        # 숫자 환각 방지용 원문 숫자 집합(제목+내용+발행일) — 아래 행 생성 시 근거 대조에 쓴다.
        src_numbers = _source_numbers(items)

        if not news_context:
            logger.warning(f"'{query}' 쿼리에 대해 처리할 뉴스가 없습니다.")
            result[query] = []
            continue

        # ---------------- Agent 1: 요약 ----------------
        # openai.OpenAIError/JSONDecodeError/TypeError(빈 응답)로 좁혀서, 이 코드 자체의
        # 버그(NameError 등)까지 "API 실패"로 오인되어 조용히 삼켜지지 않도록 한다.
        try:
            draft_text = _summarize_agent(news_context, language, sentence_range)
            draft_json = json.loads(draft_text)
        except (openai.OpenAIError, json.JSONDecodeError, TypeError) as e:
            logger.error(f"[요약 Agent] '{query}' 처리 실패: {_safe_error_str(e)}")
            result[query] = []
            continue

        # ---------------- Agent 2: 편집/QA ----------------
        # 요약 Agent 와 같은 실패 유형(빈 응답 → json.loads(None) 의 TypeError 포함)을 잡아,
        # QA 실패는 이 쿼리만 초안으로 대체하고 다음 쿼리로 넘어가게 한다. TypeError 를 빼면
        # 빈 응답 하나가 summarize() 밖으로 전파돼 그 호출의 나머지 쿼리·구독자 발송까지 막는다.
        try:
            final_issues, qa_report = _qa_agent(draft_json, original_links, language, sentence_range,
                                                source_text=news_context)
        except (openai.OpenAIError, json.JSONDecodeError, TypeError) as e:
            logger.error(f"[QA Agent] '{query}' 실행 실패, 요약 Agent 초안으로 대체합니다: {_safe_error_str(e)}")
            # draft_json 이 dict 가 아니면(모델이 JSON 배열 등을 반환) .get 이 AttributeError 를 던져
            # 좁은 except 를 빠져나가 쿼리 격리가 깨진다 — dict 일 때만 issues 를 꺼낸다.
            final_issues = draft_json.get("issues", []) if isinstance(draft_json, dict) else []
            qa_report = []

        if qa_report:
            logger.info(f"[QA Agent] '{query}' 수정 내역: {qa_report}")

        # LLM 이 유효 JSON 으로 issues 를 null/비리스트로 주는 경우(모델이 필드를 비우는 흔한 케이스)가 있다.
        # 그대로 순회하면 아래 for 가 TypeError 를 던지는데, 이 for 는 쿼리 루프의 try 밖이라 예외가
        # summarize() 전체를 죽여(격리 실패) 그 구독자의 다른 키워드/조합 발송까지 막는다. 리스트가
        # 아니면 이 쿼리만 빈 결과로 처리한다.
        if not isinstance(final_issues, list):
            logger.warning(f"'{query}' - issues 가 리스트가 아니라 이 쿼리를 빈 결과로 처리합니다: {final_issues!r}")
            final_issues = []

        # ---------------- 행(row) 단위로 펼치기 + 링크 무결성 최종 필터링 ----------------
        rows = []
        for issue in final_issues:
            if not isinstance(issue, dict):
                logger.warning(f"'{query}' - 이슈가 예상된 dict 형태가 아니라 건너뜁니다: {issue!r}")
                continue
            # headline/topic/summary 가 JSON null 이면 None 이, 숫자면 int/float 가 흘러든다 —
            # DB NOT NULL INSERT 실패(IntegrityError로 요약 배치 중단)·렌더의 html.escape(None) 크래시,
            # 그리고 아래 '문자열 + 문자열' 근거 검사에서의 TypeError 를 막으려 문자열로 정규화한다.
            headline = str(issue.get("headline") or "")
            topics = issue.get("topics", [])
            articles = issue.get("articles", [])

            # LLM 응답은 json_object 형식만 보장할 뿐 스키마는 보장하지 않는다 — topics가
            # dict 리스트가 아니면(예: 문자열 리스트) 아래 topic.get(...)에서 AttributeError로
            # 이슈 전체를 물귀신처럼 끌고 내려가지 않도록 여기서 걸러낸다.
            if not isinstance(topics, list) or any(not isinstance(t, dict) for t in topics):
                logger.warning(f"'{query}' - '{headline}' 이슈의 topics가 예상된 형태가 아니라 건너뜁니다: {topics!r}")
                continue

            if not isinstance(articles, list):
                articles = []

            # 원본에 실제로 존재하는 링크만 남기고, 지어낸/교차 매칭된 링크는 제거
            valid_links = [link for link in articles if link in original_links]
            if len(valid_links) < len(articles):
                logger.warning(
                    f"'{query}' - '{headline}' 이슈에서 원본에 없는 링크를 제거했습니다: "
                    f"{[l for l in articles if l not in original_links]}"
                )

            if not topics:
                continue

            # articles 는 이슈 단위(최대 3개)로 온다. 대표 링크 1개만 남기면 나머지가
            # 버려지므로, 이슈의 모든 주제(topic)마다 유효 링크 전체를 행으로 펼친다
            # (db.group_digest_rows 가 같은 headline/topic 의 행들을 링크 리스트로 다시 묶는다).
            for topic in topics:
                topic_summary = str(topic.get("summary") or "")   # 숫자/None 이 와도 아래 근거 검사·렌더에서 안전하게
                subtitle = str(topic.get("subtitle") or "")
                # 숫자 근거 백스톱 — 주제 제목·요약·이슈 제목 어디든 원문에 없는 수치가 있으면 그 topic 을 버린다.
                # subtitle 은 카드 제목(뉴스_제목)으로 독자에게 노출되므로 반드시 함께 검사한다.
                # 잘못된 숫자를 보내느니 그 항목을 누락하는 편이 낫다(가독성 기준 ①정확성: 자동 발송 금지).
                ungrounded = _ungrounded_numbers(subtitle + " " + topic_summary + " " + headline, src_numbers)
                if ungrounded:
                    logger.warning(
                        f"'{query}' - '{headline}' 요약에 원문에 없는 수치 {ungrounded} 감지 → 이 topic 제외(환각 방지)")
                    continue
                for link in (valid_links or [""]):
                    rows.append({
                        "headline": headline,
                        "topic": subtitle,
                        "topic_summary": topic_summary,
                        "link": link,
                    })

        result[query] = rows

    return result
