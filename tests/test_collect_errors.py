"""collectors.naver_news 요청 실패 내성 테스트 (네트워크 없이 동작).

재시도 소진/비정상 응답 시에도 예외로 죽지 않고 안전하게 반환하는지 확인한다.

실행:  python -m pytest   또는   python -m unittest
"""
import unittest
from unittest import mock

import requests

from src.collectors import naver_news


class TestRequestResilience(unittest.TestCase):
    def test_network_error_returns_none_status(self):
        with mock.patch.object(naver_news._session, "get",
                               side_effect=requests.ConnectionError("boom")):
            status, payload = naver_news.get_naver_news("주식")
        self.assertIsNone(status)
        self.assertEqual(payload, {})

    def test_non_json_response_returns_empty_payload(self):
        fake = mock.Mock()
        fake.status_code = 500
        fake.json.side_effect = ValueError("not json")
        with mock.patch.object(naver_news._session, "get", return_value=fake):
            status, payload = naver_news.get_naver_news("주식")
        self.assertEqual(status, 500)
        self.assertEqual(payload, {})

    def test_collect_survives_failure(self):
        # 요청이 완전히 실패해도 collect 는 예외 없이 빈 리스트를 준다(스케줄러 tick 보호)
        with mock.patch.object(naver_news, "get_naver_news", return_value=(None, {})):
            result = naver_news.collect(["주식"])
        self.assertEqual(result, {"주식": []})


if __name__ == "__main__":
    unittest.main()
