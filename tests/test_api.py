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
        # 실제 SMTP 연결 방지(느림·네트워크 의존) — 확인 메일 발송 여부만 스파이로 확인
        send_patcher = mock.patch.object(api.send_email, "send_email")
        self.send_email = send_patcher.start()
        self.addCleanup(send_patcher.stop)

    def _body(self, **over):
        d = {"email": "a@x.com", "name": "홍길동", "keywords": ["주식", "금리"],
             "send_hour": 8, "send_minute": 30}
        d.update(over)
        return d

    def _admin_headers(self):
        return {"X-Admin-Password": ADMIN_PASSWORD}

    def _access_code_for(self, email):
        """email 앞으로 본인 확인 코드를 발급받아(엔드포인트 호출) 그 값을 DB에서 그대로 읽어온다."""
        self.client.post(f"/subscribers/{email}/access-code")
        return db.peek_access_code(email, path=self.path)

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

    def test_create_confirmed_duplicate_returns_409(self):
        self.client.post("/subscribers", json=self._body())
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.client.get(f"/confirm?token={token}")  # 확인 완료 상태로 만듦
        res = self.client.post("/subscribers", json=self._body())
        self.assertEqual(res.status_code, 409)

    def test_create_unconfirmed_duplicate_resends_instead_of_409(self):
        # 확인 전에 같은 이메일로 다시 신청하면 409가 아니라 재전송(같은 토큰 유지)
        self.client.post("/subscribers", json=self._body())
        token1 = db.fetch_confirm_token("a@x.com", path=self.path)
        res = self.client.post("/subscribers", json=self._body(name="다시 신청"))
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.json()["confirmed"])
        token2 = db.fetch_confirm_token("a@x.com", path=self.path)
        self.assertEqual(token1, token2)

    def test_create_sends_confirmation_email(self):
        self.client.post("/subscribers", json=self._body())
        self.send_email.assert_called_once()
        self.assertEqual(self.send_email.call_args.args[0], "a@x.com")

    def test_create_returns_unconfirmed(self):
        res = self.client.post("/subscribers", json=self._body())
        self.assertFalse(res.json()["confirmed"])

    def test_create_invalid_minute_returns_400(self):
        res = self.client.post("/subscribers", json=self._body(send_minute=15))
        self.assertEqual(res.status_code, 400)

    def test_create_hour_24_ok(self):
        res = self.client.post("/subscribers", json=self._body(send_hour=24, send_minute=0))
        self.assertEqual(res.status_code, 201)

    # ── GET /confirm (더블 옵트인) ────────────────────────────────
    def test_confirm_with_valid_token_returns_200(self):
        self.client.post("/subscribers", json=self._body())
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        res = self.client.get(f"/confirm?token={token}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("a@x.com", res.text)

    def test_confirm_makes_subscriber_confirmed(self):
        self.client.post("/subscribers", json=self._body())
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.client.get(f"/confirm?token={token}")
        res = self.client.get("/subscribers/a@x.com", headers=self._admin_headers())
        self.assertTrue(res.json()["confirmed"])

    def test_confirm_with_invalid_token_returns_400(self):
        res = self.client.get("/confirm?token=not-a-real-token")
        self.assertEqual(res.status_code, 400)

    def test_confirm_token_cannot_be_reused(self):
        self.client.post("/subscribers", json=self._body())
        token = db.fetch_confirm_token("a@x.com", path=self.path)
        self.client.get(f"/confirm?token={token}")
        res = self.client.get(f"/confirm?token={token}")  # 재사용
        self.assertEqual(res.status_code, 400)

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

    def test_get_one_with_access_code(self):
        self.client.post("/subscribers", json=self._body())
        code = self._access_code_for("a@x.com")
        res = self.client.get("/subscribers/a@x.com", headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "홍길동")

    def test_get_one_with_admin_password(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.get("/subscribers/a@x.com", headers=self._admin_headers())
        self.assertEqual(res.status_code, 200)

    def test_get_one_without_auth_returns_401(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.get("/subscribers/a@x.com")
        self.assertEqual(res.status_code, 401)

    def test_get_one_with_wrong_access_code_returns_401(self):
        self.client.post("/subscribers", json=self._body())
        self._access_code_for("a@x.com")
        res = self.client.get("/subscribers/a@x.com", headers={"X-Access-Code": "WRONG1"})
        self.assertEqual(res.status_code, 401)

    def test_get_one_with_expired_access_code_returns_401(self):
        self.client.post("/subscribers", json=self._body())
        code = db.generate_access_code("a@x.com", ttl_minutes=-1, path=self.path)
        res = self.client.get("/subscribers/a@x.com", headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 401)

    def test_get_missing_with_admin_returns_404(self):
        res = self.client.get("/subscribers/none@x.com", headers=self._admin_headers())
        self.assertEqual(res.status_code, 404)

    def test_get_missing_without_auth_returns_401(self):
        # 존재 여부보다 인증이 먼저 걸린다 — 없는 이메일도 진짜 주인인지 증명 못 하면 401
        res = self.client.get("/subscribers/none@x.com")
        self.assertEqual(res.status_code, 401)

    # ── 본인 확인 코드 발송 ──────────────────────────────────────
    def test_request_access_code_sends_email(self):
        self.client.post("/subscribers", json=self._body())
        self.send_email.reset_mock()  # 가입 확인 메일 호출 기록은 제외하고 본다
        res = self.client.post("/subscribers/a@x.com/access-code")
        self.assertEqual(res.status_code, 202)
        self.send_email.assert_called_once()
        self.assertEqual(self.send_email.call_args.args[0], "a@x.com")

    def test_request_access_code_missing_email_returns_404(self):
        res = self.client.post("/subscribers/none@x.com/access-code")
        self.assertEqual(res.status_code, 404)

    def test_access_code_reusable_within_window(self):
        # 조회 후 수정처럼 API를 연달아 부르는 흐름을 지원하기 위해 1회용이 아니다
        self.client.post("/subscribers", json=self._body())
        code = self._access_code_for("a@x.com")
        res1 = self.client.get("/subscribers/a@x.com", headers={"X-Access-Code": code})
        res2 = self.client.put("/subscribers/a@x.com", json={"send_hour": 9, "send_minute": 0},
                               headers={"X-Access-Code": code})
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)

    # ── PUT ───────────────────────────────────────────────────
    def test_update_replaces(self):
        self.client.post("/subscribers", json=self._body(send_hour=8))
        code = self._access_code_for("a@x.com")
        res = self.client.put("/subscribers/a@x.com",
                              json={"keywords": ["환율"], "send_hour": 18, "send_minute": 0},
                              headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["send_hour"], 18)
        self.assertEqual(body["keywords"], ["환율"])

    def test_update_without_auth_returns_401(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.put("/subscribers/a@x.com",
                              json={"send_hour": 8, "send_minute": 0})
        self.assertEqual(res.status_code, 401)

    def test_update_missing_with_admin_returns_404(self):
        res = self.client.put("/subscribers/none@x.com",
                              json={"send_hour": 8, "send_minute": 0},
                              headers=self._admin_headers())
        self.assertEqual(res.status_code, 404)

    def test_update_invalid_returns_400(self):
        self.client.post("/subscribers", json=self._body())
        code = self._access_code_for("a@x.com")
        res = self.client.put("/subscribers/a@x.com",
                              json={"send_hour": 8, "send_minute": 45},  # 45분 불가
                              headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 400)

    # ── DELETE ────────────────────────────────────────────────
    def test_delete_with_access_code(self):
        self.client.post("/subscribers", json=self._body())
        code = self._access_code_for("a@x.com")
        res = self.client.delete("/subscribers/a@x.com", headers={"X-Access-Code": code})
        self.assertEqual(res.status_code, 204)
        self.assertEqual(
            self.client.get("/subscribers/a@x.com", headers=self._admin_headers()).status_code, 404
        )

    def test_delete_with_admin_password(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.delete("/subscribers/a@x.com", headers=self._admin_headers())
        self.assertEqual(res.status_code, 204)

    def test_delete_without_auth_returns_401(self):
        self.client.post("/subscribers", json=self._body())
        res = self.client.delete("/subscribers/a@x.com")
        self.assertEqual(res.status_code, 401)

    def test_delete_missing_with_admin_returns_404(self):
        res = self.client.delete("/subscribers/none@x.com", headers=self._admin_headers())
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
