# -*- coding: utf-8 -*-
"""발송 시연용 - 수집 -> 요약 -> 렌더 -> 발송을 '단계별로 멈춰 가며' 실행한다.

이 스크립트는 **시연 보조자가 자기 터미널에서** 돌리는 용도다.
 - 각 단계 뒤에 Enter를 눌러야 다음으로 넘어간다(자동 원클릭이 아님) -> 설명하며 진행하거나 원하는 지점에서 멈출 수 있다.
 - 화면에는 코드가 아니라 '사람이 읽는 진행 메시지'만 나온다.
 - 발송 직전에도 한 번 멈추므로, 거기서 그치면 실제 발송은 일어나지 않는다.

용도:
 - (리허설) 실행해서 받은편지함에 실물 메일을 확보해 둔다.
 - (라이브) 보조자가 발표 중 실행하고, 발표 화면에는 그 로그 또는 받은편지함만 띄운다.

이 파일은 백엔드 디렉터리(team_project)에 있고, 발송은 프론트 없이 이 백엔드만으로 끝까지 돈다.

사용법(백엔드 폴더에서):
    python demo_send.py 받는사람@gmail.com 금리,반도체
    (인자를 생략하면 실행 중에 물어본다)
"""
import os
import sys

from dotenv import load_dotenv

# 실행 위치와 무관하게 이 파일 옆의 .env를 먼저 로드하고, src 를 import 가능하게 한다.
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))
sys.path.insert(0, _HERE)


def _pause(msg="계속하려면 Enter를 누르세요 (멈추려면 Ctrl+C)"):
    try:
        input(f"\n  >>{msg} ")
    except (EOFError, KeyboardInterrupt):
        print("\n중단했습니다. (발송 전이라면 메일은 나가지 않았습니다.)")
        raise SystemExit(0)


def _check_env():
    """발송에 필요한 값이 .env에 있는지 먼저 점검해, 낯선 에러 대신 친절히 안내한다."""
    need = {
        "NAVER_CLIENT_ID": "수집(네이버 뉴스)",
        "NAVER_CLIENT_SECRET": "수집(네이버 뉴스)",
        "OPENAI_API_KEY": "요약(LLM · OpenRouter)",
        "SENDER": "발송(Gmail 주소)",
        "GOOGLE_APP_PASSWORD": "발송(Gmail 앱 비밀번호)",
    }
    missing = [f"    - {k}   <- {why}" for k, why in need.items() if not os.getenv(k)]
    if missing:
        print("[X].env에 아래 값이 비어 있어 발송 시연을 할 수 없습니다:\n")
        print("\n".join(missing))
        print("\n.env를 채운 뒤 다시 실행하세요. (자세한 건 시연_가이드.md 1-2 참고)")
        raise SystemExit(1)


def main():
    _check_env()

    email = sys.argv[1] if len(sys.argv) > 1 else input("받는 이메일 주소: ").strip()
    kw_arg = sys.argv[2] if len(sys.argv) > 2 else input("키워드(쉼표로 구분, 예: 금리,반도체): ").strip()
    keywords = [k.strip() for k in kw_arg.split(",") if k.strip()]
    if not email or not keywords:
        print("이메일과 키워드는 반드시 있어야 합니다.")
        raise SystemExit(1)

    # 키 점검을 통과한 뒤에야 파이프라인을 불러온다
    # (요약 모듈은 import 하는 순간 OPENAI_API_KEY를 요구하기 때문).
    from src import config, db
    from src.collectors import naver_news
    from src.processors import summarizer
    from src.renderers import report
    from src.notifiers import send_email

    print(f"\n>받는 사람 : {email}")
    print(f">키워드    : {', '.join(keywords)}")
    _pause("시작하려면 Enter")

    # ── 1/4 수집 ───────────────────────────────────────────
    print("\n[1/4] 뉴스 수집 중...")
    collected = naver_news.collect(keywords, config.NEWS_DISPLAY)
    total = sum(len(v) for v in collected.values())
    for kw, items in collected.items():
        print(f"      · {kw}: {len(items)}건")
    if total == 0:
        print("수집된 뉴스가 없습니다. 키워드를 바꿔 다시 실행하세요.")
        raise SystemExit(1)
    print(f"      -> 총 {total}건 수집 완료")
    _pause()

    # ── 2/4 요약(LLM) ──────────────────────────────────────
    print("\n[2/4] AI가 요약하는 중... (수 초~수십 초 걸릴 수 있습니다)")
    flat = summarizer.summarize(collected, "중간", "한국어")
    rows_total = sum(len(v) for v in flat.values())
    for kw, rows in flat.items():
        print(f"      · {kw}: {len(rows)}개 항목")
    if rows_total == 0:
        print("요약 결과가 비었습니다. (LLM 응답 문제) 다시 시도해 보세요.")
        raise SystemExit(1)
    print(f"      -> 요약 완료 (총 {rows_total}개 항목)")
    _pause()

    # ── 3/4 렌더(메일 HTML) ────────────────────────────────
    print("\n[3/4] 메일 형태로 만드는 중...")
    digests = {kw: db.group_digest_rows(rows) for kw, rows in flat.items() if rows}
    html = report.render(digests)
    print(f"      -> 메일 HTML 생성 완료 ({len(html):,}자)")
    _pause("실제로 발송하려면 Enter (여기서 멈추면 발송되지 않습니다)")

    # ── 4/4 발송(Gmail) ────────────────────────────────────
    print("\n[4/4] 발송 중...")
    send_email.send_email(email, subject="[데일리] 오늘의 뉴스 브리핑", body_html=html)
    print(f"      [OK]발송 완료 -> {email}")
    print("\n받은편지함을 확인하세요.")


if __name__ == "__main__":
    main()
