# CONTEXT — 종가베팅 위기 시 부분 흡수 + 선별 강화

## 1. 변경 이유 (1줄)
2026-05-27 phase1 후보 4건 전원 price_cap 거부 → MarketGuard DANGER 시 swing_idle 흡수 완전 차단 정책이 후보 단가 분포(중앙값 250k)와 맞지 않아 사실상 진입 불가.

## 2. 현재 코드 상태 (라인 단위)

### 2-1. `closing_bet_system/infra/fund_guard.py`

#### GuardConfig (line 62~80)
```python
@dataclass(frozen=True)
class GuardConfig:
    capital_ratio: float = 0.10                 # base 자금 비중
    max_position_per_stock: float = 0.25
    max_concurrent_positions: int = 4
    max_daily_entries: int = 4
    weekly_loss_limit: float = -0.05
    # 동적 자본 분리 (2026-05-23)
    absorb_swing_idle: bool = False             # default False (롤백 안전)
    swing_capital_ratio: float = 0.9
    closing_bet_pool_cap: float = 0.5
    swing_used_source: str = "cost_basis"
    disable_absorb_on_crisis: bool = True       # CRISIS/DANGER 시 absorb 비활성
    # ← 신규 추가: crisis_absorb_ratio: float = 0.0
```

#### compute_capital_limit (line 251~322)
```python
def compute_capital_limit(self, total_value, *, external_risk_active=False):
    cfg = self.config
    base_pool = int(total_value * cfg.capital_ratio)

    # 흡수 비활성 분기 1: absorb_swing_idle=false
    if not cfg.absorb_swing_idle:
        return base_pool, {"mode": "base_only(absorb_off)", ...}

    # 흡수 비활성 분기 2: 위기 + disable_absorb_on_crisis=true (현행)
    if external_risk_active and cfg.disable_absorb_on_crisis:
        return base_pool, {"mode": "base_only(crisis)", ...}

    # 정상 흡수 경로
    swing_pool = int(total_value * cfg.swing_capital_ratio)
    try:
        swing_used = self._get_swing_used_value()
    except Exception:
        swing_used = swing_pool  # idle=0 강제 (보수)

    swing_idle = max(0, swing_pool - swing_used)
    # ← 신규: external_risk_active 시 부분 흡수 swing_idle = int(swing_idle * cfg.crisis_absorb_ratio)
    cap_amount = int(total_value * cfg.closing_bet_pool_cap)
    dynamic_pool = base_pool + swing_idle
    capital_limit = min(dynamic_pool, cap_amount)
    ...
```

#### from_settings (line 81~117)
- 신규 추가: `crisis_absorb_ratio` 로드 + 범위 검증 (0.0~1.0 clamp, 이탈 시 기본값 사용)

### 2-2. `closing_bet_system/execution/entry_executor.py`

#### EntryExecutorSettings (line 58~85)
```python
@dataclass(frozen=True)
class EntryExecutorSettings:
    enabled: bool = False
    dry_run: bool = True
    score_threshold: int = 2                  # 현행 단일값
    # ← 신규 추가: score_threshold_crisis: int = 2 (default 무변화)
    position_ratio: float = 0.70
    phase1_ratio: float = 0.50
    phase2_enabled: bool = True
    ...
    market_guard_enabled: bool = True
    caution_ratio_multiplier: float = 0.5    # CAUTION/DANGER ratio_mult
```

#### execute_phase1 (line 172~242)
```python
async def execute_phase1(self, trade_date):
    ...
    if status in (MarketStatus.CAUTION, MarketStatus.DANGER):
        ratio_mult = self.settings.caution_ratio_multiplier
        external_risk_active = True

    # Phase 1: 후보 select
    candidates = await asyncio.to_thread(
        self._select_phase1_candidates, trade_date
        # ← 신규: external_risk_active=external_risk_active 추가 전달
    )
```

#### _select_phase1_candidates (line 411~432)
```python
def _select_phase1_candidates(self, trade_date) -> list[dict]:
    # ← 신규 시그니처: , *, external_risk_active: bool = False
    threshold = self.settings.score_threshold
    # ← 신규: threshold = (self.settings.score_threshold_crisis
    #                     if external_risk_active else self.settings.score_threshold)
    top_n = self.fund_guard.config.max_concurrent_positions
    with self.candidate_logger.db.get_cursor() as cursor:
        cursor.execute(
            """SELECT candidate_id, ticker, name, total_score
               FROM candidates WHERE trade_date=?
                 AND candidate_status='recommended' AND total_score >= ?
               ORDER BY total_score DESC, candidate_id ASC LIMIT ?""",
            (trade_date, threshold, top_n),
        )
        return [dict(row) for row in cursor.fetchall()]
```

### 2-3. `closing_bet_system/config/settings.yaml` (현행)

```yaml
fund:
  capital_ratio: 0.10
  max_position_per_stock: 0.25
  max_concurrent_positions: 4
  max_daily_entries: 4
  weekly_loss_limit: -0.05
  absorb_swing_idle: true
  swing_capital_ratio: 0.9
  closing_bet_pool_cap: 0.5
  swing_used_source: "cost_basis"
  disable_absorb_on_crisis: true       # ← false 로 변경
  # crisis_absorb_ratio: 0.5            # ← 신규

entry_executor:
  enabled: true
  dry_run: false
  score_threshold: 2                    # NORMAL 시 적용 (유지)
  # score_threshold_crisis: 3           # ← 신규
  ...
```

