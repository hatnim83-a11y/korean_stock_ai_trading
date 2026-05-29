---
analysis_period: 2026-05-04 ~ 2026-05-29 (실발주 활성화 5/23 이후 4영업일)
mode: focus:closing_bet_capital_limit_crisis_policy
sample_size: 진입 성공 2건 / 후보 등록 278건
generated_at: 2026-05-29 14:00 KST
status: draft (사용자 승인 대기)
---

# 종가베팅 자본 한도 — 위기 시 부분 흡수 + 선별 강화 제안서

## 1. 분석 개요
- **분석 기간**: 실발주 활성화일(2026-05-23) ~ 2026-05-29 (4영업일)
- **대상 시스템**: closing_bet_system (종가베팅, 별도 DB `data/closing_bet.db`)
- **표본**: 후보 등록 278건, 진입 성공 2건, 진입 실패(자동 거부) 다수
- **트리거 이벤트**: 2026-05-27 phase1 후보 4개 전원 `price_cap` 자동 거부 → 사용자가 원인 조사 의뢰

**중요 한계**:
- 실거래 표본이 **2건**(2026-05-26, 둘 다 score=4)에 한정 → 통계적 유의성 확보 불가
- 본 제안은 **score 구간별 EV 분석이 아닌, 구조적 결함(후보 18~20건 중 진입 0건이 다수일자) 기반**
- 신뢰도: **Low** (1주 관찰 후 재평가 필수)

## 2. 트리거 사건 분석 (2026-05-27)

### 사실
| 항목 | 값 |
|---|---|
| MarketGuard 상태 | DANGER (코스피 또는 코스닥 -1% 이상 -2% 미만) |
| phase1 select top4 | 한화오션(s=3), 삼성전기/SK하이닉스/현대모비스(s=2) |
| 총 평가금액 | 9,333,651원 |
| base_pool | 933,365원 (10%) |
| swing_idle 흡수 | **차단** (external_risk_active=True + disable_absorb_on_crisis=true) |
| capital_limit | 933,365원 |
| per_stock 한도 | 233,341원 (= cap_limit ÷ 4) |
| ratio_mult 적용 후 order_amount | **40,834원** (= 233,341 × 0.7 × 0.5 × 0.5) |
| 최저 단가 후보 (한화오션) | 134,900원 |
| 결과 | 4건 전부 quantity=0 → `price_cap` 거부 |

### 자본 한도 산식 (현행)
```
capital_limit  = min(base_pool + swing_idle, total × closing_bet_pool_cap)
                 ※ disable_absorb_on_crisis=true + external_risk_active → swing_idle=0
per_stock      = capital_limit / max_concurrent_positions
order_amount   = per_stock × position_ratio × phase1_ratio × ratio_mult
```

## 3. 시뮬레이션 (가격상한 미적용 가정 — 매수가=종가, 매도가=다음 영업일 시초가)

| 일자 | 종목 | score | 가상 net | MarketGuard |
|---|---|---|---|---|
| 5/26 | 대한광통신 (실제) | 4 | **-4.36%** | NORMAL 추정 |
| 5/26 | HPSP (실제) | 4 | **-3.89%** | NORMAL 추정 |
| 5/27 | 한화오션 | 3 | -1.72% | DANGER |
| 5/27 | 삼성전기 | 2 | -2.83% | DANGER |
| 5/27 | SK하이닉스 | 2 | -2.72% | DANGER |
| 5/27 | 현대모비스 | 2 | **+7.10%** | DANGER |
| 5/28 | 삼화콘덴서 | 3 | +2.51% | (시뮬) |
| 5/28 | LG이노텍 | 3 | +4.89% | (시뮬) |
| 5/28 | 삼성SDI | 3 | +0.28% | (시뮬) |
| 5/29 | — | — | 데이터 부족 | DANGER |

- 9건 단순 평균: **-0.08%** (사실상 본전)
- 시뮬 한계: 매수가=종가는 보수적 추정(실제는 vwap×1.005 또는 일중 고가, 종가보다 1~2% 낮을 가능성), phase2 미반영

## 4. 핵심 발견

### 발견 1: 위기 시 자본 한도가 후보 단가 분포와 정합하지 않음
- DANGER 시 종목당 한도 40k → 단가 100k 이상 후보 전부 자동 거부
- 금주 phase1 select top4 후보 단가 중앙값: **약 250k** (5/27 기준)
- **구조적 mismatch** — 위기 시 보수성과 후보 풀의 단가 분포가 맞지 않음

