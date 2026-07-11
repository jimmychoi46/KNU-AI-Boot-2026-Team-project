# KNU-AI-Boot-2026-Team-project (Frontend)

강원대 2026 AI 계절학기(여름) 부트캠프 팀 프로젝트_5조

Streamlit 기반 AI 뉴스레터 구독 관리 화면입니다. 사용자는 관심 키워드와 발송 옵션을 골라 구독하고, 사용자·관리자 화면에서 구독 정보를 조회·수정·삭제합니다. 데이터 처리는 별도 FastAPI 백엔드 API를 호출해 이뤄집니다.

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run app.py
```

- 멀티페이지 Streamlit 앱이며 진입점은 `app.py`입니다.
- 백엔드 API 주소는 환경변수 `API_BASE_URL`로 지정하고, 미설정 시 `http://localhost:8000`을 사용합니다. 로컬 백엔드를 `uvicorn src.api:app`으로 띄웠다면 기본값 그대로 두면 됩니다. 필요하면 `.env.example`을 `.env`로 복사해 값을 조정하세요.
- 관리자 화면은 `.streamlit/secrets.toml`의 `admin_password`로 접근을 제한합니다. `.streamlit/secrets.toml.example`을 복사해 값을 채우되, 백엔드 `.env`의 `ADMIN_PASSWORD`와 같은 값이어야 목록 조회까지 성공합니다.

## 페이지 구성

| 페이지 | 파일 | 하는 일 |
|---|---|---|
| 구독 신청 | `pages/1_subscribe.py` | 이름·이메일·관심 키워드(필수)와 받는 시간·발송 주기·요약 길이·언어를 골라 구독을 신청합니다. 선택지는 백엔드 `GET /options`에서 받아옵니다. |
| 관리자 모드 | `pages/2_dashboard.py` | `secrets.toml`의 `admin_password`로 인증한 뒤 전체 구독자 목록·통계를 조회하고 구독자를 수정·삭제합니다. |
| 구독 취소 | `pages/3_unsubscribe.py` | 이메일과 본인 확인 코드로 구독을 취소합니다. |
| 사용자 모드 | `pages/4_user_dashboard.py` | 이메일로 받은 본인 확인 코드로 내 구독 정보를 조회·수정합니다. |

## 백엔드 연동 (`utils.py`)

`utils.py`는 백엔드 FastAPI를 호출하는 클라이언트 계층으로, 모든 요청은 `API_BASE_URL` 기준으로 나갑니다.

- **구독자 관리**: 신청(`save_subscriber`)·수정(`update_subscriber`)·삭제(`delete_subscriber`)·목록/통계(`load_subscribers`, `get_statistics`)·본인 확인 코드 발급(`request_access_code`).
- **인증 헤더**: 관리자 비밀번호는 `X-Admin-Password`, 본인 확인 코드는 `X-Access-Code` 헤더로 싣습니다.
- **선택지 조회**: 발송 옵션은 백엔드 `GET /options`에서 받아 하드코딩 불일치를 막고, 조회가 실패하거나 응답 본문이 예상과 다른 형태(딕셔너리가 아닌 배열·문자열 등)여도 기본 선택지로 폴백해 폼이 정상적으로 뜨도록 합니다.
