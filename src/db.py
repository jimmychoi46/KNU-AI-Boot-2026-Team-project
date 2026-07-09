import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

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
    UNIQUE(keyword, link)
);

CREATE TABLE IF NOT EXISTS digests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword        TEXT    NOT NULL,
    summary_length TEXT    NOT NULL,   -- config.SUMMARY_LENGTH 중 하나
    language       TEXT    NOT NULL,   -- config.LANGUAGE 중 하나
    created_at     TEXT    NOT NULL
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

"""


_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(keyword);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_digests_lookup ON digests(keyword, summary_length, language, created_at);
CREATE INDEX IF NOT EXISTS idx_subscribers_confirm_token ON subscribers(confirm_token);
"""


def _now(now=None):
    """기준 시각. 미지정 시 현재 KST."""
    return now or datetime.now(pytz.timezone(config.TIMEZONE))


def _connect(path=None):
    """DB 커넥션 반환. 상위 디렉터리가 없으면 만든다(첫 실행 대비)."""
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# 테이블 마이그레이션 이력 — 기존 DB 파일에 새 컬럼을 ALTER TABLE 로 보정하기 위한 목록.
_COLUMN_MIGRATIONS = {
    "subscribers": {
        "confirmed": "INTEGER NOT NULL DEFAULT 0",
        "confirm_token": "TEXT",
        "access_code": "TEXT",
        "access_code_expires_at": "TEXT",
    },
}


def _ensure_columns(conn, table, columns):
    """table 에 없는 컬럼을 ALTER TABLE ADD COLUMN 으로 추가한다(가벼운 자체 마이그레이션)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


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
                  updated_at=excluded.updated_at""",
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
    except sqlite3.OperationalError:
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
    except sqlite3.OperationalError:
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
    except sqlite3.OperationalError:
        return 0


def fetch_confirm_token(email, path=None):
    """이메일의 현재 확인 토큰을 반환. 이미 확인됐거나(토큰 폐기됨) 없는 이메일이면 None.
    """
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT confirm_token FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
    return row["confirm_token"] if row else None


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
        conn.execute(
            "UPDATE subscribers SET confirmed = 1, confirm_token = NULL WHERE confirm_token = ?",
            (token,),
        )
    return row["email"]


def generate_access_code(email, ttl_minutes=None, now=None, path=None):
    """셀프서비스 본인 확인 코드 발급(confirm_token과 달리 만료 전까진 재사용 가능). 없는 이메일이면 None."""
    ttl_minutes = config.ACCESS_CODE_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    code = secrets.token_hex(3).upper()
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
    if not hmac.compare_digest(row["access_code"], code):
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
                cur = conn.execute(
                    """INSERT OR IGNORE INTO articles
                       (keyword, title, link, description, published_at, collected_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (keyword, title, link, item.get("description", ""),
                     item.get("published_at"), ts),
                )
                inserted += cur.rowcount
    return inserted


def prune_old_articles(now=None, hours=None, path=None):
    """RECENCY_HOURS(또는 지정한 hours)보다 오래된 기사를 삭제한다.

    fetch_articles_for_keyword 가 이 창보다 오래된 기사는 애초에 요약 대상으로 삼지
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


def save_digest(keyword, summary_length, language, rows, now=None, path=None):
    """summarizer 의 평평한 요약 행을 이슈→주제→기사 계층으로 묶어 새 다이제스트로 저장.

    rows 가 비어있으면 저장하지 않고 None 을 반환한다.
    발송(fetch_digests_for_keywords)은 조합당 '최신' 다이제스트 1개만 쓰므로, 새로
    저장하는 즉시 같은 (keyword, summary_length, language) 조합의 예전 다이제스트는
    바로 지운다(하위 issue/topic/link 는 FK ON DELETE CASCADE 로 함께 삭제) —
    summarize_job 이 30분마다 도는 걸 그대로 두면 스냅샷이 무한히 쌓이므로,
    "TODO: 나중에 정리" 로 미루지 않고 조합당 항상 최신 1건만 남도록 즉시 정리한다.
    returns: 생성된 digest_id (rows 가 비었으면 None).
    """
    issues = group_digest_rows(rows)
    if not issues:
        return None
    ts = _now(now).isoformat()
    with closing(_connect(path)) as conn, conn:
        digest_id = conn.execute(
            "INSERT INTO digests (keyword, summary_length, language, created_at) VALUES (?, ?, ?, ?)",
            (keyword, summary_length, language, ts),
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
               WHERE keyword = ? AND summary_length = ? AND language = ? AND id != ?""",
            (keyword, summary_length, language, digest_id),
        )
    return digest_id


def fetch_digests_for_keywords(keywords, summary_length, language, now=None, hours=None, path=None):
    """구독자 키워드 + (summary_length, language) 조합의 '최신' 다이제스트를 {keyword: [issue,...]} 로 반환.

    키워드마다 그 조합으로 만들어진 가장 최근 다이제스트 1개만 쓴다(그 이전 스냅샷은 버림).
    그 최신 것조차 hours 시간보다 오래됐으면(최근 갱신 없었음) 그 키워드는 결과에서 제외한다.
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
                """SELECT id, created_at FROM digests
                   WHERE keyword = ? AND summary_length = ? AND language = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (keyword, summary_length, language),
            ).fetchone()
            if digest is None or not _is_recent(digest["created_at"], cutoff):
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
    except ValueError:
        return False
