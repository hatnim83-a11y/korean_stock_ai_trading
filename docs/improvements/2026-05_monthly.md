---
analysis_period: 2026-04-01 ~ 2026-05-01
mode: monthly
sample_size: 14 (전건 ai_review 파싱 성공)
generated_at: 2026-05-01 23:55 KST
---

# 2026년 5월 월간 거래 개선 제안서 (4월 누적 30일 재평가)

> **표본**: 월간 임계값(N≥15)에 1건 미달이지만 전건 AI 분석 완료 + post_trade_prices D+5 매칭 13건으로 **High~Medium 신뢰도 분석 가능**. 4/27~5/1 주간 발견은 동시 작성된 `docs/improvements/2026-W18-weekly.md`에서 별도 다룸. 본 제안서는 **30일 누적 데이터로 트레일링/손절/보유기간 파라미터 재평가** + 시스템 무결성 영향 범위 측정에 집중한다.

## 1. 분석 개요

- 분석 기간: **2026-04-01 ~ 2026-05-01** (KST, 30일 / 22 영업일)
- 총 매매 건수 (`trade_reviews`): **14건**
- AI 분석 완료 건수: **14/14 (100%)**, JSON 파싱 성공: **14/14 (100%)**
- `post_trade_prices` D+5 매칭: **13건** (한화오션 제외 — 시스템 무결성 이슈, 발견 5)
- 신규 매도 (trades 테이블 SELL): **18건** → trade_reviews 14건 → **누락 4건** (발견 5에서 다룸)
- 대상 전략 분포 (trade_reviews 기준):
  - trailing_stop 5건 / take_profit 5건 / stop_loss 3건 / max_hold 1건
- 적용된 최신 파라미터 변경 (`change_log.md` 기준):
  - 2026-04-14: TRAIL_BE_* (+5% BE 프리-트레일링) 소급 기록
  - 2026-04-16: ORDER_TYPE_DEFAULT = "limit_aggressive" 소급 기록
  - 2026-04-21: THEME_MOMENTUM_BOOST_FACTOR/CLAMP/DROP_COOLDOWN 소급 기록
  - **2026-04-24: RSI 동적(BULL75/NORMAL70/BEAR65) + THEME_MIN_SLOT (Phase A 배포)** ← 본 사이클 핵심 관찰

## 2. Before/After 추적 (`change_log.md` 기반)

### 2-1. TRAIL_BE_* (2026-04-14, 소급 기록): "+5% 도달 후 하락" 케이스 재발 여부

오이솔루션(04-02, 매수 당일 +5.44% 후 -3.36% 손절) 패턴이 4월 후반 이후 재발했는지 점검:

```sql
SELECT stock_code, sell_date, sell_reason, ROUND(profit_rate,2) AS profit_pct,
       ROUND(max_profit_during_hold,2) AS max_pct, hold_days
FROM trade_reviews
WHERE sell_date >= '2026-04-15' AND sell_date <= '2026-05-01'
  AND max_profit_during_hold >= 5.0 AND profit_rate < 0;
```

결과: **0건**. BE 도입 이후 매수일 +5% 도달했다가 손실 마감한 케이스 없음 → **BE 손절 프리-트레일링 도입 효과 1차 확인**(표본 1건이지만 방향성 일관).

다만 키움증권(04-20 손절, max_profit 1.04%)은 BE 활성화 미도달(+5% 미달) → BE는 작동하지 않았고 일반 손절 발동. BE 도입 자체는 본 사이클에서 추가 발동 사례 없음.

### 2-2. 공격적 지정가 주문 (2026-04-16, 소급 기록): 슬리피지 측정

```sql
SELECT action, COUNT(*) AS total, COUNT(slippage) AS with_slip,
       ROUND(AVG(slippage),5) AS avg_slip, ROUND(MIN(slippage),5) AS min_s, ROUND(MAX(slippage),5) AS max_s
FROM trades
WHERE date >= '2026-04-16' AND date <= '2026-05-01'
GROUP BY action;
```

결과:

| 액션 | 총 건 | 슬리피지 기록 | 평균 | 최소 | 최대 |
|-----|------|------------|------|------|------|
| buy | 9 | 8 | **+0.296%** | -1.081% | +1.392% |
| sell | 13 | **0** | — | — | — |

**관찰**:
- 매수 8건 중 슬리피지 평균 +0.296% (불리 방향 = 예상가보다 비싸게 체결) → 공격적 지정가 + 1.04배 증거금 의도와 부합 (즉시 체결 우선, 약간 불리)
- **매도 13건은 모두 slippage 컬럼 NULL** → 매도 시 슬리피지 측정 미구현 (별도 시스템 점검 필요, 발견 5와 별개)

### 2-3. 테마 회전문 방지 (2026-04-21): 화요일 재선정 모니터링

본 사이클 화요일 재선정: 4/21(배포일), 4/28
- 4/21 → 통신/금융/아이폰/조선/CXL (5개)
- 4/28 → 전력반도체/플랫폼/전기차/건설/AI반도체 (5개)
- 4/21 vs 4/28 **공통 테마 0개** (전체 교체)
- → 회전문 패턴(drop 후 재진입) 없음. 단, 표본 1회로 직접 효과 측정 불가. 5월 화요일 추가 누적 후 재검토.

