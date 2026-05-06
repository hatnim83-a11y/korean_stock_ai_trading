"""단위 2-9c 검증: universe_filters KRX bulk 우회 (작업 G).

배경:
    KRX 사이트 정책 변경으로 pykrx ``get_market_*_by_ticker`` (시장 단위 bulk)가
    빈 응답 반환. 종목별 ``get_market_ohlcv_by_date`` 는 정상 작동.
    본 단위는 bulk 빈 응답 시 종목별 by_date 폴백으로 우회.

시나리오 (10):
    UF-C-1:  bulk 빈 응답 + tickers 전달 → 폴백 진입, close/change/value 채워짐 (시총 None)
    UF-C-2:  시총 None → apply_attribute_filters 가 data_not_found 보수 탈락 (옵션 A 정합)
    UF-C-3:  토글 false + bulk 빈 응답 → 폴백 진입 안 함, 모두 data_not_found
    UF-C-4:  bulk 정상 + tickers 전달 → 폴백 호출 안 됨 (회귀 안전)
    UF-C-5:  폴백 후 캐시 히트 — 두 번째 호출 시 추가 외부 호출 0회
    UF-C-6:  tickers=None + bulk 빈 응답 → 기존 동작 유지 (회귀)
    UF-C-7:  폴백 종목별 1건 RuntimeError → graceful 격리, 다른 종목 진행
    UF-C-8:  MAX_FALLBACK_TICKERS=100 초과 입력 → 100건만 폴백 호출
    UF-C-9:  settings.yaml `data_source` 미존재 → default true 폴백
    UF-C-10: KIND severity 사전 제외 후 survivors 만 폴백 호출 (PRD 4-1 정합)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import universe_filters as uf


# ===== Fixtures =====


def _make_today_ohlcv_df(close: float, change_rate: float, trading_value: float) -> pd.DataFrame:
    """1일치 OHLCV (1행) — 폴백 by_date(today, today, ticker) 응답 모킹."""
    return pd.DataFrame(
        {
            "종가": [close],
            "등락률": [change_rate],
            "거래대금": [trading_value],
        }
    )


def _build_fake_krx_2_9c(
    *,
    bulk_empty: bool = True,
    cap_rows: list[tuple[str, float]] = None,
    ohlcv_rows: list[tuple[str, float, float, float]] = None,
    by_date_today: dict[str, pd.DataFrame] = None,
    by_date_lookback: dict[str, pd.DataFrame] = None,
    raise_on_tickers: set[str] = None,
):
    """폴백 시나리오용 fake KRX 빌더.

    Args:
        bulk_empty: True 시 ``get_market_*_by_ticker`` 모두 빈 DataFrame 반환 (KRX 차단 시뮬)
        cap_rows / ohlcv_rows: bulk 정상 시뮬용 (bulk_empty=False 시 사용)
        by_date_today: ``{ticker: 1일치 DataFrame}`` — 폴백 호출 응답
        by_date_lookback: ``{ticker: 1년/30일치 DataFrame}`` — 52주/20일 평균 호출 응답
        raise_on_tickers: 폴백 호출 시 RuntimeError 발생시킬 종목 집합
    """
    fake_krx = MagicMock()
    raise_on_tickers = raise_on_tickers or set()

    def _fake_cap(date, market):
        if bulk_empty:
            return pd.DataFrame()
        rows = [r for r in (cap_rows or [])]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame({"시가총액": [v for _, v in rows]}, index=[t for t, _ in rows])

    def _fake_ohlcv_bulk(date, market):
        if bulk_empty:
            return pd.DataFrame()
        rows = [r for r in (ohlcv_rows or [])]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "종가": [r[1] for r in rows],
                "등락률": [r[2] for r in rows],
                "거래대금": [r[3] for r in rows],
            },
            index=[r[0] for r in rows],
        )

    def _fake_by_date(fromdate, todate, ticker):
        if ticker in raise_on_tickers:
            raise RuntimeError(f"simulated KRX failure for {ticker}")
        # today, today 호출(폴백) → by_date_today 응답
        if fromdate == todate and by_date_today is not None and ticker in by_date_today:
            return by_date_today[ticker]
        # 1년치 또는 30일치 호출 → by_date_lookback 응답
        if by_date_lookback is not None and ticker in by_date_lookback:
            return by_date_lookback[ticker]
        return pd.DataFrame()

    fake_krx.get_market_cap_by_ticker.side_effect = _fake_cap
    fake_krx.get_market_ohlcv_by_ticker.side_effect = _fake_ohlcv_bulk
    fake_krx.get_market_ohlcv_by_date.side_effect = _fake_by_date
    return fake_krx


# ===== 시나리오 =====


def test_UF_C_1_bulk_empty_with_tickers_triggers_fallback():
    """bulk 빈 응답 + tickers 전달 → 폴백 진입, close/change/value 채워짐."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
            "100002": _make_today_ohlcv_df(8000.0, -0.5, 80_000_000_000),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            data = uf._fetch_market_data_bulk("20260506", tickers=["100001", "100002"])
    assert "100001" in data and "100002" in data, f"UF-C-1 FAIL: {data}"
    assert data["100001"]["close"] == 5000.0
    assert data["100001"]["change_rate"] == 1.5
    assert data["100001"]["trading_value"] == 50_000_000_000
    assert "market_cap" not in data["100001"], "옵션 A 보수: 시총은 채우지 않음"
    print(f"[PASS] UF-C-1: bulk 빈 응답 + tickers → 폴백 진입 (시총 미보강) → {len(data)}건")


