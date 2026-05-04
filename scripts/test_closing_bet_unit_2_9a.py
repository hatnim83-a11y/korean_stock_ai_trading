"""단위 2-9a 검증: universe_provider_v2 (4 출처 통합).

시나리오 (15+):
    UV2-1:  4 출처 모두 정상 → 합집합 + 중복 제거
    UV2-2:  pykrx 1개 출처 실패 → 다른 3개로 진행
    UV2-3:  pykrx 모든 출처 실패 → 빈 리스트 폴백
    UV2-4:  캐시 히트 (같은 거래일 두 번째 호출, 출처 호출 0회)
    UV2-5:  스윙 테마 + pykrx 합산 (출처 다중성 검증)
    UV2-6:  무효 종목코드 정규식 필터
    UV2-7:  스윙 보유 제외
    UV2-8:  hard_cap 도달 시 잘라냄
    UV2-9:  KOSPI+KOSDAQ 합산 (거래대금 상위 N)
    UV2-10: 외국인 순매수 — investor="외국인" 정확 매핑 확인
    UV2-11: 등락률 상위 N — 음수(하락) 종목도 nlargest 영향
    UV2-12: pykrx 호출 시간 < 10초 (모킹 환경 즉시 반환 + 회귀)
    UV2-13: 비-영업일 조회 (빈 DataFrame) → 빈 리스트
    UV2-14: 인스턴스 lock 동시 호출 안전 (스레드 2개 동시)
    UV2-15: 호출 결과 list[str] 타입 + 모두 6자리
    UV2-16: 빈 DataFrame (None 반환) graceful 폴백
    UV2-17: get_universe alias 호환 (v1 시그니처 호출)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import universe_provider_v2 as upv2


# ===== Fixtures =====


def _make_themes(n: int) -> list[dict]:
    return [
        {"name": f"테마{i}", "url": f"https://example.com/theme/{100+i}"}
        for i in range(1, n + 1)
    ]


def _make_stocks(prefix: str, count: int) -> list[dict]:
    """6자리 코드 mock 종목 리스트."""
    return [
        {"code": f"{prefix}{i:04d}", "name": f"종목{i}", "price": 1000 + i, "change_rate": 1.0}
        for i in range(count)
    ]


def _make_ohlcv_df(codes_with_value: list[tuple[str, int]]) -> pd.DataFrame:
    """ticker → 거래대금 mapping df (pykrx 형식)."""
    return pd.DataFrame(
        {"거래대금": [v for _, v in codes_with_value]},
        index=[c for c, _ in codes_with_value],
    )


def _make_change_df(codes_with_change: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"등락률": [v for _, v in codes_with_change]},
        index=[c for c, _ in codes_with_change],
    )


def _make_foreign_df(codes_with_value: list[tuple[str, int]]) -> pd.DataFrame:
    """외국인 순매수 거래대금 df — pykrx 실제 컬럼명 '순매수거래대금' 사용."""
    return pd.DataFrame(
        {"순매수거래대금": [v for _, v in codes_with_value]},
        index=[c for c, _ in codes_with_value],
    )


def _patch_all_sources(
    *,
    theme_codes: list[str] = None,
    value_codes: list[str] = None,
    change_codes: list[str] = None,
    foreign_codes: list[str] = None,
    swing_holdings: set = None,
):
    """4 출처 + 스윙 보유를 한꺼번에 패치하는 컨텍스트 헬퍼."""
    return patch.multiple(
        upv2,
        _fetch_theme_codes_v2=MagicMock(return_value=theme_codes or []),
        _fetch_top_value_codes=MagicMock(return_value=value_codes or []),
        _fetch_top_change_codes=MagicMock(return_value=change_codes or []),
        _fetch_top_foreign_buy_codes=MagicMock(return_value=foreign_codes or []),
        _fetch_swing_holdings=MagicMock(return_value=swing_holdings or set()),
    )


# ===== 시나리오 =====


def test_UV2_1_네_출처_정상_합집합():
    upv2.reset_cache()
    with _patch_all_sources(
        theme_codes=["100001", "100002"],
        value_codes=["200001", "200002"],
        change_codes=["300001", "300002"],
        foreign_codes=["400001", "400002"],
    ):
        universe = upv2.get_universe_v2()
    assert len(universe) == 8, f"UV2-1 FAIL: {len(universe)}건 (8 기대)"
    assert len(set(universe)) == 8, "UV2-1 FAIL: 중복 발생"
    assert all(c.isdigit() and len(c) == 6 for c in universe), "UV2-1 FAIL: 6자리 위반"
    print(f"[PASS] UV2-1: 4 출처 합집합 → {len(universe)}건")


def test_UV2_2_한_출처_실패_나머지_진행():
    upv2.reset_cache()
    # foreign 만 빈 리스트 (예외 흡수 후) — 다른 3 출처 정상
    with _patch_all_sources(
        theme_codes=["100001"],
        value_codes=["200001", "200002"],
        change_codes=["300001"],
        foreign_codes=[],
    ):
        universe = upv2.get_universe_v2()
    assert len(universe) == 4, f"UV2-2 FAIL: {len(universe)}건 (4 기대)"
    print(f"[PASS] UV2-2: 1 출처 실패 → 나머지 3개로 {len(universe)}건")


def test_UV2_3_모든_출처_실패():
    upv2.reset_cache()
    with _patch_all_sources():  # 모두 빈 리스트 default
        universe = upv2.get_universe_v2()
    assert universe == [], f"UV2-3 FAIL: {universe}"
    print("[PASS] UV2-3: 4 출처 모두 실패 → 빈 리스트")


def test_UV2_4_캐시_히트():
    upv2.reset_cache()
    theme_mock = MagicMock(return_value=["100001"])
    value_mock = MagicMock(return_value=["200001"])
    change_mock = MagicMock(return_value=["300001"])
    foreign_mock = MagicMock(return_value=["400001"])
    swing_mock = MagicMock(return_value=set())
    with patch.multiple(
        upv2,
        _fetch_theme_codes_v2=theme_mock,
        _fetch_top_value_codes=value_mock,
        _fetch_top_change_codes=change_mock,
        _fetch_top_foreign_buy_codes=foreign_mock,
        _fetch_swing_holdings=swing_mock,
    ):
        u1 = upv2.get_universe_v2()
        u2 = upv2.get_universe_v2()
    assert u1 == u2
    # 4 출처 mock 은 첫 호출만 1회씩
    assert theme_mock.call_count == 1, f"UV2-4 FAIL: theme {theme_mock.call_count}회"
    assert value_mock.call_count == 1, f"UV2-4 FAIL: value {value_mock.call_count}회"
    assert change_mock.call_count == 1, f"UV2-4 FAIL: change {change_mock.call_count}회"
    assert foreign_mock.call_count == 1, f"UV2-4 FAIL: foreign {foreign_mock.call_count}회"
    print("[PASS] UV2-4: 캐시 히트 — 두 번째 호출 시 출처 호출 0회")


def test_UV2_5_스윙_pykrx_합산_다중성():
    upv2.reset_cache()
    # 출처별 다른 종목 → 합집합 후 모두 universe 에 포함
    with _patch_all_sources(
        theme_codes=["100001", "100002", "100003"],   # 스윙 테마
        value_codes=["200001", "200002"],              # 거래대금
        change_codes=["300001"],                        # 등락률
        foreign_codes=["400001", "400002"],             # 외국인
    ):
        universe = upv2.get_universe_v2()
    assert len(universe) == 8, f"UV2-5 FAIL: {len(universe)}"
    # 출처별 모두 universe 에 들어있는지 확인 (다중성)
    assert "100001" in universe and "200001" in universe
    assert "300001" in universe and "400001" in universe
    print(f"[PASS] UV2-5: 4 출처 다중성 검증 → {len(universe)}건 (테마 3 + 거래대금 2 + 등락률 1 + 외국인 2)")


def test_UV2_6_무효_종목코드_정규식_필터():
    upv2.reset_cache()
    # 무효 종목 (pykrx 가 잘못된 ticker 반환한 시나리오)
    with _patch_all_sources(
        theme_codes=["100001", "0015G0", "1234567", None, "100002"],
    ):
        universe = upv2.get_universe_v2()
    # 4건은 무효 (영문/길이/None) → 정상 2건만
    assert universe == ["100001", "100002"], f"UV2-6 FAIL: {universe}"
    print("[PASS] UV2-6: 무효 종목코드 정규식 필터 (3건 제외)")


def test_UV2_7_스윙_보유_제외():
    upv2.reset_cache()
    with _patch_all_sources(
        theme_codes=["100001", "100002"],
        value_codes=["200001"],
        swing_holdings={"100001", "200001"},  # 둘 다 스윙 보유
    ):
        universe = upv2.get_universe_v2()
    assert universe == ["100002"], f"UV2-7 FAIL: {universe}"
    print("[PASS] UV2-7: 스윙 보유 종목 제외")


def test_UV2_8_hard_cap_도달():
    upv2.reset_cache()
    # 4 출처 합산 200건 → hard_cap=10 으로 잘라냄
    big_codes_a = [f"10{i:04d}" for i in range(50)]
    big_codes_b = [f"20{i:04d}" for i in range(50)]
    big_codes_c = [f"30{i:04d}" for i in range(50)]
    big_codes_d = [f"40{i:04d}" for i in range(50)]
    with _patch_all_sources(
        theme_codes=big_codes_a,
        value_codes=big_codes_b,
        change_codes=big_codes_c,
        foreign_codes=big_codes_d,
    ):
        universe = upv2.get_universe_v2(hard_cap=10)
    assert len(universe) == 10, f"UV2-8 FAIL: {len(universe)}건 (10 기대)"
    print(f"[PASS] UV2-8: hard_cap=10 적용 → {len(universe)}건")


def test_UV2_9_KOSPI_KOSDAQ_거래대금_합산():
    """_fetch_top_value_codes 가 KOSPI+KOSDAQ 합산 후 nlargest 적용."""
    upv2.reset_cache()
    # KOSPI 5개, KOSDAQ 5개 → 합산 10개 중 상위 3개 (거래대금 기준)
    df_kospi = _make_ohlcv_df([
        ("100001", 1000), ("100002", 5000), ("100003", 2000),
        ("100004", 8000), ("100005", 100),  # 가장 큰: 100004(8000)
    ])
    df_kosdaq = _make_ohlcv_df([
        ("200001", 9000), ("200002", 50), ("200003", 3000),
        ("200004", 7000), ("200005", 200),  # 가장 큰: 200001(9000)
    ])
    fake_krx = MagicMock()
    fake_krx.get_market_ohlcv_by_ticker.side_effect = lambda d, market: (
        df_kospi if market == "KOSPI" else df_kosdaq
    )
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_value_codes("20260430", top_n=3)
    # 합산 상위 3: 200001(9000), 100004(8000), 200004(7000)
    assert codes == ["200001", "100004", "200004"], f"UV2-9 FAIL: {codes}"
    print(f"[PASS] UV2-9: KOSPI+KOSDAQ 거래대금 합산 nlargest → {codes}")


def test_UV2_10_외국인_investor_정확_매핑():
    """_fetch_top_foreign_buy_codes 가 investor='외국인' 한글 정확 매핑.

    KOSPI/KOSDAQ 두 시장에 대해 분리된 mock 응답.
    """
    upv2.reset_cache()
    df_kospi = _make_foreign_df([("100001", 5000), ("100002", 10000), ("100003", 3000)])
    df_kosdaq = _make_foreign_df([("200001", 8000), ("200002", 1000)])
    fake_krx = MagicMock()
    fake_krx.get_market_net_purchases_of_equities_by_ticker.side_effect = (
        lambda fromdate, todate, market, investor: df_kospi if market == "KOSPI" else df_kosdaq
    )
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_foreign_buy_codes("20260430", top_n=3)
    # 모든 호출 중 investor 한글 정확 매핑 확인
    all_calls = fake_krx.get_market_net_purchases_of_equities_by_ticker.call_args_list
    assert len(all_calls) == 2, f"UV2-10 FAIL: 호출 수 {len(all_calls)} (KOSPI+KOSDAQ 2회 기대)"
    for call in all_calls:
        investor_arg = call.kwargs.get("investor")
        assert investor_arg == "외국인", f"UV2-10 FAIL: investor={investor_arg}"
    # 합산 nlargest 3: 100002(10000), 200001(8000), 100001(5000)
    assert codes == ["100002", "200001", "100001"], f"UV2-10 FAIL: {codes}"
    print(f"[PASS] UV2-10: investor='외국인' 정확 매핑 + KOSPI+KOSDAQ 합산 → {codes}")


def test_UV2_11_등락률_음수_혼재_nlargest():
    """등락률 상위 N — 음수(하락) 종목도 nlargest 영향 (양수 우선)."""
    upv2.reset_cache()
    df_kospi = _make_change_df([
        ("100001", 5.0), ("100002", -3.0), ("100003", 12.0),
        ("100004", -8.0), ("100005", 0.5),
    ])
    df_kosdaq = _make_change_df([
        ("200001", 8.0), ("200002", -1.0),
    ])
    fake_krx = MagicMock()
    fake_krx.get_market_price_change_by_ticker.side_effect = lambda f, t, market: (
        df_kospi if market == "KOSPI" else df_kosdaq
    )
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_change_codes("20260430", top_n=3)
    # 상위 3: 100003(12.0), 200001(8.0), 100001(5.0)
    assert codes == ["100003", "200001", "100001"], f"UV2-11 FAIL: {codes}"
    print(f"[PASS] UV2-11: 등락률 nlargest (음수 무시) → {codes}")


def test_UV2_12_호출_시간_제한():
    """모킹 환경에서 4 출처 호출 < 1초 (회귀 — 실전 < 10초)."""
    upv2.reset_cache()
    with _patch_all_sources(
        theme_codes=["100001"], value_codes=["200001"],
        change_codes=["300001"], foreign_codes=["400001"],
    ):
        t0 = time.time()
        upv2.get_universe_v2()
        elapsed = time.time() - t0
    assert elapsed < 1.0, f"UV2-12 FAIL: {elapsed:.3f}s"
    print(f"[PASS] UV2-12: 호출 시간 {elapsed*1000:.1f}ms < 1000ms")


def test_UV2_13_비영업일_빈_DataFrame():
    """pykrx 가 빈 DataFrame 반환 시 빈 리스트 graceful 폴백."""
    upv2.reset_cache()
    empty_df = pd.DataFrame()
    fake_krx = MagicMock()
    fake_krx.get_market_ohlcv_by_ticker.return_value = empty_df
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_value_codes("20260101", top_n=10)
    assert codes == [], f"UV2-13 FAIL: {codes}"
    print("[PASS] UV2-13: 비-영업일 (빈 DataFrame) → 빈 리스트")


def test_UV2_14_lock_동시_호출_안전():
    """스레드 2개 동시 호출 — 캐시 lock 으로 중복 빌드 방지."""
    upv2.reset_cache()
    call_count = {"theme": 0}

    def slow_theme(*args, **kwargs):
        call_count["theme"] += 1
        time.sleep(0.05)
        return ["100001", "100002"]

    with patch.multiple(
        upv2,
        _fetch_theme_codes_v2=MagicMock(side_effect=slow_theme),
        _fetch_top_value_codes=MagicMock(return_value=[]),
        _fetch_top_change_codes=MagicMock(return_value=[]),
        _fetch_top_foreign_buy_codes=MagicMock(return_value=[]),
        _fetch_swing_holdings=MagicMock(return_value=set()),
    ):
        results = []

        def worker():
            results.append(upv2.get_universe_v2())

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert results[0] == results[1]
    assert call_count["theme"] == 1, f"UV2-14 FAIL: theme {call_count['theme']}회 (1회 기대 — 캐시 lock)"
    print(f"[PASS] UV2-14: 동시 호출 시 빌드 1회 (lock 검증)")


def test_UV2_15_타입_검증():
    """반환 결과: list[str] + 모두 6자리 정수 문자열."""
    upv2.reset_cache()
    with _patch_all_sources(
        theme_codes=["100001"], value_codes=["200001"],
        change_codes=["300001"], foreign_codes=["400001"],
    ):
        universe = upv2.get_universe_v2()
    assert isinstance(universe, list), f"UV2-15 FAIL: type={type(universe)}"
    for c in universe:
        assert isinstance(c, str), f"UV2-15 FAIL: 종목코드 type={type(c)}"
        assert c.isdigit() and len(c) == 6, f"UV2-15 FAIL: '{c}' 6자리 위반"
    print(f"[PASS] UV2-15: list[str] + 모두 6자리 — {len(universe)}건")


def test_UV2_16_None_반환_graceful():
    """pykrx 함수가 None 반환 시도 → 빈 리스트."""
    upv2.reset_cache()
    fake_krx = MagicMock()
    fake_krx.get_market_ohlcv_by_ticker.return_value = None
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_value_codes("20260430", top_n=10)
    assert codes == [], f"UV2-16 FAIL: {codes}"
    print("[PASS] UV2-16: None 반환 graceful → 빈 리스트")


def test_UV2_17_get_universe_alias_v1_호환():
    """v1 인터페이스 alias: ``get_universe()`` v1 시그니처 인자로 호출.

    단위 2-9b 도입 후 ``get_universe`` 는 ``get_universe_v2_filtered`` 로 라우팅.
    필터는 mock 으로 무력화 (입력 그대로 통과) — 본 시나리오는 시그니처 호환성만 검증.
    """
    upv2.reset_cache()
    # apply_all_filters mock — 입력 universe 그대로 통과
    fake_apply = MagicMock(side_effect=lambda tickers, **kwargs: (list(tickers), {}))
    with _patch_all_sources(
        theme_codes=["100001", "100002"],
        value_codes=["200001"],
    ), patch(
        "closing_bet_system.collectors.universe_filters.apply_all_filters",
        fake_apply,
    ):
        # v1 시그니처 인자만 사용
        universe = upv2.get_universe(
            theme_count=5,
            stocks_per_theme=4,
            hard_cap=20,
            exclude_swing_holdings=True,
        )
    assert len(universe) == 3, f"UV2-17 FAIL: {len(universe)}"
    print(f"[PASS] UV2-17: v1 시그니처 alias 호환 (필터 라우팅 포함) → {len(universe)}건")


def test_UV2_18_KeyError_pykrx_라이브러리_결함():
    """pykrx 가 KeyError 발생시 (실전에서 본 패턴) graceful 폴백."""
    upv2.reset_cache()
    fake_krx = MagicMock()
    fake_krx.get_market_ohlcv_by_ticker.side_effect = KeyError(
        "None of [Index(['시가', '고가', '저가', '종가'])] are in the [columns]"
    )
    with patch.object(upv2, "_import_pykrx", return_value=fake_krx):
        codes = upv2._fetch_top_value_codes("20260430", top_n=10)
    assert codes == [], f"UV2-18 FAIL: {codes}"
    print("[PASS] UV2-18: pykrx KeyError graceful → 빈 리스트")


if __name__ == "__main__":
    print("=" * 70)
    print("단위 2-9a 검증: universe_provider_v2 (4 출처 통합)")
    print("=" * 70)
    test_UV2_1_네_출처_정상_합집합()
    test_UV2_2_한_출처_실패_나머지_진행()
    test_UV2_3_모든_출처_실패()
    test_UV2_4_캐시_히트()
    test_UV2_5_스윙_pykrx_합산_다중성()
    test_UV2_6_무효_종목코드_정규식_필터()
    test_UV2_7_스윙_보유_제외()
    test_UV2_8_hard_cap_도달()
    test_UV2_9_KOSPI_KOSDAQ_거래대금_합산()
    test_UV2_10_외국인_investor_정확_매핑()
    test_UV2_11_등락률_음수_혼재_nlargest()
    test_UV2_12_호출_시간_제한()
    test_UV2_13_비영업일_빈_DataFrame()
    test_UV2_14_lock_동시_호출_안전()
    test_UV2_15_타입_검증()
    test_UV2_16_None_반환_graceful()
    test_UV2_17_get_universe_alias_v1_호환()
    test_UV2_18_KeyError_pykrx_라이브러리_결함()
    print("\n" + "=" * 70)
    print("✅ 단위 2-9a 18 시나리오 모두 PASS (요구 15+ 충족)")
    print("=" * 70)
