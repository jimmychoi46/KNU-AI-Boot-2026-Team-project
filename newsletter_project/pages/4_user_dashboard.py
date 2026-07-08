import streamlit as st
from utils import (
    get_subscriber_by_email,
    update_subscriber,
    generate_time_options,
    FREQUENCY_OPTIONS,
    SUMMARY_LENGTH_OPTIONS,
    LANGUAGE_OPTIONS,
)

# --------------------------------------------------
# 유저 대시보드 페이지 제목
# 일반 사용자가 자신의 구독 정보를 확인하고 수정하는 페이지
# --------------------------------------------------
st.title("👤 유저 대시보드")
st.write("구독 신청에 사용한 이메일을 입력하면 내 구독 정보를 수정할 수 있습니다.")

# --------------------------------------------------
# 사용자가 자신의 이메일을 입력하는 칸
# --------------------------------------------------
if "user_dashboard_email" not in st.session_state:
    st.session_state.user_dashboard_email = ""

# 위젯이 이번 실행에서 이미 인스턴스화된 뒤에는 그 key를 못 바꾸므로,
# 저장 성공 시엔 pending 값만 남겨두고 다음 실행 맨 앞(위젯 생성 전)에서 반영한다.
if "user_dashboard_pending_email" in st.session_state:
    st.session_state.user_dashboard_email = st.session_state.pop("user_dashboard_pending_email")

user_email = st.text_input("내 이메일을 입력하세요", key="user_dashboard_email")

# --------------------------------------------------
# 이메일이 입력된 경우에만 아래 기능 실행
# --------------------------------------------------
if user_email:
    # 입력한 이메일로 구독자 정보 1건 조회
    subscriber = get_subscriber_by_email(user_email)

    # --------------------------------------------------
    # 해당 이메일의 구독 정보가 없는 경우
    # --------------------------------------------------
    if subscriber is None:
        st.error("해당 이메일의 구독 정보를 찾을 수 없습니다.")

    # --------------------------------------------------
    # 해당 이메일의 구독 정보가 있는 경우
    # --------------------------------------------------
    else:
        st.success("구독 정보를 찾았습니다. 아래에서 수정할 수 있습니다.")
        if not subscriber["confirmed"]:
            st.info("아직 이메일 확인 전입니다. 가입 시 받은 확인 메일의 링크를 눌러야 뉴스레터가 발송됩니다.")

        # 시간 선택지 생성
        send_time_options = generate_time_options()

        # --------------------------------------------------
        # 기존 값을 기본값으로 넣어서 수정 입력창 생성
        # --------------------------------------------------
        edit_name = st.text_input("이름 수정", value=subscriber["name"])
        edit_keywords = st.text_input("관심 키워드 수정", value=subscriber["keywords"])

        # 이메일은 본인 확인 기준이므로 기본값으로 보여주되 수정 가능하게 둘 수도 있고,
        # 수정 불가능하게 둘 수도 있다.
        # 여기서는 수정 가능하게 구성 (바꾸면 새 이메일로 재가입되어 다시 확인 메일을 받는다)
        edit_email = st.text_input("이메일 수정", value=subscriber["email"])
        if edit_email != user_email:
            st.caption("이메일을 바꾸면 새 이메일로 다시 가입 처리되어, 그 주소로 확인 메일이 재발송됩니다.")

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
            index=send_time_index
        )

        # 현재 발송 주기 기본 선택값
        current_frequency = str(subscriber["frequency"])
        frequency_index = (
            FREQUENCY_OPTIONS.index(current_frequency)
            if current_frequency in FREQUENCY_OPTIONS else 0
        )

        edit_frequency = st.selectbox(
            "발송 주기 수정",
            FREQUENCY_OPTIONS,
            index=frequency_index
        )

        # 현재 요약 길이 기본 선택값
        current_summary_length = str(subscriber["summary_length"])
        summary_length_index = (
            SUMMARY_LENGTH_OPTIONS.index(current_summary_length)
            if current_summary_length in SUMMARY_LENGTH_OPTIONS else 0
        )

        edit_summary_length = st.selectbox(
            "요약 길이 수정",
            SUMMARY_LENGTH_OPTIONS,
            index=summary_length_index
        )

        # 현재 언어 기본 선택값
        current_language = str(subscriber["language"])
        language_index = (
            LANGUAGE_OPTIONS.index(current_language)
            if current_language in LANGUAGE_OPTIONS else 0
        )

        edit_language = st.selectbox(
            "언어 수정",
            LANGUAGE_OPTIONS,
            index=language_index
        )

        # --------------------------------------------------
        # 저장 버튼
        # --------------------------------------------------
        if st.button("내 정보 수정 저장"):
            # 필수 입력값 검사
            if not edit_name or not edit_email or not edit_keywords:
                st.warning("이름, 이메일, 관심 키워드를 모두 입력해 주세요.")
            else:
                # 기존 이메일(user_email)을 기준으로 수정
                success, error = update_subscriber(
                    old_email=user_email,
                    name=edit_name,
                    new_email=edit_email,
                    keywords=edit_keywords,
                    send_time=edit_send_time,
                    frequency=edit_frequency,
                    summary_length=edit_summary_length,
                    language=edit_language
                )

                if success:
                    st.session_state.user_dashboard_pending_email = edit_email
                    st.success("내 구독 정보가 수정되었습니다.")
                    st.rerun()
                else:
                    st.error(error or "수정에 실패했습니다.")
