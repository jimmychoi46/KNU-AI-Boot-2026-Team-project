import html
import hmac
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query, Request, Response, Security
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src import config, db, subscriptions
from src.notifiers import send_email

logger = logging.getLogger(__name__)


def _mask_email(email):
    """로그에 이메일 원문을 남기지 않도록 로컬파트를 가린다 (a***@example.com)."""
    try:
        local, _, domain = str(email).partition("@")
        head = local[:1] if local else ""
        return f"{head}***@{domain}" if domain else f"{head}***"
    except Exception:
        return "***"


_admin_password_header = APIKeyHeader(name="X-Admin-Password", auto_error=False)
_access_code_header = APIKeyHeader(name="X-Access-Code", auto_error=False)

# 가입/인증코드 발송/본인확인 엔드포인트는 인증 없이 호출 가능해 무차별 대입(코드 브루트포스)·
# 메일 폭탄의 표적이 된다. IP 기준으로 속도를 제한해 두 위험을 함께 줄인다.
def _client_ip(request):
    """rate limit 키용 클라이언트 IP. 리버스 프록시가 있으면 X-Forwarded-For 의 첫 IP(실제 클라이언트),
    없으면 소켓 주소. (프록시 없는 로컬/직결에서는 소켓 주소 그대로.)"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


def _rate_key_email(request):
    """리소스(경로의 이메일) 기준 rate limit 키. Streamlit 이 백엔드를 서버-대-서버로 호출해 모든
    최종 사용자 요청의 소켓 IP 가 동일해도, 특정 이메일에 대한 시도만 그 이메일 버킷으로 제한한다 —
    (1) 남의 이메일 조회/수정/삭제·코드 요청이 사이트 전체 상한을 공유하지 않고, (2) 한 이메일의
    인증 코드 브루트포스가 여러 IP 로 분산돼도 그 이메일 기준으로 묶여 막힌다. 경로에 이메일이
    없으면 IP 로 폴백한다. 대소문자 변형으로 버킷을 우회(브루트포스를 여러 표기로 분산)하지
    못하도록 정규화한 이메일을 키로 쓴다."""
    email = request.path_params.get("email")
    return subscriptions.normalize_email(email) if email else _client_ip(request)


limiter = Limiter(key_func=_client_ip)


def _is_admin(password):
    """X-Admin-Password 값이 유효한 관리자 인증인지. ADMIN_PASSWORD 미설정 시 항상 거부(안전 기본값)."""
    if not (config.ADMIN_PASSWORD and password):
        return False
    try:
        return hmac.compare_digest(password, config.ADMIN_PASSWORD)
    except TypeError:
        # 비ASCII 문자(헤더로 온 0xE9 등)면 compare_digest 가 TypeError 를 던진다 —
        # 500 이 아니라 인증 실패(잘못된 값=불일치)로 처리한다.
        return False


def require_admin(password: str | None = Security(_admin_password_header)):
    """GET /subscribers(관리자 전용) 인증 검사 헬퍼. dependencies= 로 걸지 않고 엔드포인트 본문에서 직접 호출한다(이유는 list_subscribers docstring 참고 — @limiter.limit 이 먼저 집계해야 함).

    설정된 관리자 비밀번호와 일치하는 값이 X-Admin-Password 헤더로 와야 통과한다.
    비밀번호 비교에 hmac.compare_digest 를 써서 타이밍 공격을 막는다.
    ADMIN_PASSWORD 가 서버에 아예 설정 안 돼 있으면(운영 실수) 무엇을 보내도 거부한다
    — "설정 안 됨"을 "누구나 통과"로 취급하지 않는다(안전 기본값).
    """
    if not _is_admin(password):
        raise HTTPException(status_code=401, detail="관리자 인증 실패")


def _check_admin_or_owner(email, admin_password, access_code):
    """GET/PUT/DELETE .../{email} 용 인증: 관리자 비밀번호 또는 본인 확인 코드(실패 시 401).

    FastAPI 의 dependencies=[] 가 아니라 엔드포인트 본문 안에서 호출한다 — 그래야
    @limiter.limit 데코레이터가 요청을 먼저 집계한 뒤 이 검사가 돈다. dependencies 로
    걸면 401 이 데코레이터보다 먼저 나서 실패 요청이 rate limit 에 집계되지 않고,
    본인 확인 코드(8자리 hex, 32비트) 무차별 대입을 사실상 막지 못한다.
    """
    if _is_admin(admin_password):
        return
    if access_code and db.verify_access_code(subscriptions.normalize_email(email), access_code):
        return
    raise HTTPException(status_code=401, detail="본인 확인이 필요합니다. 인증 코드를 요청해주세요.")


# ── 요청/응답 스키마 ──────────────────────────────────────────
class SubscriberIn(BaseModel):
    """신규 구독(POST) 요청 본문."""
    email: str = Field(..., description="구독자 이메일(식별자)", examples=["user@example.com"])
    name: str = Field("", description="구독자 이름")
    keywords: list[str] = Field(
        default_factory=list,
        description="관심 키워드 (자유 입력). 저장 시 공백/빈값/중복만 정리됨",
        examples=[["주식", "금리"]],
    )
    send_hour: int = Field(..., description="발송 시각(시) 0~24 (범위 밖이면 400)")
    send_minute: int = Field(..., description="발송 시각(분) 0 또는 30 (그 외 400)")
    frequency: str | None = Field(None, description="발송 주기 (config.FREQUENCY, 미지정 시 기본값)")
    summary_length: str | None = Field(None, description="요약 길이 (config.SUMMARY_LENGTH, 미지정 시 기본값)")
    language: str | None = Field(None, description="언어 (config.LANGUAGE, 미지정 시 기본값)")


class SubscriberUpdate(BaseModel):
    """구독자 수정(PUT) 요청 본문. 경로의 이메일이 '현재' 식별자다.

    email 을 넣고 그 값이 현재 이메일과 다르면 '이메일 변경'으로 처리한다 — 새 주소로
    (미확인) 재가입 + 확인 메일 발송 + 기존 주소 삭제(POST 신규가입과 같은 규칙).
    email 을 생략하거나 현재와 같으면 제자리 수정(전체 교체)이다.
    """
    email: str | None = Field(None, description="바꿀 새 이메일(생략/현재와 동일 시 이메일 변경 없음)")
    name: str = ""
    keywords: list[str] = Field(default_factory=list)
    send_hour: int = Field(..., description="발송 시각(시) 0~24 (범위 밖이면 400)")
    send_minute: int = Field(..., description="발송 시각(분) 0 또는 30 (그 외 400)")
    frequency: str | None = None
    summary_length: str | None = None
    language: str | None = None


class SubscriberOut(BaseModel):
    """응답: 저장·정규화된 구독자."""
    email: str
    name: str
    keywords: list[str]
    send_hour: int
    send_minute: int
    frequency: str
    summary_length: str
    language: str
    confirmed: bool = Field(description="이메일 소유 확인(더블 옵트인) 여부 — 확인 전엔 발송 대상 아님")


class OptionsOut(BaseModel):
    """응답: 프론트가 선택지 드롭다운을 채울 때 쓰는 서버 쪽 정답값."""
    frequency: list[str]
    summary_length: list[str]
    language: list[str]


def _record(payload, email):
    """Pydantic 입력 → save_subscription 용 dict.

    (None 인 시각 필드를 넣으면 검증이 형식 오류로 처리하므로, 있을 때만 넣는다.)
    """
    rec = {
        "email": email,
        "name": payload.name,
        "keywords": payload.keywords,
        "frequency": payload.frequency,
        "summary_length": payload.summary_length,
        "language": payload.language,
    }
    if payload.send_hour is not None and payload.send_minute is not None:
        rec["send_hour"] = payload.send_hour
        rec["send_minute"] = payload.send_minute
    return rec


def _out(sub):
    """Subscription(dataclass) → SubscriberOut."""
    return SubscriberOut(
        email=sub.email,
        name=sub.name,
        keywords=sub.keywords,
        send_hour=sub.send_hour,
        send_minute=sub.send_minute,
        frequency=sub.frequency,
        summary_length=sub.summary_length,
        language=sub.language,
        confirmed=sub.confirmed,
    )


def _send_confirmation_email(email):
    """미확인 구독자에게 구독 확인 메일을 보낸다. 이미 확인됐으면(토큰 없음) 아무것도 안 한다.

    SMTP 실패는 신청 자체를 막지 않도록 로그만 남기고 삼킨다 — 회원가입(DB 저장)과
    메일 발송은 분리된 관심사라, 발신 서버 문제로 가입 자체가 막히면 안 된다.
    """
    token = db.fetch_confirm_token(email)
    if token is None:
        return
    confirm_url = f"{config.API_BASE_URL}/confirm?token={quote(token)}"
    body_html = (
        "<html><body style=\"font-family: sans-serif;\">"
        "<h2>구독 확인이 필요합니다</h2>"
        "<p>아래 버튼을 눌러 구독을 확인해주세요. 확인 전까지는 뉴스레터가 발송되지 않습니다.</p>"
        f'<p><a href="{html.escape(confirm_url, quote=True)}">구독 확인하기</a></p>'
        "</body></html>"
    )
    try:
        send_email.send_email(email, subject="[데일리 금융 뉴스] 구독 확인이 필요합니다", body_html=body_html)
    except Exception as exc:  # SMTP 실패가 회원가입 자체를 막지 않도록 격리
        logger.warning("[확인 메일 발송 실패] %s: %s", _mask_email(email), exc)


def _send_access_code_email(email, code):
    """본인 확인 코드를 이메일로 보낸다. SMTP 실패는 삼킨다(확인 메일과 동일한 이유)."""
    body_html = (
        "<html><body style=\"font-family: sans-serif;\">"
        "<h2>본인 확인 코드</h2>"
        f"<p>아래 코드를 입력해 본인 확인을 완료해주세요. (유효 시간 {config.ACCESS_CODE_TTL_MINUTES}분)</p>"
        f"<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px;\">{html.escape(code)}</p>"
        "</body></html>"
    )
    try:
        send_email.send_email(email, subject="[데일리 금융 뉴스] 본인 확인 코드", body_html=body_html)
    except Exception as exc:  # SMTP 실패가 요청 자체를 막지 않도록 격리
        logger.warning("[본인 확인 코드 발송 실패] %s: %s", _mask_email(email), exc)


def _confirm_result_page_html(success, email):
    """POST /confirm 처리 결과로 보여줄 안내 HTML."""
    if success:
        return (
            "<html><body style=\"font-family: sans-serif;\">"
            "<h2>구독이 확인되었습니다 ✅</h2>"
            f"<p>{html.escape(email)} 앞으로 뉴스레터가 발송됩니다.</p>"
            "</body></html>"
        )
    return (
        "<html><body style=\"font-family: sans-serif;\">"
        "<h2>확인 링크가 유효하지 않습니다</h2>"
        "<p>이미 사용됐거나 잘못된 링크입니다. 구독을 다시 신청해주세요.</p>"
        "</body></html>"
    )


def _confirm_prompt_page_html(token, email):
    """GET /confirm 결과로 보여줄 '확정하시겠습니까?' 안내 페이지.

    이메일 클라이언트/보안 스캐너가 링크를 미리 열람(prefetch)해도 이 GET 자체는
    아무 상태도 바꾸지 않는다 — 실제 확정은 사용자가 버튼을 눌러야 발생하는
    POST /confirm 에서만 일어난다(더블 옵트인이 사전열람으로 우회되는 것을 방지).
    """
    if email is None:
        return (
            "<html><body style=\"font-family: sans-serif;\">"
            "<h2>확인 링크가 유효하지 않습니다</h2>"
            "<p>이미 사용됐거나 잘못된 링크입니다. 구독을 다시 신청해주세요.</p>"
            "</body></html>"
        )
    return (
        "<html><body style=\"font-family: sans-serif;\">"
        "<h2>구독을 확정하시겠습니까?</h2>"
        f"<p>{html.escape(email)} 앞으로 뉴스레터를 받으시려면 아래 버튼을 눌러주세요.</p>"
        f'<form method="post" action="/confirm">'
        f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
        '<button type="submit">구독 확정하기</button>'
        "</form>"
        "</body></html>"
    )


@asynccontextmanager
async def lifespan(app):
    db.init_db()  # 서버 시작 시 테이블 보장
    # 배포 시 외부 주소 미설정 감지 — 기본값(localhost)이면 확인/구독취소 메일 링크가 죽는다.
    #   값이 없어도 에러 없이 조용히 기본값을 쓰므로, 최소한 시작 로그로 신호를 남긴다.
    for _name, _url in (("API_BASE_URL", config.API_BASE_URL), ("FRONTEND_BASE_URL", config.FRONTEND_BASE_URL)):
        if "localhost" in _url or "127.0.0.1" in _url:
            print(f"[설정 경고] {_name}={_url} (기본값) - 실제 배포라면 메일 링크가 열리지 않습니다. 외부 주소를 환경변수로 설정하세요.")
    yield


app = FastAPI(
    title="구독자 관리 API",
    version="1.0.0",
    description="데일리 금융 뉴스 브리핑 — 구독자 관리 API",
    lifespan=lifespan,
    # /docs 하단 'Schemas' 목록을 접는다(-1). 거기 모이는 FastAPI 자동 생성 스키마
    # (HTTPValidationError 등)를 숨겨 화면을 정리 — 각 엔드포인트 안의 요청/응답 예시는 그대로다.
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """429 응답도 다른 에러와 같은 {detail} 스키마로 통일한다(프론트가 에러를 한 형태로 파싱하도록).

    slowapi 기본 핸들러는 {"error": ...} 를 주는데, 이 모듈의 다른 모든 에러(401/404/409/400)는
    {"detail": ...} 라 클라이언트가 두 형태를 모두 다뤄야 했다.
    """
    resp = JSONResponse(
        status_code=429,
        content={"detail": f"요청이 너무 잦습니다. 잠시 후 다시 시도해주세요. ({exc.detail})"},
    )
    try:  # 기본 핸들러처럼 Retry-After 등 속도제한 헤더를 실어 준다
        resp = request.app.state.limiter._inject_headers(resp, request.state.view_rate_limit)
    except Exception:
        pass
    return resp


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]

# CORS — 프론트가 React 등 브라우저 SPA로 바뀔 때를 대비한 사전 설정.
#   Streamlit(서버 대 서버 호출)에는 필요 없지만, 미리 열어둬도 무방하다.
#   와일드카드("*") + allow_credentials 조합은 브라우저가 막으므로 출처를 명시한다(config.CORS_ORIGINS).
#   allow_headers="*" 로 X-Admin-Password·X-Access-Code 같은 커스텀 헤더의 프리플라이트를 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],  # React가 페이지네이션 총 개수를 응답 헤더로 읽을 수 있게
)


class _NormalizeEmailPathMiddleware:
    """rate limit 이 경로 대소문자로 버킷을 가르는 것을 막는다.

    slowapi 는 (key_func 결과 + 요청 경로)로 버킷을 잡는다. 그래서 경로의 이메일이 대소문자만 달라도
    (/subscribers/User@x.com vs /subscribers/user@x.com) 서로 다른 버킷이 되어, 인증 코드 브루트포스를
    이메일의 대소문자 변형(같은 구독자로 정규화됨)으로 여러 버킷에 분산시킬 수 있다. /subscribers/{email}...
    의 이메일 세그먼트를 정규화(소문자)한 경로로 라우팅해 버킷을 하나로 모은다 — 백엔드는 어차피 이메일을
    정규화해 조회하므로 동작은 불변이다(소문자 특수문자 이메일은 정규화해도 그대로라 라우팅 영향 없음)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            prefix = "/subscribers/"
            if path.startswith(prefix) and len(path) > len(prefix):
                seg, slash, tail = path[len(prefix):].partition("/")
                norm = subscriptions.normalize_email(seg)
                if norm != seg:
                    scope = dict(scope)
                    scope["path"] = prefix + norm + slash + tail
        await self.app(scope, receive, send)