## 3. 5/27 사고 데이터 (근거)
- 총 평가금액: 9,333,651원 (시스템 로그 잔고 조회 결과)
- base_pool: 933,365원
- per_stock: 233,341원 (= 933,365 ÷ 4)
- order_amount: 40,834원 (= 233,341 × 0.7 × 0.5 × 0.5)
- 후보 단가:
  - 한화오션(042660): 134,900원 → 1주 매수 불가
  - 삼성전기(009150): 1,630,000원 → 불가
  - SK하이닉스(000660): 2,243,000원 → 불가
  - 현대모비스(012330): 688,000원 → 불가

## 4. 5/26 실거래 데이터 (유일한 진입 사례 — 표본 한계)
| candidate_id | ticker | 종목 | score | phase1 | phase2 | exit | net |
|---|---|---|---|---|---|---|---|
| 244 | 010170 | 대한광통신 | 4 | 27,750 × 2 | None | 26,650 | -4.36% |
| 251 | 403870 | HPSP | 4 | 60,100 × 1 | 60,100 × 1 | 58,000 | -3.89% |

## 5. 시뮬 데이터 (가격상한 미적용 가정)
- 5/27 시뮬: 4종목 평균 -0.04% (현대모비스 +7.10% 단독 만회)
- 5/28 시뮬: 3종목 평균 +2.56% (시장 반등 국면)

## 6. 핵심 스니펫 (변경 위치 정확히)

### 6-1. fund_guard.py compute_capital_limit (line 282~290 사이)
```python
# 기존 (현행)
if external_risk_active and cfg.disable_absorb_on_crisis:
    return base_pool, {"mode": "base_only(crisis)", "base_pool": base_pool, "external_risk": True}

# 변경 후 — 분기 자체는 유지 (disable_absorb_on_crisis=false 시 진입 X)
# 그 아래 swing_idle 계산 직후 부분 흡수 적용
swing_idle = max(0, swing_pool - swing_used)
if external_risk_active and cfg.crisis_absorb_ratio < 1.0:
    swing_idle = int(swing_idle * cfg.crisis_absorb_ratio)
    debug_info["mode"] = "absorb_swing_idle(crisis_partial)"
    debug_info["crisis_absorb_ratio"] = cfg.crisis_absorb_ratio
```

### 6-2. entry_executor.py _select_phase1_candidates (line 411)
```python
# 기존
def _select_phase1_candidates(self, trade_date: str) -> list[dict]:
    threshold = self.settings.score_threshold

# 변경 후
def _select_phase1_candidates(
    self, trade_date: str, *, external_risk_active: bool = False
) -> list[dict]:
    threshold = (
        self.settings.score_threshold_crisis
        if external_risk_active
        else self.settings.score_threshold
    )
```

### 6-3. entry_executor.py execute_phase1 (line 207~209)
```python
# 기존
candidates = await asyncio.to_thread(
    self._select_phase1_candidates, trade_date
)

# 변경 후
candidates = await asyncio.to_thread(
    self._select_phase1_candidates, trade_date,
    external_risk_active=external_risk_active,
)
```

## 7. 과거 버그 — 회피 주의
- **2026-05-23 v17 hotfix**: 종가베팅 충돌 가드 B' — 스윙 풀에서 swing_used 산출. 본 변경은 그 로직에 손대지 않고, swing_idle 흡수 비율만 조정 (정합성 유지)
- **disable_absorb_on_crisis 도입 배경**: 2026-05-23 Plan planner "심각 2 — 시장 위험 신호 시 흡수 차단" 가드. 본 변경은 그 가드를 풀되 **score 컷오프 강화로 보완**

## 8. 영향 범위
- **종가베팅 진입 로직만**: phase1 진입 자본 계산 + score 컷오프
- **스윙 시스템 무관**: swing_used 산출 / fund_guard.allow_order의 swing 보유 차단 동일 유지
- **테스트 영향**: fund_guard 단위 테스트 3건 신규, entry_executor 단위 테스트 3건 신규. 기존 테스트 전수 PASS 보장 필요

## 9. 기각된 대안
- **권장 2안 (base_pool 10%→15%)**: DANGER 시 per_stock 70k 여전히 부족
- **권장 3안 (DANGER 자유 진입)**: 5/26 손실 -4.13% 패턴이 확장될 위험
- **universe 단가 다양화**: 본 작업과 분리 (별도 제안서 필요)

## 10. 참고
- 제안서: `docs/improvements/20260529_closing_bet_capital_limit_crisis_policy.md`
- PLAN: `./PLAN.md`
- CHECKLIST: `./CHECKLIST.md`
- 관련 PRD: `종가베팅_트레이딩_시스템_PRD_v2.0.md`
- 관련 메모리: `memory/project_closing_bet_followups.md`, `memory/project_closing_bet_system.md`
