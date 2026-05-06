"""단위 2-9e 검증: 종가베팅 universe v2 시총 보강 정규화 (옵션 A).

배경: 단위 2-9d (commit b7ae572) 후 universe 출처 2~4 KIS Open API 라우팅 완성
(theme=17 / top_value=25 / top_foreign=21 = 63종목), 그러나 시총 보강 OFF로
universe = 0건. 본 단위는 KIS `ranking/market-cap` 응답 `stck_avls` 컬럼을
× 100,000,000 (억원 → 원) 정규화하고 fallback_include_market_cap 토글을 ON
으로 전환해 시총 보강을 활성화한다.

사전 조사 (probe_kis_unit_2_9e.py 검증):
- 005930 stck_avls=15,551,101 × 1억 = 1,555,110,100,000,000원
- 검증 1: stck_prpr × lstn_stcn = 1,554,909,948,728,000원 (일치)
- 검증 2: inquire-price market_cap = 1,555,110,100,000,000원 (일치)

시나리오 (11):
    MC-1:   stck_avls=15551101 → market_cap=1,555,110,100,000,000원 (정규화 정확)
    MC-1b:  isinstance(entry["market_cap"], int) — 타입 검증
    MC-2:   stck_avls=0 → entry["market_cap"] 키 미생성 (mcap > 0 가드)
    MC-3:   비-6자리 코드 혼입 → 정규식 필터링 정상
    MC-4:   fallback_include_market_cap=true → universe_filters 시총 보강 적용
    MC-5:   상위 200 외 종목 → market_cap 미매치 → 옵션 A 보수 탈락 유지
    MC-6a:  정규화 적용 → 600억 → min_market_cap=500억 PASS
    MC-6b:  정규화 누락 회귀 시뮬 → 600 → 500억 미달 FAIL
    MC-7:   fallback_include_market_cap=false → 옵션 A 보수 (시총 보강 호출 X)
    MC-7b:  _load_filter_config 예외 → DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=False 안전망
    MC-8:   NaN/inf/빈 문자열 시총 → _safe_float None → entry["market_cap"] 키 미생성
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd  # MC-4/5/7/7b 시총 보강 통합 테스트용

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import kis_market_provider as kmp
from closing_bet_system.collectors import universe_filters as uf


# ===== Fixtures =====


def _build_fake_kis_api(*, response_data=None, raise_on_get: bool = False):
    """KISMarketProvider 가 위임하는 KISApi 의 내부 메서드를 mock."""
    fake_kis = MagicMock()
    fake_kis._rate_limit = MagicMock()
    fake_kis.base_url = "https://openapi.koreainvestment.com:9443"
    fake_kis._get_headers = MagicMock(return_value={"authorization": "Bearer mock"})

    fake_response = MagicMock()
    if raise_on_get:
        fake_kis.client.get = MagicMock(side_effect=RuntimeError("simulated failure"))
    else:
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(return_value=response_data or {})
        fake_kis.client.get = MagicMock(return_value=fake_response)
    return fake_kis


def _make_market_cap_response(items: list[dict]) -> dict:
    """KIS market-cap ranking 정상 응답 (rt_cd=0, output 리스트)."""
    return {"rt_cd": "0", "msg1": "정상처리", "output": items}


# ===== MC-1: 핵심 단위 정규화 정확성 =====


def test_MC_1_단위_정규화_정확성():
    """stck_avls=15551101 → market_cap=1,555,110,100,000,000원."""
    kmp.reset_singleton()
    items = [{
        "mksc_shrn_iscd": "005930",
        "hts_kor_isnm": "삼성전자",
        "stck_prpr": "266000",
        "prdy_ctrt": "14.41",
        "stck_avls": "15551101",
    }]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    assert "005930" in result, f"MC-1 FAIL: {result}"
    expected = 1_555_110_100_000_000  # 15,551,101 × 100,000,000
    actual = result["005930"]["market_cap"]
    assert actual == expected, (
        f"MC-1 FAIL: expected={expected}, actual={actual} "
        f"(× 1억 정규화 미적용)"
    )
    print(f"[PASS] MC-1: 005930 raw=15,551,101 × 1억 → {actual:,}원 (= 1,555조원)")


# ===== MC-1b: int 타입 검증 =====


def test_MC_1b_int_타입_검증():
    """`int(round(...))` 캐스팅 → market_cap 은 int 타입."""
    kmp.reset_singleton()
    items = [{
        "mksc_shrn_iscd": "000660",
        "stck_prpr": "1601000",
        "stck_avls": "11410365",
    }]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    mc = result["000660"]["market_cap"]
    assert isinstance(mc, int), f"MC-1b FAIL: type={type(mc).__name__} (int 기대)"
    print(f"[PASS] MC-1b: market_cap type=int (= {mc:,}원)")


# ===== MC-2: mcap=0 가드 =====


def test_MC_2_mcap_zero_guard():
    """stck_avls=0 → entry["market_cap"] 키 미생성 (mcap > 0 가드)."""
    kmp.reset_singleton()
    items = [
        {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "0"},
        {"mksc_shrn_iscd": "000660", "stck_prpr": "1601000", "stck_avls": "11410365"},
    ]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    # 005930 은 mcap=0 → market_cap 키 미생성 (단, close 키는 생성)
    assert "005930" in result, f"MC-2 FAIL: 005930 없음 — {result}"
    assert "market_cap" not in result["005930"], (
        f"MC-2 FAIL: mcap=0 인데 market_cap 키 생성됨: {result['005930']}"
    )
    # 000660 은 정상
    assert result["000660"]["market_cap"] == 1_141_036_500_000_000
    print(f"[PASS] MC-2: mcap=0 → market_cap 키 미생성, 정상 종목은 정규화 적용")


# ===== MC-3: 비-6자리 코드 정규식 필터링 =====


def test_MC_3_invalid_ticker_filter():
    """비-6자리 코드 혼입 → 정규식 필터링."""
    kmp.reset_singleton()
    items = [
        {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "15551101"},
        {"mksc_shrn_iscd": "ABCDEF", "stck_prpr": "1000", "stck_avls": "100"},  # 영문 → 제외
        {"mksc_shrn_iscd": "12345", "stck_prpr": "1000", "stck_avls": "100"},   # 5자리 → 제외
        {"mksc_shrn_iscd": "000660", "stck_prpr": "1601000", "stck_avls": "11410365"},
        {"mksc_shrn_iscd": "", "stck_prpr": "1000", "stck_avls": "100"},        # 빈 문자열 → 제외
    ]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    assert set(result.keys()) == {"005930", "000660"}, (
        f"MC-3 FAIL: {set(result.keys())}"
    )
    print(f"[PASS] MC-3: 비-6자리 정규식 필터링 → {sorted(result.keys())}")


# ===== MC-4: 시총 보강 통합 (universe_filters) =====


def test_MC_4_시총_보강_통합():
    """fallback_include_market_cap=true → universe_filters 가 KIS market-cap 호출."""
    uf.reset_cache()
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_market_cap_response([
            {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "15551101"},
        ])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)

    fake_pykrx_krx = MagicMock()
    fake_pykrx_krx.get_market_cap_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    with patch.object(uf, "_import_pykrx", return_value=fake_pykrx_krx):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(uf, "_load_filter_config", return_value={
                "fallback_per_ticker_enabled": True,
                "fallback_include_market_cap": True,  # 단위 2-9e 활성
                "kis_market_cap_top_n": 200,
            }):
                with patch.object(uf, "time") as mock_time:
                    mock_time.sleep = MagicMock()
                    mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                    data = uf._fetch_market_data_bulk("20260507", tickers=["005930"])
    assert "005930" in data, f"MC-4 FAIL: {data}"
    assert data["005930"].get("market_cap") == 1_555_110_100_000_000, (
        f"MC-4 FAIL: market_cap={data['005930'].get('market_cap')}"
    )
    print(f"[PASS] MC-4: universe_filters 시총 보강 → {data['005930']['market_cap']:,}원")


# ===== MC-5: 상위 200 외 종목 옵션 A 보수 탈락 =====


def test_MC_5_상위200_외_종목_보수_탈락():
    """KIS market-cap top 200 미포함 종목 → market_cap 키 없음 → 옵션 A 보수."""
    uf.reset_cache()
    kmp.reset_singleton()
    # KIS market-cap 응답에는 005930 만 포함 (상위 200), 999999 는 미포함 (소형주 시뮬)
    fake_kis = _build_fake_kis_api(
        response_data=_make_market_cap_response([
            {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "15551101"},
        ])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)

    fake_pykrx_krx = MagicMock()
    fake_pykrx_krx.get_market_cap_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    with patch.object(uf, "_import_pykrx", return_value=fake_pykrx_krx):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(uf, "_load_filter_config", return_value={
                "fallback_per_ticker_enabled": True,
                "fallback_include_market_cap": True,
                "kis_market_cap_top_n": 200,
            }):
                with patch.object(uf, "time") as mock_time:
                    mock_time.sleep = MagicMock()
                    mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                    # candidates: 005930 (상위 200) + 999999 (상위 200 외)
                    data = uf._fetch_market_data_bulk("20260507", tickers=["005930", "999999"])
    # 005930 은 시총 보강 성공
    assert data.get("005930", {}).get("market_cap") == 1_555_110_100_000_000
    # 999999 는 보강 실패 → market_cap 키 없음 또는 None (옵션 A 보수)
    mc_999 = data.get("999999", {}).get("market_cap")
    assert mc_999 is None, f"MC-5 FAIL: 상위 200 외 종목 시총 보강됨: {mc_999}"
    print(f"[PASS] MC-5: 상위 200 외 (999999) → market_cap=None (옵션 A 보수)")


# ===== MC-6a: 정규화 적용 PASS =====


def test_MC_6a_정규화_적용_PASS():
    """정규화 적용 → 600억 stck_avls=600 → market_cap=60_000_000_000 → 500억 PASS."""
    kmp.reset_singleton()
    items = [{
        "mksc_shrn_iscd": "005930",
        "stck_prpr": "70000",
        "stck_avls": "600",  # 600억원 (= 60_000_000_000원 정규화 후)
    }]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    mc = result["005930"]["market_cap"]
    assert mc == 60_000_000_000, f"MC-6a FAIL: market_cap={mc:,}"
    # 500억 임계 PASS 검증
    assert mc >= 50_000_000_000, f"MC-6a FAIL: 500억 미달 ({mc:,})"
    print(f"[PASS] MC-6a: 정규화 적용 → {mc:,}원 ≥ 500억 PASS")


# ===== MC-6b: 정규화 누락 회귀 FAIL =====


def test_MC_6b_정규화_누락_회귀_FAIL():
    """정규화 미적용 시 stck_avls=600 → market_cap=600 → 500억 FAIL (정규화 누락 회귀 검출)."""
    # 정규화 미적용 시뮬: _STCK_AVLS_UNIT_TO_WON 을 1로 patch
    kmp.reset_singleton()
    items = [{
        "mksc_shrn_iscd": "005930",
        "stck_prpr": "70000",
        "stck_avls": "600",
    }]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)

    with patch.object(kmp, "_STCK_AVLS_UNIT_TO_WON", 1):  # 정규화 누락 시뮬
        result = provider.get_top_market_cap_data(top_n=10)
    mc = result["005930"]["market_cap"]
    assert mc == 600, f"MC-6b FAIL: 정규화 미적용 시뮬 결과 {mc} (기대 600)"
    # 500억 임계 FAIL 검증 — 정규화 누락 시 모든 종목 탈락
    assert mc < 50_000_000_000, (
        f"MC-6b FAIL: 정규화 누락 시뮬에서도 500억 통과 — 회귀 검출 실패"
    )
    print(f"[PASS] MC-6b: 정규화 누락 시뮬 → {mc} < 500억 FAIL (회귀 검출 동작)")


# ===== MC-7: 토글 OFF 회귀 =====


def test_MC_7_토글_OFF_회귀():
    """fallback_include_market_cap=false → KIS market-cap 호출 X (단위 2-9d 동작)."""
    uf.reset_cache()
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_market_cap_response([
            {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "15551101"},
        ])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)
    # KIS market-cap 호출 카운트 추적
    fake_provider.get_top_market_cap_data = MagicMock(
        wraps=fake_provider.get_top_market_cap_data
    )

    fake_pykrx_krx = MagicMock()
    fake_pykrx_krx.get_market_cap_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    with patch.object(uf, "_import_pykrx", return_value=fake_pykrx_krx):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(uf, "_load_filter_config", return_value={
                "fallback_per_ticker_enabled": True,
                "fallback_include_market_cap": False,  # 단위 2-9d 동작 회귀
                "kis_market_cap_top_n": 200,
            }):
                with patch.object(uf, "time") as mock_time:
                    mock_time.sleep = MagicMock()
                    mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                    data = uf._fetch_market_data_bulk("20260507", tickers=["005930"])
    # KIS market-cap 호출 0회 검증
    assert fake_provider.get_top_market_cap_data.call_count == 0, (
        f"MC-7 FAIL: 토글 OFF 인데 market-cap 호출 {fake_provider.get_top_market_cap_data.call_count}회"
    )
    # 005930 은 market_cap 키 없음 (옵션 A 보수)
    mc = data.get("005930", {}).get("market_cap")
    assert mc is None, f"MC-7 FAIL: 토글 OFF 인데 market_cap 보강됨: {mc}"
    print(f"[PASS] MC-7: 토글 OFF → KIS market-cap 호출 0회, 옵션 A 보수")


# ===== MC-7b: settings 로드 실패 안전망 =====


def test_MC_7b_settings_로드_실패_안전망():
    """_load_filter_config 예외 → DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=False 분기 (Step 2 변경 검증)."""
    # DEFAULT 값이 False 임을 명시 검증
    assert uf.DEFAULT_FALLBACK_INCLUDE_MARKET_CAP is False, (
        f"MC-7b FAIL: DEFAULT_FALLBACK_INCLUDE_MARKET_CAP={uf.DEFAULT_FALLBACK_INCLUDE_MARKET_CAP} "
        f"(False 기대 — 단위 2-9e Step 2 변경)"
    )

    # _load_filter_config 예외 시 cfg fallback → DEFAULT False 적용 검증
    uf.reset_cache()
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_market_cap_response([
            {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": "15551101"},
        ])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)
    fake_provider.get_top_market_cap_data = MagicMock(
        wraps=fake_provider.get_top_market_cap_data
    )

    fake_pykrx_krx = MagicMock()
    fake_pykrx_krx.get_market_cap_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    with patch.object(uf, "_import_pykrx", return_value=fake_pykrx_krx):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            # _load_filter_config 가 예외 던짐 → cfg fallback 분기 진입
            with patch.object(
                uf, "_load_filter_config",
                side_effect=RuntimeError("simulated settings load failure"),
            ):
                with patch.object(uf, "time") as mock_time:
                    mock_time.sleep = MagicMock()
                    mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                    data = uf._fetch_market_data_bulk("20260507", tickers=["005930"])
    # DEFAULT=False 라 market-cap 호출 0회
    assert fake_provider.get_top_market_cap_data.call_count == 0, (
        f"MC-7b FAIL: settings 실패 시 DEFAULT=False 인데 market-cap 호출됨 "
        f"({fake_provider.get_top_market_cap_data.call_count}회)"
    )
    print(f"[PASS] MC-7b: settings 로드 실패 → DEFAULT=False 분기 → 옵션 A 보수")


# ===== MC-8: NaN/inf/빈 문자열 가드 =====


def test_MC_8_invalid_value_guard():
    """stck_avls=빈 문자열/'-'/'N/A' → _safe_float None → entry["market_cap"] 키 미생성."""
    kmp.reset_singleton()
    items = [
        {"mksc_shrn_iscd": "005930", "stck_prpr": "266000", "stck_avls": ""},     # 빈 문자열
        {"mksc_shrn_iscd": "000660", "stck_prpr": "1601000", "stck_avls": "-"},   # 비숫자
        {"mksc_shrn_iscd": "035420", "stck_prpr": "200000", "stck_avls": "N/A"},  # 비숫자
        {"mksc_shrn_iscd": "005380", "stck_prpr": "550000", "stck_avls": "1126168"},  # 정상
    ]
    fake_kis = _build_fake_kis_api(response_data=_make_market_cap_response(items))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=10)
    # 비정상 값은 mcap=0 (default) → market_cap 키 미생성, close 키만
    for code in ("005930", "000660", "035420"):
        assert code in result, f"MC-8 FAIL: {code} 누락 — {result}"
        assert "market_cap" not in result[code], (
            f"MC-8 FAIL: {code} 비정상 stck_avls 인데 market_cap 키 생성됨: {result[code]}"
        )
    # 005380 은 정상
    expected = 112_616_800_000_000  # 1,126,168 × 1억
    assert result["005380"]["market_cap"] == expected
    print(f"[PASS] MC-8: 비정상 stck_avls (빈 문자열/'-'/'N/A') → market_cap 키 미생성")


# ===== Runner =====


if __name__ == "__main__":
    tests = [
        test_MC_1_단위_정규화_정확성,
        test_MC_1b_int_타입_검증,
        test_MC_2_mcap_zero_guard,
        test_MC_3_invalid_ticker_filter,
        test_MC_4_시총_보강_통합,
        test_MC_5_상위200_외_종목_보수_탈락,
        test_MC_6a_정규화_적용_PASS,
        test_MC_6b_정규화_누락_회귀_FAIL,
        test_MC_7_토글_OFF_회귀,
        test_MC_7b_settings_로드_실패_안전망,
        test_MC_8_invalid_value_guard,
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
    print(f"단위 2-9e 총 {total}건 / 성공 {total - failed} / 실패 {failed}")
    sys.exit(0 if failed == 0 else 1)
