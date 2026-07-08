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

    def test_save_digest_prunes_old_snapshot_for_same_combo(self):
        # 같은 조합으로 다시 저장하면 예전 digests 행이 실제로 삭제된다(무한 누적 방지).
        first_id = db.save_digest("주식", "짧게", "한국어", self._rows(headline="옛날 이슈"),
                                   now=self.now - timedelta(hours=2), path=self.path)
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
        # 다른 (키워드, 조합)의 다이제스트는 건드리지 않는다
        db.save_digest("주식", "짧게", "한국어", self._rows(), now=self.now, path=self.path)
        db.save_digest("금리", "짧게", "한국어", self._rows(), now=self.now, path=self.path)
        db.save_digest("주식", "길게", "영어", self._rows(), now=self.now, path=self.path)
        db.save_digest("주식", "짧게", "한국어", self._rows(headline="갱신"), now=self.now, path=self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]
        self.assertEqual(total, 3)  # 주식/짧게/한국어만 1개로 정리, 나머지 둘은 그대로

    def test_fetch_digests_excludes_stale_latest(self):
        # 가장 최신 것조차 창(hours) 밖이면 제외된다
        db.save_digest("주식", "짧게", "한국어", self._rows(),
                       now=self.now - timedelta(hours=48), path=self.path)
        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어",
                                            now=self.now, hours=24, path=self.path)
        self.assertEqual(out, {})

    def test_fetch_digests_empty_keywords(self):
        self.assertEqual(db.fetch_digests_for_keywords([], "짧게", "한국어", now=self.now, path=self.path), {})


if __name__ == "__main__":
    unittest.main()
