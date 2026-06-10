# CONTEXT — 매수↔모니터 재시작 레이스

## 변경 이유
2026-06-10, 피에스케이홀딩스(031980)가 09:06:19 체결됐으나 모니터 재시작(09:06:00)보다 19초 늦어
당일 종일 모니터링 누락 → 손절/트레일링/분할익절/불타기 전부 미작동(무방비 노출).
사용자 발견 후 수동 `systemctl restart`로 12:22 복구(restart 시 `load_positions_from_db`가 holding 전체 재적재).

## 현재 코드 상태 (파일:라인)
- `scheduler.py:191-193` — early_buy 분기
  - `screening_minute = 2 if early_buy else 5`
  - `buy_minute = 5 if early_buy else 25`
  - `monitoring_minute = 6 if early_buy else 26`
- `scheduler.py:226-249` — `execute_buy`(buy_minute), `monitoring_start_early`(09:00), `monitoring_start`(monitoring_minute) 잡 등록
- `scheduler.py:561-585` — `_run_execute_buy`, `_run_monitoring_start` 래퍼
- `main.py` — `execute_buy_orders()`(매수 오케스트레이션), `start_monitoring()`/`stop_monitoring()`(모니터 재시작; stop 시 SellLock clear_all)
- `modules/trading_engine/portfolio_monitor_v2.py:519` `load_positions_from_db`(holding 전량 적재), `:386` `add_position`(개별 편입), `stop_monitoring`(789), `_dump_monitor_state`(30s 주기 JSON 덤프)

## 핵심 스니펫 — 사건 로그 (KST=UTC+9)
```
00:05:00 _run_execute_buy: 💰 자동 매수 실행 (09:05)     ← 매수 루프 시작
00:05:01 execute_buy_orders: 가용 슬롯 4, 모닝필터 통과 3개
00:05:05 분할 진입 활성: 1차 50% (662,596원/종목, 2차 trigger=+5% first 기준)
00:06:00 _run_monitoring_start: 모니터링 재시작 (신규 매수분 포함)
00:06:00 load_positions_from_db: 포지션 로드: 1개   ← 031980 아직 DB 미저장!
00:06:16 ✅ 매수 주문 성공: 031980 4주
00:06:19 [BL] 완료: 피에스케이홀딩스 4/4주 @ 132,300원
00:06:19 save_holding_position: 031980 (tranche=1, pending=1, atr=14878.57)  ← 재시작 19초 후 저장
```
복구(수동 restart):
```
03:22:30 add_position: 031980 4주 @ 132,300 (tranche=1, pending=True, atr=14878.57)
03:22:30 load_positions_from_db: 포지션 로드: 2개
```

## 영향 범위
- early_buy 모드(`PARTIAL_PROFIT_EARLY_MONITORING_ENABLED=true`)에서만 1분 간격 → 상시 위험.
- legacy 모드(09:25 매수 / 09:26 재시작)도 동일한 1분 간격 구조이나, 매수 루프가 1분 초과 시 동일 레이스 가능.
- 누락 시 영향: 해당 종목 당일 전(全) 청산 로직 미작동 = 리스크 관리 공백.

## 과거 관련 사건/맥락
- monitor_state 잔재 버그(2026-05-12 한화오션, 5-18 기아) — 그건 "잔재로 인한 오작동", 이번은 "누락으로 인한 미작동"으로 반대 방향.
- 09:26 재시작이 "신규 매수분 포함" 목적인데, 정작 그 신규 매수분이 아직 저장 안 됨 → 설계 의도와 타이밍 불일치가 핵심.

## 검증 도구
- 로그: `grep "포지션 로드:\|add_position\|save_holding_position" logs/system_YYYY-MM-DD.log`
- DB: `SELECT stock_code FROM portfolio WHERE status='holding'` vs `monitor_state.json` keys 비교
- 정합: holding 종목 수 == 모니터 포지션 수 여야 정상
