# CONTEXT — 단위 2-9e · 시총 보강 정규화 (옵션 A)

## 변경 이유
단위 2-9d 후 universe 출처 2~4 KIS Open API 라우팅 완성 (theme=17 / top_value=25 / top_foreign=21 = **63종목**), 그러나 시총 보강 OFF로 옵션 A 보수 탈락 (`universe = 0건`). 본 단위는 KIS `ranking/market-cap` 응답의 `stck_avls` 컬럼 단위(억원)를 정규화해 시총 보강을 활성화한다.

## 사전 조사 결과 (2026-05-06 본 세션)

### 핵심: stck_avls 단위 = 억원 확정
- `005930 stck_avls=15,551,101` × 100,000,000 = `1,555,110,100,000,000원` (1,555조원)
- 검증 1: `005930 stck_prpr=266,000원 × lstn_stcn=5,846,278,608주 = 1,554,909,948,728,000원` (1,555조원)
- 검증 2: `inquire-price market_cap = 1,555,110,100,000,000원` (1,555조원)
- 3값 정확 일치 → `stck_avls` 단위 = 억원, × 1억 정규화 = 원 단위

### 종목별 검증 (5종목)
| 종목 | stck_avls × 1억 (계산) | inquire-price market_cap | 일치 |
|------|------------------------|--------------------------|------|
| 005930 삼성전자 | 1,555,110,100,000,000원 | 1,555,110,100,000,000원 | ✅ |
| 000660 SK하이닉스 | 1,141,036,500,000,000원 | 1,141,036,500,000,000원 | ✅ |
| 005380 현대차 | 112,616,800,000,000원 | 112,616,800,000,000원 | ✅ |
| 035420 NAVER | (top10 외, 별도 호출) | 32,625,300,000,000원 | (참고) |
| 035720 카카오 | (top10 외, 별도 호출) | 20,510,900,000,000원 | (참고) |

### KIS ranking/market-cap 응답 11개 키
`mksc_shrn_iscd`, `data_rank`, `hts_kor_isnm`, `stck_prpr`, `prdy_vrss`, `prdy_vrss_sign`, `prdy_ctrt`, `acml_vol`, `lstn_stcn`(발행주식수), `stck_avls`(시총-억원), `mrkt_whol_avls_rlim`(시장전체비중%)

### KIS volume-rank 응답 시총 키 부재
- 거래대금/등락률/외국인 ranking 응답에는 시총 직접 컬럼 **없음**
- 대신 `lstn_stcn` (발행주식수) + `stck_prpr` (현재가) 둘 다 존재 → 옵션 C 자체 계산 가능 (단위 2-9f 후속 검토)

### CONTEXT.md (단위 2-9d) 추정 오류 정정
- 기존 추정: 005930 시총 ≈ 530조원 (액면분할 후 가격 가정)
- 사실: `stck_prpr=266,000원`은 액면분할 전 상태로 표시 → 시총 ≈ 1,555조원
- 단위 미스매치가 아니라 **추정값 자체가 틀림**. 실제 KIS 응답은 일관됨

## 현재 코드 상태

### `closing_bet_system/collectors/kis_market_provider.py`
- 라인 42: `from modules.stock_screener.kis_api import KISApi, _safe_int, _safe_float` — modules `_safe_float` 사용 (default=0.0 시그니처 지원)
- 라인 67: `_FIELD_MARKET_CAP = "stck_avls"` (응답 컬럼명, **단위 = 억원** — 단위 2-9e 확정)
- 라인 222-263: `get_top_market_cap_data(top_n=200, market="ALL") -> dict[str, dict]`
  - 라인 235: `mcap = _safe_float(item.get(_FIELD_MARKET_CAP), default=0.0)` (raw, 억원)
  - 라인 239-244: 인라인 주석 "단위 미확정" — **본 단위 정정 대상** ("억원 확정, × 1억 정규화" 표기)
  - 라인 246: `entry["market_cap"] = mcap` ← **본 단위 수정 대상** (`int(round(mcap * _STCK_AVLS_UNIT_TO_WON))`)
  - 라인 247-253: 진단 로그 — **본 단위 정정 대상** ("min_market_cap=5,000억" 오기 → "500억" + 단위 확정 표기)

### `closing_bet_system/collectors/universe_filters.py`
- 라인 54: `DEFAULT_MIN_MARKET_CAP = 50_000_000_000` (500억) ← 사용자 메시지/PLAN 초안 5,000억 표기는 오기 정정
- 라인 86: `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = True` ← **본 단위 False 로 변경** (settings 로드 실패 시 cfg fallback 안전망 정합)
- 라인 168-277: `_fetch_market_data_bulk(today_str, tickers=None)`
- 라인 280-322: `_fetch_per_ticker_today_data(ticker, today_str, krx)` (옵션 A 보수: 시총 안 채움)
- 라인 360-403: `_maybe_run_per_ticker_fallback` (`fallback_include_market_cap=true` 시 KIS market-cap 호출 분기, **bulk 빈 응답 시에만 진입 — pykrx 정상 복구 시 자동 OFF 의존성 존재**)
- 라인 592-595: `market_cap is None` → `data_not_found` 보수 탈락 분기 (단위 2-9c 추가)
- 시총 비교 임계: `min_market_cap=50_000_000_000` (500억) — `cfg["min_market_cap"]` 로 settings.yaml:97 우선

### `closing_bet_system/config/settings.yaml`
- 라인 ~55-65: `data_source` 섹션
  - `fallback_per_ticker_enabled: true` (단위 2-9c)
  - `use_kis_ranking: true` (단위 2-9d)
  - `fallback_include_market_cap: false` (단위 2-9d, **본 단위 true 전환**)
  - `kis_top_n: 30` / `kis_market_cap_top_n: 200`

