# CONTEXT — 종가베팅 누적 실현손익 노출

## 변경 이유
사용자가 오늘 종가베팅 실현손익(+236,742원)이 대시보드 전역 실현손익/현재자산에 반영됐는지 물었고,
read-only 조사 결과 **미반영**임이 확인됨. 종가베팅 탭에 별도의 정확한 누적 실현손익을 노출하고,
전역 자산과 KIS 잔액 불일치의 원인을 코드/문서로 드러내는 것이 목적.

## 현재 코드 상태 (파일:라인)

### 전역 portfolio 산출 — `web/dashboard_service.py:76-181` `get_portfolio_data()`
- `web/dashboard_service.py:80` `sell_trades = db.get_all_sell_trades()` → **trading.db** 소스
- `web/dashboard_service.py:82` `realized_pnl = sum(t.get("profit_amount") or 0 for t in sell_trades)`
  - → closing_bet.db 는 **전혀 조회하지 않음**. 종가베팅 실현손익 구조적 미포함.
- `web/dashboard_service.py:165-167`
  ```
  cash_remaining = max(0, settings.TOTAL_CAPITAL - total_invest)
  current_total  = cash_remaining + total_eval + int(realized_pnl)
  ```
  - → `settings.TOTAL_CAPITAL`(고정 config) 기반 모델값. **KIS get_balance() 미사용**.
- KIS `total_value` 는 `web/dashboard_service.py:597` 부근의 별도 함수(자금 계산)에서만 사용 → current_total 과 무관.

### 종가베팅 어댑터 — `closing_bet_system/dashboard/data_adapter.py`
- `_open_ro()` (46-61): `closing_bet.db` read-only 연결. DB/스키마 부재 시 None 반환(예외 흡수).
- `_resolve_db_path()` (36-43): settings.yaml 기반 경로. 테스트는 이 함수를 monkeypatch.
- 기존 5개 헬퍼 모두 read-only, 빈 결과 안전 반환 패턴. 신규 함수도 동일 패턴 준수.

### 종가베팅 API — `web/api_routes.py:94-127`
- `/closing-bet/today`, `/gate-progress`, `/orderbook-history`, `/rejections`, `/fund-guard-status` 5개.
- 모두 `asyncio.to_thread(cb_adapter.xxx)` 래핑. 신규 엔드포인트도 동일.

### 종가베팅 UI — `web/templates/dashboard.html`
- panel: `#panel-closingbet` (654-716). 상단 "운영 점검 게이트" 섹션 앞/뒤에 카드 추가 가능.
- JS: `loadClosingBet()` (1131-1225), `Promise.all` 5 API → 6 API 로 확장.
- 스타일 헬퍼: `fmt()`(734), `valueClass()`(737, plus/minus), `fmtPct()`(735).

## 핵심 스니펫 — closing_bet.db candidates 스키마 (관련 컬럼)
```
net_pnl_pct           REAL    # 엔진 산출 순수익률 (소수). ⚠️ 실현 원화와 불일치하는 행 존재
entry_price           REAL    # 진입 평균 체결가
entry_amount          REAL    # 실제 진입 총원가 (KRW). 전 15행 == entry_price×exit_shares
exit_price            REAL    # 청산 평균 체결가
exit_shares           INTEGER # 최종 청산 수량
buy_commission        REAL    # 매수 수수료 (KRW 절대금액)
sell_commission       REAL    # 매도 수수료 (KRW)
transaction_tax       REAL    # 거래세 (KRW)
estimated_slippage    REAL    # 추정 슬리피지 (KRW)
final_exit_time       TEXT    # ISO8601 +09:00 (예: 2026-07-10T09:05:46.817509+09:00)
entry_phase1/2_executed_shares  # 진입 체결수량 (합 == exit_shares, 전 15행 검증됨)
```

## 계산 검증 (2026-07-10 financial-correctness follow-up — read-only)

**개정 결정**: 저장된 `net_pnl_pct` 를 신뢰하는 재구성이 15행 중 2행에서 실현 원화와 불일치.
→ **direct persisted-cost arithmetic**(실제 체결가·비용 레그 직접 산술)로 primary 전환.

| candidate | direct `(exit−entry)×shares − Σcosts` | net_pnl_pct 재구성 | 차이 |
|-----------|-----------|-----------|------|
| 251 HPSP  | `(58000−60100)×2 − (9.015+8.7+104.4+118.1)` = **−4,440.22** | −4,680.08 | −239.86 |
| 414 LG엔솔 | `(394500−389500)×4 − (116.85+118.35+1420.2+1568.0)` = **+16,776.60** | +13,552.18 | −3,224.42 |
| 나머지 13행 | 두 방식 동일 | 동일 | 0 |
| **누적 15건** | **+341,736.71** | 338,272.43 | +3,464 |

- 사용자 지시문의 문자 그대로의 공식 `net_pnl_pct × (entry_amount + buy_commission)` 은 위
  "net_pnl_pct 재구성"과 동일(전 15행 `entry_amount == entry_price×exit_shares`)하여
  타깃 −4,440 / +16,777 을 **재현하지 못함**. 사용자가 명시한 **검증 타깃값**(= "persisted-cost
  explicit")과 "reconcile with direct persisted-cost arithmetic to rounded KRW" 요구를
  동시에 만족하는 유일한 해가 direct arithmetic 이므로 이를 primary 로 채택.
- `exit_shares ≠ 진입수량` 우려: 전 15행(및 exit_shares>0 전수)에서 `exit_shares == p1+p2` 라
  현행 데이터엔 해당 행 없음. 그럼에도 Tier 2 폴백은 exit_shares 대용 금지 + 진입체결수량 사용으로
  하드닝(회귀 테스트 `test_tier2_fallback_uses_entry_phase_qty_not_exit_shares`).

## 과거 버그 / 주의
- `daily_snapshots` 총자본 컬럼은 `total_capital` (total_value 아님) — web/CLAUDE.md.
- data_adapter 는 read-only 원칙: `mode=ro` URI. write 절대 금지.
- sqlite `date()` 는 +09:00 오프셋을 UTC 변환할 수 있어 KST 날짜가 밀릴 위험 → **문자열 접두 비교**(`final_exit_time[:10]`)로 KST 청산일 판정.

## 영향 범위
- 순수 read-only 추가. 전역 portfolio/trades/performance 계산 및 응답 스키마 무변경.
- closing_bet.db / trading.db write 없음. KIS 신규 호출 없음.
