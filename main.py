# 속보 감시는 아직 미도입 기능이라 스케줄러에 등록하지 않는다 — future/breaking.py 참고.
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from src import config, db
from src.pipeline import collect_job, dispatch_job, summarize_job
from src.subscriptions import import_from_json


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
    print(
        f"스케줄러 시작 ({config.TIMEZONE}): "
        f"수집=매 {config.COLLECT_INTERVAL_MINUTES}분, "
        f"요약=매 {config.SUMMARIZE_INTERVAL_MINUTES}분, "
        f"발송=매 분"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("스케줄러 종료")


if __name__ == "__main__":
    main()