app.add_middleware(_NormalizeEmailPathMiddleware)


@app.get("/health", summary="헬스체크(생존 확인)")
def health():
    """백엔드가 살아있는지 확인하는 용도. 프론트·프록시·모니터가 부담 없이 부를 수 있게
    인증·속도 제한을 두지 않는다(프로세스 liveness 체크 — DB 등 의존성 검사는 하지 않는다).
    """
    return {"status": "ok"}


@app.get("/options", response_model=OptionsOut, summary="선택지 목록 조회")
def get_options():
    """frequency/summary_length/language 선택지를 반환한다.

    이 셋은 표시용 라벨이 아니라 스케줄링(FREQUENCY_WEEKDAYS)·LLM 프롬프트
    (LENGTH_PRESETS, 언어 지시)가 실제로 키로 쓰는 값이라 백엔드가 정의해야 한다.
    프론트가 하드코딩하면 백엔드에서 선택지가 바뀌었을 때 조용히 어긋난다.
    """
    return OptionsOut(
        frequency=config.FREQUENCY,
        summary_length=config.SUMMARY_LENGTH,
        language=config.LANGUAGE,
    )


class SubscriberStatsOut(BaseModel):
    """응답: 관리자 대시보드용 구독자 통계(백엔드가 집계 — 프론트가 다시 구현하지 않도록)."""
    total_subscribers: int
    confirmed_count: int
    most_common_frequency: str
    most_common_language: str


