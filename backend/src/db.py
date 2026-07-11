import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytz

from src import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    email            TEXT    PRIMARY KEY,            -- 구독자 식별자(1인 1구독)
    name             TEXT    NOT NULL DEFAULT '',
    keywords         TEXT    NOT NULL DEFAULT '[]',  -- JSON 배열 문자열 (예: ["주식","금리"])
    send_hour        INTEGER NOT NULL DEFAULT 0,
    send_minute      INTEGER NOT NULL DEFAULT 0,
    frequency        TEXT,
    summary_length   TEXT,
    language         TEXT,
    confirmed        INTEGER NOT NULL DEFAULT 0,  -- 이메일 소유 확인(더블 옵트인) 여부, 0/1
    confirm_token    TEXT,                         -- 확인 링크용 1회용 토큰 (확인 후 NULL)
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    link         TEXT    NOT NULL,
    description  TEXT,
    published_at TEXT,                 -- ISO 8601 (수집 단계에서 날짜 불명 기사는 이미 걸러짐)
    collected_at TEXT    NOT NULL,     -- ISO 8601
    simhash      TEXT,                 -- 제목+본문 스니펫(description) SimHash(16진수) - 근접 중복(같은 안건) 판정용
    UNIQUE(keyword, link)
);

CREATE TABLE IF NOT EXISTS digests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword           TEXT    NOT NULL,
    summary_length    TEXT    NOT NULL,   -- config.SUMMARY_LENGTH 중 하나
    language          TEXT    NOT NULL,   -- config.LANGUAGE 중 하나
    created_at        TEXT    NOT NULL,
    latest_article_at TEXT                -- 이 스냅샷이 담은 기사 중 가장 최근 발행일(발송 포함 기간 판정용)
);

