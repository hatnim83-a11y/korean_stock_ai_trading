# CHECKLIST — 자본 배분 동적 분리

## Phase 0: 선행 EV 검증
- [x] 213건 라벨 score별 EV+ 분석
- [x] `docs/improvements/score_ev_distribution_20260523.md` 작성
- [x] LIMIT 4 + score_threshold=2 결정

## Phase A: 설정 + 헬퍼
- [ ] `settings.yaml` fund 섹션 신규 키 5개 추가
  - [ ] `absorb_swing_idle: true`
  - [ ] `swing_capital_ratio: 0.9`
  - [ ] `closing_bet_pool_cap: 0.5`
  - [ ] `swing_used_source: cost_basis`
  - [ ] `disable_absorb_on_crisis: true`
- [ ] `GuardConfig` dataclass 5개 필드 추가 + 기본값
- [ ] `from_settings()` `.get(key, cls.default)` 폴백 (DC-9)
- [ ] `closing_bet_system/infra/swing_db_reader.py` `get_swing_used_value(source)` 신규
  - [ ] `cost_basis` 모드: `SUM(COALESCE(quantity,0) * COALESCE(buy_price,0))`
  - [ ] `evaluation` 모드: position_state JOIN + 폴백
  - [ ] DB 파일 없음 → `ConnectionError` raise
  - [ ] `mode=ro` connection

## Phase B: fund_guard SoT
- [ ] `FundGuard.compute_capital_limit(total_value, external_risk_active)` 신규
- [ ] CRISIS 분기 (`disable_absorb_on_crisis + external_risk_active` → base만)
- [ ] swing_idle = max(0, swing_pool - swing_used) 방어 (DC-8)
- [ ] cap_amount = total × closing_bet_pool_cap 최대
- [ ] `allow_order()` 흐름에서 compute_capital_limit 호출
- [ ] `per_stock_limit = capital_limit // cfg.max_concurrent_positions` (S-1)
- [ ] swing_db_reader ConnectionError 폴백 (swing_pool 가정 → swing_idle=0)
- [ ] 진단 로그: `[fund_guard] capital_limit=N (base=X + swing_idle=Y / cap=Z applied?)`

## Phase C: entry_executor 동적 한도 + LIMIT 4
- [ ] `_compute_order_amount(ratio, external_risk_active=False)` 시그니처 확장
- [ ] fund_guard.compute_capital_limit() 호출 (SoT, S-2)
- [ ] `per_stock = capital_limit // max_concurrent_positions`
- [ ] return `int(per_stock * ratio)`
- [ ] `_select_phase1_candidates`에 `LIMIT 4` 추가
- [ ] `ORDER BY total_score DESC, candidate_id ASC` 안정 정렬 (tie-breaking)
- [ ] execute_phase1 흐름에서 external_risk_active 인자 전달

## Phase D: 스윙 90% + exit 로그
- [ ] `main.py:1340` SWING_CAPITAL_RATIO=0.9 적용
- [ ] `os.getenv("SWING_CAPITAL_RATIO", "0.9")` 환경변수 분리
- [ ] v17 불타기(2차 진입) 코드 grep + 동일 한도 적용 여부 확인
- [ ] `exit_executor.py` 매도 결과 처리부 환원 로그 추가:
  - `logger.info(f"[exit] 스윙 자본 환원 — {cycle} 매도 {sell_qty}주 × {sell_price:,}원 = {sell_amount:,}원 (KIS 자동, 다음 영업일 스윙 09:25 인식)")`

## Phase E: 단위 테스트 12건
- [ ] `scripts/test_dynamic_capital_allocation.py` 신규
- [ ] DC-1: 스윙 풀 0% → cap 도달
- [ ] DC-2: 스윙 풀 100%+ → base 10%만
- [ ] DC-3: 스윙 풀 50% → 10%+40%
- [ ] DC-4: absorb_swing_idle=false → 기존 동작
- [ ] DC-5: 후보 5건 → top 4
- [ ] DC-5-B: 후보 2건 → 2건 반환 (LIMIT 가드)
- [ ] DC-6: 빈 portfolio → swing_used=0
- [ ] DC-6-B: 스윙 DB 없음 → fund_guard 폴백 swing_pool
- [ ] DC-7: SoT 동기화 (fund_guard==entry_executor)
- [ ] DC-8: swing_used > swing_pool → swing_idle=0
- [ ] DC-9: settings 키 누락 → 기본값
- [ ] DC-10: external_risk_active=True → absorb 비활성

## Phase F: code-tester + 회귀
- [ ] code-tester 에이전트 호출 (5 파일 + 신규 테스트)
- [ ] 심각 이슈 0건 → 통과
- [ ] 기존 `scripts/test_closing_bet_fund_guard.py` 회귀 PASS
- [ ] py_compile 6개 파일 PASS

## Phase G: 배포
- [ ] main checkout cp (변경 6 파일 + 테스트 1개 + 3문서)
- [ ] git add 명시적 (지정 파일만)
- [ ] git commit (단일 commit)
- [ ] git push origin/main
- [ ] (별도 단계) `sudo systemctl restart trading_system`
- [ ] journalctl 1분 grep — capital_limit 동적 로그 확인
- [ ] 텔레그램 활성화 알림 (옵션)

## 문서 업데이트
- [ ] `memory/MEMORY.md` 인덱스 한 줄 추가 (project_capital_allocation 신규 메모)
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] `CLAUDE.md` 자본 배분 규칙 섹션 추가

## 아카이브
- [ ] 모든 [x] 완료 후 active/ → completed/20260524_dynamic-capital-allocation/

## 5/25 실발주 검증 항목
- [ ] 15:18 entry_pipeline 자연 발화 시 capital_limit 동적 로그 (base + swing_idle)
- [ ] 종목별 entry_amount가 sample 시나리오 표와 일치
- [ ] 4종목 진입 시도 (score 순)
- [ ] daily_summary entered 카운트 + closed_positions 표시

## 비상 정지
- 1단: settings.yaml `absorb_swing_idle: false` + restart
- 2단: `SWING_CAPITAL_RATIO=1.0` env + restart
- 3단: git revert + restart
- 4단: systemctl stop (전체 중단)
