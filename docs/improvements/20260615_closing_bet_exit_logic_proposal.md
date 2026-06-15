---
analysis_period: 2026-05-26 ~ 2026-06-12
mode: focus:closing_bet_exit
sample_size: 5 (실거래 entered, entry_time IS NOT NULL)
generated_at: 2026-06-15 KST
confidence_overall: Low (표본 5건 — 메커니즘 명확, 통계 확신 제한)
approval_required: true
---

# 종가베팅 청산 로직 개선 제안서 — 시장가 투매 → 시가 지정가 + 오전 트레일링

## 0. 요약 (TL;DR)

종가베팅 실거래 5건이 라벨(신호)은 양호함에도 실현손익 평균 **−3.00%**로 전멸한 원인은
**진입/신호가 아니라 청산 체결 타이밍**이다. `exit_executor.py`의 09:01 emergency_stop +
09:02 morning_exit가 **둘 다 `sell_market_order`(시장가)만** 호출해, 시가 직후 형성되는
오전 dip(저점)에 시장가로 투매한다. 5건 전부 **실제 청산가가 익일 시가 대비 −1.78% ~ −6.30%
아래**에서 체결됐다.

반사실(counterfactual) 추정: 동일 5건을 **시가 지정가 매도**로만 바꿔도 실현 평균
**−3.00% → +0.49%**, 부분익절/오전 캡처를 더하면 **+1.55%**까지 회복 가능(비용 0.41% 차감 후).

단, **표본 5건**뿐이라 통계적 확신은 낮다. 메커니즘은 로그·DB로 명확하나, 본 제안은
**dry_run 재검증 + 추가 관찰**을 전제로 한 **토글 기반 단계 적용**을 권고한다.

---

## 1. 분석 개요

- 분석 기간: 2026-05-26 ~ 2026-06-12 (실거래 발생일 기준)
- 대상 데이터: `data/closing_bet.db` (절대경로 `/home/hatni/korean_stock_ai_trading/data/closing_bet.db`)
- 실거래(entered, `entry_time IS NOT NULL`): **5건**
- 라벨 완료(candidate_labels JOIN): 5/5
- 청산 경로: `closing_bet_system/execution/exit_executor.py` (단위 2-5c)
- 상위 분석 문서: `docs/improvements/20260615_closing_bet_candidate_full_analysis.md` (권고 1순위 = 본 제안서)
- Claude API 추가 호출: **없음** (정량 SQL + 코드 정독만)

> **⚠️ 표본 경고**: N=5. operational_review 게이트(30건)·auto_decision 게이트(100건)
> 어디에도 못 미친다. 본 제안의 신뢰도 등급은 전부 **Low**이며, "메커니즘 입증 + 방향 제시"
> 수준이다. 파라미터 실배포 전 dry_run 단발 검증을 **반드시** 선행한다.

---

## 2. 현황 — 실거래 5건 재확인 (SQL 원문 + 수치)

### 쿼리 (재확인용)
```sql
SELECT c.trade_date, c.name, c.ticker, c.net_pnl_pct, c.entry_price, c.exit_price, c.exit_time,
       l.next_open_pct, l.next_morning_high_pct, l.next_morning_low_pct
FROM candidates c
LEFT JOIN candidate_labels l ON c.candidate_id = l.candidate_id
WHERE c.entry_time IS NOT NULL
ORDER BY c.trade_date, c.name;
```
> 단위 주의: `net_pnl_pct`·라벨은 **분수**(0.03 = +3%).