CREATE TABLE IF NOT EXISTS digest_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id   INTEGER NOT NULL,
    headline    TEXT    NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,   -- LLM이 낸 순서 보존(원래 등장 순)
    FOREIGN KEY(digest_id) REFERENCES digests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS digest_topics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id      INTEGER NOT NULL,
    topic         TEXT    NOT NULL,
    topic_summary TEXT    NOT NULL,
    order_index   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(issue_id) REFERENCES digest_issues(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS digest_links (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    link     TEXT    NOT NULL,
    FOREIGN KEY(topic_id) REFERENCES digest_topics(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sent_articles (
    email   TEXT    NOT NULL,          -- 수신자
    link    TEXT    NOT NULL,          -- 정규화된 기사 링크(재발송 방지 키)
    sent_at TEXT    NOT NULL,          -- ISO 8601 — 보존 기간 정리용
    simhash TEXT,                      -- 발송한 기사의 SimHash - 다음 발송의 근접 중복 판정용
    PRIMARY KEY(email, link)
);

"""


_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(keyword);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
-- 근접 중복 판정이 link 로 SimHash 를 조회한다(WHERE link IN (...)). keyword 선두 복합 인덱스로는
-- link 단독 필터를 못 받으므로 발송/기록마다 풀스캔이 되는 걸 막는 전용 인덱스.
CREATE INDEX IF NOT EXISTS idx_articles_link ON articles(link);
CREATE INDEX IF NOT EXISTS idx_digests_lookup ON digests(keyword, summary_length, language, created_at);
CREATE INDEX IF NOT EXISTS idx_subscribers_confirm_token ON subscribers(confirm_token);
-- digests 이력을 8일 보존하면서 커지므로: 트렌드 집계 JOIN(digest_topics→digest_issues→digests)의
-- 자식쪽 조인 컬럼과, prune_old_digests 의 created_at 단독 삭제를 각각 인덱스로 받쳐준다.
-- (idx_digests_lookup 은 keyword 선두라 created_at 단독 필터엔 못 쓴다)
CREATE INDEX IF NOT EXISTS idx_digest_issues_digest ON digest_issues(digest_id);
CREATE INDEX IF NOT EXISTS idx_digest_topics_issue ON digest_topics(issue_id);
CREATE INDEX IF NOT EXISTS idx_digests_created_at ON digests(created_at);
-- 재발송 방지 기록: 조회는 PK(email, link) 로 받고, 보존 기간 정리(sent_at 단독)는 이 인덱스로.
CREATE INDEX IF NOT EXISTS idx_sent_articles_sent_at ON sent_articles(sent_at);
"""


def _now(now=None):
    """기준 시각. 미지정 시 현재 KST."""
    return now or datetime.now(pytz.timezone(config.TIMEZONE))


def _connect(path=None):
    """DB 커넥션 반환. 상위 디렉터리가 없으면 만든다(첫 실행 대비).

    collect/summarize/dispatch 세 스케줄러 + API 서버가 같은 SQLite 파일에 동시
    접근하므로, sqlite3 기본 타임아웃(5초)보다 넉넉히 잡아 짧은 락 경합에서
    예외 대신 자체적으로 대기하도록 한다(PRAGMA busy_timeout도 동일한 값으로 맞춤).
    """
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    if path != ":memory:":
        # WAL: 스케줄러 프로세스들 + API 서버가 한 파일을 공유하므로 쓰기가 읽기를 막지 않게 한다
        # (기본 롤백저널은 쓰기 동안 읽기를 busy_timeout(최대 15초)까지 차단). WAL 은 파일 DB 에만 의미.
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


# 테이블 마이그레이션 이력 — 기존 DB 파일에 새 컬럼을 ALTER TABLE 로 보정하기 위한 목록.
_COLUMN_MIGRATIONS = {
    "subscribers": {
        "confirmed": "INTEGER NOT NULL DEFAULT 0",
        "confirm_token": "TEXT",
        "access_code": "TEXT",
        "access_code_expires_at": "TEXT",
        "last_sent_at": "TEXT",  # 중복 발송 방지용 — 마지막으로 실제 발송을 마친 시각
    },
    "digests": {
        "latest_article_at": "TEXT",  # 스냅샷이 담은 기사 중 가장 최근 발행일(발송 포함 기간 판정용)
    },
    "articles": {
        "simhash": "TEXT",  # 제목+본문 스니펫(description) SimHash - 근접 중복(같은 안건) 판정용
    },
    "sent_articles": {
        "simhash": "TEXT",  # 발송한 기사의 SimHash - 다음 발송의 근접 중복 판정용
    },
}


def _ensure_columns(conn, table, columns):
    """table 에 없는 컬럼을 ALTER TABLE ADD COLUMN 으로 추가한다(가벼운 자체 마이그레이션)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError as exc:
                # 스케줄러+API 가 새 DB 에 동시에 기동하면 두 프로세스가 같은 컬럼을 각각 ALTER 하다
                # 두 번째가 'duplicate column name' 을 만난다 — 이미 추가됐다는 뜻이라 무시한다.
                # 그 외 OperationalError(락 등)는 그대로 전파해 진짜 문제를 숨기지 않는다.
                if "duplicate column name" not in str(exc).lower():
                    raise


def _normalize_email_casing(conn):
    """subscribers.email 을 전부 소문자로 통일한다(1회성·멱등 마이그레이션).

    이메일 정규화(대소문자 무시)를 나중에 도입했을 때, 이미 대소문자가 섞인 채
    저장된 기존 행까지 소급 적용하기 위한 자체 마이그레이션이다. 같은 메일함이
    대소문자 차이로 두 행(예: alice@x.com / Alice@x.com)에 걸쳐 있었다면,
    확인된(confirmed) 쪽을 남기고(둘 다 같으면 최근 수정된 쪽) 나머지는 버린다
    — 이미 실사용 중이던 확인 상태·설정을 최대한 보존하기 위함.
    이후 저장/조회는 모두 subscriptions.normalize_email 을 거치므로, 이 함수는
    두 번째 실행부터는 아무 것도 바꾸지 않는다(멱등).
    """
    rows = conn.execute("SELECT * FROM subscribers").fetchall()
    groups = {}
    for row in rows:
        groups.setdefault(row["email"].strip().casefold(), []).append(row)  # 런타임 normalize_email(casefold)과 동일 규칙

    for lower_email, group in groups.items():
        group.sort(key=lambda r: (r["confirmed"], r["updated_at"] or ""), reverse=True)
        survivor = group[0]
        for dup in group[1:]:
            conn.execute("DELETE FROM subscribers WHERE email = ?", (dup["email"],))
            print(f"[이메일 정규화] 중복 구독자 병합: {dup['email']!r} → {lower_email!r} (삭제됨)")
        if survivor["email"] != lower_email:
            conn.execute(
                "UPDATE subscribers SET email = ? WHERE email = ?",
                (lower_email, survivor["email"]),
            )


def init_db(path=None):
    """테이블/인덱스를 생성한다(이미 있으면 그대로 둔다).

    이미 있는 DB 파일에 스키마가 새 컬럼을 추가하는 방향으로 바뀐 경우,
    파일을 지우지 않아도 되도록 _COLUMN_MIGRATIONS 로 가볍게 보정한다
    (예: subscribers 테이블에 confirmed/confirm_token 이 나중에 추가됨).
    """
    # closing(): sqlite3 커넥션을 명시적으로 닫아, 파일 삭제/수정이 막히는 현상을 방지한다.
    with closing(_connect(path)) as conn, conn:
        conn.executescript(_SCHEMA)  # 1) 테이블 생성(없으면)
        for table, columns in _COLUMN_MIGRATIONS.items():  # 2) 기존 테이블 컬럼 보정
            _ensure_columns(conn, table, columns)
        conn.executescript(_INDEXES)  # 3) 인덱스(보정된 컬럼 참조 가능)
        _normalize_email_casing(conn)  # 4) 기존 행의 이메일 대소문자 통일(1회성·멱등)


# ─────────────────────────────────────────────────────────────
# 구독자 (subscribers) — 프론트 대시보드가 쓰고 백엔드가 읽는다
# ─────────────────────────────────────────────────────────────

def upsert_subscriber(record, now=None, path=None):
    """구독자 1명을 저장(있으면 갱신). email 을 키로 하는 UPSERT.

    record: {"email", "name", "keywords"(list), "send_hour", "send_minute",
             "frequency", "summary_length", "language", }
    keywords 리스트는 JSON 문자열로 직렬화해 저장한다.
    검증(키워드 필터·시간 범위 등)은 subscriptions.save_subscription 이 맡고,
    여기서는 저장만 한다.

    confirmed/confirm_token 은 이 함수가 직접 다루지 않는다 — 신규 구독자는
    확인 전(confirmed=0) 상태로 새 토큰을 받고, 이미 있는 구독자는(정보만 수정하는
    경우) 기존 확인 상태·토큰이 그대로 유지된다(ON CONFLICT 절에 두 컬럼을 넣지 않음).
    """
    ts = _now(now).isoformat()
    new_token = secrets.token_urlsafe(24)  # 신규 삽입일 때만 실제로 쓰임(갱신이면 무시됨)
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            """INSERT INTO subscribers
                 (email, name, keywords, send_hour, send_minute,
                  frequency, summary_length, language, confirmed, confirm_token, updated_at)
               VALUES
                 (:email, :name, :keywords, :send_hour, :send_minute,
                  :frequency, :summary_length, :language, 0, :confirm_token, :updated_at)
               ON CONFLICT(email) DO UPDATE SET
                  name=excluded.name,
                  keywords=excluded.keywords,
                  send_hour=excluded.send_hour,
                  send_minute=excluded.send_minute,
                  frequency=excluded.frequency,
                  summary_length=excluded.summary_length,
                  language=excluded.language,
                  updated_at=excluded.updated_at,
                  -- 미확인인데 토큰이 없는 경우에만 새 토큰을 발급한다. 확인 전 컬럼이 나중에
                  -- 추가돼(마이그레이션) 기존 행이 confirmed=0·token=NULL 로 남으면, 재구독해도
                  -- 토큰이 안 나와 확인 메일이 영영 안 가고 복구 경로가 없던 문제를 막는다.
                  -- (미확인+토큰 有 → 기존 토큰 재사용 / 확인됨 → NULL 유지)
                  confirm_token=CASE
                      WHEN subscribers.confirmed = 0 AND subscribers.confirm_token IS NULL
                      THEN excluded.confirm_token ELSE subscribers.confirm_token END""",
            {
                "email": record["email"],
                "name": record.get("name", ""),
                "keywords": json.dumps(record.get("keywords", []), ensure_ascii=False),
                "send_hour": int(record.get("send_hour", 0)),
                "send_minute": int(record.get("send_minute", 0)),
                "frequency": record.get("frequency"),
                "summary_length": record.get("summary_length"),
                "language": record.get("language"),
                "confirm_token": new_token,
                "updated_at": ts,
            },
        )


def delete_subscriber(email, path=None):
    """이메일로 구독자를 삭제. 삭제된 행 수를 반환(없으면 0)."""
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute("DELETE FROM subscribers WHERE email = ?", (email,))
        return cur.rowcount


def fetch_all_subscribers(path=None):
    """전체 구독자를 행(dict) 리스트로 반환. keywords 는 리스트로 복원한다.

    아직 테이블이 없으면(init 전 등) 빈 리스트를 반환한다(구독자 없음).
    """
    try:
        with closing(_connect(path)) as conn:
            rows = conn.execute(
                """SELECT email, name, keywords, send_hour, send_minute,
                          frequency, summary_length, language, confirmed
                   FROM subscribers ORDER BY email"""
            ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"[DB 경고] fetch_all_subscribers 실패({exc}) - 이번 호출은 빈 목록으로 처리됩니다")
        return []

    result = []
    for row in rows:
        record = dict(row)
        try:
            record["keywords"] = json.loads(record["keywords"]) if record["keywords"] else []
        except (json.JSONDecodeError, TypeError):
            record["keywords"] = []
        record["confirmed"] = bool(record["confirmed"])
        result.append(record)
    return result


def fetch_subscriber(email, path=None):
    """이메일로 구독자 1명을 행(dict)으로 반환. 없으면(또는 테이블 없음) None.

    keywords 는 리스트로 복원한다.
    """
    try:
        with closing(_connect(path)) as conn:
            row = conn.execute(
                """SELECT email, name, keywords, send_hour, send_minute,
                          frequency, summary_length, language, confirmed
                   FROM subscribers WHERE email = ?""",
                (email,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        print(f"[DB 경고] fetch_subscriber({email!r}) 실패({exc}) - 이번 호출은 None으로 처리됩니다")
        return None
    if row is None:
        return None
    record = dict(row)
    try:
        record["keywords"] = json.loads(record["keywords"]) if record["keywords"] else []
    except (json.JSONDecodeError, TypeError):
        record["keywords"] = []
    record["confirmed"] = bool(record["confirmed"])
    return record


def count_subscribers(path=None):
    """구독자 수. 테이블이 없으면 0."""
    try:
        with closing(_connect(path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
    except sqlite3.OperationalError as exc:
        print(f"[DB 경고] count_subscribers 실패({exc}) - 이번 호출은 0으로 처리됩니다")
        return 0


def subscriber_stats(path=None):
    """관리자 대시보드용 통계를 SQL 집계로 계산해 dict 로 반환(전체 행을 파이썬으로 로드하지 않는다).

    반환: {total_subscribers, confirmed_count, most_common_frequency, most_common_language}.
    테이블이 없거나 비어 있으면 0/'없음' 을 준다.
    """
    def _top(conn, column):  # column 은 고정 리터럴(frequency/language)이라 주입 위험 없음
        row = conn.execute(
            f"SELECT {column} FROM subscribers "
            f"WHERE {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY COUNT(*) DESC, {column} LIMIT 1"
        ).fetchone()
        return row[0] if row else "없음"
    try:
        with closing(_connect(path)) as conn:
            total, confirmed = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(confirmed), 0) FROM subscribers"
            ).fetchone()
            freq = _top(conn, "frequency")
            lang = _top(conn, "language")
    except sqlite3.OperationalError as exc:
        print(f"[DB 경고] subscriber_stats 실패({exc}) - 0/'없음'으로 처리됩니다")
        return {"total_subscribers": 0, "confirmed_count": 0,
                "most_common_frequency": "없음", "most_common_language": "없음"}
    return {
        "total_subscribers": total,
        "confirmed_count": confirmed,
        "most_common_frequency": freq,
        "most_common_language": lang,
    }


def fetch_subscribers_page(limit=-1, offset=0, path=None):
    """구독자 한 페이지를 행(dict) 리스트로 반환(SQL LIMIT/OFFSET). limit=-1 이면 전체.

    fetch_all_subscribers 와 같은 정렬(email)·행 형태(keywords 리스트 복원)이되, 전체를
    메모리로 올린 뒤 자르지 않고 DB 에서 필요한 페이지만 가져온다.
    """
    try:
        with closing(_connect(path)) as conn:
            rows = conn.execute(
                """SELECT email, name, keywords, send_hour, send_minute,
                          frequency, summary_length, language, confirmed
                   FROM subscribers ORDER BY email LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"[DB 경고] fetch_subscribers_page 실패({exc}) - 이번 호출은 빈 목록으로 처리됩니다")
        return []
    result = []
    for row in rows:
        record = dict(row)
        try:
            record["keywords"] = json.loads(record["keywords"]) if record["keywords"] else []
        except (json.JSONDecodeError, TypeError):
            record["keywords"] = []
        record["confirmed"] = bool(record["confirmed"])
        result.append(record)
    return result


def fetch_confirm_token(email, path=None):
    """이메일의 현재 확인 토큰을 반환. 이미 확인됐거나(토큰 폐기됨) 없는 이메일이면 None.
    """
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT confirm_token FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
    return row["confirm_token"] if row else None


def peek_confirm_token(token, path=None):
    """토큰이 가리키는 이메일을 확정 없이 조회한다(확인 페이지 표시용). 없으면 None."""
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT email FROM subscribers WHERE confirm_token = ?", (token,)
        ).fetchone()
    return row["email"] if row else None


def confirm_subscriber(token, path=None):
    """구독자 처리 후 토큰 폐기 수행

    returns: 확인된 이메일. 토큰이 존재하지 않으면(이미 쓰였거나 잘못된 값) None.
    """
    with closing(_connect(path)) as conn, conn:
        row = conn.execute(
            "SELECT email FROM subscribers WHERE confirm_token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE subscribers SET confirmed = 1, confirm_token = NULL WHERE confirm_token = ?",
            (token,),
        )
        if cur.rowcount != 1:
            # 동시 확인 경쟁 — 다른 요청이 SELECT 와 UPDATE 사이에 같은 토큰을 먼저 소진했다.
            # '내가 실제로 비운' 경우(rowcount==1)만 성공 처리해 1회용(이중 성공 방지)을 보장한다.
            return None
    return row["email"]


def generate_access_code(email, ttl_minutes=None, now=None, path=None):
    """셀프서비스 본인 확인 코드 발급(confirm_token과 달리 만료 전까진 재사용 가능). 없는 이메일이면 None.

    코드는 32비트(8자리 hex ≈ 43억 경우의 수)로 만든다. 예전 24비트(6자리)는 여러 IP를 돌려가며
    TTL(기본 15분) 안에 특정 이메일을 표적 브루트포스할 여지가 있었다 — 조회→수정의 2단계 흐름
    때문에 코드를 1회용으로 폐기할 수는 없어(재사용 필요), 대신 탐색 공간을 키워 방어한다.
    """
    ttl_minutes = config.ACCESS_CODE_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    code = secrets.token_hex(4).upper()
    expires_at = (_now(now) + timedelta(minutes=ttl_minutes)).isoformat()
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute(
            "UPDATE subscribers SET access_code = ?, access_code_expires_at = ? WHERE email = ?",
            (code, expires_at, email),
        )
    return code if cur.rowcount else None


def verify_access_code(email, code, now=None, path=None):
    """본인 확인 코드가 일치하고 만료 전인지 확인."""
    if not code:
        return False
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT access_code, access_code_expires_at FROM subscribers WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None or not row["access_code"]:
        return False
    try:
        # 저장 코드는 대문자(token_hex(4).upper())라, 사용자가 소문자/공백을 섞어 입력해도
        # 통과하도록 입력을 정규화(트림·대문자)한 뒤 상수시간 비교한다.
        if not hmac.compare_digest(row["access_code"], str(code).strip().upper()):
            return False
    except TypeError:
        # 비ASCII 코드가 헤더로 오면 compare_digest 가 TypeError — 500 대신 인증 실패로 처리.
        return False
    try:
        expires_at = datetime.fromisoformat(row["access_code_expires_at"])
    except (TypeError, ValueError):
        return False
    return _now(now) < expires_at


def peek_access_code(email, path=None):
    """(테스트용) 발급된 확인 코드 반환."""
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT access_code FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
    return row["access_code"] if row else None


def claim_dispatch(email, now=None, within_seconds=90, path=None):
    """이번 발송 슬롯을 원자적으로 선점한다(중복 발송 방지). 선점 성공 시 True.

    within_seconds 안에 발송한 적(last_sent_at 갱신)이 없을 때만 last_sent_at 을 지금으로
    올리고 True 를 반환한다. 조건부 UPDATE 한 방이라, 분 단위 디스패처(dispatch_job)가
    같은 틱에 두 프로세스로 겹쳐 돌아도(예: 롤링 배포 중 신·구 프로세스가 겹치는 구간)
    정확히 한 쪽만 rowcount=1 로 선점해 발송하고 나머지는 False 로 건너뛴다.
    '읽어서 확인 → 나중에 표시'로 나누면 그 사이가 레이스라, 선점을 조건부 UPDATE 하나로
    원자화한다. 정상적인 다음 발송 슬롯(최소 30분 뒤)까지는 걸리지 않는다.
    """
    now = _now(now)
    cutoff = (now - timedelta(seconds=within_seconds)).isoformat()
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute(
            """UPDATE subscribers SET last_sent_at = ?
               WHERE email = ? AND (last_sent_at IS NULL OR last_sent_at < ?)""",
            (now.isoformat(), email, cutoff),
        )
    return cur.rowcount == 1


def mark_confirmed(email, path=None):
    """구독자를 확인됨으로 표시하고 토큰을 폐기한다.

    확인 메일 절차를 거치지 않은 신뢰 가능한 경로(기존 JSON 이관 등)에서만 쓴다 —
    이미 실제로 쓰던 구독자를 이 기능 도입 시점에 재확인 없이 넘겨받는 용도.
    """
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "UPDATE subscribers SET confirmed = 1, confirm_token = NULL WHERE email = ?", (email,)
        )


