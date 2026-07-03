# PLAN — 종가베팅 현재 성과 분석·평가

> 생성: 2026-07-03 12:28:14 KST
> 목적: 고도화 판단(flow reliability 점수 반영, Phase2 2차 진입 재활성화, 파라미터 튜닝)을 하기 전에 현재까지의 실전 성과를 데이터로 평가한다.

## 1. 목표

종가베팅 시스템의 최근 실전 운영 결과를 정량 평가한다. 평가 전에는 신규 고도화 설정을 켜지 않는다.

## 2. 현재 운영 스냅샷

- `entry_executor.enabled=True`
- `entry_executor.dry_run=False`
- `entry_executor.phase2_enabled=False`
- `morning_exit.enabled=True`
- `morning_exit.dry_run=False`
- `morning_exit.morning_trailing_enabled=True`
- `score.layer1_weight=0.0`
- `TRANCHE_ENTRY_ENABLED=True`
- `DRY_RUN_PYRAMID=False`
- `EARLY_BUY_ENABLED=True`

## 3. 분석 범위

1. 기간: 2026-05-25 실발주 활성화 이후부터 최신 거래일까지
2. 대상: 종가베팅 `candidates` DB의 실제 진입/체결/청산 기록
3. 제외: 단순 recommended 후보 중 미진입 건은 funnel 분석에는 포함하되 손익 통계에는 제외

## 4. 핵심 질문

- 실제 진입 종목 수와 진입 빈도는 충분한가?
- 평균/중앙 수익률, 승률, 최대 손실, 손익비는 어떤가?
- 청산 사유별 성과는 어떤가?
- MarketGuard 상태별 성과 차이가 있는가?
- Phase1 단발 구조가 안정적인가?
- Phase2 2차 진입을 다시 켤 근거가 있는가?
- flow reliability 데이터가 점수 반영할 만큼 예측력이 있는가?

## 5. 실행 단계

### Step 1. DB 스키마/컬럼 확인

- `data/closing_bet.db`의 `candidates`, `candidate_features`, `flow_reliability`, `orderbook_snapshots` 스키마 확인
- 손익/청산/체결 관련 컬럼명을 확정

### Step 2. 분석 스크립트 작성

- 신규 파일 후보: `scripts/analyze_closing_bet_performance.py`
- 출력:
  - 전체 요약
  - 월별/주별 성과
  - 청산 사유별 성과
  - 진입 후보 funnel
  - MarketGuard/score 구간별 성과
  - flow reliability 상관/적중률

### Step 3. 로컬/운영 DB read-only 실행

- DB는 읽기 전용 연결 사용
- 주문/매매 API 호출 금지
- Telegram 발송 금지

### Step 4. 평가 리포트 작성

- 신규 문서 후보: `docs/improvements/YYYYMMDD_closing_bet_performance_review.md`
- 포함:
  - 결론 먼저
  - 데이터 기간/표본 수
  - 성과표
  - 문제점
  - 고도화 판단

### Step 5. 고도화 판단 게이트

- `flow reliability layer1_weight`는 direction match가 충분할 때만 검토
- `phase2_enabled`는 Phase1 단발 성과가 안정적일 때만 검토
- 손실/미체결/슬리피지 문제가 있으면 고도화보다 안정화 우선

## 6. 검증 명령

```bash
PYTHONPATH=. ./venv/bin/python scripts/analyze_closing_bet_performance.py --db data/closing_bet.db --since 2026-05-25
PYTHONPATH=. ./venv/bin/python -m py_compile scripts/analyze_closing_bet_performance.py
```

## 7. 산출물

- 분석 스크립트
- 성과 분석 리포트
- 고도화 판단표:
  - 지금 켤 것
  - 관측 더 필요한 것
  - 보류할 것

## 8. 안전 원칙

- 분석 중 실전 설정 변경 금지
- 분석 중 서비스 재시작 금지
- 분석 중 KIS 주문/취소 API 호출 금지
- 모든 결론은 실제 DB 결과 기반으로만 작성
