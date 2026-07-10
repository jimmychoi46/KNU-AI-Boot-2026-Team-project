"""시연 보조 CLI - 발표자가 DB/curl 을 직접 안 만지고 데모를 돌리도록 돕는다.

Windows 콘솔(cp949)에서도 안 깨지게, 출력은 한글 + ASCII 기호([OK]/[X]/->)만 쓴다.

사용법 (team_project 폴더에서):
    python demo_helper.py reset                 # 구독자 전부 삭제(데모 초기화)
    python demo_helper.py seed                   # 데모용 구독자 몇 명 넣기(관리자 화면용)
    python demo_helper.py list                  # 현재 구독자 목록 보기
    python demo_helper.py confirm-url <이메일>   # 확인 링크 출력(메일 대신)
    python demo_helper.py code <이메일>          # 본인확인 코드 출력(메일 대신)
    python demo_helper.py bad-time              # 백엔드 발송시각 검증 시연(API 필요)
    python demo_helper.py pipeline-demo <이메일> # 수집->요약->렌더 (내장 샘플, 키 불필요)
    python demo_helper.py norepeat-demo <이메일> # 재발송 방지: 이틀 발송 -> 둘째날 새 기사만 (키 불필요)
    python demo_helper.py pipeline-live <이메일> # 실제 수집+LLM+발송 (키 필요, 보조자용)
"""
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config, db, subscriptions  # noqa: E402


def _p(msg=""):
    print(msg)


def _api_base():
    return config.API_BASE_URL.rstrip("/")


# ── reset / list ─────────────────────────────────────────────
def cmd_reset(args):
    """구독자 테이블을 비운다(뉴스/요약 데이터는 건드리지 않음)."""
    import sqlite3
    from contextlib import closing
    before = db.count_subscribers()
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.execute("DELETE FROM subscribers")
        conn.commit()
    _p(f"[OK] 구독자 {before}명 삭제 -> 현재 {db.count_subscribers()}명")
    _p("     (뉴스 기사/요약 데이터는 그대로 둡니다)")


_SEED = [
    {"email": "minjun@demo.com", "name": "김민준", "keywords": ["주식", "금리"],
     "send_hour": 8, "send_minute": 0, "frequency": "매일", "confirmed": True},
    {"email": "seoyeon@demo.com", "name": "이서연", "keywords": ["환율"],
     "send_hour": 9, "send_minute": 30, "frequency": "매주", "confirmed": True},
    {"email": "jihu@demo.com", "name": "박지후", "keywords": ["코인", "주식"],
     "send_hour": 7, "send_minute": 0, "frequency": "주 3회", "confirmed": False},
]


def cmd_seed(args):
    """데모용 구독자 몇 명을 넣는다(관리자 대시보드에 보여줄 목록 만들기용)."""
    for row in _SEED:
        subscriptions.save_subscription({k: v for k, v in row.items() if k != "confirmed"})
        if row["confirmed"]:
            db.mark_confirmed(row["email"])
    _p(f"[OK] 데모 구독자 {len(_SEED)}명 추가:")
    for row in _SEED:
        mark = "[v]" if row["confirmed"] else "[ ]"
        _p(f"     {mark} {row['email']} ({row['name']}, {', '.join(row['keywords'])})")
    _p("")
    _p("     박지후(jihu@demo.com)는 일부러 '확인 전' 상태 -> 관리자 화면에서 확인 여부 차이를 보여줄 수 있음")


def cmd_list(args):
    """구독자 목록을 표로 보여준다(관리자 대시보드의 CLI 버전)."""
    rows = db.fetch_all_subscribers()
    if not rows:
        _p("구독자가 없습니다. (python demo_helper.py reset 직후 정상)")
        return
    _p(f"구독자 {len(rows)}명:")
    _p(f"  {'확인':<4} {'이메일':<32} {'이름':<12} 키워드")
    _p("  " + "-" * 70)
    for r in rows:
        mark = "[v]" if r["confirmed"] else "[ ]"
        kws = ", ".join(r["keywords"])
        _p(f"  {mark:<4} {r['email']:<32} {(r['name'] or ''):<12} {kws}")
    _p("")
    _p("  [v] = 이메일 확인 완료(발송 대상), [ ] = 확인 전")


# ── 이메일 대체(확인 링크 / 본인확인 코드) ─────────────────────
def cmd_confirm_url(args):
    """확인 메일 링크를 출력한다(실제 메일 대신 쓰는 폴백)."""
    email = _need_email(args)
    token = db.fetch_confirm_token(email)
    if token is None:
        _p(f"[X] '{email}' 의 확인 토큰이 없습니다.")
        _p("    -> 이미 확인됐거나(재확인 불필요), 아직 구독 신청 전인 이메일입니다.")
        _p("    -> 먼저 프론트 /subscribe 에서 이 이메일로 구독 신청을 하세요.")
        return
    url = f"{_api_base()}/confirm?token={quote(token)}"
    _p("확인 링크(이 주소를 브라우저에 붙여넣고 '구독 확정하기' 버튼을 누르세요):")
    _p("")
    _p(f"    {url}")
    _p("")
    _p("설명용: 원래는 이 링크가 가입 확인 메일로 발송됩니다.")