# ─────────────────────────────────────────────────────────────
# 재발송 방지 기록 (sent_articles) — 구독자별로 이미 받은 기사를 다음 발송에서 뺀다
# ─────────────────────────────────────────────────────────────

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "spm", "ref", "ref_src"}


def _normalize_link(link):
    """재발송 방지 비교용 링크 정규화.

    추적용 쿼리 파라미터(utm_*, fbclid 등)만 제거하고 나머지 쿼리는 보존한다 — 네이버 뉴스처럼
    oid/aid 쿼리가 '기사 식별자'인 경우 쿼리를 통째로 지우면 서로 다른 기사가 같게 뭉개지기 때문.
    추가로 스킴/호스트 소문자화 + 프래그먼트 제거 + 경로 끝 슬래시 제거 + 쿼리 파라미터 정렬을 한다
    (파라미터 순서만 바뀐 같은 기사(?oid=1&aid=2 vs ?aid=2&oid=1)를 같은 키로 본다 — 키가 달라도
    서로 다른 기사는 여전히 다른 키라 안 뭉개진다).
    """
    if not link:
        return ""
    try:
        parts = urlsplit(link.strip())
    except ValueError:
        return link.strip()
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PARAM_PREFIXES) and k.lower() not in _TRACKING_PARAMS
    )
    # 경로 양끝 공백을 rstrip('/') '전에' 제거한다 — 스킴/호스트 없는 비정상 입력('abc/ #5' 등)에서
    # 공백을 나중에 없애면 그때 드러난 끝 슬래시가 남아 norm(norm(x)) != norm(x) 가 된다.
    # 이 순서라야 멱등이 보장된다(정상 URL 은 경로에 공백이 없어 영향 없음).
    path = parts.path.strip().rstrip("/") or parts.path.strip()
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")).strip()


