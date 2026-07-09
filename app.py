import streamlit as st

st.set_page_config(
    page_title="AI Newsletter Bot",
    page_icon="📩",
    layout="wide"
)

st.title("📩 AI Newsletter Bot")
st.header("개인 맞춤형 AI 뉴스레터 구독 관리 시스템")

st.write(
    """
    이 프로젝트는 사용자가 뉴스레터 구독 정보를 입력하면 저장하고,
    관리자는 대시보드에서 구독자 현황을 확인할 수 있는
    Streamlit 기반 웹 애플리케이션입니다.
    """
)

st.subheader("사용 방법")
st.markdown(
    """
    - **subscribe**: 일반 사용자가 뉴스레터를 구독하는 페이지
    - **user_dashboard**: 일반 사용자가 본인 구독 정보를 수정하는 페이지
    - **unsubscribe**: 일반 사용자가 본인 이메일로 구독을 취소하는 페이지
    - **dashboard**: 관리자만 접근 가능한 페이지
    """
)

st.info("왼쪽 사이드바에서 원하는 페이지를 선택하세요.")
