"""notifiers.send_email 단위 테스트 (실제 발송 없이 동작).

SMTP_SSL 을 목(mock)으로 대체해 네트워크/메일 발송 없이
메일 구성 및 발송 호출 흐름만 검증한다.

실행:  python -m pytest   또는   python -m unittest
"""
import unittest
from unittest import mock

from src.notifiers import send_email


class TestSendEmail(unittest.TestCase):
    def test_send_email_logs_in_and_sends(self):
        with mock.patch("src.notifiers.send_email.smtplib.SMTP_SSL") as smtp:
            server = smtp.return_value.__enter__.return_value
            send_email.send_email("to@example.com", "제목", "<p>본문</p>")
            server.login.assert_called_once()
            server.sendmail.assert_called_once()

    def test_send_to_recipients_iterates(self):
        with mock.patch("src.notifiers.send_email.send_email") as one:
            send_email.send_to_recipients(["a@x.com", "b@x.com"], "제목", "<p>본문</p>")
            self.assertEqual(one.call_count, 2)


if __name__ == "__main__":
    unittest.main()
