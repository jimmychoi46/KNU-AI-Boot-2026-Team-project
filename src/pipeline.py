from datetime import datetime, timedelta

import pytz

from src import config, db
from src.collectors import naver_news    # 백엔드
from src.processors import summarizer     # LLM/Agent (요약·편집)
from src.renderers import report          # 기획/데이터 (템플릿·렌더링)
from src.notifiers import send_email      # 백엔드
from src.subscriptions import due_subscribers, is_weekly_anchor, load_subscriptions, send_window_hours


def run_for_subscriber(sub):
    """구독자 sub 의 키워드로 뉴스를 만들어 그의 메일로 발송한다.

    args:
        sub(Subscription): email / keywords / send_hour / send_minute
    """
    # 0. 보낼 게 있는지 먼저 확인 — 빈 메일 발송 방지 + 이메일 미확인자는 발송 금지
    if not sub.confirmed:
        print(f"[발송 건너뜀] {sub.email}: 이메일 미확인")
        return
    if not sub.keywords:
        print(f"[발송 건너뜀] {sub.email}: 선택된 키워드 없음")
        return

    # 1. 수집 (백엔드) — 구독자가 고른 키워드로만
    collected = naver_news.collect(sub.keywords, config.NEWS_DISPLAY)
    if not any(collected.values()):
        print(f"[발송 건너뜀] {sub.email}: 최근 뉴스 없음")
        return

    # 2. 요약·편집 (LLM/Agent 인터페이스) — 구독자가 고른 길이/언어로.
    #    summarizer 는 평평한(flat) 행을 주므로, 렌더러에 넘기기 전에 이슈→주제 계층으로 묶는다
    #    (DB 경유 발송과 동일한 규칙 — db.group_digest_rows 를 공유해서 일관성 유지).
    #    LLM 응답 스키마가 어긋나는 등 예상 밖 실패가 이 한 명의 발송만 막고 API 요청
    #    전체를 죽이지 않도록 격리한다(summarize_job의 배치 격리와 동일한 원칙).
    try:
        flat = summarizer.summarize(collected, sub.summary_length, sub.language)
    except Exception as exc:
        print(f"[발송 건너뜀] {sub.email}: 요약 실패 ({exc})")
        return
    digests = {kw: db.group_digest_rows(rows) for kw, rows in flat.items() if rows}

    # 3. 렌더링 (기획/데이터 인터페이스)
    body_html = report.render(digests)

    # 4. 발송 (백엔드)
    send_email.send_email(
        sub.email,
        subject=f"[데일리] 오늘의 {sub.keywords} 뉴스 브리핑",
        body_html=body_html,
    )
    print(f"[발송 완료] {sub.email} ({sub.send_hour:02d}:{sub.send_minute:02d})")


def send_breaking_alert(sub, event):
    """감지된 속보 event 를 구독자 sub 에게 시간과 무관하게 긴급 발송한다.

    정기 발송과 동일한 요약·렌더링 파이프라인을 재사용하되, 제목만 [긴급]으로 구분한다.
    args:
        sub(Subscription): 수신자
        event(dict): breaking.detect() 가 만든 이벤트 (keyword/items/... 포함)
    """
    if not sub.confirmed:
        print(f"[긴급 발송 건너뜀] {sub.email}: 이메일 미확인")
        return
    try:
        flat = summarizer.summarize({event["keyword"]: event["items"]}, sub.summary_length, sub.language)
    except Exception as exc:
        print(f"[긴급 발송 건너뜀] {sub.email}: 요약 실패 ({exc})")
        return
    digests = {kw: db.group_digest_rows(rows) for kw, rows in flat.items() if rows}
    body_html = report.render(digests)
    send_email.send_email(
        sub.email,
        subject=f"[긴급] {event['keyword']} 속보",
        body_html=body_html,
    )
    print(f"[긴급 발송] {sub.email} ← {event['keyword']} (x{event['factor']})")


# ─────────────────────────────────────────────────────────────
# 정기 발송 배치 잡 (DB 를 사이에 둔 3단계)
# ─────────────────────────────────────────────────────────────

