# 데일리 금융 뉴스 브리핑

네이버 뉴스를 매일 자동 수집·요약하여 이메일로 발송하는 파이프라인 프로젝트.

## 역할 분담

| 역할 | 담당 | 관련 모듈 |
|---|---|---|
| A. LLM/Agent | 검색·요약·편집 멀티에이전트 | `processors/summarizer.py` |
| **B. 백엔드** | **스크래퍼·스케줄러·이메일 발송 서버** | **`collectors/`, `notifiers/`, `main.py`, `pipeline.py`, `config.py`** |
| C. 프론트 | 구독 신청 페이지·키워드/발송시간 대시보드 | (수신자·키워드 입력 → `config`/구독 저장소) |
| D. 기획/데이터 | 요약 가독성 검증·템플릿 디자인·프롬프트 인젝션 방어 | `renderers/report.py`, `templates/` |

> 모듈 간 경계는 **인터페이스(함수 시그니처)**로 고정되어 있어, 각 담당은 자기 모듈 내부만 채우면 된다.

## 디렉터리 구조

```
team_project/
├── main.py                     # [B] 스케줄러 진입점 (매일 지정 시각 실행)
├── src/
│   ├── config.py               # [B] .env 로드 + 동작 설정(검색어/수신자/스케줄)
│   ├── collectors/
│   │   └── naver_news.py        # [B] 네이버 뉴스 수집
│   ├── processors/
│   │   └── summarizer.py        # [A] 요약·편집  (summarize 인터페이스)
│   ├── renderers/
│   │   └── report.py            # [D] 렌더링      (render 인터페이스)
│   ├── templates/
│   │   └── daily_report.html    # [D] 메일 HTML 템플릿
│   ├── notifiers/
│   │   └── send_email.py        # [B] Gmail SMTP 발송
│   └── pipeline.py              # [B] 수집→요약→렌더링→발송 조립
├── tests/                      # 단위 테스트
├── requirements.txt
├── .env.example                # 환경 변수 템플릿
└── .gitignore
```

## 데이터 흐름 (인터페이스 계약)

```
main.py (스케줄러, B)
  └─> pipeline.run_pipeline()  (B)
        ├─ collectors.naver_news.collect(queries)  → {query: [raw_item]}   # B
        ├─ processors.summarizer.summarize(dict)   → {query: [{headline, summary, link}]}  # A
        ├─ renderers.report.render(dict)           → html(str)             # D
        └─ notifiers.send_email.send_to_recipients(recipients, subject, html)  # B
```

각 단계는 위 입출력 형태만 지키면 되고, A·D 는 현재 임시 스텁이 들어가 있어 백엔드 파이프라인은 지금도 끝까지 동작한다.

## 설치

```bash
pip install -r requirements.txt
```

## 환경 변수 설정

`.env.example` 을 `.env` 로 복사한 뒤 값을 채운다.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|---|---|
| `SENDER` | 발신 Gmail 주소 |
| `GOOGLE_APP_PASSWORD` | Google 앱 비밀번호 |
| `NAVER_CLIENT_ID` | 네이버 검색 API Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 검색 API Client Secret |
| `ANTHROPIC_API_KEY` | (선택, A) LLM 요약 연동 시 |

## 실행

```bash
# 스케줄러로 매일 자동 실행
python main.py

# 파이프라인만 즉시 1회 실행 (테스트용)
python -m src.pipeline

# 단위 테스트
python -m pytest
```

> ⚠️ 실행 시 한글이 깨져 보이면 콘솔 인코딩 문제입니다. `chcp 65001` 또는 환경변수 `PYTHONUTF8=1` 설정으로 해결됩니다.