def test_UF_C_2_fallback_market_cap_none_triggers_data_not_found():
    """폴백 결과에 market_cap 키가 없으면 data_not_found 보수 탈락 — 옵션 A 정합 (코더 검토 심각 #1)."""
    # 케이스 1: 폴백 성공 (close/change/value 채워짐) but market_cap 키 부재 → data_not_found 탈락
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            passed, rejected = uf.apply_attribute_filters(["100001"])
    assert passed == [], f"UF-C-2(case1) FAIL: 폴백 시총 None인데 통과: passed={passed}"
    assert rejected.get("100001") == "data_not_found", (
        f"UF-C-2(case1) FAIL: market_cap None에도 data_not_found 아님: {rejected}"
    )

    # 케이스 2: 폴백도 빈 응답 → data 자체 없음 → data_not_found
    fake_krx2 = _build_fake_krx_2_9c(bulk_empty=True, by_date_today={})
    uf.reset_cache()
    with patch.object(uf, "_import_pykrx", return_value=fake_krx2):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            passed2, rejected2 = uf.apply_attribute_filters(["100001"])
    assert passed2 == [], f"UF-C-2(case2) FAIL: {passed2}"
    assert rejected2.get("100001") == "data_not_found", f"UF-C-2(case2) FAIL: {rejected2}"
    print(f"[PASS] UF-C-2: 폴백 시총 없음 / 폴백 빈 응답 모두 data_not_found 보수 탈락 (옵션 A)")