def collect_job(now=None):
    """① 수집 잡 — 전체 구독자가 고른 키워드의 뉴스를 수집·정제해 DB(articles)에 저장.

    구독자별로 따로 수집하지 않고, 모든 구독자의 키워드를 합쳐(중복 제거) 한 번에 수집한다
    (같은 키워드를 여러 구독자가 골라도 API 호출·저장은 1회).
    이메일 미확인 구독자는 제외한다 — 확인 전 주소를 위해 API 호출·저장을 낭비하지 않는다.
    저장 직후 RECENCY_HOURS 보다 오래된 기사를 정리한다 — articles 가 무한히 쌓이지
    않도록(digests 가 조합당 최신 1건만 남기는 것과 같은 이유의 자체 정리).
    returns: 새로 저장된 기사 수.
    """
    db.prune_sent_articles(now=now)  # 재발송 방지 기록 정리는 구독자/키워드 유무와 무관하게 항상
    subs = [s for s in load_subscriptions() if s.confirmed]
    keywords = sorted({kw for s in subs for kw in s.keywords})
    if not keywords:
        print("[수집 잡] 대상 키워드 없음")
        return 0
    collected = naver_news.collect(keywords, config.NEWS_DISPLAY, now=now)
    saved = db.save_articles(collected, now=now)
    pruned = db.prune_old_articles(now=now)
    print(f"[수집 잡] 키워드 {len(keywords)}개 → 신규 기사 {saved}건 저장, 오래된 기사 {pruned}건 정리")
    return saved


def summarize_job(now=None):
    """② 요약 잡 — 구독자가 실제 구독한 (키워드, 요약 길이, 언어) 조합마다 최근 기사를
    모아 LLM에게 이슈→주제 단위로 요약시키고, 그 결과를 새 다이제스트로 저장.

    기존 '기사 1건 = 요약 1건' 모델과 달리, LLM이 여러 기사를 묶어 이슈/주제로
    재구성하므로 '아직 요약 안 된 기사'라는 개념이 없다. 매 실행마다 그 키워드에
    보유 중인 기사 전체를 다시 넘겨 새 다이제스트 스냅샷을 만든다 — 오래된 스냅샷은
    발송 시 포함 기간으로 걸러진다(db.fetch_digests_for_keywords).
    조합 수는 실제 구독 중인 (키워드, summary_length, language) 조합만큼 — 보통 적다.
    이메일 미확인 구독자는 제외한다.
    returns: 새로 생성된 다이제스트 수.
    """
    triples = sorted({
        (kw, s.summary_length, s.language)
        for s in load_subscriptions() if s.confirmed for kw in s.keywords
    })
    if not triples:
        return 0

    created = 0
    for keyword, summary_length, language in triples:
        articles = db.fetch_articles_for_keyword(keyword, now=now)
        if not articles:
            continue
        collected = {keyword: articles}
        try:
            summarized = summarizer.summarize(collected, summary_length, language)
        except Exception as exc:  # 한 조합의 LLM 실패가 나머지 조합 요약을 막지 않도록 격리
            print(f"[요약 실패] {keyword} ({summary_length}/{language}): {exc}")
            continue
        # 이 스냅샷이 담은 기사 중 가장 최근 발행일 — 발송 시 '새 기사가 있을 때만' 보내는 판정에 쓴다.
        latest_article_at = max((a.get("published_at") for a in articles if a.get("published_at")),
                                default=None)
        digest_id = db.save_digest(keyword, summary_length, language,
                                    summarized.get(keyword, []), now=now,
                                    latest_article_at=latest_article_at)
        if digest_id is not None:
            created += 1
            print(f"[요약 잡] {keyword} ({summary_length}/{language}) → "
                  f"다이제스트 #{digest_id} 생성 (기사 {len(articles)}건)")
    pruned = db.prune_old_digests(now=now)
    if pruned:
        print(f"[요약 잡] 오래된 다이제스트 {pruned}건 정리")
    return created


def dispatch_job(now=None):
    """③ 발송 잡 — 지금 발송할 구독자에게 DB의 최근 요약을 렌더링해 이메일 발송.

    분 단위 디스패처로 돌리며, 매 실행마다 구독 저장소를 새로 읽어 대시보드 변경을 즉시 반영한다.
    """
    now = now or datetime.now(pytz.timezone(config.TIMEZONE))
    subs = load_subscriptions()
    due = due_subscribers(subs, now)
    if not due:
        return
    print(f"[{now:%H:%M}] 발송 잡 대상 {len(due)}명")
    trend_cache = {}  # 이번 실행 동안 키워드별 트렌드 결과 공유(구독자 간 동일 쿼리 반복 방지)
    for sub in due:
        try:
            dispatch_one(sub, now=now, trend_cache=trend_cache)
        except Exception as exc:  # 한 명 실패가 나머지 발송을 막지 않도록 격리
            print(f"[발송 실패] {sub.email}: {exc}")


