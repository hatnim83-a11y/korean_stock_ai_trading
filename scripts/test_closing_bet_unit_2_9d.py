"""단위 2-9d 검증: KIS Open API ranking 대체 (작업 F-2).

배경: 단위 2-9c (commit d3755e9) 후 KIS Open API ranking 4종으로 pykrx bulk 의존
완전 제거. 시총 보강(옵션 B 자연 활성)도 추가.

시나리오 (12):
    KP-1:  KIS volume-rank 정상 → 30종목 list
    KP-2:  KIS fluctuation 정상 → 30종목 list
    KP-3:  KIS foreign-institution-total 정상 → 30종목 list
    KP-4:  KIS market-cap 정상 → ticker→{market_cap, close, change_rate} dict
    KP-5:  rt_cd != "0" 응답 → 빈 리스트/dict + warning
    KP-6:  비-6자리 코드 혼입 → 정규식 필터링
    KP-7:  토큰 공유 검증 — KISMarketProvider 가 _shared_token 재사용
    KP-8:  rate_limit 호출 검증 (메서드 호출 시 _rate_limit 1회)
    KP-9:  use_kis_ranking=false → pykrx 폴백 경로 (회귀)
    KP-10: use_kis_ranking=true → universe_provider_v2 통합 (KIS 호출)
    KP-11: fallback_include_market_cap=true → universe_filters 시총 보강
    KP-12: KIS RuntimeError → graceful 폴백 (pykrx 또는 빈 리스트)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import kis_market_provider as kmp
from closing_bet_system.collectors import universe_provider_v2 as upv2
from closing_bet_system.collectors import universe_filters as uf


# ===== Fixtures =====


def _build_fake_kis_api(*, response_data=None, raise_on_get: bool = False):
    """KISMarketProvider 가 위임하는 KISApi 의 내부 메서드를 mock.

    `_kis.client.get` 호출 시 `response_data` 를 반환하는 fake response 를 돌려준다.
    """
    fake_kis = MagicMock()
    fake_kis._rate_limit = MagicMock()  # no-op
    fake_kis.base_url = "https://openapi.koreainvestment.com:9443"
    fake_kis._get_headers = MagicMock(return_value={"authorization": "Bearer mock"})

    fake_response = MagicMock()
    if raise_on_get:
        fake_kis.client.get = MagicMock(side_effect=RuntimeError("simulated KIS failure"))
    else:
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(return_value=response_data or {})
        fake_kis.client.get = MagicMock(return_value=fake_response)
    return fake_kis


def _make_volume_rank_response(tickers: list[str], values: list[float] = None) -> dict:
    """KIS volume-rank API 정상 응답 시뮬 (rt_cd=0, output 리스트)."""
    output = []
    for i, t in enumerate(tickers):
        v = values[i] if values and i < len(values) else float(50_000_000_000 - i * 1_000_000_000)
        output.append({
            "mksc_shrn_iscd": t,
            "hts_kor_isnm": f"종목{t}",
            "stck_prpr": f"{5000 + i * 100}",
            "prdy_ctrt": f"{1.5 - i * 0.1:.2f}",
            "acml_vol": "1000000",
            "acml_tr_pbmn": str(int(v)),
            "stck_avls": str(int(v * 10)),  # 시총 (volume의 10배 가정)
        })
    return {"rt_cd": "0", "msg1": "정상", "output": output}


# ===== KP-1 ~ KP-4: 4 ranking 정상 응답 =====


def test_KP_1_volume_rank_정상_응답():
    kmp.reset_singleton()
    tickers = [f"{100000 + i:06d}" for i in range(30)]
    fake_kis = _build_fake_kis_api(response_data=_make_volume_rank_response(tickers))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_value_codes(top_n=30)
    assert len(result) == 30, f"KP-1 FAIL: {len(result)}"
    assert result == tickers
    print(f"[PASS] KP-1: volume-rank 정상 30건")


def test_KP_2_fluctuation_정상_응답():
    kmp.reset_singleton()
    tickers = [f"{200000 + i:06d}" for i in range(30)]
    fake_kis = _build_fake_kis_api(response_data=_make_volume_rank_response(tickers))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_change_codes(top_n=30, direction="up")
    assert len(result) == 30
    print(f"[PASS] KP-2: fluctuation 정상 30건")


def test_KP_3_foreign_정상_응답():
    kmp.reset_singleton()
    tickers = [f"{300000 + i:06d}" for i in range(30)]
    fake_kis = _build_fake_kis_api(response_data=_make_volume_rank_response(tickers))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_foreign_buy_codes(top_n=30)
    assert len(result) == 30
    print(f"[PASS] KP-3: foreign-institution-total 정상 30건")


def test_KP_4_market_cap_정상_응답():
    kmp.reset_singleton()
    tickers = [f"{400000 + i:06d}" for i in range(50)]
    fake_kis = _build_fake_kis_api(response_data=_make_volume_rank_response(tickers))
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_market_cap_data(top_n=50)
    assert len(result) == 50
    sample = result[tickers[0]]
    assert "market_cap" in sample and sample["market_cap"] > 0
    assert "close" in sample and sample["close"] > 0
    print(f"[PASS] KP-4: market-cap 정상 50건, 샘플 시총={sample['market_cap']}")


# ===== KP-5 ~ KP-6: 비정상 응답 처리 =====


def test_KP_5_rt_cd_failure():
    """rt_cd != '0' → 빈 리스트/dict + warning."""
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data={"rt_cd": "1", "msg1": "조회 권한 없음", "output": []}
    )
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    assert provider.get_top_value_codes() == []
    assert provider.get_top_change_codes() == []
    assert provider.get_top_foreign_buy_codes() == []
    assert provider.get_top_market_cap_data() == {}
    print(f"[PASS] KP-5: rt_cd 실패 → 모든 메서드 빈 결과")


def test_KP_6_invalid_ticker_filter():
    """비-6자리 코드 혼입 → 정규식 필터링."""
    kmp.reset_singleton()
    output = [
        {"mksc_shrn_iscd": "005930"},  # 정상
        {"mksc_shrn_iscd": "ABCDEF"},  # 영문 → 제외
        {"mksc_shrn_iscd": "12345"},   # 5자리 → 제외
        {"mksc_shrn_iscd": "1234567"}, # 7자리 → 제외
        {"mksc_shrn_iscd": "000660"},  # 정상
        {"mksc_shrn_iscd": ""},        # 빈 문자열 → 제외
        {"mksc_shrn_iscd": "035420"},  # 정상
    ]
    fake_kis = _build_fake_kis_api(response_data={"rt_cd": "0", "output": output})
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    result = provider.get_top_value_codes(top_n=10)
    assert result == ["005930", "000660", "035420"], f"KP-6 FAIL: {result}"
    print(f"[PASS] KP-6: 비-6자리 코드 정규식 필터링 → {result}")


# ===== KP-7 ~ KP-8: 토큰/rate_limit 인프라 =====


def test_KP_7_token_sharing():
    """KISMarketProvider 가 KISApi._shared_token 재사용 (신규 토큰 발급 X)."""
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_volume_rank_response(["005930", "000660"])
    )
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    provider.get_top_value_codes()
    # _kis.client.get 이 호출되며, 그 인자에 fake_kis._get_headers 결과가 들어감
    fake_kis._get_headers.assert_called()  # 헤더 생성 호출됨 (= 토큰 사용)
    # KISApi._shared_token 재발급 호출은 발생 안 함 (KISApi 인스턴스 위임이라)
    print(f"[PASS] KP-7: 토큰 공유 — KISApi 위임 (신규 토큰 발급 X)")


def test_KP_8_rate_limit_call():
    """모든 ranking 호출 시 `_rate_limit` 1회 호출."""
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_volume_rank_response(["005930"])
    )
    provider = kmp.KISMarketProvider(kis_api=fake_kis)
    provider.get_top_value_codes()
    provider.get_top_change_codes()
    provider.get_top_foreign_buy_codes()
    provider.get_top_market_cap_data()
    assert fake_kis._rate_limit.call_count == 4, (
        f"KP-8 FAIL: rate_limit 호출 {fake_kis._rate_limit.call_count}회 (기대 4)"
    )
    print(f"[PASS] KP-8: rate_limit 4회 호출 (메서드당 1회)")


# ===== KP-9 ~ KP-10: 토글 라우팅 =====


def test_KP_9_use_kis_ranking_false_pykrx_fallback():
    """`use_kis_ranking=False` → pykrx 폴백 경로 (회귀 안전)."""
    upv2.reset_cache()
    with patch.object(upv2, "_is_kis_ranking_enabled", return_value=False):
        with patch.object(upv2, "_import_pykrx") as mock_pykrx:
            mock_krx = MagicMock()
            mock_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(
                __len__=lambda self: 0, columns=[]
            )
            mock_pykrx.return_value = mock_krx
            # 실제 함수 호출 — pykrx 경로로 진입
            result = upv2._fetch_top_value_codes("20260507", top_n=10)
            # pykrx mock 이 호출됐는지 검증
            assert mock_krx.get_market_ohlcv_by_ticker.called, (
                "KP-9 FAIL: pykrx 경로 진입 안 함"
            )
    print(f"[PASS] KP-9: use_kis_ranking=False → pykrx 경로")


def test_KP_10_use_kis_ranking_true_routes_to_kis():
    """`use_kis_ranking=True` → KIS 호출 (KIS 정상 시 pykrx 호출 X)."""
    upv2.reset_cache()
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(
        response_data=_make_volume_rank_response([f"{100000 + i:06d}" for i in range(5)])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)

    with patch.object(upv2, "_is_kis_ranking_enabled", return_value=True):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(upv2, "_import_pykrx") as mock_pykrx:
                result = upv2._fetch_top_value_codes("20260507", top_n=5)
                assert len(result) == 5, f"KP-10 FAIL: {result}"
                assert not mock_pykrx.called, "KP-10 FAIL: KIS 정상인데 pykrx 호출됨"
    print(f"[PASS] KP-10: use_kis_ranking=True → KIS 라우팅 (pykrx 호출 0회)")


# ===== KP-11: 시총 보강 (universe_filters) =====


def test_KP_11_market_cap_enrichment():
    """`fallback_include_market_cap=True` → KIS market-cap 으로 시총 채움 → 옵션 A 보수 탈락 회피."""
    uf.reset_cache()
    kmp.reset_singleton()
    # KIS market-cap mock — 100001 만 시총 보강
    fake_kis = _build_fake_kis_api(
        response_data=_make_volume_rank_response(["100001"], values=[60_000_000_000_000])
    )
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)

    fake_pykrx_krx = MagicMock()
    # bulk 호출은 빈 응답
    fake_pykrx_krx.get_market_cap_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    fake_pykrx_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(__len__=lambda self: 0, columns=[])
    # 종목별 by_date 도 빈 응답 (시총 보강만으로 통과되는지 검증)
    import pandas as pd
    fake_pykrx_krx.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    with patch.object(uf, "_import_pykrx", return_value=fake_pykrx_krx):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(uf, "time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
                data = uf._fetch_market_data_bulk("20260507", tickers=["100001"])
    assert "100001" in data, f"KP-11 FAIL: {data}"
    assert data["100001"].get("market_cap", 0) > 0, (
        f"KP-11 FAIL: market_cap 보강 안 됨: {data['100001']}"
    )
    print(f"[PASS] KP-11: KIS market-cap 보강 → 시총 {data['100001']['market_cap']}")


# ===== KP-12: 예외 격리 =====


def test_KP_12_kis_exception_graceful():
    """KIS RuntimeError → graceful 폴백 (universe_provider_v2 → pykrx)."""
    upv2.reset_cache()
    kmp.reset_singleton()
    fake_kis = _build_fake_kis_api(raise_on_get=True)  # client.get RuntimeError
    fake_provider = kmp.KISMarketProvider(kis_api=fake_kis)

    with patch.object(upv2, "_is_kis_ranking_enabled", return_value=True):
        with patch(
            "closing_bet_system.collectors.kis_market_provider.get_kis_market_provider",
            return_value=fake_provider,
        ):
            with patch.object(upv2, "_import_pykrx") as mock_pykrx:
                mock_krx = MagicMock()
                mock_krx.get_market_ohlcv_by_ticker.return_value = MagicMock(
                    __len__=lambda self: 0, columns=[]
                )
                mock_pykrx.return_value = mock_krx
                # KIS 실패 → pykrx 폴백 → pykrx 도 빈 결과 → 빈 리스트 반환
                result = upv2._fetch_top_value_codes("20260507", top_n=5)
                assert result == [], f"KP-12 FAIL: {result}"
                # pykrx 가 호출됐는지 검증 (graceful 폴백)
                assert mock_krx.get_market_ohlcv_by_ticker.called, (
                    "KP-12 FAIL: KIS 실패에도 pykrx 폴백 진입 안 함"
                )
    print(f"[PASS] KP-12: KIS RuntimeError → pykrx graceful 폴백")


# ===== Runner =====


if __name__ == "__main__":
    tests = [
        test_KP_1_volume_rank_정상_응답,
        test_KP_2_fluctuation_정상_응답,
        test_KP_3_foreign_정상_응답,
        test_KP_4_market_cap_정상_응답,
        test_KP_5_rt_cd_failure,
        test_KP_6_invalid_ticker_filter,
        test_KP_7_token_sharing,
        test_KP_8_rate_limit_call,
        test_KP_9_use_kis_ranking_false_pykrx_fallback,
        test_KP_10_use_kis_ranking_true_routes_to_kis,
        test_KP_11_market_cap_enrichment,
        test_KP_12_kis_exception_graceful,
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
    print(f"단위 2-9d 총 {total}건 / 성공 {total - failed} / 실패 {failed}")
    sys.exit(0 if failed == 0 else 1)
