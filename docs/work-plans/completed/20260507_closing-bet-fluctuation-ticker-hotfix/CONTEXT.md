# CONTEXT — 단위 2-9d 핫픽스 · KIS fluctuation 종목코드 필드 fallback

## 변경 이유
- **PRD 16-3 본래 의도**: universe v2가 4 출처 합집합으로 시그니처 다양성(거래대금/등락률/외국인 매수/테마) 보장
- **회귀 결과**: 단위 2-9d 도입 후 출처 3 (등락률 상위) 영손실. PRD Layer 3 모멘텀 시그니처 종목 한번도 universe 진입 못함
- **30건 게이트 통과 후 발견**: 33/30건 통과 후 5/7 자연 트리거 검증에서 비로소 noticed. 단위 2-9d/2-9e 단발 검증은 시총 보강에 집중되어 출처 3 검증 누락

## 현재 코드 상태

### 단일 필드 사용 패턴 (수정 대상)
```python
# closing_bet_system/collectors/kis_market_provider.py:60-67
# 응답 컬럼명 (volume_rank 검증, 다른 ranking 동일 패턴 추정)
_FIELD_TICKER = "mksc_shrn_iscd"            # 단축종목코드 (6자리)
_FIELD_NAME = "hts_kor_isnm"                # 종목명
_FIELD_PRICE = "stck_prpr"                  # 현재가
_FIELD_CHANGE_RATE = "prdy_ctrt"            # 등락률 (%)
_FIELD_VOLUME = "acml_vol"                  # 누적거래량
_FIELD_VALUE = "acml_tr_pbmn"               # 누적거래대금
_FIELD_MARKET_CAP = "stck_avls"             # 시가총액 (market-cap ranking)
```

```python
# closing_bet_system/collectors/kis_market_provider.py:91-107
def _filter_valid_tickers(items: list[dict], top_n: int) -> list[str]:
    """output 리스트에서 6자리 종목코드만 추출 (중복 제거 + top_n 절단)."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get(_FIELD_TICKER, "")).strip()  # ← 단일 필드만 시도
        if _TICKER_PATTERN.match(code) and code not in seen:
            seen.add(code)
            result.append(code)
        if len(result) >= top_n:
            break
    return result
```

### 시총 보강 동일 패턴 (라인 230-235 근처)
```python
# get_top_market_cap_data 내부
for item in items or []:
    if not isinstance(item, dict):
        continue
    code = str(item.get(_FIELD_TICKER, "")).strip()  # ← 동일 패턴, 핫픽스 동시 적용
    if not _TICKER_PATTERN.match(code):
        continue
    ...
```

## 5/7 KST 19:15 단발 raw 응답 (실제 데이터)

### fluctuation (FHPST01700000) — 종목코드는 `stck_shrn_iscd`
```
--- item 0 (data_rank=1, 비비안) ---
  hts_kor_isnm = '비비안'
  prdy_ctrt = '-7.00'
  stck_prpr = '11020'
  stck_shrn_iscd = '002070'    ← 종목코드 여기 있음
  (mksc_shrn_iscd 키 자체가 응답에 없음)

--- item 1 (data_rank=2, 에이전트AI) ---
  stck_shrn_iscd = '060900'

--- item 2 (data_rank=3, 한울앤제주) ---
  stck_shrn_iscd = '276730'
```

### volume_rank (FHPST01710000) — `mksc_shrn_iscd` 정상
- 5/7 자연 트리거에서 27건 정상 반환 + 5/7 19:30 단발 검증 — mksc_shrn_iscd='005930' (삼성전자) 단독
- **추가 발견**: 응답에 `lstn_stcn=5846278608` (발행주식수) 포함 — 단위 2-9f 자체 시총 계산 가능

### foreign_total (FHPTJ04400000) — `mksc_shrn_iscd` 정상
- 5/7 자연 트리거에서 24건 정상 반환 + 5/7 19:30 단발 검증 — mksc_shrn_iscd='034020' (두산에너빌리티) 단독

### market_cap (FHPST01740000) — `mksc_shrn_iscd` 정상
- 단위 2-9e 사전 조사 (5종목) + 5/7 19:30 단발 검증 — mksc_shrn_iscd='005930' 단독
- **추가 발견**: 응답에 `lstn_stcn=5846278608` 포함 — 단위 2-9f 자체 시총 계산 가능 (top 200 한도 내에서)

### 4종 ranking ticker 필드 매핑표 (5/7 19:30 단발 검증 확정)