def weekly_trend_articles_for(keywords, now, language=None, cache=None):
    """keywords 각각의 최근 TREND_LOOKBACK_HOURS 이내 상위 트렌드 topic을 요약·관련 기사와 함께 모은다.

    키워드 나열이 아니라 '토픽 + 요약 + 관련 기사'를 담아 별도 주간 메일로 보내기 위한 데이터.
    관련 기사는 그 주 다이제스트에 이미 저장된 것을 재사용한다(추가 수집/LLM 없음).
    language 를 주면 그 언어 다이제스트만 집계해, 구독자가 고른 언어의 topic·요약만 나가게 한다.
    빈 결과(topic 이력 없음)인 키워드는 제외한다.
    cache(dict, optional): 같은 dispatch_job 실행 안에서 여러 구독자가 공유하는 (키워드,언어) 조합의
        집계 결과를 재사용해 동일 쿼리 반복을 막는다(없는 조합도 캐시에 저장). 언어가 달라도 안 섞이게
        캐시 키에 언어를 포함한다.
    returns: {keyword: [{"topic", "days", "summary", "links"}, ...]} (트렌드 있는 키워드만).
    """
    since = now - timedelta(hours=config.TREND_LOOKBACK_HOURS)
    result = {}
    for kw in keywords:
        ckey = (kw, language)
        if cache is not None and ckey in cache:
            topics = cache[ckey]
        else:
            topics = db.get_top_topic_articles(kw, since, language=language)
            if cache is not None:
                cache[ckey] = topics
        if topics:
            result[kw] = topics
    return result


def _drop_seen_articles(digests, email):
    """digests 에서 이 구독자가 이미 받은 기사를 뺀다(재발송 방지). '이미 받음'은 완전 일치 링크뿐
    아니라 근접 중복(링크가 달라도 제목+요약이 거의 같은 같은 안건, db.fetch_seen_or_similar)까지 본다.

    링크가 있던 topic 의 링크가 전부 이미 본 것이면 그 topic(=이미 읽은 뉴스)을 통째로 뺀다.
    topic 이 다 빠진 issue, issue 가 다 빠진 keyword 도 제거한다(링크 없는 요약 topic 은 유지).
    to_send 는 대표 링크(links[0])만이 아니라 그 topic 의 fresh 링크 '전부'를 담는다 — 하나의 topic 은
    같은 사건을 다룬 기사 묶음이라, 요약이 그 기사들을 다 소화해 전달한 것으로 보고 기사 단위로 '받음'
    처리한다(대표 링크만 기록하면 같은 사건이 다음 날 다른 멤버 URL로 재발송되어 '새 것만' 취지가 깨진다).
    returns: (걸러진 digests, 이번에 새로 나갈 링크 리스트)  — 링크 리스트는 발송 성공 후 발송 내역에 기록.
    """
    all_links = [
        link for issues in digests.values() for issue in issues
        for topic in issue.get("topics", []) for link in (topic.get("links") or [])
    ]
    if not all_links:
        return digests, []
    seen = db.fetch_seen_or_similar(email, all_links)  # 완전 일치 + 근접 중복(같은 안건)
    filtered = {}
    to_send = []
    for keyword, issues in digests.items():
        new_issues = []
        for issue in issues:
            new_topics = []
            for topic in issue.get("topics", []):
                orig = topic.get("links") or []
                if not orig:
                    new_topics.append(topic)   # 링크 없는 요약은 dedup 대상 아님 — 유지
                    continue
                fresh = [link for link in orig if link not in seen]
                if not fresh:
                    continue                   # 링크가 전부 이미 본 것 → 이미 읽은 뉴스, 통째 제외
                to_send.extend(fresh)
                new_topics.append({**topic, "links": fresh})
            if new_topics:
                new_issues.append({**issue, "topics": new_topics})
        if new_issues:
            filtered[keyword] = new_issues
    return filtered, to_send


