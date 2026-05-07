# CONTEXT — 단위 2-9f · ETF/우선주 차단 + 자체 시총 계산 (사전 리뷰 반영)

## 변경 이유
1. **PRD 16-3 정합도 향상** — universe에 ETF/우선주 진입은 종가베팅 시그니처와 무관. 알림 노이즈 + 자동매매 시 본주 가격 괴리
2. **시총 보강 매치율 향상** — KIS market-cap top 200 한도 우회 (5/7 매치율 18/68 → 60+/68 목표)
3. **시총 일관성 옵션** — pykrx vs KIS 출처 차이 토글로 운영자 선택
4. **5/7 자연 트리거 트리거 조건 충족** — 우선주 005935 universe 진입 발견

## 사전 리뷰 결과 (Plan + strategy-coder 병렬, 2026-05-07)

### P0 블로커 2건 (구현 전 해결 필수)

**B1. ETF prefix 룰의 false positive + 메이저 ETF 누락 (커버리지 22%)**
- 누락 prefix 38건: 102/122/233/252/360/419/466 등
- 누락 메이저 ETF: 102110 TIGER 200 / 122630 KODEX 레버리지 / 233740 KODEX 코스닥150 레버리지 / 252670 KODEX 200선물인버스2X
- prefix 화이트리스트의 false positive (KOSDAQ 보통주 진입):
  - 069080 웹젠 (게임주)
  - 091990 셀트리온헬스케어 (구 KOSDAQ 시총 상위)
  - 117730 티로보틱스
  - 292340 모코
- **해결책 채택**: pykrx `get_etf_ticker_list(today_str)` 동적 조회 (1차) + 정적 화이트리스트 폴백 (2차, pykrx 실패 시)

**B2. 우선주 끝자리 5/7/9 룰의 false positive (KOSDAQ 보통주 + SPAC)**
- KOSDAQ 보통주 끝자리 5/7/9 케이스 다수 존재
- SPAC 종목 (예: 478545 같은 형태) — 합병 후 코드 변경 가능, 그 사이 ranking 진입 가능
- 재상장 종목 끝자리 0 외 다양
- **해결책 채택**: 종목명 "우/우B/우C/우K" 접미사 + 끝자리 5/7/9 **AND 조건**으로만 차단
  - 종목명 부재 시 보수적 통과 (false positive 회피)
  - 끝자리만 만족하고 종목명 미충족 → 통과 (일반 보통주 false positive 회피)
  - 종목명만 만족하고 끝자리 미충족 → 통과 (이론상 불가하지만 보수)

### 심각 11건 (요약)

**S3. IEEE 754 정밀도 보호 필요 (단위 2-9e 패턴 일관)**:
- `_safe_int(lstn_stcn) × _safe_int(stck_prpr)` = 정수 × 정수 = 정수 (정밀도 손실 없음)
- `_safe_float` 사용 절대 금지 → IEEE 754 mantissa 53비트 한계 (9 × 10^15)에 시총 상위 종목 진입 위험

**M1. `get_top_value_data` 옵션 A (메서드 분리) 채택**:
- 단위 2-9d `get_top_market_cap_data()` 명명 일관성 (`_data` 접미사 = dict 반환)
- 기존 `get_top_value_codes()` 유지 (universe_v2.py 호환)

**M2. `_maybe_run_per_ticker_fallback` helper 3개 분리**:
- 현재 108줄 → 단위 2-9f 추가 시 130~150줄 비대화 위험
- 분리 위치: `_enrich_market_cap_from_kis_top` (단위 2-9d/2-9e) / `_enrich_market_cap_from_volume_rank` (단위 2-9f) / `_enrich_ohlcv_per_ticker` (단위 2-9c)

