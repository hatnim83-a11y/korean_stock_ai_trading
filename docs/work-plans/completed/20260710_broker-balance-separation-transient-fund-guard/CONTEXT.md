# CONTEXT

## 관측된 사실 (2026-07-10 KST)
1. `web/dashboard_service.py:166` `current_total = cash_remaining + total_eval + realized`
   = 내부 전략 추정자산. `dashboard.html:800` "현재 자산" 카드로 표시 → KIS 실잔고로 오인.
2. `modules/trading_engine/kis_order_api.py:797` `total_value = tot_evlu_amt`,
   `cash = dnca_tot_amt`. output2의 계좌총자산 원본 필드 미보존.
3. 15:18 entry에서 KIS inquire-balance 500 2회. 첫 호출 다음 성공(총평가 9,091,759)이나
   KB금융(candidate 666, L1/L2/L3=1/1/0 total=2)은 두번째 transient 500으로
   `fund_guard:총 자산 조회 실패 또는 0원 (보수적 차단)` → 영구 `rejected_filter`. 전일은 entered.

## 현재 코드 상태 (파일:라인)
- `fund_guard.allow_order()` `fund_guard.py:159`
  - `191` `total_value = self._get_total_value()` → `192 if total_value <= 0: return False, "총 자산 조회 실패..."`
  - `_get_total_value()` `363` → provider 주입 or `kis_client.get_total_account_value()`
  - `kis_client.get_total_account_value()` `kis_client.py:76` → `order_api.get_balance()["total_value"]`, 실패 시 0
- `entry_executor._process_phase1_candidate()` `entry_executor.py:305`
  - `370` `allowed, reason = fund_guard.allow_order(...)`
  - `375` `if not allowed:` → `mark_rejected_by_filter(candidate_id, f"fund_guard:{reason}")` (영구)
- phase2 동일 패턴 `entry_executor.py:543-554`
- `candidate_status` CHECK enum: `recommended/entered/rejected_filter/rejected_manual`
  (`storage/db.py:253`). transient는 enum 추가 대신 **recommended 유지**로 처리.

## 핵심 스니펫 (수정 방향)
### fund_guard OrderDecision + retry
```python
@dataclass(frozen=True)
class OrderDecision:
    allowed: bool
    reason: str
    transient: bool = False  # True=일시적(재시도가능), 영구 rejected_filter 금지

def evaluate_order(self, ticker, amount, *, external_risk_active=False) -> OrderDecision:
    ... 입력검증(비-transient) ...
    total_value, tv_transient = self._fetch_total_value_with_retry()
    if total_value <= 0:
        return OrderDecision(False, "총 자산 조회 실패 또는 0원 (보수적 차단)", transient=tv_transient)
    ...
def allow_order(self, ...) -> tuple[bool, str]:
    d = self.evaluate_order(...)
    return d.allowed, d.reason
```
재시도: `balance_retry_count`회 추가 시도, 각 실패 후 `sleep_func(backoff)`. 값>0 확보 즉시 성공.
모두 실패 → `(0, True)`. **추정치로 대체 금지**(항상 0 반환→차단).

### entry_executor transient 분기
```python
decision = await asyncio.to_thread(lambda: self.fund_guard.evaluate_order(...))
if not decision.allowed:
    if decision.transient:
        order.rejection_reason = "fund_guard_transient"   # 후보 recommended 유지
        logger.warning(...일시적 잔고오류 → 영구거부 보류...)
    else:
        await asyncio.to_thread(self.candidate_logger.mark_rejected_by_filter, ...)
        order.rejection_reason = "fund_guard"
    return order
```

### dashboard 분리
- `get_portfolio_data()`: `strategy_current_total` 키 추가(값=current_total). 라벨만 재정의.
- `get_broker_balance()`: 신규. KIS get_balance → `{source:"KIS", status, fetched_at,
  total_assets, cash, eval_amount, total_assets_field, error}`. 실패 시 status="error",
  total_assets=None. **전략값 폴백 금지.**

## 과거 버그 / 주의
- KIS 응답 파싱은 `_safe_int()`/`_safe_float()`로 빈문자열 방어(CLAUDE.md).
- 시간은 `now_kst()` 사용(서버 UTC).
- get_balance는 web dashboard와 fund_guard가 공유하는 함수 — 반환 dict에 **키 추가만**
  (기존 키 삭제/의미변경 금지). `total_value` 유지 필수.
- 테스트는 sleep 실제 대기 금지 → `sleep_func` 주입.

## 영향 범위
- get_balance 소비자: `kis_client.get_total_account_value`, `dashboard_service`,
  `swing_db_reader`? (아님) — 키 추가라 무영향.
- allow_order 소비자: entry_executor(phase1/2), scripts 테스트 — evaluate_order 도입 후
  allow_order wrapper 유지로 후방호환.