def cmd_code(args):
    """본인확인 코드를 출력한다(실제 메일 대신 쓰는 폴백).

    프론트에서 '인증 코드 받기'를 이미 눌렀으면 그때 발급된 코드를, 안 눌렀으면 새로 발급해 출력.
    """
    email = _need_email(args)
    if subscriptions.get_subscription(email) is None:
        _p(f"[X] '{email}' 은 등록된 구독자가 아닙니다. 먼저 /subscribe 에서 신청하세요.")
        return
    code = db.peek_access_code(email)
    if not code:
        code = db.generate_access_code(email)
        note = "(새로 발급함)"
    else:
        note = "(프론트에서 방금 발급된 코드)"
    _p(f"본인확인 코드 {note}:")
    _p("")
    _p(f"    {code}")
    _p("")
    _p(f"유효 시간 {config.ACCESS_CODE_TTL_MINUTES}분. 프론트 '인증 코드' 칸에 이 값을 입력하세요.")
    _p("설명용: 원래는 이 코드가 본인확인 메일로 발송됩니다.")


# ── 발송시각 검증 시연(백엔드 최후 방어선) ─────────────────────
def cmd_bad_time(args):
    """프론트를 우회해 API 를 직접 두드렸을 때 백엔드가 잘못된 발송시각을 막는지 보여준다."""
    import requests
    base = _api_base()
    _p("프론트를 거치지 않고 API 를 직접 호출합니다(잘못된 클라이언트를 흉내).")
    _p(f"대상: {base}/subscribers")
    _p("")
    body = lambda **o: {"email": o.get("email", "badtime@demo.com"), "name": "경계값테스트",
                        "keywords": ["주식"], "send_hour": o.get("h", 8), "send_minute": o.get("m", 0)}
    try:
        r1 = requests.post(f"{base}/subscribers", json=body(email="bt15@demo.com", h=8, m=15), timeout=10)
        _p(f"  send_minute=15  -> {r1.status_code}  {_detail(r1)}")
        r2 = requests.post(f"{base}/subscribers", json=body(email="bt25@demo.com", h=25, m=0), timeout=10)
        _p(f"  send_hour=25    -> {r2.status_code}  (Pydantic 스키마가 0~24 범위 밖을 막음)")
        r3 = requests.post(f"{base}/subscribers", json=body(email="bt24@demo.com", h=24, m=0), timeout=10)
        _p(f"  send_hour=24    -> {r3.status_code}  (자정 표시값 '24:00' 은 허용)")
        # 24:00 로 실제 생성된 임시 구독자는 정리
        if r3.status_code == 201 and config.ADMIN_PASSWORD:
            requests.delete(f"{base}/subscribers/bt24@demo.com",
                            headers={"X-Admin-Password": config.ADMIN_PASSWORD}, timeout=10)
            _p("                     (24:00 임시 구독자는 자동 정리함)")
    except requests.RequestException as exc:
        _p(f"[X] API 에 연결하지 못했습니다: {exc}")
        _p(f"    -> 백엔드가 떠 있는지 확인하세요: uvicorn src.api:app --port 8000")
        return
    _p("")
    _p("핵심: 프론트 드롭다운이 애초에 30분 단위만 주고(원천 차단), 그걸 우회해도 백엔드가 한 번 더 막습니다.")


def _detail(resp):
    try:
        return resp.json().get("detail", "")
    except ValueError:
        return ""


# ── 파이프라인 시연 ───────────────────────────────────────────
_SAMPLE_DIGESTS = {
    "금리": [{
        "headline": "한국은행 기준금리 3.5% 동결",
        "topics": [
            {"topic": "동결 배경", "links": ["https://news.example/rate-1"],
             "topic_summary": "한국은행 금융통화위원회가 기준금리를 연 3.5%로 동결했다. 물가 둔화 흐름과 경기 하방 위험을 함께 고려한 결정이다."},
            {"topic": "시장 반응", "links": ["https://news.example/rate-2"],
             "topic_summary": "채권 금리는 소폭 하락했고, 시장은 연내 인하 가능성에 무게를 두는 분위기다."},
        ],
    }],
    "주식": [{
        "headline": "반도체株 강세로 코스피 상승 마감",
        "topics": [
            {"topic": "지수 흐름", "links": ["https://news.example/stock-1"],
             "topic_summary": "코스피가 반도체 대형주 강세에 힘입어 1.2% 오르며 마감했다."},
        ],
    }],
}
# 주간 트렌드 '별도' 메일용 샘플: 키워드별 토픽 + 요약 + 관련 기사 링크
_SAMPLE_TREND_ARTICLES = {
    "금리": [
        {"topic": "기준금리 동결", "days": 3,
         "summary": "한국은행이 기준금리를 연 3.5%로 3회 연속 동결했다. 물가와 경기 위험을 함께 고려한 결정이다.",
         "links": ["https://news.example/rate-1"]},
        {"topic": "연내 인하 전망", "days": 2,
         "summary": "채권 시장은 연말 기준금리 인하 가능성에 무게를 두는 분위기다.",
         "links": ["https://news.example/rate-2"]},
    ],
    "주식": [
        {"topic": "반도체 강세", "days": 2,
         "summary": "반도체 대형주가 이번 주 코스피 상승을 이끌었다.",
         "links": ["https://news.example/stock-1"]},
    ],
}