| Ranking | TR_ID | Ticker 필드 | lstn_stcn | 비고 |
|---------|-------|------------|-----------|------|
| volume_rank | FHPST01710000 | `mksc_shrn_iscd` | ✅ 있음 | 19개 필드 |
| **fluctuation** | FHPST01700000 | **`stck_shrn_iscd`** | ❌ 없음 | 24개 필드 (회귀 원인) |
| foreign_total | FHPTJ04400000 | `mksc_shrn_iscd` | ❌ 없음 | 26개 필드 |
| market_cap | FHPST01740000 | `mksc_shrn_iscd` | ✅ 있음 | 11개 필드 |

핫픽스 `_TICKER_KEYS_DEFAULT` 우선순위: `("mksc_shrn_iscd", "stck_shrn_iscd")` — mksc 우선 (3/4 ranking 사용), stck fallback.

## 핵심 스니펫 (수정안)
```python
# 새 시그니처 — 다중 필드 fallback
_TICKER_KEYS_DEFAULT = ("mksc_shrn_iscd", "stck_shrn_iscd")

def _filter_valid_tickers(
    items: list[dict],
    top_n: int,
    *,
    ticker_keys: tuple[str, ...] = _TICKER_KEYS_DEFAULT,
) -> list[str]:
    """output 리스트에서 6자리 종목코드만 추출 (중복 제거 + top_n 절단).

    KIS ranking 종류별 종목코드 필드명 차이 대응:
    - volume_rank / market_cap / foreign_total: ``mksc_shrn_iscd``
    - fluctuation: ``stck_shrn_iscd``
    각 item에서 ``ticker_keys`` 순서로 빈 문자열/None이 아닌 첫 값을 사용.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = ""
        for key in ticker_keys:
            raw = item.get(key)
            if raw:
                code = str(raw).strip()
                if code:
                    break
        if _TICKER_PATTERN.match(code) and code not in seen:
            seen.add(code)
            result.append(code)
        if len(result) >= top_n:
            break
    return result
```

## 과거 버그 / 단서
- **단위 2-9d CONTEXT 노트** ("통합 단발: KIS ranking 정상(theme=17/top_value=25/top_foreign=21=63종목, 17→63 3.7배)") — top_change 결과가 적혀 있지 않음 → 단위 2-9d 단발 검증 누락이 원인. 5/6 자연 트리거가 첫 발견 기회였으나 다른 회귀(전 출처 0)에 묻힘
- **단위 2-9d code-tester** (심각 3건 + 주의 6건) — direction 인자 미반영, _FIELD_CHANGE_RATE 상수, cfg NameError 등을 잡았지만 응답 필드명 차이는 검출 못함 (raw 응답 단발 검증 부재)

## 영향 범위
- **universe_v2**: 출처 3 (등락률) 영손실 → 합집합 종목 수 감소. 5/7 기준 70종목 (다른 출처 70 = 19+27+0+24)
- **PRD 16-3 Layer 3 시그니처**: 등락률 상위 종목이 universe에 들어오지 못함 → Layer 3 모멘텀 점수 베이스라인 부재 (Phase 2 가중치 활성화 시 영향)
- **30건 게이트**: 통과는 했지만 시그니처 다양성 측정엔 무효 (등락률 시그니처 부재)
- **호환성**: 핫픽스는 기존 ranking 동작 보존(mksc 우선 fallback). volume_rank/foreign_total/market_cap이 우연히 stck 키도 응답에 포함하는 경우에도 mksc 값 우선이라 회귀 위험 없음

## 코드 검증
- 핫픽스 후 `_filter_valid_tickers` 호출 경로 확인
  - `get_top_value_codes` (volume_rank): mksc 정상 → 동작 변화 없음
  - `get_top_change_codes` (fluctuation): stck fallback 적용 → 신규 정상화
  - `get_top_foreign_buy_codes` (foreign_total): mksc 정상 → 동작 변화 없음
  - `get_top_market_cap_data` (market_cap): 시총 보강 코드 동일 패턴 적용 → 동작 변화 없음

## 시간 가이드
- 사전 단발 검증 (4종 ranking 필드 매핑): KIS 토큰 1분 제한 → 단일 스크립트로 4종 일괄 호출 (네번째까지 약 30~40초 예상)
- 핫픽스 코드 변경: 10~20분
- 단위 테스트 14건 작성: 30분
- code-tester + 회귀: 20분
- systemd 재시작 + 30분 모니터링: 30분
- **총 예상**: 1.5~2시간 (5/8 자연 트리거 검증은 익일 09:00 이후)
