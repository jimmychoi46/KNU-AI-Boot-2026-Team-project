"""요약·편집 단계 인터페이스.

담당: LLM/Agent — 검색·요약·편집 멀티에이전트.
백엔드가 수집·정제한 '깨끗한' 뉴스를 받아 요약/편집한다.
백엔드는 summarize() 를 호출하기만 하고, 실제 LLM/에이전트 구현은 담당자가 채운다.
"""


def summarize(collected, summary_length, language):
    """정제된 뉴스를 요약·편집한다.

    [인터페이스 계약] — 구현 시 아래 입출력 형태를 지켜주세요.
        args:
            collected(dict): {query(str): [cleaned_item(dict), ...]}
                cleaned_item: {"title", "link", "description", "published_at"}
                (백엔드가 이미 태그·엔티티 제거, 날짜 파싱까지 마친 상태)
            summary_length(str): config.SUMMARY_LENGTH 중 하나 ("짧게"/"중간"/"길게")
                — 요약 문장 길이/분량을 이 값에 맞춰야 한다.
            language(str): config.LANGUAGE 중 하나 ("한국어"/"영어")
                — headline/summary 를 이 언어로 작성해야 한다.
                (원문이 한국어 뉴스라도 language="영어" 면 영어로 번역·요약)
        returns:
            dict: 렌더러(기획/데이터)가 소비할 구조. 예)
                {query(str): [{"headline": str, "topic": str, "topic_summary": str, "link": str}, ...]}

    백엔드는 구독자마다 다른 summary_length/language 조합에 대해 이 함수를 별도로 호출하고,
    결과를 그 조합 전용으로 저장한다(같은 기사도 조합마다 다른 요약이 남는다).

    ※ 아래는 구현 전 임시 통과(passthrough) 스텁이다. summary_length/language 를 아직
      반영하지 않지만, LLM 없이도 백엔드 파이프라인이 끝까지 돌도록 최소 형태만 만든다.
    """
    # TODO(LLM/Agent): summary_length/language 를 반영한 실제 요약 로직으로 교체
    result = {}
    for query, items in collected.items():
        rows = []
        for item in items:
            rows.append({
                "headline": item.get("title", ""),  # 입력이 이미 정제돼 있어 그대로 사용
                "topic": "",
                "topic_summary": "",                        # summary_length/language 에 맞춰 채움
                "link": item.get("link", ""),
            })
        result[query] = rows
    return result
