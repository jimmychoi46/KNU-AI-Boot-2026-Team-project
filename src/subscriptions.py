"""구독 데이터 모델 & 저장소.

담당: 백엔드 — 스키마/검증/저장소 정의.
구독 데이터는 DB(src/db.py 의 subscribers 테이블)에 저장한다.
  - 프론트 대시보드: save_subscription() / delete_subscription() 을 호출해 쓰기
  - 백엔드:          load_subscriptions() 으로 읽기
(과거엔 data/subscriptions.json 파일이 계약이었으나, 동시성·일관성을 위해 DB 로 이전했다.
 기존 JSON 이 있으면 import_from_json() 으로 최초 1회 DB 에 시드한다.)

[구독 레코드 형식] — save_subscription() 에 넘기는 dict, DB 열과 1:1
    {
        "email": "user@example.com",
        "name": "홍길동",               # (선택) 구독자 이름, 기본 ""
        "keywords": ["주식", "금리"],   # 자유 입력(프론트). 저장 시 공백/빈값/중복만 정리
        "send_hour": 8,                 # 발송 시각(시, KST) 0~24
        "send_minute": 0,               # 발송 시각(분) (0 혹은 30)[30분 단위]
        "frequency": "매일",            # (선택) config.FREQUENCY 중 선택, 기본 첫 번째
        "summary_length": "짧게",       # (선택) config.SUMMARY_LENGTH 중 선택, 기본 첫 번째
        "language": "한국어",           # (선택) config.LANGUAGE 중 선택, 기본 첫 번째
    }

[검증 위치] save_subscription(쓰기) 과 load_subscriptions(읽기) 모두 _from_row 로 검증한다
    — 쓸 때 걸러도, 혹시 잘못된 행이 DB 에 있으면 읽을 때 그 행만 건너뛴다(이중 방어).
"""
import json
import os
import re
from dataclasses import dataclass, field

from src import config, db

# 이메일 '형식' 검사용 정규식 — 일부러 단순하게 둔다.
#   local@domain.tld 최소 골격만 본다: @ 앞뒤에 공백·@ 없는 글자, 도메인에 점 1개 이상.
#   RFC를 완벽히 따르는 정규식은 길고 오탐이 많은데, '진짜 유효한 주소인지'는
#   결국 인증(확인 메일 클릭)이 증명하므로 형식 검사는 명백한 오타만 걸러주면 된다.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$") # 올바른 형식 ex) abc123@abc.com  


@dataclass
class Subscription:
    email: str
    keywords: list = field(default_factory=list)
    send_hour: int = 0
    send_minute: int = 0
    emergency_opt_in: bool = False
    name: str = ""
    frequency: str = config.FREQUENCY[0]
    summary_length: str = config.SUMMARY_LENGTH[0]
    language: str = config.LANGUAGE[0]