**M3. `kis_market_cap_priority` 토글 의미 명확화 (해석 A 채택)**:
- **해석 A**: pykrx bulk 빈 응답 시에만 시총 보강 진입 (현 동작 유지). 토글 ON 시 pykrx 정상 시에도 시총 컬럼만 KIS로 후처리 덮어쓰기
- 해석 B (기각): pykrx bulk 정상 자체를 무시하고 항상 KIS ranking 우선 호출

**시총 자체 계산 단위 정합성**:
- `stck_avls × 1억`: 자기주식 차감 후 (KRX 공식 일관)
- `lstn_stcn × stck_prpr`: 단순 발행주식수 × 현재가
- 약 0.01~5% 차이 (자기주식 영향 큰 종목 — SK하이닉스, 셀트리온 등)
- **우선순위**: stck_avls × 1억 (1순위, 정확) → lstn_stcn × stck_prpr (2순위, top 200 한도 우회)

**rejection_reason 모듈 상수**:
- `REJECTION_REASON_IS_ETF = "is_etf"`
- `REJECTION_REASON_IS_PREF_STOCK = "is_pref_stock"`
- 기존 reason과 충돌 없음 (`kind_severity_*`, `data_not_found`, `market_cap_too_low`, `price_too_low`, `upper_limit`, `below_52w_high_drop`, `today_value_too_low`, `avg_value_20d_too_low`)

**점진 활성화 권장 (default 정책)**:
- `etf_block_enabled: true` (즉시, false positive pykrx 동적 조회로 회피)
- `pref_stock_block_enabled: false` (1주 관찰 후 활성화 — 사전 조사 종목명 분포 확인)
- `kis_market_cap_priority: false` (default false — pykrx 정상 시 KRX 공식 사용)
- `kis_div_cls_code: "0"` (Step 0 매뉴얼 검증 후 "1" 활성화)

**차단 분기 위치**:
- `apply_attribute_filters` 본문 `for ticker in tickers:` 루프 첫 단계
- KIND severity `if severity_map:` 블록 **밖** (severity_map 유무 무관 적용)
- ETF 차단 → 우선주 차단 순서 (ETF 매치 시 `is_etf` 우선 기록, first-rejection-only)

**로그 분해**:
- 기존: `[universe_filters] 속성 필터: {passed}/{original} 통과 (KIND 사전 제외 N + 속성 탈락 M)`
- 신규: `... (KIND {kind} + ETF {etf} + 우선주 {pref} + 속성 {attr} 탈락)`
- 첫 호출 1회 차단 list 진단 로그 (단위 2-9e 패턴 일관)

### 사전 조사 강제 항목 (Step 0)

1. **pykrx ETF 리스트 동적 조회 가능 여부** — 단위 2-9c에서 KRX bulk API 차단됐지만 `get_etf_ticker_list()`는 별도 엔드포인트일 가능성. 단발 검증 필수
2. **KIS API `FID_DIV_CLS_CODE` 매뉴얼 검증** — koreainvestment/open-trading-api 공식 GitHub WebFetch:
   - volume-rank: 코드 주석에서 `(1=보통주, 2=우선주)` 확인 완료
   - fluctuation: `fid_div_cls_code` 파라미터 존재? 의미 동일?
   - foreign-institution-total: `FID_DIV_CLS_CODE` 파라미터 존재?
3. **5/7 universe 18건 + KOSPI/KOSDAQ 거래대금 top 100 분포 실측** — `scripts/probe_etf_pref_distribution.py` 신규
4. **자기주식 비중 큰 종목 sample 단발 검증** — SK하이닉스/셀트리온/네이버 stck_avls × 1억 vs lstn_stcn × stck_prpr 차이 측정

## 현재 코드 상태 (수정 대상)

