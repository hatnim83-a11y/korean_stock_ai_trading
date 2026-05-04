"""단위 2-9b 검증: universe_filters (PRD 4-1 속성 + 4-4 유동성 필터).

시나리오 (12+):
    UF-1:  시총 500억 미만 제외
    UF-2:  주가 1000원 미만 제외
    UF-3:  상한가 제외 (+29.5% 이상)
    UF-4:  52주 고점 -30% 초과 하락 제외
    UF-5:  20일 평균 거래대금 < 50억 제외
    UF-6:  당일 거래대금 < 100억 제외
    UF-7:  모든 필터 통과 종목 → 빈 dict (rejection 없음)
    UF-8:  한 종목이 여러 필터 위반 → 첫 위반만 기록
    UF-9:  pykrx 호출 실패 → 빈 결과 (graceful 통과)
    UF-10: 통합 함수 — universe_v2 + 필터 → 최종 list
    UF-11: rejected 사유 dict 형식 검증 ({ticker: reason})
    UF-12: 정상 시 통과율 50~90% 분포 (50종목 시뮬)
    UF-13: 빈 입력 → 빈 결과
    UF-14: settings.yaml 로드 실패 → default 폴백
    UF-15: 통합 함수 get_universe_v2_filtered — graceful 폴백 (필터 import 실패)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import universe_filters as uf
from closing_bet_system.collectors import universe_provider_v2 as upv2


# ===== Fixtures =====


def _make_market_cap_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """시가총액 DataFrame (pykrx get_market_cap_by_ticker 형식)."""
    return pd.DataFrame(
        {"시가총액": [v for _, v in rows]},
        index=[t for t, _ in rows],
    )


def _make_ohlcv_df(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """OHLCV DataFrame: (ticker, close, change_rate_pct, trading_value)."""
    return pd.DataFrame(
        {
            "종가": [r[1] for r in rows],
            "등락률": [r[2] for r in rows],
            "거래대금": [r[3] for r in rows],
        },
        index=[r[0] for r in rows],
    )


def _make_year_ohlcv_df(highs: list[float]) -> pd.DataFrame:
    """1년치 OHLCV DataFrame — 고가만 의미. 거래대금은 별도."""
    return pd.DataFrame(
        {"고가": highs, "거래대금": [10_000_000_000] * len(highs)},
    )


def _make_recent_ohlcv_df(values: list[float]) -> pd.DataFrame:
    """최근 30일 OHLCV — 거래대금 컬럼만 의미."""
    return pd.DataFrame({"거래대금": values, "고가": [50_000] * len(values)})


def _build_fake_krx(
    *,
    cap_data: dict[str, list[tuple[str, float]]] = None,
    ohlcv_data: dict[str, list[tuple[str, float, float, float]]] = None,
    by_date_data: dict[str, pd.DataFrame] = None,
):
    """KOSPI/KOSDAQ 분리 + ticker별 데이터를 mock하는 헬퍼.

    Args:
        cap_data: ``{"KOSPI": [(ticker, market_cap), ...], "KOSDAQ": [...]}``
        ohlcv_data: ``{"KOSPI": [(ticker, close, chg, value), ...], "KOSDAQ": [...]}``
        by_date_data: ``{ticker: DataFrame}`` — 종목별 1년치 또는 30일치
    """
    fake_krx = MagicMock()

    def _fake_cap(date, market):
        rows = (cap_data or {}).get(market, [])
        return _make_market_cap_df(rows) if rows else pd.DataFrame()

    def _fake_ohlcv(date, market):
        rows = (ohlcv_data or {}).get(market, [])
        return _make_ohlcv_df(rows) if rows else pd.DataFrame()

    def _fake_by_date(fromdate, todate, ticker):
        return (by_date_data or {}).get(ticker, pd.DataFrame())

    fake_krx.get_market_cap_by_ticker.side_effect = _fake_cap
    fake_krx.get_market_ohlcv_by_ticker.side_effect = _fake_ohlcv
    fake_krx.get_market_ohlcv_by_date.side_effect = _fake_by_date
    return fake_krx


# ===== 시나리오 =====


def test_UF_1_시총_500억_미만_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={
            "KOSPI": [
                ("100001", 100_000_000_000),  # 1000억 ✅
                ("100002", 30_000_000_000),   # 300억 ❌
            ],
            "KOSDAQ": [],
        },
        ohlcv_data={
            "KOSPI": [
                ("100001", 5000.0, 1.0, 50_000_000_000),
                ("100002", 5000.0, 1.0, 50_000_000_000),
            ],
            "KOSDAQ": [],
        },
        # 52주 고점 데이터 (현재가 5000원, 고가 6000원 → 16% 하락 → 통과)
        by_date_data={
            "100001": _make_year_ohlcv_df([6000.0]),
            "100002": _make_year_ohlcv_df([6000.0]),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    assert passed == ["100001"], f"UF-1 FAIL passed: {passed}"
    assert rejected.get("100002") == "market_cap_too_low", f"UF-1 FAIL rejected: {rejected}"
    print(f"[PASS] UF-1: 시총 500억 미만 제외 → {passed} (탈락: {rejected})")


def test_UF_2_주가_1000원_미만_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 100_000_000_000),
            ("100002", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 1.0, 50_000_000_000),  # 통과
            ("100002", 800.0, 1.0, 50_000_000_000),    # 800원 ❌
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_year_ohlcv_df([6000.0]),
            "100002": _make_year_ohlcv_df([1000.0]),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    assert passed == ["100001"]
    assert rejected.get("100002") == "price_too_low"
    print(f"[PASS] UF-2: 주가 1000원 미만 제외 → {passed} (탈락: {rejected})")


def test_UF_3_상한가_29p5_이상_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 100_000_000_000),
            ("100002", 100_000_000_000),
            ("100003", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 28.0, 50_000_000_000),  # 28% — 통과
            ("100002", 5000.0, 29.5, 50_000_000_000),  # 29.5% — 상한가
            ("100003", 5000.0, 29.97, 50_000_000_000), # 29.97% — 상한가
        ], "KOSDAQ": []},
        by_date_data={
            t: _make_year_ohlcv_df([6000.0]) for t in ["100001", "100002", "100003"]
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002", "100003"])
    assert passed == ["100001"]
    assert rejected.get("100002") == "upper_limit"
    assert rejected.get("100003") == "upper_limit"
    print(f"[PASS] UF-3: 상한가 +29.5% 이상 제외 → 통과 {passed}")


def test_UF_4_52주_고점_30p_초과_하락_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 100_000_000_000),
            ("100002", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 1.0, 50_000_000_000),  # 종가 5000원, 고점 6000원 → 16% 하락 (통과)
            ("100002", 5000.0, 1.0, 50_000_000_000),  # 종가 5000원, 고점 10000원 → 50% 하락 (탈락)
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_year_ohlcv_df([6000.0, 5500.0, 5000.0]),
            "100002": _make_year_ohlcv_df([10000.0, 8000.0, 5000.0]),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    assert passed == ["100001"]
    assert rejected.get("100002") == "below_52w_high_drop"
    print(f"[PASS] UF-4: 52주 고점 -30% 초과 하락 제외 → {passed}")


def test_UF_5_20일_평균_거래대금_50억_미만_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 100_000_000_000),
            ("100002", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 1.0, 100_000_000_000),  # 당일 1000억 ✅
            ("100002", 5000.0, 1.0, 100_000_000_000),  # 당일 1000억 ✅
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_recent_ohlcv_df([60_000_000_000] * 20),  # 20일 평균 600억 ✅
            "100002": _make_recent_ohlcv_df([3_000_000_000] * 20),   # 20일 평균 30억 ❌ (< 50억)
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_liquidity_filters(["100001", "100002"])
    assert passed == ["100001"]
    assert rejected.get("100002") == "avg_value_20d_too_low"
    print(f"[PASS] UF-5: 20일 평균 거래대금 50억 미만 제외 → {passed}")


def test_UF_6_당일_거래대금_100억_미만_제외():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 100_000_000_000),
            ("100002", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 1.0, 50_000_000_000),  # 당일 500억 ✅
            ("100002", 5000.0, 1.0, 5_000_000_000),    # 당일 50억 ❌ (< 100억)
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_recent_ohlcv_df([60_000_000_000] * 20),
            "100002": _make_recent_ohlcv_df([60_000_000_000] * 20),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_liquidity_filters(["100001", "100002"])
    assert passed == ["100001"]
    assert rejected.get("100002") == "today_value_too_low"
    print(f"[PASS] UF-6: 당일 거래대금 100억 미만 제외 → {passed}")


def test_UF_7_모두_통과():
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 200_000_000_000),
            ("100002", 300_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 8000.0, 5.0, 200_000_000_000),
            ("100002", 9000.0, 3.0, 150_000_000_000),
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_year_ohlcv_df([9000.0]),  # 종가/고점 = 8000/9000 → 11% (통과)
            "100002": _make_year_ohlcv_df([10000.0]), # 종가/고점 = 9000/10000 → 10% (통과)
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    assert passed == ["100001", "100002"], f"UF-7 FAIL: {passed}"
    assert rejected == {}, f"UF-7 FAIL: rejected {rejected}"
    print(f"[PASS] UF-7: 모두 통과 → rejected 빈 dict")


def test_UF_8_여러_필터_위반_첫_위반만_기록():
    """시총 미달 + 주가 미달 동시 위반 시 시총만 사유 기록 (먼저 검사된 항목)."""
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [("100001", 30_000_000_000)], "KOSDAQ": []},  # 300억 ❌
        ohlcv_data={"KOSPI": [
            ("100001", 500.0, 35.0, 50_000_000_000),  # 주가 500원 + 35% 모두 ❌
        ], "KOSDAQ": []},
        by_date_data={"100001": _make_year_ohlcv_df([10000.0])},
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001"])
    assert passed == []
    # 시총이 첫 검사 → market_cap_too_low 만 기록
    assert rejected.get("100001") == "market_cap_too_low", f"UF-8 FAIL: {rejected}"
    print(f"[PASS] UF-8: 여러 위반 시 첫 위반 사유만 기록 → {rejected}")


def test_UF_9_pykrx_호출_실패_graceful():
    """pykrx import/호출 모두 실패 시 → 모든 종목 graceful pass (필터 미적용)."""
    uf.reset_cache()
    fake_krx = MagicMock()
    fake_krx.get_market_cap_by_ticker.side_effect = RuntimeError("krx 차단")
    fake_krx.get_market_ohlcv_by_ticker.side_effect = RuntimeError("krx 차단")
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    # market_data 비어있음 → 모든 종목 "data_not_found" rejected
    assert passed == []
    assert all(r == "data_not_found" for r in rejected.values())
    print(f"[PASS] UF-9: pykrx 실패 graceful → 모두 'data_not_found' 처리")


def test_UF_10_통합_함수_get_universe_v2_filtered():
    """get_universe_v2_filtered = v2 산출 + 필터 적용."""
    upv2.reset_cache()
    uf.reset_cache()
    # v2 출처는 "100001", "100002" 산출하고
    # 필터에서 100002 만 탈락하도록 mock
    fake_apply = MagicMock(return_value=(["100001"], {"100002": "market_cap_too_low"}))
    with patch.multiple(
        upv2,
        _fetch_theme_codes_v2=MagicMock(return_value=["100001", "100002"]),
        _fetch_top_value_codes=MagicMock(return_value=[]),
        _fetch_top_change_codes=MagicMock(return_value=[]),
        _fetch_top_foreign_buy_codes=MagicMock(return_value=[]),
        _fetch_swing_holdings=MagicMock(return_value=set()),
    ), patch(
        "closing_bet_system.collectors.universe_filters.apply_all_filters",
        fake_apply,
    ):
        final = upv2.get_universe_v2_filtered()
    assert final == ["100001"]
    fake_apply.assert_called_once()
    print(f"[PASS] UF-10: 통합 함수 → v2 + 필터 = {final}")


def test_UF_11_rejected_dict_형식():
    """rejected 사유 dict는 {ticker: str_reason} 형식."""
    uf.reset_cache()
    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": [
            ("100001", 30_000_000_000),  # 시총 ❌
            ("100002", 100_000_000_000),
        ], "KOSDAQ": []},
        ohlcv_data={"KOSPI": [
            ("100001", 5000.0, 1.0, 50_000_000_000),
            ("100002", 800.0, 1.0, 50_000_000_000),  # 주가 ❌
        ], "KOSDAQ": []},
        by_date_data={
            "100001": _make_year_ohlcv_df([6000.0]),
            "100002": _make_year_ohlcv_df([1000.0]),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(["100001", "100002"])
    assert isinstance(rejected, dict)
    for k, v in rejected.items():
        assert isinstance(k, str) and len(k) == 6 and k.isdigit()
        assert isinstance(v, str) and len(v) > 0
    print(f"[PASS] UF-11: rejected dict 형식 검증 → {rejected}")


def test_UF_12_정상_분포_통과율():
    """50종목 시뮬에서 통과율 50~90% 분포 확인 (자연 분포)."""
    uf.reset_cache()
    # 50종목: 30개는 통과 / 20개는 다양한 사유 탈락
    cap_kospi = [(f"10{i:04d}", 100_000_000_000) for i in range(30)]  # 시총 OK (통과 후보)
    cap_kospi += [(f"20{i:04d}", 30_000_000_000) for i in range(10)]   # 시총 미달
    cap_kospi += [(f"30{i:04d}", 100_000_000_000) for i in range(10)]  # 시총 OK (다른 필터 탈락)

    ohlcv_kospi = [(f"10{i:04d}", 5000.0, 1.0, 50_000_000_000) for i in range(30)]
    ohlcv_kospi += [(f"20{i:04d}", 5000.0, 1.0, 50_000_000_000) for i in range(10)]
    # 30군: 5개 주가 미달, 5개 상한가
    ohlcv_kospi += [(f"30{i:04d}", 500.0, 1.0, 50_000_000_000) for i in range(5)]
    ohlcv_kospi += [(f"30{i+5:04d}", 5000.0, 30.0, 50_000_000_000) for i in range(5)]

    fake_krx = _build_fake_krx(
        cap_data={"KOSPI": cap_kospi, "KOSDAQ": []},
        ohlcv_data={"KOSPI": ohlcv_kospi, "KOSDAQ": []},
        by_date_data={
            t: _make_year_ohlcv_df([6000.0])
            for t in [t for t, _ in cap_kospi]
        },
    )
    tickers = [t for t, _ in cap_kospi]
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        passed, rejected = uf.apply_attribute_filters(tickers)
    pass_rate = len(passed) / len(tickers)
    assert 0.5 <= pass_rate <= 0.9, f"UF-12 FAIL: 통과율 {pass_rate:.1%}"
    print(f"[PASS] UF-12: 50종목 통과율 {pass_rate:.1%} (50~90% 자연 분포)")


def test_UF_13_빈_입력():
    uf.reset_cache()
    passed, rejected = uf.apply_attribute_filters([])
    assert passed == [] and rejected == {}
    passed, rejected = uf.apply_liquidity_filters([])
    assert passed == [] and rejected == {}
    passed, rejected = uf.apply_all_filters([])
    assert passed == [] and rejected == {}
    print("[PASS] UF-13: 빈 입력 → 빈 결과")


def test_UF_14_settings_로드_실패_default_폴백():
    """settings.yaml 로드 실패 시 default 임계값 사용."""
    uf.reset_cache()
    with patch(
        "closing_bet_system.storage.db._load_settings",
        side_effect=RuntimeError("yaml 손상"),
    ):
        cfg = uf._load_filter_config()
    # default 값으로 폴백
    assert cfg["min_market_cap"] == uf.DEFAULT_MIN_MARKET_CAP
    assert cfg["min_price"] == uf.DEFAULT_MIN_PRICE
    assert cfg["max_drop_from_52w_high"] == uf.DEFAULT_MAX_DROP_FROM_52W_HIGH
    assert cfg["min_avg_value_20d"] == uf.DEFAULT_MIN_AVG_VALUE_20D
    assert cfg["min_today_value"] == uf.DEFAULT_MIN_TODAY_VALUE
    print(f"[PASS] UF-14: settings.yaml 실패 → default 폴백 (min_cap={cfg['min_market_cap']})")


def test_UF_15_통합_함수_필터_import_실패_graceful():
    """get_universe_v2_filtered: 필터 import 실패 시 unfiltered universe 반환."""
    upv2.reset_cache()
    uf.reset_cache()
    with patch.multiple(
        upv2,
        _fetch_theme_codes_v2=MagicMock(return_value=["100001", "100002"]),
        _fetch_top_value_codes=MagicMock(return_value=[]),
        _fetch_top_change_codes=MagicMock(return_value=[]),
        _fetch_top_foreign_buy_codes=MagicMock(return_value=[]),
        _fetch_swing_holdings=MagicMock(return_value=set()),
    ), patch(
        "closing_bet_system.collectors.universe_filters.apply_all_filters",
        side_effect=ImportError("filters 모듈 차단"),
    ):
        final = upv2.get_universe_v2_filtered()
    assert final == ["100001", "100002"], f"UF-15 FAIL: {final}"
    print(f"[PASS] UF-15: 필터 import 실패 → unfiltered {final}")


if __name__ == "__main__":
    print("=" * 70)
    print("단위 2-9b 검증: universe_filters (PRD 4-1 + 4-4 하드 필터)")
    print("=" * 70)
    test_UF_1_시총_500억_미만_제외()
    test_UF_2_주가_1000원_미만_제외()
    test_UF_3_상한가_29p5_이상_제외()
    test_UF_4_52주_고점_30p_초과_하락_제외()
    test_UF_5_20일_평균_거래대금_50억_미만_제외()
    test_UF_6_당일_거래대금_100억_미만_제외()
    test_UF_7_모두_통과()
    test_UF_8_여러_필터_위반_첫_위반만_기록()
    test_UF_9_pykrx_호출_실패_graceful()
    test_UF_10_통합_함수_get_universe_v2_filtered()
    test_UF_11_rejected_dict_형식()
    test_UF_12_정상_분포_통과율()
    test_UF_13_빈_입력()
    test_UF_14_settings_로드_실패_default_폴백()
    test_UF_15_통합_함수_필터_import_실패_graceful()
    print("\n" + "=" * 70)
    print("✅ 단위 2-9b 15 시나리오 모두 PASS (요구 12+ 충족)")
    print("=" * 70)
