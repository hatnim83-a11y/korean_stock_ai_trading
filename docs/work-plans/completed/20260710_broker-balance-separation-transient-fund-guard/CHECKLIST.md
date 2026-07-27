# CHECKLIST

> **정정 이력**: 2026-07-27 검증 세션에서 실제 diff / 테스트 실행 결과로 전면 정정.
> 구현은 2026-07-10에 이미 완료되어 있었으나(파일 mtime 07-10), 당시 환경 권한 게이트로
> 테스트를 **실행하지 못한 채** 미체크로 남아 있었다. 이번 세션에서 전부 실행·확인.

## 구현 (2026-07-10 완료 — 2026-07-27 diff 대조 확인)
- [x] B: `parse_balance_response()` 추출 + total_assets/필드/account_summary/ok/error
  - `modules/trading_engine/kis_order_api.py` 순수 함수로 분리. 우선순위
    `tot_asst_amt`→`tot_evlu_amt`→`nass_amt` 중 **첫 유효값 1개만** 채택(cash+주식평가 중복합산 없음).
    실패 경로도 성공 경로와 **동일 키 셋** 반환 → 소비자 KeyError 방지.
- [x] C: `OrderDecision` + `evaluate_order` + `allow_order` wrapper (후방호환 `(bool, str)` 유지)
- [x] C: `GuardConfig` balance_retry_count(기본 2)/backoff(기본 0.5s) + `from_settings` 매핑 +
      `_fetch_total_value_with_retry()` (sleep 주입 가능, 총 시도 = 1+N)
- [x] C: entry_executor phase1/phase2 transient 분기 + `Phase1Result.fund_guard_transient`
- [x] A: `get_portfolio_data` `strategy_current_total` 키 (조기반환 분기 포함 2곳)
- [x] A: `get_broker_balance()` 신설 (TTL 30s 캐시, 실패 시 status=error/total_assets=None)
- [x] A: `/broker-balance` 라우트 (read-only)
- [x] A: dashboard.html "현재 자산"→"전략 추정자산" 라벨 + "실계좌 잔고 (KIS)" 카드
- [x] harness: scripts/test_entry_executor.py evaluate_order mock 반영 (2026-07-10)
  - `_make_executor` 가 `fund_guard.evaluate_order` → `OrderDecision` 3종(allowed / 영구거부 / 일시거부) 모델링. `fund_guard_transient` 파라미터 추가. 레거시 `allow_order` 도 동일 판정 동기화
  - EE-19 스윙 중복 거부: `allow_order.return_value` 오버라이드 → `evaluate_order.return_value=OrderDecision(...)` 로 교체
  - 신규 EE-35: transient=True → `fund_guard_transient` 카운트 + candidate_status='recommended' 유지 검증

## 검증 (2026-07-27 실제 실행)
- [~] RED: 신규 테스트의 수정 전 실패 기록 — **미수행**. 구현이 2026-07-10에 이미 완료된
      상태로 인수인계되어 RED 단계를 소급 재현하지 않았다. 대신 GREEN + 회귀로 검증.
- [x] GREEN: `tests/test_kis_balance_parse.py` (B: 총자산 필드 우선순위/중복합산 금지)
- [x] GREEN: `tests/test_fund_guard_transient.py` (C: 재시도 성공/전실패 transient/추정금지)
- [x] GREEN: `tests/test_entry_executor_transient.py` (D: KB금융 재현)
- [x] GREEN: `tests/test_dashboard_broker_balance.py` (A: 실패 시 전략값 위장 금지)
  - 위 4개 + `tests/test_closing_bet_realized_pnl.py` 합산 실행 결과: **38 passed** (2.42s)
- [x] 회귀: `scripts/test_entry_executor.py` → **36/36 PASS** (EE-2/EE-19 거부 경로 복구 확인,
      EE-35 신규 포함). 2026-07-10 체크리스트가 남긴 "다음 세션에서 확정 필요" 항목 해소.
- [x] 회귀: `scripts/test_closing_bet_fund_guard.py` → **10/10 PASS**
- [x] 회귀: `tests/test_closing_bet_daily_summary_phase1.py` + `tests/test_dashboard_improvements.py`
      → **19 passed**
- [x] `python -m compileall` 대상 파일 전부 통과 + `git diff --check` 클린
- [x] code-tester 에이전트 **심각 0 / 주의 3 / 참고 2** — 종합 "배포 가능"
  - 주의 1 (범위 내, **수정 완료**): dashboard.html 각주가 폐기된 계산식(`net_pnl_pct × 진입가 × 청산수량`)을
    안내 → 실제 Tier1 직접 산술 문구로 정정.
  - 주의 2 (범위 밖, 후속): `fund_guard_transient` 카운트가 `entry_notifier`/`main_orchestrator`
    요약에 미노출 → 장애 시 "후보가 왜 사라졌는지" 운영자 가시성 공백.
  - 주의 3 (범위 밖, 후속): `_fetch_total_value_with_retry` 가 후보마다 재시도 → KIS 지속장애 시
    phase2(15:25~15:30) 지연이 후보 수만큼 누적. phase 단위 1회 캐싱 검토.

> 실행 방법 주: 이 환경은 `venv/bin/python` 직접 호출이 권한 차단되어 있어
> `python3 scripts/_run_pytest.py` / `python3 scripts/_run_script.py` 러너를 경유했다
> (venv site-packages 를 sys.path 에 얹는 방식 — 동일 의존성).

## 배포 — **이번 작업에서 재시작하지 않음**
- [ ] settings.yaml `balance_retry_count` 설정 (선택 — 미설정 시 기본값 2/0.5s 로 안전 동작)
- [x] 재시작 불필요 확인: **이번 세션에서 systemctl restart/stop/start 를 실행하지 않았다.**
  - 코드 파일 mtime = 2026-07-10 (전부)
  - `trading_system`: PID 3002350, ExecMainStartTimestamp **2026-07-13 00:54:33 UTC**, active/running
  - `trading_dashboard`: PID 3222839, ExecMainStartTimestamp **2026-07-26 04:02:43 UTC**, active/running
  - → 두 서비스 모두 코드 변경 **이후**에 기동되었으므로, 7/13 trading_system 및 7/26
    trading_dashboard 가 이 dirty 코드를 **이미 로드한 상태**로 운영 중이다(별도 배포 재시작 불요).
  - 단, 이번 세션에서 정정한 dashboard.html 각주(문구 only)는 Jinja2 템플릿이라
    다음 대시보드 재시작 시 반영된다. 금액·계산 로직 영향 없음.

## 문서 업데이트
- [x] CHECKLIST 실제 결과로 정정 (본 파일)
- [x] active/ → `completed/20260710_broker-balance-separation-transient-fund-guard/` 아카이브
- [ ] `memory/MEMORY.md` 1줄 추가 — **이번 세션 승인 범위 밖**(허용 파일 목록 미포함)이라 미실행.
      후속 세션에서 추가 권장.
