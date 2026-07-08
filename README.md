# 데일리 금융 뉴스 브리핑

사용자가 **직접 고른 키워드**의 네이버 뉴스를, **각자 지정한 시각**에 이메일로 자동 발송하는 파이프라인 프로젝트.

## 역할 분담

| 포지션 | 담당 | 관련 모듈 |
|---|---|---|
| LLM/Agent | 검색·요약·편집 멀티에이전트 | `processors/summarizer.py` |
| **백엔드** | **스크래퍼·스케줄러·이메일 발송 서버** | **`collectors/`, `notifiers/`, `subscriptions.py`, `main.py`, `pipeline.py`, `config.py`** |
| 프론트 | 구독 신청 페이지·키워드/발송시간 대시보드 | `subscriptions.save_subscription()` / `delete_subscription()` 호출 |
| 기획/데이터 | 요약 가독성 검증·템플릿 디자인·프롬프트 인젝션 방어 | `renderers/report.py`, `templates/` |

> 모듈 간 경계는 **인터페이스(함수 시그니처)**와 **구독 레코드 형식**으로 고정되어 있어, 각 담당은 자기 영역만 채우면 된다.

## 핵심 설계 — 사용자별 키워드 · 발송 시간

- 발송 시간과 키워드는 **상수가 아니라 구독자별 데이터**다. (DB `subscribers` 테이블)
- 키워드는 프론트에서 **자유 입력**이다. 백엔드는 후보로 제한하지 않고 저장 시 공백/빈값/중복만 정리한다. (`config.SUGGESTED_KEYWORDS` 는 프론트 자동완성/예시용일 뿐 검증에 쓰이지 않는다.)
- 스케줄러는 **분 단위 디스패처**: 매 분마다 저장소를 읽어 "지금 발송할 구독자"만 처리한다. 대시보드에서 시간이 바뀌면 재시작 없이 즉시 반영된다.

### 발송 주기(frequency) — 발송 요일 + 되돌아보는 창(window)

뉴스레터는 "지난 발송 이후 ~ 이번 발송" 사이 소식을 커버해야 하므로, `frequency` 가 **(a) 발송 요일**과 **(b) 창(되돌아보는 시간)** 을 함께 결정한다. 발송 요일은 프론트에 요일 선택 UI가 생기기 전까지 **고정 규칙**(`config.FREQUENCY_WEEKDAYS`, 팀 합의로 변경 가능)을 쓴다.

| frequency | 발송 요일 | 창(window) |
|---|---|---|
| 매일 | 매일 | 24h |
| 주 3회 | 월·수·금 | 월=72h(직전 금), 수·금=48h |
| 매주 | 월요일 | 168h |

- **발송 요일 게이팅**(`is_due`): 시:분이 맞아도 그 주기의 발송 요일이 아니면 보내지 않는다. → '매주' 구독자가 매일 중복 수신하는 일이 없다.
- **창**(`send_window_hours`): "직전 발송 요일까지의 실제 간격"으로 계산한다. `dispatch_one` 이 이 창으로 DB 요약을 조회한다.
- 수집(`collect_job`)은 공용 저장소라 **가장 긴 주기(매주=168h)를 커버**하도록 `RECENCY_HOURS = 24*7` 로 넉넉히 둔다. (주간 커버리지를 늘리려면 `NEWS_DISPLAY` 상향 필요)

### 구독자 저장 — DB (`subscribers` 테이블)

구독자 정보는 **SQLite DB(`data/newsletter.db`)의 `subscribers` 테이블**에 저장한다. (과거엔 `data/subscriptions.json` 파일이었으나, 프론트가 쓰는 도중 백엔드가 읽으면 깨질 수 있어 동시성·일관성을 위해 DB로 이전.)

- **프론트(쓰기)**: `subscriptions.save_subscription(record)` 로 신청/수정, `delete_subscription(email)` 로 해지. `record` 는 아래 "구독 레코드 형식"과 동일한 dict. `email` 이 키라 같은 이메일로 다시 저장하면 갱신된다(중복 생성 없음).
- **백엔드(읽기)**: `load_subscriptions()` 가 DB에서 읽어 `Subscription` 리스트로 반환.
- **검증 이중 방어**: 쓸 때(`save_subscription`)도 검증하고, 읽을 때(`load_subscriptions`)도 잘못된 행은 그 행만 건너뛴다. → 한 명의 잘못된 값이 그 시각 전체 발송을 막지 않는다.
- **최초 시드**: 기존 `data/subscriptions.json` 이 있으면 `main.py` 첫 실행 시 DB가 비어 있을 때만 자동으로 가져온다(`import_from_json`). 이후엔 DB가 원천.

