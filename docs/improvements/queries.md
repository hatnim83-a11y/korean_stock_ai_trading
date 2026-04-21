# 표준 SQL 쿼리 세트

`trade-improvement-analyst` 에이전트가 재사용하는 표준 쿼리 모음. 모든 쿼리는 SELECT 전용이며, `mcp__sqlite__read_query`로 실행한다. DB 경로는 `data/trading.db`.

> **주의**: SQLite의 `date('now','-N days','localtime')`는 서버 타임존(UTC) 기준이므로 **KST 정밀도가 필요한 필드엔 Python 선처리로 KST 날짜 범위를 바인드**할 것. 여기 쿼리는 대략적 범위 산출용.

## 1. 기본 통계

### 1-1. 표본 수 + JSON 파싱 가능 건수
```sql
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN ai_review IS NOT NULL THEN 1 ELSE 0 END) AS with_review,
  SUM(CASE WHEN ai_review IS NOT NULL AND json_valid(ai_review) = 1 THEN 1 ELSE 0 END) AS parseable
FROM trade_reviews
WHERE sell_date >= date('now','-7 days','localtime');
```

### 1-2. timing_score 분포 (최근 7일)
```sql
SELECT json_extract(ai_review,'$.timing_score') AS ts, COUNT(*) AS cnt
FROM trade_reviews
WHERE sell_date >= date('now','-7 days','localtime')
  AND ai_review IS NOT NULL
  AND json_valid(ai_review) = 1
GROUP BY ts
ORDER BY ts;
```

### 1-3. overall_assessment 분포 (최근 30일, 전략별)
```sql
SELECT strategy,
       json_extract(ai_review,'$.overall_assessment') AS grade,
       COUNT(*) AS cnt
FROM trade_reviews
WHERE sell_date >= date('now','-30 days','localtime')
  AND ai_review IS NOT NULL
GROUP BY strategy, grade
ORDER BY strategy, grade;
```

### 1-4. ai_review NULL 대기 상태 판정 (정상 대기 vs 시스템 이슈 구분)
`modules/post_trade_analyzer/analyzer.py`의 `MIN_CALENDAR_DAYS_WAIT=8` 조건 때문에 D+8 미만 매도건은 ai_review가 정상적으로 NULL. 본 쿼리로 "대기 중"과 "실제 분석 지연"을 구분:
```sql
SELECT id, stock_code, sell_date,
       CAST(julianday('now','localtime') - julianday(sell_date) AS INTEGER) AS days_since_sell,
       CASE
         WHEN CAST(julianday('now','localtime') - julianday(sell_date) AS INTEGER) < 8
         THEN '대기중(D+8미만)'
         ELSE '분석지연(조사필요)'
       END AS status
FROM trade_reviews
WHERE ai_review IS NULL
  AND sell_date >= date('now','-30 days','localtime')
ORDER BY sell_date DESC;
```
'분석지연' 행이 발견되면 제안서 섹션 9 메타에 원인 조사 권고를 필수 포함.

## 2. 파라미터 집중 분석

### 2-1. 손절 관련 — 조기 매도 후 반등 케이스 (`focus:stop_loss`)
매도 후 D+5에서 +3% 이상 상승한 건수 (기회비용 발생 케이스):
```sql
SELECT tr.id, tr.stock_code, tr.sell_date, tr.profit_rate,
       json_extract(tr.ai_review,'$.timing_score') AS ts,
       json_extract(tr.ai_review,'$.overall_assessment') AS grade,
       ptp.change_from_sell AS d5_change
FROM trade_reviews tr
JOIN post_trade_prices ptp
  ON ptp.review_id = tr.id AND ptp.days_after_sell = 5
WHERE tr.sell_date >= date('now','-30 days','localtime')
  AND tr.ai_review IS NOT NULL
  AND ptp.change_from_sell >= 3.0
ORDER BY ptp.change_from_sell DESC;
```

### 2-2. 손절 발동 후 실제 하락 지속 여부
손절(-7% 전후) 건이 D+5에도 여전히 음수인지:
```sql
SELECT tr.stock_code, tr.sell_date, tr.profit_rate,
       ptp.change_from_sell AS d5_change,
       json_extract(tr.ai_review,'$.overall_assessment') AS grade
FROM trade_reviews tr
JOIN post_trade_prices ptp
  ON ptp.review_id = tr.id AND ptp.days_after_sell = 5
WHERE tr.profit_rate <= -0.05
  AND tr.sell_date >= date('now','-30 days','localtime')
ORDER BY tr.profit_rate;
```

