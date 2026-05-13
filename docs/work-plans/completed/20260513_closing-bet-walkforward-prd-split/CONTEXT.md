# CONTEXT: 종가베팅 walkforward PRD 10-1 분할매도

## 변경 이유

### 1. 평가/운영 정책 불일치
현재 `phase25_simulator.simulate_candidate()`(라인 163~231)는 3 시나리오만 지원:
- `morning_exit`: +1.2% 한 점 100% 매도
- `stop_risk`: -1.0% 한 점 100% 매도
- `market_open`: next_open_pct 한 점 100% 매도

PRD 10-1 시가 액션 매트릭스(`종가베팅_트레이딩_시스템_PRD_v2.0.md` 라인 321~331)는 **5구간 분할매도**:
- 갭업 ≥+2%: 50% 시초가 + 50% 09:30 고가
- +0.5~+2%: 동일 50/50 분할
- 보합/약갭다운/갭다운: 100% 시초가

→ 평가가 운영보다 보수적으로 측정되어 EV 음수 일관.

### 2. 5/11 walkforward 결과 (보수적 평가)
`docs/improvements/phase25_ev_report_20260511.md`:
- 48건 / conservative: EV **-0.32%** / WL **0.56** / Sharpe **-1.27** → 4 게이트 FAIL
- aggressive +0.23%p 개선 (-0.10%) 발견
- 사용자 결정: 100건 도달 시 walkforward 재실행 + score≥2 정책 비교

### 3. 100건 게이트 도달
DB 직접 조회 결과 (2026-05-13 기준):
- 후보 117건 / 라벨 99건 / EV+ 60건 = **60.6%**
- 5/13 후보 17건 5/14 10:00 자동 라벨링 → 116건 도달 예정
- score 3+: 18/22 = **81.8%** / score 2: 26/40 = **65.0%**

## 현재 코드 상태

### `closing_bet_system/backtest/phase25_simulator.py` (528줄)

**모듈 상수** (라인 79~106):
```python
_MORNING_EXIT_TARGET_PCT = 0.012
_STOP_RISK_TARGET_PCT = -0.010
_DEFAULT_BUY_PRICE = 10000.0
_DEFAULT_SHARES = 1
_DEFAULT_SCENARIO_POLICY = "conservative"
_VALID_SCENARIO_POLICIES = ("conservative", "aggressive", "split")
SCENARIO_STOP_RISK = "stop_risk"
SCENARIO_MORNING_EXIT = "morning_exit"
SCENARIO_MARKET_OPEN = "market_open"
SCENARIO_SPLIT = "split"
SCENARIO_EXCLUDED = "excluded"
_RESULT_COLUMNS = ("scenario", "simulated_exit_pct", "simulated_net_pnl_pct", "simulated_excluded_reason")
```

**`EVReport` dataclass** (라인 124~157):
- 11 필수 필드 + 옵션 C split 전용 3 필드 (n_split / p_split / mean_split_pct, default 0)
- 신규 PRD 카운터 추가 시 동일 패턴 (default 0)으로 회귀 영향 차단

**`_map_scenario()`** (라인 375~416):
- stop/morning 라벨 둘 다 NULL → excluded
- policy 분기 (conservative / aggressive / split)
- 둘 다 False → market_open (next_open_pct)
- open_pct NULL → excluded

**`simulate_candidate()` SPLIT 분기** (라인 201~214):
```python
if scenario == SCENARIO_SPLIT:
    morning_pnl = _compute_pnl_pct(engine, _MORNING_EXIT_TARGET_PCT)
    stop_pnl = _compute_pnl_pct(engine, _STOP_RISK_TARGET_PCT)
    avg_pnl = (morning_pnl + stop_pnl) / 2.0
    avg_exit_pct = (_MORNING_EXIT_TARGET_PCT + _STOP_RISK_TARGET_PCT) / 2.0
    return SimulationResult(...)
```

### `closing_bet_system/backtest/phase25_walkforward.py` (625줄)

