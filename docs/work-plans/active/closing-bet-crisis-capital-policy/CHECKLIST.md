# CHECKLIST — 종가베팅 위기 시 부분 흡수 + 선별 강화

## 구현

### 단위 1: GuardConfig + compute_capital_limit
- [ ] `closing_bet_system/infra/fund_guard.py` `GuardConfig` 에 `crisis_absorb_ratio: float = 0.0` 추가
- [ ] `from_settings()` 에서 `crisis_absorb_ratio` 로드 + 범위 검증 (0.0~1.0, 이탈 시 기본값)
- [ ] `compute_capital_limit()` 정상 흡수 분기 (line 291~316) 내부에 `external_risk_active` 시 `swing_idle = int(swing_idle * cfg.crisis_absorb_ratio)` 적용
- [ ] debug_info 에 `crisis_partial_absorb` / `crisis_absorb_ratio` 키 추가
- [ ] 단위 테스트 신규 3건:
  - [ ] `test_compute_capital_limit_normal_full_absorb` (NORMAL: 전체 흡수 정상 동작)
  - [ ] `test_compute_capital_limit_crisis_partial_absorb` (DANGER + crisis_absorb_ratio=0.5: swing_idle × 0.5 적용)
  - [ ] `test_compute_capital_limit_crisis_disable_fallback` (DANGER + disable_absorb_on_crisis=true: base만 반환, 기존 동작 유지)

### 단위 2: EntryExecutorSettings + score_threshold 동적 적용
- [ ] `closing_bet_system/execution/entry_executor.py` `EntryExecutorSettings` 에 `score_threshold_crisis: int = 2` 추가
- [ ] settings 로드 함수에서 `entry_executor.score_threshold_crisis` 로드
- [ ] `_select_phase1_candidates()` 시그니처 변경: `*, external_risk_active: bool = False`
- [ ] threshold 분기 적용 (외부 위기 시 `score_threshold_crisis`)
- [ ] `execute_phase1()` 호출 시 `external_risk_active=external_risk_active` 전달
- [ ] 단위 테스트 신규 3건:
  - [ ] `test_select_phase1_candidates_normal_threshold_2` (NORMAL: score≥2 후보 선정)
  - [ ] `test_select_phase1_candidates_crisis_threshold_3` (DANGER: score=2 후보 차단, score≥3만 선정)
  - [ ] `test_execute_phase1_passes_external_risk_to_select` (execute_phase1 → _select_phase1_candidates 호출 인자 검증)

### 단위 3: settings.yaml 변경
- [ ] `closing_bet_system/config/settings.yaml` 변경:
  - [ ] `fund.disable_absorb_on_crisis: true → false`
  - [ ] `fund.crisis_absorb_ratio: 0.5` 신규 추가
  - [ ] `entry_executor.score_threshold_crisis: 3` 신규 추가
- [ ] 각 변경 라인에 주석으로 근거 명시 (제안서 경로 + 활성화 일자)

### 단위 4: code-tester 검증 + 배포
- [ ] code-tester 에이전트 호출 → 심각/주의 이슈 보고 확인
- [ ] 심각 이슈 0건 도달까지 수정
- [ ] py_compile 통과 (`python -m py_compile closing_bet_system/infra/fund_guard.py closing_bet_system/execution/entry_executor.py`)
- [ ] 기존 테스트 전수 PASS 확인 (`python -m pytest closing_bet_system/`)
- [ ] 이중 실행 점검: `ps aux | grep main.py | grep -v grep` 으로 systemd 외 프로세스 확인
- [ ] PID 파일 잔여 제거: `trading_system.pid` 확인
- [ ] `sudo systemctl restart trading_system`
- [ ] `sudo systemctl status trading_system` 정상 기동 확인
- [ ] 텔레그램 시작 메시지 수신 확인

### 단위 5: 문서 갱신 + change_log
- [ ] `docs/improvements/change_log.md` 1줄 추가:
  ```
  | 2026-05-XX | closing_bet 위기 시 부분 흡수 + 선별 강화 | before: disable_absorb_on_crisis=true / score_threshold=2 단일값 → DANGER 시 base 10% 만 사용, 후보 단가(중앙값 250k) 매수 불가 (5/27 4건 전원 price_cap) | after: disable_absorb_on_crisis=false / crisis_absorb_ratio=0.5 / score_threshold_crisis=3 → DANGER 시 swing_idle × 0.5 흡수 (capital_limit ~5배 회복) + score≥3 만 진입 (약한 신호 차단) | docs/improvements/20260529_closing_bet_capital_limit_crisis_policy.md | hatni | 표본 부족 신뢰도 Low. 1주 관찰 후 재평가. 5/26 손실 패턴(-4.13%) 은 변경 후에도 통과 (score=4 그대로) — 본 변경의 직접 손실 차단 효과 없음 |
  ```
- [ ] `memory/project_closing_bet_followups.md` 갱신
- [ ] `CLAUDE.md` 종가베팅 섹션 업데이트 (위기 시 부분 흡수 정책 명시)

## 검증

- [ ] 단위 테스트 신규 6건 전부 PASS
- [ ] 기존 종가베팅 테스트 회귀 PASS
- [ ] DRY-RUN 시뮬 (가능하면): MarketGuard mock DANGER → phase1 candidate select → score_threshold_crisis=3 적용 확인
- [ ] 실 환경 DRY-RUN 검증 (entry_executor.dry_run=true 일시 토글 후 1일 관찰)

## 배포

- [ ] 작업 브랜치 정리 (단위별 commit 권장)
- [ ] PR/머지 (또는 직접 main 푸시 — 사용자 권한)
- [ ] systemctl restart trading_system
- [ ] 익일 09:25 (스윙 매수)/15:18 (종가 phase1) 정상 진입 모니터링

## 문서 업데이트

- [ ] `docs/improvements/change_log.md` 1줄 추가 (위 단위 5 참조)
- [ ] `memory/project_closing_bet_followups.md` 갱신 — 위기 시 부분 흡수 정책 추가
- [ ] `memory/MEMORY.md` 갱신 — project_closing_bet_followups 한 줄 갱신
- [ ] `CLAUDE.md` 종가베팅 섹션 업데이트 — 자본 한도 산식 변경 명시
- [ ] active/ → completed/YYYYMMDD_closing-bet-crisis-capital-policy/ 아카이브

## 관찰 (배포 후 1주)

- [ ] DANGER 일자 진입 건수 일평균 ≥ 1건
- [ ] DANGER 일자 평균 net_pnl_pct 손실 -3% 이내
- [ ] DANGER 시 score=2 후보 진입 0건 (차단 확인)
- [ ] NORMAL 시 진입 건수 변화 없음
- [ ] swing 매수 실패 0건
- [ ] 1주 후 재평가: 데이터 기반 유지/조정/롤백 판단
