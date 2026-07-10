# 🖥️ 백엔드 — 스크래퍼·스케줄러·구독자 API

> 담당: 백엔드 (스크래퍼·스케줄러·이메일 발송 서버·구독자 API) <br>
> 역할: 사용자가 직접 고른 키워드의 네이버 뉴스를 수집·정제하고, LLM/Agent가 만든 요약을
> 각자 지정한 시각에 이메일로 자동 발송하는 파이프라인을 구동합니다.

> **서비스 정의** — *관심 있는 분야가 있지만 뉴스 챙길 시간은 없는 직장인*을 위한, 아침 3분짜리 개인 뉴스레터입니다. <br>
> 내가 고른 키워드만 · 내가 고른 길이/언어로 · **같은 기사는 두 번 보내지 않고**(재발송 방지) 받아보고,
> 주 1회는 "이번 주 핵심" 트렌드를 회고합니다. 모든 개인화·정제가 "당신의 시간을 아낀다"는 한 축으로 정렬되어 있습니다.

---

<details>
<summary><b>📁 파일 구성</b></summary>

```
team_project/
├── main.py                     # 스케줄러 진입점 (수집/요약/발송 작업 등록)
├── future/                     # 아직 미도입 기능 (스케줄러 미등록, 코드·테스트만 준비)
│   ├── breaking.py              # 속보 감지 (급증/긴급 키워드 → 이벤트)
│   └── test_breaking.py
├── data/
│   ├── subscriptions.json       # 구독자 초기 시드용 JSON
│   ├── subscriptions.example.json  # 예시
│   └── newsletter.db            # 구독자/뉴스/요약 SQLite DB (실행 시 생성)
├── src/
│   ├── config.py                # .env 로드 + 고정 상수(키워드 후보/시간대/DB 경로)
│   ├── subscriptions.py         # 구독 모델·검증 + 저장/조회(save/delete/load)
│   ├── db.py                    # SQLite 저장소 (subscribers/articles/digests 계층)
│   ├── collectors/naver_news.py # 네이버 뉴스 수집
│   ├── processors/summarizer.py # 요약·편집 인터페이스
│   ├── renderers/report.py      # 렌더링 인터페이스
│   ├── templates/daily_report.html  # 메일 HTML 템플릿
│   ├── notifiers/send_email.py  # Gmail SMTP 발송
│   ├── pipeline.py               # 배치 작업(collect/summarize/dispatch) + 속보 발송
│   └── api.py                    # 구독자 REST API (FastAPI, Swagger /docs)
└── requirements.txt / .env.example / .gitignore
```

</details>

---

<details>
<summary><b>🧩 역할 분담</b></summary>