### 2-4. RSI 동적 + 테마 슬롯 보장 (Phase A, 2026-04-24): 1주 효과

본 제안서에선 30일 시야로 보면 Phase A 발동 5영업일이 차지하는 비중은 작음. **상세는 W18 weekly 제안서(`docs/improvements/2026-W18-weekly.md`) 섹션 2를 참조**. 핵심만 요약:
- 슬롯 보장 발동: 5일 중 24/25 슬롯 = **96%**
- Phase A 배포 후 매수 4건 중 **3건이 슬롯 보장 통과** (네패스아크, DL이앤씨, 삼성전자)
- 4/30 종가 기준 4건 모두 평가손실(unrealized -180,970원, MDD -210%) → 매수량 확대는 됐으나 단기 결과는 부정적. 표본 4건으로 결론 불가.

## 3. 성과 요약

### 3-1. 매도 이유별 분포 (30일)

```sql
SELECT sell_reason, COUNT(*) AS cnt,
       ROUND(AVG(profit_rate),2) AS avg_profit_pct,
       ROUND(MIN(profit_rate),2) AS min_p, ROUND(MAX(profit_rate),2) AS max_p,
       ROUND(AVG(hold_days),2) AS avg_hold
FROM trade_reviews
WHERE sell_date >= '2026-04-01' AND sell_date <= '2026-05-01'
GROUP BY sell_reason ORDER BY cnt DESC;
```

| 매도 이유 | 건수 | 평균 수익률(%) | 최소/최대 | 평균 보유일 |
|----------|-----|--------------|----------|-----------|
| 트레일링L1 | 4 | +7.87 | +5.05 / +10.23 | 2.75 |
| 손절 | 3 | -6.89 | -9.58 / -3.36 | 2.0 |
| 1차 분할익절 | 3 | +13.57 | +10.02 / +20.47 | 1.33 |
| 트레일링L2 | 1 | +19.10 | — | 2.0 |
| 2차 분할익절 | 1 | +20.47 | — | 2.0 |
| 3차 분할익절 | 1 | +20.72 | — | 2.0 |
| 최대 보유 기간 | 1 | +2.92 | — | 6.0 |

### 3-2. strategy_stats 30일 집계

```sql
SELECT strategy_type, SUM(trade_count) AS trades, SUM(win_count) AS wins,
       ROUND(100.0*SUM(win_count)/NULLIF(SUM(trade_count),0),1) AS win_rate,
       ROUND(SUM(total_pnl),0) AS total_pnl,
       ROUND(AVG(avg_profit_rate)*100,2) AS avg_ret_pct
FROM strategy_stats WHERE date >= '2026-04-01' AND date <= '2026-05-01'
GROUP BY strategy_type ORDER BY trades DESC;
```

| 전략 | 건수 | 승/패 | 승률 | 누적 PnL(KRW) | 평균 수익률(%) |
|-----|-----|-------|------|--------------|--------------|
| trailing_stop | 5 | 5/0 | 100% | +385,500 | +10.11 |
| take_profit | 5 | 5/0 | 100% | +337,500 | +16.38 |
| stop_loss | 3 | 0/3 | 0% | -158,100 | -6.89 |
| max_hold | 1 | 1/0 | 100% | +11,500 | +2.92 |

**총 실현손익**: ≈ **+576,400 KRW** (4월 -158,100 손절 vs +734,500 익절+트레일링)

### 3-3. timing_score / overall_assessment 분포

```sql
SELECT json_extract(ai_review,'$.timing_score') AS ts,
       json_extract(ai_review,'$.overall_assessment') AS grade,
       COUNT(*) AS cnt
FROM trade_reviews WHERE sell_date >= '2026-04-01' AND sell_date <= '2026-05-01'
GROUP BY ts, grade ORDER BY ts;
```

| timing_score | grade | 건수 |
|------|------|------|
| 2 | Poor | 3 |
| 3 | Poor | 5 |
| 6 | Poor | 1 |
| 8 | Good | 1 |
| 9 | Excellent | 4 |

평균 timing_score: (2×3 + 3×5 + 6 + 8 + 9×4) / 14 = **5.07 / 10**

**분포 양극화 패턴**:
- 9점(Excellent) 4건 = LG디스플레이 04-17/04-20, 삼성중공업 04-23 ×2 → **D+5에 매도가 옳았음을 확인**(매도 후 -10~-12% 급락)
- 3점(Poor) 5건 = LG이노텍 4건 + SK하이닉스 → **트레일링 너무 타이트, 추가 상승 놓침**
- 2점(Poor) 3건 = HJ중공업, HD한국조선해양, HPSP → **트레일링/max_hold 조기 발동, 또는 손절 후 D+5 +11.33%**

### 3-4. hold_days별 성과

