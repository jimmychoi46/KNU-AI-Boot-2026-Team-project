"""뉴스/요약 저장소 (SQLite).

담당: 백엔드 — 수집·요약·발송 배치 잡이 공유하는 데이터 저장소.

파이프라인이 세 배치 잡으로 분리되면서, 각 잡은 메모리로 값을 주고받는 대신
이 DB 를 통해 단계 결과를 넘긴다.
  ① 수집 잡: 정제한 뉴스를 articles 에 저장                     (save_articles)
  ② 요약 잡: 구독자가 실제 구독한 (키워드, summary_length, language) 조합마다
             최근 기사를 모아 LLM 에게 이슈→주제 단위로 요약시키고,
             그 결과를 새 다이제스트 스냅샷으로 저장
             (fetch_articles_for_keyword → summarizer.summarize → save_digest)
  ③ 발송 잡: 구독자 키워드 + 그 구독자의 (summary_length, language) 에 맞는
             '최신' 다이제스트를 조회 → 렌더링 → 발송
             (fetch_digests_for_keywords)

LLM 은 여러 기사를 묶어 "핵심 이슈(headline) → 하위 주제(topic) 1~3개 → 주제별 요약
+ 관련 기사(복수)" 구조로 편집한다. 한 키워드에 이슈가 여러 개 나올 수도, 한 주제에
관련 기사가 여러 건 달릴 수도 있다. summarizer 는 이를 평평한(flat) 행 리스트
[{"headline","topic","topic_summary","link"}, ...] 로 반환하고, group_digest_rows() 가
이를 이슈→주제→링크 계층으로 묶는다(같은 기사 1건=요약 1건이던 구모델과 다름 — 이제
"아직 요약 안 된 기사"라는 개념이 없고, 매 요약 잡 실행마다 보유 기사 전체를 다시 넘겨
새 스냅샷을 만든다. 오래된 스냅샷은 발송 시 창(window)으로 걸러진다).

[스키마]
  subscribers   : 구독자 1명 (email=PK, name, keywords, send_hour/minute, frequency,
                  summary_length, language, confirmed, confirm_token)
                  프론트 대시보드가 여기에 쓰고, 백엔드가 읽는다(과거엔 JSON 파일이었음).
                  confirmed 는 이메일 소유 확인(더블 옵트인) 여부 — confirmed=0 인
                  구독자는 정기/속보 발송 대상에서 제외된다(subscriptions.is_due 등).
                  confirm_token 은 확인 메일 링크에 실리는 1회용 토큰(확인 후 NULL).
  articles      : 정제된 뉴스 1건 (keyword, title, link, description, published_at, collected_at)
                  같은 키워드에서 같은 링크는 한 번만 저장 (UNIQUE(keyword, link))
  digests       : (키워드, summary_length, language) 조합의 요약 생성 1회(스냅샷)
  digest_issues : 다이제스트 안의 핵심 이슈(headline) — 다이제스트당 여러 개 가능
  digest_topics : 이슈 아래 하위 주제(topic) + 주제별 요약(topic_summary) — 이슈당 1~3개
  digest_links  : 주제에 딸린 관련 기사 링크 — 주제당 여러 개 가능

데모(로컬 파일 하나)로도, 배포(파일 경로만 교체)로도 그대로 쓸 수 있게 표준
라이브러리 sqlite3 만 사용한다. 별도 DB 서버가 필요하면 이 모듈만 교체하면 된다.
"""
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

CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(keyword);
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


def init_db(path=None):
    """테이블/인덱스를 생성한다(이미 있으면 그대로 둔다)."""
    # closing(): sqlite3 커넥션은 with 문만으로는 닫히지 않아(트랜잭션만 커밋) 명시적으로 닫는다.
    #            Windows 에서 커넥션이 열린 채 남으면 파일이 잠겨 삭제/재열기가 막힌다.
    with closing(_connect(path)) as conn, conn:
        conn.executescript(_SCHEMA)


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
    """
    ts = _now(now).isoformat()
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            """INSERT INTO subscribers
                 (email, name, keywords, send_hour, send_minute,
                  frequency, summary_length, language, updated_at)
               VALUES
                 (:email, :name, :keywords, :send_hour, :send_minute,
                  :frequency, :summary_length, :language, :updated_at)
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
                          frequency, summary_length, language
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
                          frequency, summary_length, language
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
    return record


def count_subscribers(path=None):
    """구독자 수. 테이블이 없으면 0."""
    try:
        with closing(_connect(path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


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


def fetch_articles_for_keyword(keyword, now=None, hours=None, path=None):
    """keyword 에 해당하는 최근 기사를 cleaned_item 형태 리스트로 반환 (요약 잡 입력용).

    item: {"title", "link", "description", "published_at"}
    hours 미지정 시 config.RECENCY_HOURS(수집 보관 기간 전체, 기본 7일)를 쓴다
    — 즉 DB에 아직 남아있는 그 키워드의 기사 전부를 요약 대상으로 삼는다.
    """
    hours = config.RECENCY_HOURS if hours is None else hours
    cutoff = _now(now) - timedelta(hours=hours)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            """SELECT title, link, description, published_at
               FROM articles WHERE keyword = ? ORDER BY published_at DESC""",
            (keyword,),
        ).fetchall()
    return [dict(r) for r in rows if _is_recent(r["published_at"], cutoff)]


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
