# PLAN: 대시보드 읽기 전용 전환 + 텔레그램 매도 명령어

## 목표
대시보드에서 매도 기능 제거 → 순수 모니터링 전용으로 전환.
매도 기능은 텔레그램으로 이전 (CHAT_ID 인증 보호).

## 구현 단계

### Step 1: dashboard.html — Actions UI 제거
- [x] Actions 탭 버튼 제거 (line 160)
- [x] Actions 패널 제거 (lines 252-267)
- [x] 확인 모달 제거 (lines 271-282)
- [x] 액션/모달 CSS 제거 (lines 111-127)
- [x] Actions JS 전체 제거 (lines 482-548)
- [x] 매도 셀렉트 업데이트 코드 제거 (lines 357-360)

### Step 2: api_routes.py — POST 매도 엔드포인트 제거
- [x] POST /actions/sell, /actions/sell-all 삭제 (lines 57-75)
- [x] import에서 Body, JSONResponse 제거

### Step 3: dashboard_service.py — reason 파라미터 추가
- [x] execute_sell()에 reason 파라미터 추가 (default="수동 매도")
- [x] execute_sell_all()에 reason 파라미터 추가 → 개별 execute_sell에 전달
- [x] 하드코딩 "대시보드 수동 매도" → reason 파라미터로 교체

### Step 4: telegram_notifier.py — 매도 명령어 추가
- [x] _pending_sell, _pending_sell_all 인스턴스 변수 추가
- [x] /sell, /sellall, /confirm, /cancel 핸들러 구현
- [x] 명령어 분기 추가 (start_command_listener)
- [x] /help 메시지 업데이트

## 변경 파일 목록
1. `web/templates/dashboard.html`
2. `web/api_routes.py`
3. `web/dashboard_service.py`
4. `modules/reporter/telegram_notifier.py`

## 롤백 계획
- git revert로 커밋 되돌리기

## 완료 기준
- 대시보드에 Actions 탭 없음
- POST /api/v1/actions/sell → 404
- 텔레그램 /sell, /sellall → /confirm 흐름 동작
- py_compile 통과
