import streamlit as st
from utils import load_subscribers, save_subscriber, generate_time_options, load_common_css

# 공통 CSS 적용
load_common_css()

# ---------------------------------------------------
# 상단 Hero
# ---------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">📝 뉴스레터 구독 신청</div>
    <div class="page-desc">
        관심 있는 키워드와 뉴스레터 옵션을 입력하세요.
        발송 시간, 발송 주기, 요약 길이, 언어를 직접 선택할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">구독 정보 입력</div>', unsafe_allow_html=True)

st.markdown("""
<div class="info-card">
    아래 항목을 입력하면 뉴스레터 구독 정보가 저장됩니다.
    필수 입력 항목은 이름, 이메일, 관심 키워드입니다.
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------
# 입력 폼
# ---------------------------------------------------
col1, col2 = st.columns(2)

with col1:
    name = st.text_input("이름")
    email = st.text_input("이메일")
    keywords = st.text_input("관심 키워드 (예: AI, Python, 스타트업)")

with col2:
    send_time_options = generate_time_options()
    send_time = st.selectbox("받는 시간", send_time_options)

    frequency_options = ["매일", "주 3회", "매주"]
    frequency = st.selectbox("발송 주기", frequency_options)

    summary_length_options = ["짧게", "보통", "길게"]
    summary_length = st.selectbox("요약 길이", summary_length_options)

    language_options = ["한국어", "English"]
    language = st.selectbox("언어", language_options)

# ---------------------------------------------------
# 구독 신청 버튼
# ---------------------------------------------------
if st.button("구독 신청"):
    if not name or not email or not keywords:
        st.warning("이름, 이메일, 관심 키워드는 필수 입력 항목입니다.")
    else:
        existing = load_subscribers()
        if not existing.empty and email in existing["email"].values:
            st.warning("이미 등록된 이메일입니다. user dashboard에서 정보를 수정하세요.")
        else:
            save_subscriber(name, email, keywords, send_time,
                            frequency, summary_length, language)
            st.success(f"{name}님의 구독 신청이 완료되었습니다!")

st.markdown("""
<div class="info-card">
    입력한 이메일을 기준으로 이후 <b>user dashboard</b> 페이지에서
    구독 정보를 조회하거나 수정할 수 있습니다.
</div>
""", unsafe_allow_html=True)