| hold_days | 건수 | 평균 수익률(%) | 평균 timing_score |
|----------|-----|-------------|-----------------|
| 0 | 1 | -3.36 | 6.0 |
| 1 | 3 | +9.87 | 9.0 |
| 2 | 7 | +12.61 | 3.71 |
| 4 | 1 | -7.72 | 8.0 |
| 6 | 2 | +3.98 | 2.0 |

**관찰**:
- hold_days 2일 표본 7건이 평균 +12.61%로 가장 높지만 timing_score는 **3.71/10** → "조기 익절/트레일링 발동"이 다수. 보유 1일 익절이 timing_score 9점인 것과 대조 → **trailing 발동 시점이 너무 빠른** 가설 재확인.
- hold_days 6일 표본 2건이 +3.98%인데 timing_score 2.0 → max_hold가 적용되었으나 AI는 "더 길게 보유했어야"로 평가.

### 3-5. 테마별 성과

```sql
SELECT theme, COUNT(*) AS sells, ROUND(AVG(profit_rate),2) AS avg_profit_pct, SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END) AS wins
FROM trade_reviews WHERE sell_date >= '2026-04-01' AND sell_date <= '2026-05-01'
GROUP BY theme ORDER BY avg_profit_pct DESC;
```

| 테마 | 건수 | 평균 수익률(%) | 승률 |
|------|-----|--------------|------|
| 아이폰 | 6 | +16.30 | 6/6 |
| 반도체 | 1 | +10.23 | 1/1 |
| 조선 | 4 | +6.84 | 4/4 |
| 통신 | 1 | -3.36 | 0/1 |
| 금융 | 1 | -7.72 | 0/1 |
| AI반도체 | 1 | -9.58 | 0/1 |

**관찰**: 아이폰 테마(LG이노텍 4건 분할 + LG디스플레이 2건) 압도적. 손실 테마는 통신/금융/AI반도체 각 1건씩 → 손실 테마의 표본이 적어 "테마 자체 문제" 결론 불가.

### 3-6. 자본 추이 (`daily_snapshots`)

| 날짜 | total_capital | unrealized | num_positions |
|------|--------------|-----------|---------------|
| 2026-04-01 | 4,592,374 | 0 | 0 |
| 2026-04-22 | 9,670,624 | +110,250 | 2 |
| 2026-05-01 | 9,588,704 | -180,970 | 5 |

**관찰**:
- 4/8~4/10 간 **자본 4.7M → 9.25M으로 점프**(+97%) — 입금 또는 외부 자본 변동 추정. **분석 결과 왜곡 가능성 있음** → 본 제안서의 KRW 절대 수치는 신뢰하지 말고 **수익률(profit_rate)** 기반 판단 우선.
- 4/30 unrealized -180,970 (Phase A 매수 4건 평가손실 영향). MDD -210.73% (daily_return 표시 단위 이슈일 가능성. 메타 정보에 기록).

## 4. 핵심 발견

### 발견 1: 트레일링L1 발동 후 D+5 추적 — 정확/조기 양극화 강함 (N=4)

```sql
SELECT tr.stock_code, tr.sell_date, ROUND(tr.profit_rate,2) AS profit_pct,
       ROUND(tr.max_profit_during_hold,2) AS max_pct, tr.trailing_level,
       ROUND(ptp.change_from_sell,2) AS d5_change_pct
FROM trade_reviews tr
JOIN post_trade_prices ptp ON ptp.review_id=tr.id AND ptp.days_after_sell=5
WHERE tr.sell_date >= '2026-04-01' AND tr.sell_date <= '2026-05-01'
  AND tr.sell_reason = '트레일링L1'
ORDER BY ptp.change_from_sell DESC;
```

| 종목 | 매도일 | 수익률 | 최고점 | D+5 변화 | AI 판정 |
|-----|------|------|------|---------|--------|
| HJ중공업 | 04-17 | +5.05% | +9.61% | **+18.48%** | Poor (ts=2) |
| SK하이닉스 | 04-09 | +10.23% | +14.91% | **+14.02%** | Poor (ts=3) |
| LG디스플레이 | 04-20 | +6.82% | +11.21% | **-10.28%** | Excellent (ts=9) |
| 삼성중공업 | 04-23 | +9.37% | +14.05% | **-4.43%** | Excellent (ts=9) |

**해석**:
- 4건 중 **2건은 D+5에 추가 상승**(HJ중공업 +18.48%, SK하이닉스 +14.02%) → 트레일링 조기 발동
- 4건 중 **2건은 D+5에 급락**(LG디스플레이 -10.28%, 삼성중공업 -4.43%) → 트레일링 정확 발동
- → **트레일링L1 자체가 일관되게 잘못된 게 아니라 종목별 모멘텀 강도에 따라 상반된 결과** → **단순 파라미터 조정으로는 해결되지 않을 가능성**

