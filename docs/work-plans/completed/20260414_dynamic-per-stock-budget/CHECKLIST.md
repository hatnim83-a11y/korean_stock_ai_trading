# CHECKLIST — 종목당 예산 상한 동적화

## 구현 항목
- [x] main.py Phase 4: `get_balance()` 항상 호출 + `total_capital` 변수 획득
- [x] main.py Phase 4: `total_capital` 폴백 로직 (0 이하 → `max(TOTAL_CAPITAL, available_cash)`)
- [x] main.py Line 1241: `max_per_stock = int(total_capital) // MAX_POSITIONS`
- [x] 로그 메시지 업데이트 (총자산 표시 + 상한 기준 명시)

## 검증 항목
- [x] py_compile main.py
- [x] code-tester 에이전트 검증 (심각 0, 주의 3건 중 float 전파 1건 수정)
- [x] 시뮬레이션 5케이스 통과:
  - 오늘 재현: 1,836,774원 ✅ (기대치 일치)
  - 초기: 800,000원 (기존 동일)
  - 빈슬롯 1개: 1,836,774원 상한 유지 (몰빵 방지)
  - Guard 70%: 상한 유지, per_slot 축소
  - API 실패: 초기값 폴백

## 배포 항목
- [x] 장중 재시작 (2026-04-14 14:04 KST, 사용자 승인)
- [x] 서비스 정상 가동 확인 (PID 2498325)
- [ ] 다음 거래일(4/15) 매수 로그에서 `슬롯 배분: ...원/종목 × N종목 (상한: ...원, 총자산÷5)` 확인

## 문서 업데이트 항목
- [ ] memory `project_strategy.md` 파라미터 섹션 (예산 로직 동적화 기록)
- [ ] active → completed 아카이브 (`20260414_dynamic-per-stock-budget`)
