import streamlit as st
import pandas as pd
from pathlib import Path
from utils import load_common_css, generate_time_options

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
    <div class="page-title">👤 유저 대시보드</div>
    <div class="page-desc">
        이메일을 입력해 본인의 구독 정보를 조회하고,
        관심 키워드와 뉴스레터 옵션을 수정할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">구독 정보 조회</div>', unsafe_allow_html=True)

st.markdown("""
<div class="info-card">
    구독 시 사용한 이메일 주소를 입력한 뒤 조회 버튼을 눌러 주세요.
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------
# 조회 로직
# ---------------------------------------------------
search_email = st.text_input("이메일 입력")

if st.button("조회"):
    st.session_state["search_email"] = search_email

if "search_email" in st.session_state and st.session_state["search_email"]:
    df = load_data()

    if df.empty:
        st.warning("저장된 구독 정보가 없습니다.")
    else:
        email_key = st.session_state["search_email"].strip().lower()
        matched = df[df["email"].astype(str).str.strip().str.lower() == email_key]

        if matched.empty:
            st.warning("해당 이메일의 구독 정보를 찾을 수 없습니다.")
        else:
            row = matched.iloc[0]

            st.markdown('<div class="section-title">현재 구독 정보</div>',
                        unsafe_allow_html=True)

            m1, m2, m3 = st.columns(3)
            with m1:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">이름</div>
                    <div class="metric-value">{row['name']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m2:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">발송 주기</div>
                    <div class="metric-value">{row['frequency']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m3:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">언어</div>
                    <div class="metric-value">{row['language']}</div>
                </div>
                """, unsafe_allow_html=True)

            # ---------------------------------------------------
            # 수정 폼
            # ---------------------------------------------------
            st.markdown('<div class="section-title">구독 정보 수정</div>',
                        unsafe_allow_html=True)

            send_time_options      = generate_time_options()
            frequency_options      = ["매일", "주 3회", "매주"]
            summary_length_options = ["짧게", "보통", "길게"]
            language_options       = ["한국어", "English"]

            col1, col2 = st.columns(2)

            with col1:
                new_name     = st.text_input("이름", value=row["name"],
                                             key="user_name")
                new_keywords = st.text_input("관심 키워드", value=row["keywords"],
                                             key="user_keywords")

            with col2:
                send_time_index = send_time_options.index(row["send_time"]) \
                    if row["send_time"] in send_time_options else 0
                frequency_index = frequency_options.index(row["frequency"]) \
                    if row["frequency"] in frequency_options else 0
                summary_index = summary_length_options.index(row["summary_length"]) \
                    if row["summary_length"] in summary_length_options else 0
                language_index = language_options.index(row["language"]) \
                    if row["language"] in language_options else 0

                new_send_time = st.selectbox("받는 시간", send_time_options,
                                             index=send_time_index,
                                             key="user_send_time")
                new_frequency = st.selectbox("발송 주기", frequency_options,
                                             index=frequency_index,
                                             key="user_frequency")
                new_summary_length = st.selectbox("요약 길이", summary_length_options,
                                                  index=summary_index,
                                                  key="user_summary")
                new_language = st.selectbox("언어", language_options,
                                            index=language_index,
                                            key="user_language")

            if st.button("수정 저장", key="user_save"):
                target_idx = matched.index[0]
                df.at[target_idx, "name"]           = new_name
                df.at[target_idx, "keywords"]       = new_keywords
                df.at[target_idx, "send_time"]      = new_send_time
                df.at[target_idx, "frequency"]      = new_frequency
                df.at[target_idx, "summary_length"] = new_summary_length
                df.at[target_idx, "language"]       = new_language
                save_data(df)
                st.success("구독 정보가 수정되었습니다.")
                st.rerun()

st.markdown("""
<div class="info-card">
    이후 다시 구독하고 싶다면 <b>subscribe</b> 페이지에서 새로 신청하면 됩니다.
</div>
""", unsafe_allow_html=True)
