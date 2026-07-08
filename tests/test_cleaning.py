"""collectors.naver_news 정제 로직 단위 테스트 (네트워크 없이 동작).

실행:  python -m pytest   또는   python -m unittest
"""
import unittest

from src.collectors import naver_news


class TestCleaning(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        raw = "삼성전자 <b>주가</b> &quot;급등&quot; &amp; 신고가"
        self.assertEqual(naver_news.clean_text(raw), '삼성전자 주가 "급등" & 신고가')

    def test_clean_text_handles_empty(self):
        self.assertEqual(naver_news.clean_text(None), "")
        self.assertEqual(naver_news.clean_text(""), "")

    def test_entity_encoded_tags_are_neutralized(self):
        # 엔티티로 위장한 태그도 제거되어야 함 (정제 순서: 복원 → 제거)
        out = naver_news.clean_text("&lt;script&gt;alert(1)&lt;/script&gt; 급등")
        self.assertNotIn("<script>", out)
        self.assertEqual(out, "alert(1) 급등")

    def test_parses_rfc822_pubdate(self):
        iso = naver_news.parse_pubDate("Mon, 07 Jul 2026 18:00:00 +0900")
        self.assertTrue(iso.startswith("2026-07-07T18:00:00"))

    def test_invalid_pubdate_returns_none(self):
        self.assertIsNone(naver_news.parse_pubDate("not-a-date"))
        self.assertIsNone(naver_news.parse_pubDate(None))

    def test_clean_item_shape(self):
        raw = {
            "title": "<b>코인</b> 급등",
            "link": "http://news/1",
            "description": "AT&amp;T 실적 &quot;호조&quot;",
            "pubDate": "Mon, 07 Jul 2026 09:00:00 +0900",
        }
        item = naver_news.clean_item(raw)
        self.assertEqual(item["title"], "코인 급등")
        self.assertEqual(item["link"], "http://news/1")
        self.assertEqual(item["description"], 'AT&T 실적 "호조"')
        self.assertTrue(item["published_at"].startswith("2026-07-07T09:00:00"))


if __name__ == "__main__":
    unittest.main()
