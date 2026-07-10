import pandas as pd
import os
import streamlit as st

# 구독자 정보를 저장할 CSV 파일 경로
FILE_PATH = "subscribers.csv"


def load_common_css():
    """
    모든 페이지에서 공통으로 사용할 CSS를 주입하는 함수
    각 페이지 상단에서 반드시 호출해야 함
    """
    st.markdown("""
    <style>
    /* =========================================================
       공통 디자인 토큰 (단일 소스)
       모든 폰트 크기 / 색상은 아래 변수로 통일한다.
       ========================================================= */
    :root {
        --fs-hero-title: 40px;   /* 페이지 대제목 */
        --fs-hero-sub:   20px;   /* 히어로 보조 문구 */
        --fs-section:    26px;   /* 섹션 제목 */
        --fs-card-title: 18px;   /* 카드 제목 */
        --fs-body:       15px;   /* 본문 / 설명 */
        --fs-label:      15px;   /* 위젯 라벨 */
        --fs-metric-t:   14px;   /* 지표 라벨 */
        --fs-metric-v:   22px;   /* 지표 값 */
        --fs-nav:        18px;   /* 사이드바 네비게이션 */

        --card-h: 160px;         /* 주요 기능 카드 고정 높이 */

        --c-heading: #0F172A;
        --c-body:    #334155;
        --c-accent:  #1D4ED8;
        --c-accent2: #3B82F6;
        --c-border:  #BAE6FD;
    }

    /* 앱 전역 본문 기본 크기 통일 */
    html, body, .stApp, [data-testid="stAppViewContainer"] {
        font-size: var(--fs-body);
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        padding-left: 3rem;
        padding-right: 3rem;
    }

    /* =========================================================
       사이드바 네비게이션
       ========================================================= */
    section[data-testid="stSidebar"] {
        width: 320px !important;
        min-width: 320px !important;
        max-width: 320px !important;
    }

    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.2rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }

    [data-testid="stSidebarNav"] ul {
        gap: 10px !important;
    }

    [data-testid="stSidebarNav"] li {
        margin-bottom: 10px !important;
    }

    [data-testid="stSidebarNav"] a {
        display: flex !important;
        align-items: center !important;
        min-height: 56px !important;
        padding: 14px 16px !important;
        border-radius: 14px !important;
        text-decoration: none !important;
    }

    [data-testid="stSidebarNav"] a span {
        font-size: var(--fs-nav) !important;
        font-weight: 700 !important;
        line-height: 1.4 !important;
        color: var(--c-heading) !important;
    }

    [data-testid="stSidebarNav"] a:hover {
        background-color: #DCEEFF !important;
    }

    [data-testid="stSidebarNav"] a:hover span {
        font-size: var(--fs-nav) !important;
        font-weight: 700 !important;
        color: var(--c-heading) !important;
    }

    [data-testid="stSidebarNav"] a[aria-current="page"] {
        background-color: #A9D0F5 !important;
        border-radius: 14px !important;
    }

    [data-testid="stSidebarNav"] a[aria-current="page"] span {
        font-size: var(--fs-nav) !important;
        font-weight: 800 !important;
        color: var(--c-heading) !important;
    }

    /* =========================================================
       히어로 / 제목
       ========================================================= */
    .hero-box {
        background: linear-gradient(135deg, #E0F2FE 0%, #F0F9FF 100%);
        padding: 28px;
        border-radius: 22px;
        margin-bottom: 28px;
        border: 1px solid var(--c-border);
    }

    .page-title {
        font-size: var(--fs-hero-title);
        font-weight: 800;
        color: var(--c-heading);
        margin-bottom: 8px;
    }

    .hero-sub {
        font-size: var(--fs-hero-sub);
        font-weight: 700;
        color: var(--c-accent);
        margin-bottom: 10px;
    }

    .page-desc {
        font-size: var(--fs-body);
        color: var(--c-body);
        line-height: 1.7;
    }

    .section-title {
        font-size: var(--fs-section);
        font-weight: 800;
        color: var(--c-heading);
        margin-top: 10px;
        margin-bottom: 16px;
    }

    /* =========================================================
       카드류
       ========================================================= */
    .info-card {
        background-color: #FFFFFF;
        border: 1px solid var(--c-border);
        border-radius: 18px;
        padding: 20px;
        box-shadow: 0 6px 18px rgba(59, 130, 246, 0.10);
        margin-bottom: 20px;
        font-size: var(--fs-body);
        color: var(--c-body);
        line-height: 1.7;
    }

    .feature-card {
        background-color: #FFFFFF;
        border: 1px solid var(--c-border);
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
        margin-bottom: 16px;
        min-height: 100px;
    }

    .feature-title {
        font-size: var(--fs-card-title);
        font-weight: 700;
        color: var(--c-accent);
        margin-bottom: 8px;
    }

    .feature-desc {
        font-size: var(--fs-body);
        color: var(--c-body);
        line-height: 1.6;
    }

    .step-card {
        background-color: #FFFFFF;
        border: 1px solid var(--c-border);
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
        min-height: 100px;
    }

    .step-label {
        font-size: var(--fs-card-title);
        font-weight: 700;
        color: var(--c-accent2);
        margin-bottom: 8px;
    }

    .metric-card {
        background-color: #FFFFFF;
        border: 1px solid var(--c-border);
        border-radius: 16px;
        padding: 16px 18px;
        margin-bottom: 16px;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
    }

    .metric-title {
        font-size: var(--fs-metric-t);
        color: #64748B;
        margin-bottom: 6px;
        font-weight: 500;
    }

    .metric-value {
        font-size: var(--fs-metric-v);
        font-weight: 700;
        color: var(--c-accent);
    }

    /* =========================================================
       클릭 가능한 feature 카드 (st.page_link 기반)
       사이드바와 동일하게 Streamlit 내부 라우팅을 사용하므로
       전체 페이지 리로드가 발생하지 않는다.
       ========================================================= */
    /* 카드 컨테이너 + 내부 툴팁/문단 래퍼까지 전부 100% 폭 강제 */
    [data-testid="stPageLink"] {
        margin-bottom: 16px;
        width: 100% !important;
    }

    [data-testid="stPageLink"] > div,
    [data-testid="stPageLink"] > div > div,
    [data-testid="stPageLink"] p {
        width: 100% !important;
        box-sizing: border-box !important;
    }

    [data-testid="stPageLink"] a[data-testid="stPageLink-NavLink"] {
        display: flex !important;
        flex-direction: column !important;
        box-sizing: border-box !important;
        background-color: #FFFFFF;
        border: 1px solid var(--c-border);
        border-radius: 18px;
        padding: 22px 24px !important;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
        width: 100% !important;
        height: var(--card-h) !important;
        min-height: var(--card-h) !important;
        max-height: var(--card-h) !important;
        align-items: flex-start !important;
        justify-content: flex-start !important;
        overflow: hidden;
        transition: box-shadow 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
    }

    [data-testid="stPageLink"] a[data-testid="stPageLink-NavLink"]:hover {
        box-shadow: 0 8px 24px rgba(59, 130, 246, 0.20);
        transform: translateY(-2px);
        border-color: var(--c-accent2);
    }

    [data-testid="stPageLink"] a[data-testid="stPageLink-NavLink"] p {
        font-size: var(--fs-body);
        color: var(--c-body);
        line-height: 1.6;
    }

    /* 카드 제목(첫 강조 문구) 스타일 */
    [data-testid="stPageLink"] a[data-testid="stPageLink-NavLink"] p strong {
        display: block;
        font-size: var(--fs-card-title);
        font-weight: 700;
        color: var(--c-accent);
        margin-bottom: 6px;
    }

    /* =========================================================
       네이티브 위젯 (라벨 / 버튼 / 알림) 폰트 통일
       ========================================================= */
    [data-testid="stWidgetLabel"] p,
    label[data-testid="stWidgetLabel"] p {
        font-size: var(--fs-label) !important;
        font-weight: 600 !important;
        color: var(--c-heading) !important;
    }

    /* 입력창 / 셀렉트박스 내부 텍스트 */
    .stTextInput input,
    .stSelectbox div[data-baseweb="select"] {
        font-size: var(--fs-body) !important;
    }

    div.stButton > button {
        background-color: var(--c-accent2);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 10px 18px;
        font-weight: 600;
        font-size: var(--fs-body);
    }

    div.stButton > button:hover {
        background-color: #2563EB;
        color: white;
    }

    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    div[data-testid="stAlert"] p {
        font-size: var(--fs-body) !important;
    }
    </style>
    """, unsafe_allow_html=True)


