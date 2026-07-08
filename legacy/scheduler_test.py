from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
import pytz




def get_current_time():
    """
        스케쥴러를 통해 실행할 함수. 
        KST 기준 현재 시각을 출력하는 기능을 수행함.
    """
    kst = pytz.timezone("Asia/Seoul") # KST 시간대 설정

    now = datetime.now(kst) # KST(UTC+9) 기준 현재 시각
    current_time = now.strftime("%H:%M:%S")
    print(f"현재 시각: {current_time}")

scheduler = BlockingScheduler() # 스케쥴러 정의


# 스케쥴러에서 수행할 작업 추가
scheduler.add_job(get_current_time, 'interval', seconds=10) # 10초마다 get_current_time() [현재 시각 출력]을 수행하는지 테스트할 것.
scheduler.start()

