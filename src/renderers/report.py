import html
from urllib.parse import urlparse


def _safe_href(url):
    """http/https 링크만 허용. 그 외(javascript:, data: 등)는 '#' 로 대체."""
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        return "#"
    return url if scheme in ("http", "https") else "#"


def render(digests):
    """다이제스트(이슈→주제→기사 계층)를 메일 HTML 본문으로 렌더링한다.

    [인터페이스 계약] — 구현 시 아래 입출력 형태를 지켜주세요.
        args:
            digests(dict): {query(str): [issue, ...]}
                issue: {"headline": str, "topics": [topic, ...]}
                topic: {"topic": str, "topic_summary": str, "links": [str, ...]}
                (하나의 query 에 issue 가 여러 개, 하나의 issue 에 topic 이 1~3개,
                 하나의 topic 에 관련 기사 link 가 여러 개 있을 수 있다)
        returns:
            str: 완성된 HTML 본문

    ※ 아래는 구현 전 임시 스텁이다. 실제 디자인/템플릿은 기획/데이터 담당이 맡는다.
      단, 이스케이프/링크 검증은 디자인을 바꿔도 반드시 유지할 것(각 link 마다 적용).
    """
    # TODO(기획/데이터): templates/daily_report.html 기반 실제 디자인으로 교체
    blocks = ["<html><body style=\"font-family: sans-serif;\">"]
    blocks.append("<h2>오늘의 금융 뉴스 브리핑</h2>")
    for query, issues in digests.items():
        blocks.append(f"<h3>{html.escape(query)}</h3>")
        for issue in issues:
            blocks.append(f"<h4>{html.escape(issue.get('headline', ''))}</h4><ul>")
            for topic in issue.get("topics", []):
                summary = html.escape(topic.get("topic_summary", ""))
                links_html = " ".join(
                    f'<a href="{html.escape(_safe_href(link), quote=True)}">[기사]</a>'
                    for link in topic.get("links", [])
                )
                blocks.append(
                    f"<li><strong>{html.escape(topic.get('topic', ''))}</strong> "
                    f"{summary} {links_html}</li>"
                )
            blocks.append("</ul>")
    blocks.append("</body></html>")
    return "\n".join(blocks)