**모듈 상수** (라인 57~80):
- `_GATE_EV_THRESHOLD = 0.005` / `_GATE_WIN_LOSS_RATIO = 1.3` / `_GATE_SHARPE = 1.0` / `_GATE_MIN_SAMPLES = 100`
- `_SCORE_BUCKETS = (0, 1, 2, 3)`
- `_POLICIES = ("conservative", "aggressive", "split")`
- `_DEFAULT_REPORT_DIR = "docs/improvements"`

**`compare_scenario_policies()`** (라인 218~231): `_POLICIES` 튜플 순회만 하므로 튜플 확장하면 자동 비교

**`generate_report()` default 정책** (라인 290): `policy="conservative"` 하드코딩 — 변경 필요

**`_build_markdown()` 섹션 4** (라인 551~574): 정책 비교 테이블 — `_POLICIES` 순회

**`_evaluate_gate()`** (라인 416~449): 4 게이트 자동 판정 — 그대로 재사용

### `closing_bet_system/backtest/phase25_data_loader.py`

- `_LABEL_PCT_COLUMNS = ("next_open_pct", "next_morning_high_pct", "next_morning_low_pct")` (라인 71)
- `total_score` SELECT (라인 192)
- **무변경**

## 핵심 스니펫: PRD 10-1 시가 액션 매트릭스 매핑

```python
# 옵션 prd_split_optimistic
if open_pct >= +0.005:    # +0.5% 이상 → 분할
    exit_pct = 0.5 * open_pct + 0.5 * morning_high_pct
    scenario = SCENARIO_PRD_SPLIT_GAPUP
elif open_pct <= -0.010:  # -1% 이하 → 즉시 손절
    exit_pct = open_pct
    scenario = SCENARIO_PRD_SPLIT_GAPDOWN
else:                     # 보합/약갭다운 → 100% 시초가
    exit_pct = open_pct
    scenario = SCENARIO_PRD_SPLIT_FLAT

# 옵션 prd_split_realistic
# GAPUP 케이스만 다름:
exit_pct = 0.5 * open_pct + 0.5 * (open_pct + morning_high_pct) / 2
        = 0.75 * open_pct + 0.25 * morning_high_pct
```

## 과거 버그/주의사항

- **NULL 가드**: pandas `pd.isna()` 사용 필수 (CLAUDE.md 규칙) — `_safe_bool` / `_safe_float` 헬퍼 재사용
- **이중 비용 차감 방지**: `cost_engine.compute_pnl()`이 이미 비용 차감 → raw_ev = net_ev
- **morning_high NULL 폴백**: realistic에서 morning_high NULL이면 open_pct만 사용 (graceful)
- **EVReport 신규 필드 default 처리**: 옵션 A/B 회귀 영향 차단 (기존 split 3 필드 패턴 동일)
- **datetime KST**: `from config import now_kst` (CLAUDE.md 규칙)

## 영향 범위

| 시스템 | 영향 |
|---|---|
| 운영 봇 (main.py) | **무영향** |
| main_orchestrator | **무영향** |
| entry_executor / morning_exit_manager | **무영향** (이번 작업은 평가만) |
| DB 스키마 | **무변경** |
| systemd 재시작 | **불필요** |
| 백테스트 모듈 | 2 파일 수정 + 2 파일 신규 (테스트) |

## DB 현황 (2026-05-13 기준)

```
candidates: 117건
candidate_labels: 99건
EV+: 60건 (60.6%)

일자별:
- 5/04: 18건 → 15+ (83.3%, 폭등장)
- 5/07: 18건 → 4+ (22.2%, 약세장)
- 5/08: 18건 → 15+ (83.3%, 폭등장)
- 5/11: 23건 → 17+ (73.9%)
- 5/12: 22건 → 9+ (40.9%)
- 5/13: 17건 → 0+ (라벨링 5/14 10:00 예정)

점수별:
- score 3+: 18/22 = 81.8%
- score 2: 26/40 = 65.0%
- score 1: 13/30 = 43.3%
- score 0: 3/7 = 42.9%
```
