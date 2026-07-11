import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytz

from src.config import BASE_DIR, FRONTEND_BASE_URL, NEWSLETTER_NAME, TIMEZONE

_TEMPLATE_PATH = Path(BASE_DIR) / "src" / "templates" / "daily_report.html"
_REPEAT_START = "<!-- [반복 시작] -->"
_REPEAT_END = "<!-- [반복 끝] -->"


def _safe_href(url):
    """http/https 링크만 허용. 그 외(javascript:, data: 등)는 '#' 로 대체."""
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        return "#"
    return url if scheme in ("http", "https") else "#"


def _escaped_href(url):
    """href 속성에 그대로 넣어도 안전하도록 스킴 검증 + 속성 이스케이프까지 적용."""
    return html.escape(_safe_href(url), quote=True)


def _esc(value):
    """None·숫자 등 문자열이 아닌 값이 와도 안전하게 문자열로 만들어 이스케이프한다.
    LLM 이 내는 JSON 은 스키마(값 타입)를 보장하지 않아 제목·요약이 숫자로 올 수 있는데,
    그대로 html.escape 에 넘기면 int.replace 가 없어 AttributeError 로 발송이 통째로 멈춘다."""
    return html.escape("" if value is None else str(value))


_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")


def _fill(block, values):
    """{{key}} 자리표시자를 values 로 치환한다(모르는 key 는 그대로 둔다).

    단일 패스 치환이라, 앞서 치환해 넣은 값 안에 우연히 다른 {{...}} 문구가 있어도
    뒤 치환이 그걸 다시 건드리지 않는다 — 순차 str.replace 였을 때는 LLM 요약에 우연히
    '{{원문_링크}}' 같은 문구가 섞이면 그 자리에 실제 URL 이 끼어들어 본문이 깨졌다.
    """
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), block)


def _split_item_block(template):
    """본문 템플릿을 (머리말, 반복 아이템 블록, 꼬리말) 로 분리한다."""
    start = template.index(_REPEAT_START) + len(_REPEAT_START)
    end = template.index(_REPEAT_END)
    return template[:start], template[start:end], template[end:]