## 영향 범위
- **수정**: `kis_market_provider.py` (라인 246 + 진단 로그) / `settings.yaml` (1줄 토글)
- **신규**: `scripts/test_closing_bet_unit_2_9e.py` (단위 테스트)
- **호출자**: `universe_provider_v2.get_universe_v2_filtered` (변경 없음, 토글 분기)
- **간접**: KIS API 호출량 +1회/일 (단위 2-9d 51회 → 52회)

## 호출 흐름 (단위 2-9e 적용 후)
```
main_orchestrator.run_daily_pipeline (15:10 KST)
  └─> universe_provider_v2.get_universe_v2_filtered()
        ├─> get_universe_v2()  # 출처 1~4 (변경 없음)
        ├─> apply_attribute_filters(severity_map=...)
        │     └─> _fetch_market_data_bulk(today, tickers=...)
        │           ├─> pykrx bulk (빈 응답)
        │           ├─> [✅ 단위 2-9e 활성] kis_market_provider.get_top_market_cap_data()  # 시총 보강
        │           │     ├─> stck_avls × 100,000,000 = 원 단위 시총 (정규화)
        │           │     └─> return {ticker: {market_cap, close, change_rate}}
        │           └─> _maybe_run_per_ticker_fallback (옵션 A 보수, 상위 200 외 종목)
        └─> apply_liquidity_filters
```

## 과거 버그 / 교훈 (단위 2-9c~2-9d)
- 데드락: `_merge_with_fallback_cache` 의 lock 재획득 시 non-reentrant 데드락 → lock 제거
- 옵션 A 정합 누락: `market_cap=None` 시 시총 검증 스킵 → `data_not_found` 명시 탈락 보강
- 코더 검토 심각 #1 (cfg NameError): settings 로드 실패 시 default 폴백 명시
- 코더 검토 심각 #2 (direction 인자): rank_sort 분기 적용 누락
- 코더 검토 심각 #3 (`_FIELD_CHANGE_RATE` 상수): 문자열 리터럴 제거
- **본 단위 신규 위험**: 단위 곱셈 누락 → 5,000억 비교 시 모든 종목 탈락. MC-6 단위 테스트로 사전 차단

## 호환성 / 회귀 위험
- 토글 false 시 옵션 A 보수 탈락 그대로 (단위 2-9d 동작 유지)
- KIS market-cap ranking 응답 추가 호출 1회 — 약 0.11s rate_limit + 0.5s 응답 = 0.6s 추가
- **pykrx bulk 게이트 의존성**: 시총 보강은 `_maybe_run_per_ticker_fallback` 진입 시(bulk 빈 응답)만 트리거. KRX 정책이 풀려 bulk 정상 복구되면 시총 보강 자동 OFF → universe 회귀 가능. 단위 2-9f 영역에서 분기 자체 변경 검토. 자연 트리거에서 `[universe_filters] 시총 보강` 로그 미발생 시 bulk 복구 의심
- **DEFAULT 값 안전망 역동작 (Step 2 변경으로 봉쇄)**: `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=True` (이전) ↔ `settings.yaml false` 였음 → settings 로드 실패 시 cfg fallback 분기에서 의도치 않은 활성화 위험. Step 2 에서 default False 로 정합
- **부동소수점 절단 위험**: `_safe_float` 가 float 반환 후 곱셈 → IEEE 754 정밀도 한계로 경계값(500억 근처) 절단 누락. `int(round(...))` 적용으로 봉쇄
- **ETF/우선주 universe 진입 위험**: ranking/market-cap 응답에 ETF(069500)/우선주(005935) 포함, 현재 universe_filters 명시적 차단 부재 (CONTEXT.md 단위 2-9d 의 "자연 제거" 진술과 코드 불일치). 사용자 결정으로 단위 2-9f 영역으로 미룸. Phase 1 알림형이라 자동매수 위험 0

## 사전 조사 산출물
- `scripts/probe_kis_unit_2_9e.py` — KIS market-cap/volume-rank/inquire-price 단발 호출 + 단위 분석 출력
- 보존 권장: 향후 KIS API 응답 변경 시 빠른 재검증 가능
- 또는 단위 2-9e 완료 후 단위 테스트 MC-1 ~ MC-7로 대체 가능 (선택)

## 검증 환경
- venv: `/home/hatni/korean_stock_ai_trading/venv/bin/python`
- DB: `data/closing_bet.db` + `data/trading.db` (스윙 read-only)
- KIS 도메인: `is_mock=false` 실전 (단위 2-9d 전환)
- 봇 PID 확인: `ps aux | grep main.py | grep -v grep`

## 사각지대 (단위 2-9f 후속 후보)
**옵션 C — `lstn_stcn × stck_prpr` 자체 계산**:
- ranking 4종 모든 응답에 두 키 존재 → 발행주식수 × 현재가 = 시총 산출 가능
- 상위 200 한도 없음 — 등락률/외국인 ranking 의 중소형주도 시총 보강 가능
- **단점**: 우선주(별도 발행) / ETF(NAV 기반) 분기 처리 필요
- **트리거**: 단위 2-9e 활성 후 1주 관찰 — universe 5건 미달 빈발 시 진입

## 단위 2-9e 완료 후 다음 작업
1. 5/7 (또는 다음 영업일) 자연 트리거 검증
2. 1주 실전 관찰 (universe 평균/최저/표준편차)
3. 단위 2-9c/2-9d/2-9e active → completed 일괄 아카이브
4. universe < 5건 빈발 시 단위 2-9f 진입 검토