def cmd_pipeline_demo(args):
    """내장 샘플로 일간 뉴스레터 + '별도' 주간 트렌드 메일 HTML 두 개를 만든다(외부 키 불필요)."""
    from src.renderers import report
    email = _need_email(args, required=False) or "demo@example.com"
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_output")
    os.makedirs(out_dir, exist_ok=True)
    _p("[자체 완결 데모 모드] 외부 API/키 없이 파이프라인을 재현합니다.")
    _p("")
    _p("  [1/5] 수집       : 내장 샘플 뉴스 로드 (금리 2건 + 주식 1건)")
    _p("  [2/5] LLM 요약   : 데모용 내장 요약 사용 (실키 없이 이슈->주제 구조 시연)")
    daily = report.render(_SAMPLE_DIGESTS)
    daily_path = os.path.join(out_dir, "newsletter_demo.html")
    with open(daily_path, "w", encoding="utf-8") as f:
        f.write(daily)
    _p(f"  [3/5] 일간 렌더  : 일간 뉴스레터 HTML 생성 ({len(daily)}자)")
    weekly = report.render_weekly_trend(_SAMPLE_TREND_ARTICLES)
    weekly_path = os.path.join(out_dir, "weekly_trend_demo.html")
    with open(weekly_path, "w", encoding="utf-8") as f:
        f.write(weekly)
    _p(f"  [4/5] 주간 트렌드: 키워드+요약+관련 기사를 담은 '별도' 메일 HTML 생성 ({len(weekly)}자)")
    _p(f"  [5/5] 발송       : 데모 모드라 실제 발송 대신 파일로 저장 (수신자: {email})")
    _p("")
    _p(f"[OK] 일간 뉴스레터        : {daily_path}")
    _p(f"[OK] 주간 트렌드(별도 메일): {weekly_path}")
    _p("     -> 두 파일을 브라우저로 열어 각각의 메일을 보여주세요.")
    if "--open" in args:
        for p in (daily_path, weekly_path):
            try:
                os.startfile(p)  # Windows
            except Exception:
                pass