### 결과
| 일자 | 종목 | 실현(net) | 익일시가갭 | 아침고가 | 아침저가 | 청산시각 | 청산경로 |
|---|---|---|---|---|---|---|---|
| 05-26 | 대한광통신 | **−4.36%** | +0.00% | +0.18% | −8.09% | 09:02:02 | morning_exit(flat) |
| 05-26 | HPSP | **−3.89%** | **+3.00%** | +3.16% | −9.82% | 09:02:04 | morning_exit(flat) |
| 06-09 | HPSP | **−4.08%** | −1.93% | **+4.56%** | −5.79% | 09:01:01 | emergency_stop |
| 06-10 | HD현대중공업 | **−3.52%** | −0.47% | −0.47% | −4.99% | 09:02:03 | morning_exit(weak_gap_down) |
| 06-11 | LG에너지솔루션 | **+0.87%** | +3.90% | **+7.67%** | +2.47% | 09:02:02 | morning_exit(gap_up_high) |

- **n=5, 실현 평균 −3.00%, 합계 −14.98%, 승률 1/5 (20%)**
- 같은 5건 라벨: 익일시가갭 평균 **+0.90%**, 아침고가 **+3.02%**, 아침저가 **−5.24%**

### 핵심 증거 — 청산가가 시가보다 한참 아래에서 체결됨
진입가(=전일 종가 근사)에 라벨 `next_open_pct`를 적용해 익일 시가를 환산하고, 실제 청산가와 비교:

| 일자 | 종목 | 진입가 | 익일시가(추정) | 실제청산가 | **청산가 − 시가** |
|---|---|---|---|---|---|
| 05-26 | 대한광통신 | 27,750 | 27,750 (+0.00%) | 26,650 | **−3.96%** |
| 05-26 | HPSP | 60,100 | 61,900 (+3.00%) | 58,000 | **−6.30%** |
| 06-09 | HPSP | 57,100 | 55,998 (−1.93%) | 55,000 | **−1.78%** |
| 06-10 | HD현대중공업 | 642,000 | 638,995 (−0.47%) | 622,000 | **−2.66%** |
| 06-11 | LG에너지솔루션 | 389,500 | 404,695 (+3.90%) | 394,500 | **−2.52%** |

> **결론**: 5건 모두 실제 체결가가 익일 시가보다 **−1.78% ~ −6.30% 낮다.** 시장가 매도가
> 시가가 아니라 **시가 직후 오전 dip에 투매**되고 있다는 직접 증거. HPSP 5/26은 시가가
> +3.00%였는데도 −6.30% 아래 체결 → 신호(라벨)는 맞았으나 청산이 엣지를 통째로 반납.

---

## 3. 근본 원인 — `exit_executor.py` 코드 인용

### 3-1. 모든 청산 경로가 시장가 단일 (`_execute_market_sell`)
`exit_executor.py:475~518` — emergency_stop / morning_exit / force_close가 **모두**
이 함수를 통해 `sell_market_order`만 호출한다. 지정가 분기가 없다.

```python
# exit_executor.py:513
sell_result = await asyncio.to_thread(
    self.kis_order_api.sell_market_order, target.ticker, quantity,
)
```

### 3-2. emergency_stop: −1% 시가에서 **전량 시장가** (`map_action`)
`exit_executor.py:118~128` + `ExitExecutorSettings.hard_stop_loss = -0.01`(line 67):

```python
def map_action(gap_rate, settings):
    if gap_rate <= settings.hard_stop_loss:      # ≤ -1%
        return ExitAction.EMERGENCY_STOP          # → 09:01 전량 시장가
    ...
```
- 시가가 −1% 이하면 09:01에 **전량 시장가**. 그러나 HPSP 6/9는 −1.93% 갭하락 시가에서
  손절했는데 **그 직후 아침고가 +4.56%로 반등**. 즉 −1% 손절 임계가 갭하락 종목을 **반등 직전
  저점에 던지는** 구조.

### 3-3. morning_exit: gap_up_high만 50% 분할, 나머지는 **전량 시장가** + 트레일링 없음
`exit_executor.py:389~402`:
```python
if action == ExitAction.GAP_UP_HIGH:             # 시가 ≥ +2% 만 50% 분할
    partial_qty = max(int(target.total_shares * gap_up_high_partial_ratio), 1)
    ...
return await self._execute_market_sell(...,      # flat/weak_gap_down/gap_up_low = 전량 시장가
    quantity=target.total_shares, ...)
```
- flat(−0.5%~+0.5%)·weak_gap_down(−1%~−0.5%)·gap_up_low(+0.5%~+2%)는 **전량 시장가 즉시 청산**.
  오전 보유·트레일링·부분익절 여지가 전혀 없다. `settings.yaml exit.trailing_stop_pct = -0.015`가
  정의돼 있으나 **단위 2-5g 미구현**(코드 미존재)이라 사실상 dead config.

