import smtplib
from dotenv import load_dotenv
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import APScheduler


load_dotenv()

def send_email(to_email, subject, body_html):

    # 송신자, 비밀번호 설정
    sender = os.getenv("TEST_SENDER")
    app_password = os.getenv("GOOGLE_APP_PASSWORD")

    # 메시지 설정 (주제, 송신자, 수신자 등) 
    message = MIMEMultipart("alternative")
    message["Subject"] = subject # 주제 설정
    message["From"] = sender    # 송신자 설정
    message["To"] = to_email    # 수신자 설정
    message.attach(MIMEText(body_html, "html"))


    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)  # login 수행
        server.sendmail(sender, to_email, message.as_string()) # 메일 전송

def 

if __name__ == "__main__":
    body_html = """
    <html>
        <body style="font-family: sans-serif;">
            <h2>[테스트] 오늘의 금융 트렌드</h2>
            <p>이건 SMTP 발송 테스트용 더미 데이터입니다.</p>
            <ul>
                <li>테스트 종목 A: 임시 이슈 요약 1</li>
                <li>테스트 종목 B: 임시 이슈 요약 2</li>
            </ul>
        </body>
    </html>
    """
    send_email("jimmychoi46@gmail.com", "test", body_html)