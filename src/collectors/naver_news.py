import requests

from src.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, NEWS_DISPLAY

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


def get_naver_news(query, display=NEWS_DISPLAY):
    """검색어 query 에 해당하는 뉴스를 반환하는 GET 메서드.

    args:
        query(str): 검색어
        display(int): 검색결과 출력건수

    returns:
        (status_code, json_dict):
            status_code - 200 성공, 그 외 실패
                (401: client id/secret 불일치(확인 권장), 429: 한도 초과)
            json_dict   - 응답 JSON
    """

    # API 호출을 위해 필요한 헤더
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": query,
        "display": display,
        "sort": "date",  # date=최신순, sim=정확도순
    }
    res = requests.get(NAVER_NEWS_URL, headers=headers, params=params)
    return res.status_code, res.json()


def collect(queries, display=NEWS_DISPLAY):
    """뉴스를 수집해 {query: [items]} 형태의 딕셔너리로 반환하는 함수.

    이때, 실패한 검색어의 경우 빈 리스트로 채운다.(단, 재시도 등 예외 처리가 수행될 수 있음)
    """
    result = {} # 수집된 뉴스를 담을 딕셔너리 result 정의

    for query in queries:
        status, data = get_naver_news(query, display)
        if status == 200:
            # items 키에 대응되는 값(뉴스) 반환, 이때 items가 없다면 빈 리스트 반환
            result[query] = data.get("items", [])
        else:
            # TODO: 로깅 / 재시도 등 에러 처리
            print(f"[수집 실패] query={query}, status={status}")
            result[query] = []
    return result
