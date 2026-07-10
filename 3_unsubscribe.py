import streamlit as st
import pandas as pd
from pathlib import Path
from utils import load_common_css

# 공통 CSS 적용
load_common_css()

# ---------------------------------------------------
# 파일 경로
# ---------------------------------------------------
DATA_FILE = Path("subscribers.csv")

# ---------------------------------------------------
# 공통 함수
# ---------------------------------------------------
def load_data():
    if DATA_FILE.exists():
        return pd.read_csv(DATA_FILE)
    return pd.DataFrame(columns=[
        "name", "email", "keywords",
        "send_time", "frequency", "summary_length", "language"
    ])

def save_data(df):
    df.to_csv(DATA_FILE, index=False)

# ---------------------------------------------------
# 상단 Hero
# ---------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">❌ 뉴스레터 구독 취소</div>
    <div class="page-desc">
        이메일을 입력하면 해당 이메일로 등록된 뉴스레터 구독 정보를 찾아
        구독을 취소할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">구독 취소 요청</div>', unsafe_allow_html=True)

st.markdown("""
<div class="info-card">
    구독 시 사용한 이메일 주소를 입력하세요.
    일치하는 구독 정보가 있으면 삭제됩니다.
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------
# 구독 취소 로직
# ---------------------------------------------------
email = st.text_input("이메일")

if st.button("구독 취소"):
    if not email:
        st.warning("이메일을 입력해 주세요.")
    else:
        df = load_data()
        if df.empty:
            st.warning("현재 저장된 구독 정보가 없습니다.")
        else:
            before_count = len(df)
            df = df[df["email"].astype(str).str.strip().str.lower()
                    != email.strip().lower()]
            after_count = len(df)

            if before_count == after_count:
                st.warning("해당 이메일로 등록된 구독 정보를 찾을 수 없습니다.")
            else:
                save_data(df)
                st.success("구독이 정상적으로 취소되었습니다.")

st.markdown("""
<div class="info-card">
    이후 다시 구독하고 싶다면 <b>subscribe</b> 페이지에서 새로 신청하면 됩니다.
</div>
""", unsafe_allow_html=True)
