import streamlit as st
from utils import (
    load_common_css,
    request_access_code,
    get_subscriber_by_email,
    update_subscriber,
    generate_time_options,
    get_options,
)

# 공통 CSS 적용
load_common_css()

# --------------------------------------------------
# 유저 대시보드 페이지
# 일반 사용자가 본인 확인 코드로 인증한 뒤 자신의 구독 정보를 확인/수정하는 페이지
# --------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">👤 유저 대시보드</div>
    <div class="page-desc">
        구독 신청에 사용한 이메일을 입력하고 인증 코드를 받아 본인 확인을 하면,
        내 구독 정보를 확인하고 관심 키워드와 뉴스레터 옵션을 수정할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">본인 확인</div>', unsafe_allow_html=True)

st.markdown("""
<div class="info-card">
    구독 시 사용한 이메일 주소를 입력한 뒤 인증 코드를 받아 본인 확인을 진행해 주세요.
</div>
""", unsafe_allow_html=True)

# --------------------------------------------------
# 위젯이 이번 실행에서 이미 인스턴스화된 뒤에는 그 key를 못 바꾸므로,
# 이메일이 바뀌는 저장 성공 시엔 pending 값만 남겨두고 다음 실행 맨 앞
# (아래 위젯들 생성 전)에서 반영한다. 이메일이 바뀌면 인증 코드도 새로
# 받아야 하므로 코드 입력값도 함께 비운다.
# --------------------------------------------------
if "user_dashboard_email" not in st.session_state:
    st.session_state.user_dashboard_email = ""

if "user_dashboard_pending_email" in st.session_state:
    st.session_state.user_dashboard_email = st.session_state.pop("user_dashboard_pending_email")
    st.session_state.user_dashboard_code = ""

# --------------------------------------------------
# 사용자가 자신의 이메일을 입력하는 칸
# --------------------------------------------------
user_email = st.text_input("내 이메일을 입력하세요", key="user_dashboard_email")

