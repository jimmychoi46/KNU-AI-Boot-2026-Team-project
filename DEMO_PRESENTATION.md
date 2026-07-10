# 발표용 데모 순서표 (한 장짜리)

발표 중 화면으로 보여줄 순서만 짧게. **각 줄은 "무엇을 하고 무엇이 뜨는지"만** 적었습니다 — 설명 멘트는 발표자 말투로.

> **처음 시연하거나 발표자가 개발자가 아니면 → [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)** (설치·서버 기동·초기화·확인링크/코드 얻는 법·문제 해결까지 전체 조작 안내). 상세 검증 기록은 [DEMO_SCENARIOS.md](DEMO_SCENARIOS.md).

**사전 준비**: 백엔드(`uvicorn src.api:app --port 8000`) + 프론트(`streamlit run app.py --server.port 8501`) 기동 → 터미널에서 `python demo_helper.py reset` → `seed`. 관리자 비밀번호는 팀이 설정한 값(`.env`의 `ADMIN_PASSWORD` = 프론트 담당자 `secrets.toml`). 확인링크·코드는 `python demo_helper.py confirm-url/code` 로.

## 1. 신규 구독 → 이메일 확인
- `/subscribe`에서 이름·이메일·키워드 입력 → "구독 신청" → 접수 메시지.
- 확인 링크: 메일 링크 또는 `demo_helper.py confirm-url <이메일>` → 열어서 "구독 확정하기" → "구독이 확인되었습니다".

## 2. 관리자 대시보드
- `/dashboard` 비밀번호 입력 → 통계·목록.
- 구독자 이름 수정 → 저장 → 목록 반영. 삭제 → 목록에서 사라짐.

## 3. 셀프서비스 본인확인
- `/user_dashboard` 이메일 입력 → "인증 코드 받기".
- 코드: 메일 또는 `demo_helper.py code <이메일>` → 입력 → 내 정보 표시 → 이름 수정 → 저장.

## 4. 수집 → 요약 → 발송 파이프라인
- `demo_helper.py pipeline-demo <이메일> --open` → 5단계 로그 + HTML 2개 열림: 일간 뉴스레터 + **별도** 주간 트렌드 메일(키워드+관련 기사).
- (실시간 뉴스+실제 발송은 부록 B의 `pipeline-live`, 키 필요.)

## 5. 엣지 — 이미 구독 중인 이메일
- 확인 완료된 이메일로 다시 구독 신청 → "이미 구독 중인 이메일입니다" 오류.

## 6. 엣지 — 지저분한 키워드 입력
- 키워드 칸에 `주식,  , 주식,   금리 ,금리` 입력 → 제출.
- 대시보드/`list`에서 해당 구독자 키워드가 `주식, 금리`로 정리됨.

## 7. 엣지 — 발송 시각 검증
- `/subscribe` "받는 시간" 드롭다운 → 30분 단위 값만 있음.
- `demo_helper.py bad-time` → `send_minute=15 → 400`, `send_hour=25 → 422`, `send_hour=24 → 201`.

## 8. ⭐ 차별점 — 재발송 방지 (같은 기사 두 번 안 보냄)
- `demo_helper.py norepeat-demo <이메일> --open` → HTML 2개 열림: 1일차 뉴스 3건 vs 2일차 **새 기사 1건만**(어제 본 건 빠짐), 3일차 새 것 없어 `발송 안 함`.
- 서사: "시간 없는 직장인을 위해 매일 새 것만."
