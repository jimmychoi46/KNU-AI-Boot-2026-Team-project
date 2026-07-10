"""db 모듈 단위 테스트 (임시 파일 DB로 동작, 외부 의존성 없음).

실행:  python -m pytest   또는   python -m unittest
"""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone

from src import db

KST = timezone(timedelta(hours=9))


class TestDb(unittest.TestCase):
    def setUp(self):
        # 테스트마다 독립된 임시 파일 DB를 만들고 스키마 생성
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)  # init_db가 새로 만들도록 비워둠
        db.init_db(self.path)
        self.now = datetime(2026, 7, 7, 18, 0, 0, tzinfo=KST)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _article(self, keyword="주식", link="http://a/1", title="제목1", hours_ago=1):
        published = (self.now - timedelta(hours=hours_ago)).isoformat()
        return {"title": title, "link": link, "description": "본문", "published_at": published}

    # ── save_articles ─────────────────────────────────────────
    def test_save_articles_inserts_and_counts(self):
        n = db.save_articles({"주식": [self._article()]}, now=self.now, path=self.path)
        self.assertEqual(n, 1)

    def test_save_articles_skips_duplicate_link(self):
        payload = {"주식": [self._article(link="http://a/1")]}
        db.save_articles(payload, now=self.now, path=self.path)
        # 같은 (keyword, link) 재저장 → 0건
        n = db.save_articles(payload, now=self.now, path=self.path)
        self.assertEqual(n, 0)

    def test_save_articles_skips_incomplete(self):
        rows = [
            {"title": "제목", "link": "", "description": "", "published_at": None},  # 링크 없음
            {"title": "", "link": "http://a/2", "description": "", "published_at": None},  # 제목 없음
        ]
        n = db.save_articles({"주식": rows}, now=self.now, path=self.path)
        self.assertEqual(n, 0)

    # ── fetch_articles_for_keyword ──────────────────────────────
    def test_fetch_articles_for_keyword_filters_by_keyword_and_recency(self):
        db.save_articles({
            "주식": [self._article(keyword="주식", link="http://a/1", hours_ago=1),
                     self._article(keyword="주식", link="http://a/stale", hours_ago=200)],
            "금리": [self._article(keyword="금리", link="http://b/1", hours_ago=1)],
        }, now=self.now, path=self.path)
        articles = db.fetch_articles_for_keyword("주식", now=self.now, hours=24, path=self.path)
        links = [a["link"] for a in articles]
        self.assertEqual(links, ["http://a/1"])  # 다른 키워드·오래된 기사는 제외

    def test_fetch_articles_for_keyword_empty_when_none(self):
        self.assertEqual(db.fetch_articles_for_keyword("주식", now=self.now, path=self.path), [])

    # ── prune_old_articles ──────────────────────────────────────
    def test_prune_old_articles_deletes_only_stale_rows(self):
        db.save_articles({
            "주식": [self._article(link="http://a/fresh", hours_ago=1),
                     self._article(link="http://a/stale", hours_ago=200)],
        }, now=self.now, path=self.path)
        deleted = db.prune_old_articles(now=self.now, hours=24, path=self.path)
        self.assertEqual(deleted, 1)
        remaining = db.fetch_articles_for_keyword("주식", now=self.now, hours=999, path=self.path)
        self.assertEqual([a["link"] for a in remaining], ["http://a/fresh"])

    def test_prune_old_articles_default_hours_is_recency_hours(self):
        # hours 미지정 시 config.RECENCY_HOURS(7일) 기준 — 그보다 살짝 안 지난 기사는 남는다
        db.save_articles({
            "주식": [self._article(link="http://a/just_inside", hours_ago=167),
                     self._article(link="http://a/just_outside", hours_ago=169)],
        }, now=self.now, path=self.path)
        db.prune_old_articles(now=self.now, path=self.path)
        remaining = db.fetch_articles_for_keyword("주식", now=self.now, hours=999, path=self.path)
        self.assertEqual([a["link"] for a in remaining], ["http://a/just_inside"])

    def test_prune_old_articles_returns_zero_when_nothing_stale(self):
        db.save_articles({"주식": [self._article(hours_ago=1)]}, now=self.now, path=self.path)
        self.assertEqual(db.prune_old_articles(now=self.now, hours=24, path=self.path), 0)

    # ── group_digest_rows (순수 함수, DB 없음) ───────────────────
    def test_group_digest_rows_builds_issue_topic_link_hierarchy(self):
        rows = [
            {"headline": "이슈A", "topic": "주제1", "topic_summary": "요약1", "link": "http://x/1"},
            {"headline": "이슈A", "topic": "주제1", "topic_summary": "요약1", "link": "http://x/2"},  # 같은 주제, 링크 추가
            {"headline": "이슈A", "topic": "주제2", "topic_summary": "요약2", "link": "http://x/3"},
            {"headline": "이슈B", "topic": "주제3", "topic_summary": "요약3", "link": "http://x/4"},
        ]
        issues = db.group_digest_rows(rows)
        self.assertEqual([i["headline"] for i in issues], ["이슈A", "이슈B"])  # 등장 순서 유지
        issue_a = issues[0]
        self.assertEqual(len(issue_a["topics"]), 2)
        self.assertEqual(issue_a["topics"][0]["links"], ["http://x/1", "http://x/2"])  # 링크 누적
        self.assertEqual(issues[1]["topics"][0]["links"], ["http://x/4"])

    def test_group_digest_rows_dedupes_links_within_topic(self):
        rows = [
            {"headline": "이슈A", "topic": "주제1", "topic_summary": "요약1", "link": "http://x/1"},
            {"headline": "이슈A", "topic": "주제1", "topic_summary": "요약1", "link": "http://x/1"},  # 중복 링크
        ]
        issues = db.group_digest_rows(rows)
        self.assertEqual(issues[0]["topics"][0]["links"], ["http://x/1"])

    def test_group_digest_rows_empty_input(self):
        self.assertEqual(db.group_digest_rows([]), [])

    # ── save_digest / fetch_digests_for_keywords ────────────────
    def _rows(self, headline="이슈A", topic="주제1", link="http://x/1"):
        return [{"headline": headline, "topic": topic, "topic_summary": "요약", "link": link}]

    def test_save_digest_returns_none_for_empty_rows(self):
        self.assertIsNone(db.save_digest("주식", "짧게", "한국어", [], now=self.now, path=self.path))

    def test_save_digest_then_fetch_roundtrip(self):
        digest_id = db.save_digest("주식", "짧게", "한국어", self._rows(), now=self.now, path=self.path)
        self.assertIsNotNone(digest_id)
        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=self.now, path=self.path)
        self.assertEqual(out["주식"][0]["headline"], "이슈A")
        self.assertEqual(out["주식"][0]["topics"][0]["topic"], "주제1")
        self.assertEqual(out["주식"][0]["topics"][0]["links"], ["http://x/1"])

    def test_fetch_digests_filters_by_keyword(self):
        db.save_digest("주식", "짧게", "한국어", self._rows(), now=self.now, path=self.path)
        db.save_digest("금리", "짧게", "한국어", self._rows(headline="이슈B"), now=self.now, path=self.path)
        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=self.now, path=self.path)
        self.assertEqual(list(out.keys()), ["주식"])

    def test_fetch_digests_filters_by_combo(self):
        # 짧게/한국어로 저장된 다이제스트는 길게/영어 조회에 안 나온다
        db.save_digest("주식", "짧게", "한국어", self._rows(), now=self.now, path=self.path)
        out = db.fetch_digests_for_keywords(["주식"], "길게", "영어", now=self.now, path=self.path)
        self.assertEqual(out, {})

    def test_fetch_digests_uses_only_the_latest_snapshot(self):
        # 같은 키워드/조합으로 두 번 생성하면, 최신 것만 반환된다(과거 스냅샷 버림)
        db.save_digest("주식", "짧게", "한국어", self._rows(headline="옛날 이슈"),
                       now=self.now - timedelta(hours=2), path=self.path)
        db.save_digest("주식", "짧게", "한국어", self._rows(headline="최신 이슈"),
                       now=self.now, path=self.path)
        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=self.now, path=self.path)
        self.assertEqual(out["주식"][0]["headline"], "최신 이슈")

    def test_save_digest_keeps_old_snapshot_within_retention_window(self):
        # 같은 조합이라도 보존 기간(DIGEST_RECENCY_HOURS) 안이면 예전 스냅샷을 남긴다
        # — 주간 트렌드 집계(get_top_topics)가 지난 며칠치 이력을 훑어야 하므로.
        first_id = db.save_digest("주식", "짧게", "한국어", self._rows(headline="옛날 이슈"),
                                   now=self.now - timedelta(hours=2), path=self.path)
        second_id = db.save_digest("주식", "짧게", "한국어", self._rows(headline="최신 이슈"),
                                    now=self.now, path=self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT id FROM digests WHERE keyword='주식' AND summary_length='짧게' AND language='한국어'"
            ).fetchall()
        self.assertEqual({r[0] for r in rows}, {first_id, second_id})  # 둘 다 남음

    def test_save_digest_prunes_snapshot_outside_retention_window(self):
        # 보존 기간(기본 8일)보다 오래된 예전 스냅샷은 새로 저장할 때 정리된다(무한 누적 방지).
        first_id = db.save_digest("주식", "짧게", "한국어", self._rows(headline="옛날 이슈"),
                                   now=self.now - timedelta(hours=24 * 9), path=self.path)
        second_id = db.save_digest("주식", "짧게", "한국어", self._rows(headline="최신 이슈"),
                                    now=self.now, path=self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT id FROM digests WHERE keyword='주식' AND summary_length='짧게' AND language='한국어'"
            ).fetchall()
            issue_count = conn.execute("SELECT COUNT(*) FROM digest_issues").fetchone()[0]
        self.assertEqual([r[0] for r in rows], [second_id])  # 옛 id는 사라지고 최신 id만 남음
        self.assertEqual(issue_count, 1)  # 하위 이슈도 CASCADE로 함께 삭제됨(옛 이슈 안 남음)

    def test_save_digest_does_not_prune_other_combos(self):
        # 다른 (키워드, 조합)의 다이제스트는 건드리지 않는다(보존 기간 밖이어도)
        db.save_digest("주식", "짧게", "한국어", self._rows(),
                       now=self.now - timedelta(hours=24 * 9), path=self.path)
        db.save_digest("금리", "짧게", "한국어", self._rows(),
                       now=self.now - timedelta(hours=24 * 9), path=self.path)
        db.save_digest("주식", "길게", "영어", self._rows(),
                       now=self.now - timedelta(hours=24 * 9), path=self.path)
        db.save_digest("주식", "짧게", "한국어", self._rows(headline="갱신"), now=self.now, path=self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]
        self.assertEqual(total, 3)  # 주식/짧게/한국어의 옛 스냅샷만 정리되고, 나머지 둘은 그대로

    # ── prune_old_digests ────────────────────────────────────────
    def test_prune_old_digests_deletes_only_stale_rows(self):
        db.save_digest("주식", "짧게", "한국어", self._rows(headline="옛날"),
                       now=self.now - timedelta(hours=24 * 9), path=self.path)
        db.save_digest("금리", "짧게", "한국어", self._rows(headline="최신"), now=self.now, path=self.path)
        deleted = db.prune_old_digests(now=self.now, path=self.path)
        self.assertEqual(deleted, 1)
        with closing(sqlite3.connect(self.path)) as conn:
            remaining = conn.execute("SELECT keyword FROM digests").fetchall()
        self.assertEqual([r[0] for r in remaining], ["금리"])

    def test_prune_old_digests_default_hours_is_digest_recency_hours(self):
        # hours 미지정 시 config.DIGEST_RECENCY_HOURS(8일) 기준 — 그보다 살짝 안 지난 건 남는다
        db.save_digest("주식", "짧게", "한국어", self._rows(),
                       now=self.now - timedelta(hours=24 * 8 - 1), path=self.path)
        self.assertEqual(db.prune_old_digests(now=self.now, path=self.path), 0)

    # ── get_top_topics (주간 트렌드 키워드 집계) ──────────────────
    def _topic_rows(self, headline, topic, link):
        return [{"headline": headline, "topic": topic, "topic_summary": "요약", "link": link}]

    def test_get_top_topics_orders_by_frequency(self):
        db.save_digest("주식", "짧게", "한국어", self._topic_rows("이슈1", "금리 인상", "http://a/1"),
                       now=self.now - timedelta(days=1), path=self.path)
        db.save_digest("주식", "짧게", "한국어",
                       self._topic_rows("이슈2", "금리 인상", "http://a/2")
                       + self._topic_rows("이슈2", "환율 변동", "http://a/3"),
                       now=self.now, path=self.path)
        top = db.get_top_topics("주식", self.now - timedelta(days=7), path=self.path)
        self.assertEqual(top, [("금리 인상", 2), ("환율 변동", 1)])

    def test_get_top_topics_filters_by_since(self):
        db.save_digest("주식", "짧게", "한국어", self._topic_rows("옛날 이슈", "오래된 주제", "http://a/1"),
                       now=self.now - timedelta(days=10), path=self.path)
        top = db.get_top_topics("주식", self.now - timedelta(days=7), path=self.path)
        self.assertEqual(top, [])

    def test_get_top_topics_respects_limit(self):
        rows = []
        for i in range(5):
            rows += self._topic_rows("이슈", f"주제{i}", f"http://a/{i}")
        db.save_digest("주식", "짧게", "한국어", rows, now=self.now, path=self.path)
        top = db.get_top_topics("주식", self.now - timedelta(days=7), limit=2, path=self.path)
        self.assertEqual(len(top), 2)

    def test_get_top_topics_empty_when_no_history(self):
        self.assertEqual(db.get_top_topics("주식", self.now - timedelta(days=7), path=self.path), [])

    def test_fetch_digests_excludes_stale_latest(self):
        # 가장 최신 것조차 창(hours) 밖이면 제외된다
        db.save_digest("주식", "짧게", "한국어", self._rows(),
                       now=self.now - timedelta(hours=48), path=self.path)
        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어",
                                            now=self.now, hours=24, path=self.path)
        self.assertEqual(out, {})

    def test_fetch_digests_empty_keywords(self):
        self.assertEqual(db.fetch_digests_for_keywords([], "짧게", "한국어", now=self.now, path=self.path), {})

    def test_get_top_topics_counts_kst_days_not_utc(self):
        # 같은 KST 하루(07-09)의 02:00과 20:00 스냅샷은 등장일 1일로 세야 한다.
        # UTC 기준 DATE()면 02:00(+09:00)은 07-08로 넘어가 2일로 잘못 세진다(회귀 방지).
        kst_02 = datetime(2026, 7, 9, 2, 0, tzinfo=KST)
        kst_20 = datetime(2026, 7, 9, 20, 0, tzinfo=KST)
        db.save_digest("주식", "짧게", "한국어", self._topic_rows("이슈", "금리 인상", "http://a/1"),
                       now=kst_02, path=self.path)
        db.save_digest("주식", "짧게", "한국어", self._topic_rows("이슈", "금리 인상", "http://a/2"),
                       now=kst_20, path=self.path)
        top = db.get_top_topics("주식", datetime(2026, 7, 1, tzinfo=KST), path=self.path)
        self.assertEqual(top, [("금리 인상", 1)])  # 하루로 집계

    # ── 이메일 대소문자 정규화 마이그레이션 ─────────────────────────
    def test_email_casing_migration_merges_duplicates_keeping_confirmed(self):
        # subscriptions 계층(normalize_email)을 우회해 대소문자 섞인 중복 행을 직접 만든다
        db.upsert_subscriber({"email": "Alice@X.com", "keywords": []}, now=self.now, path=self.path)
        db.upsert_subscriber({"email": "alice@x.com", "keywords": []}, now=self.now, path=self.path)
        token = db.fetch_confirm_token("alice@x.com", path=self.path)
        db.confirm_subscriber(token, path=self.path)  # 소문자 쪽만 확인 상태로 만듦

        db.init_db(self.path)  # 마이그레이션 재실행 → 병합

        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute("SELECT email, confirmed FROM subscribers").fetchall()
        self.assertEqual([r[0] for r in rows], ["alice@x.com"])  # 소문자 하나만 생존
        self.assertEqual(rows[0][1], 1)  # 확인된 쪽이 살아남음

    # ── claim_dispatch (원자적 중복 발송 방지) ─────────────────────
    def test_claim_dispatch_succeeds_on_first_call(self):
        db.upsert_subscriber({"email": "a@x.com", "keywords": []}, now=self.now, path=self.path)
        self.assertTrue(db.claim_dispatch("a@x.com", self.now, path=self.path))

    def test_claim_dispatch_second_call_within_window_fails(self):
        # 같은 슬롯을 두 프로세스가 겹쳐 선점 시도 → 두 번째는 실패(중복 발송 방지)
        db.upsert_subscriber({"email": "a@x.com", "keywords": []}, now=self.now, path=self.path)
        self.assertTrue(db.claim_dispatch("a@x.com", self.now, path=self.path))
        soon_after = self.now + timedelta(seconds=30)
        self.assertFalse(db.claim_dispatch("a@x.com", soon_after, path=self.path))

    def test_claim_dispatch_succeeds_again_after_window_passes(self):
        db.upsert_subscriber({"email": "a@x.com", "keywords": []}, now=self.now, path=self.path)
        db.claim_dispatch("a@x.com", self.now, path=self.path)
        next_slot = self.now + timedelta(minutes=30)
        self.assertTrue(db.claim_dispatch("a@x.com", next_slot, path=self.path))

    def test_claim_dispatch_fails_when_subscriber_missing(self):
        # DB에 없는 이메일은 선점 불가(조건부 UPDATE rowcount=0) — load_subscriptions가
        # DB에서 읽어오므로 실제 발송 경로에선 항상 행이 존재한다.
        self.assertFalse(db.claim_dispatch("nobody@x.com", self.now, path=self.path))

    def test_email_casing_migration_is_idempotent(self):
        db.upsert_subscriber({"email": "bob@x.com", "keywords": []}, now=self.now, path=self.path)
        db.init_db(self.path)
        db.init_db(self.path)  # 두 번째 실행은 아무 것도 바꾸지 않음
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute("SELECT email FROM subscribers").fetchall()
        self.assertEqual([r[0] for r in rows], ["bob@x.com"])


if __name__ == "__main__":
    unittest.main()
