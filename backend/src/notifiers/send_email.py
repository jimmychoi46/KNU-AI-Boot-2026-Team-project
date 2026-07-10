import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import EMAIL_SENDER, GOOGLE_APP_PASSWORD, SMTP_TIMEOUT


def send_email(to_email, subject, body_html):
    """Gmail SMTP 를 사용하여 HTML 형식으로 메일을 전송하는 함수.

    args:
        to_email(str): 수신자
        subject(str): 제목
        body_html(str): 본문 (정확히는 본문을 구성하는 HTML)
    """
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_SENDER
    message["To"] = to_email
    message.attach(MIMEText(body_html, "html"))

    # timeout 없으면 서버 무응답 시 소켓이 영원히 블록 — 예외가 아니라 '행'이라 호출부
    # try/except 로도 못 막는다. SMTP_TIMEOUT 을 줘서 무응답을 예외(TimeoutError)로 전환한다.
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=SMTP_TIMEOUT) as server:
        server.login(EMAIL_SENDER, GOOGLE_APP_PASSWORD)
        server.sendmail(EMAIL_SENDER, to_email, message.as_string())


def send_to_recipients(recipients, subject, body_html):
    """수신자 목록 전체에 동일한 메일을 발송. 한 수신자 실패가 나머지 발송을 막지 않도록 격리한다.

    returns: (성공 수, 실패 수).
    """
    sent = failed = 0
    for recipient in recipients:
        try:
            send_email(recipient, subject, body_html)
        except Exception as exc:  # 한 명 실패(형식 오류·수신 거부 등)가 뒤 수신자 발송을 막지 않게 격리
            failed += 1
            print(f"[발송 실패] {recipient}: {exc}")
        else:
            sent += 1
            print(f"[발송 완료] {recipient}")
    return sent, failed
