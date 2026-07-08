"""collectors.naver_news 기간 필터·중복 제거 단위 테스트 (네트워크 없이 동작).

실행:  python -m pytest   또는   python -m unittest
"""
import unittest
from datetime import datetime, timedelta, timezone

from src.collectors import naver_news

KST = timezone(timedelta(hours=9))


class TestRecency(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 7, 18, 0, 0, tzinfo=KST)

    def _item(self, dt):
        return {"published_at": dt.isoformat() if dt else None}

    def test_keeps_recent(self):
        item = self._item(datetime(2026, 7, 7, 12, 0, 0, tzinfo=KST))
        self.assertTrue(naver_news.within_recency(item, self.now, 24))

    def test_drops_old(self):
        item = self._item(datetime(2026, 7, 5, 12, 0, 0, tzinfo=KST))
        self.assertFalse(naver_news.within_recency(item, self.now, 24))

    def test_drops_undated(self):
        # 날짜 불명이면 트렌드 왜곡 방지를 위해 제외
        self.assertFalse(naver_news.within_recency(self._item(None), self.now, 24))


class TestDedupe(unittest.TestCase):
    def test_dedupe_by_link(self):
        items = [
            {"link": "http://a", "title": "뉴스1"},
            {"link": "http://a", "title": "제목이 달라도 링크가 같음"},
        ]
        self.assertEqual(len(naver_news.dedupe(items)), 1)

    def test_dedupe_by_normalized_title(self):
        items = [
            {"link": "http://a", "title": "같은   제목"},
            {"link": "http://b", "title": "같은 제목"},  # 공백 정규화 후 동일
        ]
        self.assertEqual(len(naver_news.dedupe(items)), 1)

    def test_keeps_distinct(self):
        items = [
            {"link": "http://a", "title": "뉴스1"},
            {"link": "http://b", "title": "뉴스2"},
        ]
        self.assertEqual(len(naver_news.dedupe(items)), 2)


if __name__ == "__main__":
    unittest.main()
