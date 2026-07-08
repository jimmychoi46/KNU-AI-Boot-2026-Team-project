import streamlit as st
from utils import unsubscribe_subscriber

st.title("❌ 뉴스레터 구독 취소")

st.write("구독 취소를 원하는 이메일을 입력해 주세요.")

email = st.text_input("이메일")

if st.button("구독 취소"):
    if not email:
        st.warning("이메일을 입력해 주세요.")
    else:
        success = unsubscribe_subscriber(email)

        if success:
            st.success(f"{email} 구독이 취소되었습니다.")
        else:
            st.error("해당 이메일의 구독 정보를 찾을 수 없습니다.")
