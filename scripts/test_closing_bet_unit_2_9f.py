"""단위 2-9f 테스트 — ETF/우선주 차단 + lstn_stcn × stck_prpr 자체 시총 계산.

Step 0 사전 조사 결과 반영:
- ETF: 종목명 brand prefix 매칭 (KOSPI top 30 100% 정확도, 13건 검증)
- 우선주: 끝자리 + 종목명 AND 조건 (KOSPI top 30 false positive 0건)
- 시총: stck_avls × 1억 (1순위) + lstn_stcn × stck_prpr (2순위, 자기주식 영향 ≈ 0%)

테스트 분포 (assert 단위):
- 5.1 ETF/ETN 차단 (5건)
- 5.2 우선주 차단 (5건)
- 5.3 자체 시총 계산 (4건)
- 5.4 통합 (7건)
- 5.5 회귀 (3건)
- 5.6 Edge case (10건)
= 34건 PASS

회귀 누적 (단위 2-9d-hotfix 116건 기준): 116 + 34 = 150건 (또는 단위 2-2b 포함 175건+).
"""
import sys
from unittest.mock import patch

sys.path.insert(0, "/home/hatni/korean_stock_ai_trading")

from closing_bet_system.collectors.universe_filters import (
    _ETF_BRAND_PREFIXES,
    _PREF_STOCK_LAST_DIGITS,
    _PREF_STOCK_NAME_SUFFIXES,
    _is_etf_or_etn,
    _is_pref_stock,
    REJECTION_REASON_IS_ETF,
    REJECTION_REASON_IS_PREF_STOCK,
    apply_attribute_filters,
)
from closing_bet_system.collectors.kis_market_provider import (
    _FIELD_LISTED_SHARES,
    _FIELD_NAME,
    _FIELD_PRICE,
    _compute_market_cap_from_response,
    KISMarketProvider,
)


PASS = 0
FAIL = 0


def assert_eq(label: str, actual, expected) -> None:
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  PASS {label}: {actual!r}")
    else:
        FAIL += 1
        print(f"  FAIL {label}: actual={actual!r}, expected={expected!r}")


def assert_true(label: str, value) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  PASS {label}: True")
    else:
        FAIL += 1
        print(f"  FAIL {label}: actual={value!r}, expected=True")


# ===== 5.1 ETF/ETN 차단 (5건) =====
print("\n=== 5.1 ETF/ETN 차단 ===")
# ETF-1: KODEX brand
assert_true("ETF-1: KODEX 200 → True", _is_etf_or_etn("KODEX 200"))
# ETF-2: TIGER brand
assert_true("ETF-2: TIGER 반도체TOP10 → True", _is_etf_or_etn("TIGER 반도체TOP10"))
# ETF-3: 보통주 → False (회귀)
assert_eq("ETF-3: 삼성전자 → False (보통주 회귀)", _is_etf_or_etn("삼성전자"), False)
# ETF-4: ETN 키워드
assert_true("ETF-4: 신한 ETN-1F WTI원유 → True", _is_etf_or_etn("신한 ETN-1F WTI원유"))
# ETF-5: KOSEF
assert_true("ETF-5: KOSEF 국고채10년 → True", _is_etf_or_etn("KOSEF 국고채10년"))


# ===== 5.2 우선주 차단 (5건) =====
print("\n=== 5.2 우선주 차단 (AND 조건) ===")
# PREF-1: 005935 + "삼성전자우" → True
assert_true("PREF-1: 005935 삼성전자우 → True", _is_pref_stock("005935", "삼성전자우"))
# PREF-2: 005930 보통주 → False (끝자리 0)
assert_eq("PREF-2: 005930 삼성전자 → False", _is_pref_stock("005930", "삼성전자"), False)
# PREF-3: 005385 + "현대차우" → True
assert_true("PREF-3: 005385 현대차우 → True", _is_pref_stock("005385", "현대차우"))
# PREF-4: 끝자리 5 + 종목명 "우" 미포함 → False (false positive 회피)
assert_eq("PREF-4: 069085 KOSDAQ 보통주 가상 → False (AND 미충족)", _is_pref_stock("069085", "웹젠"), False)
# PREF-5: 끝자리 7 + "우C" → True
assert_true("PREF-5: 003547 대신증권1우C → True", _is_pref_stock("003547", "대신증권1우C"))


# ===== 5.3 자체 시총 계산 (4건) =====
print("\n=== 5.3 자체 시총 계산 ===")
# MC-CALC-1: 삼성전자 실측값 (Step 0.4)
mcap_1 = _compute_market_cap_from_response({
    _FIELD_LISTED_SHARES: "5846278608",
    _FIELD_PRICE: "271500",
})
assert_eq("MC-CALC-1: 삼성전자 5,846,278,608 × 271,500", mcap_1, 5_846_278_608 * 271_500)

# MC-CALC-2: lstn_stcn 부재 → None
mcap_2 = _compute_market_cap_from_response({_FIELD_PRICE: "271500"})
assert_eq("MC-CALC-2: lstn_stcn 부재 → None", mcap_2, None)