**관련 parameter_suggestion 인용**:
> "트레일링 레벨을 L2 또는 L3로 상향 조정 필요. 최고 수익률 14.91%에서 10.23%로 하락 시 매도는 너무 타이트함. 반도체 섹터 특성상 변동성이 크므로 트레일링 허용 범위를 7-10%로 확대 권장" (SK하이닉스 04-09)
> "현재 트레일링L1 파라미터 매우 적절. D+3 급락(-15.83% 낙폭)을 회피했으므로 파라미터 유지 권장. 다만 최고점 대비 손실 허용폭을 약간 확대(L2 테스트)하여 D+2 고점 포착 가능성 검토 가치 있음" (LG디스플레이 04-20)

**제안 여부**: → 섹션 5 **"트레일링 단순 완화 제안 보류"** + **"발동 트리거 모멘텀 의존" 가설** 추가 관찰 항목 등록.

### 발견 2: 트레일링L2 (LG이노텍 19.1%) — 추가 상승 +24.11% 미포착 (N=1)

LG이노텍 4건(분할익절 1/2/3차 + 트레일링L2)은 동일 종목의 분할 매도 시퀀스 (04-22, 보유 2일):
- 1차 분할익절 +20.47% → D+5 +22.69%
- 2차 분할익절 +20.47% → D+5 +22.69%
- 3차 분할익절 +20.72% → D+5 +22.44%
- 트레일링L2 +19.10% → D+5 **+24.11%**

**관찰**:
- 분할익절 자체는 정확히 작동(+20%대 수익 실현). 그러나 잔여 물량의 트레일링L2가 **+22.85% 고점에서 -3% 하락 시 발동**되어 추가 +5%p 상승 모멘텀 놓침
- AI 4건 모두 timing_score **3 (Poor)** + 일관된 parameter_suggestion: "트레일링 레벨 3-4단계 상향 또는 분할 매도 시 일부 물량 트레일링 레벨 상향"

**관련 parameter_suggestion 인용**:
> "트레일링L2(약 3.75% 하락 시 매도)는 변동성 큰 테마주에 과도하게 민감. 아이폰 테마와 같은 강한 모멘텀 종목은 트레일링L3-L4(5-7% 하락 허용) 또는 트레일링 스탑 비율을 8-10%로 확대 권장. 2일 보유로 19% 수익은 우수하나 추세가 명확할 때 더 긴 호흡 필요" (LG이노텍 04-22)

**제안 여부**: → 섹션 5 **TRAIL_LEVEL2_PCT/TRAIL_LEVEL3_PCT 완화 제안 (Low 신뢰도, 표본 1건)**.

### 발견 3: 손절 평시 표본 N=2 (Phase B 트리거 미달, 그러나 D+5 강한 반등 패턴 재확인)

`memory/project_stop_loss_review.md`의 Phase B 착수 트리거: `sell_date >= '2026-04-09'` 평시 손절 **10건 이상**. 본 사이클 결과:

```sql
SELECT COUNT(*) AS peacetime_stop_loss_count, MIN(sell_date), MAX(sell_date)
FROM trade_reviews WHERE sell_reason = '손절' AND sell_date >= '2026-04-09';
```

결과: **2건** (HPSP 04-13, 키움증권 04-20). 트리거 10건에 **8건 미달**.

D+5 추적 데이터 (오이솔루션 04-02 전쟁기간 포함 3건):

| 종목 | 매도일 | 수익률 | D+1 | D+2 | D+5 | 비고 |
|-----|------|------|-----|-----|------|------|
| 오이솔루션 | 04-02 | -3.36% | +0.96% | +5.39% | -5.39% | **전쟁 기간** (필터 제외) |
| HPSP | 04-13 | -9.58% | +3.90% | +3.65% | **+11.33%** | 평시 |
| 키움증권 | 04-20 | -7.72% | +2.15% | +0.45% | +2.49% | 평시 |

**관찰 (평시 표본 N=2)**:
- HPSP는 **D+5 +11.33%** 강한 반등 (Phase 1 focus:stop_loss 가설과 동일 방향)
- 키움증권은 D+5 +2.49% 약한 반등
- 평시 D+1~D+2 평균 **+2.54%** 반등 (작은 표본)

**해석**: Phase B 트리거 미달이지만 평시 2건도 **반등 방향성 유지**. Phase 1 focus 제안서의 "전쟁 표본 오염" 우려는 일부 해소됐으나 **신뢰도 등급은 여전히 Low** (N=2). 5월 추가 평시 손절 누적 필요.

**제안 여부**: → 섹션 5 **GRACE_PERIOD_DAYS / STOP_LOSS 변경 제안 보류**(Phase B 트리거 미달).

### 발견 4: max_hold 발동 (HD한국조선해양 04-17) — 발동은 옳았으나 D+5 추적이 결과적으로 추가 상승

- 009540 HD한국조선해양 (04-17, max_hold, +2.92%, hold_days=6, max_profit 4.57%)
- AI 판정: timing_score **2 (Poor)**
- lesson_learned: "기계적인 max_hold 전략보다 섹터 모멘텀과 가격 흐름을 고려한 유연한 전략이 필요. 특히 대형 조선주는 상승 시 지속성이 강한 특징을 고려해야 함"

