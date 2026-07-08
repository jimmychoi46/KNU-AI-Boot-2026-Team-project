import streamlit as st
from utils import save_subscriber, generate_time_options

st.title("📝 뉴스레터 구독 신청")

name = st.text_input("이름")
email = st.text_input("이메일")
keywords = st.text_input("관심 키워드 (예: AI, Python, 스타트업)")

send_time_options = generate_time_options()
frequency_options = ["매일", "주 3회", "매주"]
summary_length_options = ["짧게", "중간", "길게"]
language_options = ["한국어", "영어"]

send_time = st.selectbox("받는 시간", send_time_options)
frequency = st.selectbox("발송 주기", frequency_options)
summary_length = st.selectbox("요약 길이", summary_length_options)
language = st.selectbox("언어", language_options)

if st.button("구독 신청"):
    if not name or not email or not keywords:
        st.warning("이름, 이메일, 관심 키워드를 모두 입력해 주세요.")
    else:
        success, message = save_subscriber(
            name=name,
            email=email,
            keywords=keywords,
            send_time=send_time,
            frequency=frequency,
            summary_length=summary_length,
            language=language
        )
        if success:
            st.success("확인 메일을 보냈습니다. 메일함에서 링크를 눌러야 구독이 시작됩니다.")
        else:
            st.error(message)
