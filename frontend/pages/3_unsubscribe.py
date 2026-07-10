import streamlit as st
from utils import request_access_code, unsubscribe_subscriber, load_common_css

# 공통 CSS 적용
load_common_css()

# ---------------------------------------------------
# 상단 Hero
# ---------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">❌ 뉴스레터 구독 취소</div>
    <div class="page-desc">
        구독 취소를 원하는 이메일을 입력하고, 본인 확인 코드를 받아
        인증한 뒤 구독을 취소할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">구독 취소 요청</div>', unsafe_allow_html=True)

st.markdown("""
<div class="info-card">
    구독 시 사용한 이메일 주소를 입력하고 인증 코드를 받아주세요.
    본인 확인이 완료되면 구독이 취소됩니다.
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------
# 구독 취소 로직
# ---------------------------------------------------
email = st.text_input("이메일")

if email:
    if st.button("인증 코드 받기"):
        ok, error = request_access_code(email)
        if ok:
            st.success("이메일로 인증 코드를 보냈습니다. 아래에 입력해주세요.")
        else:
            st.error(error or "인증 코드 발송에 실패했습니다.")

    access_code = st.text_input("인증 코드")

    if st.button("구독 취소"):
        if not access_code:
            st.warning("인증 코드를 입력해 주세요.")
        else:
            success, error = unsubscribe_subscriber(email, access_code)

            if success:
                st.success(f"{email} 구독이 취소되었습니다.")
            elif error:
                # 서버 오류/연결 실패/429 등 — 코드 문제로 오인 안내하지 않는다.
                st.error(f"구독 취소에 실패했습니다: {error}")
            else:
                st.error("인증 코드가 올바르지 않거나 만료되었거나, 해당 이메일의 구독 정보를 찾을 수 없습니다.")

st.markdown("""
<div class="info-card">
    이후 다시 구독하고 싶다면 <b>subscribe</b> 페이지에서 새로 신청하면 됩니다.
</div>
""", unsafe_allow_html=True)