def dispatch_one(sub, now=None, trend_cache=None):
    """구독자 한 명에게 DB의 최신 다이제스트(일간)와, 앵커일이면 주간 트렌드(별도 메일)를 발송한다.

    되돌아보는 기간은 구독자 주기(frequency)에 따라 다르다
    — 지난 발송 이후 소식을 커버하도록 send_window_hours 로 정한다.
    다이제스트는 구독자가 고른 summary_length/language 조합으로 생성된 것만 가져온다.
    발송일이 그 구독자의 '이번 주 첫 발송 요일'(is_weekly_anchor)이면 주기와 무관하게
    주간 트렌드 키워드를 '토픽+요약+관련 기사'로 담아 일간과 '별도의 메일'로 함께 보낸다
    — 발송 요일 규칙(FREQUENCY_WEEKDAYS)에서 직접 도출하므로 규칙이 바뀌어도 자동으로 따라간다.
    한 번의 슬롯 선점(claim_dispatch)으로 이 틱의 일간·주간 발송을 함께 묶어 중복을 막는다.
    일간 본문에서는 이 구독자가 지난 발송에 이미 받은 기사를 빼고(재발송 방지), 새 기사가 하나도
    없으면 일간을 보내지 않는다 — "매일 새 것만" 약속. 주간 트렌드(회고성)는 이 필터 대상이 아니다.
    trend_cache(dict, optional): dispatch_job 이 넘기는 키워드별 트렌드 캐시(중복 쿼리 방지).
    args:
        sub(Subscription): 수신자 (email / keywords / send_hour / send_minute / frequency
                            / summary_length / language)
    """
    if now is None:
        now = datetime.now(pytz.timezone(config.TIMEZONE))
    hours = send_window_hours(sub, now)
    digests = db.fetch_digests_for_keywords(
        sub.keywords, sub.summary_length, sub.language, now=now, hours=hours
    )
    digests, sent_links = _drop_seen_articles(digests, sub.email)  # 이미 본 기사 제거
    trends = (weekly_trend_articles_for(sub.keywords, now, language=sub.language, cache=trend_cache)
              if is_weekly_anchor(sub, now) else {})
    if not digests and not trends:
        print(f"[발송 건너뜀] {sub.email}: 새 기사 없음")
        return
    if not db.claim_dispatch(sub.email, now):
        # 같은 틱에 두 프로세스(예: 롤링 배포 중 신·구 프로세스 겹침)가 함께 돌 때
        # 정확히 한 쪽만 이 슬롯을 선점해 발송한다(원자적 조건부 UPDATE로 레이스 제거).
        print(f"[발송 건너뜀] {sub.email}: 방금 전 이미 발송함(중복 방지)")
        return
    # 일간과 주간(별도 메일)을 각각 격리한다 — 하나의 claim 으로 묶여 있어도, 앞선 일간 발송이
    # SMTP 일시 오류로 실패했다고 뒤의 주간 트렌드 메일까지 통째로 못 나가는 일이 없도록.
    if digests:
        try:
            send_email.send_email(
                sub.email,
                subject="[데일리] 오늘의 금융 뉴스 브리핑",
                body_html=report.render(digests, now=now),
            )
        except Exception as exc:
            print(f"[발송 실패] {sub.email} 일간: {exc}")
        else:
            # 발송 성공 후에만 '받음'으로 기록한다. 기록(DB 쓰기)만 실패해도 메일은 이미 나갔으므로
            # '발송 실패'로 오기록하지 않고 따로 로그한다 — 그 경우 그 기사들이 다음 발송에서 한 번
            # 더 나갈 수 있지만(일시적·드묾), 발송 자체는 성사된 것이 맞다.
            try:
                db.record_sent_articles(sub.email, sent_links, now)
            except Exception as exc:
                print(f"[발송 내역 기록 실패] {sub.email}: {exc} (일부 기사가 다음 발송에서 재발송될 수 있음)")
            print(f"[발송 완료] {sub.email} ({sub.send_hour:02d}:{sub.send_minute:02d})")
    if trends:
        try:
            send_email.send_email(
                sub.email,
                subject="[주간 트렌드] 이번 주 키워드와 관련 기사",
                body_html=report.render_weekly_trend(trends, now=now),
            )
            print(f"[주간 트렌드 발송] {sub.email} (키워드 {len(trends)}개)")
        except Exception as exc:
            print(f"[주간 트렌드 발송 실패] {sub.email}: {exc}")