def _parse_send_time(row):
    """row 에서 발송 시각을 (hour, minute) 으로 뽑아낸다.

    (이때, send_hour/send_minute(정식 형식)이 모두 존재하지 않으면 ValueError 반환)
    """
    if "send_hour" in row and "send_minute" in row:
        try:
            hour = int(row["send_hour"])
            minute = int(row["send_minute"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"send_hour 혹은 send_minute 형식 오류: {exc}")
    else:
        raise ValueError("send_hour 혹은 send_minute 누락")

    if not (0 <= hour <= 24):
        raise ValueError(f"발송 시각(시) 범위 오류: {hour} (0~24)")
    if minute not in (0, 30):
        raise ValueError(f"발송 시각(분)은 30분 단위(정각,30분)만 허용: {minute}")
    return hour, minute


def _pick(value, candidates, default):
    """value 가 candidates 안에 있으면 그대로, 아니면 default 로 대체."""
    return value if value in candidates else default


def _looks_like_email(email):
    """이메일 '형식'이 그럴듯한지(오타 수준) 검사. 소유 여부는 인증(확인 메일)이 증명한다."""
    return bool(_EMAIL_RE.match(email))


def _clean_keywords(keywords):
    """키워드 정리 수행
    
    작업 수행 순서: 앞뒤 공백 제거 → 빈값 제거 → 중복 제거(순서 유지)
    """
    cleaned = []                        # 정리된 키워드를 담을 빈 리스트 정의
    for k in keywords or []:            
        k = str(k).strip()              # k를 문자열로 변환한 뒤, 공백 제거 수행      
        if k and k not in cleaned:      # k가 존재하고 이미 cleaned에 존재하지 않는 경우 추가
            cleaned.append(k)
    return cleaned


def _from_row(row):
    """JSON 레코드(dict)를 Subscription 으로 변환.

    필수 항목이 없거나 형식이 틀리면 ValueError 반환(단, 예외로 인한 시스템 장애를 방지하기 위해 예외 발생 시 호출부에서 해당 레코드 건너뜀)
    키워드는 공백/빈값/중복 정리(_clean_keywords) 수행.
    단, 예외 가능성을 고려하여 frequency,summary_length, language의 경우 _pick()을 통해 구독자별 설정값이 존재할 경우 해당 설정값, 존재하지 않을 경우 미리 설정된 기본값으로 설정.
    """
    email = row.get("email")
    if not email:
        raise ValueError("email 누락")
    email = str(email).strip()
    if not _looks_like_email(email):
        raise ValueError(f"유효한 이메일 형식이 아닙니다: {email!r}")
    send_hour, send_minute = _parse_send_time(row)

    keywords = _clean_keywords(row.get("keywords", []))
    return Subscription(
        email=email,
        keywords=keywords,
        send_hour=send_hour,
        send_minute=send_minute,
        name=str(row.get("name", "")).strip(),
        frequency=_pick(row.get("frequency"), config.FREQUENCY, config.FREQUENCY[0]),
        summary_length=_pick(row.get("summary_length"), config.SUMMARY_LENGTH, config.SUMMARY_LENGTH[0]),
        language=_pick(row.get("language"), config.LANGUAGE, config.LANGUAGE[0]),
    )


def load_subscriptions(path=None):
    """DB(subscribers)를 읽어 Subscription 리스트로 반환.

    구독자가 없으면(또는 테이블이 아직 없으면) 빈 리스트를 반환한다.
    잘못된 레코드가 하나 있어도 그 레코드만 건너뛰고 나머지는 정상 처리 (한 명의 잘못된 정보가 그 시각 전체 발송을 막는 일이 발생하지 않도록 하기 위함)
    path: DB 경로(테스트용 주입). 미지정 시 config.DB_PATH.
    """
    subscriptions = []
    for i, row in enumerate(db.fetch_all_subscribers(path)):
        try:
            subscriptions.append(_from_row(row))
        except (ValueError, AttributeError) as exc:
            print(f"[구독 레코드 건너뜀] #{i}: {exc}")
    return subscriptions


def get_subscription(email, path=None):
    """이메일로 구독자 1명을 Subscription 으로 반환. 없거나 잘못된 행이면 None."""
    row = db.fetch_subscriber(email, path=path)
    if row is None:
        return None
    try:
        return _from_row(row)
    except (ValueError, AttributeError):
        return None


def save_subscription(record, path=None):
    """구독자 1명을 검증한 뒤 DB 에 저장(있으면 갱신)한다.

    프론트 대시보드가 신청/수정 시 호출하는 쓰기 API.
    record 형식이 틀리면(email 누락·시각 범위 오류 등) ValueError 를 던진다.
    저장은 검증·정규화된 값(후보에 없는 키워드 제거, 기본값 채움)으로 이뤄진다.
    returns: 저장된 Subscription.
    """
    sub = _from_row(record)  # 검증 + 정규화
    db.upsert_subscriber(
        {
            "email": sub.email,
            "name": sub.name,
            "keywords": sub.keywords,
            "send_hour": sub.send_hour,
            "send_minute": sub.send_minute,
            "frequency": sub.frequency,
            "summary_length": sub.summary_length,
            "language": sub.language,
        },
        path=path,
    )
    return sub


def delete_subscription(email, path=None):
    """이메일로 구독자를 삭제. 삭제된 수를 반환(없으면 0)."""
    return db.delete_subscriber(email, path=path)


def import_from_json(json_path=None, db_path=None):
    """기존 JSON 파일의 구독자를 DB 로 가져온다(최초 1회 시드/이관용).

    JSON 이 없으면 0 을 반환한다. 잘못된 레코드는 건너뛴다.
    returns: 가져온 구독자 수.
    """
    json_path = json_path or config.SUBSCRIPTIONS_PATH
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, encoding="utf-8") as f:
            rows = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[구독 JSON 로드 실패] {json_path}: {exc}")
        return 0

    imported = 0
    for i, row in enumerate(rows):
        try:
            save_subscription(row, path=db_path)
            imported += 1
        except (ValueError, AttributeError) as exc:
            print(f"[구독 가져오기 건너뜀] #{i}: {exc}")
    return imported


def is_due(sub, now):
    """지금(now) 이 구독자에게 발송할 시각인가.

    시:분이 일치하고, now 의 요일이 그 구독자 주기(frequency)의 발송 요일이면 True.
    (예: '매주'(월요일) 구독자는 월요일 그 시각에만 True → 매일 중복 발송 방지)
    """
    if not (sub.send_hour == now.hour and sub.send_minute == now.minute):
        return False
    weekdays = config.FREQUENCY_WEEKDAYS.get(sub.frequency, config.FREQUENCY_WEEKDAYS["매일"])
    return now.weekday() in weekdays


def due_subscribers(subscriptions, now):
    """now(datetime) 에 발송해야 하는 구독자만 필터링 (시:분 + 주기별 발송 요일)."""
    return [sub for sub in subscriptions if is_due(sub, now)]


def send_window_hours(sub, now):
    """이 발송이 되돌아봐야 할 시간(창, hours) — 직전 발송 요일까지의 간격.

    뉴스레터는 '지난 발송 이후 ~ 이번 발송' 사이 소식을 커버해야 하므로,
    창은 주기(발송 요일 간격)에 따라 달라진다.
      - 매일          : 어제 이후 → 24h
      - 주 3회(월·수·금): 월=직전 금 이후 72h, 수·금=이틀 전 48h
      - 매주(월)       : 지난주 월 이후 → 168h
    알 수 없는 주기면 기본값(config.SUMMARY_RECENCY_HOURS).
    """
    weekdays = config.FREQUENCY_WEEKDAYS.get(sub.frequency)
    if not weekdays:
        return config.SUMMARY_RECENCY_HOURS
    today = now.weekday()
    for gap in range(1, 8):
        if (today - gap) % 7 in weekdays:
            return gap * 24
    return 7 * 24
