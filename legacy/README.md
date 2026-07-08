# legacy — 초기 프로토타입 (보관용)

`src/` 구조로 옮기기 전 초기 실험용 스크립트들. **현재 실행에는 쓰이지 않으며**, 참고용으로 남겨둔다.

| 파일 | 대체된 위치 |
|---|---|
| `scrap_test.py` | `src/collectors/naver_news.py` |
| `email_test.py`, `email_test2.py` | `src/notifiers/send_email.py` |
| `scheduler_test.py` | `main.py` |
| `config.py` | `src/config.py` (이 루트 config 는 중복이라 이관) |

> ⚠️ `scheduler_test.py` 는 불러오기만 해도 스케줄러가 시작된다. 검사 시 `tests/` 만 대상으로 하도록 `pytest.ini` 에 설정해 두었다.
