"""renderers.report 보안(이스케이프/링크 검증) 단위 테스트.

실행:  python -m pytest   또는   python -m unittest
"""
import unittest

from src.renderers import report


def _one(headline, link):
    return {"주식": [{"headline": headline, "topics": [
        {"topic": "주제", "topic_summary": "", "links": [link]}
    ]}]}


class TestRenderEscaping(unittest.TestCase):
    def test_escapes_headline_tags(self):
        html = report.render(_one("<script>alert(1)</script>", "https://ok"))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_sanitizes_javascript_scheme(self):
        html = report.render(_one("x", "javascript:alert(1)"))
        self.assertNotIn("javascript:", html)
        self.assertIn('href="#"', html)

    def test_prevents_href_attribute_breakout(self):
        html = report.render(_one("x", 'https://a/"><img src=x onerror=alert(1)>'))
        self.assertNotIn("<img", html)   # 태그로 살아있지 않음
        self.assertNotIn('"><', html)    # 속성 탈출 없음


if __name__ == "__main__":
    unittest.main()
