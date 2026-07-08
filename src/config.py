"""환경 변수 불러오기 & 상수 정의

1. .env 파일의 값(환경 변수)을 읽어 변수에 저장, 각 모듈에서 공통으로 사용하도록 한다.
2. 시스템 동작에 필요한 '고정' 상수(선택 가능한 키워드 후보, 시간대 등)만 관리한다.
   - 사용자가 고르는 값(개인 키워드/발송 시간)은 상수가 아니라 '구독' 데이터로 관리한다.
     → data/subscriptions.json (프론트의 대시보드가 생성/수정, src/subscriptions.py 로 로드)
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

# 2-1. api.py (구독자 API) 관리자 인증용 — 프론트가 이 값을 헤더로 보내야 GET /subscribers 허용.
#      미설정(None/빈 문자열)이면 관리자 엔드포인트는 항상 거부한다(안전 기본값).
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# 2-2. api.py 가 이메일 확인(더블 옵트인) 링크를 만들 때 쓰는 이 서비스의 외부 주소.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


# 3. 고정 상수 정의

# 추천 키워드 — 프론트 자동완성/예시용 (검증용 아님).
#   키워드는 프론트에서 '자유 입력'이라 백엔드가 후보로 제한하지 않는다.
#   (구독 저장 시엔 공백/빈값/중복 정리만 한다 — subscriptions._clean_keywords)
SUGGESTED_KEYWORDS = ["주식", "금리", "환율", "코인"]

# 메일 발송 주기 정의
FREQUENCY = ["매일", "주 3회", "매주"]
# 발송 요일 (frequency별) — Python weekday(): 월=0 … 일=6
#   프론트에 요일 선택 UI가 생기기 전까지 쓰는 고정 규칙(팀 합의로 변경 가능).
#   '창(window)'과 '발송 여부'가 모두 이 요일에서 파생된다.
FREQUENCY_WEEKDAYS = {
    "매일": {0, 1, 2, 3, 4, 5, 6},
    "주 3회": {0, 2, 4},   # 월·수·금
    "매주": {0},           # 월요일
}
# 요약 길이 (LLM 파트에 전달해야 할 데이터)
SUMMARY_LENGTH = ["짧게", "중간", "길게"]
# 검색 시 출력할 뉴스 수
NEWS_DISPLAY = 5
# 수집 기간 필터: 최근 몇 시간 이내 뉴스를 저장할지.
#   발송 창은 구독자 주기별로 dispatch 에서 다시 자르므로, 수집은 '가장 긴 주기'(매주=168h)를
#   커버하도록 넉넉히 둔다. (articles 는 30분마다 INSERT OR IGNORE 로 누적되므로 이력도 쌓인다)
RECENCY_HOURS = 24 * 7
# 언어: 기사 수집 언어? 요약 작성 언어? (현행 프론트에는 한국어, 영어만 존재)
LANGUAGE = ["한국어", "영어"]

# 3-2) 시간대 (KST)
TIMEZONE = "Asia/Seoul"

# 3-3) 긴급(속보) 감지 설정 — 시간이 아니라 '이벤트' 기반 트리거의 임계값
#      결합 규칙: (A: 긴급 키워드) AND (B: 물량 급증)  또는  (강한 B 단독)
MONITOR_INTERVAL_MINUTES = 10   # 감시 주기 & 급증 카운트 구간(분)
MONITOR_DISPLAY = 100           # 급증 판정용 수집 건수 (네이버 max 100)

# 긴급 메일 발송 후보 키워드 정의 (실제 뉴스 수집 시 추가/수정 필요)

BREAKING_KEYWORDS = ["급락", "폭락", "상장폐지", "서킷브레이커", "디폴트", "횡령", "감자"]

# 급등 기준 정의

SURGE_BASELINE_WINDOWS = 6      # 기준선 계산에 쓸 과거 구간 수
SURGE_FACTOR = 3.0              # 3배 이상이면 급증(B)
STRONG_SURGE_FACTOR = 6.0       # 6배 이상이면 키워드(A) 없이도 발송
SURGE_MIN_COUNT = 5             # 최소 표본(저물량 노이즈 방지)                
EMERGENCY_COOLDOWN_MINUTES = 60 # 구독자당 긴급 재발송 최소 간격

# 3-4) 외부 API 요청 안정성 (타임아웃/재시도)
HTTP_TIMEOUT = (5, 10)   # (연결, 응답) 타임아웃 초 — 무한 대기 방지(스케줄러 멈춤 방지)
HTTP_MAX_RETRIES = 3     # 일시적 실패(연결 오류/429/5xx) 재시도 횟수
HTTP_BACKOFF = 0.5       # 재시도 간 지수 백오프 계수(0.5 → 0.5s, 1s, 2s...)


# 4. 구독자 데이터 경로 (프론트의 대시보드가 채우는 저장소)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBSCRIPTIONS_PATH = os.path.join(BASE_DIR, "data", "subscriptions.json")

# 5. 뉴스/요약 저장용 DB (SQLite) 경로
#    - 수집 잡: 정제한 뉴스를 articles 테이블에 저장
#    - 요약 잡: articles 를 조회해 LLM이 이슈→주제로 재구성 → digests 계층 테이블에 저장
#    - 발송 잡: digests 를 조회해 렌더링 후 이메일 발송
DB_PATH = os.path.join(BASE_DIR, "data", "newsletter.db")

# 6. 배치 잡 실행 주기(분) — 수집/요약은 주기적으로, 발송은 분 단위 디스패처(별도)
COLLECT_INTERVAL_MINUTES = 30      # 뉴스 수집 주기
SUMMARIZE_INTERVAL_MINUTES = 30    # 요약 처리 주기
# 발송 시 '최근 N시간 내 다이제스트'만 뉴스레터에 포함 (오래된 스냅샷 재발송 방지)
SUMMARY_RECENCY_HOURS = 24
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               