"""요약·편집 단계 인터페이스.

담당: A (LLM/Agent) — 검색·요약·편집 멀티에이전트.
백엔드(B)는 이 모듈의 summarize() 를 호출하기만 하고,
실제 LLM/에이전트 구현은 A 가 이 함수 내부를 채운다.
"""
import re


def summarize(collected):
    """수집된 원본 뉴스를 요약·편집한다.

    [인터페이스 계약] — A 가 구현할 때 아래 입출력 형태를 지켜주세요.
        args:
            collected(dict): {query(str): [naver_item(dict), ...]}
                naver_item 예: {"title", "link", "description", "pubDate", ...}
        returns:
            dict: 렌더러(D)가 소비할 구조. 예)
                {query(str): [{"headline": str, "summary": str, "link": str}, ...]}

    ※ 아래는 A 구현 전 임시 통과(passthrough) 스텁이다.
      LLM 없이도 백엔드 파이프라인이 끝까지 돌도록 최소 형태만 만든다.
    """
    # TODO(A): LLM / 멀티에이전트 요약·편집 로직으로 교체
    result = {}
    for query, items in collected.items():
        rows = []
        for item in items:
            headline = re.sub(r"<.*?>", "", item.get("title", "")).strip()
            rows.append({
                "headline": headline,
                "summary": "",  # A 가 요약 텍스트로 채움
                "link": item.get("link", ""),
            })
        result[query] = rows
    return result