# --------------------------------------------------
# 이메일이 입력된 경우에만 아래 기능 실행
# --------------------------------------------------
if user_email:
    if st.button("인증 코드 받기"):
        ok, error = request_access_code(user_email)
        if ok:
            st.success("이메일로 인증 코드를 보냈습니다. 아래에 입력해주세요.")
        else:
            st.error(error or "인증 코드 발송에 실패했습니다.")

    access_code = st.text_input("인증 코드", key="user_dashboard_code")

    # --------------------------------------------------
    # 인증 코드까지 입력된 경우에만 조회/수정 진행
    # --------------------------------------------------
    if access_code:
        # (이메일, 코드) 조합당 한 번만 조회해 세션에 캐시한다 — 편집 위젯을 건드릴 때마다
        # GET /subscribers/{email}(10/분 제한)을 재호출해 429로 화면이 깨지는 것을 막는다.
        fetch_key = (user_email, access_code)
        if st.session_state.get("user_dashboard_fetch_key") != fetch_key:
            _sub, _err = get_subscriber_by_email(user_email, access_code)
            st.session_state.user_dashboard_fetch_key = fetch_key
            st.session_state.user_dashboard_sub = _sub
            st.session_state.user_dashboard_fetch_err = _err
        subscriber = st.session_state.get("user_dashboard_sub")
        fetch_err = st.session_state.get("user_dashboard_fetch_err")

        # --------------------------------------------------
        # 서버 오류(다운/429 등) vs 코드 오류(틀림/만료/없음)를 구분해 안내
        # --------------------------------------------------
        if fetch_err:
            st.error(f"조회에 실패했습니다: {fetch_err}")
        elif subscriber is None:
            st.error("인증 코드가 올바르지 않거나 만료되었습니다. 코드를 다시 요청해주세요.")

        # --------------------------------------------------
        # 인증에 성공한 경우
        # --------------------------------------------------
        else:
            st.success("본인 확인이 완료되었습니다. 아래에서 수정할 수 있습니다.")
            if not subscriber["confirmed"]:
                st.info("아직 이메일 확인 전입니다. 가입 시 받은 확인 메일의 링크를 눌러야 뉴스레터가 발송됩니다.")

            # --------------------------------------------------
            # 현재 구독 정보 요약 카드
            # --------------------------------------------------
            st.markdown('<div class="section-title">현재 구독 정보</div>', unsafe_allow_html=True)

            m1, m2, m3 = st.columns(3)
            with m1:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">이름</div>
                    <div class="metric-value">{subscriber['name']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m2:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">발송 주기</div>
                    <div class="metric-value">{subscriber['frequency']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m3:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">언어</div>
                    <div class="metric-value">{subscriber['language']}</div>
                </div>
                """, unsafe_allow_html=True)

            # --------------------------------------------------
            # 수정 폼
            # --------------------------------------------------
            st.markdown('<div class="section-title">구독 정보 수정</div>', unsafe_allow_html=True)

            # 시간·주기·요약길이·언어 선택지 (백엔드 GET /options, 실패 시 폴백)
            send_time_options = generate_time_options()
            options = get_options()

            col1, col2 = st.columns(2)

            with col1:
                # --------------------------------------------------
                # 기존 값을 기본값으로 넣어서 수정 입력창 생성
                # --------------------------------------------------
                edit_name = st.text_input("이름 수정", value=subscriber["name"], key="user_name")
                edit_keywords = st.text_input("관심 키워드 수정", value=subscriber["keywords"], key="user_keywords")

                # 이메일은 본인 확인 기준이므로 기본값으로 보여주되 수정 가능하게 둔다.
                edit_email = st.text_input("이메일 수정", value=subscriber["email"], key="user_email_edit")
                if edit_email != user_email:
                    st.caption(
                        "이메일을 바꾸면 새 이메일로 다시 가입 처리되어, 그 주소로 확인 메일이 "
                        "재발송되고 인증 코드도 새로 받아야 합니다."
                    )

            with col2:
                # --------------------------------------------------
                # 현재 저장된 시간을 시간 목록에서 찾아 기본 선택값으로 설정
                # --------------------------------------------------
                current_send_time = str(subscriber["send_time"])
                send_time_index = (
                    send_time_options.index(current_send_time)
                    if current_send_time in send_time_options else 0
                )
                edit_send_time = st.selectbox(
                    "받는 시간 수정",
                    send_time_options,
                    index=send_time_index,
                    key="user_send_time"
                )

                # 현재 발송 주기 기본 선택값
                current_frequency = str(subscriber["frequency"])
                frequency_index = (
                    options["frequency"].index(current_frequency)
                    if current_frequency in options["frequency"] else 0
                )
                edit_frequency = st.selectbox(
                    "발송 주기 수정",
                    options["frequency"],
                    index=frequency_index,
                    key="user_frequency"
                )

                # 현재 요약 길이 기본 선택값
                current_summary_length = str(subscriber["summary_length"])
                summary_length_index = (
                    options["summary_length"].index(current_summary_length)
                    if current_summary_length in options["summary_length"] else 0
                )
                edit_summary_length = st.selectbox(
                    "요약 길이 수정",
                    options["summary_length"],
                    index=summary_length_index,
                    key="user_summary"
                )

                # 현재 언어 기본 선택값
                current_language = str(subscriber["language"])
                language_index = (
                    options["language"].index(current_language)
                    if current_language in options["language"] else 0
                )
                edit_language = st.selectbox(
                    "언어 수정",
                    options["language"],
                    index=language_index,
                    key="user_language"
                )

            # --------------------------------------------------
            # 저장 버튼
            # --------------------------------------------------
            if st.button("내 정보 수정 저장", key="user_save"):
                # 필수 입력값 검사
                if not edit_name or not edit_email or not edit_keywords:
                    st.warning("이름, 이메일, 관심 키워드를 모두 입력해 주세요.")
                else:
                    # 기존 이메일(user_email)을 기준으로 수정, 본인 확인 코드로 인증
                    success, error = update_subscriber(
                        old_email=user_email,
                        name=edit_name,
                        new_email=edit_email,
                        keywords=edit_keywords,
                        send_time=edit_send_time,
                        frequency=edit_frequency,
                        summary_length=edit_summary_length,
                        language=edit_language,
                        access_code=access_code,
                    )

                    if success:
                        # 캐시를 비워 다음 실행에서 갱신된 값을 다시 조회하게 한다.
                        st.session_state.pop("user_dashboard_fetch_key", None)
                        if edit_email != user_email:
                            st.session_state.user_dashboard_pending_email = edit_email
                        st.success("내 구독 정보가 수정되었습니다.")
                        st.rerun()
                    else:
                        st.error(error or "수정에 실패했습니다.")

st.markdown("""
<div class="info-card">
    새로 구독하고 싶다면 <b>구독 신청</b> 페이지에서 언제든 다시 신청할 수 있습니다.
</div>
""", unsafe_allow_html=True)
