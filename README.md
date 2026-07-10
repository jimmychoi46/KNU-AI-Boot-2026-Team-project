# KNU-AI-Boot-2026-Team-project
강원대 2026 AI 계절학기(여름) 부트캠프 팀 프로젝트_5조

개인화 트렌드 뉴스레터 — 사용자가 고른 키워드의 뉴스를 수집·요약해 지정한 시각에 메일로 보내는 시스템입니다. 백엔드(수집·요약·발송·구독자 API)와 프론트엔드(구독 신청·관리 화면)를 한 브랜치에 합친 통합본입니다.

## 구성

| 디렉터리 | 내용 | 실행 |
|---|---|---|
| `backend/` | FastAPI 구독자 API + 스케줄러(수집·요약·발송) + LLM 요약(`LLM_fn.py`) | `uvicorn src.api:app`, `python main.py` |
| `frontend/` | Streamlit 구독 신청·대시보드·구독취소 화면 | `streamlit run app.py` |
| `Newsletter_template/` | 이메일 템플릿 원본 + 요약 검증 기준·프롬프트 인젝션 방어 명세(기획 문서) | — |

프론트가 백엔드의 REST API를 호출하는 구조라, 두 서버를 각각 띄워야 합니다.

## 실행

### 1) 백엔드

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env      # SENDER / GOOGLE_APP_PASSWORD / NAVER_* / OPENAI_API_KEY / ADMIN_PASSWORD 채우기
uvicorn src.api:app --reload   # 구독자 API — http://localhost:8000/docs
python main.py                 # 수집·요약·발송 스케줄러 (별도 터미널)
```

### 2) 프론트엔드

```bash
cd frontend
pip install -r requirements.txt
cp .env.example .env                              # API_BASE_URL (기본 http://localhost:8000)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # admin_password 채우기
streamlit run app.py           # http://localhost:8501
```

- 프론트의 `admin_password`(`.streamlit/secrets.toml`)는 백엔드 `.env`의 `ADMIN_PASSWORD`와 같아야 관리자 대시보드가 열립니다.
- 세부 설계·API 계약·발송 파이프라인 설명은 각 디렉터리의 `README.md`에 있습니다.