# 템플릿 파일은 프로세스 수명 동안 바뀌지 않으므로 모듈 로드 시 1회만 읽고 분리해 둔다.
# (render()가 발송 대상마다 반복 호출되는데, 매번 디스크에서 다시 읽으면 낭비다.)
_HEAD, _ITEM_BLOCK, _TAIL = _split_item_block(_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _trend_keyword_block(keyword, topics):
    """주간 트렌드 이메일의 키워드 1개 블록: 토픽 제목 + 요약 + 관련 기사 링크.

    다크모드 대응 클래스(text-title/text-body/link-gold)를 달아, 다크모드 메일에서 카드 배경만
    어두워지고 이 글자가 라이트모드 색 그대로 남아 안 보이게 되는 일을 막는다.
    topics: [{"topic", "summary", "links": [str,...]}, ...]
    """
    rows = []
    for t in topics:
        links = t.get("links") or []
        summary = _esc(t.get("summary"))
        link_html = (
            f'<a class="link-gold" href="{_escaped_href(links[0])}" '
            f'style="color:#a6842f; font-size:13px; font-weight:700; text-decoration:none;">관련 기사 보기 &rsaquo;</a>'
            if links else ""
        )
        rows.append(
            '<tr><td style="padding:10px 0; border-bottom:1px solid #eef1f5;">'
            f'<p class="text-title" style="margin:0 0 4px 0; color:#0a2540; font-size:15px; font-weight:700;">'
            f'{_esc(t.get("topic"))}</p>'
            f'<p class="text-body" style="margin:0 0 6px 0; color:#4a5568; font-size:14px; line-height:1.6;">{summary}</p>'
            f'{link_html}</td></tr>'
        )
    return (
        '<tr><td style="padding:16px 32px;">'
        f'<p class="text-title" style="margin:0 0 6px 0; color:#0a2540; font-size:17px; font-weight:800;">'
        f'#{_esc(keyword)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>'
        '</td></tr>'
    )


def render_weekly_trend(trends, now=None):
    """주간 트렌드 키워드를 '토픽 + 요약 + 관련 기사'로 담은 '별도' 이메일 본문을 렌더링한다.

    일간 뉴스레터와 분리된 독립 메일이라 머리말/꼬리말(_HEAD/_TAIL)을 함께 붙여 완성된 HTML을 만든다.
    trends: {keyword: [{"topic", "article_count", "summary", "links": [str,...]}, ...]}
        (pipeline.weekly_trend_articles_for). 비어 있으면 빈 문자열을 반환한다.
    관련 기사 링크는 그 주 다이제스트에 이미 저장된 것을 그대로 쓴다(추가 수집/LLM 없음).
    """
    if not trends:
        return ""
    now = now or datetime.now(pytz.timezone(TIMEZONE))
    brief = "이번 주 가장 많이 다뤄진 키워드와 관련 기사입니다."
    head = _fill(_HEAD, {
        "뉴스레터_이름": html.escape(NEWSLETTER_NAME),
        "발송_날짜": now.strftime("%Y년 %m월 %d일"),
        "프리헤더_문구": html.escape(brief),
        "오늘의_핵심_요약": html.escape(brief),
    })
    label = (
        '<tr><td style="padding:20px 32px 0 32px;">'
        '<p class="link-gold" style="margin:0; color:#a6842f; font-size:12px; font-weight:700; letter-spacing:1.5px;">'
        "THIS WEEK'S TREND</p></td></tr>"
    )
    blocks = "".join(_trend_keyword_block(kw, topics) for kw, topics in trends.items())
    tail = _fill(_TAIL, {
        "뉴스레터_이름": html.escape(NEWSLETTER_NAME),
        "구독취소_링크": _escaped_href(f"{FRONTEND_BASE_URL}/unsubscribe"),
        "설정_링크": _escaped_href(f"{FRONTEND_BASE_URL}/user_dashboard"),
    })
    return head + label + blocks + tail


def render(digests, now=None):
    """다이제스트(이슈→주제→기사 계층)를 메일 HTML 본문으로 렌더링한다.

    [인터페이스 계약] — 구현 시 아래 입출력 형태를 지켜주세요.
        args:
            digests(dict): {query(str): [issue, ...]}
                issue: {"headline": str, "topics": [topic, ...]}
                topic: {"topic": str, "topic_summary": str, "links": [str, ...]}
                (하나의 query 에 issue 가 여러 개, 하나의 issue 에 topic 이 1~3개,
                 하나의 topic 에 관련 기사 link 가 여러 개 있을 수 있다)
            now(datetime, optional): 발송_날짜에 찍을 기준 시각. 미지정 시 현재 KST
                (테스트·백필에서 결정론적 시각을 주입할 수 있도록).
        returns:
            str: 완성된 HTML 본문

    디자인은 templates/daily_report.html (기획/데이터 담당, Template 브랜치 산출물)을 그대로
    쓴다. topic 하나당 뉴스 아이템 한 칸을 채우며, 카드 제목은 topic 자신의 제목(topic.topic)을
    쓰고(없으면 이슈 헤드라인으로 대체), 대표 링크는 topic.links[0]을 쓴다
    (여러 기사를 한 요약이 인용하더라도 "원문 보기"는 한 곳만 가리키는 카드형 레이아웃이므로).
    AI/스크래핑 출처 텍스트는 전부 html.escape, 링크는 전부 _safe_href 를 거친다(각 topic 마다).
    """
    items_html = []
    highlight = ""
    for query, issues in digests.items():
        for issue in issues:
            # 값이 JSON null(None)·숫자로 들어와도 html.escape 크래시가 안 나도록 최후 방어선에서 문자열화.
            headline = str(issue.get("headline") or "")
            highlight = highlight or headline
            for topic in issue.get("topics") or []:  # 값이 JSON null(None)이어도 안전(기본값은 키 부재 때만 적용됨)
                links = topic.get("links") or []
                items_html.append(_fill(_ITEM_BLOCK, {
                    "카테고리": _esc(query),
                    "뉴스_제목": _esc(topic.get("topic") or headline),
                    "요약_본문": _esc(topic.get("topic_summary")),
                    "원문_링크": _escaped_href(links[0] if links else ""),
                }))

    now = now or datetime.now(pytz.timezone(TIMEZONE))
    highlight = highlight or "오늘의 뉴스 브리핑"
    brief = (
        f"{highlight} 외 총 {len(items_html)}건의 뉴스를 정리했습니다."
        if len(items_html) > 1 else highlight
    )

    head = _fill(_HEAD, {
        "뉴스레터_이름": html.escape(NEWSLETTER_NAME),
        "발송_날짜": now.strftime("%Y년 %m월 %d일"),
        "프리헤더_문구": html.escape(brief),
        "오늘의_핵심_요약": html.escape(brief),
    })
    tail = _fill(_TAIL, {
        "뉴스레터_이름": html.escape(NEWSLETTER_NAME),
        "구독취소_링크": _escaped_href(f"{FRONTEND_BASE_URL}/unsubscribe"),
        "설정_링크": _escaped_href(f"{FRONTEND_BASE_URL}/user_dashboard"),
    })

    return head + "".join(items_html) + tail
