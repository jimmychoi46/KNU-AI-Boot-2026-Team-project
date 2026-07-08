"""구독자 REST API (FastAPI).

담당: 백엔드. 프론트/관리자가 구독자를 관리하는 HTTP 엔드포인트.
저장·검증은 subscriptions.py(→ db.py) 를 그대로 재사용한다(검증 실패 → 400).

Swagger UI:  서버 실행 후  http://localhost:8000/docs  (테스트/시연용)
실행:        uvicorn src.api:app --reload

엔드포인트
  POST   /subscribers          신규 구독 (이미 있으면 409)
  GET    /subscribers          전체 구독자 조회 (관리자 전용 — X-Admin-Password 헤더 필요)
  GET    /subscribers/{email}  구독 정보 조회
  PUT    /subscribers/{email}  구독자 정보 수정 (전체 교체)
  DELETE /subscribers/{email}  구독 취소

[관리자 인증] GET /subscribers 는 관리자 전용이다. 프론트는 요청마다
X-Admin-Password 헤더에 config.ADMIN_PASSWORD 와 같은 값을 실어 보내야 한다.
Swagger UI 우측 상단 "Authorize" 에 비밀번호를 넣으면 이후 요청에 자동으로 실린다.
ADMIN_PASSWORD 가 서버에 설정돼 있지 않으면(운영 실수 방지) 어떤 값을 보내도 거부한다.
"""
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src import config, db, subscriptions

_admin_password_header = APIKeyHeader(name="X-Admin-Password", auto_error=False)


def require_admin(password: str | None = Security(_admin_password_header)):
    """GET /subscribers(관리자 전용)에 붙이는 인증 의존성.

    설정된 관리자 비밀번호와 일치하는 값이 X-Admin-Password 헤더로 와야 통과한다.
    비밀번호 비교에 hmac.compare_digest 를 써서 타이밍 공격을 막는다.
    ADMIN_PASSWORD 가 서버에 아예 설정 안 돼 있으면(운영 실수) 무엇을 보내도 거부한다
    — "설정 안 됨"을 "누구나 통과"로 취급하지 않는다(안전 기본값).
    """
    if not config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="관리자 인증이 설정되지 않았습니다.")
    if not password or not hmac.compare_digest(password, config.ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="관리자 인증 실패")


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
    send_time: str | None = Field(
        None, description="(호환) 프리셋 문자열: '오전 8시'/'정오(12시)'/'오후 6시'. send_hour/minute 없을 때 사용",
    )
    frequency: str | None = Field(None, description="발송 주기 (config.FREQUENCY, 미지정 시 기본값)")
    summary_length: str | None = Field(None, description="요약 길이 (config.SUMMARY_LENGTH, 미지정 시 기본값)")
    language: str | None = Field(None, description="언어 (config.LANGUAGE, 미지정 시 기본값)")


class SubscriberUpdate(BaseModel):
    """구독자 수정(PUT) 요청 본문: 이메일은 경로에서 받으므로 본문엔 없음(전체 교체)."""
    name: str = ""
    keywords: list[str] = Field(default_factory=list)
    send_hour: int | None = Field(None, ge=0, le=24)
    send_minute: int | None = Field(None, description="0 또는 30")
    send_time: str | None = None
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


def _record(payload, email):
    """Pydantic 입력 → save_subscription 용 dict.

    시각은 send_hour/send_minute 를 우선 쓰고, 없으면 send_time 프리셋을 넘긴다.
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
    elif payload.send_time is not None:
        rec["send_time"] = payload.send_time
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
    """구독자를 새로 추가한다. 같은 이메일이 이미 구독되어 있다면(즉, DB에 이미 이메일이 저장되어 있다면) 409를 반환한다."""
    if subscriptions.get_subscription(payload.email) is not None:
        raise HTTPException(status_code=409, detail=f"이미 구독 중인 이메일입니다: {payload.email}")
    try:
        sub = subscriptions.save_subscription(_record(payload, payload.email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(sub)


@app.get(
    "/subscribers", response_model=list[SubscriberOut], summary="전체 구독자 조회(관리자)",
    dependencies=[Depends(require_admin)],
)
def list_subscribers():
    """전체 구독자 목록. 관리자 전용 — X-Admin-Password 헤더 인증 필요(401 시 거부)."""
    return [_out(s) for s in subscriptions.load_subscriptions()]



@app.get("/subscribers/{email}", response_model=SubscriberOut, summary="구독 정보 조회")
def get_subscriber(email: str):
    """이메일로 구독 정보를 조회한다. 해당 이메일이 DB에 없다면 404를 반환한다."""
    sub = subscriptions.get_subscription(email)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    return _out(sub)


@app.put("/subscribers/{email}", response_model=SubscriberOut, summary="구독자 정보 수정")
def update_subscriber(email: str, payload: SubscriberUpdate):
    """구독자 정보 수정을 수행한다. 이때 해당 이메일이 DB에 없다면 404, 값이 잘못되었다면 400을 반환한다."""
    if subscriptions.get_subscription(email) is None:
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
    try:
        sub = subscriptions.save_subscription(_record(payload, email))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(sub)


@app.delete("/subscribers/{email}", status_code=204, summary="구독 취소")
def delete_subscriber(email: str):
    """구독을 취소(삭제)한다. 이때 해당 이메일이 DB에 없다면 404를 반환한다."""
    if not subscriptions.delete_subscription(email):
        raise HTTPException(status_code=404, detail=f"구독자를 찾을 수 없습니다: {email}")
