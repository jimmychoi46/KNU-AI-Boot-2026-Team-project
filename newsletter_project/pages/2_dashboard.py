import streamlit as st
from utils import (
    load_subscribers,
    get_statistics,
    delete_subscriber,
    update_subscriber,
    generate_time_options,
    FREQUENCY_OPTIONS,
    SUMMARY_LENGTH_OPTIONS,
    LANGUAGE_OPTIONS,
)

st.title("🔐 관리자 대시보드")

admin_password = st.text_input("관리자 비밀번호를 입력하세요", type="password")

if admin_password != st.secrets["admin_password"]:
    st.warning("관리자만 접근할 수 있습니다.")
    st.stop()

st.success("관리자 인증 완료")

df, error = load_subscribers(admin_password)

if error:
    st.error(error)
    st.stop()

stats = get_statistics(df)

col1, col2, col3, col4 = st.columns(4)
col1.metric("전체 구독자 수", stats["total_subscribers"])
col2.metric("이메일 확인 완료", stats["confirmed_count"])
col3.metric("가장 많은 발송 주기", stats["most_common_frequency"])
col4.metric("가장 많은 언어", stats["most_common_language"])

st.subheader("구독자 목록")

if df.empty:
    st.info("아직 저장된 구독자가 없습니다.")
else:
    st.dataframe(df, use_container_width=True)

    st.subheader("구독자 수정")

    selected_email_for_edit = st.selectbox(
        "수정할 구독자의 이메일을 선택하세요",
        df["email"].tolist(),
        key="edit_email"
    )

    selected_row = df[df["email"] == selected_email_for_edit].iloc[0]

    send_time_options = generate_time_options()

    edit_name = st.text_input("이름 수정", value=selected_row["name"])
    edit_email = st.text_input("이메일 수정", value=selected_row["email"])
    edit_keywords = st.text_input("관심 키워드 수정", value=selected_row["keywords"])

    current_send_time = str(selected_row["send_time"])
    send_time_index = send_time_options.index(current_send_time) if current_send_time in send_time_options else 0

    edit_send_time = st.selectbox(
        "받는 시간 수정",
        send_time_options,
        index=send_time_index
    )

    current_frequency = str(selected_row["frequency"])
    frequency_index = FREQUENCY_OPTIONS.index(current_frequency) if current_frequency in FREQUENCY_OPTIONS else 0

    edit_frequency = st.selectbox(
        "발송 주기 수정",
        FREQUENCY_OPTIONS,
        index=frequency_index
    )

    current_summary_length = str(selected_row["summary_length"])
    summary_length_index = (
        SUMMARY_LENGTH_OPTIONS.index(current_summary_length)
        if current_summary_length in SUMMARY_LENGTH_OPTIONS else 0
    )

    edit_summary_length = st.selectbox(
        "요약 길이 수정",
        SUMMARY_LENGTH_OPTIONS,
        index=summary_length_index
    )

    current_language = str(selected_row["language"])
    language_index = LANGUAGE_OPTIONS.index(current_language) if current_language in LANGUAGE_OPTIONS else 0

    edit_language = st.selectbox(
        "언어 수정",
        LANGUAGE_OPTIONS,
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
                st.success("구독자 정보가 수정되었습니다.")
                st.rerun()
            else:
                st.error(error or "수정에 실패했습니다.")

    st.subheader("구독자 삭제")

    selected_email_for_delete = st.selectbox(
        "삭제할 구독자의 이메일을 선택하세요",
        df["email"].tolist(),
        key="delete_email"
    )

    if st.button("선택한 구독자 삭제"):
        success = delete_subscriber(selected_email_for_delete, admin_password=admin_password)

        if success:
            st.success(f"{selected_email_for_delete} 구독 정보가 삭제되었습니다.")
            st.rerun()
        else:
            st.error("삭제에 실패했습니다.")
