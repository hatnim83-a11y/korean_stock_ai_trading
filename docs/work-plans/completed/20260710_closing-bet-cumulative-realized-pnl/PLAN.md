# PLAN — 종가베팅 누적 실현손익 노출 + 자산 정합성 가시화

## 목표
1. 종가베팅 탭에 **누적 실현 손익 / 청산 완료 건수 / 오늘 실현 손익**을 정확히 표시.
2. 대시보드 전역 `실현 손익`·`현재 자산`이 종가베팅 PnL을 포함하지 않는다는 사실과, KIS 잔액과의 불일치 원인을 **코드/문서/테스트로 명확히 드러낸다**.

## 배경 (2026-07-10 KST read-only 검증)
- `closing_bet.db` closed candidates 15건 (`final_exit_time IS NOT NULL AND exit_shares > 0`).
- 오늘 청산 2건: 금호건설 +173,776원, KB금융 +62,966원 = **+236,742원** (본 작업 공식과 정확히 일치).
- 누적 실현손익: 초기 net_pnl_pct 공식 = +338,222원 → **direct persisted-cost arithmetic 개정 후 = +341,736.71원** (15건). 개정 사유는 아래 "실현손익 계산식" 참조.
- 전역 dashboard `/portfolio`: realized_pnl **601,669원**, current_total **9,556,966원**.
- KIS `get_balance()`: total_value **9,127,759원**, cash 7,232,751원, position_count 6.
- **불일치 금액**: current_total − KIS total_value = **+429,207원**.

### 정합성 결론 (원인)
- 전역 `realized_pnl` = `trading.db`의 `get_all_sell_trades().profit_amount` 합 → **closing_bet.db PnL 미포함** (별도 DB).
- 전역 `current_total` = `settings.TOTAL_CAPITAL`(고정 config) + 로컬 holdings 평가 + 로컬 realized → **KIS 잔액을 사용하지 않는 모델값**. broker 실제(6종목)와 구조적으로 다름.
- 따라서 429,207원 불일치는 버그가 아니라 **설계상 서로 다른 두 수치**(모델 vs broker)의 차이.

### 실현손익 계산식 (2026-07-10 financial-correctness follow-up로 개정)

> **개정 사유**: 초기 공식(`net_pnl_pct × entry_price × exit_shares (+ buy_commission)`)이
> 완전청산 15행 중 2행에서 실제 실현 원화와 불일치. 저장된 `net_pnl_pct`(엔진 산출)가
> 실현 원화 손익과 정합하지 않는 행이 존재하기 때문(candidate 251: -4,680.08 재구성 vs
> **-4,440.22 실제**; candidate 414: +13,552.18 재구성 vs **+16,776.60 실제**).
> `entry_amount == entry_price × exit_shares` 이고 `exit_shares == 진입체결수량`이 **전 15행에서
> 성립**하므로 exit_shares↔entry_amount 치환은 현행 데이터에서 무영향(no-op) — 실제 결함은
> net_pnl_pct 신뢰였음. → **직접 원가 산술(direct persisted-cost arithmetic)로 전환**.

3단(Tier) 로직 (`data_adapter._row_realized_pnl`):
```
Tier 1 (primary, 저장 net_pnl_pct 무시):
  row_pnl = (exit_price − entry_price) × exit_shares
            − (buy_commission + sell_commission + transaction_tax + estimated_slippage)
  조건: entry_price>0 AND exit_price>0 AND exit_shares>0. 비용 레그 NULL → 0.0(가산 비용).

Tier 2 (fallback, exit_price 부재 legacy):
  row_pnl = net_pnl_pct × (entry_cost_basis + buy_commission)
  entry_cost_basis = entry_amount(>0) 우선, 없으면 entry_price × 진입체결수량(p1+p2).
  ※ exit_shares 를 진입수량 대용으로 쓰지 않음. buy_commission NULL → 설정율 재구성(실패 0.0).

Tier 3 (skip-safe): 진입원가/손익 미확정 → skip(손익 미조작), skipped_incomplete 로 집계.

cumulative = Σ row_pnl  over rows where final_exit_time IS NOT NULL AND exit_shares > 0
today      = Σ row_pnl  where final_exit_time[:10] == today_kst   # KST 청산일 접두 비교
```
- 오늘 필터는 `final_exit_time` ISO 문자열의 날짜 접두(=KST 캘린더일) 비교 → 타임존 안전.
- 실 DB 재현: Tier 1 총합 15건 = **+341,736.71** (기존 net_pnl_pct 방식 338,272와 상이).
  개별 251=-4,440 / 414=+16,777 원 단위 재현(회귀 테스트 고정).

## 비목표 (Non-Goals)
- 전역 `realized_pnl`·`current_total`을 종가베팅 PnL과 **합산/덮어쓰지 않는다**.
- 자산 계산을 KIS 값으로 덮어쓰지 않는다. trading.db / closing_bet.db **write/migration 없음**.
- request handler에 신규 실시간 KIS API 호출 추가 없음.
- KIS reconciliation(전역 자산 재설계)은 별도 작업 + 사용자 승인.

## 구현 단계
1. `closing_bet_system/dashboard/data_adapter.py` — `get_realized_pnl_summary()` read-only 함수 추가. **(2026-07-10 개정)** 계산을 `_row_realized_pnl()` 3단 로직으로 분리 + `_num()` 헬퍼 추가 + 반환 dict에 `skipped_incomplete` 키 추가.
2. `web/api_routes.py` — `GET /api/v1/closing-bet/realized-pnl` 신규 엔드포인트.
3. `web/templates/dashboard.html` — 종가베팅 탭 상단에 "누적 실현 손익" 카드 섹션(누적/오늘/청산건수) + JS 렌더.
4. 테스트: `tests/test_closing_bet_realized_pnl.py` — Tier 1 직접계산(비용 레그 차감·net_pnl_pct 무시) + **실 DB 251/414 rounded KRW 재현** + exit_shares≠진입수량 Tier 2 폴백 + Tier 3 skip-safe + 오늘/누적 분리 + 부분/비청산 제외 + reconciliation 회귀(전역 realized_pnl은 종가베팅 미포함).

## 변경 파일
- `closing_bet_system/dashboard/data_adapter.py` (신규 함수)
- `web/api_routes.py` (신규 엔드포인트)
- `web/templates/dashboard.html` (카드 + JS)
- `tests/test_closing_bet_realized_pnl.py` (신규)

## 검증
- RED → GREEN: 신규 테스트, `python -m pytest tests/test_closing_bet_realized_pnl.py -v`
- 관련 스위트: `tests/test_closing_bet_daily_summary_phase1.py`, `tests/test_dashboard_improvements.py`
- 정적: `python -m compileall closing_bet_system/dashboard/data_adapter.py web/api_routes.py`
- code-tester 에이전트 검토.

## 롤백
- 순수 추가(신규 함수/엔드포인트/UI 카드)라 기존 응답·계산 무변경. UI 카드 블록 + JS 블록 + 엔드포인트 + 함수 제거로 완전 원복.

## Deploy Approval Gate
- 코드 구현·테스트까지 승인됨. **production dashboard(`trading_dashboard.service`) restart/배포는 사용자 명시 승인 전 금지**.
- 배포 시: `sudo systemctl restart trading_dashboard.service` (본 작업에서 실행하지 않음).

## 완료 기준
- 종가베팅 탭에 누적 실현손익/오늘 실현손익/청산 완료 건수가 원화·양음 스타일로 표시.
- 전역 portfolio 카드 수치 무변경(회귀 없음).
- 신규 테스트 전부 GREEN, compileall 통과, 불일치 원인 문서화 완료.
