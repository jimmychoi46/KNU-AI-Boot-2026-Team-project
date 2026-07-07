"""스케줄러 진입점.

매일 지정한 시각(KST)에 파이프라인을 자동 실행한다.
기존 scheduler_test.py 의 로직을 실제 작업(run_pipeline)에 연결한 것.

실행:  python main.py
"""
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from src import config
from src.pipeline import run_pipeline


def main():
    scheduler = BlockingScheduler(timezone=pytz.timezone(config.TIMEZONE))
    scheduler.add_job(
        run_pipeline,
        "cron",
        hour=config.SCHEDULE_HOUR,
        minute=config.SCHEDULE_MINUTE,
    )
    print(
        f"스케줄러 시작: 매일 {config.SCHEDULE_HOUR:02d}:{config.SCHEDULE_MINUTE:02d} "
        f"({config.TIMEZONE}) 파이프라인 실행"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("스케줄러 종료")


if __name__ == "__main__":
    main()
