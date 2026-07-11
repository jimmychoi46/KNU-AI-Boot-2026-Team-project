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
    name: str = ""
    frequency: str = config.FREQUENCY[0]
    summary_length: str = config.SUMMARY_LENGTH[0]
    language: str = config.LANGUAGE[0]
    confirmed: bool = False  # 이메일 인증 여부


def _parse_send_time(row):
    """hh:mm 형식의 발송 시각(send_time)에서 시간/분으로 파싱해서 반환한다.

    (이때, send_hour/send_minute(정식 형식)이 모두 존재하지 않으면 ValueError 반환)
    """
    if "send_hour" in row and "send_minute" in row:
        raw_hour, raw_minute = row["send_hour"], row["send_minute"]
        # 소수(5.9 등)가 int()로 조용히 절삭돼 엉뚱한 시각으로 저장되는 것을 막는다(정수만 허용).
        for raw in (raw_hour, raw_minute):
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError(f"발송 시각은 정수여야 합니다(소수 불가): {raw}")
        try:
            hour = int(raw_hour)
            minute = int(raw_minute)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"send_hour 혹은 send_minute 형식 오류: {exc}") from exc
    else:
        raise ValueError("send_hour 혹은 send_minute 누락")

    if not (0 <= hour <= 24):
        raise ValueError(f"발송 시각(시) 범위 오류: {hour} (0~24)")
    if minute not in (0, 30):
        raise ValueError(f"발송 시각(분)은 30분 단위(정각,30분)만 허용: {minute}")
    if hour == 24 and minute != 0:
        # 24시는 '자정(다음날 0시)' 표시값이라 24:00만 의미가 있다. 24:30은 is_due 가 0시로
        # 정규화하면서 분은 30으로 남아 00:30에 오발송되므로 여기서 막는다.
        raise ValueError("발송 시각 24시는 24:00(정각)만 허용합니다")
    return hour, minute


def _pick(value, candidates, default):
    """value 가 candidates 안에 있으면 그대로, 아니면 default 로 대체. (get()과 유사하게 해당 value가 없어 생길 수 있는 시스템 장애 발생 가능성을 방지함)"""
    return value if value in candidates else default


def _looks_like_email(email):
    """이메일 형식이 맞는지 검증하는 함수"""
    return bool(_EMAIL_RE.match(email)) # 이메일 형식이 _EMAIL_RE에 정의된 형식인지 확인, 형식이 일치하면 True, 불일치 시 False


# 제어문자(C0/C1·DEL)와 양방향/서식/제로폭 등 '보이지 않는' 유니코드 서식 문자를 제거한다.
# 이름·키워드·이메일에 섞이면 로그(ANSI 시퀀스) 오염·표시 무결성 훼손·이메일 시각적
# 스푸핑(RTL 오버라이드 U+202E)에 악용될 수 있어, 정규화·정리 단계에서 없앤다(정상 입력엔 영향 없음).
# 범위를 코드포인트로 구성한다 — 소스에 보이지 않는 문자를 직접 박아 넣으면 편집·리뷰가 어렵기 때문.
_CONTROL_RANGES = (
    (0x00, 0x1F), (0x7F, 0x9F),           # C0 / DEL / C1
    (0xAD, 0xAD),                         # SOFT HYPHEN
    (0x61C, 0x61C),                       # ARABIC LETTER MARK (양방향)
    (0x180E, 0x180E),                     # MONGOLIAN VOWEL SEPARATOR
    (0x200B, 0x200F),                     # 제로폭 + LRM/RLM
    (0x202A, 0x202E),                     # 양방향 임베딩/오버라이드(RLO 등)
    (0x2060, 0x2064), (0x2066, 0x206F),   # WORD JOINER·불가시 연산자 + 양방향 격리
    (0xFEFF, 0xFEFF),                     # BOM (제로폭 no-break space)
)
_CONTROL_RE = re.compile(
    "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CONTROL_RANGES) + "]"
)


def _strip_controls(text):
    return _CONTROL_RE.sub("", str(text))


