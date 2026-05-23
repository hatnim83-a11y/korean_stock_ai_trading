# score별 EV 분포 분석 (2026-05-23)

자본 배분 동적 분리 작업 Phase C 선행 검증 — strategy-planner 심각 1번 대응

## 데이터
- 종가베팅 누적 라벨 213건 (5/4~5/22)
- DB: `data/closing_bet.db` candidates JOIN candidate_labels

## score별 EV+ 비율

| score | n | EV+ | 승률 | 평균 시초% | 평균 모닝고% | min 모닝고% | max 모닝고% |
|---|---|---|---|---|---|---|---|
| 0 | 15 | 9 | 60.0% | +1.60% | +2.96% | -3.63% | +9.75% |
| 1 | 61 | 33 | **54.1%** | +0.26% | +2.50% | -3.53% | +16.09% |
| 2 | 81 | 49 | 60.5% | +1.20% | +4.75% | -5.84% | +27.96% |
| 3 | 46 | 33 | **71.7%** | +1.40% | +5.28% | -1.66% | +25.15% |
| 4 | 12 | 11 | **91.7%** | +1.45% | **+9.71%** | -1.23% | +29.88% |

## 누적 임계값 분석

| cohort | n | EV+ | 승률 | 평균 모닝고% |
|---|---|---|---|---|
| score≥1 | 200 | 126 | 63.0% | +4.48% |
| score≥2 | 139 | 93 | 66.9% | +5.36% |
| score≥3 | 58 | 44 | 75.9% | +6.21% |
| score≥4 | 12 | 11 | **91.7%** | **+9.71%** |

## 일별 score≥2 후보 수 (5/4~5/22)

| 거래일 | 후보 수 |
|---|---|
| 5/22 | 12건 |
| 5/21 | 9건 |
| 5/20 | 11건 |
| **5/19 (CRISIS)** | **6건** ← 최소값 |
| 5/18 | 15건 |
| 5/15 | 9건 |
| 5/14 | 8건 |
| 5/13 | 14건 |
| 5/12 | 14건 |
| 5/11 | 9건 |
| 5/8 | 11건 |
| 5/7 | 11건 |
| 5/4 | 8건 |

**모든 영업일 후보 ≥ 6건** → top 4 채우기 충분

## 결론 (LIMIT 4 의사결정)

### ✅ 진행 안전
- score 순 top 4 진입 시 예상 평균 EV+ 약 **75%** (score≥3 평균)
- score=4 종목 진입 가능 시 (매일 0~2건) 추가 EV 부스트
- score_threshold=2 유지 (score=1 EV+ 54.1%는 위험)
- score 동점 시 tie-breaking: `candidate_id ASC` (선등록 종목 우선)

### 잠재 위험
- score=2 종목 (EV+ 60.5%, n=81) 진입 비중이 가장 큼 — 손실 약 40%
- 다만 top 4 강제 시 매일 score=3+ 우선 선택 → 평균 EV+ 보정 효과

### CRISIS/DANGER 날 흡수 분기와 정합
- 5/19 (CRISIS) 6건 후보로 top 4 가능
- 단, Plan에 명시된 `disable_absorb_on_crisis=True` 활성 시 base 10%만 사용 → 1종목 사이즈 작아짐

## 다음 액션 (Phase B/C 구현)
- `entry_executor._select_phase1_candidates` SQL에 `LIMIT 4` 추가
- `ORDER BY total_score DESC, candidate_id ASC` 안정 정렬

## 분석 SQL (재현용)
```sql
SELECT c.total_score, COUNT(*) n, SUM(cl.label_net_ev_positive) ev_pos,
       AVG(cl.next_open_pct)*100 avg_open, AVG(cl.next_morning_high_pct)*100 avg_high,
       MIN(cl.next_morning_high_pct)*100 min_high, MAX(cl.next_morning_high_pct)*100 max_high
FROM candidates c JOIN candidate_labels cl ON c.candidate_id=cl.candidate_id
GROUP BY c.total_score ORDER BY c.total_score;
```
