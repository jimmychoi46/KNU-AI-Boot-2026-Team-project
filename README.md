# 🧠 LLM/Agent 모듈 — 요약·편집 에이전트

> 담당: LLM/Agent (검색·요약·편집 멀티에이전트)
> 역할: 백엔드가 수집·정제한 뉴스 데이터를 받아, 이슈 그룹화 → 요약 → 편집/QA를 거쳐
> 뉴스레터용 구조화된 데이터로 반환합니다.

---

<details>
<summary><b>📁 파일 구성</b></summary>

| 파일 | 역할 |
|---|---|
| `LLM_fn.py` | 요약 Agent, 편집/QA Agent, 링크 무결성 검증 등 핵심 로직 |
| `temporary.py` | 백엔드와 합의한 `summarize()` 인터페이스 (실제 연동 지점) |
| `test_summarize.py` | `summarize()` 동작 확인용 테스트 스크립트 |
| `collected_sample.json` | 테스트용 샘플 입력 데이터 |

</details>

---

<details>
<summary><b>⚙️ 설치 및 환경 설정</b></summary>

### 필요 패키지
```bash
pip install openai python-dotenv
```

### 환경 변수
프로젝트 루트에 `.env` 파일을 만들고 아래 값을 채워주세요.
```
OPENAI_API_KEY=여기에_API_키_입력
```
- 키가 없으면 모듈 로드 시 `RuntimeError`가 발생합니다.
- OpenRouter(`https://openrouter.ai/api/v1`)를 통해 `openai/gpt-4o` 모델을 호출합니다.

</details>

---

<details>
<summary><b>🔌 인터페이스 계약 (백엔드 연동 지점)</b></summary>

### 함수 시그니처
```python
from summarize_interface import summarize

result = summarize(collected, summary_length, language)
```

### 입력 (`collected`)
```python
{
    "쿼리명(예: IT 트렌드)": [
        {
            "title": "기사 제목",
            "link": "https://...",
            "description": "기사 설명/본문 요약",
            "published_at": "2026-07-08T09:00:00"
        },
        ...
    ],
    ...
}
```
- 백엔드가 이미 태그·엔티티 제거, 날짜 파싱까지 마친 **정제된 상태**로 전달합니다.

### 파라미터
| 파라미터 | 값 | 설명 |
|---|---|---|
| `summary_length` | `"짧게"` / `"중간"` / `"길게"` | 이슈 하나당 전체 요약 분량 (각각 2~3 / 4~5 / 6~7문장) |
| `language` | `"한국어"` / `"영어"` 등 | 결과 텍스트 작성 언어 |

### 출력
```python
{
    "쿼리명": [
        {
            "headline": "이슈 제목",
            "topic": "핵심 주제",
            "topic_summary": "요약 내용",
            "link": "https://..."
        },
        ...
    ],
    ...
}
```
- 이슈 하나에 주제가 여러 개면, 주제 수만큼 **행(row)이 여러 개**로 펼쳐집니다.
- `link`는 해당 이슈의 원본 링크 중 검증을 통과한 **대표 링크 1개**입니다.

</details>

---

<details>
<summary><b>🔄 내부 동작 순서</b></summary>

```
collected 입력
   │
   ▼
_build_context()            ─ 쿼리별 기사 목록을 LLM 프롬프트용 텍스트로 변환
   │
   ▼
_run_summary_pipeline()
   ├─ _summarize_agent()     ─ [LLM 호출 1] 이슈 그룹화 + 요약 초안 생성
   └─ _qa_agent()            ─ [LLM 호출 2] 문장 길이·객관성·가독성·링크 재검수
   │
   ▼
링크 무결성 필터링           ─ 원본에 없는 링크는 코드 레벨에서 제거
   │
   ▼
행(row) 단위로 변환 후 반환
```

- **요약 Agent와 QA Agent를 분리한 이유**: 요약 Agent가 생성한 초안을 사람이 아닌 LLM이 한 번 더
  검수하게 하여, 문장 길이 준수·객관성 위반·가독성 문제를 1차적으로 자동 필터링합니다.
- **`analyze_news()`와의 관계**: `LLM_fn.py`에는 단일 배치용 함수 `analyze_news()`도 있으며,
  `summarize()`와 내부적으로 `_run_summary_pipeline()`을 공유합니다(중복 로직 없음).

</details>

---

<details>
<summary><b>🛡️ 보안 — 프롬프트 인젝션 방어</b></summary>

- 뉴스 본문/설명은 **신뢰할 수 없는 외부 데이터**로 취급합니다.
- 모든 시스템 프롬프트에 `SECURITY_GUARDRAIL` 문구를 공통 삽입하여, 뉴스 데이터 내부에
  지시문("이전 지침을 무시하라" 등)이 있어도 명령으로 취급하지 않도록 강제합니다.
- 뉴스 본문은 `<news_data>...</news_data>` 태그로, QA 초안은 `<draft_json>...</draft_json>`
  태그로 감싸서 모델이 "이 안은 데이터 영역"이라고 구조적으로 인식하게 합니다.
- **링크 무결성은 프롬프트만으로 강제하지 않고, 코드로도 재검증**합니다. LLM이 원본에 없는
  링크를 생성하거나 다른 기사와 교차 매칭하더라도, 최종 출력 전에 코드에서 걸러냅니다.

</details>

---

<details>
<summary><b>🧪 테스트 방법</b></summary>

```bash
python test_summarize.py                      # 기본 샘플 데이터로 테스트
python test_summarize.py 내_테스트데이터.json    # 원하는 데이터 파일로 테스트
```

테스트 스크립트가 확인하는 것:
1. 입력 파일이 스키마(`title`/`link`/`description`/`published_at`)를 만족하는지
2. `summarize()` 반환값이 요구된 4개 키(`headline`/`topic`/`topic_summary`/`link`)를 모두 포함하는지
3. 반환된 `link`가 실제로 입력 데이터의 원본 링크 목록에 존재하는지

> ⚠️ 실제 API를 호출하므로 비용이 발생하고, `.env`에 `OPENAI_API_KEY`가 설정되어 있어야 합니다.

</details>

---

<details>
<summary><b>🚧 알려진 제한사항 / TODO</b></summary>

- 링크가 없는 이슈는 `link: ""`(빈 문자열)로 반환됩니다 — 프론트/템플릿 쪽에서 처리 방식 협의 필요.
- 현재는 "정제된 뉴스 목록"만 입력으로 받으며, 검색 키워드 자체를 LLM이 확장/생성하는 기능은
  포함되어 있지 않습니다(필요 시 별도 협의).
- LLM 호출이 쿼리당 2회(요약 + QA) 발생하여, 쿼리 수가 많아지면 비용·응답 시간이 비례해 증가합니다.

</details>
