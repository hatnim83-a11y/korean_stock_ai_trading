# CHECKLIST — 종가베팅 청산/필터 수익 극대화

## Phase 1 (A) — 시가 지정가 + 폴백

### 구현
- [x] `ExitExecutorSettings`에 `open_limit_sell_enabled: bool = False` + `limit_fill_deadline_sec: float = 30.0` 추가
- [x] `settings.yaml morning_exit:` 섹션에 `open_limit_sell_enabled: false` + `limit_fill_deadline_sec: 30` 추가
- [x] `main_orchestrator.py` ee_settings에 신규 2키 매핑
- [x] `_execute_limit_sell_with_fallback` + `_fallback_market_remainder` + `_safe_log_exit` 신규: 지정가 발주 → 폴링 → (전량 시 log_exit 1회) / (미체결·부분 시 cancel→확인→최종수량 재조회→잔량 시장가→log_exit 1회 가중평균)
- [x] `_process_morning_exit`에서 `_execute_morning_sell` 디스패처 경유 (emergency/force_close는 기존 경로 유지)
- [x] dry_run 로그 포맷에 `LIMIT_SELL @open_price` 구분
- [x] `MockOrderApi.sell_limit_order(stock_code, quantity, price)` 추가 (buy_limit_order 패턴)
- [x] `_pending_exit_orders` ODNO 교체 순서(취소확정 후) 보장
- [x] (리뷰 보강) 부분폴백 잔량 미청산 가시성 마커 `partial_fallback_pending`

### 검증
- [x] py_compile 통과 (exit_executor.py, main_orchestrator.py, kis_order_api.py)
- [x] 테스트 EX-31: 지정가 전량체결 → log_exit 1회, 시장가 미발주
- [x] 테스트 EX-33: 지정가 부분체결 → 잔량만 시장가 폴백 → log_exit 1회 가중평균가
- [x] 테스트 EX-32: 지정가 미체결 → 전량 시장가 폴백 / EX-36: 지정가 발주실패 폴백
- [x] 테스트 EX-34: 토글 OFF → 기존 `sell_market_order` 경로 NO-OP (sell_limit_order 미호출)
- [x] 테스트 EX-35: 토글 ON + dry_run → KIS 미발주 + log_exit 미호출
- [x] 기존 EX-* 테스트 전부 PASS (37/37, 토글 default False 영향 없음)
- [x] **code-tester 에이전트** 실행 → 심각 0건(배포가능), 주의 2건(잔량미청산=실발주전 해결권장 / dry_run mark=기존동일)
- [ ] dry_run 단발 검증: 다음 진입 익일 "지정가=open_price" 로그 + KIS 실발주 0 (settings dry_run 유지 + 토글 ON) — 운영 환경 필요

### 배포
- [x] **change_log.md 1줄 추가** (morning_exit 발주방식 시장가→시가지정가+폴백, 토글)
- [ ] (실발주 전환은 **별도 사용자 승인** — settings `open_limit_sell_enabled=true` + restart)
- [ ] **실발주 전제조건(code-tester 주의-1)**: `candidates.exit_shares` 컬럼 추가 → force_close 잔량 재조회 (부분폴백 잔량 미청산 근본 방어)
- [ ] 실발주 1주 관찰: "청산가−시가" 갭 −3.44%p→0 수렴 쿼리 재실행

### 문서 업데이트
- [ ] phase25_simulator open_pct 가정 주석/문서 동기
- [ ] `memory/MEMORY.md` + `project_closing_bet_*` 메모리 1줄
- [ ] CLAUDE.md 종가베팅 운영 규칙에 청산 토글 추가
- [ ] PLAN/CONTEXT/CHECKLIST → completed/ 아카이브 (Phase 1 종료 시)

## Phase 2A — 부분청산 회계 + gap_up_high 잔여 미청산 버그 fix ✅ 구현 완료
- [x] DB v4: `exit_shares`+`final_exit_time` + 원자적 백필(멱등)
- [x] `log_partial_exit`(누적/가중평균가/전량시 exit_time) + `log_exit` exit_shares 정합 보강
- [x] `ExitTarget.remaining_shares` + select에 exit_shares 노출(`exit_time IS NULL` 유지)
- [x] 청산경로 `accumulate` 플래그: gap_up_high 1차/force_close 잔여 → 누적, force_close=remaining만
- [x] 테스트 38/38(EX-37 흐름) + 24/24 PASS, code-tester 심각0
- [x] change_log.md 1줄
- 잔여 주의(후속): gap_up_high_partial_ratio 주석 50%→60% 정정 / 부분청산중 get_closed_today 미집계(설계상 허용)

## Phase 2B — 트레일링 사이클 ✅ 구현 완료(토글 off)
- [x] `run_morning_trailing` IntervalTrigger(30초, max_instances=1, coalesce) + 내부 윈도우 가드(09:05~10:25, is_trading_day)
- [x] `execute_trailing_stop`/`_process_trailing_stop`: remaining 보유분 → get_snapshot → `current ≤ high×(1+trailing_stop_pct)` + 활성화가드(high≥진입×1.01) + snap유효성(high/current>0)
- [x] sell_lock 핸드오프(release→acquire owner "closing_bet:morning_trailing") — morning_exit lock 차단 해소
- [x] 토글 morning_trailing_enabled(default false, 잡 미등록 NO-OP) + trailing_stop_pct(-0.015, exit:) + trailing_activation_pct(0.01)
- [x] 텔레그램 "오전 TRAILING STOP" 레이블 + send_trailing_result
- [x] 44/44 PASS(TR-1~6) + code-tester 심각0 + change_log
- 잔여 주의(후속): trailing↔force_close 10:25:30 race(KIS 잔고로 차단, 저위험) / sell_lock owner 미검증 release(force_close 동일 패턴)

## Phase 2C — 1차 부분익절 확대 (2B 활성 시, 코드 가드)
- [ ] morning_exit gap_up 구간 morning_partial_ratio 매도, 잔여 트레일링 위임
- [ ] 2B 비활성 시 부분익절도 비활성(잔여 고아 방지) 코드 가드

## Phase 3 (필터) — atr_overheat 밴드 차등 ✅ 구현 완료(토글 off)
- [x] `signal_score_engine.py` 밴드 차등 토글 `atr_overheat_band_enabled`(default false) + `atr_overheat_band_high`(2.2)
- [x] 필터 로직: band on 시 `max < atr <= band_high`만 차단, band_high 초과 극과열 통과. band off=하드컷 NO-OP
- [x] `__init__` 검증(band_high>=max) + `from_settings` 매핑 + settings.yaml `score:` 3키
- [x] 테스트 28/28 PASS (밴드 6건: NO-OP/중간차단/극과열통과/경계/정상/ValueError)
- [x] change_log.md 1줄
- [ ] (실적용 별도 승인) 청산 개선(Phase 1)과 세트 + 드라이런 30건 누적 검증
- [ ] code-tester 정식 검증 (실적용 전)

## 진행 메모
- 2026-06-15: 3문서 생성. 범위=전부(A+B+과열) phased. Phase 1부터 착수.