## 핵심 설계 — 3단계 배치 (DB 경유)

정기 발송은 **수집 → 요약 → 발송**을 한 번에 돌리지 않고, **SQLite DB(`data/newsletter.db`)를 사이에 둔 독립 배치 잡 3개**로 나눈다. 각 잡은 서로 다른 주기로 돌 수 있고, 단계 결과는 메모리가 아니라 DB로 넘긴다.

| 잡 | 주기 | 하는 일 | DB |
|---|---|---|---|
| `collect_job` | 매 N분 | 구독 키워드 뉴스 수집·정제 | → `articles` 저장 |
| `summarize_job` | 매 N분 | 구독 중인 (키워드, 요약 길이, 언어) 조합마다 최근 기사를 모아 LLM에게 이슈→주제 단위로 재구성시킴 | `articles` 조회 → `digests` 저장 |
| `dispatch_job` | 매 분 | 발송 대상 구독자에게 (자기 조합의) 최신 다이제스트 발송 | `digests` 조회 |

- **왜 나누나**: 수집·요약(느리고 비용 큼)을 발송(시간 정확도 중요)과 분리해, 무거운 작업이 발송 타이밍을 밀지 않게 한다.
- **이슈→주제→기사 계층**: LLM은 기사 1건당 요약 1건이 아니라, 여러 기사를 묶어 **핵심 이슈(headline) → 하위 주제(topic) 1~3개 → 주제별 요약 + 관련 기사(복수)** 구조로 편집한다. 한 키워드에 이슈가 여러 개, 한 주제에 관련 기사가 여러 건 나올 수 있다. `summarizer.summarize()`는 이를 평평한(flat) 행 리스트 `[{"headline","topic","topic_summary","link"}, ...]`로 반환하고, `db.group_digest_rows()`가 이슈→주제→링크로 묶는다.
- **구독자별 요약 길이/언어**: `summary_length`(짧게/중간/길게)·`language`(한국어/영어)는 구독자마다 다르다. `summarize_job`은 실제 구독 중인 (키워드, 요약 길이, 언어) 조합마다 별도로 다이제스트를 만든다.
- **다이제스트는 매번 새 스냅샷, 조합당 항상 최신 1건만 유지**: 기사 단위 "아직 요약 안 됨" 개념이 없다 — `summarize_job`이 돌 때마다 그 키워드에 보유 중인 기사 전체를 다시 넘겨 새 `digests` 레코드를 만든다. 발송은 조합별로 **가장 최근** 다이제스트만 쓰므로, `save_digest()`가 새 스냅샷을 넣는 즉시 같은 (키워드, 요약 길이, 언어) 조합의 예전 스냅샷을 지운다(하위 issue/topic/link 는 `FK ON DELETE CASCADE` 로 함께 삭제) — 30분마다 다시 돌아도 조합당 테이블에 항상 1건만 남아 무한히 쌓이지 않는다. 그 최신 것조차 오래됐으면(구독자 창(window) 밖) 발송 시 건너뛴다.
- **DB 스키마**: `subscribers`(구독자) · `articles`(정제 뉴스) → `digests`(요약 생성 1회, 조합별) → `digest_issues`(헤드라인, 다이제스트당 여러 개) → `digest_topics`(주제+요약, 이슈당 1~3개) → `digest_links`(관련 기사, 주제당 여러 개).
- **속보 발송**은 시간과 무관한 즉시 발송이라 DB를 거치지 않고 그때그때 동기적으로 요약·발송한다(`send_breaking_alert`).

## 디렉터리 구조

```
team_project/
├── main.py                     # [백엔드] 스케줄러 진입점 (수집/요약/발송/속보 잡 등록)
├── data/
│   ├── subscriptions.json       # [프론트] 구독자 초기 시드용 JSON (개인정보 → .gitignore)
│   ├── subscriptions.example.json  # 예시 (커밋)
│   └── newsletter.db            # [백엔드] 구독자/뉴스/요약 SQLite DB (런타임 생성 → .gitignore)
├── src/
│   ├── config.py               # [백엔드] .env 로드 + 고정 상수(키워드 후보/시간대/DB 경로)
│   ├── subscriptions.py        # [백엔드] 구독 모델·검증 + 저장/조회(save/delete/load)
│   ├── db.py                   # [백엔드] SQLite 저장소 (subscribers/articles/digests 계층)
│   ├── collectors/
│   │   └── naver_news.py        # [백엔드] 네이버 뉴스 수집
│   ├── processors/
│   │   └── summarizer.py        # [LLM/Agent] 요약·편집   (summarize 인터페이스)
│   ├── renderers/
│   │   └── report.py            # [기획/데이터] 렌더링      (render 인터페이스)
│   ├── templates/
│   │   └── daily_report.html    # [기획/데이터] 메일 HTML 템플릿
│   ├── notifiers/
│   │   └── send_email.py        # [백엔드] Gmail SMTP 발송
│   ├── pipeline.py              # [백엔드] 배치 잡(collect/summarize/dispatch) + 속보 발송
│   └── api.py                   # [백엔드] 구독자 REST API (FastAPI, Swagger /docs)
├── tests/                      # 단위 테스트
├── requirements.txt / .env.example / .gitignore
```

