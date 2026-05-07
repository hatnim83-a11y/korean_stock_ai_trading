# CONTEXT — 단위 2-9 리팩토링

## 변경 이유
- `_maybe_run_per_ticker_fallback` (universe_filters.py:422-564) 가 단위 2-9c~2-9f 누적으로 145줄 비대화
- 한 함수에 4개의 책임 혼재:
  1. 가드(bulk 비어있음/tickers/캐시/cfg/토글)
  2. 1순위 시총 보강 (KIS market-cap top N)
  3. 2순위 시총 보강 (volume_rank lstn_stcn × stck_prpr)
  4. OHLCV 보강 (pykrx by_date 종목별 폴백)
- Phase 2 자동매매 진입 전 정리 — 자동매매 코드 추가 시 fallback 함수 추가 비대화 회귀 위험

## 현재 코드 상태 (universe_filters.py)

### 함수 위치
- `_maybe_run_per_ticker_fallback`: line 422-564 (145 raw lines, 약 126 유효 라인)
- `_fetch_per_ticker_today_data`: line 288-330 (헬퍼 3 내부에서 호출)
- `_fetch_market_data_bulk`: line 332-420 (메인 함수의 유일한 caller)

### 호출 컨텍스트
- 단일 호출자: `_fetch_market_data_bulk` 내부, line 354 부근의 `if bulk_result: return` 가드 외부에서 호출
- 호출 파라미터: `(today_str, tickers, krx, bulk_result)`
- 결과 저장 위치: 모듈 전역 `_per_ticker_market_cache[today_str]` (별도 격리, bulk 캐시 오염 방지)

### 핵심 모듈 상수 (universe_filters.py:51-122)
```python
DEFAULT_FALLBACK_PER_TICKER_ENABLED = True
DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = False  # 단위 2-9e — settings 로드 실패 시 안전 default
DEFAULT_KIS_MARKET_CAP_TOP_N = 200
MAX_FALLBACK_TICKERS = 100
FALLBACK_RATE_LIMIT_SEC = 0.05
```

### 시총 보강 우선순위 (메인 함수 docstring)
1. **1순위 (단위 2-9d/2-9e)**: KIS market-cap top 200 — `fallback_include_market_cap=True` 시
2. **2순위 (단위 2-9f)**: volume_rank lstn_stcn × stck_prpr — 1순위 미매치 종목 대상, **2순위도 `fallback_include_market_cap=True` 블록 안에 중첩**
3. **3순위**: 미매치 → market_cap None 유지 → apply_attribute_filters 옵션 A 보수 탈락

### 핵심 스니펫 (현재 구조)

```python
def _maybe_run_per_ticker_fallback(today_str, tickers, krx, bulk_result):
    # 가드 (bulk 비어있음/tickers/캐시 히트)
    if bulk_result: return
    if not tickers: return
    if today_str in _per_ticker_market_cache: return
    
    # cfg 로드 + default 폴백
    cfg = {default...}
    try: cfg = _load_filter_config()
    except ...
    if not cfg.get("fallback_per_ticker_enabled"): return
    
    # candidates (중복제거 + 상한)
    seen, candidates = set(), []
    for t in tickers: ...
    
    fallback = {}
    
    # 1순위 시총 보강 — KIS market-cap top 200 (단위 2-9d)
    if cfg.get("fallback_include_market_cap"):
        try:
            from ... import get_kis_market_provider
            mcap_top_n = cfg.get(...)
            mcap_data = get_kis_market_provider().get_top_market_cap_data(top_n=mcap_top_n)
            for ticker in candidates:
                if ticker in mcap_data:
                    fallback[ticker] = dict(mcap_data[ticker])
        except: ...
        
        # 2순위 — volume_rank 자체 계산 (단위 2-9f, 1순위 블록 안에 중첩)
        unmatched = [t for t in candidates if t not in fallback]
        if unmatched:
            try:
                from ... import get_kis_market_provider
                vol_data = get_kis_market_provider().get_top_value_data(top_n=30)
                for ticker in unmatched:
                    if ... market_cap in entry:
                        fallback[ticker] = dict(entry)
            except: ...
    
    # OHLCV 보강 — pykrx 종목별 by_date (단위 2-9c)
    success = 0
    start = time.monotonic()
    for ticker in candidates:
        data = _fetch_per_ticker_today_data(ticker, today_str, krx)
        if data:
            fallback.setdefault(ticker, {}).update(data)
            success += 1
        time.sleep(FALLBACK_RATE_LIMIT_SEC)
    elapsed = time.monotonic() - start
    
    _per_ticker_market_cache[today_str] = fallback
    logger.info(f"... {success}/{n_input}건 성공, {elapsed:.2f}초")
```

## 과거 버그 (반드시 보존해야 할 동작)
- **2-9e 안전 default**: `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = False` — settings 로드 실패 시 의도와 반대 활성화 위험 차단
- **2-9f 중첩 의도**: 2순위 블록은 `fallback_include_market_cap=True` 블록 **안에** 위치 → 토글 OFF 시 두 단계 모두 스킵 (헬퍼 분리 후에도 동일 의미 유지)
- **fallback dict 순서**: 시총 보강이 OHLCV 보강 **이전**에 실행 → OHLCV가 시총 위에 누적 (`setdefault().update()`)
- **try/except 격리**: 1순위 실패 → 2순위 시도, 2순위 실패 → 옵션 A 보수 탈락 (각 단계 독립 격리)
- **rate limit sleep**: pykrx OHLCV 보강 루프에서만 sleep, 시총 보강은 1회 호출이라 sleep 불필요

## 영향 범위
- **호출자**: `_fetch_market_data_bulk` 단 1곳 (universe_filters.py:354)
- **외부 모듈 영향**: 없음 — 헬퍼 3개 모두 모듈 private (`_` prefix)
- **운영 동작**: 변화 없음 (순수 추출 리팩토링)
- **DB 스키마**: 변화 없음
- **settings.yaml**: 변화 없음

## 리팩토링 후 시그니처 (확정)

```python
def _enrich_market_cap_from_kis_top(
    candidates: list[str],
    fallback: dict[str, dict],
    mcap_top_n: int,
    n_input: int,
) -> int:
    """단위 2-9d/2-9e — KIS market-cap top N 매치하여 fallback 채움. matched 건수 반환.
    
    fallback dict mutate (in-place). 예외는 caller 가 try/except 격리.
    """

def _enrich_market_cap_from_volume_rank(
    candidates: list[str],
    fallback: dict[str, dict],
) -> int:
    """단위 2-9f — 1순위 미매치 종목에 volume_rank lstn_stcn × stck_prpr 매치. matched 건수 반환.
    
    fallback dict mutate. 1순위 후 호출 시 `unmatched` 자동 산출 (fallback 미진입 candidates).
    """

def _enrich_ohlcv_per_ticker(
    candidates: list[str],
    fallback: dict[str, dict],
    today_str: str,
    krx,
) -> tuple[int, float]:
    """단위 2-9c — pykrx 종목별 by_date 폴백으로 OHLCV 보강. (success, elapsed_sec) 반환.
    
    시총 보강 데이터 위에 setdefault().update() 누적. rate limit sleep 내장.
    """
```
