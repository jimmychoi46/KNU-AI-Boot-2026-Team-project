"""renderers.report 보안(이스케이프/링크 검증) 단위 테스트.

실행:  python -m pytest   또는   python -m unittest
"""
import unittest
from datetime import datetime, timezone

from src.renderers import report


def _one(headline, link):
    return {"주식": [{"headline": headline, "topics": [
        {"topic": "주제", "topic_summary": "", "links": [link]}
    ]}]}


class TestRenderTopicTitles(unittest.TestCase):
    def test_each_card_uses_its_own_topic_title_not_the_shared_headline(self):
        # 회귀 테스트: 카드 제목에 토픽별 제목 대신 이슈 헤드라인이 중복 삽입되던 버그
        digests = {"주식": [{"headline": "삼성 실적 발표", "topics": [
            {"topic": "매출 급증", "topic_summary": "요약1", "links": ["https://a"]},
            {"topic": "영업이익 감소", "topic_summary": "요약2", "links": ["https://b"]},
        ]}]}
        out = report.render(digests)
        self.assertIn("매출 급증", out)
        self.assertIn("영업이익 감소", out)
        # 헤드라인은 상단 요약(프리헤더 문구 + 오늘의 핵심 요약, 총 2곳)에만 쓰이고,
        # 카드 제목 자리(토픽마다 1장)에는 반복되지 않는다 — 버그 당시엔 카드마다 한 번씩
        # 더 나타나 총 4회(2+2)였다.
        self.assertEqual(out.count("삼성 실적 발표"), 2)

    def test_falls_back_to_headline_when_topic_title_missing(self):
        digests = {"주식": [{"headline": "헤드라인만", "topics": [
            {"topic": "", "topic_summary": "요약", "links": ["https://a"]},
        ]}]}
        out = report.render(digests)
        self.assertIn("헤드라인만", out)

    def test_summary_text_containing_placeholder_is_not_corrupted(self):
        # 회귀: 요약 본문에 우연히 '{{원문_링크}}' 같은 자리표시자 문구가 있어도
        # 뒤 치환이 그걸 실제 URL로 덮어쓰지 않고 문구 그대로 남아야 한다(단일 패스 치환).
        digests = {"주식": [{"headline": "H", "topics": [
            {"topic": "T", "topic_summary": "여기 {{원문_링크}} 는 그대로 남아야 함",
             "links": ["https://real"]},
        ]}]}
        out = report.render(digests)
        self.assertIn("여기 {{원문_링크}} 는 그대로 남아야 함", out)
        self.assertIn("https://real", out)  # 실제 링크는 카드 href 로만 들어간다

    def test_now_is_injectable_for_deterministic_output(self):
        digests = {"주식": [{"headline": "H", "topics": [
            {"topic": "T", "topic_summary": "S", "links": []},
        ]}]}
        fixed = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
        out = report.render(digests, now=fixed)
        self.assertIn("2026년 01월 05일", out)


class TestRenderWeeklyTrend(unittest.TestCase):
    """주간 트렌드 '별도' 이메일 렌더러 — 토픽 + 요약 + 관련 기사."""

    def _trends(self):
        return {"주식": [
            {"topic": "실적 발표", "days": 3, "summary": "삼성 실적 호조", "links": ["https://a/1"]},
            {"topic": "금리 인하", "days": 1, "summary": "인하 기대", "links": ["https://a/2"]},
        ]}

    def test_returns_empty_when_no_trends(self):
        self.assertEqual(report.render_weekly_trend({}), "")
        self.assertEqual(report.render_weekly_trend(None), "")

    def test_standalone_email_with_header_footer(self):
        out = report.render_weekly_trend(self._trends())
        self.assertIn("THIS WEEK'S TREND", out)
        self.assertIn("트렌드 뉴스레터", out)          # _HEAD 의 뉴스레터 이름
        self.assertIn("구독 취소", out)                # _TAIL 이 함께 붙은 완성 메일

    def test_topics_in_rank_order_without_counts(self):
        # 건수는 노출하지 않는다 — LLM이 매번 topic 문구를 새로 지어내는 근사 집계라
        # 숫자를 보여주면 실제보다 정밀한 지표처럼 보일 수 있어서, 순위 순서로만 보여준다.
        out = report.render_weekly_trend(self._trends())
        self.assertLess(out.index("실적 발표"), out.index("금리 인하"))
        self.assertNotIn(">3<", out)  # days 숫자를 그대로 노출하지 않음

    def test_includes_summary_and_related_article_link(self):
        out = report.render_weekly_trend(self._trends())
        self.assertIn("삼성 실적 호조", out)                   # 요약
        self.assertIn('href="https://a/1"', out)              # 관련 기사 링크
        self.assertIn("관련 기사 보기", out)

    def test_dark_mode_classes_present(self):
        out = report.render_weekly_trend(self._trends())
        self.assertIn('class="text-title"', out)
        self.assertIn('class="text-body"', out)
        self.assertIn('class="link-gold"', out)

    def test_escapes_topic_and_summary(self):
        out = report.render_weekly_trend({"주식": [
            {"topic": "<script>alert(1)</script>", "days": 1, "summary": "<b>x</b>", "links": []}]})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<b>x</b>", out)

    def test_daily_render_has_no_trend_section(self):
        # 완전 분리: 일간 뉴스레터에는 트렌드가 들어가지 않는다
        daily = report.render({"주식": [{"headline": "H", "topics": [
            {"topic": "T", "topic_summary": "S", "links": []}]}]})
        self.assertNotIn("THIS WEEK'S TREND", daily)


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