_SIMHASH_BITS = 64


def _simhash(text):
    """제목+본문 스니펫(description) 텍스트의 문자 3-gram SimHash(64비트, 16진수 문자열). 근접 중복(같은 안건) 탐지용.

    결정론적 해시(blake2b)라 실행/프로세스마다 값이 같다 - DB 에 저장해두고 다음 발송과 비교한다.
    문자 3-gram 이라 한국어 형태소 분석 없이도 '거의 같은 글'이 작은 Hamming 거리를 갖는다.
    텍스트가 3글자 미만이면 None(판정 제외).
    """
    text = " ".join((text or "").split()).lower()
    if len(text) < 3:
        return None
    weights = {}
    for i in range(len(text) - 2):
        gram = text[i:i + 3]
        weights[gram] = weights.get(gram, 0) + 1
    vector = [0] * _SIMHASH_BITS
    for gram, weight in weights.items():
        h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(_SIMHASH_BITS):
            vector[bit] += weight if (h >> bit) & 1 else -weight
    fingerprint = 0
    for bit in range(_SIMHASH_BITS):
        if vector[bit] > 0:
            fingerprint |= 1 << bit
    return f"{fingerprint:016x}"


def _hamming_hex(a, b):
    """두 16진수 SimHash 문자열의 Hamming 거리(서로 다른 비트 수)."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def fetch_seen_links(email, links, path=None):
    """email 이 이미 받은(발송 내역에 있는) 링크만 골라 반환.

    입력 links(원본 문자열들) 중 정규화 형태가 발송 내역에 있는 것들의 '원본'을 set 으로 돌려준다.
    """
    norm_map = {}
    for link in links:
        norm_map.setdefault(_normalize_link(link), []).append(link)
    norms = [n for n in norm_map if n]
    if not norms:
        return set()
    seen = set()
    try:
        with closing(_connect(path)) as conn:
            # SQLite 변수 한도(구버전 999) 대비 IN 절을 청크로 나눠 조회한다.
            for i in range(0, len(norms), 400):
                chunk = norms[i:i + 400]
                placeholders = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT link FROM sent_articles WHERE email = ? AND link IN ({placeholders})",
                    (email, *chunk),
                ).fetchall():
                    seen.update(norm_map.get(row["link"], []))
    except sqlite3.OperationalError as exc:
        # 일시적 DB 락 등으로 발송 내역 조회가 실패하면 '아무것도 안 본 것'으로 간주해 발송은 진행한다
        # — 그 틱을 통째로 놓치는 것보다 낫다(이미 본 기사가 한 번 더 나갈 수는 있으나 발송은 성사).
        print(f"[발송 내역 조회 경고] {email}: {exc} - 이번엔 재발송 방지를 건너뜁니다")
        return set()
    return seen


def fetch_seen_or_similar(email, links, path=None):
    """email 이 이미 받은 링크 + 근접 중복(같은 안건) 기사를 '이미 본 것'으로 골라 반환.

    두 층을 합친다:
      1) 완전 일치 - 정규화 링크가 발송 내역에 있음(fetch_seen_links, 기존 재발송 방지).
      2) 근접 중복 - 그 링크 기사의 SimHash 가 이 사람이 이미 받은 기사 SimHash 와
         Hamming <= NEAR_DUP_HAMMING_MAX. 링크가 달라도 제목+본문 스니펫(description)이 거의 같은 전재/경미
         수정 기사를 같은 안건으로 본다. 임계값을 작게 둬 서로 다른 안건 오합병(진짜 뉴스
         누락)을 피한다.
    반환: 입력 links(원본) 중 빼야 할 링크 set. 근접 중복 조회가 실패하면(락 등) 완전 일치분만
    돌려줘 발송을 막지 않는다. config.NEAR_DUP_HAMMING_MAX 가 음수면 근접 중복은 끈다.
    """
    seen = fetch_seen_links(email, links, path)
    if config.NEAR_DUP_HAMMING_MAX < 0:
        return seen
    remaining = [link for link in links if link and link not in seen]
    if not remaining:
        return seen
    try:
        with closing(_connect(path)) as conn:
            sent = [
                r["simhash"] for r in conn.execute(
                    "SELECT DISTINCT simhash FROM sent_articles WHERE email = ? AND simhash IS NOT NULL",
                    (email,),
                ).fetchall()
            ]
            if not sent:
                return seen
            candidates = {}
            for i in range(0, len(remaining), 400):
                chunk = remaining[i:i + 400]
                placeholders = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT link, simhash FROM articles WHERE simhash IS NOT NULL AND link IN ({placeholders})",
                    chunk,
                ).fetchall():
                    candidates.setdefault(row["link"], row["simhash"])
    except sqlite3.OperationalError as exc:
        print(f"[근접 중복 조회 경고] {email}: {exc} - 이번엔 근접 중복 판정을 건너뜁니다")
        return seen
    limit = config.NEAR_DUP_HAMMING_MAX
    sent_ints = [int(s, 16) for s in sent]  # 후보마다 재파싱하지 않도록 한 번만 정수화
    for link, fingerprint in candidates.items():
        fp_int = int(fingerprint, 16)
        if any(bin(fp_int ^ s).count("1") <= limit for s in sent_ints):
            seen.add(link)
    return seen


def record_sent_articles(email, links, now=None, path=None):
    """email 에게 방금 발송한 기사 링크를 발송 내역에 기록(정규화 후, 이미 있으면 무시).

    각 링크의 기사 SimHash 도 함께 저장한다 - 다음 발송에서 링크가 다른 근접 중복(같은 안건)을
    걸러내는 데 쓴다. 기사가 이미 정리됐으면 SimHash 는 NULL(그 링크는 완전 일치로만 걸림).
    """
    ts = _now(now).isoformat()
    norm_to_orig = {}
    for link in links:
        if not link:
            continue
        norm = _normalize_link(link)
        if norm:
            norm_to_orig.setdefault(norm, link)
    if not norm_to_orig:
        return
    with closing(_connect(path)) as conn, conn:
        originals = list(norm_to_orig.values())
        simmap = {}
        for i in range(0, len(originals), 400):
            chunk = originals[i:i + 400]
            placeholders = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT link, simhash FROM articles WHERE link IN ({placeholders})", chunk
            ).fetchall():
                simmap[row["link"]] = row["simhash"]
        conn.executemany(
            "INSERT OR IGNORE INTO sent_articles (email, link, sent_at, simhash) VALUES (?, ?, ?, ?)",
            [(email, norm, ts, simmap.get(orig)) for norm, orig in norm_to_orig.items()],
        )


def prune_sent_articles(now=None, hours=None, path=None):
    """보존 기간(SENT_ARTICLE_RETENTION_HOURS)보다 오래된 발송 내역 항목을 정리. 삭제 수 반환."""
    hours = config.SENT_ARTICLE_RETENTION_HOURS if hours is None else hours
    cutoff = (_now(now) - timedelta(hours=hours)).isoformat()
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute("DELETE FROM sent_articles WHERE sent_at < ?", (cutoff,))
        return cur.rowcount


# ─────────────────────────────────────────────────────────────
# 뉴스 (articles) / 요약 다이제스트 (digests → digest_issues → digest_topics → digest_links)
# ─────────────────────────────────────────────────────────────

def save_articles(articles_by_keyword, now=None, path=None):
    """정제된 뉴스({keyword: [cleaned_item]})를 articles 에 저장.

    cleaned_item: {"title", "link", "description", "published_at"}
    같은 (keyword, link) 은 이미 있으면 건너뛴다(재수집해도 중복 저장 안 됨).
    returns: 새로 저장된 기사 수.
    """
    ts = _now(now).isoformat()
    inserted = 0
    with closing(_connect(path)) as conn, conn:
        for keyword, items in articles_by_keyword.items():
            for item in items:
                link = item.get("link", "")
                title = item.get("title", "")
                if not link or not title:
                    continue  # 링크/제목 없는 불완전 기사는 저장하지 않음
                description = item.get("description", "")
                cur = conn.execute(
                    """INSERT OR IGNORE INTO articles
                       (keyword, title, link, description, published_at, collected_at, simhash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (keyword, title, link, description,
                     item.get("published_at"), ts, _simhash(f"{title} {description}")),
                )
                inserted += cur.rowcount
    return inserted


