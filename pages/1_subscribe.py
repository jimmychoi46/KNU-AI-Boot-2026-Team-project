import streamlit as st
from utils import (
    save_subscriber,
    generate_time_options,
    FREQUENCY_OPTIONS,
    SUMMARY_LENGTH_OPTIONS,
    LANGUAGE_OPTIONS,
)

st.title("📝 뉴스레터 구독 신청")

name = st.text_input("이름")
email = st.text_input("이메일")
keywords = st.text_input("관심 키워드 (예: AI, Python, 스타트업)")

send_time_options = generate_time_options()

send_time = st.selectbox("받는 시간", send_time_options)
frequency = st.selectbox("발송 주기", FREQUENCY_OPTIONS)
summary_length = st.selectbox("요약 길이", SUMMARY_LENGTH_OPTIONS)
language = st.selectbox("언어", LANGUAGE_OPTIONS)

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
