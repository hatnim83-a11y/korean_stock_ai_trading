---
analysis_period: YYYY-MM-DD ~ YYYY-MM-DD
mode: weekly | monthly | focus:<topic>
sample_size: N
generated_at: YYYY-MM-DD HH:MM KST
---

# <제안서 제목 — 예: 2026년 17주차 주간 거래 개선 제안서>

## 1. 분석 개요
- 분석 기간: YYYY-MM-DD ~ YYYY-MM-DD (N 영업일)
- 총 매매 건수: N
- AI 분석 완료 건수: M (JSON 파싱 성공 M, 파싱 실패 K)
- 대상 전략: theme_momentum / dual_momentum / ...
- 적용된 최신 파라미터 변경: (change_log.md 최근 1건 요약)

## 2. Before/After 추적 (선택)
`change_log.md` 최근 변경 이력이 있을 때만 작성. 변경 이력이 없으면 "**최근 파라미터 변경 없음**"으로 1줄.

### 변경 N: <변경명 — 예: STOP_LOSS_FAST -0.07 → -0.06>
- 변경일: YYYY-MM-DD
- 변경 전 30일 성과 vs 변경 후 30일 성과:
  - 평균 timing_score: X.X → Y.Y
  - overall_assessment Excellent 비율: A% → B%
  - 수익률 평균: ...
- **해석**: ...
- **결론**: 유지 / 재조정 검토 / 롤백 권고

## 3. 성과 요약
| 지표 | 값 | 비고 |
|-----|----|------|
| 평균 timing_score | 0.0 | 0-10 척도 |
| overall_assessment 분포 | Excellent N / Good N / Neutral N / Poor N / Bad N | |
| 매도건 평균 수익률 | +X.XX% | 합산 아닌 평균 |
| 최고 수익 건 | 종목코드 +XX% | |
| 최저 수익 건 | 종목코드 -XX% | |
| 전략별 승률 (strategy_stats) | ... | |

## 4. 핵심 발견

### 발견 1: <제목>
- **증거 데이터** (쿼리 원문):
  ```sql
  SELECT ...
  ```
  결과: ...
- **해석**: ...
- **관련 parameter_suggestion 인용 (최대 3건)**:
  > "..." (stock_code, sell_date)
  > "..."
- **제안 여부**: → 섹션 5의 제안 N / 정보 공유만 / 기각

### 발견 2: ...

## 5. 파라미터 조정 제안

| 파라미터 | 현재값 | 제안값 | 근거(발견 N) | 예상 임팩트 | 신뢰도 |
|---------|-------|-------|------------|------------|--------|
| | | | | | High/Medium/Low |

**신뢰도 등급**:
- High: 표본 ≥ 30 + 통계적 유의성 확인
- Medium: 표본 ≥ 15 + 방향성 일관
- Low: 표본 ≥ 5 + 관찰 수준 (제안 약함)

**표본 부족 시**: 제안 없이 "판단 유보" 명시.

## 6. 미결 검토 항목 결론
- **project_stop_loss_review.md**: 진행/미결/결론 + 한 줄 이유
- **project_gap_filter_review.md**: ...
- **project_hold_days_review.md**: ...

## 7. 기각된 가설 (선택)
데이터가 지지하지 않거나 표본이 부족해 배제한 가설:
1. "..." — 기각 사유: 표본 N<5
2. ...

## 8. 다음 사이클 관찰 항목
- [ ] ...
- [ ] ...

## 9. 메타 정보
- Claude API 추가 호출: 아니오 / 예(이유: ...)
- WEEKLY_SUMMARY_PROMPT 결과 흡수: 예(출처) / 아니오(이유)
- JSON 파싱 실패 건수: K
  - (실패 시) 종목: ..., sell_date: ..., 앞 50자: "..."
- 에이전트 자기 점검 완료: 예
