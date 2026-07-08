import pandas as pd
import os

# 구독자 정보를 저장할 CSV 파일 경로
FILE_PATH = "subscribers.csv"


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


def load_subscribers():
    """
    CSV 파일에서 구독자 정보를 불러오는 함수
    파일이 없으면 빈 데이터프레임 반환
    """
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame(columns=[
            "name",
            "email",
            "keywords",
            "send_time",
            "frequency",
            "summary_length",
            "language"
        ])

    return pd.read_csv(FILE_PATH)


def save_subscriber(name, email, keywords, send_time, frequency, summary_length, language):
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


def update_subscriber(old_email, name, new_email, keywords, send_time, frequency, summary_length, language):
    """
    기존 이메일(old_email)을 기준으로 구독자 정보 수정
    """
    df = load_subscribers()

    if df.empty:
        return False

    if old_email not in df["email"].values:
        return False

    row_index = df[df["email"] == old_email].index[0]

    df.at[row_index, "name"] = name
    df.at[row_index, "email"] = new_email
    df.at[row_index, "keywords"] = keywords
    df.at[row_index, "send_time"] = send_time
    df.at[row_index, "frequency"] = frequency
    df.at[row_index, "summary_length"] = summary_length
    df.at[row_index, "language"] = language

    df.to_csv(FILE_PATH, index=False)
    return True

def get_subscriber_by_email(email):
    """
    이메일을 기준으로 특정 구독자 1명의 정보를 가져오는 함수

    매개변수:
        email (str): 찾고 싶은 구독자의 이메일

    반환값:
        pandas.Series 또는 None
        - 찾으면 해당 행(row) 반환
        - 없으면 None 반환
    """
    df = load_subscribers()

    if df.empty:
        return None

    matched_rows = df[df["email"] == email]

    if matched_rows.empty:
        return None

    return matched_rows.iloc[0]
