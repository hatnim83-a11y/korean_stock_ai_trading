# CONTEXT — 종가베팅 금요일/휴장 청산누락 버그

## 현재 코드 상태 (검증된 위치, 2026-06-15 worktree 기준)
### 버그 위치 — `closing_bet_system/main_orchestrator.py`
4개 exit 래퍼가 달력 어제 사용 (함수명으로 검색 권장, 라인 이동 가능):
```python
trade_date = (now_kst().date() - timedelta(days=1)).isoformat()   # run_emergency_stop_check ≈L1016
trade_date = (now_kst().date() - timedelta(days=1)).isoformat()   # run_morning_exit ≈L1034
trade_date = (now_kst().date() - timedelta(days=1)).isoformat()   # run_morning_force_close ≈L1053
trade_date = (now.date() - timedelta(days=1)).isoformat()         # run_morning_trailing ≈L1081 (now 변수)
```
→ 이 trade_date가 `exit_executor.execute_*(trade_date)` → `select_exit_targets(cl, trade_date)`의 `WHERE trade_date = ?`로 직결.

### 정답 패턴 (이미 존재) — 같은 파일 ≈L613, ≈L704
```python
# 직전 영업일 (월요일 → 금요일, 공휴일 건너뛰기)
yesterday = today - timedelta(days=1)
while not is_trading_day(yesterday):
    yesterday = yesterday - timedelta(days=1)
```
`run_label_yesterday` 등이 이미 이렇게 한다. exit 래퍼만 누락.

### config 헬퍼 — `config.py`
- `is_trading_day(check_date=None)` L41 — 주말+공휴일 체크 (holidays.KR). **이미 존재**.
- `count_trading_days(from_date, to_date)` L52.
- `now_kst()` 존재. → 신규 `previous_trading_day()`는 이 둘로 간단 구현.

## 변경 이유 / 영향
- 정상 연속 거래일: 직전 거래일 == 달력 어제 → **동작 동일**(회귀 없음).
- 금/휴장 갭에서만 달라짐 = 정확히 고치려는 케이스.
- 영향 범위: 종가베팅 청산 대상 선정만. 진입(entry_pipeline 오늘 trade_date)·라벨(이미 walk-back)·스윙봇 무관.

## 증거 — 미청산 005935 (candidate_id=437)
`data/closing_bet.db` candidates:
```
candidate_id=437, ticker=005935(삼성전자우), trade_date=2026-06-12,
candidate_status='recommended'(phase1_only), entry_phase1_order_id='0030602600',
entry_phase1_executed_price=206500.0, entry_phase1_executed_shares=7,
entry_phase2_executed_shares=NULL, exit_time=NULL, exit_shares=0
```
- 진입체결+미청산은 이 1건뿐 (나머지 과거 5 entered는 모두 exit_time 박제).
- 청산창=6/15(월) 오전, 구코드(trade_date=일)로 이미 지나감 → **이 fix로 자동회수 안 됨**.
- 회수: 별도 수동. `execute_force_close("2026-06-12")` 호출 시 select_exit_targets가 437을 잡아 remaining 7주 매도(Phase 2A로 remaining_shares 기반 동작 확인됨). 단 **morning_exit.dry_run=false(실매도)**.

## 활성화/배포 맥락 (2026-06-15 직전 작업)
- 종가베팅 수익극대화 Phase 1/2A/2B/2C/3 전부 구현·머지·**활성화 완료**(토글 true, 서비스 재시작 PID 515777, DB v4 마이그레이션 완료).
- 토글: settings.yaml `morning_exit.open_limit_sell_enabled=true`, `morning_trailing_enabled=true`, `score.atr_overheat_band_enabled=true`. 백업 `settings.yaml.bak.20260615_130447`, DB백업 `data/closing_bet.bak.20260615_133016`.
- 이 버그는 활성화와 **무관**(지난 금요일부터 존재). 상세 메모리: `memory/project_closing_bet_exit_profit_max.md`.

## 주의 / 함정
- run_morning_trailing은 변수명이 `now`(now_kst() 박제). 교체 시 `now.date()` 기준 유지하거나 헬퍼에 ref 전달.
- KIS inquire-balance 500 종일 지속 → 005935 API 확인은 **장 마감 후 재시도**. 토큰 1분 1회 제한 — 실행 서비스와 경합 주의(분리 프로세스 호출 시 서비스 토큰 무효화 가능, 다음 잡에서 자연 재발급).
- 005935 수동 force_close를 분리 프로세스로 돌리면 토큰 경합 + 실매도. 서비스 내 트리거가 안전.

## 작업 중 발견 (다음 세션이 채울 것)
- (구현 중 갱신)