def prune_old_articles(now=None, hours=None, path=None):
    """RECENCY_HOURS(또는 지정한 hours)보다 오래된 기사를 삭제한다.

    fetch_articles_for_keyword 가 이 기간보다 오래된 기사는 애초에 요약 대상으로 삼지
    않으므로, DB에 남겨둬도 다시 쓰일 일이 없다 — 저장 공간만 차지하는 죽은 데이터.
    collect_job 이 돌 때마다(수집 직후) 호출해 articles 테이블이 무한히 커지지 않게 한다
    (digests 가 조합당 최신 1건만 남기는 것과 같은 이유의 자체 정리).
    returns: 삭제된 기사 수.
    """
    hours = config.RECENCY_HOURS if hours is None else hours
    cutoff = _now(now) - timedelta(hours=hours)
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute("DELETE FROM articles WHERE published_at < ?", (cutoff.isoformat(),))
        return cur.rowcount


def fetch_articles_for_keyword(keyword, now=None, hours=None, path=None):
    """keyword 에 해당하는 최근 기사를 cleaned_item 형태 리스트로 반환 (요약 잡 입력용).

    item: {"title", "link", "description", "published_at"}
    hours 미지정 시 config.RECENCY_HOURS(수집 보관 기간 전체, 기본 7일)를 쓴다
    — 즉 DB에 아직 남아있는 그 키워드의 기사 전부를 요약 대상으로 삼는다.
    recency 필터를 SQL WHERE 절에서 처리한다(Python 에서 다 읽어와 거르지 않음) —
    published_at 은 항상 타임존 포함 ISO 8601 문자열이라 사전식 비교가 시간 순서와 일치한다.
    """
    hours = config.RECENCY_HOURS if hours is None else hours
    cutoff = _now(now) - timedelta(hours=hours)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            """SELECT title, link, description, published_at
               FROM articles WHERE keyword = ? AND published_at >= ?
               ORDER BY published_at DESC""",
            (keyword, cutoff.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def group_digest_rows(rows):
    """summarizer 가 반환한 평평한(flat) 요약 행을 이슈→주제→기사 계층으로 묶는다.

    rows: [{"headline", "topic", "topic_summary", "link"}, ...]
    같은 headline 을 가진 행들은 한 이슈로, 그 안에서 같은 topic 을 가진 행들은 한
    주제로 묶이고, 그 주제의 link 들은 등장 순서를 유지한 채 중복 제거된다.
    (headline/topic 은 처음 등장한 순서를 유지 — LLM 이 매긴 중요도 순서로 간주)
    returns: [{"headline": str, "topics": [{"topic": str, "topic_summary": str,
              "links": [str, ...]}, ...]}, ...]
    """
    issues = {}
    order = []
    for row in rows:
        headline = row.get("headline", "")
        topic = row.get("topic", "")
        link = row.get("link", "")
        if headline not in issues:
            issues[headline] = {"topics": {}, "topic_order": []}
            order.append(headline)
        issue = issues[headline]
        if topic not in issue["topics"]:
            issue["topics"][topic] = {"topic_summary": row.get("topic_summary", ""), "links": []}
            issue["topic_order"].append(topic)
        links = issue["topics"][topic]["links"]
        if link and link not in links:
            links.append(link)

    return [
        {
            "headline": headline,
            "topics": [
                {
                    "topic": topic,
                    "topic_summary": issues[headline]["topics"][topic]["topic_summary"],
                    "links": issues[headline]["topics"][topic]["links"],
                }
                for topic in issues[headline]["topic_order"]
            ],
        }
        for headline in order
    ]


def _digest_cutoff(now=None, hours=None):
    """다이제스트 보존 경계 시각(ISO). 이보다 오래된 다이제스트는 정리 대상.

    save_digest(조합별 정리)와 prune_old_digests(전체 정리)가 같은 보존 기준을 공유하도록
    컷오프 계산을 한곳에 둔다 — 보존 정책을 바꿀 때 두 곳이 어긋나지 않게.
    """
    hours = config.DIGEST_RECENCY_HOURS if hours is None else hours
    return (_now(now) - timedelta(hours=hours)).isoformat()


def save_digest(keyword, summary_length, language, rows, now=None, latest_article_at=None, path=None):
    """summarizer 의 평평한 요약 행을 이슈→주제→기사 계층으로 묶어 새 다이제스트로 저장.

    rows 가 비어있으면 저장하지 않고 None 을 반환한다.
    발송(fetch_digests_for_keywords)은 조합당 '최신' 다이제스트 1개만 쓰지만, 주간 트렌드
    키워드 집계(get_top_topic_articles)가 지난 며칠치 이력을 훑어야 하므로 예전 스냅샷을 즉시
    지우지 않고 DIGEST_RECENCY_HOURS 만큼 보존한다 — 그보다 오래된 것만 이번에 정리한다
    (하위 issue/topic/link 는 FK ON DELETE CASCADE 로 함께 삭제).
    latest_article_at: 이 스냅샷이 담은 기사 중 가장 최근 발행일(ISO). 발송 시 구독자 포함 기간 안에
        실제 새 기사가 있을 때만 보내는 판정에 쓴다 — 새 뉴스가 없으면 30분마다 재요약돼 스냅샷의
        created_at 은 늘 최신이라, 같은 옛 기사를 매일 반복 발송하는 걸 이 값으로 막는다.
    returns: 생성된 digest_id (rows 가 비었으면 None).
    """
    issues = group_digest_rows(rows)
    if not issues:
        return None
    now = _now(now)
    ts = now.isoformat()
    cutoff = _digest_cutoff(now)
    with closing(_connect(path)) as conn, conn:
        digest_id = conn.execute(
            "INSERT INTO digests (keyword, summary_length, language, created_at, latest_article_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (keyword, summary_length, language, ts, latest_article_at),
        ).lastrowid
        for i_idx, issue in enumerate(issues):
            issue_id = conn.execute(
                "INSERT INTO digest_issues (digest_id, headline, order_index) VALUES (?, ?, ?)",
                (digest_id, issue["headline"], i_idx),
            ).lastrowid
            for t_idx, topic in enumerate(issue["topics"]):
                topic_id = conn.execute(
                    """INSERT INTO digest_topics (issue_id, topic, topic_summary, order_index)
                       VALUES (?, ?, ?, ?)""",
                    (issue_id, topic["topic"], topic["topic_summary"], t_idx),
                ).lastrowid
                conn.executemany(
                    "INSERT INTO digest_links (topic_id, link) VALUES (?, ?)",
                    [(topic_id, link) for link in topic["links"]],
                )
        conn.execute(
            """DELETE FROM digests
               WHERE keyword = ? AND summary_length = ? AND language = ? AND id != ? AND created_at < ?""",
            (keyword, summary_length, language, digest_id, cutoff),
        )
    return digest_id


def prune_old_digests(now=None, hours=None, path=None):
    """DIGEST_RECENCY_HOURS(또는 지정한 hours)보다 오래된 다이제스트를 삭제한다.

    save_digest 는 같은 (keyword, summary_length, language) 조합이 다시 저장될 때만
    오래된 스냅샷을 정리하므로, 구독이 끊기는 등 더는 갱신되지 않는 조합의 이력은 여기서
    별도로 정리한다(prune_old_articles 와 같은 이유의 자체 정리).
    returns: 삭제된 다이제스트 수.
    """
    cutoff = _digest_cutoff(now, hours)
    with closing(_connect(path)) as conn, conn:
        cur = conn.execute("DELETE FROM digests WHERE created_at < ?", (cutoff,))
        return cur.rowcount


def get_top_topics(keyword, since, limit=None, path=None):
    """keyword 의 since 이후 다이제스트에 등장한 topic 을 '등장한 날 수' 순으로 상위 limit개 반환.

    summary_length/language 구분 없이 그 keyword 로 쌓인 모든 다이제스트를 훑는다 —
    "무엇이 자주 다뤄졌는지"는 구독자가 고른 요약 길이/언어와 무관한 신호이기 때문.
    집계 기준은 스냅샷 '개수'가 아니라 '서로 다른 날짜 수'다. summarize_job 이 30분마다
    같은 기사를 재요약해 스냅샷을 계속 새로 만들기 때문에, 개수로 세면 오래 우려먹힌 이슈가
    하루에도 ~48건씩 쌓여 순위를 지배한다 — 날짜 단위로 세면 그 30분 중복 팽창이 사라지고
    "이번 주 며칠에 걸쳐 다뤄졌나"라는 지속성 신호가 된다. 동률이면 총 등장 수, 그다음 이름 순.
    (LLM이 매 실행마다 topic 을 새로 지어내므로, 같은 사안도 표현이 갈리면 따로 집계되는 근사치)
    returns: [(topic, days), ...] 등장일 수 내림차순.
    """
    limit = config.TREND_TOP_N if limit is None else limit
    since = since.isoformat() if hasattr(since, "isoformat") else since
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            # created_at 은 항상 KST(+09:00) ISO 문자열이라 앞 10글자(YYYY-MM-DD)가 곧 KST 날짜다.
            # SQLite DATE() 는 오프셋을 UTC로 환산해 09:00 경계에서 하루가 어긋나므로 substr 로 자른다.
            """SELECT dt.topic AS topic,
                      COUNT(DISTINCT substr(d.created_at, 1, 10)) AS days,
                      COUNT(*) AS cnt
               FROM digest_topics dt
               JOIN digest_issues di ON di.id = dt.issue_id
               JOIN digests d ON d.id = di.digest_id
               WHERE d.keyword = ? AND d.created_at >= ?
               GROUP BY dt.topic
               ORDER BY days DESC, cnt DESC, dt.topic ASC
               LIMIT ?""",
            (keyword, since, limit),
        ).fetchall()
    return [(row["topic"], row["days"]) for row in rows]