### 발견 2: DANGER 등급 일자에서 시뮬 결과가 일률적 손실이 아님
- 5/26: -4.13% (위기 첫날 갭다운 누적)
- 5/27: -0.04% (현대모비스 +7.1% 단독 만회)
- 5/28: +2.56% (시장 반등 국면)
- → **DANGER 일률 차단은 회복 국면 기회 손실**

### 발견 3: score 컷오프 상향 시 5/26 손실 사례 그대로 통과
- 5/26 진입 2건은 모두 score=4
- 제안하는 `score_threshold` 2→3 변경은 5/26 손실을 막지 못함
- **선별 강화의 한계** — 단순 score 상향만으로는 위기 손실 차단 불가
- 단, score=2 후보(현재 다수)는 차단 가능 → 표본 부족 구간의 진입 위험 축소

### 발견 4: 표본 부족으로 score별 EV 측정 불가
- 진입 성공 2건 모두 score=4 → score 2/3 진입 사례 0건
- score별 평균 net_pnl 추정 불가 → 본 제안의 효과는 1주 관찰 후 재평가 필수

## 5. 파라미터 조정 제안 (권장 1안)

| 파일 | 파라미터 | 현재값 | 제안값 | 근거 | 신뢰도 |
|---|---|---|---|---|---|
| `settings.yaml` `fund:` | `disable_absorb_on_crisis` | `true` | `false` | 발견 1, 2 | Low |
| `settings.yaml` `fund:` | `crisis_absorb_ratio` (신규) | — | `0.5` | 발견 1 | Low |
| `settings.yaml` `entry_executor:` | `score_threshold` | `2` (단일값) | `2`(NORMAL) / `3`(DANGER·CAUTION) | 발견 3 | Low |

### 5-1. 변경 후 자본 한도 (DANGER 시)
```
swing_pool       = total × swing_capital_ratio (0.9) ≈ 8,400k
swing_used       = (스윙 보유 평가액) — 예: 5/27 종가 매수 후 약 60k
swing_idle       = max(0, swing_pool - swing_used) ≈ 8,340k
crisis_absorb    = swing_idle × crisis_absorb_ratio (0.5) ≈ 4,170k
capital_limit    = min(base_pool(933k) + crisis_absorb(4,170k), cap_amount(4,666k)) ≈ 4,666k (cap 도달)
per_stock        = 4,666k / 4 = 1,166k
order_amount     = 1,166k × 0.7 × 0.5 × 0.5 = 204k
```
→ 한화오션(134k) 1주, 삼성전기(1,630k) 0주, SK하이닉스(2,243k) 0주, 현대모비스(688k) 0주
→ **여전히 대형주 일부는 미매수**. 추가 보완은 후속(단가 다양화) 작업으로 분리.

### 5-2. score 컷오프 동적 적용
- NORMAL: `score_threshold=2` (현행 유지)
- CAUTION/DANGER: `score_threshold=3`
- 5/27 적용 시: 후보 4개 → 1개(한화오션 score=3)만 통과
- 5/26 적용 시: 후보 2개(score=4) 그대로 통과 → 손실 차단 효과 없음, 단 noise 감소

## 6. 변경 위치 (Before/After 코드)

### 6-1. `closing_bet_system/config/settings.yaml`
```diff
 fund:
   capital_ratio: 0.10
   ...
   absorb_swing_idle: true
   swing_capital_ratio: 0.9
   closing_bet_pool_cap: 0.5
   swing_used_source: "cost_basis"
-  disable_absorb_on_crisis: true
+  disable_absorb_on_crisis: false
+  crisis_absorb_ratio: 0.5             # 신규: 위기 시 swing_idle × 0.5 만 흡수
 
 entry_executor:
-  score_threshold: 2
+  score_threshold: 2                   # NORMAL 시 적용
+  score_threshold_crisis: 3            # 신규: CAUTION/DANGER 시 적용
```

### 6-2. `closing_bet_system/infra/fund_guard.py`
- `GuardConfig` 필드 추가:
  ```python
  crisis_absorb_ratio: float = 0.0     # 기본 0.0 (롤백 안전: 위기 시 흡수 없음)
  ```
- `compute_capital_limit()` 분기 수정 (Line 284 부근):
  ```python
  # 위기 시 부분 흡수 (2026-05-29 추가)
  if external_risk_active and cfg.disable_absorb_on_crisis:
      return base_pool, {...}  # 완전 차단 분기 (기존, 폴백)

  # absorb_swing_idle=true + (위기 X 또는 disable_absorb_on_crisis=false)
  swing_pool = int(total_value * cfg.swing_capital_ratio)
  swing_idle = max(0, swing_pool - swing_used)
  # 위기 시 부분 흡수 적용
  if external_risk_active:
      swing_idle = int(swing_idle * cfg.crisis_absorb_ratio)
  ...
  ```

