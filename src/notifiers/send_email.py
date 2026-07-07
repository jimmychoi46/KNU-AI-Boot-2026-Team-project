"""Gmail SMTP 메일 발송기.

담당: B (백엔드) — 이메일 발송 서버.
기존 email_test.py 의 로직을 모듈화한 것.
(추후 SendGrid 등 REST API 방식으로 교체할 경우 이 모듈만 바꾸면 된다.)
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import EMAIL_SENDER, GOOGLE_APP_PASSWORD


def send_email(to_email, subject, body_html):
    """Gmail SMTP 를 사용하여 HTML 메일 한 통을 전송.

    args:
        to_email(str): 수신자
        subject(str): 제목
        body_html(str): 본문 HTML
    """
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_SENDER
    message["To"] = to_email
    message.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, GOOGLE_APP_PASSWORD)
        server.sendmail(EMAIL_SENDER, to_email, message.as_string())


def send_to_recipients(recipients, subject, body_html):
    """수신자 목록 전체에 동일한 메일을 발송."""
    for to_email in recipients:
        send_email(to_email, subject, body_html)
        print(f"[발송 완료] {to_email}")
