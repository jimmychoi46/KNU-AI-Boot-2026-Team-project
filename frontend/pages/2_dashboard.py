import streamlit as st
from utils import (
    load_common_css,
    load_subscribers,
    get_statistics,
    delete_subscriber,
    update_subscriber,
    generate_time_options,
    get_options,
)

# 공통 CSS 적용
load_common_css()

# ---------------------------------------------------
# 상단 Hero / 관리자 인증
# ---------------------------------------------------
st.markdown("""
<div class="hero-box">
    <div class="page-title">📊 관리자 대시보드</div>
    <div class="page-desc">
        전체 구독자 현황을 파악하고,
        구독자 목록 조회 / 수정 / 삭제를 수행할 수 있습니다.
    </div>
</div>
""", unsafe_allow_html=True)

admin_password = st.text_input("관리자 비밀번호를 입력하세요", type="password")

if not admin_password:
    st.info("관리자 비밀번호를 입력하세요.")
    st.stop()

# 입력받은 비밀번호를 프론트 .streamlit/secrets.toml 의 admin_password 와 대조한다(관리자 게이트).
# secrets.toml 이 없거나 admin_password 가 비어 있으면 트레이스백으로 죽지 않고 안내 후 멈춘다.
try:
    _expected_admin_pw = st.secrets.get("admin_password")
except Exception:
    _expected_admin_pw = None
if not _expected_admin_pw:
    st.error("프론트 `.streamlit/secrets.toml` 에 `admin_password` 가 없습니다. "
             "`secrets.toml.example` 을 복사해 백엔드 `.env` 의 `ADMIN_PASSWORD` 와 같은 값으로 채워 주세요.")
    st.stop()
if admin_password != _expected_admin_pw:
    st.error("관리자 비밀번호가 올바르지 않습니다.")
    st.stop()

# 게이트를 통과하면 그 비밀번호로 백엔드 목록을 조회한다(같은 값을 X-Admin-Password 로 전달).
# secrets.toml 의 admin_password 는 백엔드 .env 의 ADMIN_PASSWORD 와 같아야 조회까지 성공한다.
# (캐시: 같은 비번으로 재실행하면 재조회하지 않아 반복 호출/속도 제한을 줄인다.)
if st.session_state.get("dashboard_admin_pw") != admin_password:
    _df, _error = load_subscribers(admin_password)
    st.session_state.dashboard_admin_pw = admin_password
    st.session_state.dashboard_df = _df
    st.session_state.dashboard_error = _error
df = st.session_state.get("dashboard_df")
error = st.session_state.get("dashboard_error")

if error:
    st.error(f"관리자 인증 실패 또는 서버 오류: {error}")
    st.stop()

st.success("관리자 인증 완료")

options = get_options()

stats = get_statistics(df)

# ---------------------------------------------------
# 전체 현황
# ---------------------------------------------------
st.markdown('<div class="section-title">전체 현황</div>', unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">전체 구독자 수</div>
        <div class="metric-value">{stats["total_subscribers"]}</div>
    </div>
    """, unsafe_allow_html=True)
with c2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">이메일 확인 완료</div>
        <div class="metric-value">{stats["confirmed_count"]}</div>
    </div>
    """, unsafe_allow_html=True)
with c3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">가장 많은 발송 주기</div>
        <div class="metric-value">{stats["most_common_frequency"]}</div>
    </div>
    """, unsafe_allow_html=True)
with c4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">가장 많은 언어</div>
        <div class="metric-value">{stats["most_common_language"]}</div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------
# 구독자 목록
# ---------------------------------------------------
st.markdown('<div class="section-title">구독자 목록</div>', unsafe_allow_html=True)

if df.empty:
    st.warning("아직 저장된 구독자가 없습니다.")
else:
    st.dataframe(df, use_container_width=True)

    # ---------------------------------------------------
    # 구독자 정보 수정
    # ---------------------------------------------------
    st.markdown('<div class="section-title">구독자 정보 수정</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-card">수정할 구독자 이메일 선택</div>
    """, unsafe_allow_html=True)

    selected_email_for_edit = st.selectbox(
        "수정할 구독자의 이메일을 선택하세요",
        df["email"].tolist(),
        label_visibility="collapsed",
        key="edit_email"
    )

    selected_row = df[df["email"] == selected_email_for_edit].iloc[0]

    send_time_options = generate_time_options()

    col1, col2 = st.columns(2)

    with col1:
        edit_name = st.text_input("이름 수정", value=selected_row["name"])
        edit_email = st.text_input("이메일 수정", value=selected_row["email"])
        edit_keywords = st.text_input("관심 키워드 수정", value=selected_row["keywords"])

    with col2:
        current_send_time = str(selected_row["send_time"])
        send_time_index = send_time_options.index(current_send_time) if current_send_time in send_time_options else 0

        edit_send_time = st.selectbox(
            "받는 시간 수정",
            send_time_options,
            index=send_time_index
        )

        current_frequency = str(selected_row["frequency"])
        frequency_index = options["frequency"].index(current_frequency) if current_frequency in options["frequency"] else 0

        edit_frequency = st.selectbox(
            "발송 주기 수정",
            options["frequency"],
            index=frequency_index
        )

        current_summary_length = str(selected_row["summary_length"])
        summary_length_index = (
            options["summary_length"].index(current_summary_length)
            if current_summary_length in options["summary_length"] else 0
        )

        edit_summary_length = st.selectbox(
            "요약 길이 수정",
            options["summary_length"],
            index=summary_length_index
        )

        current_language = str(selected_row["language"])
        language_index = options["language"].index(current_language) if current_language in options["language"] else 0

        edit_language = st.selectbox(
            "언어 수정",
            options["language"],
            index=language_index
        )

    if st.button("수정 저장"):
        if not edit_name or not edit_email or not edit_keywords:
            st.warning("이름, 이메일, 관심 키워드를 모두 입력해 주세요.")
        else:
            success, error = update_subscriber(
                old_email=selected_email_for_edit,
                name=edit_name,
                new_email=edit_email,
                keywords=edit_keywords,
                send_time=edit_send_time,
                frequency=edit_frequency,
                summary_length=edit_summary_length,
                language=edit_language,
                admin_password=admin_password,
            )

            if success:
                st.session_state.pop("dashboard_admin_pw", None)  # 목록 캐시 무효화 → 재조회
                st.success("구독자 정보가 수정되었습니다.")
                st.rerun()
            else:
                st.error(error or "수정에 실패했습니다.")

    # ---------------------------------------------------
    # 구독자 삭제
    # ---------------------------------------------------
    st.markdown('<div class="section-title">구독자 삭제</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-card">삭제할 구독자 이메일 선택</div>
    """, unsafe_allow_html=True)

    selected_email_for_delete = st.selectbox(
        "삭제할 구독자의 이메일을 선택하세요",
        df["email"].tolist(),
        label_visibility="collapsed",
        key="delete_email"
    )

    if st.button("선택한 구독자 삭제"):
        success, derr = delete_subscriber(selected_email_for_delete, admin_password=admin_password)

        if success:
            st.session_state.pop("dashboard_admin_pw", None)  # 목록 캐시 무효화 → 재조회
            st.success(f"{selected_email_for_delete} 구독 정보가 삭제되었습니다.")
            st.rerun()
        elif derr:
            st.error(f"삭제에 실패했습니다: {derr}")
        else:
            st.error("삭제에 실패했습니다 (권한 없음 또는 없는 구독자).")