# MC-CALC-3: stck_prpr=0 → None
mcap_3 = _compute_market_cap_from_response({
    _FIELD_LISTED_SHARES: "5846278608",
    _FIELD_PRICE: "0",
})
assert_eq("MC-CALC-3: stck_prpr=0 → None", mcap_3, None)

# MC-CALC-4: NaN/문자/음수 → None
mcap_4 = _compute_market_cap_from_response({
    _FIELD_LISTED_SHARES: "abc",
    _FIELD_PRICE: "271500",
})
assert_eq("MC-CALC-4: 문자 → None", mcap_4, None)


# ===== 5.4 통합 (3건) =====
print("\n=== 5.4 통합 ===")
# INTEGRATION-1: get_top_value_data mock 30종목
class _DummyResp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._p

class _DummyClient:
    def __init__(self, payload):
        self.payload = payload
    def get(self, url, headers=None, params=None):
        return _DummyResp(self.payload)

class _DummyKIS:
    def __init__(self, payload):
        self.base_url = "https://mock"
        self.client = _DummyClient(payload)
    def _rate_limit(self):
        pass
    def _get_headers(self, tr_id):
        return {"tr_id": tr_id}


# 시총 자체 계산 통합 — 30종목 mock 응답, 모두 lstn_stcn × stck_prpr 정상
mock_payload = {
    "rt_cd": "0",
    "msg1": "OK",
    "output": [
        {
            "mksc_shrn_iscd": f"{i:06d}",
            "hts_kor_isnm": f"종목{i}",
            "lstn_stcn": str(1_000_000 + i * 1000),
            "stck_prpr": str(10000 + i * 100),
            "prdy_ctrt": "1.5",
        }
        for i in range(30)
    ],
}
prov = KISMarketProvider(kis_api=_DummyKIS(mock_payload))
result = prov.get_top_value_data(top_n=30)
assert_eq("INTEGRATION-1: 30종목 자체 시총 계산", len(result), 30)
# 각 entry에 market_cap 포함
all_have_mcap = all("market_cap" in entry for entry in result.values())
assert_true("INTEGRATION-1b: 모든 entry에 market_cap 포함", all_have_mcap)

# INTEGRATION-2: lstn_stcn 부재 종목 → market_cap 키 미생성 (옵션 A 보수)
mock_payload_2 = {
    "rt_cd": "0",
    "msg1": "OK",
    "output": [
        {
            "mksc_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "lstn_stcn": "5846278608",
            "stck_prpr": "271500",
        },
        {
            "mksc_shrn_iscd": "000660",
            "hts_kor_isnm": "SK하이닉스",
            # lstn_stcn 부재
            "stck_prpr": "200000",
        },
    ],
}
prov2 = KISMarketProvider(kis_api=_DummyKIS(mock_payload_2))
result_2 = prov2.get_top_value_data(top_n=10)
assert_true("INTEGRATION-2a: 005930 market_cap 채워짐", "market_cap" in result_2.get("005930", {}))
assert_eq("INTEGRATION-2b: 000660 market_cap 미생성", "market_cap" not in result_2.get("000660", {}), True)

# INTEGRATION-3: apply_attribute_filters with name_lookup_map
# severity_map=None, ETF/우선주 차단 활성, 시총 필터는 cfg로 우회
def _mock_market_data(today_str, tickers=None):
    return {t: {"market_cap": 100_000_000_000_000, "close": 50000, "change_rate": 1.0}
            for t in (tickers or [])}

with patch("closing_bet_system.collectors.universe_filters._fetch_market_data_bulk", _mock_market_data), \
     patch("closing_bet_system.collectors.universe_filters._fetch_52w_high", lambda t, d: 60000.0):
    tickers = ["005930", "069500", "005935"]  # 보통주 / KODEX ETF / 삼성전자우
    name_map = {
        "005930": "삼성전자",
        "069500": "KODEX 200",
        "005935": "삼성전자우",
    }
    cfg = {
        "min_market_cap": 50_000_000_000,
        "min_price": 1000,
        "max_drop_from_52w_high": 0.30,
        "exclude_upper_limit": True,
        "etf_block_enabled": True,
        "pref_stock_block_enabled": True,
    }
    passed, rejected = apply_attribute_filters(
        tickers, config=cfg, name_lookup_map=name_map,
    )
    assert_eq("INTEGRATION-3a: 005930 통과", "005930" in passed, True)
    assert_eq("INTEGRATION-3b: 069500 KODEX → is_etf", rejected.get("069500"), REJECTION_REASON_IS_ETF)
    assert_eq("INTEGRATION-3c: 005935 → is_pref_stock", rejected.get("005935"), REJECTION_REASON_IS_PREF_STOCK)


