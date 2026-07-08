import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# 백엔드(team_project) API 주소. .env 에서 오버라이드 가능(배포 시 실제 주소로 교체).
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


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


def _parse_send_time(hhmm):
    """'13:30' -> (13, 30). 백엔드는 send_hour(0~24)/send_minute(0 또는 30)을 따로 받는다."""
    hour_str, minute_str = hhmm.split(":")
    return int(hour_str), int(minute_str)


def _format_send_time(hour, minute):
    """백엔드의 send_hour/send_minute -> 화면 선택지 형식('13:30')으로 복원."""
    return f"{int(hour):02d}:{int(minute):02d}"


def _split_keywords(text):
    """'AI, Python, 스타트업' -> ['AI', 'Python', '스타트업']. 백엔드는 keywords 를 리스트로 받는다."""
    return [k.strip() for k in str(text).split(",") if k.strip()]


def _join_keywords(keywords):
    """백엔드가 돌려주는 keywords 리스트 -> 화면 표시/수정용 콤마 문자열."""
    if isinstance(keywords, list):
        return ", ".join(keywords)
    return str(keywords)


def _api_error_message(response):
    """API 에러 응답에서 사람이 읽을 메시지를 뽑아낸다(백엔드가 {"detail": "..."} 형식으로 줌)."""
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def _request(method, path, **kwargs):
    """공통 요청 래퍼 — 네트워크 오류(서버 다운 등)를 사람이 읽을 메시지로 통일해서 반환.

    returns: (response 또는 None, 에러메시지 또는 None)
    """
    try:
        res = requests.request(method, f"{API_BASE_URL}{path}", timeout=10, **kwargs)
    except requests.RequestException as exc:
        return None, f"서버에 연결할 수 없습니다: {exc}"
    return res, None


def load_subscribers(admin_password):
    """전체 구독자 목록을 조회한다 (관리자 전용 — GET /subscribers).

    returns: (DataFrame, None) 성공 시 / (None, 에러메시지) 실패 시.
    """
    res, err = _request(
        "GET", "/subscribers", headers={"X-Admin-Password": admin_password},
    )
    if err:
        return None, err
    if res.status_code != 200:
        return None, _api_error_message(res)

    rows = res.json()
    for row in rows:
        row["keywords"] = _join_keywords(row["keywords"])
        row["send_time"] = _format_send_time(row["send_hour"], row["send_minute"])

    df = pd.DataFrame(rows, columns=[
        "name", "email", "keywords", "send_time", "frequency",
        "summary_length", "language", "confirmed",
    ])
    return df, None


def save_subscriber(name, email, keywords, send_time, frequency, summary_length, language):
    """새 구독자 정보를 등록한다 (POST /subscribers).

    성공해도 아직 confirmed=False 상태다 — 확인 메일의 링크를 눌러야 실제 발송 대상이 된다
    (더블 옵트인). 이미 확인된 이메일로 재신청하면 409, 미확인 상태 재신청은 확인 메일 재전송.
    returns: (True, None) 성공 / (False, 에러메시지) 실패.
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
    res, err = _request("POST", "/subscribers", json=payload)
    if err:
        return False, err
    if res.status_code in (200, 201):
        return True, None
    return False, _api_error_message(res)


def get_statistics(df):
    """
    구독자 통계 계산 (load_subscribers 로 이미 받아온 DataFrame 기준)
    """
    if df is None or df.empty:
        return {
            "total_subscribers": 0,
            "most_common_frequency": "없음",
            "most_common_language": "없음",
            "confirmed_count": 0,
        }

    return {
        "total_subscribers": len(df),
        "most_common_frequency": df["frequency"].mode()[0],
        "most_common_language": df["language"].mode()[0],
        "confirmed_count": int(df["confirmed"].sum()) if "confirmed" in df else 0,
    }


def delete_subscriber(email):
    """
    이메일 기준 구독자 삭제 (DELETE /subscribers/{email})
    returns: (True, None) 성공 / (False, 에러메시지) 실패.
    """
    res, err = _request("DELETE", f"/subscribers/{email}")
    if err:
        return False, err
    if res.status_code == 204:
        return True, None
    return False, _api_error_message(res)


def unsubscribe_subscriber(email):
    """
    일반 사용자가 자기 이메일로 구독 취소
    """
    return delete_subscriber(email)


def update_subscriber(old_email, name, new_email, keywords, send_time, frequency, summary_length, language):
    """
    기존 이메일(old_email)을 기준으로 구독자 정보 수정 (PUT /subscribers/{old_email})

    이메일 자체를 바꾸는 경우: 이메일이 백엔드의 식별자(PK)라 PUT으로 이메일을 바꿀 수
    없다 — 기존 것을 삭제하고 새 이메일로 다시 신청한다(다시 confirmed=False 로 시작,
    새 확인 메일 발송).
    returns: (True, 안내메시지 또는 None) 성공 / (False, 에러메시지) 실패.
    """
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

    if new_email != old_email:
        deleted, err = delete_subscriber(old_email)
        if not deleted:
            return False, err
        created, err = save_subscriber(
            name, new_email, keywords, send_time, frequency, summary_length, language,
        )
        if not created:
            return False, err
        return True, "이메일이 변경되어 새 확인 메일이 발송되었습니다. 이메일을 확인해주세요."

    res, err = _request("PUT", f"/subscribers/{old_email}", json=payload)
    if err:
        return False, err
    if res.status_code == 200:
        return True, None
    return False, _api_error_message(res)