def normalize_email(email):
    """이메일을 식별자로 쓰기 전 항상 거치는 정규화(대소문자 무시).

    대부분의 실제 메일 서비스는 로컬파트 대소문자를 구분하지 않으므로,
    'Alice@Example.com'과 'alice@example.com'을 서로 다른 구독자로 만들지 않기 위해
    조회/저장/URL 경로 파라미터 등 이메일이 등장하는 모든 지점에서 이 함수를 거친다.
    """
    return _strip_controls(email).strip().casefold()  # 제어/양방향 문자 제거 후, lower() 대신 casefold(ß 등 유니코드 케이스 폴딩)


def _clean_keywords(keywords):
    """키워드 정리 수행
    
    작업 수행 순서: 앞뒤 공백 제거 → 빈값 제거 → 중복 제거
    """
    # keywords 가 리스트가 아닐 수 있다(구 JSON 이관 등). 문자열이면 글자 단위로 쪼개지지 않게
    # 키워드 1개로 감싸고, 숫자/None/불리언 등은 키워드 없음으로 처리한다(for 루프 TypeError 방지).
    if isinstance(keywords, str):
        keywords = [keywords]
    elif not isinstance(keywords, (list, tuple)):
        keywords = []
    cleaned = []                        # 정리된 키워드를 담을 빈 리스트 정의
    for k in keywords:
        k = _strip_controls(k).strip()  # 제어/양방향 문자 제거 후 공백 제거
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
    if not email or not str(email).strip():
        raise ValueError("email 누락")
    email = normalize_email(email)
    if not _looks_like_email(email):                                        # 이메일 형식 검증
        raise ValueError(f"유효한 이메일 형식이 아닙니다: {email!r}")
    send_hour, send_minute = _parse_send_time(row)                          # 발송 시간에서 시간, 분 분리

    keywords = _clean_keywords(row.get("keywords", []))
    return Subscription(
        email=email,
        keywords=keywords,
        send_hour=send_hour,
        send_minute=send_minute,
        name=_strip_controls(row.get("name", "")).strip(),
        frequency=_pick(row.get("frequency"), config.FREQUENCY, config.FREQUENCY[0]),
        summary_length=_pick(row.get("summary_length"), config.SUMMARY_LENGTH, config.SUMMARY_LENGTH[0]),
        language=_pick(row.get("language"), config.LANGUAGE, config.LANGUAGE[0]),
        confirmed=bool(row.get("confirmed", False)),
    )


def load_subscriptions(path=None):
    """DB(구독자 정리 DB)를 읽어 Subscription 리스트로 반환.

    이때, DB 테이블 혹은 구독자가 없으면 빈 리스트 반환
    잘못된 레코드가 하나 있어도 그 레코드만 건너뛰고 나머지는 정상 처리 (한 명의 잘못된 정보가 그 시각 전체 발송을 막는 일이 발생하지 않도록 하기 위함)
    path: DB 경로(테스트용 주입). 미지정 시 config.DB_PATH.
    """
    subscriptions = []
    for i, row in enumerate(db.fetch_all_subscribers(path)):
        try:
            subscriptions.append(_from_row(row))
        except (ValueError, AttributeError) as exc:         # 잘못된 레코드 -> 예외 처리(시스템 장애 방지)
            print(f"[구독 레코드 건너뜀] #{i}: {exc}")
    return subscriptions