### universe_filters.py (속성 필터 — ETF/우선주 차단 분기 부재)
```python
# closing_bet_system/collectors/universe_filters.py:534~635 (apply_attribute_filters 본문)
def apply_attribute_filters(
    tickers: list[str],
    today_str: str,
    *,
    severity_map: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> list[FilterResult]:
    """PRD 4-1 속성 필터: 시총/가격/상한가/52주 고점."""
    cfg = cfg or _load_filter_config()
    # ... KIND severity 사전 제외 if severity_map: ...
    # ETF/우선주 차단 분기 없음 → 우선주 005935 universe 진입 가능
    # ... 시총/가격/상한가/52주 고점 필터 ...

# closing_bet_system/collectors/universe_filters.py:337~444 (_maybe_run_per_ticker_fallback)
# 108줄 — 단위 2-9c+2-9d+2-9e 누적. 본 단위 2-9f 추가 시 130~150줄 비대화 위험
```

### kis_market_provider.py (단위 2-9d-hotfix 직후 상태)
```python
# 모듈 상수 (61~71)
_FIELD_TICKER = "mksc_shrn_iscd"
_FIELD_TICKER_ALT = "stck_shrn_iscd"        # 단위 2-9d-hotfix
_TICKER_KEYS_DEFAULT = (_FIELD_TICKER, _FIELD_TICKER_ALT)
_FIELD_NAME = "hts_kor_isnm"
_FIELD_PRICE = "stck_prpr"
_FIELD_CHANGE_RATE = "prdy_ctrt"
_FIELD_VOLUME = "acml_vol"
_FIELD_VALUE = "acml_tr_pbmn"
_FIELD_MARKET_CAP = "stck_avls"
# _FIELD_LISTED_SHARES 미정의 → 신규 추가 필요

# get_top_value_codes — list[str] 반환 (lstn_stcn 미회수)
def get_top_value_codes(self, top_n=DEFAULT_TOP_N, market="ALL") -> list[str]:
    items = self._call_ranking(_PATH_VOLUME_RANK, _TR_VOLUME_RANK, params, source="top_value")
    return _filter_valid_tickers(items, top_n)

# get_top_market_cap_data — top 200 한도 + stck_avls × 1억 정규화 (단위 2-9e)
def get_top_market_cap_data(self, top_n=DEFAULT_MARKET_CAP_TOP_N, market="ALL") -> dict[str, dict]:
    # market_cap = int(round(stck_avls * 100_000_000))  # 단위 2-9e
```

### settings.yaml (ETF/우선주 토글 부재)
```yaml
stock_filter:
  min_market_cap: 50_000_000_000
  min_price: 1000
  upper_limit_threshold: 0.295
  drop_from_52w_high: 0.30
  # ETF/우선주 토글 없음 (단위 2-9f 추가)

data_source:
  fallback_per_ticker_enabled: true
  fallback_include_market_cap: true
  use_kis_ranking: true
  kis_market_cap_top_n: 200
  # kis_market_cap_priority 없음 (단위 2-9f 추가)
  # kis_div_cls_code 없음 (단위 2-9f 추가)
```

## 핵심 스니펫 (사전 리뷰 반영 수정안)

### 1. ETF 동적 조회 (universe_filters.py)
```python
import threading

_etf_ticker_cache: dict[str, frozenset[str]] = {}
_etf_ticker_cache_lock = threading.Lock()

def _get_etf_ticker_set(today_str: str) -> frozenset[str]:
    """pykrx ETF 종목 리스트 동적 조회 (일별 캐시).

    pykrx 실패 시 정적 화이트리스트 set 폴백 (Step 0 사전 합의).
    """
    cached = _etf_ticker_cache.get(today_str)
    if cached is not None:
        return cached
    with _etf_ticker_cache_lock:
        cached = _etf_ticker_cache.get(today_str)
        if cached is not None:
            return cached
        try:
            from pykrx import stock as krx
            etf_list = krx.get_etf_ticker_list(today_str)
            result = frozenset(t for t in etf_list if isinstance(t, str) and len(t) == 6)
            logger.info(f"[universe_filters] ETF 리스트 {len(result)}건 캐시 ({today_str})")
        except Exception as e:
            logger.warning(f"[universe_filters] pykrx ETF 리스트 호출 실패 → 정적 폴백: {e}")
            result = _ETF_STATIC_FALLBACK  # Step 0 사전 합의 set
        _etf_ticker_cache[today_str] = result
        return result

def _is_etf(ticker: str, today_str: str) -> bool:
    """ETF 종목코드 확인 (pykrx 동적 + 정적 폴백)."""
    if not ticker or not _TICKER_PATTERN.match(ticker):
        return False
    return ticker in _get_etf_ticker_set(today_str)
```

