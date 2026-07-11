"""프론트 utils 동적 스모크 (가짜 백엔드로 requests 를 몽키패치).

실서버 없이 utils 함수의 계약을 값 수준으로 검증한다:
URL 인코딩 / 서버오류↔코드오류 구분(튜플) / 대소문자 정규화 / 부분실패 안내 / 429 / 옵션 캐시.

실행:  (frontend 에서)  python -m pytest tests/test_utils_smoke.py -v
"""
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # utils.py 임포트
import utils


class FakeResp:
    def __init__(self, status_code, body=None, raise_on_json=False):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self._raise = raise_on_json

    def json(self):
        if self._raise:
            raise ValueError("본문이 JSON 이 아님")
        return self._body


class UtilsSmokeBase(unittest.TestCase):
    def setUp(self):
        self.calls = []           # (method, path, headers, json) 기록
        self._handler = None      # 각 테스트가 지정하는 응답 라우터
        utils._OPTIONS_CACHE = None  # 옵션 캐시 초기화(테스트 격리)

        def fake_request(method, url, timeout=None, headers=None, json=None, **kw):
            path = url[len(utils.API_BASE_URL):] if url.startswith(utils.API_BASE_URL) else url
            self.calls.append((method, path, headers or {}, json))
            return self._handler(method, path, headers or {}, json)

        p = mock.patch.object(utils.requests, "request", side_effect=fake_request)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: setattr(utils, "_OPTIONS_CACHE", None))

    def paths(self, method=None):
        return [c[1] for c in self.calls if method is None or c[0] == method]