def load_subscriptions_page(limit=-1, offset=0, path=None):
    """DB 에서 한 페이지(LIMIT/OFFSET)만 읽어 검증된 Subscription 리스트로 반환.

    load_subscriptions 와 동일한 검증(잘못된 행은 그 행만 건너뜀)이되 전체를 로드하지 않는다.
    limit=-1 이면 전체(오프셋만 적용).
    """
    # 페이지네이션을 '검증 통과한 행' 기준으로 적용한다 — DB LIMIT/OFFSET 뒤에 파이썬에서 불량 행을
    # 건너뛰면 페이지 창 안의 불량 행이 반환 개수를 줄이고 페이지 경계가 어긋난다(전체 개수와도 불일치).
    # 구독자 규모가 작아 전체 검증 후 슬라이스해도 부담이 없다. limit<0(또는 None)이면 offset 이후 전체.
    valid = load_subscriptions(path)
    valid = valid[offset:] if offset else valid
    if limit is not None and limit >= 0:
        valid = valid[:limit]
    return valid


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
    record 형식이 틀리면(email 누락·시각 범위 오류 등) ValueError
    저장은 검증·정규화된 값(후보에 없는 키워드 제거, 기본값 채움)으로 이뤄진다.
    returns: 저장된 Subscription.
    """
    # 쓰기 경로에서만 enum 값을 엄격 검증한다 — 잘못된 frequency/summary_length/language 를
    # 조용히 기본값으로 바꾸지 않고 거부(400). 읽기 경로(load_subscriptions→_from_row)는 여전히
    # _pick 으로 관대하게 처리해, 잘못된 기존 레코드 하나가 그 시각 전체 발송을 막지 않게 한다.
    for field_name, allowed in (("frequency", config.FREQUENCY),
                                ("summary_length", config.SUMMARY_LENGTH),
                                ("language", config.LANGUAGE)):
        value = record.get(field_name)
        if value is not None and value not in allowed:
            raise ValueError(f"{field_name} 값이 허용 목록에 없습니다: {value!r}")
    # 길이 상한 — 무제한 저장(스토리지 증폭)을 막는다. PUT/import 는 rate limit 이 없어 특히 필요.
    # 검사는 '저장되는 값'(정규화·strip·정리 후) 기준으로 한다 — 앞뒤 공백만으로 상한을 넘겨
    # 정리 후엔 상한 이내인 값이 오거부되지 않도록(검증 기준과 저장 기준을 일치).
    if len(normalize_email(record.get("email") or "")) > 254:
        raise ValueError("이메일이 너무 깁니다(최대 254자)")
    if len(_strip_controls(record.get("name") or "").strip()) > 100:  # 저장되는 값(_from_row 와 동일한 제어문자 제거·strip 후) 기준
        raise ValueError("이름이 너무 깁니다(최대 100자)")
    _kws = _clean_keywords(record.get("keywords"))
    if len(_kws) > 50:
        raise ValueError("키워드가 너무 많습니다(최대 50개)")
    if any(len(k) > 50 for k in _kws):
        raise ValueError("키워드가 너무 깁니다(각 최대 50자)")
    sub = _from_row(record)
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
    이관되는 구독자는 이 기능(이메일 확인) 도입 이전부터 실제로 쓰던 사람들이라,
    확인 메일 없이 바로 confirmed=True 로 넘긴다(db.mark_confirmed).
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
            sub = save_subscription(row, path=db_path)
            db.mark_confirmed(sub.email, path=db_path)
            imported += 1
        except (ValueError, AttributeError) as exc:
            print(f"[구독 가져오기 건너뜀] #{i}: {exc}")
    return imported


def is_due(sub, now):
    """지금(now) 이 구독자에게 발송할 시각인가.

    이메일 미확인(confirmed=False) 구독자는 항상 제외한다 — 소유 확인 전까지는
    어떤 정기 발송도 받지 않는다.
    확인된 구독자는 시:분이 일치하고, now 의 요일이 그 구독자 주기(frequency)의
    발송 요일이면 True. (예: '매주'(월요일) 구독자는 월요일 그 시각에만 True → 매일 중복 발송 방지)
    """
    if not sub.confirmed:
        return False
    # 알 수 없는/손상된 주기는 '매일 발송'으로 오인하지 않고 발송 대상에서 제외한다
    # (is_weekly_anchor 도 unknown→False 로 같은 방향). 정상 경로는 _from_row/_pick 이 주기를 보정한다.
    weekdays = config.FREQUENCY_WEEKDAYS.get(sub.frequency)
    if not weekdays:
        return False
    if sub.send_hour == 24:
        # send_hour=24 = 그 발송 요일의 "자정(다음날 0시)"(프론트 "24:00" 옵션). 실제 발송은 다음날
        # 00:00 이므로, 시각은 0시로 보되 발송 요일 판정도 하루 당겨(어제가 발송 요일인가) 본다.
        # 매일은 매 요일 발송이라 무해하고, 매주(월)·주3회(월수금)는 라벨(요일 24:00)과 실제 발송이
        # 다음날 00:00 로 어긋나던 것을 맞춘다. (datetime.hour 는 0~23 뿐이라 24 를 직접 비교 불가.)
        if not (now.hour == 0 and now.minute == 0):
            return False
        return (now.weekday() - 1) % 7 in weekdays
    if not (sub.send_hour == now.hour and sub.send_minute == now.minute):
        return False
    return now.weekday() in weekdays


def due_subscribers(subscriptions, now):
    """now(datetime) 에 발송해야 하는 구독자만 필터링 (시:분 + 주기별 발송 요일)."""
    return [sub for sub in subscriptions if is_due(sub, now)]


def is_weekly_anchor(sub, now):
    """now 가 이 구독자의 '이번 주 첫 발송 요일'인가 (주간 트렌드 첨부 판정용).

    주간 트렌드는 주기와 무관하게 한 주에 한 번, 그 구독자가 이번 주 처음 발송받는 요일에
    얹는다. 발송 요일 집합(FREQUENCY_WEEKDAYS)의 가장 이른 요일(월=0 기준)을 앵커로 삼아,
    발송 요일 규칙이 바뀌어도(팀 합의로 FREQUENCY_WEEKDAYS 를 고쳐도) 트렌드 첨부가 자동으로
    따라오게 한다 — TREND_WEEKDAY 같은 별도 하드코딩 상수를 두면 그 규칙과 조용히 어긋난다.
    알 수 없는 주기면 False(트렌드 미첨부).
    """
    weekdays = config.FREQUENCY_WEEKDAYS.get(sub.frequency)
    if not weekdays:
        return False
    # send_hour=24 는 '다음날 0시'에 발송되므로(is_due 와 동일하게 하루 당겨 판정), 주간 앵커 요일도
    # 하루 당겨 봐야 24:00 구독자의 주간 트렌드 첨부 요일이 is_due 와 어긋나지 않는다.
    effective_weekday = (now.weekday() - 1) % 7 if sub.send_hour == 24 else now.weekday()
    return effective_weekday == min(weekdays)


def send_window_hours(sub, now):
    """이 발송이 되돌아봐야 할 시간(hours) — 직전 발송 요일까지의 간격.

    뉴스레터는 '지난 발송 이후 ~ 이번 발송' 사이 소식을 커버해야 하므로,
    이 기간은 주기(발송 요일 간격)에 따라 달라진다.
      - 매일          : 어제 이후 → 24h
      - 주 3회(월·수·금): 월=직전 금 이후 72h, 수·금=이틀 전 48h
      - 매주(월)       : 지난주 월 이후 → 168h
    알 수 없는 주기면 기본값(config.SUMMARY_RECENCY_HOURS).
    """
    weekdays = config.FREQUENCY_WEEKDAYS.get(sub.frequency)
    if not weekdays:
        return config.SUMMARY_RECENCY_HOURS
    # send_hour=24 는 라벨 요일의 '다음날 0시'에 발송되므로 now.weekday()가 라벨보다 하루 앞선다.
    # is_due·is_weekly_anchor 와 동일하게 하루 당겨 '직전 발송 요일'을 라벨 기준으로 찾는다 —
    # 안 그러면 매주(월)·주3회 24:00 구독자의 되돌아보기 창이 항상 24h로 붕괴한다.
    today = (now.weekday() - 1) % 7 if sub.send_hour == 24 else now.weekday()
    for gap in range(1, 8):
        if (today - gap) % 7 in weekdays:
            return gap * 24
    return 7 * 24
