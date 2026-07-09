# 진행 상황 (2026-07-08 기준, 원격 브랜치 기준)

`git fetch --prune` 로 확인한 GitHub 원격(`origin`) 브랜치 실제 상태를 기준으로 정리.
(로컬에만 있는 미커밋 변경사항은 별도 섹션에 표시)

## 1. 브랜치 현황 요약

| 브랜치 | 최근 커밋 | 상태 | 비고 |
|---|---|---|---|
| `main` | `06306c3` README.md 수정 | 뼈대만 있음 | 파일이 README 1개뿐, 실제 코드 없음 |
| `dev` | `f8e4f3e` Initial commit | 뼈대만 있음 | main과 동일 수준, 사실상 미사용 |
| `feature/backend` | `484a53f` 7/8 백엔드 현재 진행상황 | **백엔드 뼈대 완성** | 구 `backend` 브랜치와 동일 커밋 — 이름만 바뀐 것으로 보임 |
| `LLM-agent` | `0e06738` LLM 코드 | **독립 스크립트로 존재, 백엔드 미병합** | 이번 세션에 로컬에서 수동 통합 진행(§3) |
| `frontend` | `49365a1` Add files via upload | Streamlit 앱 초안 | 백엔드 REST API 미연동(§4) |

> ⚠️ **원격 브랜치 정리 발견**: 이번 조사 중 `origin/backend`, `origin/backend_example`, `origin/프론트엔드` 3개 브랜치가 삭제된 상태로 확인됨(`git fetch --prune` 결과 `[deleted]`). `feature/backend`가 옛 `backend`와 동일 커밋(`484a53f`)이라, 브랜치 정리/이름 정리가 있었던 것으로 보임. 로컬 작업 브랜치(`backend`)는 원격 추적이 끊긴(`[gone]`) 상태.

## 2. 브랜치별 상세

### `feature/backend` — 백엔드 (완성도 높음)
`main.py` + `src/` 아래 스케줄러·수집·DB·발송까지 갖춘 실동작 가능한 뼈대.
- **수집**: `src/collectors/naver_news.py` — 네이버 뉴스 API, 재시도/타임아웃, 태그 제거, 중복 제거, 기간 필터
- **저장소**: `src/db.py` — SQLite. `subscribers` / `articles` / `digests → digest_issues → digest_topics → digest_links` 계층
- **배치 파이프라인**: `src/pipeline.py` — `collect_job`(수집) → `summarize_job`(요약, LLM 인터페이스 호출) → `dispatch_job`(발송) 3단계, DB로 단계 결과 전달
- **속보 감지**: `src/breaking.py` + `main.py::monitor_breaking` — 물량 급증/긴급 키워드 기반 즉시 발송
- **구독자 REST API**: `src/api.py` (FastAPI) — 구독 신청/수정/해지 + 이메일 더블 옵트인 확인 + 관리자 인증(`GET /subscribers`)
- **테스트**: `tests/` 아래 9개 파일, 이 브랜치 시점 기준 통과 상태
- **LLM/Agent 연동 지점**: `src/processors/summarizer.py::summarize()` — 이 브랜치에는 **TODO 스텁**(패스스루)만 있고 실제 LLM 로직 없음
- **기획/데이터 파트**(`src/renderers/report.py`, 템플릿)도 이 브랜치엔 최소 구현만 있는 상태로 보임(별도 검증 필요)

### `LLM-agent` — 요약·QA 에이전트 (독립 실행 스크립트 상태)
`LLM_fn.py` + `test.py` 로만 구성된 독립 스크립트. 백엔드 파이프라인에 아직 연결되지 않음.
- OpenRouter 경유 GPT-4o 호출, **요약 Agent → QA/편집 Agent** 2단계 파이프라인
- 프롬프트 인젝션 방어 문구, 링크 무결성 검증(원본에 없는 링크 생성 방지) 등 설계 품질은 좋음
- 다만 입력이 **네이버 원본 JSON**(백엔드가 이미 정제한 데이터가 아님) 기준으로 짜여 있어, 백엔드 인터페이스 계약과 형태가 다름
- 발견된 버그: `test.py`가 확인하는 저장 파일명(`test_summary.json`)과 실제 저장 파일명(`naver_news_summary.json`)이 서로 달라 테스트 7단계가 항상 실패함. `__pycache__`가 `.gitignore` 없이 커밋되어 있음.