### 3-4. 청산 시각: 09:01 / 09:02 — 시가 직후 변동성 구간
`settings.yaml schedule.emergency_stop_start = "09:01"`, `morning_exit_start = "09:02"`.
- 09:02는 시초가 동시호가 직후 1~3분 변동성이 가장 큰 구간. 이때 시장가 매도는 호가가 얇아
  하단 호가를 쓸어내며 체결(slippage). 2-2의 −1.78~−6.30% 갭이 이를 뒷받침.

### 3-5. 사용 가능한 도구 — 지정가 매도는 이미 존재
- `kis_order_api.py:377 sell_limit_order(stock_code, quantity, price)` 존재.
- `morning_price_collector.py`의 `MorningPriceSnapshot`은 **이미 `open_price`(stck_oprc)·
  high·low·current_price 4필드 보유**(line 35~38). 즉 시가 지정가 매도 구현에 추가 데이터 수집 불필요.

---

## 4. 반사실(counterfactual) 정량 추정 — 5건 적용 시 기대효과

라벨(`next_open_pct`/`next_morning_high_pct`)을 비용 **0.41%**(왕복: 매수수수료 0.015% +
매도수수료 0.015% + 거래세 0.18% + 슬리피지 편도 0.1%×2) 차감해 시나리오별 실현을 추정.

| 시나리오 | 정의 | 평균 | 합계 | 승률 |
|---|---|---|---|---|
| **현행(실측)** | 09:01/09:02 시장가 | **−3.00%** | **−14.98%** | 1/5 |
| **[A] 시가 지정가 매도** | `next_open_pct − 비용` | **+0.49%** | +2.45% | 2/5 |
| **[B] 시가~오전고가 중간 캡처** | `(open+high)/2 − 비용` | **+1.55%** | +7.75% | 3/5 |
| **[C] emergency만 시가매도 + 나머지 고가−1.5% 트레일링** | emergency 구간(시가≤−1%)은 시가매도, 그 외 `max(high−1.5%, open) − 비용` | **+0.94%** | +4.72% | 2/5 |

> 추정 한계: [B]/[C]는 "오전고가를 실제로 잡을 수 있다"는 낙관 가정. 실제 트레일링은 고점
> 대비 슬랙만큼 못 잡는다. 그래도 **[A] 시가 지정가만으로도 −3.00%→+0.49%(스프레드 ≈ +3.5%p)**
> 라는 점이 핵심 — 가장 보수적인 [A]조차 현행 대비 압도적이다.

### 시가-청산가 갭이 곧 개선 여력
2-2의 "청산가 − 시가" 평균 = (−3.96 −6.30 −1.78 −2.66 −2.52)/5 = **−3.44%p**.
이 −3.44%p가 시장가 투매로 날린 엣지이며, 시가 지정가 전환으로 회수 가능한 상한선이다.

---

## 5. 개선 제안 (before/after + 신뢰도)

> 모든 제안은 **토글 + dry_run 우선** 원칙. 표본 5건이므로 **Low 신뢰도**.
> 파라미터 박제값은 `ExitExecutorSettings`(frozen) + `settings.yaml exit:*` 양쪽에 정합 유지.

