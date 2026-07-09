"""subscriptions 모듈 단위 테스트.

- TestDueSubscribers / TestFromRow : 순수 로직(모델·검증), DB 없이 동작
- TestDbStore                      : 임시 파일 DB로 저장/조회/삭제/가져오기 왕복 확인

실행:  python -m pytest   또는   python -m unittest
"""
import json
import os
import tempfile
import unittest
from datetime import datetime

from src import db
from src.subscriptions import (
    Subscription,
    _from_row,
    delete_subscription,
    due_subscribers,
    import_from_json,
    load_subscriptions,
    save_subscription,
    send_window_hours,
)

# 2026-01: 05=월 06=화 07=수 09=금 (weekday 0/1/2/4)
MON, TUE, WED, FRI = (datetime(2026, 1, d, 8, 0) for d in (5, 6, 7, 9))


class TestDueSubscribers(unittest.TestCase):
    def _sub(self, email, hour, minute, frequency="매일", confirmed=True):
        return Subscription(email, ["주식"], hour, minute, frequency=frequency, confirmed=confirmed)

    def test_matches_time(self):
        subs = [self._sub("a@x.com", 8, 0), self._sub("b@x.com", 18, 30)]
        due = due_subscribers(subs, TUE)  # 08:00 화요일
        self.assertEqual({s.email for s in due}, {"a@x.com"})

    def test_minute_must_also_match(self):
        subs = [self._sub("b@x.com", 18, 30)]
        self.assertEqual(due_subscribers(subs, TUE.replace(hour=18, minute=0)), [])

    def test_weekly_sends_only_on_monday(self):
        sub = self._sub("w@x.com", 8, 0, frequency="매주")  # 월요일만
        self.assertEqual(due_subscribers([sub], TUE), [])                       # 화 → 발송 안 함
        self.assertEqual([s.email for s in due_subscribers([sub], MON)], ["w@x.com"])  # 월 → 발송

    def test_three_times_week_is_mon_wed_fri(self):
        sub = self._sub("t@x.com", 8, 0, frequency="주 3회")  # 월·수·금
        self.assertTrue(due_subscribers([sub], MON))
        self.assertFalse(due_subscribers([sub], TUE))
        self.assertTrue(due_subscribers([sub], WED))
        self.assertTrue(due_subscribers([sub], FRI))

    def test_unconfirmed_is_never_due(self):
        # 이메일 미확인(confirmed=False) 구독자는 시:분/요일이 맞아도 절대 발송 대상이 아니다
        sub = self._sub("u@x.com", 8, 0, confirmed=False)
        self.assertEqual(due_subscribers([sub], TUE), [])


class TestSendWindow(unittest.TestCase):
    """주기별 되돌아보는 창(hours) — 직전 발송 요일까지의 간격."""

    def _sub(self, frequency):
        return Subscription("a@x.com", ["주식"], 8, 0, frequency=frequency)

    def test_daily_is_24h(self):
        self.assertEqual(send_window_hours(self._sub("매일"), TUE), 24)

    def test_weekly_is_168h(self):
        self.assertEqual(send_window_hours(self._sub("매주"), MON), 168)

    def test_three_times_week_gaps(self):
        s = self._sub("주 3회")
        self.assertEqual(send_window_hours(s, MON), 72)  # 월 ← 직전 금(3일)
        self.assertEqual(send_window_hours(s, WED), 48)  # 수 ← 직전 월(2일)
        self.assertEqual(send_window_hours(s, FRI), 48)  # 금 ← 직전 수(2일)


