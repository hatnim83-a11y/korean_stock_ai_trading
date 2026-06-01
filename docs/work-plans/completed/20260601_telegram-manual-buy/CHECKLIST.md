# CHECKLIST — 텔레그램 `/buy` 수동 매수 (완료 2026-06-01)

## 구현
### Phase 1: DB + 헬퍼 + 토글
- [x] database.py `_migrate_v18`: `ALTER TABLE portfolio ADD COLUMN is_manual BOOLEAN DEFAULT 0`
- [x] database.py `save_holding_position()`: is_manual 파라미터 + INSERT 반영
- [x] database.py `get_portfolio()`: SELECT * 라 is_manual 자동 포함 (확인)
- [x] `modules/trading_engine/capital_utils.py` 신설: compute_per_slot_capital()
- [x] main.py: 인라인 슬롯 사이징 → 헬퍼 호출 교체 (회귀 4케이스 동일 확인)
- [x] config.py: MANUAL_BUY_ENABLED + MANUAL_BUY_CUTOFF 추가

### Phase 2: 로테이션 skip 가드
- [x] main.py `_check_midweek_replacement()` 루프: `if h.get("is_manual"): continue` + 로그
- [x] 화요일 "테마 이탈 일괄 매도" 루프는 실제 미존재 확인 (테마 매도는 midweek 단일)
- [x] run_hold_period_sells(): 가드 추가 안 함 (의도적 — 보유기간 청산 유지)
- [x] 테마 카운팅 루프(1332)에 is_manual 자연제외 주석 추가

### Phase 3: execute_buy() (web/dashboard_service.py)
- [x] execute_buy() async 신설, 현금/총자산 폴백(main.py 동일)
- [x] compute_per_slot_capital + TRANCHE_FIRST_RATIO 적용
- [x] `await asyncio.to_thread(compute_atr, code, 14)`
- [x] 손절가 = filled × (1 + DEFAULT_STOP_LOSS)
- [x] sell_lock.is_locked → buy_lock.acquire (try/finally release)
- [x] buy_market_order → 체결가 폴백 → KIS 실패 시 미저장
- [x] save_holding_position(is_manual=True) + save_trade + v17 필드

### Phase 4: 텔레그램 핸들러
- [x] _pending_buy + _clear_pending() (buy/sell 상호배제)
- [x] _is_valid_buy_time() (is_trading_day + 09:00~MANUAL_BUY_CUTOFF)
- [x] _handle_buy_command() (전체 거부 가드 + 예상치 메시지)
- [x] _handle_confirm_command() buy 분기 → execute_buy + start_monitoring 재호출
- [x] _handle_cancel_command() buy/sell 공용 + 라우터 /buy + help

## 검증
- [x] py_compile 6파일 PASS
- [x] tests/test_manual_buy.py 11/11 PASS
- [x] 관련 회귀 58 PASS (v16마이그/tranche/diversity/sell_lock/monitor_state)
- [x] tests/test_database_v16_migration.py ==16 → >=16 forward-compatible 수정
- [x] code-tester 재검증 심각 0건 (사전 심각 5건 전부 해소)

## 배포
- [x] 3문서 active → completed 아카이브
- [ ] git 커밋 + main 머지
- [ ] DB v18 자동 마이그레이션 + systemctl restart active 확인

## 문서 업데이트
- [x] CLAUDE.md — 텔레그램 수동 매수 (/buy) 섹션
- [x] ~/.claude memory: project_manual_buy.md + MEMORY.md 포인터
- [x] docs/improvements/change_log.md — 1줄 추가
