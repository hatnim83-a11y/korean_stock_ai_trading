# CONTEXT: 대시보드 읽기 전용 전환 + 텔레그램 매도 명령어

## 변경 이유
대시보드를 GCP 방화벽 없이 외부 접근 가능하게 하려면 제어 기능(매도) 제거 필요.
매도 기능은 텔레그램 CHAT_ID 인증으로 보호.

## 현재 코드 상태

### dashboard.html
- Actions 탭 (line 160), 패널 (252-267), 모달 (271-282)
- 관련 CSS (111-127), JS (482-548)
- 매도 셀렉트 업데이트 (357-360)

### api_routes.py
- POST /actions/sell (59-67), POST /actions/sell-all (70-75)
- import: Body, JSONResponse 사용

### dashboard_service.py
- execute_sell() line 374: 하드코딩 "대시보드 수동 매도" (line 401, 443)
- execute_sell_all() line 453: execute_sell() 호출

### telegram_notifier.py
- start_command_listener (662-741): /portfolio, /pause, /resume, /status, /help
- cmd 추출: text.strip().lower() (line 696)
- _send_to_chat() 메서드 존재 (906-920)
- _handle_portfolio_command() 예시 — DB 조회 패턴 참조

## 영향 범위
- 대시보드: 매도 UI 제거만, 읽기 기능 불변
- dashboard_service: execute_sell/execute_sell_all 함수는 유지 (텔레그램에서 재사용)
- 텔레그램: 새 명령어 4개 추가, 기존 명령어 불변
