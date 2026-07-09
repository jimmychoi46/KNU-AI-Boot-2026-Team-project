import html
import hmac
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src import config, db, subscriptions
from src.notifiers import send_email

_admin_password_header = APIKeyHeader(name="X-Admin-Password", auto_error=False)
_access_code_header = APIKeyHeader(name="X-Access-Code", auto_error=False)


def _is_admin(password):
    """X-Admin-Password 값이 유효한 관리자 인증인지. ADMIN_PASSWORD 미설정 시 항상 거부(안전 기본값)."""
    return bool(config.ADMIN_PASSWORD) and bool(password) and hmac.compare_digest(password, config.ADMIN_PASSWORD)


def require_admin(password: str | None = Security(_admin_password_header)):
    """GET /subscribers(관리자 전용)에 붙이는 인증 의존성.

    설정된 관리자 비밀번호와 일치하는 값이 X-Admin-Password 헤더로 와야 통과한다.
    비밀번호 비교에 hmac.compare_digest 를 써서 타이밍 공격을 막는다.
    ADMIN_PASSWORD 가 서버에 아예 설정 안 돼 있으면(운영 실수) 무엇을 보내도 거부한다
    — "설정 안 됨"을 "누구나 통과"로 취급하지 않는다(안전 기본값).
    """
    if not _is_admin(password):
        raise HTTPException(status_code=401, detail="관리자 인증 실패")


def require_admin_or_owner(
    email: str,
    admin_password: str | None = Security(_admin_password_header),
    access_code: str | None = Security(_access_code_header),
):
    """GET/PUT/DELETE .../{email} 용 인증: 관리자 비밀번호 또는 본인 확인 코드."""
    if _is_admin(admin_password):
        return
    if access_code and db.verify_access_code(email, access_code):
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
    send_hour: int | None = Field(None, ge=0, le=24, description="발송 시각(시) 0~24")
    send_minute: int | None = Field(None, description="발송 시각(분) — 0 또는 30")
    frequency: str | None = Field(None, description="발송 주기 (config.FREQUENCY, 미지정 시 기본값)")
    summary_length: str | None = Field(None, description="요약 길이 (config.SUMMARY_LENGTH, 미지정 시 기본값)")
    language: str | None = Field(None, description="언어 (config.LANGUAGE, 미지정 시 기본값)")


class SubscriberUpdate(BaseModel):
    """구독자 수정(PUT) 요청 본문: 이메일은 경로에서 받으므로 본문엔 없음(전체 교체)."""
    name: str = ""
    keywords: list[str] = Field(default_factory=list)
    send_hour: int | None = Field(None, ge=0, le=24)
    send_minute: int | None = Field(None, description="0 또는 30")
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
        print(f"[확인 메일 발송 실패] {email}: {exc}")


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
        print(f"[본인 확인 코드 발송 실패] {email}: {exc}")


def _confirm_page_html(success, email):
    """GET /confirm 결과로 보여줄 최소한의 안내 HTML(이메일 클라이언트에서 링크 클릭 시 뜸)."""
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


@asynccontextmanager
async def lifespan(app):
    db.init_db()  # 서버 시작 시 테이블 보장
    yield


app = FastAPI(
    title="구독자 관리 API",
    version="1.0.0",
    description="데일리 금융 뉴스 브리핑 — 구독자 관리 API",
    lifespan=lifespan,
)


@app.post("/subscribers", response_model=SubscriberOut, status_code=201, summary="구독 추가(신규 구독)")
def create_subscriber(payload: SubscriberIn):
    """구독자를 새로 추가한다.

    이미 확인(confirmed)된 이메일로 다시 신청하면 409. 아직 미확인 상태인 이메일로
    다시 신청하면(예: 확인 메일을 못 받음) 새 가입이 아니라 확인 메일 재전송으로
    처리한다(같은 토큰 재사용, 정보는 이번 요청 값으로 갱신).
    저장 후 confirmed=False 인 동안은 확인 메일을 (재)발송한다.
    """
    existing = subscriptions.get_subscription(payload.email)
    if existing is not None and existing.confirmed:
        raise HTTPException(status_code=409, detail=f"이미 구독 중인 이메일입니다: {payload.email}")
    try:
        sub = subscriptions.save_subscription(_record(payload, payload.email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not sub.confirmed:
        _send_confirmation_email(sub.email)
    return _out(sub)


@app.get("/confirm", response_class=HTMLResponse, summary="이메일 구독 확인(더블 옵트인)")
def confirm_subscription(token: str):
    """확인 메일 링크(GET /confirm?token=...)의 token 으로 구독을 확정한다(confirmed=True).

    확정 전까지는 어떤 정기/속보 발송도 받지 않는다. 토큰은 1회용이라 확인 후 폐기되며,
    잘못됐거나 이미 쓰인 토큰이면 400.
    """
    email = db.confirm_subscriber(token)
    if email is None:
        return HTMLResponse(_confirm_page_html(False, None), status_code=400)
    return HTMLResponse(_confirm_page_html(True, email))


@app.get(
    "/subscribers", response_model=list[SubscriberOut], summary="전체 구독자 조회(관리자)",
    dependencies=[Depends(require_admin)],
)
def list_subscribers():
    """전체 구독자 목록. 관리자 전용 — X-Admin-Password 헤더 인증 필요(401 시 거부)."""
    return [_out(s) for s in subscriptions.load_subscriptions()]



@app.post("/subscribers/{email}/access-code", status_code=202, summary="본인 확인 코드 발송")
def request_access_code(email: str):
    """본인 확인 코드를 이메일로 발송한다. 없는 이메일이면 404."""
    if subscriptions.get_subscription(email) is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    code = db.generate_access_code(email)
    _send_access_code_email(email, code)
    return {"detail": "인증 코드를 이메일로 보냈습니다."}


@app.get(
    "/subscribers/{email}", response_model=SubscriberOut, summary="구독 정보 조회",
    dependencies=[Depends(require_admin_or_owner)],
)
def get_subscriber(email: str):
    """이메일로 구독 정보를 조회한다. 없으면 404."""
    sub = subscriptions.get_subscription(email)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    return _out(sub)


@app.put(
    "/subscribers/{email}", response_model=SubscriberOut, summary="구독자 정보 수정",
    dependencies=[Depends(require_admin_or_owner)],
)
def update_subscriber(email: str, payload: SubscriberUpdate):
    """구독자 정보를 수정한다. 없으면 404, 값이 잘못되면 400."""
    if subscriptions.get_subscription(email) is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    try:
        sub = subscriptions.save_subscription(_record(payload, email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(sub)


@app.delete(
    "/subscribers/{email}", status_code=204, summary="구독 취소",
    dependencies=[Depends(require_admin_or_owner)],
)
def delete_subscriber(email: str):
    """구독을 취소(삭제)한다. 없으면 404."""
    if not subscriptions.delete_subscription(email):
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