### 2. 우선주 차단 (AND 조건)
```python
_PREF_STOCK_LAST_DIGITS: frozenset[str] = frozenset({"5", "7", "9"})
_PREF_STOCK_NAME_SUFFIXES: tuple[str, ...] = ("우", "우B", "우C", "우K")

def _is_pref_stock(ticker: str, name: Optional[str]) -> bool:
    """우선주 차단: 끝자리 5/7/9 AND 종목명 '우/우B/우C/우K' 접미사.

    AND 조건으로 false positive 회피 (KOSDAQ 보통주 끝자리 5/7/9 케이스).
    종목명 부재 시 False (보수적).
    """
    if not ticker or not _TICKER_PATTERN.match(ticker):
        return False
    if ticker[-1] not in _PREF_STOCK_LAST_DIGITS:
        return False
    if not name:
        return False
    return name.endswith(_PREF_STOCK_NAME_SUFFIXES)
```

### 3. 자체 시총 계산 (kis_market_provider.py — 단위 2-9d/2-9e 패턴 일관)
```python
_FIELD_LISTED_SHARES = "lstn_stcn"   # 발행주식수 (단위 2-9f)

def _compute_market_cap_from_response(item: dict) -> Optional[int]:
    """KIS ranking 응답 item에서 lstn_stcn × stck_prpr = 시총(원).

    단위: lstn_stcn=주, stck_prpr=원, 곱=원. 정규화 불필요.
    _safe_int 둘 다 적용 → int × int = int (IEEE 754 정밀도 손실 없음).
    stck_avls × 1억 (단위 2-9e) 대비 자기주식 차감 미반영 → 0.01~5% 차이.
    """
    lstn_stcn = _safe_int(item.get(_FIELD_LISTED_SHARES), default=0)
    stck_prpr = _safe_int(item.get(_FIELD_PRICE), default=0)
    if lstn_stcn <= 0 or stck_prpr <= 0:
        return None
    return lstn_stcn * stck_prpr  # int × int = int
```

### 4. 차단 분기 (apply_attribute_filters 본문)
```python
# KIND severity 처리 후, 시총/가격 필터 전, for ticker in tickers: 루프 첫 단계
for ticker in tickers:
    name = name_lookup_map.get(ticker)  # KIS API 응답 종목명 (호출부에서 주입)

    if cfg.get("etf_block_enabled", _BLOCK_ETF_DEFAULT) and _is_etf(ticker, today_str):
        results.append(FilterResult(
            ticker=ticker, passed=False, reason=REJECTION_REASON_IS_ETF
        ))
        etf_rejected_count += 1
        continue
    if cfg.get("pref_stock_block_enabled", _BLOCK_PREF_STOCK_DEFAULT) and _is_pref_stock(ticker, name):
        results.append(FilterResult(
            ticker=ticker, passed=False, reason=REJECTION_REASON_IS_PREF_STOCK
        ))
        pref_rejected_count += 1
        continue
    # ... 기존 시총/가격/상한가/52주 고점 ...

# 첫 호출 1회 진단 로그
if etf_rejected_count + pref_rejected_count > 0:
    logger.info(
        f"[universe_filters] ETF 차단 list: {etf_rejected_list[:10]} (총 {etf_rejected_count}건) / "
        f"우선주 차단 list: {pref_rejected_list[:10]} (총 {pref_rejected_count}건)"
    )
```

