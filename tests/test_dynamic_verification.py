"""동적 검증 시나리오 실행 하네스 (설계 매트릭스 → 실행).

기존 test_api/test_db/test_pipeline/test_render 와 겹치지 않는, '수정의 회귀 증명 + 경계/
동시성/집계/격리' 시나리오를 값 수준으로 관측한다. 각 테스트 이름 뒤 [scen-id]는 설계 매트릭스 대응.

실행:  python -m pytest tests/test_dynamic_verification.py -v
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from unittest import mock

import pytz
from fastapi.testclient import TestClient

from src import api, config, db, subscriptions
from src.processors import summarizer
from src.renderers import report
from src.notifiers import send_email as send_email_mod

KST = pytz.timezone(config.TIMEZONE)
ADMIN_PASSWORD = "test-admin-secret"


def kst(y, mo, d, h=0, mi=0):
    return KST.localize(datetime(y, mo, d, h, mi))


# ══════════════════════════════════════════════════════════════
# API (FastAPI TestClient) — 정규화·경계·특수문자·전체교체·중복·폼파싱·브루트포스
# ══════════════════════════════════════════════════════════════
class TestApiDynamic(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        for attr, val in (("DB_PATH", self.path), ("ADMIN_PASSWORD", ADMIN_PASSWORD)):
            p = mock.patch.object(config, attr, val)
            p.start()
            self.addCleanup(p.stop)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        api.limiter.reset()
        self.client = TestClient(api.app)
        sp = mock.patch.object(api.send_email, "send_email")
        self.send_email = sp.start()
        self.addCleanup(sp.stop)

    def _body(self, **over):
        d = {"email": "a@x.com", "name": "홍길동", "keywords": ["주식", "금리"],
             "send_hour": 8, "send_minute": 30}
        d.update(over)
        return d

    def _admin(self):
        return {"X-Admin-Password": ADMIN_PASSWORD}

    def _code(self, email):
        self.client.post(f"/subscribers/{email}/access-code")
        return db.peek_access_code(email, path=self.path)

    def test_email_normalized_on_store_and_path_lookup(self):
        # [api-email-normalize-05] 대문자 이메일 저장·경로조회 모두 소문자 레코드로 일치
        res = self.client.post("/subscribers", json=self._body(email="Alice@Example.com"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["email"], "alice@example.com")
        got = self.client.get("/subscribers/Alice@Example.com", headers=self._admin())
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["email"], "alice@example.com")

    def test_case_only_reapply_is_409_not_new_row(self):
        # [api-email-dedup-409-06] 확인 후 대소문자만 다른 재신청 → 같은 구독자로 409
        self.client.post("/subscribers", json=self._body(email="a@x.com"))
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.client.post("/confirm", data={"token": token})
        res = self.client.post("/subscribers", json=self._body(email="A@X.com"))
        self.assertEqual(res.status_code, 409)
        self.assertIn("a@x.com", res.json()["detail"])
        # 별도 행이 생기지 않았는지: 관리자 목록 1건
        rows = self.client.get("/subscribers", headers=self._admin()).json()
        self.assertEqual(len(rows), 1)

    def test_plus_address_roundtrip(self):
        # [api-plus-email-roundtrip-08] '+' 포함 이메일이 raw/%2B 경로 모두 같은 레코드로 왕복
        self.client.post("/subscribers", json=self._body(email="user+tag@x.com"))
        raw = self.client.get("/subscribers/user+tag@x.com", headers=self._admin())
        enc = self.client.get("/subscribers/user%2Btag@x.com", headers=self._admin())
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(enc.status_code, 200)
        self.assertEqual(raw.json()["email"], "user+tag@x.com")
        self.assertEqual(enc.json()["email"], "user+tag@x.com")

    def test_send_hour_boundaries(self):
        # [api-hour-boundary-01] 0 허용, 25/-1 은 Pydantic 422
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="h0@x.com", send_hour=0, send_minute=0)).status_code, 201)
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="h25@x.com", send_hour=25, send_minute=0)).status_code, 422)
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="hn1@x.com", send_hour=-1, send_minute=0)).status_code, 422)

    def test_send_minute_non_half_hour_is_400(self):
        # [api-minute-boundary-02] 60/-1 은 Pydantic 제약 없음 → 본문 검증에서 400
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="m60@x.com", send_minute=60)).status_code, 400)
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="mn1@x.com", send_minute=-1)).status_code, 400)

    def test_partial_send_time_is_400(self):
        # [api-time-partial-03] hour 만 있고 minute 생략 → '누락' 400 (기본값으로 안 채움)
        body = self._body(email="pt@x.com")
        del body["send_minute"]
        self.assertEqual(self.client.post("/subscribers", json=body).status_code, 400)

    def test_invalid_email_is_400(self):
        # [api-email-invalid-400-04] 형식 오류/빈 이메일 → 400
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="notanemail")).status_code, 400)
        self.assertEqual(self.client.post("/subscribers", json=self._body(email="")).status_code, 400)

    def test_keywords_all_empty_becomes_empty_list(self):
        # [api-keyword-clean-07] 공백/빈값만 오면 [] 로 저장
        res = self.client.post("/subscribers", json=self._body(email="kw@x.com", keywords=["  ", "", "   "]))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["keywords"], [])

    def test_put_full_replace_preserves_confirmed(self):
        # [api-put-full-replace-10] 최소 본문 PUT → name/keywords 비워지되 confirmed 보존
        self.client.post("/subscribers", json=self._body(email="a@x.com", name="원래이름", keywords=["주식"]))
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.client.post("/confirm", data={"token": token})  # confirmed=True
        code = self._code("a@x.com")
        res = self.client.put("/subscribers/a@x.com", json={"send_hour": 9, "send_minute": 0},
                              headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 200)
        got = self.client.get("/subscribers/a@x.com", headers=self._admin()).json()
        self.assertEqual(got["name"], "")
        self.assertEqual(got["keywords"], [])
        self.assertEqual(got["send_hour"], 9)
        self.assertTrue(got["confirmed"])  # 전체교체지만 확인 상태는 유지

    def test_confirm_requires_form_body_not_json(self):
        # [add-api-multipart-confirm-01] POST /confirm 은 폼 바디만: JSON 은 422
        self.client.post("/subscribers", json=self._body(email="a@x.com"))
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.assertEqual(self.client.post("/confirm", json={"token": token}).status_code, 422)
        self.assertEqual(self.client.post("/confirm", data={"token": token}).status_code, 200)

    def test_delete_bruteforce_hits_rate_limit_victim_survives(self):
        # [add-api-rl-delete-bruteforce-02] 오답 코드 반복 DELETE 도 429로 차단, 피해자 잔존
        self.client.post("/subscribers", json=self._body(email="victim@x.com"))
        statuses = [self.client.delete("/subscribers/victim@x.com",
                    headers={"X-Access-Code": "WRONG1"}).status_code for _ in range(11)]
        self.assertTrue(all(s == 401 for s in statuses[:10]))
        self.assertEqual(statuses[10], 429)
        # GET /subscribers/{email} 은 DELETE 와 같은 경로라 slowapi 버킷을 공유(이미 소진됨) —
        # 피해자 잔존은 DB 에서 직접 확인한다(삭제가 한 건도 성공하지 않았음).
        self.assertIsNotNone(db.fetch_subscriber("victim@x.com", path=self.path))

    def test_access_code_is_8_hex_upper(self):
        # [api-code-32bit-expiry-12] 코드가 8자리 대문자 hex(32비트) — 예전 6자리 회귀 감지
        self.client.post("/subscribers", json=self._body(email="a@x.com"))
        code = self._code("a@x.com")
        self.assertEqual(len(code), 8)
        self.assertRegex(code, r"^[0-9A-F]{8}$")

    def test_is_admin_non_ascii_header_returns_false_not_raises(self):
        # [B-4] 비ASCII 관리자 비번은 hmac TypeError(→500)가 아니라 인증 실패(False→401)로 처리
        self.assertFalse(api._is_admin("caf\xe9-한글"))  # é/한글 포함
        # 정상 값은 여전히 통과
        self.assertTrue(api._is_admin(ADMIN_PASSWORD))


# ══════════════════════════════════════════════════════════════
# DB (직접 호출) — 원자적 선점·KST 등장일 집계·보존/정리·인덱스·정규화 마이그레이션
# ══════════════════════════════════════════════════════════════
class TestDbDynamic(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _sub(self, email):
        db.upsert_subscriber({"email": email, "keywords": [], "send_hour": 8, "send_minute": 0,
                              "frequency": "매일", "summary_length": "짧게", "language": "한국어"}, path=self.path)

    def _digest(self, keyword, topic, now, sl="짧게", lang="한국어", headline="H"):
        db.save_digest(keyword, sl, lang,
                       [{"headline": headline, "topic": topic, "topic_summary": "S", "link": "https://a/1"}],
                       now=now, path=self.path)

    def test_claim_atomic_two_threads_one_wins(self):
        # [claim-atomic-01] 같은 슬롯 동시 2스레드 선점 → 정확히 1회만 True
        self._sub("c@x.com")
        now = kst(2026, 7, 6, 8, 0)
        results, barrier = [], threading.Barrier(2)
        lock = threading.Lock()

        def worker():
            barrier.wait()
            r = db.claim_dispatch("c@x.com", now=now, path=self.path)
            with lock:
                results.append(r)

        ts = [threading.Thread(target=worker) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(results), [False, True])

    def test_claim_window_and_reclaim(self):
        # [claim-window-02] 창(90s) 안 2회는 1회만, 창 밖은 재선점
        self._sub("c@x.com")
        t0 = kst(2026, 7, 6, 8, 0)
        self.assertTrue(db.claim_dispatch("c@x.com", now=t0, path=self.path))
        self.assertFalse(db.claim_dispatch("c@x.com", now=t0 + timedelta(seconds=30), path=self.path))
        self.assertTrue(db.claim_dispatch("c@x.com", now=t0 + timedelta(minutes=30), path=self.path))

    def test_claim_missing_email_false(self):
        # [claim-missing-03] 없는 이메일 선점 → 예외 없이 False
        self.assertFalse(db.claim_dispatch("ghost@x.com", now=kst(2026, 7, 6, 8, 0), path=self.path))

    def test_top_topics_days_dominate_snapshot_count(self):
        # [toptopics-daydom-05] 등장'일' 수가 스냅샷 수를 이긴다
        self._digest("주식", "P", kst(2026, 7, 6, 9, 0))
        self._digest("주식", "P", kst(2026, 7, 7, 9, 0))          # P: 서로 다른 2일
        for h in (9, 12, 15):
            self._digest("주식", "Q", kst(2026, 7, 8, h, 0))      # Q: 같은 날 3스냅샷
        since = kst(2026, 7, 1, 0, 0)
        self.assertEqual(db.get_top_topics("주식", since, path=self.path), [("P", 2), ("Q", 1)])

    def test_top_topics_tiebreak_count_then_name(self):
        # [toptopics-tiebreak-06] 일수 동률 → 총 등장수 desc, 이름 asc
        day = kst(2026, 7, 6, 9, 0)
        self._digest("환율", "X", day); self._digest("환율", "X", day.replace(hour=10))  # cnt=2
        self._digest("환율", "A", day.replace(hour=11))
        self._digest("환율", "Y", day.replace(hour=12))
        since = kst(2026, 7, 1, 0, 0)
        self.assertEqual(db.get_top_topics("환율", since, path=self.path), [("X", 1), ("A", 1), ("Y", 1)])

    def test_top_topics_since_excludes_old(self):
        # [toptopics-since-07] since 이전 오래된 스냅샷 제외
        self._digest("금리", "old", kst(2026, 6, 25, 9, 0))
        since = kst(2026, 7, 2, 0, 0)
        self.assertEqual(db.get_top_topics("금리", since, path=self.path), [])

    def test_top_topics_limit_and_default(self):
        # [toptopics-limit-08] limit 반영 + 미지정 시 TREND_TOP_N
        for i in range(7):
            self._digest("코인", f"T{i}", kst(2026, 7, 6, 9, i))
        since = kst(2026, 7, 1, 0, 0)
        self.assertEqual(len(db.get_top_topics("코인", since, limit=3, path=self.path)), 3)
        self.assertEqual(len(db.get_top_topics("코인", since, path=self.path)), config.TREND_TOP_N)

    def test_save_digest_keeps_within_retention(self):
        # [savedigest-keep-09] 보존창(8일=192h) 안 옛 스냅샷은 새 저장에도 유지
        now = kst(2026, 7, 9, 9, 0)
        self._digest("주식", "t1", now - timedelta(hours=190))  # 컷오프(-192h)보다 최근
        self._digest("주식", "t2", now)
        with closing(sqlite3.connect(self.path)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM digests WHERE keyword='주식'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_save_digest_prunes_and_cascades(self):
        # [savedigest-prune-cascade-10] 보존창 밖 옛 스냅샷은 새 저장 시 정리 + 하위 CASCADE
        now = kst(2026, 7, 9, 9, 0)
        old_id = db.save_digest("주식", "짧게", "한국어",
                                [{"headline": "H", "topic": "old", "topic_summary": "S", "link": "https://a/1"}],
                                now=now - timedelta(hours=200), path=self.path)  # 컷오프 밖
        db.save_digest("주식", "짧게", "한국어",
                       [{"headline": "H", "topic": "new", "topic_summary": "S", "link": "https://a/1"}],
                       now=now, path=self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            digests = conn.execute("SELECT COUNT(*) FROM digests WHERE keyword='주식'").fetchone()[0]
            orphan_issues = conn.execute("SELECT COUNT(*) FROM digest_issues WHERE digest_id=?", (old_id,)).fetchone()[0]
        self.assertEqual(digests, 1)         # 옛 스냅샷 삭제
        self.assertEqual(orphan_issues, 0)   # FK CASCADE 로 하위 이슈도 삭제

    def test_prune_old_digests_recent_kept(self):
        # [prune-old-digests-11] 조합 무관 오래된 것만 삭제, 최근 보존
        now = kst(2026, 7, 9, 9, 0)
        self._digest("주식", "old", now - timedelta(hours=200))
        self._digest("금리", "recent", now - timedelta(hours=1))
        removed = db.prune_old_digests(now=now, path=self.path)
        self.assertEqual(removed, 1)
        with closing(sqlite3.connect(self.path)) as conn:
            kws = {r[0] for r in conn.execute("SELECT keyword FROM digests").fetchall()}
        self.assertEqual(kws, {"금리"})

    def test_added_indexes_exist(self):
        # [index-existence-12] 추가된 인덱스 3종 존재
        with closing(sqlite3.connect(self.path)) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        for idx in ("idx_digest_issues_digest", "idx_digest_topics_issue", "idx_digests_created_at"):
            self.assertIn(idx, names)

    def test_top_topic_articles_filters_by_language(self):
        # [A-3] 언어를 주면 그 언어 다이제스트만 훑어, 다른 언어 topic/요약이 안 섞인다
        now = kst(2026, 7, 6, 9, 0)
        db.save_digest("금리", "짧게", "한국어",
                       [{"headline": "H", "topic": "기준금리 동결", "topic_summary": "동결", "link": "https://a/1"}],
                       now=now, path=self.path)
        db.save_digest("금리", "짧게", "영어",
                       [{"headline": "H", "topic": "Rate hold", "topic_summary": "hold", "link": "https://b/1"}],
                       now=now, path=self.path)
        since = kst(2026, 7, 1, 0, 0)
        ko = db.get_top_topic_articles("금리", since, language="한국어", path=self.path)
        en = db.get_top_topic_articles("금리", since, language="영어", path=self.path)
        self.assertEqual([t["topic"] for t in ko], ["기준금리 동결"])   # 한국어만
        self.assertEqual([t["topic"] for t in en], ["Rate hold"])       # 영어만
        allt = {t["topic"] for t in db.get_top_topic_articles("금리", since, path=self.path)}
        self.assertEqual(allt, {"기준금리 동결", "Rate hold"})           # 미지정=전체(하위호환)

    def test_trend_ranks_by_distinct_article_count_not_resummarization(self):
        # [#12] 같은 기사가 여러 날 재요약돼도(예전 '등장일'은 부풀려짐) 링크가 같으면 1로 세고,
        # 서로 다른 기사가 많은 topic 이 상위 — 재요약 팽창에 면역
        since = kst(2026, 7, 1, 0, 0)
        for day in (6, 7, 8):  # topic P: 같은 기사(a/1)가 3일에 걸쳐 재요약됨
            db.save_digest("주식", "짧게", "한국어",
                           [{"headline": "H", "topic": "P", "topic_summary": "S", "link": "https://a/1"}],
                           now=kst(2026, 7, day, 9, 0), path=self.path)
        db.save_digest("주식", "짧게", "한국어",  # topic Q: 서로 다른 기사 2건(하루)
                       [{"headline": "H", "topic": "Q", "topic_summary": "S", "link": "https://b/1"},
                        {"headline": "H", "topic": "Q", "topic_summary": "S", "link": "https://b/2"}],
                       now=kst(2026, 7, 6, 10, 0), path=self.path)
        got = db.get_top_topic_articles("주식", since, path=self.path)
        counts = {t["topic"]: t["article_count"] for t in got}
        self.assertEqual(counts["P"], 1)         # 3일 재요약이어도 링크 1개 → 1(부풀림 없음)
        self.assertEqual(counts["Q"], 2)
        self.assertEqual([t["topic"] for t in got][0], "Q")  # 기사 많은 Q 가 상위

    def test_daily_skips_digest_when_no_recent_article(self):
        # [#11] created_at 은 최신이어도 담은 기사(latest_article_at)가 창 밖이면 그 조합은 발송 안 함
        now = kst(2026, 7, 9, 9, 0)
        db.save_digest("주식", "짧게", "한국어",
                       [{"headline": "H", "topic": "T", "topic_summary": "S", "link": "https://a/1"}],
                       now=now, latest_article_at=(now - timedelta(hours=72)).isoformat(), path=self.path)
        # 일간 창(24h): 최신 기사가 72h 전 → 제외(같은 옛 기사 반복 발송 방지)
        self.assertEqual(db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=now, hours=24, path=self.path), {})
        # 주간 창(168h): 72h 전 기사는 창 안 → 포함
        self.assertIn("주식", db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=now, hours=168, path=self.path))

    def test_fetch_digests_falls_back_to_created_at_when_no_article_date(self):
        # [#11 하위호환] 예전 다이제스트(latest_article_at NULL)는 created_at 으로 신선도 판정
        now = kst(2026, 7, 9, 9, 0)
        db.save_digest("주식", "짧게", "한국어",
                       [{"headline": "H", "topic": "T", "topic_summary": "S", "link": "https://a/1"}],
                       now=now, path=self.path)  # latest_article_at 미지정 → NULL
        self.assertIn("주식", db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=now, hours=24, path=self.path))

    def test_sent_ledger_roundtrip_and_link_normalization(self):
        # [재발송 방지] 기록→조회 왕복, utm 등 추적 파라미터 무시, oid/aid 식별 쿼리는 보존, 사용자 격리
        now = kst(2026, 7, 9, 9, 0)
        db.record_sent_articles("a@x.com", ["https://n/1?utm_source=x", "https://n/2"], now=now, path=self.path)
        seen = db.fetch_seen_links("a@x.com", ["https://n/1", "https://n/2?utm_medium=y", "https://n/3"], path=self.path)
        self.assertEqual(seen, {"https://n/1", "https://n/2?utm_medium=y"})   # 1·2는 봄(utm 무시), 3은 새것
        self.assertEqual(db.fetch_seen_links("b@x.com", ["https://n/1"], path=self.path), set())  # 다른 사람 격리
        a = db._normalize_link("https://n.news.naver.com/read?oid=1&aid=100&utm_source=x")
        b = db._normalize_link("https://n.news.naver.com/read?oid=1&aid=101")
        self.assertNotEqual(a, b)            # 서로 다른 기사(aid)가 안 뭉개짐
        self.assertIn("aid=100", a)          # 식별 쿼리 보존

    def test_prune_sent_articles_removes_old(self):
        # [재발송 방지] 보존 기간 지난 원장 항목 정리
        now = kst(2026, 7, 9, 9, 0)
        db.record_sent_articles("a@x.com", ["https://old"], now=now - timedelta(hours=300), path=self.path)
        db.record_sent_articles("a@x.com", ["https://recent"], now=now - timedelta(hours=1), path=self.path)
        self.assertEqual(db.prune_sent_articles(now=now, path=self.path), 1)
        self.assertEqual(db.fetch_seen_links("a@x.com", ["https://old", "https://recent"], path=self.path),
                         {"https://recent"})

    def test_normalize_link_canonicalizes_param_order(self):
        # [화이트박스 D] 파라미터 순서만 다른 같은 기사는 같은 키, 서로 다른 기사(값)는 여전히 다른 키
        self.assertEqual(db._normalize_link("https://n/x?oid=1&aid=2"),
                         db._normalize_link("https://n/x?aid=2&oid=1"))
        self.assertNotEqual(db._normalize_link("https://n/x?aid=2"),
                            db._normalize_link("https://n/x?aid=3"))

    def test_verify_access_code_non_ascii_returns_false_not_raises(self):
        # [B-4] 비ASCII 본인확인 코드 → hmac TypeError(500) 아니라 False(401)
        db.upsert_subscriber({"email": "v@x.com", "keywords": [], "send_hour": 8, "send_minute": 0,
                              "frequency": "매일", "summary_length": "짧게", "language": "한국어"}, path=self.path)
        db.generate_access_code("v@x.com", path=self.path)
        self.assertFalse(db.verify_access_code("v@x.com", "caf\xe9", path=self.path))

    def test_init_db_merges_case_duplicate_rows(self):
        # [add-db-casing-migration-07] init_db 재실행이 대소문자 중복 행을 확인된 쪽으로 병합
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO subscribers (email, confirmed, updated_at) VALUES ('alice@x.com', 0, '2026-07-01')")
            conn.execute("INSERT INTO subscribers (email, confirmed, updated_at) VALUES ('Alice@x.com', 1, '2026-07-02')")
            conn.commit()
        db.init_db(self.path)  # 멱등 정규화 마이그레이션
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute("SELECT email, confirmed FROM subscribers").fetchall()
        self.assertEqual(rows, [("alice@x.com", 1)])  # 확인된 쪽 생존, 소문자 통일


# ══════════════════════════════════════════════════════════════
# 파이프라인 (mock) — 3잡 연결·조합 요약·원자적 발송·앵커 게이팅·트렌드 캐시
# ══════════════════════════════════════════════════════════════
class TestPipelineDynamic(unittest.TestCase):
    def setUp(self):
        from src import pipeline
        self.pipeline = pipeline
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        p = mock.patch.object(config, "DB_PATH", self.path)
        p.start(); self.addCleanup(p.stop)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _confirmed(self, email, keywords, sl="짧게", lang="한국어", freq="매일"):
        subscriptions.save_subscription({"email": email, "keywords": keywords, "send_hour": 8,
                                         "send_minute": 0, "frequency": freq, "summary_length": sl,
                                         "language": lang}, path=self.path)
        db.mark_confirmed(email, path=self.path)

    def test_three_jobs_chain_through_db(self):
        # [pipe-flow-01] collect→summarize→dispatch 가 DB 를 통해 값으로 이어진다
        self._confirmed("a@x.com", ["주식"])
        article = {"title": "삼성 주가 급등", "link": "https://a/1", "description": "d",
                   "published_at": kst(2026, 7, 6, 7, 0).isoformat()}
        summ_row = {"주식": [{"headline": "삼성 실적", "topic": "급등", "topic_summary": "요약", "link": "https://a/1"}]}
        with mock.patch.object(self.pipeline.naver_news, "collect", return_value={"주식": [article]}), \
             mock.patch.object(self.pipeline.summarizer, "summarize", return_value=summ_row), \
             mock.patch.object(self.pipeline.send_email, "send_email") as send:
            now = kst(2026, 7, 6, 8, 0)
            self.assertEqual(self.pipeline.collect_job(now=now), 1)
            self.assertEqual(len(db.fetch_articles_for_keyword("주식", now=now, path=self.path)), 1)
            self.assertEqual(self.pipeline.summarize_job(now=now), 1)
            got = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=now, path=self.path)
            self.assertEqual(got["주식"][0]["headline"], "삼성 실적")
            self.pipeline.dispatch_job(now=now)  # 08:00 매일 → 발송 대상
            daily = [c for c in send.call_args_list if "데일리" in c.kwargs["subject"]]
            self.assertEqual(len(daily), 1)  # 일간 뉴스레터 1통(월요일이라 별도 주간 메일도 함께 나감)
            self.assertEqual(daily[0].args[0], "a@x.com")

    def test_summarize_by_combo_not_by_subscriber(self):
        # [pipe-combo-02] 요약은 구독자 수가 아니라 (kw,길이,언어) 조합 수만큼
        self._confirmed("a@x.com", ["주식"], sl="짧게", lang="한국어")
        self._confirmed("b@x.com", ["주식"], sl="짧게", lang="한국어")  # 같은 조합 공유
        self._confirmed("c@x.com", ["주식"], sl="길게", lang="영어")
        article = {"title": "t", "link": "https://a/1", "description": "d",
                   "published_at": kst(2026, 7, 6, 7, 0).isoformat()}
        db.save_articles({"주식": [article]}, now=kst(2026, 7, 6, 7, 30), path=self.path)
        seen = []

        def fake_summarize(collected, sl, lang):
            seen.append((sl, lang))
            return {kw: [{"headline": "H", "topic": "T", "topic_summary": "S", "link": "https://a/1"}]
                    for kw in collected}

        with mock.patch.object(self.pipeline.summarizer, "summarize", side_effect=fake_summarize):
            created = self.pipeline.summarize_job(now=kst(2026, 7, 6, 8, 0))
        self.assertEqual(created, 2)
        self.assertEqual(set(seen), {("짧게", "한국어"), ("길게", "영어")})

    def test_same_tick_dispatch_sends_once(self):
        # [add-pipe-claim-concurrent-04 / pipe-claim-03] 같은 슬롯 재실행은 1통만
        self._confirmed("a@x.com", ["주식"])
        digests = {"주식": [{"headline": "H", "topics": [{"topic": "T", "topic_summary": "S", "links": ["https://a/1"]}]}]}
        sub = subscriptions.get_subscription("a@x.com", path=self.path)
        with mock.patch.object(db, "fetch_digests_for_keywords", return_value=digests), \
             mock.patch.object(self.pipeline.report, "render", return_value="<html></html>"), \
             mock.patch.object(self.pipeline.send_email, "send_email") as send:
            now = kst(2026, 7, 6, 8, 0)
            self.pipeline.dispatch_one(sub, now=now)
            self.pipeline.dispatch_one(sub, now=now + timedelta(seconds=5))  # 같은 슬롯
            send.assert_called_once()

    def test_weekly_anchor_is_earliest_weekday(self):
        # [pipe-anchor-04] is_weekly_anchor 는 발송일 중 '가장 이른 요일'만 앵커
        sub = subscriptions.Subscription(email="a@x.com", frequency="주 3회")  # {월,수,금}
        mon = kst(2026, 7, 6, 8, 0); mon = mon - timedelta(days=mon.weekday())
        wed = mon + timedelta(days=2)
        self.assertTrue(subscriptions.is_weekly_anchor(sub, mon))
        self.assertFalse(subscriptions.is_weekly_anchor(sub, wed))

    def test_weekly_anchor_follows_rule_change(self):
        # [pipe-anchor-derive-05] 발송 요일 규칙을 바꾸면 앵커가 따라 이동(하드코딩 아님)
        sub = subscriptions.Subscription(email="a@x.com", frequency="매주")
        mon = kst(2026, 7, 6, 8, 0); mon = mon - timedelta(days=mon.weekday())
        tue = mon + timedelta(days=1)
        with mock.patch.dict(config.FREQUENCY_WEEKDAYS, {"매주": {1}}):  # 월→화로 규칙 변경
            self.assertFalse(subscriptions.is_weekly_anchor(sub, mon))
            self.assertTrue(subscriptions.is_weekly_anchor(sub, tue))

    def test_trend_cache_queries_once_per_keyword(self):
        # [pipe-trendcache-06/07] 같은 실행 내 트렌드 집계는 캐시로 키워드당 1회
        now = kst(2026, 7, 6, 9, 0)

        def fake_top(kw, since, language=None, path=None):
            return [{"topic": "t", "days": 1, "summary": "", "links": []}] if kw == "주식" else []

        with mock.patch.object(db, "get_top_topic_articles", side_effect=fake_top) as spy:
            cache = {}
            r1 = self.pipeline.weekly_trend_articles_for(["주식", "금리"], now, cache=cache)
            r2 = self.pipeline.weekly_trend_articles_for(["주식", "금리"], now, cache=cache)
        self.assertEqual(list(r1.keys()), ["주식"])  # 빈 결과 키워드는 제외
        self.assertEqual(list(r2.keys()), ["주식"])
        self.assertEqual(spy.call_count, 2)  # 주식·금리 각 1회, 2번째 호출은 캐시 적중(0회)

    def test_weekly_trend_is_separate_email_only_on_anchor(self):
        # [add-pipe-anchor-render-03] 앵커 요일에만 '별도' 주간 트렌드 메일이 나간다
        self._confirmed("a@x.com", ["금리"], freq="매주")  # {월}
        sub = subscriptions.get_subscription("a@x.com", path=self.path)
        mon = kst(2026, 7, 6, 8, 0); mon = mon - timedelta(days=mon.weekday())
        wed = mon + timedelta(days=2)
        digests = {"금리": [{"headline": "H", "topics": [{"topic": "T", "topic_summary": "S", "links": ["https://a/1"]}]}]}
        articles = [{"topic": "한국은행 기준금리 동결", "days": 1, "summary": "동결 결정", "links": ["https://a/1"]}]
        with mock.patch.object(db, "fetch_digests_for_keywords", return_value=digests), \
             mock.patch.object(db, "claim_dispatch", return_value=True), \
             mock.patch.object(db, "get_top_topic_articles", return_value=articles), \
             mock.patch.object(self.pipeline.send_email, "send_email") as send:
            self.pipeline.dispatch_one(sub, now=mon)
            mon_subjects = [c.kwargs["subject"] for c in send.call_args_list]
            weekly = next(c for c in send.call_args_list if "주간 트렌드" in c.kwargs["subject"])
            send.reset_mock()
            self.pipeline.dispatch_one(sub, now=wed)  # 매주={월} 이라 수요일은 앵커 아님
            wed_subjects = [c.kwargs["subject"] for c in send.call_args_list]
        self.assertEqual(len(mon_subjects), 2)  # 월: 일간 + 주간(별도 메일)
        self.assertIn("THIS WEEK'S TREND", weekly.kwargs["body_html"])
        self.assertIn("한국은행 기준금리 동결", weekly.kwargs["body_html"])
        self.assertTrue(all("주간 트렌드" not in s for s in wed_subjects))  # 수: 주간 메일 없음

    def test_daily_send_failure_does_not_block_weekly(self):
        # [A-2] 앵커일에 일간 발송이 SMTP 오류로 실패해도, 별도 주간 트렌드 메일은 그대로 나간다
        self._confirmed("a@x.com", ["금리"], freq="매주")
        sub = subscriptions.get_subscription("a@x.com", path=self.path)
        mon = kst(2026, 7, 6, 8, 0); mon = mon - timedelta(days=mon.weekday())
        digests = {"금리": [{"headline": "H", "topics": [{"topic": "T", "topic_summary": "S", "links": ["https://a/1"]}]}]}
        articles = [{"topic": "T", "days": 1, "summary": "S", "links": ["https://a/1"]}]

        def send_side(to, subject, body_html):
            if "데일리" in subject:
                raise RuntimeError("SMTP 일시 오류")  # 일간만 실패

        with mock.patch.object(db, "fetch_digests_for_keywords", return_value=digests), \
             mock.patch.object(db, "claim_dispatch", return_value=True), \
             mock.patch.object(db, "get_top_topic_articles", return_value=articles), \
             mock.patch.object(self.pipeline.send_email, "send_email", side_effect=send_side) as send:
            self.pipeline.dispatch_one(sub, now=mon)  # 예외가 밖으로 전파되면 안 됨
        subjects = [c.kwargs["subject"] for c in send.call_args_list]
        self.assertTrue(any("데일리" in s for s in subjects))       # 일간 시도(실패)
        self.assertTrue(any("주간 트렌드" in s for s in subjects))   # 일간 실패에도 주간은 발송 시도됨

    def test_no_repeat_sends_only_new_articles_then_skips(self):
        # [재발송 방지 flagship] 이미 받은 기사는 다음 발송에서 빠지고, 새 게 없으면 일간을 안 보냄
        self._confirmed("a@x.com", ["주식"])  # freq 매일
        sub = subscriptions.get_subscription("a@x.com", path=self.path)

        def save(links, now):
            rows = [{"headline": "H", "topic": "T", "topic_summary": "S", "link": link} for link in links]
            db.save_digest("주식", "짧게", "한국어", rows, now=now,
                           latest_article_at=now.isoformat(), path=self.path)

        tue = kst(2026, 7, 7, 8, 0)   # 화요일(비앵커: 주간 트렌드 안 섞임)
        self.assertEqual(tue.weekday(), 1)
        # 1차: 기사 1,2 발송
        save(["https://n/1", "https://n/2"], tue)
        with mock.patch.object(self.pipeline.send_email, "send_email") as send:
            self.pipeline.dispatch_one(sub, now=tue)
        self.assertEqual(send.call_count, 1)
        self.assertIn("n/1", send.call_args.kwargs["body_html"])

        # 2차(다음날): 1,2 + 새 기사 3 → 새 것만 나가고 1·2는 빠짐
        wed = kst(2026, 7, 8, 8, 0)
        save(["https://n/1", "https://n/2", "https://n/3"], wed)
        with mock.patch.object(self.pipeline.send_email, "send_email") as send2:
            self.pipeline.dispatch_one(sub, now=wed)
        self.assertEqual(send2.call_count, 1)
        body2 = send2.call_args.kwargs["body_html"]
        self.assertIn("n/3", body2)          # 새 기사
        self.assertNotIn("n/1", body2)       # 이미 본 기사 빠짐
        self.assertNotIn("n/2", body2)

        # 3차: 새 기사 없음(1,2,3 다 봄) → 일간 발송 스킵
        thu = kst(2026, 7, 9, 8, 0)
        save(["https://n/1", "https://n/2", "https://n/3"], thu)
        with mock.patch.object(self.pipeline.send_email, "send_email") as send3:
            self.pipeline.dispatch_one(sub, now=thu)
        send3.assert_not_called()

    def test_record_failure_does_not_undo_send(self):
        # [화이트박스 B] 발송 성공 후 원장 기록만 실패해도 예외가 밖으로 안 나가고 메일은 발송된 것으로 처리
        self._confirmed("a@x.com", ["금리"])
        sub = subscriptions.get_subscription("a@x.com", path=self.path)
        now = kst(2026, 7, 7, 8, 0)  # 화요일(비앵커)
        digests = {"금리": [{"headline": "H", "topics": [{"topic": "T", "topic_summary": "S", "links": ["https://a/1"]}]}]}
        with mock.patch.object(db, "fetch_digests_for_keywords", return_value=digests), \
             mock.patch.object(db, "fetch_seen_links", return_value=set()), \
             mock.patch.object(db, "claim_dispatch", return_value=True), \
             mock.patch.object(db, "record_sent_articles", side_effect=RuntimeError("DB 락")), \
             mock.patch.object(self.pipeline.send_email, "send_email") as send:
            self.pipeline.dispatch_one(sub, now=now)  # 예외 전파되면 안 됨
        send.assert_called_once()  # 메일은 발송됨(기록 실패가 발송을 무르지 않음)


# ══════════════════════════════════════════════════════════════
# LLM 격리 — QA 빈 응답(TypeError) 격리 · 주입 클라이언트 존중 · 키 없이 import
# ══════════════════════════════════════════════════════════════
class TestLlmDynamic(unittest.TestCase):
    def test_qa_type_error_isolated_draft_survives(self):
        # [llm-qa-typeerr-08 / add-llm-qa-degrade-draft-05] QA TypeError 1건이 나머지 요약을 안 막음
        def fake_summarize_agent(news_context, language, sr):
            link = re.search(r"링크: (\S+)", news_context)
            link = link.group(1) if link else ""
            return json.dumps({"issues": [{"headline": "H", "topics": [{"subtitle": "T", "summary": "S"}],
                                           "articles": [link] if link else []}]})

        calls = {"n": 0}

        def fake_qa_agent(draft_json, original_links, language, sr):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TypeError("빈 응답 → json.loads(None)")  # 첫 쿼리 QA 실패
            return draft_json.get("issues", []), []

        collected = {
            "주식": [{"title": "t1", "description": "d", "link": "https://a/1", "published_at": ""}],
            "금리": [{"title": "t2", "description": "d", "link": "https://b/1", "published_at": ""}],
        }
        with mock.patch.object(summarizer, "_summarize_agent", side_effect=fake_summarize_agent), \
             mock.patch.object(summarizer, "_qa_agent", side_effect=fake_qa_agent):
            result = summarizer.summarize(collected, "짧게", "한국어")  # 예외 전파 없어야
        self.assertEqual(set(result.keys()), {"주식", "금리"})
        self.assertEqual(result["주식"], [{"headline": "H", "topic": "T", "topic_summary": "S", "link": "https://a/1"}])
        self.assertTrue(result["금리"])  # 두 번째 쿼리도 살아남음

    def test_injected_client_respected_else_requires_key(self):
        # [llm-inject-10] 주입된 client 존중(재생성/키요구 없음), 없고 키 없으면 RuntimeError
        import LLM_fn
        saved_client, saved_key = LLM_fn.client, LLM_fn.openAI_api_key
        try:
            sentinel = object()
            LLM_fn.client = sentinel
            self.assertIs(LLM_fn._client(), sentinel)
            LLM_fn.client = None
            LLM_fn.openAI_api_key = None
            with self.assertRaises(RuntimeError) as ctx:
                LLM_fn._client()
            self.assertIn("OPENAI_API_KEY", str(ctx.exception))
        finally:
            LLM_fn.client, LLM_fn.openAI_api_key = saved_client, saved_key

    def test_modules_import_without_openai_key(self):
        # [llm-lazyimport-09] 키 없이도 LLM_fn·summarizer·pipeline import 성공(호출 때만 에러)
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        code = ("import LLM_fn, src.processors.summarizer, src.pipeline;"
                "assert LLM_fn.client is None;"
                "print('MODEL=' + LLM_fn.MODEL_NAME)")
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              cwd=config.BASE_DIR, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("MODEL=", proc.stdout)

    def test_qa_null_issues_isolated_and_others_survive(self):
        # [A-1] QA가 유효 JSON {"issues": null}(→ None) 을 줘도 summarize()가 안 죽고 그 쿼리만 빈 결과로 격리
        def fake_summarize_agent(nc, lang, sr):
            return json.dumps({"issues": [{"headline": "H", "topics": [{"subtitle": "T", "summary": "S"}],
                                           "articles": []}]})

        def fake_qa_agent(draft, original_links, lang, sr):
            # 주식(links a/1)은 issues=null(None) 반환 경로, 금리(links b/1)는 정상
            return (None if "https://a/1" in original_links else draft.get("issues")), []

        collected = {
            "주식": [{"title": "t", "description": "d", "link": "https://a/1", "published_at": ""}],
            "금리": [{"title": "t2", "description": "d", "link": "https://b/1", "published_at": ""}],
        }
        with mock.patch.object(summarizer, "_summarize_agent", side_effect=fake_summarize_agent), \
             mock.patch.object(summarizer, "_qa_agent", side_effect=fake_qa_agent):
            result = summarizer.summarize(collected, "짧게", "한국어")  # TypeError 전파 없어야
        self.assertEqual(set(result.keys()), {"주식", "금리"})
        self.assertEqual(result["주식"], [])   # null issues → 빈 결과로 격리
        self.assertTrue(result["금리"])          # 나머지 쿼리는 살아남음

    def test_null_headline_topic_summary_coerced_to_empty_string(self):
        # [A-1/#3] headline/subtitle/summary 가 JSON null 이어도 None 아닌 "" 로 정규화(NOT NULL INSERT/렌더 크래시 방지)
        def fake_summarize_agent(nc, lang, sr):
            return json.dumps({"issues": [{"headline": None, "topics": [{"subtitle": None, "summary": None}],
                                           "articles": ["https://a/1"]}]})

        def fake_qa_agent(draft, original_links, lang, sr):
            return draft.get("issues"), []

        collected = {"금리": [{"title": "t", "description": "d", "link": "https://a/1", "published_at": ""}]}
        with mock.patch.object(summarizer, "_summarize_agent", side_effect=fake_summarize_agent), \
             mock.patch.object(summarizer, "_qa_agent", side_effect=fake_qa_agent):
            row = summarizer.summarize(collected, "짧게", "한국어")["금리"][0]
        self.assertEqual((row["headline"], row["topic"], row["topic_summary"]), ("", "", ""))
        self.assertNotIn(None, row.values())

    def test_analyze_news_qa_error_and_null_issues_do_not_raise(self):
        # [B-5] analyze_news 의 QA except 에 TypeError 추가 + issues 비정상 방어로 '항상 dict 반환' 계약 유지
        import LLM_fn
        draft = json.dumps({"issues": [{"headline": "H", "topics": [{"subtitle": "T", "summary": "S"}],
                                        "articles": ["https://a/1"]}]})
        raw = {"items": [{"title": "t", "description": "d", "link": "https://a/1"}]}
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)  # analyze_news 가 cwd 에 결과 파일을 쓰므로 임시 폴더에서 실행
            try:
                with mock.patch.object(LLM_fn, "_summarize_agent", return_value=draft), \
                     mock.patch.object(LLM_fn, "_qa_agent", side_effect=TypeError("빈 응답")):
                    out1 = LLM_fn.analyze_news(raw, language="한국어", length="짧게")  # QA TypeError → 초안 degrade
                with mock.patch.object(LLM_fn, "_summarize_agent", return_value=draft), \
                     mock.patch.object(LLM_fn, "_qa_agent", return_value=(None, [])):
                    out2 = LLM_fn.analyze_news(raw, language="한국어", length="짧게")  # issues=null → 방어
            finally:
                os.chdir(cwd)
        self.assertTrue(out1["success"])  # 예외 전파 없이 성공 dict 반환
        self.assertTrue(out2["success"])

    def test_clean_keywords_handles_non_list(self):
        # [B-6] keywords 가 리스트가 아니어도 글자쪼개기/TypeError 없이 안전
        self.assertEqual(subscriptions._clean_keywords("주식"), ["주식"])   # 문자열 → 1개 키워드
        self.assertEqual(subscriptions._clean_keywords(5), [])              # 스칼라 → []
        self.assertEqual(subscriptions._clean_keywords(None), [])
        self.assertEqual(subscriptions._clean_keywords(["주식", " 주식 ", "", "금리"]), ["주식", "금리"])  # 기존 정제 유지


# ══════════════════════════════════════════════════════════════
# 렌더 — 계층 카드 · 단일패스 치환 · HTML 이스케이프 · 위험스킴 · 트렌드 표시/다크
# ══════════════════════════════════════════════════════════════
class TestRenderDynamic(unittest.TestCase):
    def _digests(self, topic_summary="요약본문", links=None, query="주식", topic="매출 급증", headline="삼성 실적"):
        return {query: [{"headline": headline,
                         "topics": [{"topic": topic, "topic_summary": topic_summary,
                                     "links": links if links is not None else ["https://a.example/1", "https://a.example/2"]}]}]}

    def test_card_uses_topic_and_first_link(self):
        # [render-hier-01] 카드 제목=topic, 링크=links[0] (links[1] 은 미노출)
        out = report.render(self._digests(), now=kst(2026, 7, 6, 9, 0))
        self.assertIn("매출 급증", out)
        self.assertIn("요약본문", out)
        self.assertIn("https://a.example/1", out)
        self.assertNotIn("https://a.example/2", out)

    def test_fill_single_pass_preserves_literal_placeholder(self):
        # [render-fill-02] 요약에 '{{원문_링크}}' 리터럴이 있어도 URL 로 덮이지 않음
        out = report.render(self._digests(topic_summary="본문 {{원문_링크}} 끝", links=["https://real-xyz/1"]),
                            now=kst(2026, 7, 6, 9, 0))
        self.assertIn("본문 {{원문_링크}} 끝", out)
        expected = report._ITEM_BLOCK.count("{{원문_링크}}")
        self.assertEqual(out.count("https://real-xyz/1"), expected)

    def test_html_escape_of_scraped_text(self):
        # [render-escape-03] AI/스크랩 텍스트·카테고리 HTML 이스케이프
        out = report.render(self._digests(topic_summary="<script>alert(1)</script>", query="<b>주식</b>"),
                            now=kst(2026, 7, 6, 9, 0))
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)
        self.assertNotIn("<b>주식</b>", out)

    def test_dangerous_scheme_href_neutralized(self):
        # [render-href-04] javascript:/속성탈출 href 무력화
        out_js = report.render(self._digests(links=["javascript:alert(1)"]), now=kst(2026, 7, 6, 9, 0))
        self.assertNotIn("javascript:", out_js)
        self.assertIn('href="#"', out_js)
        out_esc = report.render(self._digests(links=['"><img src=x>']), now=kst(2026, 7, 6, 9, 0))
        self.assertNotIn("<img", out_esc)

    def test_weekly_trend_rank_order_with_articles(self):
        # [render-trend-05] 별도 주간 메일: 순위 순서 + 요약·관련 기사, 건수 미노출
        out = report.render_weekly_trend({"주식": [
            {"topic": "실적 발표", "days": 999, "summary": "삼성 실적 호조", "links": ["https://a/1"]},
            {"topic": "금리 인하", "days": 777, "summary": "인하 기대", "links": ["https://a/2"]},
        ]}, now=kst(2026, 7, 6, 9, 0))
        self.assertIn("THIS WEEK'S TREND", out)
        self.assertLess(out.index("실적 발표"), out.index("금리 인하"))
        self.assertIn("삼성 실적 호조", out)                 # 요약도 함께
        self.assertIn('href="https://a/1"', out)            # 관련 기사 링크
        self.assertNotIn(">999<", out)                      # 건수 미노출

    def test_weekly_trend_dark_mode_classes(self):
        # [render-trend-dark-06] 주간 메일 요소에 다크모드 클래스 부착
        out = report.render_weekly_trend({"주식": [
            {"topic": "실적 발표", "days": 2, "summary": "S", "links": ["https://a/1"]}]},
            now=kst(2026, 7, 6, 9, 0))
        self.assertIn('class="link-gold"', out)
        self.assertIn('class="text-title"', out)
        self.assertIn('class="text-body"', out)

    def test_weekly_trend_omitted_when_empty(self):
        # [render-trend-omit-07] 트렌드 없음/빈dict 이면 빈 문자열(별도 메일 미발송)
        self.assertEqual(report.render_weekly_trend(None), "")
        self.assertEqual(report.render_weekly_trend({}), "")

    def test_daily_render_has_no_trend(self):
        # 완전 분리: 일간 뉴스레터에는 트렌드가 들어가지 않는다
        out = report.render(self._digests(), now=kst(2026, 7, 6, 9, 0))
        self.assertNotIn("THIS WEEK'S TREND", out)


# ══════════════════════════════════════════════════════════════
# 이메일 — SMTP 소켓 타임아웃 전달
# ══════════════════════════════════════════════════════════════
class TestEmailDynamic(unittest.TestCase):
    def test_smtp_ssl_receives_timeout(self):
        # [email-timeout-08] SMTP_SSL 에 timeout=SMTP_TIMEOUT 전달(무응답 무한대기 방지)
        with mock.patch.object(send_email_mod.smtplib, "SMTP_SSL") as ssl_ctor:
            send_email_mod.send_email("a@x.com", "제목", "<p>본문</p>")
        _, kwargs = ssl_ctor.call_args
        self.assertEqual(ssl_ctor.call_args.args, ("smtp.gmail.com", 465))
        self.assertEqual(kwargs.get("timeout"), config.SMTP_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