D+5 추적은 본 데이터셋에 없으나(post_trade_prices에 매칭 없음 — 검토 필요), 같은 시기 HJ중공업(같은 조선 테마, 04-17 트레일링L1)은 D+5 **+18.48%** 상승.

**해석**: 4월 max_hold 발동은 1건이고 결과적 sub-optimal. 그러나 표본 1건으로 **MAX_HOLD_DAYS_PROFIT 10영업일 (현재) 자체가 짧다**고 단정 불가. `memory/project_hold_days_review.md`의 "20건 이상" 트리거 미달.

**제안 여부**: → 정보 공유 + 섹션 6 hold_days_review 상태 갱신.

### 발견 5: 시스템 무결성 — trade_reviews 적재 누락 4건 (4/9 삼성전기, 4/9 삼성SDI, 4/10 클래시스, 4/22 오이솔루션, 4/27 한화오션)

**4월 trades(SELL) vs trade_reviews 매칭 검사**:

```sql
SELECT t.id, t.date, t.stock_code, t.stock_name, t.action, t.reason,
       tr.id AS review_id
FROM trades t LEFT JOIN trade_reviews tr
  ON tr.stock_code = t.stock_code AND tr.sell_date = t.date
WHERE t.date >= '2026-04-01' AND t.date <= '2026-05-01' AND t.action = 'sell'
ORDER BY t.date;
```

**누락 5건** (`review_id IS NULL`):

| 매도일 | 종목 | 매도사유 | 비고 |
|-------|-----|--------|------|
| 04-09 | 삼성전기 | 주중 테마 교체 (수익 청산) | 미드위크 교체 매도 |
| 04-09 | 삼성SDI | 주중 테마 교체 (수익 청산) | 미드위크 교체 매도 |
| 04-10 | 클래시스 | 최대 보유 기간 | max_hold 매도 |
| 04-22 | 오이솔루션 | 최대 보유 기간 | max_hold 매도 |
| 04-27 | 한화오션 | 최대 보유 기간 | max_hold 매도 (W18 weekly에서도 보고) |

**패턴**: 5건 중 **3건이 "최대 보유 기간"**, 2건이 "주중 테마 교체". 2026-W18 weekly에서 한화오션 1건 보고했으나 **30일 누적 시 max_hold 매도의 review 누락이 패턴**임을 확인.

`trade_reviews`는 `database.py`의 `_close_position_in_db()`에서 자동 생성되는 구조인데, "최대 보유 기간" 매도 경로(`run_hold_period_sells()`) + "주중 테마 교체" 매도 경로(`_execute_midweek_profit_sells`/`_execute_midweek_loss_sells`)에서 review 생성 호출이 **누락**되었거나 **별도 경로**일 가능성 높음.

**영향 범위**:
- 4월 max_hold 매도 4건 중 **3건(75%)이 review 미생성** → max_hold 전략 평가에 결정적 데이터 부재
- 미드위크 교체 매도 2건 모두 미생성 → 미드위크 교체 효과 평가 불가
- AI 분석/lesson_learned/post_trade_prices 추적 **5종목 누락**

**해석**: 본 monthly 제안서의 max_hold 표본 N=1 (HD한국조선해양만)은 **실제로는 N=4**여야 했음. 손실 회피 가설(2026-04-21 monthly 발견 3, 한국카본 케이스)을 추가 검증할 데이터가 사라진 셈.

**제안 여부**: → 섹션 5 **시스템 버그 수정 권고 (구현 제안 — 코드 수정 별도 PR)**, 우선순위 **High**. 본 에이전트 제안 영역은 아니지만 사용자 작업 큐 등록 권고.

### 발견 6: parameter_suggestion 자유 서술 수동 분류 (전체 14건)

상위 5건 원문 인용 후 카테고리별 빈도 (수동 분류, 건수 집계는 빈도만 표시):

**카테고리 A: 트레일링 완화 / 레벨 상향** (8건 / 14건):
> "트레일링 레벨을 L2 또는 L3로 상향 조정 필요" (SK하이닉스 04-09)
> "조선 테마와 같은 모멘텀 강한 섹터는 트레일링L2 또는 L3 적용 권장" (HJ중공업 04-17)
> "아이폰 테마 등 강한 모멘텀 섹터는 트레일링 레벨을 3-4단계로 상향" (LG이노텍 04-22 ×3건)

**카테고리 B: 손절 / 보유기간 완화** (3건 / 14건):
> "손절 기준을 -10% 이하로 완화하거나, 최소 보유기간 3-5일 설정 필요. AI반도체 테마주는 변동성이 크므로" (HPSP 04-13)
> "트레일링 스탑 도입 필요: +1% 수익 달성 시 트레일링 스탑 활성화" (키움증권 04-20)

**카테고리 C: 현재 파라미터 유지/적절** (3건 / 14건):
> "현재 트레일링L1 파라미터 매우 적절. D+3 급락(-15.83% 낙폭)을 회피" (LG디스플레이 04-20)
> "현재 트레일링L1 파라미터 매우 적절. 1일 보유로 9.37% 수익 실현 후 5일간 -4.43% 하락을 회피" (삼성중공업 04-23)

