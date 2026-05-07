"""단위 2-9d 핫픽스 테스트 — KIS fluctuation 종목코드 필드 fallback.

mksc_shrn_iscd / stck_shrn_iscd 다중 필드 fallback 검증 (HF-1 ~ HF-14 + _extract_ticker 헬퍼 직접 검증).

회귀 누적 (단위 2-9e 91건 기준): 91 + 25 = 116건 (assert 25건).
"""
import sys

sys.path.insert(0, "/home/hatni/korean_stock_ai_trading")

from unittest.mock import patch

from closing_bet_system.collectors.kis_market_provider import (
    KISMarketProvider,
    _TICKER_KEYS_DEFAULT,
    _extract_ticker,
    _filter_valid_tickers,
)
import closing_bet_system.collectors.kis_market_provider as km


PASS = 0
FAIL = 0


def assert_eq(label: str, actual, expected) -> None:
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  ✅ {label}: {actual!r}")
    else:
        FAIL += 1
        print(f"  ❌ {label}: actual={actual!r}, expected={expected!r}")


# ===== HF-1: mksc 키만 — volume_rank 기존 동작 =====
print("\n=== HF-1: mksc 키만 (volume_rank 기존) ===")
items = [
    {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
    {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"},
    {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("mksc 단독 → 3건", codes, ["005930", "000660", "035420"])


# ===== HF-2: stck 키만 — fluctuation 신규 정상화 =====
print("\n=== HF-2: stck 키만 (fluctuation 신규) ===")
items = [
    {"stck_shrn_iscd": "002070", "hts_kor_isnm": "비비안"},
    {"stck_shrn_iscd": "060900", "hts_kor_isnm": "에이전트AI"},
    {"stck_shrn_iscd": "276730", "hts_kor_isnm": "한울앤제주"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("stck 단독 → 3건 (회귀 차단)", codes, ["002070", "060900", "276730"])


# ===== HF-3: 두 키 모두 — mksc 우선 =====
print("\n=== HF-3: 두 키 모두 (mksc 우선) ===")
items = [
    {"mksc_shrn_iscd": "005930", "stck_shrn_iscd": "999999"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("두 키 모두 → mksc 우선", codes, ["005930"])


# ===== HF-4: mksc=None + stck=value (fluctuation 실제 패턴) =====
print("\n=== HF-4: mksc=None + stck 값 (실제 fluctuation 패턴) ===")
items = [
    {"mksc_shrn_iscd": None, "stck_shrn_iscd": "002070"},
    {"mksc_shrn_iscd": None, "stck_shrn_iscd": "060900"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("mksc=None → stck fallback", codes, ["002070", "060900"])


# ===== HF-5: 두 키 모두 빈 문자열 =====
print("\n=== HF-5: 두 키 모두 빈 ===")
items = [
    {"mksc_shrn_iscd": "", "stck_shrn_iscd": ""},
    {"mksc_shrn_iscd": None, "stck_shrn_iscd": None},
    {"mksc_shrn_iscd": "  ", "stck_shrn_iscd": "  "},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("모두 빈 → []", codes, [])


# ===== HF-6: 비-6자리 코드 =====
print("\n=== HF-6: 비-6자리 코드 (정규식 탈락) ===")
items = [
    {"mksc_shrn_iscd": "A0010"},   # 5자리 + 영문
    {"stck_shrn_iscd": "12345"},    # 5자리
    {"mksc_shrn_iscd": "1234567"},  # 7자리
    {"stck_shrn_iscd": "00123A"},   # 영문 포함
    {"mksc_shrn_iscd": "005930"},   # 정상 6자리
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("정규식 탈락 후 1건", codes, ["005930"])


# ===== HF-7: 중복 코드 =====
print("\n=== HF-7: 중복 코드 (1회만) ===")
items = [
    {"mksc_shrn_iscd": "005930"},
    {"stck_shrn_iscd": "005930"},  # 다른 키지만 같은 코드
    {"mksc_shrn_iscd": "005930"},
    {"mksc_shrn_iscd": "000660"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("중복 제거 → 2건", codes, ["005930", "000660"])


# ===== HF-8: top_n 절단 =====
print("\n=== HF-8: top_n 절단 ===")
items = [{"mksc_shrn_iscd": f"{i:06d}"} for i in range(100, 200)]
codes = _filter_valid_tickers(items, top_n=5)
assert_eq("top_n=5 절단", len(codes), 5)
assert_eq("절단 결과 첫 코드", codes[0], "000100")


# ===== HF-9: items=None / [] =====
print("\n=== HF-9: items=None / [] ===")
assert_eq("None → []", _filter_valid_tickers(None, top_n=10), [])
assert_eq("[] → []", _filter_valid_tickers([], top_n=10), [])


# ===== HF-10: dict가 아닌 entry 섞임 =====
print("\n=== HF-10: dict가 아닌 entry 섞임 ===")
items = [
    {"mksc_shrn_iscd": "005930"},
    "string entry",  # type: ignore
    None,
    123,
    {"mksc_shrn_iscd": "000660"},
]
codes = _filter_valid_tickers(items, top_n=10)
assert_eq("비-dict 무시 → 2건", codes, ["005930", "000660"])


# ===== HF-11 (회귀): foreign_total mock 24건 =====
print("\n=== HF-11 (회귀): foreign_total 24건 유지 ===")
items = [
    {
        "mksc_shrn_iscd": f"{i:06d}",
        "hts_kor_isnm": f"종목{i}",
        "frgn_ntby_qty": str(i * 1000),
    }
    for i in range(1, 25)
]
codes = _filter_valid_tickers(items, top_n=30)
assert_eq("foreign_total 24건 회귀", len(codes), 24)


# ===== HF-12 (회귀): volume_rank mock 27건 =====
print("\n=== HF-12 (회귀): volume_rank 27건 유지 ===")
items = [
    {
        "mksc_shrn_iscd": f"{i:06d}",
        "hts_kor_isnm": f"종목{i}",
        "acml_tr_pbmn": str(i * 1000000),
    }
    for i in range(1, 28)
]
codes = _filter_valid_tickers(items, top_n=30)
assert_eq("volume_rank 27건 회귀", len(codes), 27)


# ===== HF-13 (시총 보강): mksc 키 정상 → market_cap 채워짐 =====
print("\n=== HF-13 (시총 보강): mksc 키 정상 ===")
mock_response = {
    "rt_cd": "0",
    "msg1": "OK",
    "output": [
        {
            "mksc_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "stck_avls": "15551101",
            "stck_prpr": "271500",
            "prdy_ctrt": "2.07",
        },
        {
            "mksc_shrn_iscd": "000660",
            "hts_kor_isnm": "SK하이닉스",
            "stck_avls": "1500000",
            "stck_prpr": "200000",
            "prdy_ctrt": "1.5",
        },
    ],
}


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, params))
        return _DummyResponse(self._payload)


class _DummyKIS:
    def __init__(self, payload):
        self.base_url = "https://mock"
        self.client = _DummyClient(payload)

    def _rate_limit(self):
        pass

    def _get_headers(self, tr_id):
        return {"tr_id": tr_id}


prov = KISMarketProvider(kis_api=_DummyKIS(mock_response))
result = prov.get_top_market_cap_data(top_n=10)
assert_eq("시총 보강 mksc → 2건", set(result.keys()), {"005930", "000660"})
assert_eq("005930 market_cap", result["005930"]["market_cap"], 1_555_110_100_000_000)


# ===== HF-14 (시총 보강 핫픽스): stck 키만 있는 응답 → 정상 동작 (예방) =====
print("\n=== HF-14 (시총 보강 핫픽스): stck 키만 → 예방적 정상 동작 ===")
mock_response_stck = {
    "rt_cd": "0",
    "msg1": "OK",
    "output": [
        {
            # mksc 키 부재, stck만 — fluctuation 패턴이 market_cap에 적용된 가상 시나리오
            "stck_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "stck_avls": "15551101",
            "stck_prpr": "271500",
            "prdy_ctrt": "2.07",
        },
    ],
}
prov2 = KISMarketProvider(kis_api=_DummyKIS(mock_response_stck))
result2 = prov2.get_top_market_cap_data(top_n=10)
assert_eq("시총 보강 stck fallback → 1건", set(result2.keys()), {"005930"})
assert_eq("stck 005930 market_cap", result2["005930"]["market_cap"], 1_555_110_100_000_000)


# ===== _extract_ticker 헬퍼 직접 검증 =====
print("\n=== _extract_ticker 헬퍼 검증 ===")
assert_eq("mksc 우선", _extract_ticker({"mksc_shrn_iscd": "005930", "stck_shrn_iscd": "999999"}), "005930")
assert_eq("mksc 부재 → stck", _extract_ticker({"stck_shrn_iscd": "002070"}), "002070")
assert_eq("mksc=None → stck", _extract_ticker({"mksc_shrn_iscd": None, "stck_shrn_iscd": "002070"}), "002070")
assert_eq("mksc='' → stck", _extract_ticker({"mksc_shrn_iscd": "", "stck_shrn_iscd": "002070"}), "002070")
assert_eq("두 키 모두 빈 → ''", _extract_ticker({"mksc_shrn_iscd": "", "stck_shrn_iscd": ""}), "")
assert_eq("두 키 모두 부재 → ''", _extract_ticker({"hts_kor_isnm": "x"}), "")
assert_eq("커스텀 키 우선순위", _extract_ticker({"a": "111111", "b": "222222"}, ("b", "a")), "222222")


# ===== 결과 =====
print(f"\n{'=' * 50}")
print(f"단위 2-9d 핫픽스 테스트 결과: {PASS} PASS / {FAIL} FAIL")
print(f"{'=' * 50}")
if FAIL > 0:
    sys.exit(1)