### 제안 ① (1순위) morning_exit 시장가 → **시가 지정가 매도** 전환
| 항목 | Before | After |
|---|---|---|
| 발주 방식 | `sell_market_order(ticker, qty)` | `sell_limit_order(ticker, qty, price=snap.open_price)` (미체결 시 deadline 후 시장가 폴백) |
| 동작 | 시가 직후 dip에 시장가 투매 | 시가 호가에 지정가 → 시가 근처 체결, 못 잡으면 force_close 안전망 |
| 코드 위치 | `_execute_market_sell` 단일 경로 | 신규 `_execute_open_limit_sell` 분기 + `fill_check_deadline_sec`(현 60초) 후 시장가 폴백 |

- **근거**: 4절 [A] — 5건 평균 −3.00%→+0.49%. 2-2 청산가 −3.44%p 갭이 회수 대상.
- **리스크**: 시가가 하락 추세면 지정가 미체결로 더 떨어질 수 있음 → **deadline(60초) 후
  시장가 폴백** 필수. 이 폴백이 현행 동작의 안전망이라 다운사이드는 제한적.
- **신뢰도**: **Low** (n=5, 메커니즘 명확하나 표본 미달).

### 제안 ② (2순위) emergency_stop `hard_stop_loss` 임계·방식 재검토
| 항목 | Before | After (검토안) |
|---|---|---|
| 임계 | 시가 ≤ −1% → 전량 시장가 즉시 | 옵션 a: 시가 지정가로 전환(즉시성 유지하되 시장가 투매 제거) / 옵션 b: 임계 −1%→−2% 완화 |
| 시점 | 09:01 일괄 | 동일(시가 확정 직후) |

- **근거**: HPSP 6/9 — −1.93% 시가에서 emergency 손절 직후 아침고가 **+4.56%** 반등.
  −1% 임계가 "약갭하락 후 반등" 종목을 저점에 던짐. 단 표본 1건이라 임계 변경(옵션 b)은
  근거가 매우 약함 → **옵션 a(시가 지정가화)를 우선**, 임계 완화는 보류.
- **리스크**: 임계 완화(−1%→−2%)는 진짜 급락 종목 손절을 늦춰 −2%~−5% 추가 손실 노출.
  표본 1건으로 결정 불가 → **이번엔 임계 불변, 발주 방식만 지정가화** 권고.
- **신뢰도**: **Low** (n=1 의존, 임계 변경은 보류 / 발주 방식 전환만 제안 ①과 묶어 적용).

### 제안 ③ (3순위, 후속) 오전 부분익절/트레일링 도입 (단위 2-5g 활성화)
| 항목 | Before | After |
|---|---|---|
| gap_up_high 외 | 전량 즉시 청산, 트레일링 없음 | 1차 시가 부분익절(예: 50%) + 잔여 오전 트레일링(고점 −1.5%, 10:30 force_close) |
| trailing config | `exit.trailing_stop_pct=-0.015` 정의됐으나 **미구현(dead)** | 폴링 루프 구현(단위 2-5g) — 별도 작업 분리 |

- **근거**: 4절 [B]/[C] — 평균 +1.55%/+0.94%. 미진입 상위 종목 오전고가 +8~15% 빈번
  (분석 doc 5절)이라 오전 상방 캡처 여지 큼.
- **리스크**: 폴링 루프 신규 구현 = 코드 복잡도·동시성(sell_lock) 리스크. **별도 작업(2-5g)으로
  분리**, 본 제안서 범위 밖. 제안 ①·② 효과 확인 후 착수.
- **신뢰도**: **Low** + 구현 미존재 → **관찰 항목으로만 등록**.

### 적용하지 않는 것 (명시)
- `gap_up_high_threshold`(+2%)/`gap_up_low_threshold`(+0.5%)/`flat_lower`(−0.5%) **임계값은 불변**.
  5건 중 구간 분포가 너무 적어(gap_up_high 1·flat 2·weak_gap_down 1·emergency 1) 조정 근거 없음.
- `gap_up_high_partial_ratio`(0.6) 불변 — 해당 구간 표본 1건.

---

## 6. 리스크 / 부작용

