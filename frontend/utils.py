import os
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# 백엔드 GET /options 조회 실패 시의 폴백 기본값. 정상 경로에서는 get_options()가
# 백엔드 config 값을 그대로 받아와 셀렉트박스를 채운다(하드코딩 드리프트 방지).
FREQUENCY_OPTIONS = ["매일", "주 3회", "매주"]
SUMMARY_LENGTH_OPTIONS = ["짧게", "중간", "길게"]
LANGUAGE_OPTIONS = ["한국어", "영어"]

_OPTIONS_CACHE = None


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


def _epath(email):
    """이메일을 URL 경로에 안전하게 넣기 위한 인코딩.

    quote(safe='')가 없으면 '#','?','%' 등이 프래그먼트/쿼리/퍼센트로 해석돼, 백엔드 정규식이
    허용하는 특수문자 이메일이 등록은 되지만 조회/수정/삭제/코드요청에서 잘려 404가 된다.
    """
    return quote(str(email), safe="")


def get_options():
    """백엔드 GET /options 로 frequency/summary_length/language 선택지를 받는다(프로세스 1회 캐시).

    백엔드 config 가 선택지를 바꾸면 프론트도 자동으로 따라가되(하드코딩 드리프트 제거),
    백엔드가 잠깐 불가해도 폼은 뜨도록 실패 시 상단 상수로 폴백한다.
    """
    global _OPTIONS_CACHE
    if _OPTIONS_CACHE is None:
        fallback = {
            "frequency": FREQUENCY_OPTIONS,
            "summary_length": SUMMARY_LENGTH_OPTIONS,
            "language": LANGUAGE_OPTIONS,
        }
        try:
            resp = _request("GET", "/options")
            data = resp.json() if resp.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            data = {}
        _OPTIONS_CACHE = {k: (data.get(k) or fallback[k]) for k in fallback}
    return _OPTIONS_CACHE


def generate_time_options():
    """
    00:00부터 24:00까지 30분 단위 시간 목록 생성
    """
    times = []

    for hour in range(24):
        times.append(f"{hour:02d}:00")
        times.append(f"{hour:02d}:30")

    times.append("24:00")
    return times


def _split_keywords(keywords_str):
    """'AI, Python, 스타트업' 같은 콤마 구분 문자열 -> 리스트."""
    return [k.strip() for k in str(keywords_str).split(",") if k.strip()]


def _join_keywords(keywords_list):
    """리스트 -> 콤마 구분 문자열 (입력 필드/표 표시용)."""
    return ", ".join(keywords_list or [])


def _parse_send_time(send_time):
    """'HH:MM' -> (hour, minute). '24:00'은 hour=24, minute=0."""
    hour_str, minute_str = str(send_time).split(":")
    return int(hour_str), int(minute_str)


def _format_send_time(hour, minute):
    return f"{hour:02d}:{minute:02d}"


def _api_error_message(response):
    """API 에러 응답에서 메시지를 뽑아낸다. 없으면 상태코드 기반 기본 메시지.

    FastAPI 는 {"detail": ...}, slowapi 의 429 는 {"error": ...} 형태라 둘 다 확인한다.
    """
    try:
        body = response.json()
        detail = body.get("detail") or body.get("error")
    except (ValueError, AttributeError):
        detail = None
    if response.status_code == 429:
        return detail or "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요."
    return detail or f"요청이 실패했습니다 (status={response.status_code})"


def _request(method, path, **kwargs):
    url = f"{API_BASE_URL}{path}"
    return requests.request(method, url, timeout=10, **kwargs)


def _auth_headers(admin_password=None, access_code=None):
    """관리자 비밀번호/본인 확인 코드 중 있는 것만 헤더로 실어 보낸다."""
    headers = {}
    if admin_password:
        headers["X-Admin-Password"] = admin_password
    if access_code:
        headers["X-Access-Code"] = access_code
    return headers


def _subscriber_to_dict(sub):
    """API 응답(SubscriberOut) -> 프론트에서 쓰기 편한 dict (send_time 문자열 포함)."""
    return {
        "name": sub["name"],
        "email": sub["email"],
        "keywords": _join_keywords(sub["keywords"]),
        "send_time": _format_send_time(sub["send_hour"], sub["send_minute"]),
        "frequency": sub["frequency"],
        "summary_length": sub["summary_length"],
        "language": sub["language"],
        "confirmed": sub["confirmed"],
    }


def load_subscribers(admin_password):
    """
    관리자 전용 전체 구독자 목록 조회.

    반환값: (DataFrame, 에러메시지) 튜플. 성공 시 에러메시지는 None,
    실패(인증 실패 등) 시 DataFrame은 None.
    """
    try:
        resp = _request("GET", "/subscribers", headers={"X-Admin-Password": admin_password})
    except requests.RequestException as exc:
        return None, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code != 200:
        return None, _api_error_message(resp)

    rows = [_subscriber_to_dict(s) for s in resp.json()]
    columns = ["name", "email", "keywords", "send_time", "frequency", "summary_length", "language", "confirmed"]
    return pd.DataFrame(rows, columns=columns), None


def get_statistics(df):
    """
    구독자 통계 계산 (대시보드에서 불러온 DataFrame 기준).
    """
    if df is None or df.empty:
        return {
            "total_subscribers": 0,
            "confirmed_count": 0,
            "most_common_frequency": "없음",
            "most_common_language": "없음",
        }

    return {
        "total_subscribers": len(df),
        "confirmed_count": int(df["confirmed"].sum()),
        "most_common_frequency": df["frequency"].mode()[0],
        "most_common_language": df["language"].mode()[0],
    }


