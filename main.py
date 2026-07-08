"""스케줄러 진입점 — 정기 발송(3단계 배치) + 속보 감시.

담당: 백엔드.
네 개의 잡을 돌린다.
  ① collect_job       매 N분 → 구독 키워드 뉴스 수집·정제 → DB(articles) 저장
  ② summarize_job     매 N분 → 구독 조합별 최근 기사 조회 → LLM이 이슈→주제로 요약 → DB(digests) 저장
  ③ dispatch_job      매 분  → 발송 대상 구독자에게 DB의 최근 요약을 렌더링해 발송
  ④ monitor_breaking  매 N분 → '속보' 감시 → 감지 시 시간과 무관하게 긴급 발송 (이벤트 기반)
모든 잡은 매 실행마다 구독 저장소를 새로 읽으므로, 대시보드 변경이 즉시 반영된다.

실행:  python main.py
"""
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from src import breaking, config, db
from src.collectors import naver_news
from src.pipeline import collect_job, dispatch_job, send_breaking_alert, summarize_job
from src.subscriptions import import_from_json, load_subscriptions


def monitor_breaking():
    """긴급 수신 동의 구독자들의 키워드를 감시해 속보를 감지, 즉시 발송."""
    now = datetime.now(pytz.timezone(config.TIMEZONE))
    subs = [s for s in load_subscriptions() if s.emergency_opt_in]
    if not subs:
        return

    keywords = sorted({kw for s in subs for kw in s.keywords})
    # 급증 카운트가 죽지 않도록 근접 중복은 유지(dedupe_flag=False)
    collected = naver_news.collect(keywords, display=config.MONITOR_DISPLAY, now=now, dedupe_flag=False)

    for event in breaking.detect(collected, now):
        if not breaking.is_new_event(event):   # 같은 사건 재발송 방지
            continue
        targets = [
            s for s in subs
            if event["keyword"] in s.keywords and not breaking.in_cooldown(s.email, now)
        ]
        if not targets:
            continue
        print(f"[속보 감지] {event['keyword']} (x{event['factor']}) → {len(targets)}명")
        for sub in targets:
            try:
                send_breaking_alert(sub, event)
                breaking.mark_sent(sub.email, now)
            except Exception as exc:
                print(f"[긴급 발송 실패] {sub.email}: {exc}")


def main():
    db.init_db()  # 첫 실행 시 테이블 생성 (이미 있으면 그대로 둠)

    # 구독자가 DB 에 하나도 없고 기존 JSON 이 있으면 최초 1회만 시드(이관).
    # 이후엔 DB 가 원천이라, JSON 을 지워도/그대로 둬도 DB 상태가 덮이지 않는다.
    if db.count_subscribers() == 0:
        seeded = import_from_json()
        if seeded:
            print(f"[구독자 시드] 기존 JSON에서 {seeded}명 → DB")

    scheduler = BlockingScheduler(timezone=pytz.timezone(config.TIMEZONE))
    scheduler.add_job(
        collect_job, "interval", minutes=config.COLLECT_INTERVAL_MINUTES
    )  # 수집: 매 N분
    scheduler.add_job(
        summarize_job, "interval", minutes=config.SUMMARIZE_INTERVAL_MINUTES
    )  # 요약: 매 N분
    scheduler.add_job(dispatch_job, "cron", minute="*")  # 발송: 매 분
    scheduler.add_job(
        monitor_breaking, "interval", minutes=config.MONITOR_INTERVAL_MINUTES
    )  # 속보: 매 N분
    print(
        f"스케줄러 시작 ({config.TIMEZONE}): "
        f"수집=매 {config.COLLECT_INTERVAL_MINUTES}분, "
        f"요약=매 {config.SUMMARIZE_INTERVAL_MINUTES}분, "
        f"발송=매 분, 속보 감시=매 {config.MONITOR_INTERVAL_MINUTES}분"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("스케줄러 종료")


if __name__ == "__main__":
    main()
