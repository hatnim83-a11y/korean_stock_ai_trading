# CHECKLIST — 종가베팅 위기 시 부분 흡수 + 선별 강화

## 구현

### 단위 1: GuardConfig + compute_capital_limit
- [x] `closing_bet_system/infra/fund_guard.py` `GuardConfig` 에 `crisis_absorb_ratio: float = 0.0` 추가
- [x] `from_settings()` 에서 `crisis_absorb_ratio` 로드 + 범위 검증 (0.0~1.0, 이탈 시 기본값)
- [x] `compute_capital_limit()` 정상 흡수 분기 내부에 `external_risk_active` + `crisis_absorb_ratio < 1.0` 시 `swing_idle = int(swing_idle_raw * cfg.crisis_absorb_ratio)` 적용
- [x] debug_info 에 `swing_idle_raw` / `crisis_absorb_ratio` / `external_risk_active` / `mode='absorb_swing_idle(crisis_partial)'` 키 추가
- [x] 단위 테스트 신규 4건 (DC-15/16/17/18) + DC-9 확장:
  - [x] DC-15 위기 부분 흡수 ratio=0.5 PASS
  - [x] DC-16 위기 부분 흡수 ratio=0.0 PASS (base만 동등)
  - [x] DC-17 crisis_absorb_ratio 범위 검증 PASS
  - [x] DC-18 disable=true 회귀 차단 PASS
- [x] 단위 1 전체: **20/20 PASS**

### 단위 2: EntryExecutorSettings + score_threshold 동적 적용
- [x] `closing_bet_system/execution/entry_executor.py` `EntryExecutorSettings` 에 `score_threshold_crisis: int = 2` (default 무변화) 추가
- [x] `closing_bet_system/main_orchestrator.py` `EntryExecutorSettings()` 초기화에 `score_threshold_crisis` 로드 (미설정 시 `score_threshold` 값 폴백)
- [x] `_select_phase1_candidates()` 시그니처 변경: `*, external_risk_active: bool = False`
- [x] threshold 분기 적용 (외부 위기 시 `score_threshold_crisis`)
- [x] `execute_phase1()` 호출 시 `external_risk_active=external_risk_active` 전달
- [x] 단위 테스트 신규 4건 (EE-31/32/33/34):
  - [x] EE-31 NORMAL threshold=2 → score>=2 후보 2건
  - [x] EE-32 DANGER threshold_crisis=3 → score=2 차단
  - [x] EE-33 CAUTION 도 threshold_crisis=3 적용
  - [x] EE-34 default threshold_crisis=2 → 롤백 안전
- [x] 단위 2 전체: **35/35 PASS** (mock 픽스처에 fund_guard.compute_capital_limit return_value 추가하여 사전 깨짐 15건 + 신규 4건 모두 해결)

### 단위 3: settings.yaml 변경
- [x] `closing_bet_system/config/settings.yaml` 변경:
  - [x] `fund.disable_absorb_on_crisis: true → false`
  - [x] `fund.crisis_absorb_ratio: 0.5` 신규 추가
  - [x] `entry_executor.score_threshold_crisis: 3` 신규 추가
- [x] 각 변경 라인에 주석으로 근거 명시 (제안서 경로 + 활성화 일자)
- [x] yaml 로드 검증 (python yaml.safe_load) PASS

### 단위 4: code-tester 검증 + 배포
- [x] code-tester 에이전트 호출 → 심각 0건/주의 5건 보고 수신
- [x] 주의 4건 즉시 반영:
  - [x] fund_guard.py `compute_capital_limit` docstring에 5-b 흐름 추가
  - [x] fund_guard.py `allow_order` docstring 부분 흡수 분기 명시
  - [x] fund_guard.py base_only 분기 logger.debug 추가 (관찰성 향상)
  - [x] test_entry_executor.py `_make_executor` 픽스처에 `fund_guard.compute_capital_limit.return_value` 추가 (사전 깨짐 15건 해결)
- [ ] 주의 1건 후속 작업으로 분리: phase2 `_compute_order_amount`에 `external_risk_active` 미전달 (현재 운용 영향 없음, Phase 2 안정화 후 개선)
- [x] py_compile PASS (fund_guard.py / entry_executor.py / main_orchestrator.py)
- [x] 회귀 누적 55/55 PASS (DC 20 + EE 35)
- [ ] **사용자 작업**: 이중 실행 점검 (`ps aux | grep main.py | grep -v grep`)
- [ ] **사용자 작업**: `sudo systemctl restart trading_system`
- [ ] **사용자 작업**: `sudo systemctl status trading_system` 정상 기동 확인
- [ ] **사용자 작업**: 텔레그램 시작 메시지 수신 확인

### 단위 5: 문서 갱신 + change_log
- [x] `docs/improvements/change_log.md` 1줄 추가 (before/after + code-tester 요약 + 롤백 / 후속 명시)
- [ ] `memory/project_closing_bet_followups.md` 갱신 (다음 세션 또는 사용자 결정)
- [ ] `memory/MEMORY.md` 인덱스 한 줄 갱신 (다음 세션 또는 사용자 결정)
- [ ] `CLAUDE.md` 종가베팅 섹션 업데이트 (자본 한도 산식 변경 명시)
- [ ] active/ → completed/20260529_closing-bet-crisis-capital-policy/ 아카이브 (배포 + 1주 관찰 완료 후)

## 검증

- [x] 단위 테스트 신규 8건 PASS (DC-15~18 + EE-31~34)
- [x] 기존 종가베팅 테스트 회귀 PASS (DC 14건 + EE 31건)
- [x] 변경 파일 py_compile PASS
- [ ] DRY-RUN 시뮬 (운영 환경 systemctl restart 후 자연 트리거 검증)

## 배포

- [x] 단위별 commit (단위 1+2+3+4 묶음 + 단위 5 분리)
- [ ] **사용자 작업**: PR 또는 main 머지 + push
- [ ] **사용자 작업**: systemctl restart trading_system
- [ ] **사용자 작업**: 익일 09:25(스윙) / 15:18(종가 phase1) 정상 진입 모니터링

## 문서 업데이트

- [x] `docs/improvements/change_log.md` 1줄 추가
- [ ] `memory/project_closing_bet_followups.md` 갱신
- [ ] `memory/MEMORY.md` 갱신
- [ ] `CLAUDE.md` 종가베팅 섹션 업데이트
- [ ] active/ → completed/ 아카이브 (1주 관찰 후)

## 관찰 (배포 후 1주, 사용자 진행)

- [ ] DANGER 일자 진입 건수 일평균 ≥ 1건
- [ ] DANGER 일자 평균 net_pnl_pct 손실 -3% 이내
- [ ] DANGER 시 score=2 후보 진입 0건 (차단 확인)
- [ ] NORMAL 시 진입 건수 변화 없음
- [ ] swing 매수 실패 0건
- [ ] 1주 후 재평가: 데이터 기반 유지/조정/롤백 판단

## 후속 분리 작업 (별도 단위)

- [ ] phase2 `_compute_order_amount` 에 `external_risk_active` 전달 (code-tester 주의 1번)
- [ ] universe 단가 다양화 — 대형주 비중 축소, 중소형주 보강
- [ ] DANGER 시 `caution_ratio_multiplier` (0.5) 추가 완화 검토 (1주 관찰 후)
- [ ] memory/MEMORY.md + CLAUDE.md 갱신 (배포 + 안정화 후)