def load_statistics(admin_password):
    """백엔드 GET /subscribers/stats 로 통계를 받는다(관리자 전용). (dict, error|None) 반환.

    프론트에서 목록을 세지 않고 백엔드 집계를 그대로 쓰는 경로. React 등 다른 프론트도
    같은 계산을 다시 구현하지 않고 이 엔드포인트를 쓰면 된다. (Streamlit 대시보드는 이미
    목록을 받아 get_statistics(df)로 계산하므로, 이 함수는 그 목록 없이 통계만 필요할 때용.)
    """
    try:
        resp = _request("GET", "/subscribers/stats", headers={"X-Admin-Password": admin_password})
    except requests.RequestException as exc:
        return None, f"서버에 연결할 수 없습니다: {exc}"
    if resp.status_code != 200:
        return None, _api_error_message(resp)
    return resp.json(), None


def save_subscriber(name, email, keywords, send_time, frequency, summary_length, language):
    """
    새 구독 신청. 성공/실패와 메시지를 (bool, str|None) 로 반환한다.
    (백엔드가 confirmed=False로 저장하고 확인 메일을 보내므로, 성공해도 즉시 발송 대상은 아니다.)
    """
    hour, minute = _parse_send_time(send_time)
    payload = {
        "email": email,
        "name": name,
        "keywords": _split_keywords(keywords),
        "send_hour": hour,
        "send_minute": minute,
        "frequency": frequency,
        "summary_length": summary_length,
        "language": language,
    }
    try:
        resp = _request("POST", "/subscribers", json=payload)
    except requests.RequestException as exc:
        return False, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code not in (200, 201):
        return False, _api_error_message(resp)
    return True, None


def request_access_code(email):
    """본인 확인 코드를 이메일로 요청한다. (bool, 에러메시지) 반환."""
    try:
        resp = _request("POST", f"/subscribers/{_epath(email)}/access-code")
    except requests.RequestException as exc:
        return False, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code != 202:
        return False, _api_error_message(resp)
    return True, None


def get_subscriber_by_email(email, access_code):
    """
    이메일 + 본인 확인 코드로 구독자 1명의 정보를 가져오는 함수.

    매개변수:
        email (str): 찾고 싶은 구독자의 이메일
        access_code (str): request_access_code() 로 발급받은 본인 확인 코드

    반환값: (subscriber|None, error|None) 튜플.
        - 조회 성공: (dict, None)
        - 코드 오류/만료/없음(401/403/404): (None, None) → 호출부가 '코드 오류'로 안내
        - 서버 오류/연결 실패/429 등: (None, 에러메시지) → 호출부가 그 원인을 안내
      '코드 오류'와 '서버 오류'를 구분해야, 백엔드가 다운/429일 때 정상 코드를 가진 사용자에게
      '코드가 틀렸다'고 오인 안내하지 않는다.
    """
    try:
        resp = _request("GET", f"/subscribers/{_epath(email)}",
                        headers=_auth_headers(access_code=access_code))
    except requests.RequestException as exc:
        return None, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code == 200:
        return _subscriber_to_dict(resp.json()), None
    if resp.status_code in (401, 403, 404):
        return None, None
    return None, _api_error_message(resp)


def delete_subscriber(email, admin_password=None, access_code=None):
    """
    이메일 기준 구독자 삭제. 관리자(admin_password) 또는 본인(access_code) 인증 필요.

    반환값: (bool, error|None). 성공(204)=(True, None), 인증실패/없음(401/403/404)=(False, None),
    서버오류/연결실패/429=(False, 에러메시지). 원인을 구분해 잘못된 안내를 막는다.
    """
    try:
        resp = _request(
            "DELETE", f"/subscribers/{_epath(email)}",
            headers=_auth_headers(admin_password, access_code),
        )
    except requests.RequestException as exc:
        return False, f"서버에 연결할 수 없습니다: {exc}"
    if resp.status_code == 204:
        return True, None
    if resp.status_code in (401, 403, 404):
        return False, None
    return False, _api_error_message(resp)


def unsubscribe_subscriber(email, access_code):
    """
    일반 사용자가 본인 확인 코드로 자기 이메일 구독을 취소. (bool, error|None) 반환.
    """
    return delete_subscriber(email, access_code=access_code)


def update_subscriber(
    old_email, name, new_email, keywords, send_time, frequency, summary_length, language,
    admin_password=None, access_code=None,
):
    """
    기존 이메일(old_email)을 기준으로 구독자 정보를 수정한다. 관리자 또는 본인 인증 필요.

    이메일 변경(new_email != old_email)도 백엔드 PUT 이 처리한다 — 새 주소는 미확인
    상태로 시작해 확인 메일을 새로 받는다(소유권이 바뀌므로). 프론트는 새 이메일을 본문에
    실어 PUT 한 번만 보내면 된다(예전의 'POST 재가입 + DELETE 삭제' 우회 로직은 백엔드로 이동).

    반환값: (bool, str|None) 성공 여부와 실패 시 에러 메시지.
    """
    hour, minute = _parse_send_time(send_time)
    payload = {
        "email": new_email,  # old_email 과 다르면 백엔드가 이메일 변경으로 처리(같거나 생략이면 제자리 수정)
        "name": name,
        "keywords": _split_keywords(keywords),
        "send_hour": hour,
        "send_minute": minute,
        "frequency": frequency,
        "summary_length": summary_length,
        "language": language,
    }
    try:
        resp = _request(
            "PUT", f"/subscribers/{_epath(old_email)}", json=payload,
            headers=_auth_headers(admin_password, access_code),
        )
    except requests.RequestException as exc:
        return False, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code != 200:
        return False, _api_error_message(resp)
    return True, None