### `frontend` — Streamlit 구독 관리 앱 (초안, 백엔드 미연동)
`newsletter_project/` 아래 `app.py` + 3개 페이지(구독/대시보드/해지).
- **로컬 CSV**(`data/subscribers.csv`)에 직접 저장 — `feature/backend`가 이미 만들어둔 **`src/api.py` REST API를 아직 호출하지 않음**. README(`feature/backend`)에 명시된 "Streamlit이 서버 사이드에서 API를 직접 호출" 연동 방식과 다른 경로로 구현되어 있어, 통합 시 이 부분을 API 호출로 바꿔야 함
- **값 불일치 발견**: `summary_length_options = ["짧게","보통","길게"]` (프론트) vs `config.SUMMARY_LENGTH = ["짧게","중간","길게"]` (백엔드) — "보통"↔"중간" 다름. `language_options = ["한국어","English"]` (프론트) vs `config.LANGUAGE = ["한국어","영어"]` (백엔드) — "English"↔"영어" 다름. 이대로면 프론트에서 고른 값이 백엔드 검증을 통과하지 못함.

### `main` / `dev`
README 문구만 다른 사실상 빈 브랜치. 실질적인 작업 브랜치로 쓰이지 않는 것으로 보임.

## 3. 이번 세션 작업 (로컬 `backend` 브랜치, 아직 커밋/푸시 안 됨)

`feature/backend`(=`backend`) 위에서 `LLM-agent` 브랜치 코드를 백엔드 인터페이스에 맞게 연동:

- **신규** `src/processors/llm_agent.py` — `LLM_fn.py`의 요약→QA 2단계 파이프라인을 이식하되, 입력을 백엔드가 이미 정제한 기사 리스트로, 출력 스키마를 이슈→**주제(topic) 단위 링크**로 조정(원래는 이슈 단위 링크였음 — DB 스키마 `digest_topics→digest_links`와 더 정확히 대응하도록 개선)
- **수정** `src/processors/summarizer.py` — TODO 스텁 제거, `llm_agent` 결과를 평평한(flat) 행으로 변환하는 어댑터로 교체
- **수정** `src/pipeline.py` — `summarize_job`에 실패 격리(`try/except`) 추가 (LLM 실패 1건이 나머지 조합 요약을 막지 않도록, 발송 잡과 동일한 패턴)
- **수정** `requirements.txt`(`openai==1.59.7`), `.env.example`(`OPENAI_API_KEY`) 추가
- **수정** `tests/test_pipeline.py` — 실제 LLM 호출 없이 돌도록 관련 테스트 3곳을 목(mock) 처리로 조정
- **검증 완료**:
  - `pytest` 111개 전부 통과 (LLM 목 처리 상태)
  - 수동 통합 테스트로 **실제** 네이버 뉴스 수집 → (LLM 미사용, 가짜 요약) → **실제 Gmail 발송**까지 1회 성공 확인 (`jimmychoi46@gmail.com` 수신)

이 작업은 **아직 커밋되지 않은 로컬 변경**이며, 원격의 어느 브랜치에도 반영되어 있지 않음.

## 4. 알려진 이슈 / 다음 단계

1. **로컬 변경사항 커밋/푸시 필요** — §3 작업이 아직 로컬에만 있음. 원격 브랜치 정리(backend 삭제/feature-backend로 이동) 상황과 맞춰서 어느 브랜치에 올릴지 확인 필요
2. **프론트-백엔드 미연동** — `frontend`가 REST API 대신 로컬 CSV를 씀. `summary_length`/`language` 선택지 값도 백엔드와 다름 (§2 참고)
3. **`data/subscriptions.json`의 `send_minute=51`** — 백엔드 검증 규칙(0/30분 단위)에 걸려 시드가 조용히 건너뛰어짐. 값 수정 필요
4. **`dev` 브랜치** — 사실상 미사용, 브랜치 전략 정리 시 삭제 후보
5. **기획/데이터 파트**(`renderers/report.py`, 템플릿) — 별도 브랜치가 안 보여 진행 상황 확인 안 됨(팀에 문의 필요)