| 포지션 | 담당 | 관련 모듈 |
|---|---|---|
| LLM/Agent | 검색·요약·편집 멀티에이전트 | `processors/summarizer.py` → `LLM_fn.py`(요약/QA Agent 구현체) |
| **백엔드** | **스크래퍼·스케줄러·이메일 발송 서버·구독자 API** | **`collectors/`, `notifiers/`, `subscriptions.py`, `main.py`, `pipeline.py`, `config.py`, `api.py`** |
| 프론트 | 구독 신청 페이지·키워드/발송시간 대시보드 | REST API(`api.py`) 호출 (`newsletter_project/utils.py`) |
| 기획/데이터 | 요약 가독성 검증·템플릿 디자인·프롬프트 인젝션 방어 | `renderers/report.py', `templates/` |

**기술 스택**

| 기술 | 설명/용도 |
|---|---|
|Python| 프론트엔드, 백엔드, LLM 로직 작성 언어 |
| FastAPI | 백엔드 API 프레임워크| 
| SQLite | 구독자 정보, 기사, 요약본 저장용 RDB| 
| APScheduler | 이메일 발송 스케줄러| 
|네이버 오픈API (검색) | 뉴스 기사 수집 |
| Gmail SMTP | 이메일 발송 |

</details>

---

<details>
<summary><b>⚙️ 설치 및 환경 설정</b></summary>

### 필요 패키지

```bash
pip install -r requirements.txt
```

### 환경 변수

`.env.example`을 `.env`로 복사한 뒤 값을 채워주세요.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|---|---|
| `SENDER` | 발신 Gmail 주소입니다. |
| `GOOGLE_APP_PASSWORD` | Google 앱 비밀번호입니다. |
| `NAVER_CLIENT_ID` | 네이버 검색 API Client ID입니다. |
| `NAVER_CLIENT_SECRET` | 네이버 검색 API Client Secret입니다. |
| `OPENAI_API_KEY` | LLM/Agent 요약(`LLM_fn.py`)용 **OpenRouter** 키입니다. OpenAI SDK 형식이라 변수명만 그대로 쓰고, `base_url`은 OpenRouter로 향합니다. |
| `ADMIN_PASSWORD` | 관리자 전용 엔드포인트(`GET /subscribers`) 인증에 씁니다. 미설정 시 항상 401을 반환합니다(안전 기본값). |
| `API_BASE_URL` | 이 API의 외부 접근 주소입니다. 구독 확인 메일 링크(`GET /confirm`) 생성에도 쓰입니다. |
| `ACCESS_CODE_TTL_MINUTES` | 셀프서비스 본인 확인 코드의 유효 시간(분)이며, 기본값은 15입니다. |

- 키가 없으면 해당 기능 호출 시 401 또는 오류가 발생합니다.

### 구독자 데이터 시드 (선택)

구독자는 DB(`subscribers` 테이블)에 저장됩니다. 로컬에서 빠르게 채우려면 예시 JSON을 복사해두면, `main.py` 첫 실행 시 DB가 비어 있을 때만 자동으로 가져옵니다.

```bash
cp data/subscriptions.example.json data/subscriptions.json
```

### 실행

```bash
# 디스패처 시작 (수집/요약/발송 작업)
python main.py

