# Checklist — Market Crisis Guard

## 구현 항목
- [x] config.py: MARKET_GUARD_* 상수 7개 추가
- [x] modules/market_guard.py: MarketGuard 클래스 신규 생성
- [x] main.py: execute_buy_orders() 가드 로직 삽입
- [x] main.py: cash_ratio → available_cash 축소 적용

## 검증 항목
- [x] py_compile 통과 (config.py, market_guard.py, main.py)
- [x] code-tester 에이전트 통과 (심각 0건, 주의 2건 수정 완료)
- [x] 판단 로직 검증: CRISIS/DANGER/CAUTION/NORMAL 조건 일치 확인
- [x] API 실패 케이스 처리 확인 (양쪽 실패→NORMAL, 한쪽 실패→0%로 간주)

## 배포 항목
- [x] systemd 서비스 재시작 (2026-03-23 15:09 KST)
- [ ] 텔레그램 알림 정상 수신 확인 (다음 매수일에 확인)

## 문서 업데이트 항목
- [x] CLAUDE.md(MEMORY.md): Market Guard 관련 정보 추가
- [x] memory/project_market_guard.md: 메모리 파일 생성
- [x] CONTEXT.md: 작업 완료 — code-tester 주의사항 2건 수정 반영
