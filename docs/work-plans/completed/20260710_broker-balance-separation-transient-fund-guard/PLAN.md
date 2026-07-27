# PLAN — 실계좌 잔고 분리 표시 + 종가베팅 일시적 잔고오류 비영구화

## 목표
2026-07-10 관측된 3가지 문제를 근본 수정한다.

- **A. 대시보드 자산 표기 혼동**: `get_portfolio_data()`가 `TOTAL_CAPITAL + realized + eval`
  로 계산한 **내부 전략 추정자산**(`current_total`)을 "현재 자산"으로 표시해 실제 KIS
  계좌 잔고로 오인. → 전략 추정치와 KIS 실계좌 잔고를 **엄격히 분리** 표시/반환.
  KIS 조회 실패 시 전략 계산값을 실잔고로 위장 금지. 실패/미조회는 `-` / `조회 실패`.
- **B. get_balance 총자산 필드 부정확**: `tot_evlu_amt`만 total_value로 쓰고 KIS output2
  의 실제 계좌총자산 필드를 보존하지 않음. → 문서상 총자산 필드(우선순위 명시,
  cash+주식평가 **중복 합산 금지**)를 원본 보존하며 노출. 성공/실패 메타데이터 추가.
- **C. 일시적 KIS 5xx 한 번에 후보 영구 rejected_filter**: 15:18 entry에서 KIS
  inquire-balance가 transient 500을 반환 → fund_guard `총 자산 조회 실패(보수적 차단)`
  → KB금융(candidate 666)이 영구 `rejected_filter`. → 제한된 재시도(짧은 backoff,
  주입 가능한 sleep) 후에도 불가하면 **여전히 안전 차단**하되 후보는 **비영구/transient
  상태**로 남겨 다음 재시도·관측 가능. **잔고 추정으로 주문 허용 절대 금지.**

## 비목표 / 운영 경계
- systemctl restart/stop/deploy, 실주문, DB 레코드 변경, 설정값 변경 **안 함**.
- 실 KIS API 호출 테스트 **안 함** — 전부 fixture/mock/주입.
- 현재 작업트리의 타인 미커밋 변경(data_adapter realized-pnl, claude_code_bridge 등) **보존**.
- DB 스키마(candidate_status enum) **변경하지 않음** — transient는 후보를 `recommended`
  로 **유지**(신규 enum 값 불필요 → migration 회피).

## 구현 단계
1. **(B)** `modules/trading_engine/kis_order_api.py` `KISApi.get_balance()`
   - 파싱 로직을 순수 함수 `parse_balance_response(data: dict) -> dict`로 추출(테스트 용이).
   - `total_assets`(우선순위 `tot_asst_amt`→`tot_evlu_amt`→`nass_amt`, 중복합산 금지) +
     `total_assets_field`(사용 필드명) + `net_asset_amount`(nass_amt) + `account_summary`
     (raw output2 핵심필드 보존) + `ok`/`error` 추가. 기존 `total_value`(=tot_evlu_amt) 유지.
2. **(C)** `closing_bet_system/infra/fund_guard.py`
   - `OrderDecision(allowed, reason, transient)` dataclass + `evaluate_order()` 신설.
     `allow_order()`는 `(allowed, reason)` 반환 wrapper로 후방호환 유지.
   - `GuardConfig`에 `balance_retry_count`/`balance_retry_backoff_sec` 추가(settings.yaml 매핑).
   - 잔고 조회 재시도 헬퍼(주입 가능한 `sleep_func`, 기본 `time.sleep`) → 실패 시
     `transient=True`로 차단. DB 조회 실패도 `transient=True`.
3. **(C)** `closing_bet_system/execution/entry_executor.py`
   - phase1/phase2 fund_guard 호출을 `evaluate_order`로 교체. `decision.transient`면
     `mark_rejected_by_filter` **호출 안 함**(후보 recommended 유지) + `rejection_reason=
     "fund_guard_transient"`. 비-transient는 기존대로 영구 rejected_filter.
   - `Phase1Result.fund_guard_transient` 카운터 추가.
4. **(A)** `web/dashboard_service.py`
   - `get_portfolio_data()`에 `strategy_current_total` 명시 키 추가(=current_total 유지, 후방호환).
   - `get_broker_balance()` 신설: KIS `get_balance()` 호출(짧은 TTL 캐시), 구조화 반환
     `{source, status, fetched_at, total_assets, cash, eval_amount, total_assets_field, error}`.
     실패/예외 → `status="error"`, `total_assets=None`, 전략값 **폴백 금지**.
5. **(A)** `web/api_routes.py` — `GET /broker-balance` 라우트 추가(read-only).
6. **(A)** `web/templates/dashboard.html`
   - "현재 자산" 라벨 → "전략 추정자산"(툴팁: 내부 전략 계산, 실계좌 아님).
   - 별도 카드 "실계좌 잔고(KIS)" 추가 — total_assets 또는 `조회 실패`/`-`. 민감정보 노출 금지.

## 변경 파일
- `modules/trading_engine/kis_order_api.py` (B)
- `closing_bet_system/infra/fund_guard.py` (C)
- `closing_bet_system/execution/entry_executor.py` (C)
- `web/dashboard_service.py` (A)
- `web/api_routes.py` (A)
- `web/templates/dashboard.html` (A)
- `scripts/test_entry_executor.py` (harness: evaluate_order mock 반영)
- `tests/test_kis_balance_parse.py` (신규, B)
- `tests/test_fund_guard_transient.py` (신규, C+D)
- `tests/test_entry_executor_transient.py` (신규, C+D 재현)
- `tests/test_dashboard_broker_balance.py` (신규, A)

## 롤백 계획
- A: dashboard.html/ api_routes/ dashboard_service revert (신규 키/카드 제거, current_total 유지).
- B: get_balance는 키 **추가**만 → 기존 소비자 무영향. revert 시 파서 함수만 인라인 복귀.
- C: `balance_retry_count=0` 설정 시 재시도 0회(기존 1회 시도) + transient 처리는
  안전측(여전히 차단)이라 무해. 완전 롤백은 evaluate_order/entry_executor revert.

## 완료 기준
- 신규 pytest 4파일 GREEN + 기존 관련 테스트 회귀 없음.
- `python -m compileall` 대상 파일 통과.
- code-tester 심각 이슈 0.
- 문서(change_log 불필요 — 파라미터 변경 아님; MEMORY.md 1줄) 갱신.
