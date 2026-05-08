# CONTEXT — 단위 2-9c · KRX bulk 우회

## 변경 이유
2026-05-06 15:10 KST 자연 트리거 로그:
```
[universe_v2] 출처별 기여: theme=17 / top_value=0 / top_change=0 / top_foreign=0
[universe_filters] bulk market data 캐시 저장 — 0종목 (20260506)
[universe_filters] 속성 필터: 0/17 통과 (KIND 사전 제외 0 + 속성 탈락 17)
[orchestrator] universe 비어있음 — 파이프라인 스킵
```

## 현재 코드 상태

### `closing_bet_system/collectors/universe_filters.py`
- 라인 168-239: `_fetch_market_data_bulk(today_str)` — KOSPI/KOSDAQ 시장 단위 호출
  - 라인 197: `krx.get_market_cap_by_ticker(today_str, market=market)` ← 빈 응답
  - 라인 210: `krx.get_market_ohlcv_by_ticker(today_str, market=market)` ← 빈 응답
  - 라인 235: `_market_data_cache[today_str] = result` (빈 dict 저장)
- 라인 245-271: `_fetch_52w_high(ticker, today)` — 종목별 by_date (정상 작동)
- 라인 274-301: `_fetch_avg_value_20d(ticker, today)` — 종목별 by_date (정상 작동)
- 라인 360: `apply_attribute_filters` 가 `_fetch_market_data_bulk(today_str)` 호출 (tickers 전달 X)
- 라인 429: `apply_liquidity_filters` 도 동일

### 검증된 정상 호출 (변경 안 함)
- `_safe_float(value)` 라인 104-119 — pandas NaN 가드
- `_load_filter_config()` 라인 125-154 — settings.yaml stock_filter/liquidity 로드
- `reset_cache()` 라인 92-98 — 테스트용

### 기존 단위 테스트 (회귀 보호 대상)
- `scripts/test_closing_bet_unit_2_9a.py` (UV2 18건)
- `scripts/test_closing_bet_unit_2_9b.py` (UF 15건) — `_build_fake_krx` 패턴
- `scripts/test_closing_bet_unit_2_2b_1.py` (KN 15건)
- `scripts/test_closing_bet_unit_2_2b_2.py` (KI 10건)

## 핵심 스니펫

### `_fetch_market_data_bulk` 빈 결과 패턴 (라인 235)
```python
_market_data_cache[today_str] = result  # 빈 dict라도 캐시 → 같은 거래일 재호출 안 함
```
→ **결과**: 캐시 hit 후 모든 종목 `data_not_found`

### `apply_attribute_filters` 호출 (라인 360)
```python
market_data = _fetch_market_data_bulk(today_str)
# 본 단위에서 변경: market_data = _fetch_market_data_bulk(today_str, tickers=tickers)
```

### KIND severity 사전 제외 (라인 340-356)
```python
if severity_map:
    survivors: list[str] = []
    for t in tickers:
        sev = int(severity_map.get(t, 0) or 0)
        if sev >= SEVERITY_EXCLUDE_THRESHOLD:
            rejected[t] = f"kind_severity_{sev}"
        else:
            survivors.append(t)
    tickers = survivors
# 본 단위에서: 이 시점의 survivors 가 폴백 후보가 됨
```

## 영향 범위
- **수정**: `universe_filters.py` 모듈 (1 파일)
- **호출자**: `universe_provider_v2.get_universe_v2_filtered()` 라인 446 (변경 없음)
- **간접**: `main_orchestrator.run_daily_pipeline` (변경 없음)

## 과거 버그 / 교훈
- 2026-05-04 단위 2-9a 통합 검증: 야간 KRX 차단으로 0건 산출 → "야간 차단" 으로 잘못 진단했으나 실제는 항상 차단된 상태 (5/6 진단으로 확정)
- 2026-05-04 단위 2-9b: 보수적 하드 필터 — 데이터 없으면 진입 안 함 (옵션 A 결정의 근거)
- 2026-05-04 단위 2-2b-2 code-tester 검토: SEVERITY_EXCLUDE_THRESHOLD 단방향 import (순환 위험 없음)
- 2026-05-06: pykrx 1.2.8 업그레이드 시 numpy 2.x로 scipy 1.11.4 충돌 → 롤백

## 호환성 / 회귀 위험
- bulk 정상 시 `if not result and tickers and toggle:` 가드라 새 분기 진입 X
- `_fetch_market_data_bulk(today_str)` (tickers=None) 호출은 기존 동작 유지 (회귀 안전)
- 캐시 키 동일 (today_str), 폴백 결과는 별도 dict 격리
- KIND severity 사전 제외 로직 변경 없음

## 휴장일 처리
- 휴장일에는 폴백도 빈 결과 → graceful skip (`is_trading_day()` 가드 불필요, orchestrator 가 처리)

## 검증 환경
- venv: `/home/hatni/korean_stock_ai_trading/venv/bin/python`
- DB: `data/closing_bet.db` (schema_version, candidates, candidate_features, candidate_labels, flow_data_reliability, orderbook_snapshots)
- 봇 PID 확인: `ps aux | grep main.py | grep -v grep`
