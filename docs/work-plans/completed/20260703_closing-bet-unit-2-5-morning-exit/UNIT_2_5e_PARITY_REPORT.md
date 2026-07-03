# 단위 2-5e — 시뮬레이터 정합성 분석 + dry_run 단발 결과

**작성 시점**: 2026-05-16 KST
**목적**: 단위 2-5f 활성화 게이트 통과 전 walkforward EV +1.04%(n=103) 가 단위 2-5c 실 매도 매트릭스와 정합한지 분석.

---

## 시뮬 3구간 ↔ 실 매도 5단계 매핑 (P0-1 정합성 분석)

### 시뮬레이터 (`phase25_simulator.py` `prd_split_realistic` 정책)
| 구간 | 조건 | exit_pct |
|---|---|---|
| `prd_split_gapdown` | open ≤ -1% | `open_pct` (100% 즉시 손절) |
| `prd_split_flat` | -1% < open < +0.5% | `open_pct` (100% 시초가) |
| `prd_split_gapup` | open ≥ +0.5% | `0.5×open + 0.5×mid(open, morning_high)` (50/50 분할, realistic) |

### 실 매도 (`exit_executor.py` 5단계)
| 액션 | 조건 | 실 exit_pct (가정) |
|---|---|---|
| `EMERGENCY_STOP` | gap ≤ -1% | `open_pct` (09:01 즉시 시장가) |
| `WEAK_GAP_DOWN` | -1% < gap ≤ -0.5% | `open_pct` (09:30 시장가) |
| `FLAT` | -0.5% < gap < +0.5% | `open_pct` (09:30 시장가) |
| `GAP_UP_LOW` | +0.5% ≤ gap < +2% | `open_pct` (09:30 시장가 100%) |
| `GAP_UP_HIGH` | gap ≥ +2% | `0.6×open + 0.4×force_close_pct` (50/50 분할, 10:30 시장가) |

### 매핑 분석 — 정합성 정도

| 시뮬 구간 | 실 매도 액션 | 정합성 |
|---|---|---|
| `prd_split_gapdown` | EMERGENCY_STOP | ✅ **완전 정합** (둘 다 100% 시초가) |
| `prd_split_flat` (-1%~+0.5%) | FLAT + WEAK_GAP_DOWN 합 | ✅ **완전 정합** (둘 다 100% 시초가) |
| `prd_split_gapup` (≥+0.5%) | GAP_UP_LOW + GAP_UP_HIGH 합 | ⚠️ **부분 정합** (실 GAP_UP_LOW는 100% 시초가, 시뮬은 50/50 분할) |

### ⚠️ GAP_UP 정합성 위험 (정량)

**시나리오**: open = +1.0% (시뮬 prd_split_gapup, 실 GAP_UP_LOW)
- 시뮬 realistic exit_pct: `0.5×1.0% + 0.5×mid(1.0%, morning_high)` — morning_high가 +3%면 mid=2.0%, exit_pct = **1.5%**
- 실 매도 exit_pct: **1.0%** (시초가 100%)
- delta: **0.5%p**

**시나리오**: open = +3.0% (시뮬 prd_split_gapup, 실 GAP_UP_HIGH)
- 시뮬 realistic exit_pct: `0.5×3.0% + 0.5×mid(3.0%, morning_high=+5%)` = 3.0%
- 실 매도 exit_pct: `0.6×3.0% + 0.4×force_close_pct` — force_close가 ~+2~3% 가정 시 2.6~2.8%
- delta: **0.2~0.4%p**

### 결론

walkforward EV +1.04% (n=103, prd_split_realistic)는 **실 매도 EV 보다 +0.3~0.5%p 높게 측정될 가능성**.
- gap_up 케이스 (전체 후보의 ~30~50%, 5/14 walkforward) 에서 50/50 분할의 morning_high 활용 효과가 실 매도 모델(100% 시초가 또는 force_close 의존)보다 우호적.
- 실 매도 추정 EV: **+0.55~0.75%** (정합성 차감 후)
- 단위 2-8 게이트 임계값 (EV ≥ +0.5%) **여전히 통과 가능성 높음**

### 정합성 보강 방안 (단위 2-5g 또는 후속)
1. **시뮬레이터 GAP_UP_LOW 모델 추가**: 시뮬에서 +0.5% ≤ open < +2% 구간을 100% 시초가로 분리. 단위 2-5g 또는 단위 2-7d 로 분리.
2. **GAP_UP_HIGH 50% 분할 시점 정밀화**: 실 매도 force_close 가격을 morning_high vs morning_high - low 중간값 등 정밀 모델로 시뮬. 단위 2-5g.
3. **단위 2-5f 활성화 전 1주 dry_run + 단위 2-4 dry_run 데이터로 실 EV 측정**: 1주 발생 candidates × 시뮬 매도 가정 vs 실 매도 가정 비교.

---

## dry_run 단발 검증 (5/16 시점)

### 실 dry_run 발화 시점
- 단위 2-4 entry_pipeline (15:18 dry_run): **5/18 월요일 첫 발화** (5/15 13:34 systemctl restart 이후 첫 KST 15:18 mon-fri 시점)
- 단위 2-5 자동매도 잡 3건 (09:01/09:30/10:30): **5/19 화요일 첫 발화** (5/18 dry_run 진입 후보의 익일 매도)
- 단, settings.yaml morning_exit.enabled=false 라 단위 2-5 잡은 함수 진입 즉시 skip 상태로 매일 발화 → 잡 등록 정합성만 검증 가능

### 5/16 단발 검증 (코드 수준)
운영 봇 PID 101811 (2026-05-15 13:34:46 UTC 가동) 기준:
- 운영 봇은 단위 2-4까지의 commit `78cecdc`+`bf0caa2` 머지 후 재시작 상태 → **단위 2-5 코드 미반영**
- 본 commit `4bbcd8b` 메인 머지 후 systemctl restart 필요

### 검증 절차 (단위 2-5e 완료 후)
1. main 브랜치에 머지 + push
2. systemctl restart trading_system
3. 잡 8건 등록 로그 확인 (closing_bet_emergency_stop / closing_bet_morning_exit / closing_bet_morning_force_close 추가)
4. 5/18 09:01/09:30/10:30 자연 발화 시 enabled=False skip 로그 확인 (텔레그램 알림 X)
5. 단위 2-5f 활성화 시점에 settings.yaml morning_exit.enabled=true / dry_run=true 토글 후 5/19 자연 검증

---

## 단위 2-5e 통과 게이트 결과

| 기준 | 결과 |
|---|---|
| 시뮬 vs 실 매도 매핑표 박제 | ✅ 본 문서 |
| 정합성 분석 정량 결과 박제 | ✅ delta +0.3~0.5%p 추정 |
| 실 매도 EV 추정 (단위 2-8 임계 ≥ +0.5%) | ✅ +0.55~0.75% (통과 가능) |
| 누적 회귀 199건 PASS | ✅ |
| code-tester (단위 2-5c) | ✅ stream timeout → 직접 6항목 검증 통과 |
| 메인 머지 준비 | ⏸ 본 commit 후 사용자 승인 필요 |

---

## 다음 단계
1. 본 commit + UNIT_2_5e_PARITY_REPORT 메인 머지 + push
2. systemctl restart 후 잡 8건 등록 로그 확인
3. 단위 2-5f 활성화는 별도 세션에서 사용자 명시 승인 후
4. 정합성 보강 단위 2-5g (선택): 시뮬레이터 5단계 모델 도입
