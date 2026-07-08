from dotenv import load_dotenv
from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
import os
import requests


load_dotenv()




def get_naver_news(query, display=10):
    """
        검색어 query에 해당하는 뉴스를 반환하는 GET 메서드

        args:
            query(str): 검색어
            display(int): 검색결과 출력건수(기본값 10) 

        returns:
            res.status_code: API 요청 결과를 나타내는 상태코드 (간단히 말하면 200->성공, 그 외 -> 실패 (주로 401(Client id/secret 일치 여부 확인하기, 일치하는데도 401이 뜨면 개발자 센터에서 '검색' API 추가 여부 확인하기),429(한도 초과 발생)))
            res.json(): API 요청을 통해 반환된 응답을 JSON 형태로 나타낸 것.
    """
    url = "https://openapi.naver.com/v1/search/news.json" # 네이버 뉴스 검색

    # API 호출을 위한 header 설정 (필요 시 naver Developers API playground에서 API 호출해서 header 구조 파악 요망)
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }


    params = {
        "query": query,
        "display": display,
        "sort": "date"  # date = 최신순, sim = 정확도순 (상황에 따라 달리 사용할 것)
    }
    
    res = requests.get(url, headers=headers, params=params)
    return res.status_code, res.json()

if __name__ == '__main__':
    status, data = get_naver_news("주식", display=15)
    print(f"Status code: {status}")
    print(data)