def get_top_topic_articles(keyword, since, language=None, limit=None, path=None):
    """get_top_topics 와 같은 순위로, 각 상위 topic 의 요약과 관련 기사 링크까지 함께 반환.

    주간 트렌드를 '키워드 나열'이 아니라 '토픽 + 요약 + 관련 기사'로 보여주기 위한 함수.
    관련 기사는 그 주에 이미 요약·저장해 둔 다이제스트의 것을 재사용한다(추가 수집/LLM 호출 없음)
    — 같은 topic 이 여러 스냅샷에 걸쳐 있으면 가장 최근 다이제스트의 요약·링크를 대표로 쓴다.
    language 를 주면 그 언어로 만든 다이제스트만 훑는다 — topic 제목·요약이 언어별 문자열이라,
    한국어 구독자에게 (같은 키워드를 구독하는) 영어 구독자용 영문 topic·요약이 섞여 나가는 걸 막는다.
    (summary_length 는 표시 문자열이 같은 언어라 구분하지 않는다 — 무엇이 자주 다뤄졌나는 길이와 무관.)
    returns: [{"topic", "article_count", "summary", "links": [str,...]}, ...] 관련 기사 수 내림차순.
    """
    limit = config.TREND_TOP_N if limit is None else limit
    since = since.isoformat() if hasattr(since, "isoformat") else since
    lang_clause = " AND d.language = ?" if language else ""
    base_params = [keyword, since] + ([language] if language else [])
    with closing(_connect(path)) as conn:
        ranked = conn.execute(
            # 순위 기준 = 그 topic 에 붙은 '서로 다른 관련 기사 수'(링크 중복 제거). 예전엔 '등장한 날 수'
            # 였는데, summarize_job 이 30분마다 같은 기사를 재요약해 새 스냅샷을 만들어서 하루짜리 뉴스도
            # 최대 7일로 부풀려졌다(대부분 topic 이 상한에 몰려 사실상 tie-break 로만 갈림). 같은 기사는
            # 같은 링크라 DISTINCT dl.link 로 세면 그 재요약 팽창에 면역이고 "얼마나 많은 기사가 다뤘나"가 된다.
            f"""SELECT dt.topic AS topic,
                      COUNT(DISTINCT dl.link) AS article_count,
                      COUNT(*) AS cnt
               FROM digest_topics dt
               JOIN digest_issues di ON di.id = dt.issue_id
               JOIN digests d ON d.id = di.digest_id
               LEFT JOIN digest_links dl ON dl.topic_id = dt.id
               WHERE d.keyword = ? AND d.created_at >= ?{lang_clause}
               GROUP BY dt.topic
               ORDER BY article_count DESC, cnt DESC, dt.topic ASC
               LIMIT ?""",
            (*base_params, limit),
        ).fetchall()
        result = []
        for row in ranked:
            latest = conn.execute(
                # 그 topic 이 등장한 가장 최근 다이제스트의 요약·링크를 대표로 쓴다.
                f"""SELECT dt.id AS topic_id, dt.topic_summary AS summary
                   FROM digest_topics dt
                   JOIN digest_issues di ON di.id = dt.issue_id
                   JOIN digests d ON d.id = di.digest_id
                   WHERE d.keyword = ? AND d.created_at >= ?{lang_clause} AND dt.topic = ?
                   ORDER BY d.created_at DESC LIMIT 1""",
                (*base_params, row["topic"]),
            ).fetchone()
            links = []
            summary = ""
            if latest is not None:
                summary = latest["summary"]
                links = [
                    r["link"] for r in conn.execute(
                        "SELECT link FROM digest_links WHERE topic_id = ?", (latest["topic_id"],),
                    ).fetchall()
                ]
            result.append({"topic": row["topic"], "article_count": row["article_count"],
                           "summary": summary, "links": links})
    return result


