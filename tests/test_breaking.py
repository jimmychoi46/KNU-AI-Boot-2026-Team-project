"""breaking 속보 감지 단위 테스트 (네트워크 없이 동작).

실행:  python -m pytest   또는   python -m unittest
"""
import unittest
from datetime import datetime, timedelta, timezone

from src import breaking, config

KST = timezone(timedelta(hours=9))


def _item(title, minutes_ago, now, link=None):
    published = (now - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "title": title,
        "description": "",
        "link": link or f"http://news/{title}/{minutes_ago}",
        "published_at": published,
    }


class TestSurgeFactor(unittest.TestCase):
    def setUp(self):
        breaking.reset_state()

    def test_cold_start_returns_zero(self):
        # 기준선이 없으면 급증으로 오판하지 않음
        self.assertEqual(breaking.surge_factor("주식", 100), 0.0)

    def test_detects_spike_over_baseline(self):
        for _ in range(config.SURGE_BASELINE_WINDOWS):
            breaking.surge_factor("주식", 2)      # 평소 2건
        factor = breaking.surge_factor("주식", 20)  # 갑자기 20건
        self.assertEqual(factor, 10.0)


class TestDetect(unittest.TestCase):
    def setUp(self):
        breaking.reset_state()
        self.now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=KST)

    def _prime_baseline(self, keyword, level=2):
        for _ in range(config.SURGE_BASELINE_WINDOWS):
            breaking.surge_factor(keyword, level)

    def test_a_and_b_fires(self):
        self._prime_baseline("주식")
        items = [_item("삼성전자 폭락 속보", 1, self.now)]
        items += [_item(f"관련기사{i}", 1, self.now) for i in range(20)]  # 급증(B)
        events = breaking.detect({"주식": items}, self.now)
        self.assertEqual(len(events), 1)
        self.assertIn("폭락", events[0]["headline"])

    def test_a_only_does_not_fire(self):
        # 긴급 키워드는 있지만 물량 급증(B)이 없으면 발송 안 함
        self._prime_baseline("주식", level=20)  # 평소에도 많음 → 급증 아님
        items = [_item("코스피 폭락", 1, self.now)] + [_item(f"x{i}", 1, self.now) for i in range(19)]
        events = breaking.detect({"주식": items}, self.now)
        self.assertEqual(events, [])

    def test_strong_b_alone_fires_without_keyword(self):
        self._prime_baseline("주식", level=2)
        # 긴급 키워드 없이도 강한 급증이면 발송
        items = [_item(f"평범한 기사{i}", 1, self.now) for i in range(20)]
        events = breaking.detect({"주식": items}, self.now)
        self.assertEqual(len(events), 1)

    def test_stale_articles_not_counted(self):
        self._prime_baseline("주식")
        # 최근 구간 밖(오래된) 기사는 급증 카운트에서 제외
        items = [_item("폭락", 1, self.now)] + [
            _item(f"old{i}", config.MONITOR_INTERVAL_MINUTES + 30, self.now) for i in range(20)
        ]
        events = breaking.detect({"주식": items}, self.now)
        self.assertEqual(events, [])


class TestDedupAndCooldown(unittest.TestCase):
    def setUp(self):
        breaking.reset_state()
        self.now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=KST)

    def test_event_sent_once(self):
        event = {"signature": "주식|http://a|폭락"}
        self.assertTrue(breaking.is_new_event(event))
        self.assertFalse(breaking.is_new_event(event))  # 두 번째는 중복

    def test_cooldown_blocks_repeat(self):
        breaking.mark_sent("a@x.com", self.now)
        self.assertTrue(breaking.in_cooldown("a@x.com", self.now + timedelta(minutes=10)))
        after = self.now + timedelta(minutes=config.EMERGENCY_COOLDOWN_MINUTES + 1)
        self.assertFalse(breaking.in_cooldown("a@x.com", after))


if __name__ == "__main__":
    unittest.main()
