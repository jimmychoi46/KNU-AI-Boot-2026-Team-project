"""메일 본문 렌더링 인터페이스.

담당: D (기획/데이터) — 템플릿 디자인·가독성.
백엔드(B)는 render() 를 호출해 최종 HTML 을 받는다.
실제 템플릿/디자인(templates/daily_report.html 등)은 D 가 담당한다.
"""


def render(summarized):
    """요약 결과를 메일 HTML 본문으로 렌더링한다.

    [인터페이스 계약] — D 가 구현할 때 아래 입출력 형태를 지켜주세요.
        args:
            summarized(dict): summarizer.summarize() 의 반환값
                {query(str): [{"headline", "summary", "link"}, ...]}
        returns:
            str: 완성된 HTML 본문

    ※ 아래는 D 구현 전 임시 스텁이다. 실제 디자인/템플릿은 D 가 담당.
    """
    # TODO(D): templates/daily_report.html 기반 실제 디자인으로 교체
    blocks = ["<html><body style=\"font-family: sans-serif;\">"]
    blocks.append("<h2>오늘의 금융 뉴스 브리핑</h2>")
    for query, rows in summarized.items():
        blocks.append(f"<h3>{query}</h3><ul>")
        for row in rows:
            blocks.append(f'<li><a href="{row["link"]}">{row["headline"]}</a></li>')
        blocks.append("</ul>")
    blocks.append("</body></html>")
    return "\n".join(blocks)