### 5. 시총 보강 helper 분리 (M2)
```python
def _enrich_market_cap_from_kis_top(candidates: list[str], cfg: dict) -> dict[str, dict]:
    """단위 2-9d/2-9e — KIS market-cap top 200 매치 (1순위, stck_avls × 1억)."""
    # 기존 _maybe_run_per_ticker_fallback L399~423 추출
    ...

def _enrich_market_cap_from_volume_rank(candidates: list[str], cfg: dict) -> dict[str, dict]:
    """단위 2-9f — KIS volume-rank 자체 시총 계산 (2순위, lstn_stcn × stck_prpr).

    1순위 미매치 종목 대상으로만 호출 (top 200 한도 우회용).
    """
    ...

def _enrich_ohlcv_per_ticker(candidates: list[str], today_str: str, krx) -> dict[str, dict]:
    """단위 2-9c — pykrx 종목별 by_date 폴백 (close/change_rate/today_value 채움)."""
    # 기존 _maybe_run_per_ticker_fallback L425~437 추출
    ...

def _maybe_run_per_ticker_fallback(...) -> ...:
    """폴백 분기 골격 (~50줄). cfg 로드 + helper 우선순위 호출 + dict merge + 캐시 저장."""
    cfg = ...
    if not cfg.get("fallback_per_ticker_enabled", DEFAULT_FALLBACK_PER_TICKER_ENABLED):
        return
    # 1순위 KIS market-cap (단위 2-9d/2-9e)
    mcap_kis_top = _enrich_market_cap_from_kis_top(candidates, cfg)
    # 2순위 KIS volume-rank (단위 2-9f)
    unmatched = [c for c in candidates if c not in mcap_kis_top]
    mcap_volume_rank = _enrich_market_cap_from_volume_rank(unmatched, cfg)
    # OHLCV (단위 2-9c)
    ohlcv = _enrich_ohlcv_per_ticker(candidates, today_str, krx)
    # merge (시총 1순위 > 2순위, OHLCV 별도)
    merged = ...
    return merged
```

## Step 0 사전 조사 결과 (2026-05-07 KST 19:50 완료)

### 0.1 pykrx ETF 동적 조회 — ❌ **사용 불가 확정**
- `pykrx.stock.get_etf_ticker_list("20260507")` → `KeyError: '시장'` (0.40초 만에 실패)
- 단위 2-9c와 동일한 KRX API 정책 변경 영향
- **결정**: ETF 차단을 정적 set이 아닌 **종목명 brand prefix 매칭** 방식으로 변경 (~800개 종목코드 set 유지보수 부담 회피)
- probe 보존: `scripts/probe_pykrx_etf_list.py`

### 0.2 KIS API `FID_DIV_CLS_CODE` 검증

| Ranking | TR_ID | div_cls=0 (전체) 끝자리 분포 | div_cls=1 (보통주) 끝자리 분포 | 우선주 차단 | ETF 차단 |
|---------|-------|---|---|:-:|:-:|
| **volume_rank** | FHPST01710000 | 0:29건 / **5:1건 (005935)** + ETF 122630/069500 진입 | **0:30건** + ETF 그대로 진입 | ✅ | ❌ |
| **fluctuation** | FHPST01700000 | 0:30건 (5/7 시점 우선주 0건) | 0:30건 (응답 동일) | 검증 불가 | ❌ |
| **foreign_total** | FHPTJ04400000 | 0:30건 (응답 변화) | 0:30건 (응답 변화 — 지원 추정) | 검증 불가 | ❌ |

**핵심 발견**:
1. **volume_rank `FID_DIV_CLS_CODE=1`** → 우선주 source-level 차단 ✅ (5/7 005935 빠짐 직접 검증)
2. **ETF는 native filter로 차단 안 됨** — 모든 ranking에서 div_cls=1로도 ETF 그대로 진입
3. **5/7 우선주 005935 진입 출처 = volume_rank 확정** (top 5에 직접 발견)

probe 보존: `scripts/probe_kis_div_cls.py`