# 구독자 API 서버 (Swagger UI: http://localhost:8000/docs)
uvicorn src.api:app --reload
```

> ⚠️ 실행 시 한글이 깨져 보인다면, 인코딩 문제입니다. 발생 시 `chcp 65001` 또는 환경변수 `PYTHONUTF8=1` 설정을 통해 해결 가능합니다.

</details>

---

<details>
<summary><b>🔧 핵심 설계 — 구독자별 키워드·발송 시간</b></summary>

- 발송 시간과 키워드는 구독자마다 다르게 설정합니다(DB `subscribers` 테이블에 저장됨).
- 키워드는 구독자가 자유롭게 입력하도록 구현하였습니다. 백엔드는 키워드 저장 시 키워드 정제(공백/빈값/중복 정리) 수행 후 저장합니다.
- 스케줄러는 분 단위 디스패처로, 매 분마다 DB를 조회하여 지금 발송할 구독자만 처리합니다. 대시보드에서 시간이 바뀌면 재시작 없이 즉시 반영됩니다.

### 발송 주기(frequency)

`frequency`가 (a) 발송 요일과 (b) 기사 포함 기간을 함께 결정합니다. 발송 요일의 경우 매주의 경우 월요일, 주 3회의 경우 월,수,금으로 고정되어 있습니다. (프론트에서 요일 선택 구현 시 변경 가능)

| frequency | 발송 요일 | 포함되는 기사(발송 시각 기준) |
|---|---|---|
| 매일 | 매일 | 24시간 이내 작성된 기사 |
| 주 3회 | 월·수·금 | 월=72시간(직전 금), 수·금=48시간 이내에 작성된 기사 |
| 매주 | 월요일 | 168식 (7일) 이내 작성도니 기사 |

- **발송 요일 필터링**(`is_due`): 그 주기의 발송 요일이 아니라면 발송 시각이 되어도 이메일이 발송되지 않도록 하여, 매주 혹은 주 3회로 설정한 구독자가 발송 요일이 아닐때 이메일을 수신하지 않도록 합니다.
- **발송에 포함되는 기사 판정**(`send_window_hours`): 오늘부터 직전 발송 요일까지 지난 일수에 24를 곱해서, 이번 발송에 몇 시간 전 기사까지 포함할지를 계산합니다. `dispatch_one`이 이 포함 기간으로 DB 요약을 조회합니다.
- 수집(`collect_job`)은 공용 DB이므로 가장 긴 발송 주기(매주=168h)까지 커버할 수 있도록 `RECENCY_HOURS = 24*7`로 넉넉히 설정합니다. 

### 구독자 저장

구독자 정보는 SQLite DB(`data/newsletter.db`)의 `subscribers` 테이블에 저장됩니다.

- **프론트(쓰기)**: `subscriptions.save_subscription(record)`로 신청/수정, `delete_subscription(email)`로 해지합니다. `email`이 키라 같은 이메일로 다시 저장하면 갱신됩니다(중복 생성 없음).
- **백엔드(읽기)**: `load_subscriptions()`가 DB에서 읽어 `Subscription` 리스트로 반환합니다.
- **검증 이중 방어**: 쓸 때(`save_subscription`)도 검증하고, 읽을 때(`load_subscriptions`)도 잘못된 행은 그 행만 건너뜁니다. 한 명의 잘못된 값이 그 시각 전체 발송을 막지 않습니다.
- **최초 시드**: 기존 `data/subscriptions.json`이 있으면 `main.py` 첫 실행 시 DB가 비어 있을 때만 자동으로 가져옵니다(`import_from_json`). 이후엔 DB가 원천입니다.

</details>

---

<details>
<summary><b>🗂️ 핵심 설계 — 3단계 배치 파이프라인</b></summary>

정기 발송은 수집 → 요약 → 발송을 한 번에 돌리지 않고, SQLite DB를 사이에 둔 독립 배치 작업 3개로 나눕니다. 각 작업은 서로 다른 주기로 돌 수 있고, 단계 결과는 DB로 넘어갑니다.

| 작업 | 주기 | 하는 일 | DB |
|---|---|---|---|
| `collect_job` | 매 N분 | 구독 키워드 뉴스 수집·정제 | → `articles` 저장 |
| `summarize_job` | 매 N분 | 구독 중인 (키워드, 요약 길이, 언어) 조합마다 최근 기사를 모아 LLM에게 이슈→주제 단위로 재구성시킵니다 | `articles` 조회 → `digests` 저장 |
| `dispatch_job` | 매 분 | 발송 대상 구독자에게 자기 조합의 최신 요약본을 발송합니다 | `digests` 조회 |

```
① collect_job() — 매 N분
     load_subscriptions() → 전체 키워드 합집합
     └─ collectors.naver_news.collect(keywords)   → {keyword: [cleaned_item]}
        └─ db.save_articles(...)                   → articles 테이블

② summarize_job() — 매 N분
     (키워드, 요약 길이, 언어) 조합마다:
     db.fetch_articles_for_keyword(keyword)          → [cleaned_item, ...]
     └─ processors.summarizer.summarize({keyword: [...]}, summary_length, language)
                                                      → {keyword: [{headline, topic, topic_summary, link}, ...]}
        └─ db.save_digest(keyword, summary_length, language, rows)
             └─ db.group_digest_rows(rows)           → 이슈→주제→링크 계층으로 묶음
             └─ digests/digest_issues/digest_topics/digest_links 테이블에 새 스냅샷 저장

③ dispatch_job() — 매 분
     due_subscribers(subs, now)                    → 지금 발송할 구독자(시:분 + 주기별 발송 요일)
     각 구독자마다 dispatch_one(sub):
     hours = send_window_hours(sub, now)            → 주기별 포함 기간
     db.fetch_digests_for_keywords(sub.keywords, sub.summary_length, sub.language, hours=hours)
                                                      → {keyword: [{headline, topics: [{topic, topic_summary, links}]}, ...]}
     ├─ renderers.report.render(dict)         → html(str)
     └─ notifiers.send_email.send_email(sub.email, subject, html)
