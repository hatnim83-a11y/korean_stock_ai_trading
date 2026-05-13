# CHECKLIST: 종가베팅 walkforward PRD 10-1 분할매도

## 구현

### 단위 1: simulator 확장
- [x] `phase25_simulator.py` 모듈 상수 추가 (정책 2종 / 시나리오 3종 / 임계 3종 / 매도 비율)
- [x] `EVReport` dataclass 신규 필드 9개 추가 (n_prd_gapup/flat/gapdown + p_*/mean_* default 0)
- [x] `_map_scenario()` prd_split_* 정책 분기 추가
- [x] `simulate_candidate()` SCENARIO_PRD_SPLIT_GAPUP optimistic/realistic 변형 처리
- [x] `compute_ev()` 신규 시나리오 3종 가중 합 추가
- [x] `scripts/test_phase25_simulator_prd.py` 신규 14건 작성 (PRD-1~11 + 헬퍼 3건)

### 단위 2: walkforward 확장
- [x] `phase25_walkforward.py` `_POLICIES` 5종 확장 + `_SCORE_FILTERS` 상수 신규
- [x] `generate_report()` default 정책 conservative → prd_split_realistic
- [x] `--policy` CLI 인자 추가
- [x] `_build_score_gate_matrix()` 신규 (15셀)
- [x] `_build_markdown()` 섹션 4 5개 정책 비교 + 신규 섹션 6 (score 임계 매트릭스) + 섹션 7 (limitation)
- [x] `scripts/test_phase25_walkforward_prd.py` 신규 7건 작성 (WF-1~7)

## 검증

### 단위 테스트
- [x] 신규 14건 (simulator PRD) 모두 PASS
- [x] 신규 7건 (walkforward PRD) 모두 PASS
- [x] 회귀: 기존 simulator 26건 PASS
- [x] 회귀: 기존 walkforward 13건 PASS
- [x] **총 60건 PASS**

### 통합
- [x] py_compile 0 에러
- [x] 1차 실행: `python -m closing_bet_system.backtest.phase25_walkforward --start 2026-05-04 --end 2026-05-12 --db-path <main>/data/closing_bet.db` 성공
- [x] `docs/improvements/phase25_ev_report_20260513.md` 생성 (88건 평가, recommended only)
- [x] 게이트 매트릭스 15셀 채워짐 + 결과: prd_split_realistic 전체 EV+0.95% / score≥2 +1.59% / score≥3 +2.65% (표본 88<100만 부족)
- [ ] 5/14 10:00 자동 라벨링 후 2차 실행 `--end 2026-05-13` → `phase25_ev_report_20260514.md` (5/14 별도 단계)

### code-tester
- [x] 자체 점검 (timeout으로 미완 — 60건 단위 테스트 + 회귀 PASS로 보강)

## 배포
- [x] 운영 코드 미변경 — systemd 재시작 **불필요** (배포 N/A)
- [x] 워크트리 격리 작업, 메인 머지는 사용자 승인 후

## 문서 업데이트
- [x] `docs/improvements/change_log.md` 1줄 추가
- [x] `memory/project_closing_bet_followups.md` 갱신 (1차 결과 + 5/14 2차 실행 예약)
- [x] CHECKLIST 체크 완료
- [ ] active/ → completed/ 아카이브 (커밋 직전)
