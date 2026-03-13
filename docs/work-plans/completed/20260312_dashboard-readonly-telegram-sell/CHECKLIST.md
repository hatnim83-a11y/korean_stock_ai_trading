# CHECKLIST: 대시보드 읽기 전용 전환 + 텔레그램 매도 명령어

## 구현 항목
- [x] dashboard.html: Actions 탭/패널/모달/CSS/JS 제거
- [x] dashboard.html: 매도 셀렉트 업데이트 코드 제거
- [x] api_routes.py: POST 매도 엔드포인트 제거 + import 정리
- [x] dashboard_service.py: execute_sell() reason 파라미터 추가
- [x] dashboard_service.py: execute_sell_all() reason 파라미터 추가
- [x] telegram_notifier.py: _pending_sell 인스턴스 변수 추가
- [x] telegram_notifier.py: /sell 핸들러 구현
- [x] telegram_notifier.py: /sellall 핸들러 구현
- [x] telegram_notifier.py: /confirm 핸들러 구현 (이중 매도 방지 포함)
- [x] telegram_notifier.py: /cancel 핸들러 구현
- [x] telegram_notifier.py: 명령어 분기 추가
- [x] telegram_notifier.py: /help 메시지 업데이트

## 검증 항목
- [x] py_compile 3개 Python 파일 통과
- [x] code-tester 심각 이슈 수정 완료
- [x] dashboard.html에 actions 관련 잔여 코드 없음
- [x] POST /api/v1/actions/sell → 404 확인
- [x] GET /api/v1/portfolio → 200 정상 확인
- [x] GET /api/v1/system/status → 200 정상 확인

## 배포 항목
- [x] systemctl restart trading_system
- [x] systemctl restart trading_dashboard

## 문서 업데이트 항목
- [x] 프로젝트 메모리 업데이트 (텔레그램 명령어 목록)