def generate_time_options():
    """
    00:00부터 23:00까지 1시간 단위 시간 목록 생성
    """
    return [f"{hour:02d}:00" for hour in range(24)]


def load_subscribers():
    """
    CSV 파일에서 구독자 정보를 불러오는 함수
    파일이 없으면 빈 데이터프레임 반환
    """
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame(columns=[
            "name", "email", "keywords",
            "send_time", "frequency",
            "summary_length", "language"
        ])
    return pd.read_csv(FILE_PATH)


def save_subscriber(name, email, keywords, send_time,
                    frequency, summary_length, language):
    """
    새 구독자 정보를 저장하는 함수
    """
    df = load_subscribers()
    new_subscriber = {
        "name": name,
        "email": email,
        "keywords": keywords,
        "send_time": send_time,
        "frequency": frequency,
        "summary_length": summary_length,
        "language": language
    }
    df = pd.concat([df, pd.DataFrame([new_subscriber])], ignore_index=True)
    df.to_csv(FILE_PATH, index=False)


def get_statistics():
    """
    구독자 통계 계산
    """
    df = load_subscribers()
    if df.empty:
        return {
            "total_subscribers": 0,
            "most_common_frequency": "없음",
            "most_common_language": "없음"
        }
    return {
        "total_subscribers": len(df),
        "most_common_frequency": df["frequency"].mode()[0],
        "most_common_language": df["language"].mode()[0]
    }


