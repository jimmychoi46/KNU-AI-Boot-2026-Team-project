"""구독자 API 엔드포인트 테스트 (FastAPI TestClient, 임시 DB로 동작).

실행:  python -m pytest   또는   python -m unittest
"""
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from src import api, config, db


ADMIN_PASSWORD = "test-admin-secret"


class TestSubscriberApi(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        # 모든 db 접근이 임시 DB를 쓰도록 config.DB_PATH 교체 후 스키마 생성
        patcher = mock.patch.object(config, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        # api.py 는 `from src import config` 로 같은 config 모듈을 참조하므로 이 patch로 충분
        admin_patcher = mock.patch.object(config, "ADMIN_PASSWORD", ADMIN_PASSWORD)
        admin_patcher.start()
        self.addCleanup(admin_patcher.stop)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.client = TestClient(api.app)

    def _body(self, **over):
        d = {"email": "a@x.com", "name": "홍길동", "keywords": ["주식", "금리"],
             "send_hour": 8, "send_minute": 30}
        d.update(over)
        return d

    # ── POST ──────────────────────────────────────────────────
    def test_create_returns_201_and_normalized(self):
        # 키워드는 자유 입력 — 후보 제한 없이 공백/중복만 정리
        res = self.client.post("/subscribers", json=self._body(keywords=["수학", " 주식 ", "수학"]))
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["email"], "a@x.com")
        self.assertEqual(body["keywords"], ["수학", "주식"])   # 정리만(제한 X)
        self.assertEqual(body["send_hour"], 8)
        self.assertEqual(body["frequency"], "매일")     # 미지정 → 기본값

    def test_create_duplicate_returns_409(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.post("/subscribers", json=self._body())
        self.assertEqual(res.status_code, 409)

    def test_create_invalid_minute_returns_400(self):
        res = self.client.post("/subscribers", json=self._body(send_minute=15))
        self.assertEqual(res.status_code, 400)

    def test_create_hour_24_ok(self):
        res = self.client.post("/subscribers", json=self._body(send_hour=24, send_minute=0))
        self.assertEqual(res.status_code, 201)

    # ── GET /subscribers (관리자 인증) ───────────────────────────
    def test_list_subscribers_with_correct_password(self):
        self.client.post("/subscribers", json=self._body(email="a@x.com"))
        self.client.post("/subscribers", json=self._body(email="b@x.com"))
        res = self.client.get("/subscribers", headers={"X-Admin-Password": ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertEqual({r["email"] for r in res.json()}, {"a@x.com", "b@x.com"})

    def test_list_subscribers_without_header_returns_401(self):
        res = self.client.get("/subscribers")
        self.assertEqual(res.status_code, 401)

    def test_list_subscribers_with_wrong_password_returns_401(self):
        res = self.client.get("/subscribers", headers={"X-Admin-Password": "wrong"})
        self.assertEqual(res.status_code, 401)

    def test_list_subscribers_rejected_when_admin_password_unset(self):
        # 서버에 ADMIN_PASSWORD 가 아예 설정 안 됐으면(운영 실수), 맞는 값을 보내도 거부한다
        with mock.patch.object(config, "ADMIN_PASSWORD", None):
            res = self.client.get("/subscribers", headers={"X-Admin-Password": ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 401)

    def test_get_one(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.get("/subscribers/a@x.com")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "홍길동")

    def test_get_missing_returns_404(self):
        self.assertEqual(self.client.get("/subscribers/none@x.com").status_code, 404)

    # ── PUT ───────────────────────────────────────────────────
    def test_update_replaces(self):
        self.client.post("/subscribers", json=self._body(send_hour=8))
        res = self.client.put("/subscribers/a@x.com",
                              json={"keywords": ["환율"], "send_hour": 18, "send_minute": 0})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["send_hour"], 18)
        self.assertEqual(body["keywords"], ["환율"])

    def test_update_missing_returns_404(self):
        res = self.client.put("/subscribers/none@x.com",
                              json={"send_hour": 8, "send_minute": 0})
        self.assertEqual(res.status_code, 404)

    def test_update_invalid_returns_400(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.put("/subscribers/a@x.com",
                              json={"send_hour": 8, "send_minute": 45})  # 45분 불가
        self.assertEqual(res.status_code, 400)

    # ── DELETE ────────────────────────────────────────────────
    def test_delete(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.delete("/subscribers/a@x.com")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.client.get("/subscribers/a@x.com").status_code, 404)

    def test_delete_missing_returns_404(self):
        self.assertEqual(self.client.delete("/subscribers/none@x.com").status_code, 404)


if __name__ == "__main__":
    unittest.main()
