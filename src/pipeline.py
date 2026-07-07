"""파이프라인 조립.

담당: B (백엔드).
수집(B) → 요약·편집(A) → 렌더링(D) → 발송(B) 순서로 엮어 1회 실행한다.
A / D 파트는 인터페이스만 호출하며, 각 모듈 내부 구현은 담당자가 채운다.
"""
from src import config
from src.collectors import naver_news    # 담당 B
from src.processors import summarizer     # 담당 A (요약·편집)
from src.renderers import report          # 담당 D (템플릿·렌더링)
from src.notifiers import send_email      # 담당 B


def run_pipeline():
    """뉴스 수집 → 요약 → 렌더링 → 발송까지 한 번 실행한다 (스케줄러가 호출)."""
    # 1. 수집 (B)
    collected = naver_news.collect(config.SEARCH_QUERIES, config.NEWS_DISPLAY)

    # 2. 요약·편집 (A 인터페이스)
    summarized = summarizer.summarize(collected)

    # 3. 렌더링 (D 인터페이스)
    body_html = report.render(summarized)

    # 4. 발송 (B)
    send_email.send_to_recipients(
        config.EMAIL_RECIPIENTS,
        subject="[데일리] 오늘의 금융 뉴스 브리핑",
        body_html=body_html,
    )
    print("파이프라인 실행 완료")


if __name__ == "__main__":
    # 스케줄러 없이 파이프라인만 즉시 1회 실행하고 싶을 때 사용
    run_pipeline()
