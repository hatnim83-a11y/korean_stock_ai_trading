# CONTEXT — 단위 2-9d · KIS Open API ranking 대체

## 변경 이유
단위 2-9c (commit d3755e9) 배포 후 통합 단발 검증에서 universe = 0건 (옵션 A 보수 탈락). 본질 해결을 위해 KIS Open API의 ranking 4종으로 pykrx bulk 완전 대체. 시총 보강도 KIS market-cap 1회 호출로 옵션 B 자연 활성화.

## 사전 조사 결과 (KIS Open API 매뉴얼)

### 출처별 endpoint 확정
| 출처 | TR_ID | URL Path |
|------|-------|----------|
| 거래대금 상위 | `FHPST01710000` | `/uapi/domestic-stock/v1/quotations/volume-rank` |
| 등락률 상위 | `FHPST01700000` | `/uapi/domestic-stock/v1/ranking/fluctuation` |
| 외국인 순매수 상위 | `FHPTJ04400000` | `/uapi/domestic-stock/v1/quotations/foreign-institution-total` |
| 시가총액 상위 | `FHPST01740000` | `/uapi/domestic-stock/v1/ranking/market-cap` |

### 응답 컬럼 (volume_rank 검증, 다른 ranking 동일 추정)
- `mksc_shrn_iscd` — 단축종목코드 (6자리)
- `hts_kor_isnm` — 종목명 (HTS 한글)
- `stck_prpr` — 현재가
- `prdy_ctrt` — 등락률 (%)
- `acml_vol` — 누적거래량
- `acml_tr_pbmn` — 누적거래대금
- `stck_avls` — 시가총액 (market-cap ranking 추정)

### Sources
- [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api)
- [KIS Developers 공식 포탈](https://apiportal.koreainvestment.com/apiservice-apiservice)

## 현재 코드 상태

### `closing_bet_system/collectors/universe_provider_v2.py`
- 라인 296-324: `_fetch_top_value_codes(today_str, top_n=30)` — pykrx `get_market_ohlcv_by_ticker` 사용 (KRX 차단)
- 라인 327-357: `_fetch_top_change_codes(today_str, top_n=30)` — pykrx `get_market_price_change_by_ticker` (KRX 차단)
- 라인 360-397: `_fetch_top_foreign_buy_codes(today_str, top_n=30)` — pykrx `get_market_net_purchases_of_equities_by_ticker` (KRX 차단)
- 라인 240-256: `_safe_call` 헬퍼 (try/except 격리)

### `closing_bet_system/collectors/universe_filters.py` (단위 2-9c 후)
- 라인 168-277: `_fetch_market_data_bulk(today_str, tickers=None)` — pykrx bulk + 폴백 분기
- 라인 280-322: `_fetch_per_ticker_today_data(ticker, today_str, krx)` — 폴백 헬퍼 (시총 안 채움 — 옵션 A)
- 라인 360-403: `_maybe_run_per_ticker_fallback` — 폴백 진입 가드
- 시총 보강 위치: 폴백 진입 직전 또는 헬퍼 내부에 KIS market-cap 호출 추가 가능

### `modules/stock_screener/kis_api.py` (재사용 패턴)
- 라인 76-78: `_shared_token` / `_shared_token_expired_at` 클래스 변수
- 라인 112-115: 도메인 분기 (모의 `openapivts:29443` / 실전 `openapi:9443`)
- 라인 129-131: `_rate_limit()` (`settings.KIS_API_DELAY=0.11초`)
- 라인 133-180: `get_access_token()` — 토큰 발급 + 1시간 전 갱신 + 1분 발급 제한
- 라인 422+: `get_current_price` — `output[0]` 단건 응답 파싱 (`stck_prpr` 등)
- 라인 320, 470: `rt_cd != "0"` 체크 + warning + None 반환
- 라인 37-54: `_safe_int(value, default=0)` / `_safe_float`

### `closing_bet_system/infra/kis_client.py`
- 라인 51-59: `get_kis_api()` 싱글톤 헬퍼 (race 방지 lock)
- → 신규 `kis_market_provider.py` 도 동일 패턴 적용

### `closing_bet_system/config/settings.yaml`
- 라인 122-123: `kis: use_mock: true` (Phase 1 모의투자)
- 단위 2-9c 추가: `data_source.fallback_per_ticker_enabled: true`
- 단위 2-9d 추가 예정: `data_source.use_kis_ranking`/`fallback_include_market_cap`/`kis_top_n`/`kis_market_cap_top_n`

## 영향 범위
- **신규**: `kis_market_provider.py` (1 파일)
- **수정**: `universe_provider_v2.py` (출처 2~4 본문), `universe_filters.py` (시총 보강), `settings.yaml`
- **호출자**: `main_orchestrator.run_daily_pipeline` (변경 없음 — 토글 OFF 시 회귀 안전)
- **간접**: KIS API 호출량 +51회/일 (현 1,200 → 1,251)

## 호출 흐름 (단위 2-9d 적용 후)

```
main_orchestrator.run_daily_pipeline (15:10 KST)
  └─> universe_provider_v2.get_universe_v2_filtered()
        ├─> get_universe_v2()
        │     ├─> _fetch_theme_codes_v2()              # 출처 1 (스윙 테마, 변경 없음)
        │     ├─> _fetch_top_value_codes()              # 출처 2 → KIS volume-rank
        │     ├─> _fetch_top_change_codes()             # 출처 3 → KIS fluctuation
        │     └─> _fetch_top_foreign_buy_codes()        # 출처 4 → KIS foreign-institution-total
        ├─> apply_attribute_filters(severity_map=...)
        │     └─> _fetch_market_data_bulk(today, tickers=...)
        │           ├─> pykrx bulk (빈 응답)
        │           ├─> [옵션 B 활성 시] kis_market_provider.get_top_market_cap_data()  # 시총 보강
        │           └─> _maybe_run_per_ticker_fallback (옵션 A 보수, 시총 누락 종목)
        └─> apply_liquidity_filters
```

## 과거 버그 / 교훈 (단위 2-9c)
- 데드락: `_merge_with_fallback_cache`에 lock 재획득 시 non-reentrant 데드락 → lock 제거 (CPython GIL atomic 활용)
- 옵션 A 정합 누락: `market_cap=None` 시 시총 검증 스킵 → `data_not_found` 명시 탈락 보강
- 토글 예외 시 default 명시 체크 (코더 검토 심각 #2)

## 호환성 / 회귀 위험
- 토글 false 시 pykrx 경로 그대로 → 단위 2-9c 동작 유지
- KIS API 응답 컬럼명 추정 — 실 호출 시 KeyError 가능 → try/except + 컬럼 fallback 필수
- KIS API rate_limit 0.11초 + 4회 호출 = 약 0.44초 추가 (현 호출량과 무관)

## 검증 환경
- venv: `/home/hatni/korean_stock_ai_trading/venv/bin/python`
- DB: `data/closing_bet.db` + `data/trading.db` (스윙 read-only)
- KIS 도메인: `is_mock=true` 모의 / 실전 prefix 분기 (kis_api.py 112-115)
- 봇 PID 확인: `ps aux | grep main.py | grep -v grep`