### 6-3. `closing_bet_system/execution/entry_executor.py`
- `EntryExecutorSettings` 필드 추가:
  ```python
  score_threshold_crisis: int = 3     # 기본 2와 동일 시 무변화
  ```
- `_select_phase1_candidates()` 시그니처 변경:
  ```python
  def _select_phase1_candidates(
      self, trade_date: str, *, external_risk_active: bool = False
  ) -> list[dict]:
      threshold = (
          self.settings.score_threshold_crisis if external_risk_active
          else self.settings.score_threshold
      )
      ...
  ```
- `execute_phase1()` 에서 호출 변경:
  ```python
  candidates = await asyncio.to_thread(
      self._select_phase1_candidates,
      trade_date,
      external_risk_active=external_risk_active,
  )
  ```

## 7. 롤백 계획
1. **즉시 롤백 (긴급)**: `settings.yaml` 3줄 원복 (`disable_absorb_on_crisis: true`, `crisis_absorb_ratio` 제거, `score_threshold_crisis` 제거) → `systemctl restart trading_system`
2. **부분 롤백**: 자본 한도만 유지하고 선별 강화만 롤백, 또는 그 반대
3. **코드 롤백**: git revert 단일 커밋 (3문서 단위 작업으로 묶을 예정)

## 8. 활성화 절차 (사용자 승인 후)
1. 본 제안서 사용자 승인
2. CLAUDE.md 글로벌 규칙대로 **PLAN.md / CONTEXT.md / CHECKLIST.md 3문서 작성**
   (`docs/work-plans/active/closing-bet-crisis-capital-policy/`)
3. 단위 1: GuardConfig + compute_capital_limit 수정 + 단위 테스트
4. 단위 2: EntryExecutorSettings + _select_phase1_candidates 수정 + 단위 테스트
5. 단위 3: settings.yaml 변경 (단, `crisis_absorb_ratio=0.0`으로 시작 → 점진 활성화 안전)
6. code-tester 에이전트 검증 → systemctl restart → 텔레그램 1주 관찰
7. 1주 후 재평가 — 진입 건수, MarketGuard 등급별 진입 비율, 평균 net_pnl_pct
8. `change_log.md` before/after 라인 추가

## 9. 관찰 지표 (1주 후 재평가용)
- [ ] DANGER 일자 진입 건수 (목표: 일평균 ≥ 1건)
- [ ] DANGER 일자 평균 net_pnl_pct (목표: 손실 -3% 이내)
- [ ] score=2 후보 진입 차단 효과 — DANGER 시 score=2 후보 진입 0건 확인
- [ ] NORMAL 시 진입 건수 변화 — 변동 없어야 함 (현행 유지)
- [ ] swing 시스템 자본 침해 여부 — 스윙 매수 실패 0건 확인

## 10. 기각된 가설 (보수성 측면)

### 기각 1: "DANGER도 정상 진입 허용 (권장 3안)"
- 5/26 데이터(-4.13%)로 단독 데이터점이지만 위기 첫날 손실 패턴 가능성
- 일률 허용은 손실 ×4~5 확장 위험 → 부분 흡수(50%)로 절충

### 기각 2: "base_pool 비율 상향 (10%→15%, 권장 2안)"
- DANGER 시 per_stock 한도 70k → 여전히 대부분 후보 단가 미달
- 효과 약함 + 정상장 노출 증가의 trade-off 불리

### 기각 3: "universe 단가 다양화 (옵션 H)"
- 대형주 비중 축소 + 중소형주 보강 → 종목당 1주 단가 낮춤
- 본 제안과 무관한 별도 작업 → 후속 단위로 분리

## 11. 미결 후속 작업
- universe 단가 다양화 (별도 제안서 필요)
- swing 매수 실패 모니터링 강화 (fund_guard 침해 알림)
- DANGER 시 `caution_ratio_multiplier` (0.5) 추가 완화 검토 (1주 관찰 후)

## 12. 메타 정보
- 제안서 작성 도구: 메인 컨텍스트 (trade-improvement-analyst 에이전트 호출 중 API Overloaded 발생, 사용자 결정으로 직접 작성)
- 데이터 출처: `data/closing_bet.db.candidates`, `logs/system_2026-05-*.log`, pykrx OHLCV
- 신뢰도 등급 근거: 실거래 표본 2건 → Low (CLAUDE.md 매뉴얼 기준)
- 코드 변경은 본 제안서 승인 후 3문서 단위 별도 작업으로 진행