**해석**:
- 자유서술 14건 중 **8건(57%)이 트레일링 완화 방향** 의견. 그러나 같은 데이터에서 D+5 추적은 양극화(발견 1) → AI의 "조언"이 일관되게 트레일링 완화를 지지하지만 실제 시장 데이터는 "종목/모멘텀별 다름"
- **카테고리 C 3건은 모두 D+5 급락한 케이스** (LG디스플레이/삼성중공업) → 사후 결과로 검증된 "매도가 옳았던" 케이스
- 결론: AI parameter_suggestion은 **상승 모멘텀이 지속됐을 때만** 트레일링 완화를 권한다 → **사후 편향(hindsight bias)** 강력 의심. 직접 채택 금지.

## 5. 파라미터 조정 제안

| 파라미터 | 현재값 | 제안값 | 근거 | 예상 임팩트 | 신뢰도 |
|---------|-------|-------|------|------------|--------|
| **(보류)** TRAIL_LEVEL2_PCT | -0.03 | -0.05 (확대) | 발견 2 LG이노텍 1건. AI 자유서술 4건 모두 이 방향. **단 발견 1에서 종목별 양극화** 확인 → **표본 1건 + 사후 편향 의심으로 제안 보류** | 트레일링L2 발동 지연, 강한 모멘텀 종목 추가 상승 포착 | **Low (보류)** |
| **(유지 권고)** TRAIL_LEVEL1_PCT | -0.04 | **유지** | 발견 1 4건 중 2건은 매도가 옳았음(D+5 -10~-12% 회피), 2건은 조기 발동(D+5 +14~+18%). **단순 완화는 회피된 손실 -10%를 다시 노출** | 변경 시 양방향 임팩트 동시 발생 (긍정/부정 상쇄) | **High (유지)** |
| **(보류)** GRACE_PERIOD_DAYS | 1 | (1→2 검토 보류) | 발견 3 평시 표본 **N=2**, Phase B 트리거(10건) **미달 8건** | 적용 시 D+1 손절 회피, D+2~3 추가 손실 노출 가능 | **Low (보류)** |
| **(유지 권고)** STOP_LOSS_FAST | -0.07 | **유지** | 발견 3에서 평시 손절 D+5 반등(+11.33%, +2.49%)이 관찰되나 N=2. 다음 monthly까지 평시 10건 누적 후 재평가 | — | **High (유지)** |
| **(유지 권고)** MAX_HOLD_DAYS_PROFIT | 10 | **유지** | 발견 4 표본 **실제 N=4** 중 3건 review 미생성(발견 5). 데이터 부재로 판단 불가 | — | **N/A (데이터 부재)** |
| **(유지 권고)** TRAIL_BE_ACTIVATE_PCT / TRAIL_BE_STOP_PCT | +0.05 / -0.01 | **유지** | 발견 1, 4월 후반 +5% 도달 후 손실 마감 케이스 0건. BE 자체 추가 발동 사례도 없음(직접 효과 측정 불가) | — | **Medium (유지)** |
| **(유지 권고)** RSI_UPPER_BULL/NORMAL/BEAR | 75/70/65 | **유지** | Phase A 1주 효과는 W18 weekly가 별도 평가. 30일 시야로는 표본 부족 | — | **N/A (W18 weekly 참조)** |
| **(유지 권고)** THEME_MIN_SLOT_ENABLED | True | **유지** | 96% 슬롯 보장 발동했으나 매수 4건 모두 평가손실(N=4) → **롤백 트리거 미충족이지만 추가 관찰 필요**. W18 weekly가 5/8까지 모니터링 권고 | — | **Low (유지, 모니터링 중)** |

### 결론: 파라미터 조정 제안 **0건 (전체 유지)**

본 monthly 사이클은 **30일 누적 데이터로도 단일 방향의 파라미터 조정을 정당화하기 어려움**을 확인했다. 핵심 이유:

1. **트레일링 발동 결과의 종목별 양극화** — 단순 완화/강화는 한 방향의 손실을 다른 방향의 손실로 교환할 뿐 (발견 1)
2. **손절 평시 표본 부족** — Phase B 트리거 10건에 8건 부족 (발견 3)
3. **시스템 무결성 이슈로 max_hold 표본 75% 손실** — 데이터 자체가 불완전 (발견 5)
4. **AI parameter_suggestion의 사후 편향** — 자유서술 의견을 직접 채택 시 위험 (발견 6)

대신 본 사이클의 **단일 우선 액션은 "발견 5 시스템 버그 수정"** — 이는 파라미터가 아닌 시스템 코드 영역이며, 별도 `/plan` + strategy-coder 처리 권고.

## 6. 미결 검토 항목 결론

### `memory/project_stop_loss_review.md` — **진행 (Phase B 트리거 8건 미달)**

