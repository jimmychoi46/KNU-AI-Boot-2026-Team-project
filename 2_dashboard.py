import streamlit as st
import pandas as pd
from pathlib import Path
from utils import load_common_css, generate_time_options

# 공통 CSS 적용
load_common_css()

# ---------------------------------------------------
# 파일 경로 및 인증
# ---------------------------------------------------
DATA_FILE = Path("subscribers.csv")
ADMIN_PASSWORD = st.secrets["admin_password"]

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
# 관리자 인증
# ---------------------------------------------------
if "dashboard_authenticated" not in st.session_state:
    st.session_state.dashboard_authenticated = False

if not st.session_state.dashboard_authenticated:
    st.markdown("""
    <div class="hero-box">
        <div class="page-title">📊 관리자 대시보드</div>
        <div class="page-desc">관리자 전용 페이지입니다. 비밀번호를 입력하세요.</div>
    </div>
    """, unsafe_allow_html=True)

    password_input = st.text_input("관리자 비밀번호", type="password")

    if st.button("로그인"):
        if password_input == ADMIN_PASSWORD:
            st.session_state.dashboard_authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ---------------------------------------------------
# 상단 Hero
# ---------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">📊 관리자 대시보드</div>
    <div class="page-desc">
        전체 구독자 현황을 파악하고,
        구독자 목록 조회 / 수정 / 삭제를 수행할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------
# 데이터 로드
# ---------------------------------------------------
df = load_data()

# ---------------------------------------------------
# 전체 현황
# ---------------------------------------------------
st.markdown('<div class="section-title">전체 현황</div>', unsafe_allow_html=True)

total   = len(df)
daily   = len(df[df["frequency"] == "매일"])   if not df.empty else 0
weekly3 = len(df[df["frequency"] == "주 3회"]) if not df.empty else 0
weekly  = len(df[df["frequency"] == "매주"])   if not df.empty else 0

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">전체 구독자</div>
        <div class="metric-value">{total}</div>
    </div>
    """, unsafe_allow_html=True)
with c2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">매일 발송</div>
        <div class="metric-value">{daily}</div>
    </div>
    """, unsafe_allow_html=True)
with c3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">주 3회</div>
        <div class="metric-value">{weekly3}</div>
    </div>
    """, unsafe_allow_html=True)
with c4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">매주</div>
        <div class="metric-value">{weekly}</div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------
# 언어별 통계
# ---------------------------------------------------
st.markdown('<div class="section-title">언어별 통계</div>', unsafe_allow_html=True)

korean  = len(df[df["language"] == "한국어"]) if not df.empty else 0
english = len(df[df["language"] == "English"]) if not df.empty else 0

lc1, lc2 = st.columns(2)
with lc1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">한국어</div>
        <div class="metric-value">{korean}</div>
    </div>
    """, unsafe_allow_html=True)
with lc2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">English</div>
        <div class="metric-value">{english}</div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------
# 구독자 목록
# ---------------------------------------------------
st.markdown('<div class="section-title">구독자 목록</div>', unsafe_allow_html=True)

if df.empty:
    st.warning("현재 등록된 구독자가 없습니다.")
else:
    st.dataframe(df, use_container_width=True)

# ---------------------------------------------------
# 구독자 정보 수정
# ---------------------------------------------------
st.markdown('<div class="section-title">구독자 정보 수정</div>', unsafe_allow_html=True)

if not df.empty:
    email_list = df["email"].tolist()

    st.markdown("""
    <div class="info-card">수정할 구독자 이메일 선택</div>
    """, unsafe_allow_html=True)

    selected_email = st.selectbox(
        "수정할 구독자 이메일 선택",
        email_list,
        label_visibility="collapsed"
    )

    selected_row = df[df["email"] == selected_email].iloc[0]

    send_time_options     = generate_time_options()
    frequency_options     = ["매일", "주 3회", "매주"]
    summary_length_options = ["짧게", "보통", "길게"]
    language_options      = ["한국어", "English"]

    col1, col2 = st.columns(2)

    with col1:
        edit_name     = st.text_input("이름", value=selected_row["name"],
                                      key="edit_name")
        edit_keywords = st.text_input("관심 키워드", value=selected_row["keywords"],
                                      key="edit_keywords")

    with col2:
        send_time_index = send_time_options.index(selected_row["send_time"]) \
            if selected_row["send_time"] in send_time_options else 0
        frequency_index = frequency_options.index(selected_row["frequency"]) \
            if selected_row["frequency"] in frequency_options else 0
        summary_index = summary_length_options.index(selected_row["summary_length"]) \
            if selected_row["summary_length"] in summary_length_options else 0
        language_index = language_options.index(selected_row["language"]) \
            if selected_row["language"] in language_options else 0

        edit_send_time = st.selectbox("받는 시간", send_time_options,
                                      index=send_time_index, key="edit_send_time")
        edit_frequency = st.selectbox("발송 주기", frequency_options,
                                      index=frequency_index, key="edit_frequency")
        edit_summary_length = st.selectbox("요약 길이", summary_length_options,
                                           index=summary_index, key="edit_summary")
        edit_language = st.selectbox("언어", language_options,
                                     index=language_index, key="edit_language")

    if st.button("수정 저장"):
        idx = df[df["email"] == selected_email].index[0]
        df.at[idx, "name"]           = edit_name
        df.at[idx, "keywords"]       = edit_keywords
        df.at[idx, "send_time"]      = edit_send_time
        df.at[idx, "frequency"]      = edit_frequency
        df.at[idx, "summary_length"] = edit_summary_length
        df.at[idx, "language"]       = edit_language
        save_data(df)
        st.success("구독자 정보가 수정되었습니다.")
        st.rerun()
else:
    st.info("수정할 구독자가 없습니다.")

# ---------------------------------------------------
# 구독자 삭제
# ---------------------------------------------------
st.markdown('<div class="section-title">구독자 삭제</div>', unsafe_allow_html=True)

if not df.empty:
    st.markdown("""
    <div class="info-card">삭제할 구독자 이메일 선택</div>
    """, unsafe_allow_html=True)

    delete_email = st.selectbox(
        "삭제할 구독자 이메일 선택",
        df["email"].tolist(),
        label_visibility="collapsed",
        key="delete_select"
    )

    if st.button("선택한 구독자 삭제"):
        df = df[df["email"] != delete_email]
        save_data(df)
        st.success("구독자가 삭제되었습니다.")
        st.rerun()
else:
    st.info("삭제할 구독자가 없습니다.")
