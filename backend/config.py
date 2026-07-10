from dotenv import load_dotenv
import os

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
SMTP_SENDER = os.getenv("SENDER")
SMTP_PASSWORD = os.getenv("GOOGLE_APP_PASSWORD")