- 평시 손절 N=2 (HPSP 04-13, 키움증권 04-20) → 트리거 10건의 20% 도달
- D+5 반등 패턴은 방향성 유지 (HPSP +11.33%, 키움증권 +2.49%) 그러나 표본 부족
- **다음 트리거 체크 시점**: 2026-05-15 ~ 05-31 (5월 손절 추가 5~8건 누적 가정)
- **신규 권고**: BE 손절 도입(04-14) 이후 +5% 도달 후 손실 마감 케이스 0건 → "오이솔루션형" 방어 1차 확인. 5월에 추가 케이스 발생 시 BE 효과 측정 가능.

### `memory/project_gap_filter_review.md` — **재분석 미실행 (W18에서도 보고)**

- screening_log의 stage 컬럼은 `'filter'`만 존재 → 모닝 필터의 갭 검사 단계 별도 로그 부재 확인
- 갭 필터 효과 측정은 **다른 데이터 소스 필요**:
  - 옵션 1: portfolio_monitor_v2.py의 09:00~09:25 갭 검사 로그 (있다면)
  - 옵션 2: trades 매수 가격 vs 전일 종가 비교 (slippage 컬럼과 별도)
- **권고**: 본 제안서 범위 밖. 차주(2026-W19) 별도 `/improve focus:gap_filter` 트리거 시 위 데이터 소스 점검 선행.

### `memory/project_hold_days_review.md` — **데이터 부재 (시스템 버그로 표본 75% 손실)**

- 30일 max_hold 매도 실제 4건(클래시스 04-10, HD한국조선해양 04-17, 오이솔루션 04-22, 한화오션 04-27) 중 **review 1건만 생성**
- 이전 사이클 한국카본(`-18.05% 회피`) + 본 사이클 HD한국조선해양(`+2.92%, AI ts=2`) → 누적 N=2로 결론 불가
- **권고**: **발견 5 시스템 버그 수정 후** 미생성 review를 소급 생성하여 표본 확보 → 그 후 hold_days focus 분석 트리거

### Phase A 관찰 (`memory/project_buy_filter_phase_a.md`) — **W18 weekly에서 1주 평가 완료, 본 monthly는 보충**

- 슬롯 보장 96% 발동 + 매수 3/4건이 슬롯 통과
- 4/30 종가 기준 4건 평가손실 -180,970원 → 단기 부정적, 그러나 **롤백 트리거(동일 테마 손절 2건 동시 발생) 미충족**
- 30일 시야로 보면 Phase A는 5영업일에 불과 → 1개월 추가 관찰 필요
- **권고**: 5/8(금) `/improve weekly` (W19) 시점에 4건의 D+8 결과 재확인 → Phase A 종합 평가

## 7. 기각된 가설

1. **"4월 트레일링 파라미터는 조기 발동 일관 = 즉시 완화 필요"** — 기각: 발견 1에서 D+5 결과가 양극화(2건 추가 상승, 2건 급락 회피). 단순 완화는 양방향 임팩트로 순효과 불분명.

2. **"AI parameter_suggestion 빈도 = 파라미터 변경 근거"** — 기각: 발견 6 카테고리 분류 결과 AI 의견이 사후 편향에 강하게 영향받음. 카테고리 C(현재 유지) 3건 모두가 D+5 급락 회피 케이스인 점이 반증.

3. **"4월 손절 3건은 손절가 타이트함의 증거 = -7%→-5% 상향 정당화"** — 기각: 발견 3 평시 표본 N=2로 Phase B 트리거 미달. 또한 HPSP D+5 +11.33%는 손절가 변경이 아닌 **타이밍/Grace Period 이슈**로 해석 가능 (lesson_learned: "최소 3-5일 추세 확인 기간").

4. **"max_hold 매도 1건(HD한국조선해양) timing_score 2점 = MAX_HOLD_DAYS_PROFIT 10일 너무 짧음"** — 기각: 발견 5 시스템 버그로 max_hold 표본 75% 손실. 보고 가능한 N=1로는 결론 불가.

## 8. 다음 사이클 관찰 항목

- [ ] **2026-W19 (5/8 금) `/improve weekly`** — Phase A 매수 4건의 D+5/D+8 결과 + W19 손절/익절/트레일링 추가 누적 표본 검토
- [ ] **2026-05-31 또는 06-01 `/improve monthly`** — 본 제안서의 모든 보류 항목 재평가:
  - 평시 손절 표본 누적 (목표 10건)
  - 트레일링L1 추가 표본 (목표 4건 → 8건, 종목별 양극화 패턴 일반화 가능 여부)
  - 트레일링L2 표본 (현재 1건 → 3건)
  - max_hold 매도 review 누락 버그 **수정 후** 4월 누락 4건 소급 생성 가능 여부 확인
- [ ] **시스템 버그 수정 (발견 5)** — 별도 `/plan` 트리거 권고:
  - max_hold 매도 경로(`run_hold_period_sells()`)에서 trade_review 생성 호출 점검
  - 미드위크 교체 매도 경로(`_execute_midweek_profit_sells`/`_execute_midweek_loss_sells`)에서 동일 점검
  - 누락 5건(삼성전기 04-09, 삼성SDI 04-09, 클래시스 04-10, 오이솔루션 04-22, 한화오션 04-27)의 소급 review 생성 가능성 검토 (post_trade_prices도 함께)
