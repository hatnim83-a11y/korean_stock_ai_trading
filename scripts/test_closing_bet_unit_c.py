"""단위 C 검증: market_data_provider.

시나리오:
    MD-1: 정상 — KIS+yfinance 모두 응답 → 4 키 모두 비-None
    MD-2: 캐시 히트 — 같은 거래일 두 번째 호출 시 외부 호출 0회
    MD-3: KIS 실패 → kospi None, 다른 키는 정상
    MD-4: yfinance 실패 → vkospi/us_futures/usd_krw None, kospi 정상
    MD-5: 모두 실패 → 모든 시장 키 None (룰 비활성), placeholder 키 None
    MD-6: KIS change_rate=-1.5 (% 단위) → kospi_change_pct=-0.015 (소수)
    MD-7: yfinance NaN → None
    MD-8: yfinance 데이터 1개 → change_pct None (전일 비교 불가)
    MD-9: yfinance import 실패 → 해당 키 None, 다른 키 영향 없음
    MD-10: get_market_data 결과 dict 키 6개 모두 존재 (placeholder 포함)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import market_data_provider as mdp


# ===== Helpers =====


class _FakeHistory:
    """yfinance Ticker.history() 흉내 — pandas DataFrame.iloc 인터페이스."""

    def __init__(self, closes: list[float] | None):
        self.closes = closes
        self.empty = closes is None or len(closes) == 0

    def __len__(self):
        return 0 if self.closes is None else len(self.closes)

    def __getitem__(self, key):
        # df["Close"] 흉내
        return _FakeColumn(self.closes)


class _FakeColumn:
    def __init__(self, values):
        self.values = values

    @property
    def iloc(self):
        return self

    def __getitem__(self, idx):
        return self.values[idx]


def _make_yf_ticker(closes_by_symbol: dict):
    """yf.Ticker(symbol).history(...) → _FakeHistory mock factory."""
    def _factory(symbol):
        ticker = MagicMock()
        closes = closes_by_symbol.get(symbol)
        ticker.history.return_value = _FakeHistory(closes)
        return ticker
    return _factory


# ===== 테스트 =====


def test_MD_1_정상_4키():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.return_value = {"price": 2700.0, "change_rate": -0.5, "change": -13.5}
    closes = {
        mdp.YF_VKOSPI_SYMBOL: [20.0, 21.5],
        mdp.YF_US_FUTURES_SYMBOL: [4500.0, 4510.0],
        mdp.YF_USD_KRW_SYMBOL: [1380.0, 1395.0],
    }
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", side_effect=lambda s: closes[s][-1]), \
         patch.object(mdp, "_yf_change_pct",
                      side_effect=lambda s: round((closes[s][-1] - closes[s][-2]) / closes[s][-2], 6)):
        data = mdp.get_market_data()
    assert data["kospi_change_pct"] == -0.005
    assert data["vkospi"] == 21.5
    assert abs(data["us_futures_change_pct"] - 0.002222) < 1e-5
    assert abs(data["usd_krw_change_pct"] - 0.010870) < 1e-5
    assert data["kospi_above_200ma"] is None  # placeholder
    assert data["foreign_5d_cumulative"] is None
    print("[PASS] MD-1: 정상 — 4 키 모두 수집, placeholder 2 키 None")


def test_MD_2_캐시_히트():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.return_value = {"price": 2700.0, "change_rate": 0.0}
    yf_close = MagicMock(return_value=20.0)
    yf_change = MagicMock(return_value=0.001)
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", yf_close), \
         patch.object(mdp, "_yf_change_pct", yf_change):
        d1 = mdp.get_market_data()
        d2 = mdp.get_market_data()
    assert d1 == d2
    # 두 번째 호출은 캐시 히트 → KIS/yfinance 호출 0회 (총 호출은 첫 호출의 1+1+2=4)
    assert fake_kis.get_index_price.call_count == 1
    assert yf_close.call_count == 1
    assert yf_change.call_count == 2
    print("[PASS] MD-2: 캐시 히트 — 두 번째 호출 시 외부 호출 0회")


def test_MD_3_KIS_실패():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.side_effect = RuntimeError("KIS 장애")
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", return_value=20.0), \
         patch.object(mdp, "_yf_change_pct", return_value=0.001):
        data = mdp.get_market_data()
    assert data["kospi_change_pct"] is None
    assert data["vkospi"] == 20.0
    assert data["us_futures_change_pct"] == 0.001
    assert data["usd_krw_change_pct"] == 0.001
    print("[PASS] MD-3: KIS 실패 — kospi None, 다른 키 정상")


def test_MD_4_yfinance_실패():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.return_value = {"change_rate": -2.0}
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", side_effect=Exception("yf timeout")), \
         patch.object(mdp, "_yf_change_pct", side_effect=Exception("yf timeout")):
        data = mdp.get_market_data()
    assert data["kospi_change_pct"] == -0.02
    assert data["vkospi"] is None
    assert data["us_futures_change_pct"] is None
    assert data["usd_krw_change_pct"] is None
    print("[PASS] MD-4: yfinance 실패 — kospi 정상, 나머지 None")


def test_MD_5_모두_실패():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.side_effect = RuntimeError("X")
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", side_effect=Exception("X")), \
         patch.object(mdp, "_yf_change_pct", side_effect=Exception("X")):
        data = mdp.get_market_data()
    for k in ["kospi_change_pct", "vkospi", "us_futures_change_pct", "usd_krw_change_pct",
              "kospi_above_200ma", "foreign_5d_cumulative"]:
        assert data[k] is None, f"MD-5 FAIL: {k}={data[k]}"
    print("[PASS] MD-5: 모두 실패 — 모든 키 None (룰 비활성)")


def test_MD_6_KIS_pct_변환():
    """KIS는 % 단위(예: -1.5%) → 소수 -0.015로 변환."""
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.return_value = {"change_rate": -1.5}
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis):
        result = mdp._fetch_kospi_change_pct()
    assert result == -0.015, f"MD-6 FAIL: {result}"
    print("[PASS] MD-6: KIS %단위 → 소수 변환 (-1.5% → -0.015)")


def test_MD_7_yfinance_NaN():
    """yfinance NaN 값 → None."""
    assert mdp._safe_num(float("nan")) is None
    assert mdp._safe_num(float("inf")) is None
    assert mdp._safe_num(None) is None
    assert mdp._safe_num("abc") is None
    assert mdp._safe_num(123.45) == 123.45
    print("[PASS] MD-7: yfinance NaN/inf/None → None")


def test_MD_8_yfinance_데이터_1개():
    """yfinance 데이터가 1개면 전일 비교 불가 → None."""
    fake_yf = MagicMock()
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _FakeHistory([20.0])  # 1개
    fake_yf.Ticker.return_value = fake_ticker
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        result = mdp._yf_change_pct("ANY")
    assert result is None
    print("[PASS] MD-8: yfinance 데이터 1개 → 변동률 None")


def test_MD_9_yfinance_미설치():
    """yfinance ImportError 시 해당 키 None."""
    # sys.modules에서 yfinance 제거 + import 시 raise
    saved = sys.modules.get("yfinance")
    sys.modules["yfinance"] = None  # type: ignore
    try:
        result_close = mdp._yf_latest_close("X")
        result_change = mdp._yf_change_pct("X")
    finally:
        if saved is not None:
            sys.modules["yfinance"] = saved
        else:
            del sys.modules["yfinance"]
    # None 모듈을 import하면 ImportError 발생 → 함수가 None 반환
    assert result_close is None
    assert result_change is None
    print("[PASS] MD-9: yfinance 미설치 (ImportError) → None")


def test_MD_10_dict_키_6개():
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.return_value = None
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", return_value=None), \
         patch.object(mdp, "_yf_change_pct", return_value=None):
        data = mdp.get_market_data()
    expected_keys = {
        "kospi_change_pct", "vkospi", "us_futures_change_pct",
        "usd_krw_change_pct", "kospi_above_200ma", "foreign_5d_cumulative",
    }
    assert set(data.keys()) == expected_keys, (
        f"MD-10 FAIL: {set(data.keys())} != {expected_keys}"
    )
    print(f"[PASS] MD-10: dict 키 6개 모두 존재 — {sorted(expected_keys)}")


def test_MD_11_overnight_risk_filter_통합():
    """OvernightRiskFilter 가 결손 정책으로 None 키 안전 처리."""
    mdp.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_index_price.side_effect = RuntimeError("X")
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake_kis), \
         patch.object(mdp, "_yf_latest_close", side_effect=Exception("X")), \
         patch.object(mdp, "_yf_change_pct", side_effect=Exception("X")):
        data = mdp.get_market_data()

    from closing_bet_system.engines.overnight_risk_filter import OvernightRiskFilter
    rf = OvernightRiskFilter()
    assess = rf.assess(ticker="005930", market_data=data, dart_snapshot=None)
    # 모든 시장 키 None → can_enter=True (룰 비활성), final_size=1.0
    assert assess.can_enter is True
    assert assess.final_size_factor == 1.0
    print("[PASS] MD-11: OvernightRiskFilter — 결손 dict 안전 (can_enter=True)")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 C 검증: market_data_provider")
    print("=" * 60)
    test_MD_1_정상_4키()
    test_MD_2_캐시_히트()
    test_MD_3_KIS_실패()
    test_MD_4_yfinance_실패()
    test_MD_5_모두_실패()
    test_MD_6_KIS_pct_변환()
    test_MD_7_yfinance_NaN()
    test_MD_8_yfinance_데이터_1개()
    test_MD_9_yfinance_미설치()
    test_MD_10_dict_키_6개()
    test_MD_11_overnight_risk_filter_통합()
    print("\n" + "=" * 60)
    print("✅ 단위 C 11 시나리오 모두 PASS")
    print("=" * 60)