## 데이터 흐름 (인터페이스 계약)

```
① collect_job() — 매 N분 (백엔드)
     load_subscriptions() → 전체 키워드 합집합
     └─ collectors.naver_news.collect(keywords)   → {keyword: [cleaned_item]}   # 백엔드
        └─ db.save_articles(...)                   → articles 테이블

② summarize_job() — 매 N분 (백엔드 + LLM/Agent)
     triples = {(keyword, sub.summary_length, sub.language) for sub in 구독자 for keyword in sub.keywords}
     └─ (키워드, 요약 길이, 언어) 조합마다:
          db.fetch_articles_for_keyword(keyword)          → [cleaned_item, ...]  # 그 키워드의 보유 기사 전체
          └─ processors.summarizer.summarize({keyword: [...]}, summary_length, language)
                                                           → {keyword: [{headline, topic, topic_summary, link}, ...]}  # LLM/Agent
             └─ db.save_digest(keyword, summary_length, language, rows)
                  └─ db.group_digest_rows(rows)           → 이슈→주제→링크 계층으로 묶음
                  └─ digests/digest_issues/digest_topics/digest_links 테이블에 새 스냅샷 저장

③ dispatch_job() — 매 분 (백엔드 + 기획/데이터)
     due_subscribers(subs, now)                    → 지금 발송할 구독자(시:분 + 주기별 발송 요일)
     └─ 각 구독자마다 dispatch_one(sub):
          hours = send_window_hours(sub, now)            → 주기별 되돌아보는 창
          db.fetch_digests_for_keywords(sub.keywords, sub.summary_length, sub.language, hours=hours)
                                                           → {keyword: [{headline, topics: [{topic, topic_summary, links}]}, ...]}
          ├─ renderers.report.render(dict)         → html(str)                  # 기획/데이터
          └─ notifiers.send_email.send_email(sub.email, subject, html)          # 백엔드
```

- `cleaned_item`: `{title, link, description, published_at}` (수집 단계에서 태그·엔티티 제거, 날짜 파싱 완료. 날짜 불명 기사는 트렌드 왜곡 방지를 위해 제외)
- LLM/Agent · 기획/데이터 파트는 현재 임시 스텁이라, 백엔드 파이프라인은 지금도 끝까지 동작한다.
- `summarize(collected, summary_length, language)` — 구독자가 고른 요약 길이/언어를 반영해, 여러 기사를 이슈→주제로 재구성한 평평한 행 리스트 `[{"headline","topic","topic_summary","link"}, ...]`를 반환해야 하는 인터페이스. (summary_length/language 를 실제 LLM 프롬프트에 어떻게 반영할지는 LLM/Agent 담당 몫 — 백엔드는 값 전달과 저장까지만 책임진다.)
- `render(digests)` — `{query: [{"headline","topics":[{"topic","topic_summary","links"}]}]}` 형태의 이슈 계층을 받는 인터페이스.

## 설치

```bash
pip install -r requirements.txt
```

## 설정

**1) 환경 변수** — `.env.example` 을 `.env` 로 복사한 뒤 값을 채운다.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|---|---|
| `SENDER` | 발신 Gmail 주소 |
| `GOOGLE_APP_PASSWORD` | Google 앱 비밀번호 |
| `NAVER_CLIENT_ID` | 네이버 검색 API Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 검색 API Client Secret |
| `ANTHROPIC_API_KEY` | (선택, LLM/Agent) LLM 요약 연동 시 |

**2) 구독자 데이터** — 구독자는 DB(`subscribers` 테이블)에 저장된다. 실서비스에선 프론트 대시보드가 `save_subscription()` 으로 관리한다. 로컬에서 빠르게 채우려면 예시 JSON을 복사해 두면 되고, `main.py` 첫 실행 시 DB가 비어 있으면 자동으로 가져온다(시드).