class TestFromRow(unittest.TestCase):
    """레코드(dict) 검증·정규화 로직 — 읽기/쓰기 양쪽에서 공유."""

    def test_missing_email_raises(self):
        with self.assertRaises(ValueError):
            _from_row({"keywords": ["주식"], "send_hour": 8, "send_minute": 0})

    def test_malformed_email_raises(self):
        for bad in ("notanemail", "a@", "@b.com", "a@b", "a b@c.com", "a@@b.com", "a@b."):
            with self.assertRaises(ValueError):
                _from_row({"email": bad, "keywords": ["주식"], "send_hour": 8, "send_minute": 0})

    def test_valid_email_accepted_and_trimmed(self):
        sub = _from_row({"email": "  user@example.com  ", "keywords": ["주식"],
                         "send_hour": 8, "send_minute": 0})
        self.assertEqual(sub.email, "user@example.com")   # 앞뒤 공백 제거

    def test_keywords_free_input_cleaned(self):
        # 자유 입력: 후보 제한 없음. 공백 정리·빈값/중복 제거(순서 유지)만.
        sub = _from_row({"email": "a@x.com",
                         "keywords": ["수학", " 주식 ", "", "수학", "금리"],
                         "send_hour": 8, "send_minute": 0})
        self.assertEqual(sub.keywords, ["수학", "주식", "금리"])

    def test_hour_range_0_to_24(self):
        # 시는 0~24 허용, 그 밖(25/음수)은 거부
        for good in (0, 24):
            sub = _from_row({"email": "a@x.com", "keywords": ["주식"],
                             "send_hour": good, "send_minute": 0})
            self.assertEqual(sub.send_hour, good)
        for bad in (25, -1):
            with self.assertRaises(ValueError):
                _from_row({"email": "a@x.com", "keywords": ["주식"],
                           "send_hour": bad, "send_minute": 0})

    def test_minute_must_be_0_or_30(self):
        # 분은 30분 단위만 허용 (0 또는 30)
        for good in (0, 30):
            sub = _from_row({"email": "a@x.com", "keywords": ["주식"],
                             "send_hour": 8, "send_minute": good})
            self.assertEqual(sub.send_minute, good)
        for bad in (15, 45, 1):
            with self.assertRaises(ValueError):
                _from_row({"email": "a@x.com", "keywords": ["주식"],
                           "send_hour": 8, "send_minute": bad})

    def test_new_fields_default_when_missing(self):
        sub = _from_row({"email": "a@x.com", "keywords": ["주식"], "send_hour": 8, "send_minute": 0})
        self.assertEqual(sub.name, "")
        self.assertEqual(sub.frequency, "매일")
        self.assertEqual(sub.summary_length, "짧게")
        self.assertEqual(sub.language, "한국어")

    def test_new_fields_use_given_value(self):
        sub = _from_row({
            "email": "a@x.com", "name": "조요한", "keywords": ["주식"],
            "send_hour": 8, "send_minute": 0,
            "frequency": "주 3회", "summary_length": "길게", "language": "영어",
        })
        self.assertEqual(sub.name, "조요한")
        self.assertEqual(sub.frequency, "주 3회")
        self.assertEqual(sub.summary_length, "길게")
        self.assertEqual(sub.language, "영어")

    def test_unknown_frequency_falls_back_to_default(self):
        sub = _from_row({"email": "a@x.com", "keywords": ["주식"], "send_hour": 8, "send_minute": 0,
                         "frequency": "존재하지않는주기"})
        self.assertEqual(sub.frequency, "매일")

    def test_missing_send_hour_or_minute_raises(self):
        # send_time 프리셋 호환 코드는 제거됐다 — send_hour/send_minute 가 반드시 있어야 한다
        with self.assertRaises(ValueError):
            _from_row({"email": "a@x.com", "keywords": ["주식"], "send_hour": 8})
        with self.assertRaises(ValueError):
            _from_row({"email": "a@x.com", "keywords": ["주식"], "send_minute": 0})

    def test_new_subscriber_defaults_unconfirmed(self):
        sub = _from_row({"email": "a@x.com", "keywords": ["주식"], "send_hour": 8, "send_minute": 0})
        self.assertFalse(sub.confirmed)


