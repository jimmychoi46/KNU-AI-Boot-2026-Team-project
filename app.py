import streamlit as st
from utils import load_common_css

st.set_page_config(
    page_title="AI Newsletter Bot",
    page_icon="📨",
    layout="wide"
)


# ---------------------------------------------------
# 홈 페이지 콘텐츠
# ---------------------------------------------------
def home():
    load_common_css()

    st.markdown("""
    <div class="hero-box">
        <div class="page-title">📨 AI Newsletter Bot</div>
        <div class="hero-sub">
            개인 맞춤형 AI 뉴스레터 구독 관리 시스템
        </div>
        <div class="page-desc">
            사용자가 관심 키워드와 뉴스레터 옵션을 선택해 구독하고,
            관리자와 사용자 대시보드에서 구독 정보를 관리할 수 있는
            Streamlit 기반 웹 애플리케이션입니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-title">주요 기능</div>', unsafe_allow_html=True)

    # st.page_link 를 사용해 사이드바와 동일한 내부 라우팅으로 이동한다.
    # (전체 페이지 리로드 없이 사이드바 클릭과 동일하게 동작)
    col1, col2 = st.columns(2)

    with col1:
        st.page_link(
            "pages/1_subscribe.py",
            label="**📝 구독 신청**  \n"
                  "일반 사용자가 이름, 이메일, 관심 키워드, 발송 시간,\n\n "
                  "발송 주기, 요약 길이, 언어를 선택해 뉴스레터를 구독할 수 있습니다.",
            use_container_width=True,
        )
        st.page_link(
            "pages/3_unsubscribe.py",
            label="**❌ 구독 취소**  \n"
                  "사용자가 본인의 이메일을 입력해 기존 뉴스레터 구독을 취소할 수 있습니다.",
            use_container_width=True,
        )

    with col2:
        st.page_link(
            "pages/4_user_dashboard.py",
            label="**👤 유저 대시보드**  \n"
                  "일반 사용자가 자신의 이메일을 기준으로 구독 정보를 조회하고 수정할 수 있습니다.",
            use_container_width=True,
        )
        st.page_link(
            "pages/2_dashboard.py",
            label="**📊 관리자 대시보드**  \n"
                  "관리자가 전체 구독자 목록을 확인하고, 통계를 조회하며, "
                  "구독자 수정 및 삭제 기능을 수행할 수 있습니다.",
            use_container_width=True,
        )

    st.markdown('<div class="section-title">사용 흐름</div>', unsafe_allow_html=True)

    s1, s2, s3 = st.columns(3)

    with s1:
        st.markdown("""
        <div class="step-card">
            <div class="step-label">1단계</div>
            <div class="feature-desc">
                <b>구독 신청</b> 페이지에서<br>
                뉴스레터 구독 정보를 입력합니다.
            </div>
        </div>
        """, unsafe_allow_html=True)

    with s2:
        st.markdown("""
        <div class="step-card">
            <div class="step-label">2단계</div>
            <div class="feature-desc">
                <b>사용자 모드</b> 페이지에서<br>
                본인의 구독 정보를 수정할 수 있습니다.
            </div>
        </div>
        """, unsafe_allow_html=True)

    with s3:
        st.markdown("""
        <div class="step-card">
            <div class="step-label">3단계</div>
            <div class="feature-desc">
                필요 시 <b>구독 취소</b>에서 취소하고,<br>
                관리자는 <b>관리자 모드</b>에서 전체를 관리합니다.
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.info("왼쪽 사이드바 또는 메인 카드 영역을 클릭해 원하는 페이지로 이동하세요.")


# ---------------------------------------------------
# 사이드바 네비게이션 (라벨을 직접 지정)
# ---------------------------------------------------
pages = [
    st.Page(home, title="홈", icon="🏠", default=True),
    st.Page("pages/1_subscribe.py", title="구독 신청", icon="📝"),
    st.Page("pages/2_dashboard.py", title="관리자 모드", icon="📊"),
    st.Page("pages/3_unsubscribe.py", title="구독 취소", icon="❌"),
    st.Page("pages/4_user_dashboard.py", title="사용자 모드", icon="👤"),
]

nav = st.navigation(pages)
nav.run()