class TestUtilsSmoke(UtilsSmokeBase):
    # [utils-epath-01] 특수문자 이메일이 경로에서 퍼센트 인코딩된다
    def test_epath_encodes_special_chars(self):
        self.assertEqual(utils._epath("a+b#c@ex.com"), "a%2Bb%23c%40ex.com")
        self._handler = lambda m, p, h, j: FakeResp(202)
        ok, err = utils.request_access_code("a+b#c@ex.com")
        self.assertEqual((ok, err), (True, None))
        url_path = self.paths("POST")[0]
        self.assertEqual(url_path, "/subscribers/a%2Bb%23c%40ex.com/access-code")
        seg = url_path.split("/subscribers/")[1].split("/access-code")[0]
        for raw in ("+", "#", "@"):
            self.assertNotIn(raw, seg)

    # [utils-getsub-codeerr-02] 401/403/404 는 (None, None) 코드오류
    def test_get_subscriber_code_errors_are_none_none(self):
        for code in (401, 403, 404):
            self._handler = lambda m, p, h, j, code=code: FakeResp(code)  # 루프 변수 바인딩(B023)
            self.assertEqual(utils.get_subscriber_by_email("a@x.com", "CODE"), (None, None))

    # [utils-getsub-srverr-03] 서버오류/연결실패는 (None, 메시지)
    def test_get_subscriber_server_errors_carry_message(self):
        self._handler = lambda m, p, h, j: FakeResp(500, {"detail": "서버 점검 중"})
        self.assertEqual(utils.get_subscriber_by_email("a@x.com", "CODE"), (None, "서버 점검 중"))

        def boom(m, p, h, j):
            raise requests.ConnectionError("refused")
        self._handler = boom
        sub, err = utils.get_subscriber_by_email("a@x.com", "CODE")
        self.assertIsNone(sub)
        self.assertTrue(err and err.startswith("서버에 연결할 수 없습니다"))

    # [utils-delete-tuple-04] 204→성공, 404→코드오류, 500→서버오류 메시지
    def test_delete_three_way(self):
        self._handler = lambda m, p, h, j: FakeResp(204)
        self.assertEqual(utils.delete_subscriber("a@x.com", access_code="C"), (True, None))
        self._handler = lambda m, p, h, j: FakeResp(404)
        self.assertEqual(utils.delete_subscriber("a@x.com", access_code="C"), (False, None))
        self._handler = lambda m, p, h, j: FakeResp(500, {"detail": "boom"})
        self.assertEqual(utils.delete_subscriber("a@x.com", access_code="C"), (False, "boom"))

    # [utils-update-caseonly-05] 대소문자만 다른 변경은 PUT 1회만(재가입 아님)
    def test_update_case_only_is_single_put(self):
        self._handler = lambda m, p, h, j: FakeResp(200)
        ok, err = utils.update_subscriber(
            old_email="User@Example.com", name="n", new_email="user@example.com",
            keywords="a", send_time="08:00", frequency="매일", summary_length="짧게",
            language="한국어", access_code="CODE")
        self.assertEqual((ok, err), (True, None))
        methods = [c[0] for c in self.calls]
        self.assertEqual(methods, ["PUT"])  # POST/DELETE 없음
        self.assertEqual(self.calls[0][1], "/subscribers/User%40Example.com")
        self.assertEqual(self.calls[0][2].get("X-Access-Code"), "CODE")

    # [utils-update-emailchange-06] 실제 이메일 변경도 PUT 1회 (재가입+삭제는 백엔드가 원자 처리)
    def test_update_real_change_single_put(self):
        self._handler = lambda m, p, hd, j: FakeResp(200)
        ok, err = utils.update_subscriber(
            old_email="old@x.com", name="n", new_email="new@x.com", keywords="a",
            send_time="08:00", frequency="매일", summary_length="짧게", language="한국어",
            access_code="CODE")
        self.assertEqual((ok, err), (True, None))
        methods = [c[0] for c in self.calls]
        self.assertEqual(methods, ["PUT"])  # POST/DELETE 없음 — 단일 PUT
        self.assertEqual(self.calls[0][1], "/subscribers/old%40x.com")  # old_email 경로
        self.assertEqual(self.calls[0][3]["email"], "new@x.com")        # 본문에 새 이메일
        self.assertEqual(self.calls[0][2].get("X-Access-Code"), "CODE")

    # [utils-update-error-07] PUT 비정상 응답을 성공으로 위장하지 않고 백엔드 메시지를 그대로 노출
    def test_update_error_not_masked(self):
        # A: 409(대상이 이미 사용 중) → 실패 + 메시지 그대로, PUT 1회뿐
        self._handler = lambda m, p, h, j: FakeResp(409, {"detail": "이미 사용 중인 이메일입니다: new@x.com"})
        ok, err = utils.update_subscriber(
            old_email="old@x.com", name="n", new_email="new@x.com", keywords="a",
            send_time="08:00", frequency="매일", summary_length="짧게", language="한국어",
            access_code="CODE")
        self.assertEqual((ok, err), (False, "이미 사용 중인 이메일입니다: new@x.com"))
        self.assertEqual([c[0] for c in self.calls], ["PUT"])
        # B: 500(서버 오류) → 실패로 위장 없이 원인 메시지 노출
        self.calls.clear()
        self._handler = lambda m, p, h, j: FakeResp(500, {"detail": "db lock"})
        ok, err = utils.update_subscriber(
            old_email="old@x.com", name="n", new_email="new@x.com", keywords="a",
            send_time="08:00", frequency="매일", summary_length="짧게", language="한국어",
            access_code="CODE")
        self.assertEqual((ok, err), (False, "db lock"))

    # [utils-429-errbody-08] 429 {'error':...} 본문을 그대로 노출
    def test_429_error_body_surfaced(self):
        self._handler = lambda m, p, h, j: FakeResp(429, {"error": "Rate limit exceeded: 10 per 1 minute"})
        self.assertEqual(utils.get_subscriber_by_email("a@x.com", "CODE"),
                         (None, "Rate limit exceeded: 10 per 1 minute"))

    # [utils-429-default-09] 본문 파싱 불가한 429는 개선된 기본 메시지
    def test_429_unparseable_default_message(self):
        self._handler = lambda m, p, h, j: FakeResp(429, raise_on_json=True)
        self.assertEqual(utils.delete_subscriber("a@x.com", access_code="C"),
                         (False, "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요."))

    # [utils-options-backend-10] 백엔드 /options 값을 그대로 수신(하드코딩 드리프트 제거)
    def test_options_from_backend(self):
        backend = {"frequency": ["매일", "격주"], "summary_length": ["S", "M", "L"], "language": ["KO", "EN"]}
        self._handler = lambda m, p, h, j: FakeResp(200, backend)
        self.assertEqual(utils.get_options(), backend)
        self.assertEqual(self.paths("GET"), ["/options"])

    # [utils-options-fallback-11] 백엔드 불가/부분응답 시 상수 폴백
    def test_options_fallback(self):
        full = {"frequency": ["매일", "주 3회", "매주"], "summary_length": ["짧게", "중간", "길게"], "language": ["한국어", "영어"]}
        # A: 연결 실패
        def boom(m, p, h, j):
            raise requests.ConnectionError("down")
        self._handler = boom
        self.assertEqual(utils.get_options(), full)
        # B: 500
        utils._OPTIONS_CACHE = None
        self._handler = lambda m, p, h, j: FakeResp(500)
        self.assertEqual(utils.get_options(), full)
        # C: 부분 응답(frequency 만)
        utils._OPTIONS_CACHE = None
        self._handler = lambda m, p, h, j: FakeResp(200, {"frequency": ["A"]})
        opts = utils.get_options()
        self.assertEqual(opts["frequency"], ["A"])
        self.assertEqual(opts["summary_length"], full["summary_length"])
        self.assertEqual(opts["language"], full["language"])

    # [utils-options-cache-12] 프로세스당 1회만 백엔드 호출, 이후 캐시
    def test_options_cached_single_call(self):
        state = {"n": 0}

        def h(m, p, hd, j):
            state["n"] += 1
            return FakeResp(200, {"frequency": [f"V{state['n']}"], "summary_length": ["s"], "language": ["l"]})
        self._handler = h
        r1, r2, r3 = utils.get_options(), utils.get_options(), utils.get_options()
        self.assertEqual(state["n"], 1)  # /options 1회만
        self.assertEqual(r1["frequency"], ["V1"])
        self.assertEqual(r2["frequency"], ["V1"])  # 캐시된 첫 값
        self.assertEqual(r3["frequency"], ["V1"])


if __name__ == "__main__":
    unittest.main()