@app.get("/subscribers/stats", response_model=SubscriberStatsOut, summary="구독자 통계 조회(관리자 전용)")
@limiter.limit("60/minute")
def subscriber_stats(request: Request, password: str | None = Security(_admin_password_header)):
    """전체 구독자 통계(총원·확인 완료·최다 주기·최다 언어)를 백엔드가 집계해 반환한다(관리자 전용).

    프론트가 목록을 받아 직접 세지 않아도 되게 백엔드가 계산한다 — React 등 다른 프론트가
    붙어도 같은 집계를 다시 구현할 필요가 없다.
    (이 경로는 /subscribers/{email} 보다 먼저 선언돼야 'stats' 가 이메일로 잡히지 않는다.)
    """
    require_admin(password)
    return SubscriberStatsOut(**db.subscriber_stats())


@app.post("/subscribers", response_model=SubscriberOut, status_code=201, summary="구독 추가(신규 구독)")
@limiter.limit("5/hour")
def create_subscriber(request: Request, payload: SubscriberIn, background_tasks: BackgroundTasks):
    """구독자를 새로 추가한다.

    이미 확인(confirmed)된 이메일로 다시 신청하면 409. 아직 미확인 상태인 이메일로
    다시 신청하면(예: 확인 메일을 못 받음) 새 가입이 아니라 확인 메일 재전송으로
    처리한다(같은 토큰 재사용, 정보는 이번 요청 값으로 갱신).
    저장 후 confirmed=False 인 동안은 확인 메일을 (재)발송한다(응답 후 백그라운드 발송 —
    SMTP 지연이 응답을 붙잡지 않도록).
    """
    email = subscriptions.normalize_email(payload.email)
    existing = subscriptions.get_subscription(email)
    if existing is not None and existing.confirmed:
        raise HTTPException(status_code=409, detail=f"이미 구독 중인 이메일입니다: {email}")
    try:
        sub = subscriptions.save_subscription(_record(payload, email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sub.confirmed:
        background_tasks.add_task(_send_confirmation_email, sub.email)
    return _out(sub)


@app.get("/confirm", response_class=HTMLResponse, summary="이메일 구독 확인 안내 페이지")
def confirm_prompt(token: str):
    """확인 메일 링크(GET /confirm?token=...) 클릭 시 뜨는 안내 페이지.

    이 GET 자체는 구독을 확정하지 않는다(메일 보안 스캐너의 사전열람으로 더블
    옵트인이 무력화되는 것을 막기 위함) — 실제 확정은 이 페이지의 버튼이 보내는
    POST /confirm 에서 이뤄진다. 토큰이 잘못됐거나 이미 쓰였으면 400.
    """
    email = db.peek_confirm_token(token)
    status_code = 200 if email is not None else 400
    return HTMLResponse(_confirm_prompt_page_html(token, email), status_code=status_code)


@app.post("/confirm", response_class=HTMLResponse, summary="이메일 구독 확인(더블 옵트인)")
def confirm_subscription(token: str = Form(...)):
    """확인 페이지의 버튼(POST /confirm)으로 구독을 확정한다(confirmed=True).

    확정 전까지는 어떤 정기 발송도 받지 않는다. 토큰은 1회용이라 확인 후 폐기되며,
    잘못됐거나 이미 쓰인 토큰이면 400.
    """
    email = db.confirm_subscriber(token)
    if email is None:
        return HTMLResponse(_confirm_result_page_html(False, None), status_code=400)
    return HTMLResponse(_confirm_result_page_html(True, email))


@app.get("/subscribers", response_model=list[SubscriberOut], summary="전체 구독자 조회(관리자)")
@limiter.limit("30/minute")
def list_subscribers(
    request: Request,
    response: Response,
    admin_password: str | None = Security(_admin_password_header),
    limit: int | None = Query(None, ge=1, le=1000, description="한 번에 가져올 최대 개수(미지정 시 전체)"),
    offset: int = Query(0, ge=0, description="앞에서 건너뛸 개수(페이지네이션)"),
):
    """전체 구독자 목록. 관리자 전용 — X-Admin-Password 헤더 인증 필요(401 시 거부).

    인증 검사를 dependencies= 가 아니라 본문에서 하는 이유는 다른 관리자/본인 엔드포인트와
    같다 — @limiter.limit 이 요청을 먼저 집계한 뒤 인증이 돌아야 실패 요청도 속도 제한에
    걸려 관리자 비밀번호 무차별 대입(성공 시 전체 구독자 PII 유출)을 막을 수 있다.

    페이지네이션: limit/offset 은 선택이며 미지정 시 전체를 반환한다(기존 동작 유지).
    전체 개수는 응답 헤더 X-Total-Count 로 함께 준다(React 가 페이지 수 계산에 쓸 수 있게).
    """
    require_admin(admin_password)
    # 전체 개수·페이지 모두 '검증 통과한 구독자' 기준으로 맞춘다 — 불량 행이 있어도 X-Total-Count 와
    # 반환 목록이 어긋나지 않게 한다(load_subscriptions_page 도 검증 후 슬라이스). 규모가 작아 부담 없음.
    valid = subscriptions.load_subscriptions()
    response.headers["X-Total-Count"] = str(len(valid))
    subs = valid[offset:] if limit is None else valid[offset:offset + limit]
    return [_out(s) for s in subs]


@app.post("/subscribers/{email}/access-code", status_code=202, summary="본인 확인 코드 발송")
@limiter.limit("5/hour", key_func=_rate_key_email)
def request_access_code(request: Request, email: str, background_tasks: BackgroundTasks):
    """등록된 이메일이면 본인 확인 코드를 발송한다.

    이메일이 존재하지 않아도 항상 동일한 202를 반환한다(구독 여부가 응답으로
    노출되는 이메일 존재 확인(enumeration)을 막기 위함) — 실제 발송은 존재할 때만 한다.
    발송(SMTP)은 응답 후 백그라운드로 돌린다 — 존재할 때만 동기 SMTP를 태우면 응답 시간
    차이(수백 ms~수 초)로 구독 여부가 타이밍 사이드채널로 새기 때문(202 일괄 반환의 취지 무력화).
    """
    email = subscriptions.normalize_email(email)
    if subscriptions.get_subscription(email) is not None:
        # DB 오류가 나도 항상 동일한 202를 반환해 이메일 존재 여부가 응답으로 새지 않게 한다(열거 방지).
        # code 가 None(예: 경합으로 방금 삭제됨)이면 발송 태스크를 걸지 않는다.
        try:
            code = db.generate_access_code(email)
        except Exception as exc:
            print(f"[인증 코드 발급 실패] {exc}")
            code = None
        if code:
            background_tasks.add_task(_send_access_code_email, email, code)
    return {"detail": "등록된 이메일이면 인증 코드를 보냈습니다."}


@app.get(
    "/subscribers/{email}", response_model=SubscriberOut, summary="구독 정보 조회",
)
@limiter.limit("10/minute", key_func=_rate_key_email)
def get_subscriber(
    request: Request,
    email: str,
    admin_password: str | None = Security(_admin_password_header),
    access_code: str | None = Security(_access_code_header),
):
    """이메일로 구독 정보를 조회한다. 없으면 404."""
    _check_admin_or_owner(email, admin_password, access_code)
    email = subscriptions.normalize_email(email)
    sub = subscriptions.get_subscription(email)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    return _out(sub)


@app.put(
    "/subscribers/{email}", response_model=SubscriberOut, summary="구독자 정보 수정",
)
@limiter.limit("10/minute", key_func=_rate_key_email)
def update_subscriber(
    request: Request,
    email: str,
    payload: SubscriberUpdate,
    background_tasks: BackgroundTasks,
    admin_password: str | None = Security(_admin_password_header),
    access_code: str | None = Security(_access_code_header),
):
    """구독자 정보를 수정한다. 없으면 404, 값이 잘못되면 400.

    payload.email 이 현재 이메일과 다르면 '이메일 변경'으로 처리한다: 새 주소로 (미확인)
    재가입 + 확인 메일 발송 + 기존 주소 삭제(POST 신규가입과 같은 규칙 — 소유권이 바뀌므로
    새 주소는 다시 확인이 필요하다). 예전엔 프론트가 'POST 재가입 + DELETE 삭제'로 우회했는데,
    이 흐름을 백엔드가 담당해 어떤 프론트가 붙어도 같은 로직을 다시 구현하지 않게 한다.
    """
    _check_admin_or_owner(email, admin_password, access_code)
    email = subscriptions.normalize_email(email)
    existing = subscriptions.get_subscription(email)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")

    new_email = subscriptions.normalize_email(payload.email) if payload.email else email
    if new_email != email:
        # 이메일 변경 — 새 주소 재가입(미확인) + 확인 메일 + 기존 주소 삭제.
        #   인증은 현재(old) 이메일 기준으로 이미 통과했고, 새 주소 가입은 POST 처럼 공개 동작이라
        #   권한 관점에서도 일관된다. 이미 '확인된' 새 주소가 있으면 신규가입과 동일하게 409.
        target = subscriptions.get_subscription(new_email)
        if target is not None:
            # 확인 여부와 무관하게 이미 존재하는 주소로는 변경 불가 — 미확인 대상을 덮어쓰면 타인의
            # 대기 중(미확인) 가입이 소유권 확인 없이 파괴된다(보상 삭제까지 겹치면 완전 소실).
            raise HTTPException(status_code=409, detail=f"이미 사용 중인 이메일입니다: {new_email}")
        try:
            new_sub = subscriptions.save_subscription(_record(payload, new_email))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # 기존 주소 삭제. 삭제(다른 트랜잭션)가 실패하면 방금 만든 새 주소를 되돌려(보상) 한 사용자가
        # 두 구독으로 갈라지는 최악을 막는다. (단일 트랜잭션 rename 이 아니라 완전한 원자성은 아니지만,
        # DELETE 를 try 밖에 두어 orphan 을 남기던 이전 버전보다 안전하다.)
        try:
            subscriptions.delete_subscription(email)
        except Exception as exc:
            logger.exception("이메일 변경 실패(%s→%s): 기존 주소 삭제 오류",
                             _mask_email(email), _mask_email(new_email))
            try:
                subscriptions.delete_subscription(new_email)  # 보상: 새로 만든 것 롤백
            except Exception:
                logger.exception("이메일 변경 보상 실패: 새 주소(%s) 롤백 삭제 오류 — 수동 정리 필요",
                                 _mask_email(new_email))
            raise HTTPException(
                status_code=500, detail="이메일 변경 중 오류가 발생했습니다. 다시 시도해주세요."
            ) from exc
        if not new_sub.confirmed:
            # 확인 메일은 기존 주소 삭제가 성공한 뒤에만 보낸다(롤백될 주소로 메일 보내지 않도록).
            background_tasks.add_task(_send_confirmation_email, new_sub.email)
        return _out(new_sub)  # 새 주소는 미확인(confirmed=False)으로 시작

    # 이메일 그대로 — 제자리 수정(전체 교체).
    try:
        sub = subscriptions.save_subscription(_record(payload, email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = _out(sub)
    # PUT 은 이메일 확인 상태를 바꾸지 않는다 — save_subscription 이 돌려준 기본 False 가 아니라
    # 실제 저장돼 있는 confirmed 를 응답에 담아, 확인된 사용자가 미확인으로 잘못 보이지 않게 한다.
    out.confirmed = existing.confirmed
    return out


@app.delete(
    "/subscribers/{email}", status_code=204, summary="구독 취소",
)
@limiter.limit("10/minute", key_func=_rate_key_email)
def delete_subscriber(
    request: Request,
    email: str,
    admin_password: str | None = Security(_admin_password_header),
    access_code: str | None = Security(_access_code_header),
):
    """구독을 취소(삭제)한다. 없으면 404."""
    _check_admin_or_owner(email, admin_password, access_code)
    email = subscriptions.normalize_email(email)
    if not subscriptions.delete_subscription(email):
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