### 0.3 거래대금 top 30 ETF/우선주 분포 (5/7 19:48 KST 단발)

**ETF/ETN 식별 결과 — 13/30건 = 43% 비중** (KIS volume-rank 자체에 ETF 다수):
```
KODEX 6건: 122630 KODEX 레버리지 / 069500 KODEX 200 / 091160 KODEX 반도체
            252670 KODEX 200선물인버스2X / 114800 KODEX 인버스 / 379800 KODEX 미국S&P500
            487240 KODEX AI전력핵심설비 / 233740 KODEX 코스닥150레버리지
            494310 KODEX 반도체레버리지 / 229200 KODEX 코스닥150
TIGER 3건: 396500 TIGER 반도체TOP10 / 102110 TIGER 200 / 360750 TIGER 미국S&P500
```

**우선주 식별 결과**:
- 진성: 1건 (005935 삼성전자우, 끝자리 5 + 종목명 "우")
- **false positive 후보 (끝자리 5/7/9 + 종목명 "우" 미포함): 0건** ✅
- → KOSPI top 30 한정으로 AND 조건의 false positive 위험 검증 완료

**종목명 brand 매칭 정확도**: 13/13 = **100%** — KODEX/TIGER prefix 단순 매칭으로 정확

probe 보존: `scripts/probe_etf_pref_distribution.py`

### 0.4 자기주식 영향 검증 (top 5 시총 종목, 5/7 19:48 KST)

| 종목 | stck_avls × 1억 | lstn × prpr | 차이 |
|---|---|---|---|
| 005930 삼성전자 | 1,587,264,600,000,000 | 1,587,264,642,072,000 | **+0.000%** |
| 000660 SK하이닉스 | 1,178,809,700,000,000 | 1,178,809,711,710,000 | **+0.000%** |
| 005935 삼성전자우 | 148,759,600,000,000 | 148,759,621,036,200 | **+0.000%** |
| 402340 SK스퀘어 | 145,022,300,000,000 | 145,022,266,214,000 | **-0.000%** |
| 005380 현대차 | 117,121,400,000,000 | 117,121,442,152,000 | **+0.000%** |

**핵심 발견**: 자기주식 영향 거의 0% (절대값 약 4천만~3억원, 시총 대비 0.000%대)
- 단위 2-9e CONTEXT "~0.01~5% 차이" 추정 **과도** — 실제는 거의 동등
- → 두 방식(`stck_avls × 1억` vs `lstn_stcn × stck_prpr`) 시총값 사실상 동등
- → 우선순위 결정은 **정확도 차이가 아니라 데이터 가용성 차이**: top 200 한정 vs top 30 한정

### Step 0 종합 결정사항 확정

| 차단 대상 | 1차 방어 (source-level) | 2차 방어 (universe_filters) |
|---|---|---|
| **우선주** | volume_rank `FID_DIV_CLS_CODE=1` 토글 (Step 0.2 직접 검증) | 종목명 "우/우B/우C/우K" 접미사 + 끝자리 5/7/9 **AND 조건** |
| **ETF/ETN** | 불가 (Step 0.2 검증) | **종목명 brand prefix 매칭** (KODEX/TIGER/... + ETN 키워드) |

**자체 시총 계산 우선순위** (자기주식 영향 ≈ 0% 발견 반영):
- 1순위: `stck_avls × 1억` (market_cap top 200 한정, 단위 2-9e 기존)
- 2순위: `lstn_stcn × stck_prpr` (volume_rank top 30, top 200 외 종목 보강)
- 3순위: 미매치 → `data_not_found` 탈락 (옵션 A 보수)

**종목명 회수 경로** (신규 발견):
- universe_filters는 ticker만 받음 → 종목명 회수 인프라 필요
- 해결: universe_provider_v2.py가 KIS ranking 응답에서 (ticker, name) 쌍 회수 → name_lookup_map 누적 → universe_filters에 인자로 주입
- 폴백: KIS ranking 응답에서 종목명 부재 시 closing_bet_system/infra/name_lookup.py (KIS get_stock_name + 캐시) 호출
- 종목명 회수 실패 시 보수적 통과 (false positive 회피)

