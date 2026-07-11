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

# 2-3. 셀프서비스(본인 조회/수정/구독취소) 전 본인 확인 코드의 유효 시간(분).
#      정수가 아닌 값이 들어와도 import 단계에서 전체 기동이 막히지 않게 방어적으로 파싱한다.
def _int_env(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[설정 경고] {name}='{raw}' 는 정수가 아니라 기본값 {default}을 사용합니다")
        return default


# 0/음수 TTL 은 코드를 발급 즉시(또는 과거로) 만료시켜 본인확인을 조용히 깨뜨리므로,
# 양수가 아니면 기본값으로 되돌린다.
_ttl = _int_env("ACCESS_CODE_TTL_MINUTES", 15)
ACCESS_CODE_TTL_MINUTES = _ttl if _ttl > 0 else 15

# 2-4. report.py 가 뉴스레터 메일 하단(구독취소/발송 설정)에 넣을 프론트엔드(Streamlit) 주소.
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:8501")

# 2-5. CORS 허용 출처 — 프론트가 React 등 브라우저 SPA로 바뀌면 브라우저가 교차 출처 요청을 막으므로
#      백엔드가 허용 출처를 열어줘야 한다. 지금 프론트(Streamlit)는 서버 대 서버 호출이라 실제로는
#      필요 없지만, 나중을 대비해 미리 켜 둔다. .env의 CORS_ORIGINS(콤마 구분)로 조정하고, 기본값은
#      로컬 React 개발 서버(CRA 3000 / Vite 5173)다.
def _dedupe(seq):
    """순서를 보존하며 중복을 제거한다."""
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


CORS_ORIGINS = _dedupe(
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
    if o.strip()
)

# 2-6. report.py 가 메일 상단/하단에 넣는 뉴스레터 표시 이름.
NEWSLETTER_NAME = "트렌드 뉴스레터"


# 3. 고정 상수 정의

# 추천 키워드 — 프론트 자동완성/예시용 (검증용 아님).
#   키워드는 프론트에서 '자유 입력'이라 백엔드가 후보로 제한하지 않는다.
#   (구독 저장 시엔 공백/빈값/중복 정리만 한다 — subscriptions._clean_keywords)
SUGGESTED_KEYWORDS = ["주식", "금리", "환율", "코인"]

# 메일 발송 주기 정의
FREQUENCY = ["매일", "주 3회", "매주"]
# 발송 요일 (frequency별) — Python weekday(): 월=0 … 일=6
#   프론트에 요일 선택 UI가 생기기 전까지 쓰는 고정 규칙(팀 합의로 변경 가능).
#   '포함 기간'과 '발송 여부'가 모두 이 요일에서 파생된다.
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
#   발송 포함 기간은 구독자 주기별로 dispatch 에서 다시 자르므로, 수집은 '가장 긴 주기'(매주=168h)를
#   커버하도록 넉넉히 둔다. (articles 는 30분마다 INSERT OR IGNORE 로 누적되므로 이력도 쌓인다)
RECENCY_HOURS = 24 * 7
# 언어: 기사 수집 언어? 요약 작성 언어? (현행 프론트에는 한국어, 영어만 존재)
LANGUAGE = ["한국어", "영어"]

# 3-2) 시간대 (KST)
TIMEZONE = "Asia/Seoul"

# 3-4) 외부 API 요청 안정성 (타임아웃/재시도)
HTTP_TIMEOUT = (5, 10)   # (연결, 응답) 타임아웃 초 — 무한 대기 방지(스케줄러 멈춤 방지)
SMTP_TIMEOUT = 15        # Gmail SMTP 소켓 타임아웃 초 — 무응답 시 발송 스레드가 무한 대기하며
                         #   BlockingScheduler(max_instances=1)의 발송 잡과 동기 API를 막는 것 방지
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

# 7. 주간 트렌드 키워드 — 다이제스트 이력을 얼마나 보존/집계할지
#    보존 기간은 집계 기간(TREND_LOOKBACK_HOURS, 7일)보다 하루 여유를 둬서, 정리 잡과
#    집계 잡의 실행 시점이 살짝 어긋나도 경계의 다이제스트가 이미 지워져 있는 일이 없게 한다.
DIGEST_RECENCY_HOURS = 24 * 8
TREND_LOOKBACK_HOURS = 24 * 7
TREND_TOP_N = 5

# 8. 재발송 방지 — 구독자별로 이미 받은 기사(링크)를 기록해 다음 발송에서 뺀다.
#    보존 기간은 가장 긴 발송 간격(매주=7일)보다 길어야 지난주에 보낸 기사가 이번 주에 다시 안 나간다.
SENT_ARTICLE_RETENTION_HOURS = 24 * 8
# 근접 중복(같은 안건) 방지 — 링크가 달라도 제목+본문 스니펫(description)이 거의 같은 기사(전재·경미한 수정)를
#   같은 것으로 보고 다음 발송에서 뺀다. 기사 SimHash(문자 3-gram, 64비트)의 Hamming 거리가
#   이 값 이하면 근접 중복. 보정 관측: 전재/경미수정본은 0~11, '같은 사건이라도 독립적으로 쓴
#   기사'·다른 사건은 26 이상으로 뚜렷이 갈린다(문자 3-gram이라 실제 텍스트 재사용이 없으면
#   거의 랜덤 거리). 그 사이(12)를 임계값으로 둬서 전재본은 잡되 독립 기사 오합병(진짜 새 뉴스
#   누락)은 피한다. 음수로 두면 근접 중복을 끄고 완전 일치(URL)만 본다.
NEAR_DUP_HAMMING_MAX = 12
# 트렌드 첨부 요일은 상수로 두지 않고 subscriptions.is_weekly_anchor 가 각 구독자의
# FREQUENCY_WEEKDAYS 에서 '이번 주 첫 발송 요일'을 직접 도출한다(발송 요일 규칙과 자동 일치).