def fetch_digests_for_keywords(keywords, summary_length, language, now=None, hours=None, path=None):
    """구독자 키워드 + (summary_length, language) 조합의 '최신' 다이제스트를 {keyword: [issue,...]} 로 반환.

    키워드마다 그 조합으로 만들어진 가장 최근 다이제스트 1개만 쓴다(그 이전 스냅샷은 버림).
    신선도 판정은 다이제스트가 담은 '가장 최근 기사 발행일'(latest_article_at)이 hours 기간 안인지로 한다
    — created_at(스냅샷 생성 시각)은 30분마다 재요약돼 늘 최신이라, 새 기사가 없어도 매번 통과해
    같은 옛 기사를 반복 발송하게 된다. 기간 안에 실제 새 기사가 없으면(=그 기간의 갱신 없음) 제외한다.
    (예전 스냅샷은 latest_article_at 이 NULL 이라 created_at 으로 폴백 — 하위호환.)
    hours 미지정 시 config.SUMMARY_RECENCY_HOURS.
    """
    if not keywords:
        return {}
    hours = config.SUMMARY_RECENCY_HOURS if hours is None else hours
    cutoff = _now(now) - timedelta(hours=hours)

    result = {}
    with closing(_connect(path)) as conn:
        for keyword in keywords:
            digest = conn.execute(
                """SELECT id, created_at, latest_article_at FROM digests
                   WHERE keyword = ? AND summary_length = ? AND language = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (keyword, summary_length, language),
            ).fetchone()
            if digest is None:
                continue
            freshness = digest["latest_article_at"] or digest["created_at"]
            if not _is_recent(freshness, cutoff):
                continue
            issues = _load_issues(conn, digest["id"])
            if issues:
                result[keyword] = issues
    return result


def _load_issues(conn, digest_id):
    """digest_id 의 이슈→주제→링크 계층을 조립해 반환 (fetch_digests_for_keywords 내부용)."""
    issues = []
    for irow in conn.execute(
        "SELECT id, headline FROM digest_issues WHERE digest_id = ? ORDER BY order_index",
        (digest_id,),
    ).fetchall():
        topics = []
        for trow in conn.execute(
            "SELECT id, topic, topic_summary FROM digest_topics WHERE issue_id = ? ORDER BY order_index",
            (irow["id"],),
        ).fetchall():
            links = [
                lrow["link"] for lrow in conn.execute(
                    "SELECT link FROM digest_links WHERE topic_id = ?", (trow["id"],),
                ).fetchall()
            ]
            topics.append({"topic": trow["topic"], "topic_summary": trow["topic_summary"], "links": links})
        issues.append({"headline": irow["headline"], "topics": topics})
    return issues


def _is_recent(iso, cutoff):
    """published_at/created_at(ISO 문자열)이 cutoff 이후면 True. 파싱 실패/없음은 제외(False)."""
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso) >= cutoff
    except (ValueError, TypeError):
        # naive(오프셋 없는) ISO 는 fromisoformat 은 성공하나 aware cutoff 와 비교 시 TypeError.
        # 계약(파싱/비교 실패는 False, 예외 안 던짐)을 지켜 발송 틱이 죽지 않게 한다.
        return False