1. **지정가 미체결 리스크**: 하락 추세 시가에서 시가 지정가가 안 잡히면 더 떨어진 채 force_close.
   → `fill_check_deadline_sec`(60초) 후 **시장가 폴백**으로 현행 최악 케이스 = 현 동작과 동일하게
   바운드. 다운사이드 제한적.
2. **동시성(sell_lock)**: 신규 분기도 기존 owner 네임스페이스(`closing_bet:emergency_stop|morning_exit`)
   + `use_sell_lock` 재사용. 메인 봇 09:00 monitoring_start_early race 가드(09:01/09:02 오프셋) 유지.
3. **시뮬 정합 깨짐**: 코드 주석 P0-1 "모든 액션 시초가 시장가 매도로 통일 → phase25_simulator
   open_pct 가정 일치". 시가 **지정가**는 체결가가 시가 근처라 **오히려 시뮬 가정(open_pct)에
   더 부합**. 단 phase25_simulator 가정 문서를 함께 갱신해야 함(검증계획 참조).
4. **표본 5건의 과적합 위험**: 5건에 맞춘 튜닝이 일반화 안 될 수 있음. → dry_run 재검증으로 완화.

---

## 7. 롤백 계획

- **토글 우선 설계**: 신규 동작은 `settings.yaml`에 `exit.open_limit_sell_enabled`(가칭, default false)
  토글로 진입. **false + systemctl restart → 기존 시장가 경로 그대로 NO-OP.**
- `ExitExecutorSettings`는 frozen dataclass라 runtime 변경 불가 → 토글 OFF + 재시작이 유일·확실한 롤백.
- emergency_stop 발주 방식 변경도 동일 토글 하위. 임계값(hard_stop_loss=-0.01)은 **변경하지 않으므로
  롤백 대상 아님**.

---

## 8. 검증 계획 (배포 전 필수)

1. **dry_run 단발 검증** (`morning_exit.dry_run=true` 유지, 신규 토글 ON): 다음 진입 발생 익일,
   "DRY_RUN open_limit_sell" 로그로 **지정가 발주 가격 = snap.open_price** 확인. KIS 실발주 0.
2. **code-tester 에이전트**: 신규 분기 + 시장가 폴백 + sell_lock 정합 단위 테스트 (CLAUDE.md 필수 프로세스).
3. **phase25_simulator 가정 갱신**: open_pct 기준이 지정가 체결과 정합한지 시뮬 문서 동기화.
4. **실발주 전환 후 1주 관찰**: 다음 entered 거래들의 "청산가 − 시가" 갭이 **−3.44%p → 0 근처**로
   수렴하는지 2-2 쿼리 재실행. 동시에 `weekly_loss_limit`(−0.10) 모니터.
5. **추가 표본 축적**: entered 5→**최소 15건**까지 누적 후 본 제안 재평가(Low→Medium 승급 조건).
   현 진입률(5건/25일)이면 약 5~6주 소요 → **2026-07-말 재평가** 목표.

---

## 9. 메타 정보

- Claude API 추가 호출: **아니오** (정량 SQL + 코드 정독으로만 교차 검증)
- 상위 분석 문서 흡수: **예** (`20260615_closing_bet_candidate_full_analysis.md` 권고 1순위 구체화)
- JSON 파싱: 해당 없음 (종가베팅 DB는 ai_review 자유서술 필드 미사용, 정량 라벨만)
- 표본: N=5 (operational/auto_decision 게이트 모두 미달 → 전 제안 **Low 신뢰도**, dry_run 전제)
- 데이터 단위 주의: `net_pnl_pct`·라벨 전부 분수(0.03=3%). DB 절대경로 = 메인 repo `data/closing_bet.db`
- 이관: 사용자 승인 시 `/plan` 호출 → strategy-planner 3문서. CHECKLIST 배포 항목에
  **"`docs/improvements/change_log.md` 1줄 추가"** 포함 필수 (exit 발주 방식 변경 기록).
- 재현 SQL: 2절·2-2 쿼리 원문. 반사실: 비용 0.0041 차감 후 next_open/morning_high 조합(4절 표).
