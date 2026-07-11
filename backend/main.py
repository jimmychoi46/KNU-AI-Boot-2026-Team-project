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
    # 공통 옵션: max_instances=1(한 잡이 겹쳐 도는 것 방지), coalesce=True(밀린 실행은 하나로 합침).
    # 발송은 분 단위라 misfire_grace_time 을 넉넉히(55s) 둬서, 한 번의 발송이 조금 길어져도 그 분의
    # 발송이 '미스파이어 1s' 기본값 때문에 통째로 버려지지 않게 한다(다음 분 대상 영구 누락 방지).
    scheduler.add_job(
        collect_job, "interval", minutes=config.COLLECT_INTERVAL_MINUTES,
        max_instances=1, coalesce=True,
    )  # 수집: 매 N분
    scheduler.add_job(
        summarize_job, "interval", minutes=config.SUMMARIZE_INTERVAL_MINUTES,
        max_instances=1, coalesce=True,
    )  # 요약: 매 N분
    scheduler.add_job(
        dispatch_job, "cron", minute="*",
        max_instances=1, coalesce=True, misfire_grace_time=55,
    )  # 발송: 매 분
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
