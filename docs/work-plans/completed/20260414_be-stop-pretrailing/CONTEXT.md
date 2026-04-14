# CONTEXT — BE 손절 프리-트레일링

## 변경 이유
사후평가(trade_reviews) 누적 분석 결과, +5~+8% 구간에서 방어 장치 부재로 수익이 손실로 뒤집힌 사례 확인. 트레일링 L1(+8%)이 작동하기 전 조정에서 무방비.

## 현재 코드 상태

### config.py
- `239-243`: `DEFAULT_STOP_LOSS = -0.05`
- `248-255`: `GRACE_PERIOD_DAYS = 1`, `GRACE_PERIOD_STOP_LOSS = -0.08`
- `303-306`: `TRAIL_ACTIVATION_PCT = 0.08` ← 이 바로 앞에 BE 상수 삽입 예정
- `307-310`: `TRAIL_LEVEL1_PCT = 0.04`

### portfolio_monitor_v2.py
- `75`: Position 데이터클래스 `trailing_active`, `max_profit_rate` 필드 존재
- `193-198`: PortfolioMonitor `__init__` 트레일링 설정 읽기 — 이 블록 끝에 BE 3개 추가
- `996-1007`: `_update_trailing_stop()` 진입부, `max_profit_rate` 업데이트 (1006-1007)
- `1008-1032`: 이익 추종 if/elif 체인 — BE 블록은 이 블록 **앞**에 삽입 (독립 동작)
- `1031`: L1 진입 시 `pos.stop_loss_price = pos.buy_price` (BE 0%, BE-1%보다 높으므로 자연 오버라이드)

## 핵심 코드 스니펫 (삽입 위치)

```python
# line 1006-1007
if profit_rate > pos.max_profit_rate:
    pos.max_profit_rate = profit_rate

# [여기에 신규 BE 블록 삽입]

# line 1009 (기존)
if self.enable_profit_trailing:
    ...
```

## 관련 과거 작업
- `docs/work-plans/completed/march-analysis-improvements/` (아직 active) — Day1 Grace Period(-8%) 도입한 직전 작업
- `docs/analysis/dip_recovery_trades.md` — 전수조사 근거 (21 포지션)

## 영향 범위
- **직접**: `config.py`, `modules/trading_engine/portfolio_monitor_v2.py`
- **간접**: `position_state.max_profit_rate` 컬럼에 의존 (이미 존재) — 스키마 변경 없음
- **부작용 없음**: `stop_loss_price`는 in-memory, `enable_profit_trailing` 플래그와 독립

## 작업 중 발견 사항
- BE는 `max_profit_rate` 기준이므로 재시작 후에도 복원됨 → 별도 영속화 플래그 불필요
- L1 활성화 시 stop_loss_price = buy_price가 BE-1%보다 높아 자연 단조성 유지
- DB 스키마 변경 불필요 (v12 유지)