def cmd_norepeat_demo(args):
    """재발송 방지 시연 - 같은 구독자에게 이틀 발송해 '둘째 날엔 새 기사만' 나가는 걸 두 HTML로 대조.

    실 DB/메일 안 건드리고 임시 DB에서 실제 발송 경로(_drop_seen_articles->render)를 그대로 돌린다.
    """
    import tempfile
    from datetime import datetime
    import pytz
    from src import pipeline
    email = _need_email(args, required=False) or "demo@example.com"
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_output")
    os.makedirs(out_dir, exist_ok=True)
    KST = pytz.timezone(config.TIMEZONE)
    ARTS = [
        ("한국은행 기준금리 3.5% 동결", "https://news.example/rate"),
        ("코스피 반도체株 강세 마감", "https://news.example/stock"),
        ("원/달러 환율 1,320원 하락", "https://news.example/fx"),
    ]
    NEW = ("삼성전자 3분기 잠정 실적 발표", "https://news.example/samsung")

    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(tmp)
    saved = config.DB_PATH
    config.DB_PATH = tmp  # dispatch_one 내부 db 호출이 임시 DB 를 쓰도록
    saved_paths = []
    try:
        db.init_db(tmp)
        subscriptions.save_subscription({"email": email, "name": "재발송데모", "keywords": ["금리"],
                                         "send_hour": 8, "send_minute": 0, "frequency": "매일",
                                         "summary_length": "짧게", "language": "한국어"}, path=tmp)
        db.mark_confirmed(email, path=tmp)
        sub = subscriptions.get_subscription(email, path=tmp)

        def seed(articles, now):
            rows = [{"headline": h, "topic": h, "topic_summary": "요약 내용입니다.", "link": l}
                    for h, l in articles]
            db.save_digest("금리", "짧게", "한국어", rows, now=now, latest_article_at=now.isoformat(), path=tmp)

        def dispatch_save(now, fname):
            bodies = []
            orig = pipeline.send_email.send_email
            pipeline.send_email.send_email = lambda to, subject, body_html: bodies.append((subject, body_html))
            try:
                pipeline.dispatch_one(sub, now=now)
            finally:
                pipeline.send_email.send_email = orig
            daily = [b for s, b in bodies if "데일리" in s]
            if not daily:
                return None
            p = os.path.join(out_dir, fname)
            with open(p, "w", encoding="utf-8") as f:
                f.write(daily[0])
            return p

        tue = KST.localize(datetime(2026, 7, 7, 8, 0))   # 화요일(비앵커 - 주간 트렌드 안 섞임)
        wed = KST.localize(datetime(2026, 7, 8, 8, 0))
        thu = KST.localize(datetime(2026, 7, 9, 8, 0))
        _p("[재발송 방지 시연] 같은 구독자에게 3일 연속 발송을 재현합니다(임시 DB, 실메일 아님).")
        _p("")
        seed(ARTS, tue)
        p1 = dispatch_save(tue, "norepeat_day1.html")
        _p(f"  [1일차] 새 기사 3건 -> 3건 다 발송        : {p1}")
        seed(ARTS + [NEW], wed)
        p2 = dispatch_save(wed, "norepeat_day2.html")
        _p(f"  [2일차] 어제 3건 + 오늘 새 1건 -> '새 1건만' 발송: {p2}")
        seed(ARTS + [NEW], thu)
        p3 = dispatch_save(thu, "norepeat_day3.html")
        _p(f"  [3일차] 새 기사 없음 -> {'발송 안 함(스킵)' if p3 is None else p3}")
        saved_paths = [p for p in (p1, p2) if p]
    finally:
        config.DB_PATH = saved
        try:
            os.remove(tmp)
        except OSError:
            pass
    _p("")
    _p("[OK] 1일차·2일차 HTML 을 나란히 열어 보여주세요 - 1일차는 뉴스 3건, 2일차는 '새 기사'만 1건입니다.")
    if "--open" in args:
        for p in saved_paths:
            try:
                os.startfile(p)
            except Exception:
                pass


def cmd_pipeline_live(args):
    """실제 수집(NAVER)+LLM 요약+메일 발송을 한 구독자에게 돌린다(키/네트워크 필요, 보조자용)."""
    from src import pipeline
    email = _need_email(args)
    sub = subscriptions.get_subscription(email)
    if sub is None:
        _p(f"[X] '{email}' 은 등록된 구독자가 아닙니다. 먼저 /subscribe 에서 신청+확인하세요.")
        return
    if not sub.confirmed:
        _p(f"[X] '{email}' 은 아직 이메일 확인 전입니다(발송 대상 아님).")
        _p("    -> confirm-url 로 확인부터 끝내세요.")
        return
    if not sub.keywords:
        _p(f"[X] '{email}' 에 키워드가 없습니다.")
        return
    _p("[라이브 모드] 실제 NAVER 수집 + 실제 LLM 요약 + 실제 메일 발송을 실행합니다.")
    _p(f"  대상: {email} / 키워드: {sub.keywords}")
    _p("  (외부 API 응답에 따라 수십 초 걸릴 수 있습니다)")
    _p("")
    try:
        pipeline.run_for_subscriber(sub)  # 수집->요약->렌더->발송, 단계별 로그를 자체 출력
    except Exception as exc:
        _p(f"[X] 실행 중 오류: {exc}")
        _p("    -> .env 의 NAVER/LLM/SMTP 키가 모두 설정돼 있는지 확인하세요.")
        return
    _p("")
    _p("[OK] 라이브 파이프라인 완주. 수신함을 확인하세요.")


# ── 디스패처 ─────────────────────────────────────────────────
def _need_email(args, required=True):
    positional = [a for a in args if not a.startswith("-")]
    if positional:
        return subscriptions.normalize_email(positional[0])
    if required:
        _p("[X] 이메일을 인자로 주세요. 예: python demo_helper.py code alice@demo.com")
        sys.exit(1)
    return None


_COMMANDS = {
    "reset": cmd_reset,
    "seed": cmd_seed,
    "list": cmd_list,
    "confirm-url": cmd_confirm_url,
    "code": cmd_code,
    "bad-time": cmd_bad_time,
    "pipeline-demo": cmd_pipeline_demo,
    "norepeat-demo": cmd_norepeat_demo,
    "pipeline-live": cmd_pipeline_live,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        _p(__doc__)
        return
    cmd = sys.argv[1]
    if cmd not in _COMMANDS:
        _p(f"[X] 알 수 없는 명령: {cmd}")
        _p(f"    쓸 수 있는 명령: {', '.join(_COMMANDS)}")
        sys.exit(1)
    _COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
