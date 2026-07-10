import html
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    HTTP_BACKOFF, HTTP_MAX_RETRIES, HTTP_TIMEOUT, NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET, NEWS_DISPLAY, RECENCY_HOURS, TIMEZONE,
)


NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

# 연결 오류·429·5xx 발생 시 간격을 늘려 재시도 (GET 만 재시도) + 429(한도 초과) 는 Retry-After 헤더를 존중
_retry = Retry(
    total=HTTP_MAX_RETRIES,
    backoff_factor=HTTP_BACKOFF,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
)
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_session.mount("http://", HTTPAdapter(max_retries=_retry))


def get_naver_news(query, display=NEWS_DISPLAY):
    """검색어 query 에 해당하는 뉴스를 반환하는 GET 메서드.

    args:
        query(str): 검색어
        display(int): 검색결과 출력건수

    returns:
        (status_code, json_dict):
            status_code - 200 성공, 그 외 실패
                (401: client id/secret 불일치(확인 권장), 429: 한도 초과)
                None: 재시도 후에도 네트워크/타임아웃으로 응답을 못 받음
            json_dict   - 응답 JSON (실패 시 {})
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
    try:
        # 429/5xx/연결오류 발생 시 간격 늘려서 재시도 (timeout 으로 무한 대기 방지)
        res = _session.get(
            NAVER_NEWS_URL, headers=headers, params=params, timeout=HTTP_TIMEOUT
        )
    except requests.RequestException as exc:
        # 재시도를 했으나 실패한 경우 예외 처리를 통해 스케줄러의 동작이중단되지 않도록 함.
        print(f"[요청 실패] query={query}: {exc}")
        return None, {}

    try:
        payload = res.json()
    except ValueError:
        # 200인데 JSON 이 아닐 수 있음(에러 페이지 등)
        payload = {}
    return res.status_code, payload


def clean_text(text):
    """엔티티(&quot; 등) 복원 → <b>, </b> 제거 → 공백 정리.

    순서(HTML 인젝션 방지를 위해 중요!)
    1. 엔티티 복원
    2. 태그 제거
    """
    text = html.unescape(text or "")      # unescape(): HTML 엔터티 -> 일반 문자 변환(ex. &lt -> '<')
    # 실제 HTML 태그(<b>·</b>·<script>·<!-- -->)만 제거한다 — 태그는 문자/슬래시/! 로 시작한다.
    # <속보>·<단독> 같은 한국어 마커나 'A < B' 같은 리터럴 부등호(태그 아님)는 보존한다.
    # 엔티티 인코딩된 태그(&lt;script&gt;)는 위 unescape 로 <script> 가 된 뒤 여기서 제거되고,
    # 남는 위험 문자는 렌더 단계의 html.escape 가 최종 차단한다(방어 심층화).
    text = re.sub(r"</?[A-Za-z!][^>]*>", "", text)
    return text.strip()                   # 공백 정리 후 반환


def parse_pubDate(pubdate):
    """뉴스 JSON에 있는 pubDate의 값을 ISO 8601(표준 형식) 문자열로 변환. 실패 시 None."""
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate).isoformat()
    except (TypeError, ValueError):
        return None


def clean_item(raw):
    """수집된 뉴스를 정제하는 로직.

    1. 각 key에 대응되는 값을 get()을 통해 안전하게 읽어옴
    2. 각 key-value 쌍에 필요한 로직(title, description -> clean_text를 통한 태그 제거, pubDate -> parse_pubDate를 통해 표준 형식 문자열 변환) 적용

    args:
        raw(dict): 네이버 API 원본 item (title/link/description/pubDate 등)
    returns:
        {"title", "link", "description", "published_at"}를 key로 하는 정제된 딕셔너리
    """
    return {
        "title": clean_text(raw.get("title", "")), 
        "link": raw.get("link", ""),
        "description": clean_text(raw.get("description", "")),
        "published_at": parse_pubDate(raw.get("pubDate")),
    }


def within_recency(item, now, hours):
    """작성 시간으로부터 현재까지 지난 시간이 hours에 전달된 값보다 짧다면 True 반환(뉴스 유지), 길다면 False 반환(뉴스 수집 x)

    날짜를 알 수 없을 경우(파싱 실패/없음)에는 False(제외)로 처리
    """
    iso = item.get("published_at")
    if not iso:
        return False # published_at 컬럼 미존재 시 수집 제외
    try:
        published = datetime.fromisoformat(iso)
        return published >= now - timedelta(hours=hours)
    except (ValueError, TypeError):
        # ValueError: 날짜 파싱 실패. TypeError: tz-naive published 를 tz-aware now 와 비교(offset 불일치).
        # 한 건의 비교 실패가 수집 사이클 전체를 죽이지 않도록 그 항목만 제외한다.
        return False


def dedupe(items):
    """링크 또는 정규화한 제목이 같은 중복 뉴스를 제거(첫 등장만 유지)."""
    seen_links, seen_titles = set(), set() # 링크와 title을 담을 빈 set 정의
    result = [] # 뉴스(중복 뉴스 제거) 담을 빈 리스트 result 정의
    for item in items:
        link = item.get("link", "")
        title_key = re.sub(r"\s+", " ", item.get("title", "")).strip().lower() # title 값에 대해 여러 칸의 공백/개행을 한 칸 띄움 ()" ")으로 처리 -> 공백 정리 -> 소문자
        if (link and link in seen_links) or (title_key and title_key in seen_titles):
            continue
        if link:
            seen_links.add(link)        # link가 존재할 경우 seen_links set에 추가 시도(이미 존재한다면 추가 x)
        if title_key:
            seen_titles.add(title_key) # title_key가 존재할 경우 seen_titles set에 추가 시도(이미 존재한다면 추가 x)
        result.append(item)
    return result


def collect(queries, display=NEWS_DISPLAY, now=None, dedupe_flag=True):
    """뉴스를 수집·정제·필터링해 {query: [cleaned_item]} 로 반환.

    파이프라인: 수집 → 정제(clean_item) → 기간 필터(최근 RECENCY_HOURS) → 중복 제거
    실패한 검색어는 빈 리스트로 채운다.
    now: 기간 필터 기준 시각(테스트용 주입). 미지정 시 현재 KST.
    dedupe_flag: 중복(같은 링크/정규화 제목) 제거 여부(기본 True).
    """
    if now is None:
        now = datetime.now(pytz.timezone(TIMEZONE))

    result = {}  # 수집된 뉴스를 담을 딕셔너리
    for query in queries:
        status, data = get_naver_news(query, display)
        if status != 200:
            # TODO: 로깅 / 재시도 등 에러 처리
            print(f"[수집 실패] query={query}, status={status}")
            result[query] = []
            continue
        items = [clean_item(item) for item in data.get("items", [])]              # 정제
        items = [it for it in items if within_recency(it, now, RECENCY_HOURS)]   # 기간 필터
        if dedupe_flag:
            items = dedupe(items)                                                # 중복 제거
        result[query] = items
    return result
