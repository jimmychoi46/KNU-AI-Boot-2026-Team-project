from datetime import datetime

import pytz

from src import config, db
from src.collectors import naver_news    # 백엔드
from src.processors import summarizer     # LLM/Agent (요약·편집)
from src.renderers import report          # 기획/데이터 (템플릿·렌더링)
from src.notifiers import send_email      # 백엔드
from src.subscriptions import due_subscribers, load_subscriptions, send_window_hours


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
    flat = summarizer.summarize(collected, sub.summary_length, sub.language)
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
    flat = summarizer.summarize({event["keyword"]: event["items"]}, sub.summary_length, sub.language)
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
    발송 시 창(window)으로 걸러진다(db.fetch_digests_for_keywords).
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
        digest_id = db.save_digest(keyword, summary_length, language,
                                    summarized.get(keyword, []), now=now)
        if digest_id is not None:
            created += 1
            print(f"[요약 잡] {keyword} ({summary_length}/{language}) → "
                  f"다이제스트 #{digest_id} 생성 (기사 {len(articles)}건)")
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
    for sub in due:
        try:
            dispatch_one(sub, now=now)
        except Exception as exc:  # 한 명 실패가 나머지 발송을 막지 않도록 격리
            print(f"[발송 실패] {sub.email}: {exc}")


def dispatch_one(sub, now=None):
    """구독자 한 명에게 DB의 최신 다이제스트를 렌더링해 발송한다.

    되돌아보는 창(window)은 구독자 주기(frequency)에 따라 다르다
    — 지난 발송 이후 소식을 커버하도록 send_window_hours 로 정한다.
    다이제스트는 구독자가 고른 summary_length/language 조합으로 생성된 것만 가져온다.
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
    if not digests:
        print(f"[발송 건너뜀] {sub.email}: 최근 다이제스트 없음")
        return
    body_html = report.render(digests)
    send_email.send_email(
        sub.email,
        subject="[데일리] 오늘의 금융 뉴스 브리핑",
        body_html=body_html,
    )
    print(f"[발송 완료] {sub.email} ({sub.send_hour:02d}:{sub.send_minute:02d})")