### 2-3. 보유기간 분석 (`focus:hold_days`)
```sql
SELECT hold_days,
       COUNT(*) AS cnt,
       AVG(profit_rate) AS avg_return,
       AVG(json_extract(ai_review,'$.timing_score')) AS avg_ts
FROM trade_reviews
WHERE sell_date >= date('now','-30 days','localtime')
  AND ai_review IS NOT NULL
GROUP BY hold_days
ORDER BY hold_days;
```

### 2-4. 트레일링 분석 (`focus:trailing`)
트레일링 스탑 발동 후 추가 상승 여부 (D+5 change):
```sql
SELECT tr.stock_code, tr.sell_date, tr.profit_rate,
       ptp.change_from_sell AS d5_change,
       json_extract(tr.ai_review,'$.timing_reason') AS reason
FROM trade_reviews tr
JOIN post_trade_prices ptp
  ON ptp.review_id = tr.id AND ptp.days_after_sell = 5
WHERE tr.sell_date >= date('now','-30 days','localtime')
  AND tr.profit_rate > 0  -- 익절/트레일링 계열
ORDER BY ptp.change_from_sell DESC;
```

### 2-5. 시초가 갭 필터 관련 (`focus:gap_filter`)
screening_log 기반 (필터 통과율):
```sql
SELECT theme,
       COUNT(*) AS screened,
       SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed_cnt,
       ROUND(100.0 * SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pass_rate
FROM screening_log
WHERE date >= date('now','-7 days','localtime')
GROUP BY theme
ORDER BY pass_rate;
```

## 3. 전략별 성과 비교

### 3-1. strategy_stats 최근 30일 집계
```sql
SELECT strategy_type AS strategy,
       SUM(trades_count) AS trades,
       SUM(winning_trades) AS wins,
       ROUND(100.0 * SUM(winning_trades) / NULLIF(SUM(trades_count),0), 2) AS win_rate,
       ROUND(AVG(avg_profit_rate)*100, 2) AS avg_return_pct
FROM strategy_stats
WHERE date >= date('now','-30 days','localtime')
GROUP BY strategy_type
ORDER BY win_rate DESC;
```

> **컬럼명 주의**: `strategy_stats` 실제 컬럼은 구현에 따라 다르므로 에이전트는 실행 전 `mcp__sqlite__describe_table`로 스키마를 먼저 확인한다.

## 4. parameter_suggestion 자유 서술 샘플링

건수 집계 금지, 내용 열람만:
```sql
SELECT stock_code, sell_date,
       json_extract(ai_review,'$.timing_score') AS ts,
       json_extract(ai_review,'$.parameter_suggestion') AS suggestion
FROM trade_reviews
WHERE sell_date >= date('now','-14 days','localtime')
  AND ai_review IS NOT NULL
  AND json_extract(ai_review,'$.parameter_suggestion') IS NOT NULL
ORDER BY sell_date DESC
LIMIT 10;
```

## 5. lesson_learned 샘플링

```sql
SELECT stock_code, sell_date, lesson_learned
FROM trade_reviews
WHERE sell_date >= date('now','-14 days','localtime')
  AND lesson_learned IS NOT NULL
  AND length(lesson_learned) > 10
ORDER BY sell_date DESC
LIMIT 10;
```

---

## 쿼리 추가 규칙

새 쿼리 필요 시:
1. SELECT 전용인지 재확인
2. 민감 컬럼(계좌잔고 실수치, 주문ID) 제외
3. 제안서에 결과 인용할 때 쿼리 원문도 함께 인용
4. 결과 수치가 0/NULL일 수 있는 경우 `NULLIF` 또는 `COALESCE`로 방어

## 관련 테이블 스키마 참조
- `trade_reviews`: database.py:298-321
- `post_trade_prices`: database.py:377-395
- `strategy_stats`: DB v8 추가 (database.py 내 CREATE TABLE 참조)
- `screening_log`: DB v12 추가