class TestDbStore(unittest.TestCase):
    """DB 저장/조회/삭제/가져오기 왕복."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _record(self, email="a@x.com", **over):
        rec = {"email": email, "name": "홍길동", "keywords": ["주식", "금리"],
               "send_hour": 8, "send_minute": 0}
        rec.update(over)
        return rec

    def test_load_empty_when_none(self):
        self.assertEqual(load_subscriptions(self.path), [])

    def test_save_and_load_roundtrip(self):
        save_subscription(self._record(), path=self.path)
        subs = load_subscriptions(self.path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].email, "a@x.com")
        self.assertEqual(subs[0].name, "홍길동")
        self.assertEqual(subs[0].keywords, ["주식", "금리"])
        self.assertFalse(subs[0].confirmed)  # 신규 가입은 이메일 확인 전까지 미확인

    def test_upsert_same_email_updates_not_duplicates(self):
        save_subscription(self._record(send_hour=8), path=self.path)
        save_subscription(self._record(send_hour=18), path=self.path)  # 같은 이메일 재저장
        subs = load_subscriptions(self.path)
        self.assertEqual(len(subs), 1)             # 중복 생성 안 됨
        self.assertEqual(subs[0].send_hour, 18)    # 값은 갱신됨

    def test_save_rejects_invalid_record(self):
        with self.assertRaises(ValueError):
            save_subscription(self._record(send_hour=99), path=self.path)
        self.assertEqual(load_subscriptions(self.path), [])  # 저장 안 됨

    def test_delete(self):
        save_subscription(self._record(email="a@x.com"), path=self.path)
        save_subscription(self._record(email="b@x.com"), path=self.path)
        removed = delete_subscription("a@x.com", path=self.path)
        self.assertEqual(removed, 1)
        self.assertEqual([s.email for s in load_subscriptions(self.path)], ["b@x.com"])

    def test_load_skips_bad_row_in_db(self):
        # 검증을 우회해 잘못된 행(시각 범위 밖)을 직접 넣어도, 읽을 때 그 행만 건너뛴다.
        db.upsert_subscriber(self._record(email="good@x.com"), path=self.path)
        db.upsert_subscriber(self._record(email="bad@x.com", send_hour=25), path=self.path)
        self.assertEqual([s.email for s in load_subscriptions(self.path)], ["good@x.com"])

    def test_import_from_json_seeds_db(self):
        rows = [
            {"email": "a@x.com", "keywords": ["주식"], "send_hour": 8, "send_minute": 0},
            {"keywords": ["금리"], "send_hour": 8, "send_minute": 0},  # email 없음 → 건너뜀
        ]
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(rows, f, ensure_ascii=False)
        f.close()
        self.addCleanup(os.remove, f.name)

        imported = import_from_json(json_path=f.name, db_path=self.path)
        self.assertEqual(imported, 1)  # 정상 1건만
        subs = load_subscriptions(self.path)
        self.assertEqual([s.email for s in subs], ["a@x.com"])
        self.assertTrue(subs[0].confirmed)  # 기존 JSON 구독자는 확인 절차 없이 이관(grandfather)


class TestEmailConfirmation(unittest.TestCase):
    """더블 옵트인 — 확인 토큰 발급/확인/재전송 흐름."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _record(self, email="a@x.com", **over):
        rec = {"email": email, "keywords": ["주식"], "send_hour": 8, "send_minute": 0}
        rec.update(over)
        return rec

    def test_new_subscriber_gets_a_token(self):
        save_subscription(self._record(), path=self.path)
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.assertIsNotNone(token)

    def test_confirm_with_valid_token_marks_confirmed(self):
        save_subscription(self._record(), path=self.path)
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        confirmed_email = db.confirm_subscriber(token, path=self.path)
        self.assertEqual(confirmed_email, "a@x.com")
        subs = load_subscriptions(self.path)
        self.assertTrue(subs[0].confirmed)

    def test_confirm_token_is_single_use(self):
        save_subscription(self._record(), path=self.path)
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        db.confirm_subscriber(token, path=self.path)
        # 같은 토큰을 다시 쓰면 더 이상 유효하지 않다(폐기됨)
        self.assertIsNone(db.confirm_subscriber(token, path=self.path))

    def test_confirm_with_invalid_token_returns_none(self):
        self.assertIsNone(db.confirm_subscriber("not-a-real-token", path=self.path))

    def test_updating_existing_subscriber_preserves_confirmation(self):
        # PUT(정보 수정)은 이미 확인된 구독자의 confirmed 상태를 되돌리지 않는다
        save_subscription(self._record(), path=self.path)
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        db.confirm_subscriber(token, path=self.path)
        save_subscription(self._record(send_hour=18), path=self.path)  # 같은 이메일 재저장
        subs = load_subscriptions(self.path)
        self.assertTrue(subs[0].confirmed)
        self.assertEqual(subs[0].send_hour, 18)

    def test_resend_reuses_existing_token_while_unconfirmed(self):
        # 미확인 상태에서 재신청(재전송)해도 토큰이 바뀌지 않는다(이미 있는 토큰 재사용)
        save_subscription(self._record(), path=self.path)
        token1 = db.fetch_confirm_token("a@x.com", path=self.path)
        save_subscription(self._record(name="다시 신청"), path=self.path)
        token2 = db.fetch_confirm_token("a@x.com", path=self.path)
        self.assertEqual(token1, token2)


if __name__ == "__main__":
    unittest.main()