def test_UF_C_3_toggle_false_skips_fallback():
    """settings.yaml fallback_per_ticker_enabled=False → 폴백 진입 안 함."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
        },
    )
    fake_cfg = {
        "min_market_cap": uf.DEFAULT_MIN_MARKET_CAP,
        "min_price": uf.DEFAULT_MIN_PRICE,
        "max_drop_from_52w_high": uf.DEFAULT_MAX_DROP_FROM_52W_HIGH,
        "exclude_upper_limit": True,
        "min_avg_value_20d": uf.DEFAULT_MIN_AVG_VALUE_20D,
        "min_today_value": uf.DEFAULT_MIN_TODAY_VALUE,
        "fallback_per_ticker_enabled": False,  # 토글 OFF
    }
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "_load_filter_config", return_value=fake_cfg):
            data = uf._fetch_market_data_bulk("20260506", tickers=["100001"])
    assert data == {}, f"UF-C-3 FAIL: 폴백 진입했으면 data 채워짐: {data}"
    # 폴백 호출이 시도되지 않았는지 확인 (by_date 호출 0회)
    assert fake_krx.get_market_ohlcv_by_date.call_count == 0
    print(f"[PASS] UF-C-3: 토글 OFF → 폴백 진입 안 함, by_date 호출 0회")


def test_UF_C_4_bulk_normal_skips_fallback():
    """bulk 정상 + tickers 전달 → 폴백 호출 안 됨 (회귀 안전)."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=False,
        cap_rows=[("100001", 100_000_000_000)],
        ohlcv_rows=[("100001", 5000.0, 1.0, 50_000_000_000)],
        by_date_today={
            "100001": _make_today_ohlcv_df(9999.9, 9.9, 9_999_999_999),  # 폴백 들어가면 이 값으로 오염
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        data = uf._fetch_market_data_bulk("20260506", tickers=["100001"])
    assert data["100001"]["close"] == 5000.0, f"UF-C-4 FAIL: 폴백 값으로 오염됨: {data}"
    assert data["100001"]["market_cap"] == 100_000_000_000
    # 폴백 호출 시도 0회 검증 (오직 bulk 만 사용)
    by_date_calls = [
        c for c in fake_krx.get_market_ohlcv_by_date.call_args_list
        if c.args[0] == c.args[1]  # today_str, today_str (폴백)
    ]
    assert len(by_date_calls) == 0, f"UF-C-4 FAIL: 폴백 호출 발생 ({len(by_date_calls)}회)"
    print(f"[PASS] UF-C-4: bulk 정상 → 폴백 호출 0회 (회귀 안전)")


def test_UF_C_5_fallback_cache_hit_no_extra_calls():
    """폴백 후 같은 거래일 두 번째 호출 → 추가 외부 호출 0회."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
        },
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            data1 = uf._fetch_market_data_bulk("20260506", tickers=["100001"])
            calls_after_first = fake_krx.get_market_ohlcv_by_date.call_count
            data2 = uf._fetch_market_data_bulk("20260506", tickers=["100001"])
            calls_after_second = fake_krx.get_market_ohlcv_by_date.call_count
    assert data1 == data2, f"UF-C-5 FAIL: 캐시 hit 결과 다름"
    assert calls_after_first == calls_after_second, (
        f"UF-C-5 FAIL: 두 번째 호출에서 추가 외부 호출 발생 "
        f"({calls_after_first} → {calls_after_second})"
    )
    print(f"[PASS] UF-C-5: 폴백 캐시 hit → 추가 호출 0회 (총 {calls_after_first}회)")


def test_UF_C_6_tickers_none_preserves_legacy_behavior():
    """tickers=None + bulk 빈 응답 → 기존 동작 유지 (폴백 진입 X, 빈 dict 반환)."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(bulk_empty=True)
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        data = uf._fetch_market_data_bulk("20260506")  # tickers 인자 미제공
    assert data == {}, f"UF-C-6 FAIL: 폴백 진입했음: {data}"
    assert fake_krx.get_market_ohlcv_by_date.call_count == 0, (
        f"UF-C-6 FAIL: 폴백 호출 발생"
    )
    print(f"[PASS] UF-C-6: tickers=None + bulk 빈 응답 → 폴백 진입 X (회귀)")


def test_UF_C_7_fallback_per_ticker_runtime_error_isolated():
    """폴백 호출 1건 RuntimeError → graceful 격리, 다른 종목 진행."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
            "100003": _make_today_ohlcv_df(7000.0, 2.0, 70_000_000_000),
        },
        raise_on_tickers={"100002"},  # 100002 는 RuntimeError
    )
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            data = uf._fetch_market_data_bulk(
                "20260506", tickers=["100001", "100002", "100003"]
            )
    assert "100001" in data and "100003" in data
    assert "100002" not in data, f"UF-C-7 FAIL: 실패 종목이 결과에 포함됨"
    print(f"[PASS] UF-C-7: 1건 RuntimeError → graceful 격리 (성공 {len(data)}/3)")


def test_UF_C_8_fallback_max_tickers_cap():
    """MAX_FALLBACK_TICKERS=100 초과 입력 → 100건만 폴백 호출."""
    uf.reset_cache()
    n_input = 150
    tickers_input = [f"{i:06d}" for i in range(100000, 100000 + n_input)]
    by_date_today = {t: _make_today_ohlcv_df(5000.0, 1.0, 50_000_000_000) for t in tickers_input}
    fake_krx = _build_fake_krx_2_9c(bulk_empty=True, by_date_today=by_date_today)
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            data = uf._fetch_market_data_bulk("20260506", tickers=tickers_input)
    assert len(data) == uf.MAX_FALLBACK_TICKERS, (
        f"UF-C-8 FAIL: 결과 크기 {len(data)} (기대 {uf.MAX_FALLBACK_TICKERS})"
    )
    # 폴백 호출 카운트도 100회로 제한 (today,today 호출만 카운트)
    today_calls = [
        c for c in fake_krx.get_market_ohlcv_by_date.call_args_list
        if c.args[0] == c.args[1]
    ]
    assert len(today_calls) == uf.MAX_FALLBACK_TICKERS, (
        f"UF-C-8 FAIL: 폴백 호출 횟수 {len(today_calls)}"
    )
    print(f"[PASS] UF-C-8: 150건 입력 → {len(data)}건만 폴백 (상한 정합)")


def test_UF_C_9_settings_data_source_missing_defaults_to_true():
    """settings.yaml `data_source` 미존재 → default true 폴백 진입."""
    uf.reset_cache()
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today={
            "100001": _make_today_ohlcv_df(5000.0, 1.5, 50_000_000_000),
        },
    )
    # _load_settings 가 data_source 키 없는 dict 반환 시뮬
    with patch("closing_bet_system.storage.db._load_settings", return_value={}):
        cfg = uf._load_filter_config()
    assert cfg["fallback_per_ticker_enabled"] is True, (
        f"UF-C-9 FAIL: default True 어긴 cfg: {cfg}"
    )
    # 실제 호출도 검증
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch("closing_bet_system.storage.db._load_settings", return_value={}):
            with patch.object(uf, "time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                data = uf._fetch_market_data_bulk("20260506", tickers=["100001"])
    assert "100001" in data, f"UF-C-9 FAIL: default True 폴백 미진입: {data}"
    print(f"[PASS] UF-C-9: data_source 미존재 → default True → 폴백 진입")


def test_UF_C_10_kind_severity_pre_exclude_then_fallback_survivors_only():
    """KIND severity 사전 제외 후 survivors 만 폴백 호출 (PRD 4-1 정합)."""
    uf.reset_cache()
    by_date_today = {
        "100001": _make_today_ohlcv_df(5000.0, 1.0, 50_000_000_000),
        "100003": _make_today_ohlcv_df(8000.0, 2.0, 80_000_000_000),
    }
    fake_krx = _build_fake_krx_2_9c(
        bulk_empty=True,
        by_date_today=by_date_today,
        by_date_lookback={
            "100001": pd.DataFrame({"고가": [6000.0], "거래대금": [50_000_000_000]}),
            "100003": pd.DataFrame({"고가": [9000.0], "거래대금": [80_000_000_000]}),
        },
    )
    severity_map = {"100002": 3}  # 100002 는 관리종목 사전 제외 대상
    with patch.object(uf, "_import_pykrx", return_value=fake_krx):
        with patch.object(uf, "time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            passed, rejected = uf.apply_attribute_filters(
                ["100001", "100002", "100003"],
                severity_map=severity_map,
            )
    assert rejected.get("100002") == "kind_severity_3", (
        f"UF-C-10 FAIL: KIND 사전 제외 안 됨: {rejected}"
    )
    # 폴백은 survivors(100001, 100003)에 대해서만 호출 — 100002 는 호출 X
    today_call_tickers = [
        c.args[2] for c in fake_krx.get_market_ohlcv_by_date.call_args_list
        if c.args[0] == c.args[1]
    ]
    assert "100002" not in today_call_tickers, (
        f"UF-C-10 FAIL: KIND 사전 제외 종목까지 폴백 호출됨: {today_call_tickers}"
    )
    assert "100001" in today_call_tickers and "100003" in today_call_tickers
    print(f"[PASS] UF-C-10: KIND severity 사전 제외 후 survivors {today_call_tickers} 만 폴백")


# ===== Runner =====


if __name__ == "__main__":
    tests = [
        test_UF_C_1_bulk_empty_with_tickers_triggers_fallback,
        test_UF_C_2_fallback_market_cap_none_triggers_data_not_found,
        test_UF_C_3_toggle_false_skips_fallback,
        test_UF_C_4_bulk_normal_skips_fallback,
        test_UF_C_5_fallback_cache_hit_no_extra_calls,
        test_UF_C_6_tickers_none_preserves_legacy_behavior,
        test_UF_C_7_fallback_per_ticker_runtime_error_isolated,
        test_UF_C_8_fallback_max_tickers_cap,
        test_UF_C_9_settings_data_source_missing_defaults_to_true,
        test_UF_C_10_kind_severity_pre_exclude_then_fallback_survivors_only,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    total = len(tests)
    print(f"\n{'=' * 60}")
    print(f"총 {total}건 / 성공 {total - failed} / 실패 {failed}")
    sys.exit(0 if failed == 0 else 1)