### Step 0 게이트 통과
- [x] pykrx 사용 불가 확정 → 종목명 brand 방식 채택
- [x] KIS native filter 지원 범위 확정 (volume_rank 우선주만)
- [x] 5/7 분포 실측 (top 30 ETF 13건, 우선주 1건, false positive 0건)
- [x] 자기주식 영향 측정 (≈ 0% 확정 → 시총 보강 우선순위 = 가용성 기준)
- [x] 종목명 회수 경로 결정 (universe_provider_v2 (ticker, name) 쌍 추출)

## 한국거래소 종목코드 체계 (Step 0 결과로 갱신)

**우선주 코드 체계**:
- 일반 우선주: 끝자리 5 (예: 005935 삼성전자우, 005385 현대차우)
- 신형 우선주 (배당우선주): 끝자리 7 (예: 003547 대신증권1우C)
- 사모전환우선주: 끝자리 9 (드문 케이스)
- 보통주: 끝자리 0 (대다수, KOSPI top 30 검증)

**KOSPI 거래대금 top 30 (2026-05-07)**:
- false positive 후보 0건 (끝자리 5/7/9 + 종목명 "우" 미포함 케이스 없음)
- → AND 조건 안전 검증 완료

**ETF brand 분포 (KOSPI top 30)**:
- KODEX (삼성자산운용): 10건 (가장 빈번)
- TIGER (미래에셋자산운용): 3건
- (다른 brand는 top 30에 없음, 이외 시장에는 KOSEF/HANARO/ARIRANG/ACE/KBSTAR/SOL/KINDEX/RISE/PLUS/SMART 등 존재)

## 영향 범위
- **universe v2 출력**: 5/7 18건 기준 우선주 1건 차단 → 17건. ETF 차단 추가 시 다른 영업일에 추가 감소 가능
- **시총 보강 매치율**: 18/68(26%) → 60+/68(85%+) 향상 (volume_rank 자체 계산 효과)
- **30건 운영 게이트**: 이미 통과(33/30). ETF/우선주 차단 후 시그니처 정확도 향상
- **자동매매 진입 시**: ETF/우선주 차단 = 본주 매매 가능성 확보 (Phase 2 entry_executor 도입 시 중요)
- **호환성**: helper 분리는 _maybe_run_per_ticker_fallback 호출부에 영향 없음 (시그니처 유지). `get_top_value_data` 신규 메서드는 기존 `get_top_value_codes` 유지

## 시간 가이드 (사전 리뷰 반영)
- **Step 0 사전 조사**: 1.5~2시간 (pykrx ETF 리스트 검증 + KIS 매뉴얼 WebFetch + probe 스크립트 + 자기주식 종목 sample)
- **Step 1~4 코드 변경**: 3~4시간 (helper 분리 + 차단 분기 + 자체 시총 + 토글)
- **Step 5 단위 테스트 22건+**: 1.5시간
- **Step 6 code-tester + 회귀**: 30분
- **Step 7 systemd 재시작 + 30분 모니터링**: 30분
- **총 예상**: 7~9시간 (2~3 세션)

## 위험 / 안전장치
- **pykrx ETF 리스트 호출 실패**: 정적 폴백 set으로 안전망 (Step 0에서 폴백 set 사전 합의)
- **종목명 회수 실패**: KIS ranking 응답에 `hts_kor_isnm` 부재 시 보수적 통과 (false positive 회피)
- **자기주식 비중 큰 종목**: stck_avls × 1억 우선 사용 (단위 2-9e 일관)
- **롤백 4단계 토글**: ETF/우선주/kis_market_cap_priority/kis_div_cls_code 각각 독립 토글