```

- **작업 분리 이유**: 수집·요약(느리고 비용 큼)을 발송(시간 정확도 중요)과 분리해서, 무거운 작업이 발송 타이밍을 지연시키지 않게 합니다.
- **이슈→주제→기사 계층**: LLM은 여러 기사를 묶어 핵심 이슈 → 하위 주제 1~3개 → 주제별 요약 + 관련 기사 여러 건 구조로 편집합니다. `summarizer.summarize()`는 이를 평평한 행 리스트 `[{"headline","topic","topic_summary","link"}, ...]`로 반환하고, `db.group_digest_rows()`가 이슈→주제→링크로 묶습니다.
- **구독자별 요약 길이/언어**: `summary_length`(짧게/중간/길게)·`language`(한국어/영어)는 구독자마다 다릅니다. `summarize_job`은 실제 구독 중인 조합마다 별도로 요약본을 만듭니다.
- **요약본은 매번 새 스냅샷으로 재생성되고, `DIGEST_RECENCY_HOURS`(기본 8일)만큼 이력이 보존됩니다**: 발송(`fetch_digests_for_keywords`)은 조합당 최신 스냅샷만 쓰지만, 주간 트렌드 키워드 집계(아래)가 지난 며칠치 이력을 훑어야 해서 즉시 지우지 않습니다. `save_digest()`가 같은 조합으로 다시 저장될 때 그 보존 기간보다 오래된 것만 정리하고(하위 issue/topic/link는 `FK ON DELETE CASCADE`), 갱신이 끊긴 조합의 이력은 `summarize_job` 실행마다 도는 `db.prune_old_digests()`가 정리합니다.
- **`articles`도 자체 정리됩니다**: `collect_job`이 새 기사를 저장한 직후 `db.prune_old_articles()`를 호출해 `RECENCY_HOURS`(기본 7일)보다 오래된 기사를 지웁니다.
- **발송은 '포함 기간 안에 실제 새 기사가 있을 때만'**: 다이제스트는 담은 기사 중 가장 최근 발행일을 `digests.latest_article_at`로 저장합니다. `fetch_digests_for_keywords`는 스냅샷 생성 시각(`created_at`, 30분마다 재요약돼 늘 최신)이 아니라 이 값이 구독자 포함 기간(일간 24h/매주 168h) 안인지로 신선도를 판정합니다 — 그래서 새 뉴스가 없으면 같은 옛 기사를 매일 "오늘의 뉴스"로 반복 발송하지 않습니다(예전 스냅샷은 값이 없어 `created_at`으로 폴백).

### 주간 트렌드 키워드

추가적으로 주기(`매일`/`주 3회`/`매주`)와 무관하게 한 주에 한 번 "이번 주 트렌드 키워드"를 알려주는 메일이 기존 뉴스레터와는 별개로 발송됩니다. 첨부 요일은 별도 상수가 아니라 각 구독자의 발송 요일 규칙에서 도출합니다.

- `subscriptions.is_weekly_anchor(sub, now)`: `now`가 그 구독자의 '이번 주 첫 발송 요일'(`FREQUENCY_WEEKDAYS[sub.frequency]`의 가장 이른 요일)인지 판정합니다. 발송 요일 규칙이 바뀌어도 트렌드 첨부가 자동으로 따라가므로, `TREND_WEEKDAY` 같은 별도 하드코딩 상수와 어긋날 일이 없습니다.
- `db.get_top_topic_articles(keyword, since, language=...)`: 그 키워드로 `TREND_LOOKBACK_HOURS`(기본 7일) 이내 쌓인 다이제스트의 `topic`을 **서로 다른 관련 기사 수**(`COUNT(DISTINCT dl.link)`) 기준으로 상위 `TREND_TOP_N`개 뽑고, 각 토픽의 요약과 **관련 기사 링크**를 함께 반환합니다(그 주 다이제스트에 저장된 것을 재사용 — 추가 수집/LLM 없음). 기사 수로 세는 이유: `summarize_job`이 30분마다 같은 기사를 재요약해 스냅샷을 새로 만들어서, 스냅샷/등장일로 세면 하루짜리 뉴스도 최대 7일로 부풀려집니다. 같은 기사는 같은 링크라 링크 중복을 제거해 세면 그 팽창에 면역입니다. `summary_length`는 구분하지 않지만 `language`는 topic·요약이 언어별 문자열이라 그 언어 다이제스트만 훑습니다(다른 언어 혼입 방지). (순위만 쓰는 옛 함수 `db.get_top_topics`(등장일 기준)도 남아 있으나 발송 경로엔 안 씁니다.)
- `pipeline.weekly_trend_articles_for(keywords, now, language=..., cache=...)`: 키워드별 집계를 모아 이력이 있는 키워드만 돌려줍니다. `dispatch_job`이 넘기는 캐시로 같은 실행 안에서 구독자들이 공유하는 `(키워드, 언어)` 조합의 중복 쿼리를 막습니다.
- `dispatch_one`은 `is_weekly_anchor`가 참이면 `weekly_trend_articles_for`로 트렌드를 모아 `report.render_weekly_trend(...)`로 렌더링한 뒤, 일간 뉴스레터와 **별도의 메일**(`[주간 트렌드] ...`)로 보냅니다(일간·주간을 한 번의 슬롯 선점으로 묶되 각 발송은 격리 — 한쪽 실패가 다른 쪽을 막지 않음). 표시할 땐 순위 순서로만 보여주고 기사 수는 노출하지 않습니다.
- **한계**: LLM이 매 실행마다 topic 문구를 새로 지어내므로, 같은 사안이라도 표현이 갈리면 따로 집계됩니다(의미 기반 클러스터링이 아닌 문자열 근사치).
### 재발송 방지 (같은 기사 두 번 안 보내기)

*"매일 새 것만"* — 구독자별로 이미 받은 기사를 기록해 두고 다음 발송에서 뺀다. 뺀 뒤 새 기사가 하나도 없으면 그 일간 메일은 아예 보내지 않는다.

- `sent_articles(email, link, sent_at)` 발송 내역: **발송에 성공한** 기사 링크를 구독자별로 기록(정규화된 링크). `SENT_ARTICLE_RETENTION_HOURS`(8일)만큼 보존해 매주 구독자도 지난주 기사가 이번 주에 다시 안 나가게 하고, `collect_job`이 주기적으로 정리한다.
- `db._normalize_link`: `utm_*`·`fbclid` 등 추적 파라미터만 제거하고 쿼리 파라미터를 정렬 — 추적 파라미터·파라미터 순서만 다른 같은 기사를 같은 것으로 본다. 네이버 `oid`/`aid` 같은 식별 쿼리는 보존해 서로 다른 기사가 뭉개지지 않는다.
- `pipeline._drop_seen_articles`: `dispatch_one`이 발송 직전(claim·빈검사 **앞에서**) 이미 받은 링크를 뺀다. 한 topic 의 링크가 전부 이미 본 것이면 그 topic(=이미 읽은 뉴스)을 통째로 뺀다. topic 은 같은 사건을 다룬 기사 묶음이라 대표 링크뿐 아니라 그 묶음 링크 전부를 '받음'으로 기록한다(대표만 기록하면 같은 사건이 다음 날 다른 URL로 재발송됨). **일간 발송 성공 뒤에만** 기록하고, 기록만 실패해도(DB 락 등) 발송 자체는 성사로 처리한다.
- 위 '포함 기간 안에 새 기사가 있을 때만'(`latest_article_at`)과 층이 다르다 — 그건 "이 다이제스트에 새 기사가 있나(전체)", 재발송 방지는 "그중 이 사람이 안 본 게 있나(개인)". 둘이 합쳐져 *새 것 없으면 메일도 안 온다*가 된다.
- 주간 트렌드(회고성)는 이 필터 대상이 아니다 — 이번 주 핵심을 다시 짚어주는 게 목적이라 의도적으로 중복을 허용한다.

- **DB 스키마**: `subscribers`(구독자) · `articles`(정제 뉴스) → `digests`(조합별 요약 스냅샷; `latest_article_at`로 신선도 판정) → `digest_issues`(헤드라인) → `digest_topics`(주제+요약) → `digest_links`(관련 기사) · `sent_articles`(구독자별 재발송 방지 기록).
- **속보 발송**(`send_breaking_alert`)은 시간과 무관한 즉시 발송이라 DB를 거치지 않고, 그때그때 동기적으로 요약·발송합니다. (현재는 틀만 잡힌 상태로, 기능 추가 시 반영 예정)

</details>

---

<details>
<summary><b>🔌 구독자 API 인터페이스 계약</b></summary>

`uvicorn src.api:app --reload`로 띄우고 `/docs`의 Swagger UI에서 바로 테스트할 수 있습니다. 저장·검증은 `subscriptions.py`(→ `db.py`)를 재사용하며, 검증 실패는 400으로 응답합니다. 모든 엔드포인트에 IP 기준 속도 제한이 걸려 있어, 초과하면 아래 표에 없는 **429**가 반환될 수 있습니다.

| 메서드 | 경로 | 설명 | 실패 |
|---|---|---|---|
| `GET` | `/options` | frequency/summary_length/language 선택지 조회 (인증 불필요) | — |
| `POST` | `/subscribers` | 신규 구독 (미확인 상태로 저장 + 확인 메일 발송) | 이미 확인된 이메일이면 409, 값 오류 400 |
| `GET` | `/confirm?token=...` | 이메일 구독 확인 **안내 페이지** (상태 불변) | 토큰 무효/재사용 시 400 |
| `POST` | `/confirm` | 안내 페이지 버튼이 폼(`token`)으로 확정 → `confirmed=True` | 토큰 무효/재사용 시 400 |
| `GET` | `/subscribers` | 전체 구독자 조회 (관리자 전용) | 인증 실패 401 |
| `POST` | `/subscribers/{email}/access-code` | 본인 확인 코드 이메일 발송 (셀프서비스 전 필요) | 없는 이메일이어도 항상 202 (존재 여부 비노출) |
| `GET` | `/subscribers/{email}` | 구독 정보 조회 (관리자 또는 본인) | 인증 실패 401, 없으면 404 |
| `PUT` | `/subscribers/{email}` | 구독자 정보 수정(전체 교체) (관리자 또는 본인) | 인증 실패 401, 없으면 404, 값 오류 400 |
| `DELETE` | `/subscribers/{email}` | 구독 취소 (관리자 또는 본인) | 인증 실패 401, 없으면 404 |

- **이메일 확인**: `POST /subscribers`는 구독자를 `confirmed=False`로 저장하고, 확인 메일을 보냅니다. 그 링크(`GET /confirm`)를 열면 확인 안내 페이지만 뜨고(링크 사전열람만으론 확정되지 않게 함), 그 페이지의 버튼이 보내는 `POST /confirm`이 `confirmed=True`로 확정해 정기/속보 발송 대상이 됩니다. 토큰은 1회용이라 확인 후 폐기되며, 재사용하면 400을 반환합니다. 이미 확인된 이메일로 다시 신청하면 409지만, 아직 미확인인 이메일로 재신청하면 409 대신 확인 메일을 재전송합니다(같은 토큰 재사용).
- **발송 시각 규칙**: `send_hour`는 0~24, `send_minute`은 30분 단위(0 또는 30)입니다. 어기면 400을 반환합니다(생략하면 필수값 누락으로 422).
- **"없으면 404"의 예외**: 관리자(비밀번호)는 없는 이메일에 404를 받지만, 본인 확인 코드로 접근하면 없는 이메일은 소유 증명이 불가해 401이 됩니다(GET/PUT/DELETE `/subscribers/{email}` 공통).
- **본문 예시**(POST/PUT): `{"email":"a@x.com","name":"홍길동","keywords":["주식","금리"],"send_hour":8,"send_minute":30}`
- `keywords`는 자유 입력입니다. 저장 시 공백/빈값/중복만 정리되고, 후보 제한은 없습니다.
- **`GET /options`**: 프론트가 `frequency`/`summary_length`/`language` 드롭다운을 백엔드 `config.py` 값과 항상 일치하게 채울 수 있도록 제공합니다. 이 값들을 프론트가 직접 하드코딩하면, 나중에 백엔드에서 선택지가 바뀌었을 때 프론트가 조용히 어긋나게 됩니다.

</details>

---

<details>
<summary><b>🛡️ 보안 — 인증 체계</b></summary>

- **관리자 인증**: `GET /subscribers`는 관리자 전용입니다. `.env`의 `ADMIN_PASSWORD`와 같은 값을 `X-Admin-Password` 헤더로 보내야 합니다. 헤더가 없거나 값이 틀리면 401을 반환합니다. Swagger UI에서는 우측 상단 Authorize 버튼에 비밀번호를 넣으면 이후 요청에 자동으로 실립니다. `ADMIN_PASSWORD`가 서버에 아예 설정되지 않았으면 어떤 값을 보내도 401입니다 — 설정 안 됨을 누구나 통과로 취급하지 않습니다.
- **본인 확인(셀프서비스)**: 가입 시 이메일 확인은 이메일 소유를 1번 확인할 뿐, 그 뒤 조회·수정·삭제 요청이 실제 그 이메일 주인이 보낸 건지는 증명하지 않습니다. 그래서 `GET`/`PUT`/`DELETE /subscribers/{email}`는 `X-Admin-Password` 또는 `X-Access-Code` 중 하나가 필요합니다. 후자는 `POST .../access-code`로 발급받아 이메일로 받는 코드이며, `ACCESS_CODE_TTL_MINUTES`(기본 15분) 동안 재사용할 수 있어 조회 후 수정처럼 API를 연달아 부르는 흐름도 지원합니다.
- 이 두 인증 모두 없으면 이메일 문자열만 아는 것으로는 남의 구독 정보를 보거나 바꿀 수 없습니다.

</details>

---

<details>
<summary><b>🔄 프론트(Streamlit) 연동</b></summary>

Streamlit은 서버 사이드 Python에서 직접 호출하는 구조라 CORS 설정이 필요 없습니다. Streamlit 프로세스가 `requests`로 이 API를 서버 대 서버로 호출하면 됩니다.

| 값 | 용도 |
|---|---|
| `API_BASE_URL` | 이 API가 떠 있는 주소입니다(`http://localhost:8000` 등). |
| `ADMIN_PASSWORD` | 관리자 화면(`GET /subscribers`)을 부를 때 `X-Admin-Password` 헤더에 실을 값입니다. |

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
res.raise_for_status()  # 400/409 등은 예외로 올라옵니다 — res.json()["detail"]에 사유가 담깁니다.

# 전체 구독자 조회 (관리자 화면 — 인증 필요)
res = requests.get(
    f"{API_BASE_URL}/subscribers",
    headers={"X-Admin-Password": ADMIN_PASSWORD},
)
subscribers = res.json()

# 셀프서비스 조회/수정/해지 (일반 사용자 — 본인 확인 코드 필요)
requests.post(f"{API_BASE_URL}/subscribers/user@example.com/access-code")  # 코드 이메일 발송
code = input("메일로 받은 코드: ")
res = requests.get(
    f"{API_BASE_URL}/subscribers/user@example.com",
    headers={"X-Access-Code": code},
)
```

</details>

---

<details>
<summary><b>🚧 알려진 제한사항 / 향후 확장</b></summary>

- **속보(긴급) 감시는 아직 스케줄러에 등록되지 않았습니다.** `future/breaking.py`에 판정 로직만 존재합니다.


</details>
