import streamlit as st
from utils import request_access_code, unsubscribe_subscriber

st.title("❌ 뉴스레터 구독 취소")

st.write("구독 취소를 원하는 이메일을 입력하고, 본인 확인 코드를 받아 인증한 뒤 취소해주세요.")

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
            success = unsubscribe_subscriber(email, access_code)

            if success:
                st.success(f"{email} 구독이 취소되었습니다.")
            else:
                st.error("인증 코드가 올바르지 않거나 만료되었거나, 해당 이메일의 구독 정보를 찾을 수 없습니다.")
