import json
import logging

# LLM_fn.py에 이미 구현해 둔 요약 Agent / QA Agent / 길이 프리셋을 그대로 재사용한다.
from LLM_fn import _summarize_agent, _qa_agent, LENGTH_PRESETS

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

        if not news_context:
            logger.warning(f"'{query}' 쿼리에 대해 처리할 뉴스가 없습니다.")
            result[query] = []
            continue

        # ---------------- Agent 1: 요약 ----------------
        try:
            draft_text = _summarize_agent(news_context, language, sentence_range)
            draft_json = json.loads(draft_text)
        except Exception as e:
            logger.error(f"[요약 Agent] '{query}' 처리 실패: {e}")
            result[query] = []
            continue

        # ---------------- Agent 2: 편집/QA ----------------
        try:
            final_issues, qa_report = _qa_agent(draft_json, original_links, language, sentence_range)
        except Exception as e:
            logger.error(f"[QA Agent] '{query}' 실행 실패, 요약 Agent 초안으로 대체합니다: {e}")
            final_issues = draft_json.get("issues", [])
            qa_report = []

        if qa_report:
            logger.info(f"[QA Agent] '{query}' 수정 내역: {qa_report}")

        # ---------------- 행(row) 단위로 펼치기 + 링크 무결성 최종 필터링 ----------------
        rows = []
        for issue in final_issues:
            headline = issue.get("headline", "")
            topics = issue.get("topics", [])
            articles = issue.get("articles", [])

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
                for link in (valid_links or [""]):
                    rows.append({
                        "headline": headline,
                        "topic": topic.get("subtitle", ""),
                        "topic_summary": topic.get("summary", ""),
                        "link": link,
                    })

        result[query] = rows

    return result
