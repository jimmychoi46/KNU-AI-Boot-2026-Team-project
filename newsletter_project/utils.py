import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

FREQUENCY_OPTIONS = ["매일", "주 3회", "매주"]
SUMMARY_LENGTH_OPTIONS = ["짧게", "중간", "길게"]
LANGUAGE_OPTIONS = ["한국어", "영어"]


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
    """API 에러 응답에서 detail 메시지를 뽑아낸다. 없으면 상태코드 기반 기본 메시지."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail or f"요청이 실패했습니다 (status={response.status_code})"


def _request(method, path, **kwargs):
    url = f"{API_BASE_URL}{path}"
    return requests.request(method, url, timeout=10, **kwargs)


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


def get_subscriber_by_email(email):
    """
    이메일을 기준으로 특정 구독자 1명의 정보를 가져오는 함수 (본인 확인용, 인증 불필요).

    매개변수:
        email (str): 찾고 싶은 구독자의 이메일

    반환값:
        dict 또는 None
        - 찾으면 {"name", "email", "keywords", "send_time", "frequency",
                  "summary_length", "language", "confirmed"} 반환
        - 없으면 None 반환
    """
    try:
        resp = _request("GET", f"/subscribers/{email}")
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None
    return _subscriber_to_dict(resp.json())


def delete_subscriber(email):
    """
    이메일 기준 구독자 삭제
    """
    try:
        resp = _request("DELETE", f"/subscribers/{email}")
    except requests.RequestException:
        return False
    return resp.status_code == 204


def unsubscribe_subscriber(email):
    """
    일반 사용자가 자기 이메일로 구독 취소
    """
    return delete_subscriber(email)


def update_subscriber(old_email, name, new_email, keywords, send_time, frequency, summary_length, language):
    """
    기존 이메일(old_email)을 기준으로 구독자 정보 수정.

    이메일 자체는 구독자 식별자라 PUT으로 바꿀 수 없으므로(백엔드 제약),
    이메일이 바뀌는 경우 새 이메일로 재가입(POST) 후 기존 항목을 삭제(DELETE)한다.
    이 경로를 타면 새 이메일은 다시 미확인(confirmed=False) 상태로 시작해
    확인 메일을 새로 받는다 — 이메일 소유권이 바뀌는 것이므로 의도된 동작이다.

    반환값: (bool, str|None) 성공 여부와 실패 시 에러 메시지.
    """
    if new_email != old_email:
        created, err = save_subscriber(name, new_email, keywords, send_time, frequency, summary_length, language)
        if not created:
            return False, err
        delete_subscriber(old_email)
        return True, None

    hour, minute = _parse_send_time(send_time)
    payload = {
        "name": name,
        "keywords": _split_keywords(keywords),
        "send_hour": hour,
        "send_minute": minute,
        "frequency": frequency,
        "summary_length": summary_length,
        "language": language,
    }
    try:
        resp = _request("PUT", f"/subscribers/{old_email}", json=payload)
    except requests.RequestException as exc:
        return False, f"서버에 연결할 수 없습니다: {exc}"

    if resp.status_code != 200:
        return False, _api_error_message(resp)
    return True, None