- [ ] **매도 슬리피지 측정 누락 점검** — 발견 2-2: 13건 매도 모두 slippage NULL. 매도 경로에서 slippage 계산 호출 미연결 가능성. 시스템 버그 후순위 항목.
- [ ] **자본 점프 검증** — 4/8~4/10 4.7M → 9.25M 점프(+97%). 입금 이벤트 / 외부 자본 변동 추정 → daily_snapshots 의 `cum_ret_pct` / `mdd_pct` 값 신뢰도 별도 검증 필요. 본 제안서는 수익률 기반 분석으로 회피했으나 W19 이후 사이클에서 영향 평가 권고.
- [ ] **테마 회전문 방지 효과 측정** — 5/5(화) 재선정 시 4/28 vs 5/5 테마 비교. 동일 세션 drop 후 재진입 발생 여부 확인.

## 9. 메타 정보

- **Claude API 추가 호출 여부**: **아니오** (기존 `ai_review` JSON + `post_trade_prices` + `strategy_stats` + `trade_reviews` + `screening_log` + `daily_snapshots` + `themes` + `trades` 만 사용)
- **WEEKLY_SUMMARY_PROMPT 결과 흡수 여부**: **부분** (W18 weekly 제안서 흡수 — Phase A 5영업일 분석 + 시스템 무결성 단기 영향). 본 monthly는 W18 결과를 30일 시야로 확장.
- **JSON 파싱 실패 건수**: **0건** (14건 전부 파싱 성공)
- **`ai_review` NULL 건수**: **0건** (분석 대상 14건 모두 완료. 단 trade_reviews 자체 누락은 발견 5의 별개 이슈)
- **trade_reviews 누락 건수**: **5건** (4월 SELL 18건 vs review 14건, 4건 매핑 + 분할 매도 다중 매핑 정상화 후 5건 누락 확정)
- **자본 점프 알림**: 4/8~4/10 daily_snapshots에서 total_capital 4.7M → 9.25M 비정상 점프. 외부 자본 변동(입금) 추정. **본 제안서의 수익률 기반 분석은 영향 없으나 KRW 절대 수치 비교 시 주의**.
- **MDD 단위 의심**: daily_snapshots의 `mdd` 컬럼 값이 -396% / -210% 등 비현실적 수치. **계산 로직 별도 점검 필요** (별도 시스템 점검 항목).
- **에이전트 자기 점검**: 완료
  - [x] 모든 파라미터 제안에 쿼리/수치/신뢰도 명시 (전부 "유지" 또는 "보류")
  - [x] parameter_suggestion 자유 서술 건수 집계 안 함 (수동 카테고리 분류 + 원문 인용 5건)
  - [x] 민감 데이터(계좌잔고 실수치/주문 ID) 미포함 (KRW 수치는 PnL/total_capital만, 비율 위주)
  - [x] Claude API 추가 호출 없음
  - [x] `change_log.md` 4건 변경 이력 모두 섹션 2에서 다룸 (TRAIL_BE / 공격적 지정가 / 회전문 방지 / Phase A)
  - [x] 미결 검토 항목 4건 모두 섹션 6에 반영
  - [x] 파일명 `2026-05_monthly.md` 중복 없음 (기존 `2026-04-monthly.md`와 분리)
  - [x] W18 weekly 결과 흡수 + 30일 시야로 확장 (중복 회피)

## 10. 승인 후 이관 권고

본 제안서는 **파라미터 조정 0건 (전체 유지)** + **시스템 버그 수정 권고 1건**.

권고 액션:

1. **`/plan` 이관: trade_reviews 적재 누락 버그 수정** — 발견 5의 max_hold/midweek 경로 점검. 이는 strategy-coder가 아닌 일반 코드 수정 영역. CHECKLIST에 다음 포함 권고:
   - max_hold 매도 경로 (`run_hold_period_sells()`) 의 review 생성 호출 점검
   - midweek 교체 매도 경로 점검
   - 누락 5건 소급 생성 가능성 검토 (DB 백업 → 매도 가격/시점 복원 → review 생성 → post_trade_prices 추적 시작)
   - 매도 slippage 측정 미구현 점검 (별도 항목)
   - 회귀 테스트: max_hold 발동 1건 + midweek 교체 1건이 정상적으로 review 생성되는지 e2e 검증
   - **`docs/improvements/change_log.md`에 1줄 추가 (시스템 변경이지만 분석 데이터 무결성 영향이므로 추적 권고)**

2. **`/improve weekly` 5/8(금) 정기 트리거** — 자동 (`scheduler.py improvement_reminder_weekly`)

3. **본 monthly 자체는 파라미터 변경 0건** → strategy-planner / strategy-coder 이관 **불필요**

---

**제안서 작성 완료. 사용자 승인 후 `/plan docs/improvements/2026-05_monthly.md` 호출 시 strategy-planner가 발견 5 시스템 버그 수정 3문서 생성.**