```bash
cp data/subscriptions.example.json data/subscriptions.json   # (선택) 최초 시드용
```

## 실행

```bash
# 디스패처 시작 (수집/요약/발송/속보 잡)
python main.py

# 구독자 API 서버 (Swagger UI: http://localhost:8000/docs)
uvicorn src.api:app --reload

# 단위 테스트
python -m pytest
```

> ⚠️ 실행 시 한글이 깨져 보이면 콘솔 인코딩 문제입니다. `chcp 65001` 또는 환경변수 `PYTHONUTF8=1` 설정으로 해결됩니다.

## 구독자 API (REST)

프론트/관리자가 구독자를 관리하는 HTTP 엔드포인트. `uvicorn src.api:app --reload` 로 띄우고 **`/docs` 의 Swagger UI** 에서 바로 테스트할 수 있다. 저장·검증은 `subscriptions.py`(→ `db.py`)를 재사용하며, 검증 실패는 400으로 응답한다.

| 메서드 | 경로 | 설명 | 실패 |
|---|---|---|---|
| `POST` | `/subscribers` | 신규 구독 | 이미 있으면 409, 값 오류 400 |
| `GET` | `/subscribers` | 전체 구독자 조회 (**관리자 전용**) | 인증 실패 401, 이미 있으면 409, 값 오류 400 |
| `GET` | `/subscribers/{email}` | 구독 정보 조회 | 없으면 404 |
| `PUT` | `/subscribers/{email}` | 구독자 정보 수정(전체 교체) | 없으면 404, 값 오류 400 |
| `DELETE` | `/subscribers/{email}` | 구독 취소 | 없으면 404 |

- **관리자 인증**: `GET /subscribers`는 관리자 전용이다. `.env`의 `ADMIN_PASSWORD`와 같은 값을 **`X-Admin-Password` 헤더**로 보내야 한다. 헤더가 없거나 값이 틀리면 401. Swagger UI에서는 우측 상단 **Authorize** 버튼에 비밀번호를 넣으면 이후 요청에 자동으로 실린다. `ADMIN_PASSWORD`가 서버에 아예 설정되지 않았으면(운영 실수 방지) 어떤 값을 보내도 401 — "설정 안 됨"을 "누구나 통과"로 취급하지 않는다.
- **발송 시각 규칙**: `send_hour` 0~24, `send_minute` 는 30분 단위(0 또는 30). 어기면 400.
- **본문 예시**(POST/PUT): `{"email":"a@x.com","name":"홍길동","keywords":["주식","금리"],"send_hour":8,"send_minute":30}`
- `keywords` 는 자유 입력이다. 저장 시 공백/빈값/중복만 정리되고, 후보 제한은 없다.

### 프론트(Streamlit) 연동

Streamlit은 브라우저 JS가 아니라 **서버 사이드 Python에서 직접 호출**하는 구조라 CORS가 필요 없다 — Streamlit 프로세스가 `requests`로 이 API를 그냥 서버 대 서버로 호출하면 된다. 연동에 필요한 값 두 가지는 `.env`에서 그대로 읽는다(같은 저장소를 쓰는 팀 프로젝트라 별도 전달 체계 없이 공유):

| 값 | 용도 |
|---|---|
| `API_BASE_URL` | 이 API가 떠 있는 주소(`http://localhost:8000` 등). 배포 시 주소가 바뀌어도 코드는 안 건드리고 `.env`만 바꾸면 됨 |
| `ADMIN_PASSWORD` | 관리자 화면(`GET /subscribers`)을 부를 때 `X-Admin-Password` 헤더에 실을 값 |

```python
import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# 구독 신청 (일반 사용자 — 인증 불필요)
res = requests.post(f"{API_BASE_URL}/subscribers", json={
    "email": "user@example.com",
    "keywords": ["주식", "금리"],
    "send_hour": 8, "send_minute": 30,
})
res.raise_for_status()  # 400/409 등은 예외로 올라옴 — res.json()["detail"]에 사유

# 전체 구독자 조회 (관리자 화면 — 인증 필요)
res = requests.get(
    f"{API_BASE_URL}/subscribers",
    headers={"X-Admin-Password": ADMIN_PASSWORD},
)
subscribers = res.json()
```

- 일반 구독 신청/수정/해지(`POST`/`PUT`/`DELETE`)는 인증이 필요 없다 — 이메일 자체가 식별자다.
- 관리자 화면(`GET /subscribers`)만 위처럼 헤더를 실어야 한다. 헤더 누락·비밀번호 오류는 401, `.env`에 `ADMIN_PASSWORD`가 아예 없으면 서버가 항상 401을 돌려준다.
