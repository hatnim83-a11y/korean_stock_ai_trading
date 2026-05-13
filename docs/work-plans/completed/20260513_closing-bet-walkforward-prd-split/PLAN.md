# PLAN: 종가베팅 walkforward 재실행 — PRD 10-1 분할매도 시나리오 도입

## 목표
종가베팅 시스템 100건 게이트 도달(2026-05-13, 99건 라벨링) 시점에서 walkforward 재실행하되, **운영 정책(PRD 10-1 시가 액션 매트릭스, 다음날 시초가 일부 매도 + 09:30 잔여 청산)**과 정합되는 평가 기준으로 단위 2-4 entry_executor 진입 결정 근거 마련.

## 배경
- 현 simulator는 next_open_pct 한 점에서 **100% 일괄 매도**만 시뮬 → 실제 운영(50/50 분할매도)과 평가 기준 불일치
- 5/11 walkforward (48건, conservative): EV -0.32% / WL 0.56 / Sharpe -1.27 → 4 게이트 모두 FAIL
- score 구간별: 3+ = 81.8% / 2 = 65.0% / 1 = 43.3% → score≥2 임계 진입의 통계적 정당성 확보
- 5/14 116건으로 2차 실행 시 100건 게이트 충족 가능

## 구현 단계

### 단위 1: simulator PRD 분할매도 시나리오 확장
- `phase25_simulator.py` 정책 2종 (`prd_split_optimistic` / `prd_split_realistic`) 추가
- 시나리오 3종 (`prd_split_gapup` / `prd_split_flat` / `prd_split_gapdown`)
- PRD 10-1 5구간 분기 로직 (시가 ≥+2% / +0.5~+2% / 보합 / 약갭다운 / 갭다운)
- EVReport dataclass 신규 카운터 (default 0, 회귀 영향 차단)
- compute_ev에 신규 시나리오 가중 합 추가
- 단위 테스트 11건 (PRD-1~PRD-11)

### 단위 2: walkforward 확장 + score 임계 필터
- `phase25_walkforward.py` _POLICIES 5종 확장
- `--score-min` CLI 옵션 (None/2/3)
- default 정책 conservative → prd_split_realistic
- 신규 섹션 6 (score 임계 × 정책 매트릭스 게이트 판정)
- limitation 섹션 (optimistic/realistic 범위 = 실제 운영 기대치)
- 단위 테스트 6건 (WF-1~WF-6)

### 단위 3: 실행 + 리포트
- 1차: `python -m closing_bet_system.backtest.phase25_walkforward --start 2026-05-04 --end 2026-05-12` → `phase25_ev_report_20260513.md`
- 2차 (5/14 라벨링 후): `--end 2026-05-13` → `phase25_ev_report_20260514.md`
- 게이트 PASS/FAIL 분석 + 단위 2-4 진입 결정 근거 정리

## 변경 파일
- `closing_bet_system/backtest/phase25_simulator.py` (수정)
- `closing_bet_system/backtest/phase25_walkforward.py` (수정)
- `closing_bet_system/tests/test_phase25_simulator_prd.py` (신규)
- `closing_bet_system/tests/test_phase25_walkforward_prd.py` (신규)
- `docs/improvements/phase25_ev_report_20260513.md` (신규, 자동 생성)
- `docs/improvements/phase25_ev_report_20260514.md` (신규, 5/14 자동 생성)
- `docs/improvements/change_log.md` (1줄 추가)
- `data_loader.py` 무변경 (total_score / 라벨 3컬럼 이미 로드)

## 롤백 계획
- 백테스트 모듈만 변경 → `git revert <commit>` 1회로 복구
- 운영 코드/DB/systemd 미변경

## 완료 기준
- 단위 테스트 17건 신규 + 회귀 39건 모두 PASS
- code-tester 심각 0건
- 리포트 2개 (5/13, 5/14) 자동 생성 + 게이트 매트릭스 15셀 채워짐
- change_log.md 1줄 추가
- 메모리 `project_closing_bet_followups.md` 업데이트
