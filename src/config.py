"""환경 변수 불러오기 & 상수 정의

1. .env 파일의 값(환경 변수)을 읽어 변수에 저장, 각 모듈에서 공통으로 사용하도록 한다.
2. 시스템 동작 과정에서 사용될 상수(검색어, 수신자, 스케줄 시각 등)도 여기서 한 번에 관리한다.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 1. naver_news.py (네이버 검색 API) 사용을 위한 환경변수 불러오기
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 2. send_email.py (이메일 전송) 동작을 위한 환경변수 불러오기
EMAIL_SENDER = os.getenv("SENDER")
GOOGLE_APP_PASSWORD = os.getenv("GOOGLE_APP_PASSWORD")


# 3. 상수 정의(파이프라인 동작 중 사용)

# 3-1) 수집할 검색어 목록 (예시)
SEARCH_QUERIES = ["주식", "금리", "환율"]
# 검색 시 출력할 뉴스 수
NEWS_DISPLAY = 10
# 메일 수신자 목록(추가 예정)
EMAIL_RECIPIENTS = []

# 3-2) 스케줄러 동작을 위한 상수
TIMEZONE = "Asia/Seoul"   # 시간대 설정(KST)
SCHEDULE_HOUR = 8         # 실행 시각(시)
SCHEDULE_MINUTE = 0       # 실행 시각(분)