def delete_subscriber(email):
    """
    이메일 기준 구독자 삭제
    """
    df = load_subscribers()
    if df.empty:
        return False
    if email not in df["email"].values:
        return False
    df = df[df["email"] != email]
    df.to_csv(FILE_PATH, index=False)
    return True


def unsubscribe_subscriber(email):
    """
    일반 사용자가 자기 이메일로 구독 취소
    """
    return delete_subscriber(email)


def update_subscriber(old_email, name, new_email, keywords,
                      send_time, frequency, summary_length, language):
    """
    기존 이메일(old_email)을 기준으로 구독자 정보 수정
    """
    df = load_subscribers()
    if df.empty:
        return False
    if old_email not in df["email"].values:
        return False
    row_index = df[df["email"] == old_email].index[0]
    df.at[row_index, "name"]           = name
    df.at[row_index, "email"]          = new_email
    df.at[row_index, "keywords"]       = keywords
    df.at[row_index, "send_time"]      = send_time
    df.at[row_index, "frequency"]      = frequency
    df.at[row_index, "summary_length"] = summary_length
    df.at[row_index, "language"]       = language
    df.to_csv(FILE_PATH, index=False)
    return True


def get_subscriber_by_email(email):
    """
    이메일을 기준으로 특정 구독자 1명의 정보를 가져오는 함수
    """
    df = load_subscribers()
    if df.empty:
        return None
    matched_rows = df[df["email"] == email]
    if matched_rows.empty:
        return None
    return matched_rows.iloc[0]
