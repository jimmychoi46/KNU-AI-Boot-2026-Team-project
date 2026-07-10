import streamlit as st
from utils import save_subscriber, generate_time_options, get_options, load_common_css

# 공통 CSS 적용
load_common_css()

# 선택지는 백엔드 GET /options 에서 받아온다(하드코딩 드리프트 방지, 실패 시 폴백).
options = get_options()

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

    frequency = st.selectbox("발송 주기", options["frequency"])
    summary_length = st.selectbox("요약 길이", options["summary_length"])
    language = st.selectbox("언어", options["language"])

# ---------------------------------------------------
# 구독 신청 버튼
# ---------------------------------------------------
if st.button("구독 신청"):
    if not name or not email or not keywords:
        st.warning("이름, 이메일, 관심 키워드를 모두 입력해 주세요.")
    else:
        success, error = save_subscriber(
            name=name,
            email=email,
            keywords=keywords,
            send_time=send_time,
            frequency=frequency,
            summary_length=summary_length,
            language=language
        )

        if success:
            st.success("구독 신청이 접수되었습니다! 입력하신 이메일로 발송된 확인 메일의 링크를 눌러야 뉴스레터를 받을 수 있습니다.")
        else:
            st.error(error or "구독 신청에 실패했습니다.")

st.markdown("""
<div class="info-card">
    입력한 이메일을 기준으로 이후 <b>user dashboard</b> 페이지에서
    구독 정보를 조회하거나 수정할 수 있습니다.
</div>
""", unsafe_allow_html=True)
