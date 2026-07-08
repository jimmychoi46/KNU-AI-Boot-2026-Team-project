import smtplib
from config import TEST_SENDER, GOOGLE_APP_PASSWORD
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(to_email, subject, body_html):
    """
        Gmail SMTP를 사용하여 메일을 전송하는 함수.
        (만약, SendGrid API로 변경할 경우 smtplib가 아닌 Rest API 사용 예정)

        args:
            to_email(str): 메일 수신자
            subject(str): 메일 제목
            body_html(str): 메일 본문(정확히는 본문을 구성하는 html 코드) 
    """

    # 메시지 설정 (주제, 송신자, 수신자 등) 
    message = MIMEMultipart("alternative")
    message["Subject"] = subject # 주제 설정
    message["From"] = TEST_SENDER    # 송신자 설정
    message["To"] = to_email    # 수신자 설정
    message.attach(MIMEText(body_html, "html"))


    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(TEST_SENDER, GOOGLE_APP_PASSWORD)  # login 수행
        server.sendmail(TEST_SENDER, to_email, message.as_string()) # 메일 전송


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