# ===== 5.5 회귀 (3건) =====
print("\n=== 5.5 회귀 ===")
# REGRESSION-1: 단위 2-9d-hotfix — fluctuation stck_shrn_iscd ticker 추출 정상
from closing_bet_system.collectors.kis_market_provider import _filter_valid_tickers
items_flu = [
    {"stck_shrn_iscd": "002070", "hts_kor_isnm": "비비안"},
    {"stck_shrn_iscd": "060900", "hts_kor_isnm": "에이전트AI"},
]
codes_flu = _filter_valid_tickers(items_flu, top_n=10)
assert_eq("REGRESSION-1: 단위 2-9d-hotfix fluctuation 회귀", codes_flu, ["002070", "060900"])

# REGRESSION-2: 단위 2-9e — stck_avls × 1억 정확값 유지
mock_mcap_payload = {
    "rt_cd": "0",
    "msg1": "OK",
    "output": [{
        "mksc_shrn_iscd": "005930",
        "hts_kor_isnm": "삼성전자",
        "stck_avls": "15551101",
        "stck_prpr": "271500",
        "prdy_ctrt": "2.07",
    }],
}
prov_mcap = KISMarketProvider(kis_api=_DummyKIS(mock_mcap_payload))
result_mcap = prov_mcap.get_top_market_cap_data(top_n=10)
assert_eq("REGRESSION-2: 단위 2-9e stck_avls × 1억", result_mcap["005930"]["market_cap"], 1_555_110_100_000_000)

# REGRESSION-3: ETF/우선주 토글 둘 다 OFF + name_map 주입 → ETF/우선주 통과 (회귀)
with patch("closing_bet_system.collectors.universe_filters._fetch_market_data_bulk", _mock_market_data), \
     patch("closing_bet_system.collectors.universe_filters._fetch_52w_high", lambda t, d: 60000.0):
    cfg_off = {
        "min_market_cap": 50_000_000_000,
        "min_price": 1000,
        "max_drop_from_52w_high": 0.30,
        "exclude_upper_limit": True,
        "etf_block_enabled": False,
        "pref_stock_block_enabled": False,
    }
    passed_off, _ = apply_attribute_filters(
        ["005930", "069500", "005935"],
        config=cfg_off,
        name_lookup_map={
            "005930": "삼성전자", "069500": "KODEX 200", "005935": "삼성전자우",
        },
    )
    assert_eq("REGRESSION-3: 토글 OFF → 3건 모두 통과", set(passed_off), {"005930", "069500", "005935"})


# ===== 5.6 Edge case (4건) =====
print("\n=== 5.6 Edge case ===")
# EDGE-1: ticker None/빈 → False
assert_eq("EDGE-1a: ticker=None → _is_pref_stock False", _is_pref_stock(None, "삼성전자우"), False)
assert_eq("EDGE-1b: ticker='' → False", _is_pref_stock("", "삼성전자우"), False)
assert_eq("EDGE-1c: ticker='12345' (5자리) → False", _is_pref_stock("12345", "삼성전자우"), False)
assert_eq("EDGE-1d: ticker='ABC123' (영문) → False", _is_pref_stock("ABC123", "삼성전자우"), False)

# EDGE-2: lstn_stcn 문자열 → _safe_int 정상 처리 (이미 MC-CALC-1에서 검증)
mcap_str = _compute_market_cap_from_response({
    _FIELD_LISTED_SHARES: "5846278608",  # str
    _FIELD_PRICE: "271500",  # str
})
assert_eq("EDGE-2: lstn_stcn 문자열 응답", mcap_str, 5_846_278_608 * 271_500)

# EDGE-3: name=None / 빈 → ETF/우선주 False (보수)
assert_eq("EDGE-3a: _is_etf_or_etn(None) → False", _is_etf_or_etn(None), False)
assert_eq("EDGE-3b: _is_etf_or_etn('') → False", _is_etf_or_etn(""), False)
assert_eq("EDGE-3c: _is_pref_stock(005935, None) → False", _is_pref_stock("005935", None), False)
assert_eq("EDGE-3d: _is_pref_stock(005935, '') → False", _is_pref_stock("005935", ""), False)

# EDGE-4: name_lookup_map=None → ETF/우선주 단계 스킵 (보수)
with patch("closing_bet_system.collectors.universe_filters._fetch_market_data_bulk", _mock_market_data), \
     patch("closing_bet_system.collectors.universe_filters._fetch_52w_high", lambda t, d: 60000.0):
    cfg_skip = {
        "min_market_cap": 50_000_000_000,
        "min_price": 1000,
        "max_drop_from_52w_high": 0.30,
        "exclude_upper_limit": True,
        "etf_block_enabled": True,
        "pref_stock_block_enabled": True,
    }
    passed_skip, _ = apply_attribute_filters(
        ["005930", "069500", "005935"],
        config=cfg_skip,
        name_lookup_map=None,  # 회수 실패 시뮬
    )
    # name_lookup_map None → ETF/우선주 차단 단계 스킵 → 3건 모두 통과
    assert_eq("EDGE-4: name_lookup_map=None → 차단 스킵 (3건 통과)", len(passed_skip), 3)


# ===== 결과 =====
print(f"\n{'=' * 50}")
print(f"단위 2-9f 테스트 결과: {PASS} PASS / {FAIL} FAIL")
print(f"{'=' * 50}")
if FAIL > 0:
    sys.exit(1)
