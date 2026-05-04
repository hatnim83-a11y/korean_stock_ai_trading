"""scripts/test_closing_bet_price_volume.py

Phase 1-3: ``KisPriceVolumeCollector`` 단위 테스트.

- Mock KIS API 로 응답 파싱 / Layer 2 계산 검증 (대부분의 케이스)
- 실거래 종목 1개로 sanity check (선택 — ``--live`` 플래그)

실행:
    venv/bin/python scripts/test_closing_bet_price_volume.py          # Mock only
    venv/bin/python scripts/test_closing_bet_price_volume.py --live   # + 실제 KIS 호출 1회
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.collectors.kis_price_volume_collector import (
    ATR_PERIOD,
    DEFAULT_DAILY_COUNT,
    KisPriceVolumeCollector,
    MA_PERIOD,
    PriceVolumeSnapshot,
    _to_float,
)


# ===== Mock Fixtures =====


def _make_mock_payload(
    days: int = 60,
    base_close: int = 70_000,
    daily_high: int = 71_000,
    daily_low: int = 69_500,
    daily_open: int = 70_500,
    today_close: int = 71_000,
    today_high: int = 71_200,
    today_low: int = 70_400,
    today_open: int = 70_500,
    today_volume: int = 5_000_000,
    base_volume: int = 1_000_000,
    today_first: bool = True,
) -> list[dict]:
    """KIS get_daily_price 응답 모킹 (내림차순: payload[0] = 최신).

    Args:
        today_first: True 면 payload[0].date 를 오늘 KST 로 설정.
        today_close/today_high/today_low/today_open/today_volume: 오늘 단일 행
        base_close/daily_*: 그 외 (1일전~) 일관된 OHLCV
    """
    out: list[dict] = []
    today_str = now_kst().strftime("%Y%m%d") if today_first else "20260101"
    for i in range(days):
        if i == 0:
            out.append({
                "date": today_str if today_first else "20260101",
                "open": today_open,
                "high": today_high,
                "low": today_low,
                "close": today_close,
                "volume": today_volume,
                "trade_value": today_volume * today_close,
            })
        else:
            out.append({
                "date": f"2025{12 - (i // 30):02d}{(30 - (i % 30)):02d}",
                "open": daily_open,
                "high": daily_high,
                "low": daily_low,
                "close": base_close,
                "volume": base_volume,
                "trade_value": base_volume * base_close,
            })
    return out


def _make_collector_with_mock(payload, daily_count: int = DEFAULT_DAILY_COUNT) -> KisPriceVolumeCollector:
    mock_kis = MagicMock()
    mock_kis.get_daily_price.return_value = payload
    return KisPriceVolumeCollector(kis_api=mock_kis, daily_count=daily_count)


# ===== 테스트 =====


def test_normal_snapshot():
    """정상 응답 → Layer 2 4지표(가용) 모두 채워짐 + Phase 2 placeholder None."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.is_today is True, "오늘 KST 날짜로 만든 mock 인데 is_today=False"
    assert snap.ticker == "005930"
    assert snap.days_collected == 60

    # Layer 2 가용 4지표
    assert snap.close_strength is not None
    assert snap.upper_shadow_atr is not None
    assert snap.volume_surprise is not None
    assert snap.atr_overheat is not None

    # Phase 2 placeholder
    assert snap.last_30min_vwap_position is None
    assert snap.closing_buy_sell_ratio is None

    # 보조
    assert snap.above_ma20 is True  # today_close=71000 > base_close=70000
    assert snap.daily_change_pct is not None
    assert abs(snap.daily_change_pct - (71_000 - 70_000) / 70_000) < 1e-9

    # close_strength 정확값: (71000-70400)/(71200-70400) = 600/800 = 0.75
    assert abs(snap.close_strength - 0.75) < 1e-9

    # volume_surprise: 5_000_000 / 1_000_000 = 5.0 — 첫 행 today_volume=5M 포함되므로 평균 = (5M+19*1M)/20 = 1.2M
    expected_vol_avg = (5_000_000 + 19 * 1_000_000) / 20
    assert abs(snap.vol_avg20 - expected_vol_avg) < 1
    assert abs(snap.volume_surprise - 5_000_000 / expected_vol_avg) < 1e-6

    print(f"  [PASS] close_strength={snap.close_strength:.4f}, "
          f"vol_surprise={snap.volume_surprise:.2f}, atr_overheat={snap.atr_overheat:.3f}")


def test_to_layer2_features_keys():
    """to_layer2_features() 가 candidate_features schema_v1 컬럼명과 일치."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")
    features = snap.to_layer2_features()

    expected_keys = {
        "close_strength",
        "upper_shadow_atr",
        "last_30min_vwap_position",
        "closing_buy_sell_ratio",
        "volume_surprise",
        "atr_overheat",
    }
    assert set(features.keys()) == expected_keys
    print(f"  [PASS] Layer 2 features 키 6개 candidate_features schema 일치")


def test_invalid_ticker():
    """5자리/영문/정수 ticker → is_valid=False."""
    coll = _make_collector_with_mock([])
    for bad in ["12345", "ABCDEF", "123456789", 12345, ""]:
        snap = coll.collect_snapshot(bad)
        assert snap.is_valid is False, f"잘못된 ticker {bad!r} 가 유효 처리됨"
    print(f"  [PASS] 잘못된 ticker 5케이스 차단")


def test_empty_response():
    """빈 응답 / list 아님 / None."""
    for bad_payload in [None, [], {"daily": []}, "string"]:
        coll = _make_collector_with_mock(bad_payload)
        snap = coll.collect_snapshot("005930")
        assert snap.is_valid is False, f"빈 응답 {bad_payload!r} 처리 실패"
    print(f"  [PASS] 빈/누락 응답 4케이스 is_valid=False 처리")


def test_window_insufficient():
    """일봉 < 21건 (MA_PERIOD + 1) → Layer 2 지표 None, raw OHLCV 만 채움."""
    payload = _make_mock_payload(days=10)
    coll = _make_collector_with_mock(payload, daily_count=10)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    # raw OHLCV 는 채워져야 함
    assert snap.close == 71_000
    assert snap.high == 71_200
    assert snap.days_collected == 10
    # Layer 2 지표는 모두 None
    assert snap.close_strength is None
    assert snap.upper_shadow_atr is None
    assert snap.volume_surprise is None
    assert snap.atr_overheat is None
    assert snap.atr14 is None
    assert snap.ma20 is None
    print(f"  [PASS] 일봉 10건 (윈도우 부족) → Layer 2 None, OHLCV 보존")


def test_zero_close_today():
    """당일 close=0 (장 시작 직후 미체결) → Layer 2 지표 모두 None."""
    payload = _make_mock_payload(today_close=0, today_high=0, today_low=0)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True  # API 응답은 정상
    assert snap.close == 0
    assert snap.close_strength is None
    assert snap.atr_overheat is None
    assert snap.volume_surprise is None
    assert snap.upper_shadow_atr is None
    print(f"  [PASS] close=0 → Layer 2 모두 None (정상값과 구분)")


def test_high_eq_low():
    """고가 == 저가 (상하한가) → close_strength = None (분모 0)."""
    payload = _make_mock_payload(
        today_open=71_000, today_high=71_000, today_low=71_000, today_close=71_000
    )
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.close_strength is None, "high==low 인데 close_strength 계산됨"
    # upper_shadow_atr 는 (high - close) / atr — high==close 이라 0 (정상)
    assert snap.upper_shadow_atr == 0.0
    print(f"  [PASS] high==low → close_strength=None, upper_shadow_atr=0")


def test_corrupt_fields():
    """payload 일부 행이 None / 빈 문자열 → 계산 결과 정상 또는 None."""
    payload = _make_mock_payload()
    # 첫 행 institution 자리에 빈 문자열 (해당 calculation에 영향 없음)
    payload[0]["volume"] = ""  # volume_surprise = None
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    # volume 빈 문자열 → None
    assert snap.volume is None
    assert snap.volume_surprise is None
    # close_strength 등은 정상 계산
    assert snap.close_strength is not None
    print(f"  [PASS] volume='' → volume_surprise=None, 다른 지표 정상")


def test_atr_with_trading_halt():
    """payload 중 일부 일자가 거래정지 (high=low=0) → ATR 계산 None."""
    payload = _make_mock_payload()
    # 윈도우 안에 거래정지 일 (5일째)
    payload[5] = {
        "date": payload[5]["date"], "open": 0, "high": 0, "low": 0, "close": 0,
        "volume": 0, "trade_value": 0,
    }
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    # ATR 계산 시 거래정지 일을 만나면 None
    assert snap.atr14 is None
    assert snap.atr_overheat is None
    assert snap.upper_shadow_atr is None
    # close_strength 는 ATR 무관 → 정상
    assert snap.close_strength is not None
    print(f"  [PASS] 거래정지 행 발견 시 ATR=None, 기타 지표 영향 격리")


def test_api_exception():
    """KIS API 호출 시 예외 → is_valid=False (전체 종목 스캔 중단 X)."""
    mock_kis = MagicMock()
    mock_kis.get_daily_price.side_effect = Exception("네트워크 오류")
    coll = KisPriceVolumeCollector(kis_api=mock_kis)
    snap = coll.collect_snapshot("005930")
    assert snap.is_valid is False
    print(f"  [PASS] API 예외 → is_valid=False (collect_for_universe 격리)")


def test_universe_partial_failure():
    """다종목 수집 — 일부 실패해도 다른 종목 계속 진행."""
    mock_kis = MagicMock()
    payload_ok = _make_mock_payload()

    def side_effect(ticker, period="D", count=60):
        if ticker == "005930":
            return payload_ok
        if ticker == "111111":
            raise Exception("API 에러")
        return None

    mock_kis.get_daily_price.side_effect = side_effect

    coll = KisPriceVolumeCollector(kis_api=mock_kis)
    snapshots = coll.collect_for_universe(["005930", "111111", "222222"])

    assert len(snapshots) == 3
    assert snapshots[0].is_valid is True
    assert snapshots[1].is_valid is False
    assert snapshots[2].is_valid is False
    print(f"  [PASS] 3종목 중 1개 정상, 2개 실패 격리")


def test_to_float_helper():
    """_to_float 안전 변환."""
    assert _to_float(100) == 100.0
    assert _to_float("100") == 100.0
    assert _to_float(100.5) == 100.5
    assert _to_float(None) is None
    assert _to_float("") is None
    assert _to_float("abc") is None
    assert _to_float(float("nan")) is None
    assert _to_float(float("inf")) is None
    assert _to_float(True) is None, "bool 은 차단되어야 함"
    print(f"  [PASS] _to_float 9케이스 안전 변환 (bool 포함)")


def test_snapshot_time_default():
    """snapshot_time 미지정 시 now_kst() 사용 (KST timezone-aware)."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    before = now_kst()
    snap = coll.collect_snapshot("005930")
    after = now_kst()

    assert snap.snapshot_time.tzinfo is not None
    assert before <= snap.snapshot_time <= after
    print(f"  [PASS] snapshot_time 자동 = {snap.snapshot_time.strftime('%H:%M:%S %Z')}")


def test_empty_universe():
    """빈 리스트 collect_for_universe — 빈 결과, 에러 X."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snapshots = coll.collect_for_universe([])
    assert snapshots == []
    print(f"  [PASS] 빈 universe → []")


def test_hash_safety_with_raw_payload():
    """frozen=True + raw_payload(list) hash 안전성 — set/dict 키 사용 가능."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    try:
        snap_set = {snap}
        assert len(snap_set) == 1
    except TypeError as e:
        raise AssertionError(f"frozen + list field hash 충돌: {e}")
    print(f"  [PASS] frozen + raw_payload(list) hash 안전 (set 사용 가능)")


def test_atr_calculation_correctness():
    """ATR 계산 정확성 — 알려진 입력으로 검증.

    payload[0..14] (15일) 의 TR SMA 14개.
    """
    # 1일 전 종가 = 70000, 오늘 high=71000, low=69500 → TR = max(1500, 1000, 500) = 1500
    # 그 외 14일 모두 동일 OHLC → TR = max(1500, 1000, 500) = 1500
    payload = _make_mock_payload(
        days=20,
        base_close=70_000, daily_high=71_000, daily_low=69_500,
        today_close=71_000, today_high=71_000, today_low=69_500,
    )
    # daily_count=20 → MA_PERIOD+1=21 미만 → 윈도우 부족 → 별도 검증

    # 직접 _compute_atr 만 테스트
    coll = KisPriceVolumeCollector(kis_api=MagicMock(), daily_count=20)
    atr = coll._compute_atr(payload, period=ATR_PERIOD)

    # 모든 TR = 1500
    assert atr is not None
    assert abs(atr - 1500.0) < 1e-6
    print(f"  [PASS] ATR(14) = {atr} (예상 1500)")


def test_atr_insufficient_data():
    """ATR 계산에 period+1 일 미만 → None."""
    payload = _make_mock_payload(days=14)  # < 15
    coll = KisPriceVolumeCollector(kis_api=MagicMock())
    atr = coll._compute_atr(payload, period=14)
    assert atr is None
    print(f"  [PASS] ATR period+1 미만 → None")


def test_above_ma20_signal():
    """close > ma20 / close < ma20 / close == ma20 분기."""
    # close=72000 > ma20=(72000 + 19*70000)/20 = 70100 → True
    payload = _make_mock_payload(today_close=72_000)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")
    assert snap.is_valid is True
    assert snap.above_ma20 is True

    # close=68000 < ma20 = (68000+19*70000)/20 = 69900 → False
    payload2 = _make_mock_payload(today_close=68_000)
    coll2 = _make_collector_with_mock(payload2)
    snap2 = coll2.collect_snapshot("005930")
    assert snap2.above_ma20 is False
    print(f"  [PASS] above_ma20 분기 (True/False)")


def test_no_today_first():
    """today_first=False (비거래일 / 장 시작 전) → is_today=False, 지표 정상 계산."""
    payload = _make_mock_payload(today_first=False)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")
    assert snap.is_valid is True
    assert snap.is_today is False
    # 지표 자체는 채워짐 (sanity check)
    assert snap.close_strength is not None
    print(f"  [PASS] today_first=False → is_today=False, 지표 정상")


def test_live_kis_call(live: bool = False):
    """실거래 종목 1개로 KIS API 한 번 호출 (선택)."""
    if not live:
        print(f"  [SKIP] --live 플래그 시 활성화")
        return

    coll = KisPriceVolumeCollector()  # 기본 KIS 싱글톤
    snap = coll.collect_snapshot("005930")
    print(
        f"  [LIVE] is_valid={snap.is_valid}, is_today={snap.is_today}, "
        f"close={snap.close}, "
        f"close_strength={snap.close_strength and round(snap.close_strength, 4)}, "
        f"vol_surprise={snap.volume_surprise and round(snap.volume_surprise, 2)}, "
        f"atr_overheat={snap.atr_overheat and round(snap.atr_overheat, 3)}, "
        f"above_ma20={snap.above_ma20}"
    )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실거래 KIS 호출 sanity check")
    args = parser.parse_args()

    print("=== Phase 1-3 KisPriceVolumeCollector 단위 테스트 ===\n")
    tests = [
        test_normal_snapshot,
        test_to_layer2_features_keys,
        test_invalid_ticker,
        test_empty_response,
        test_window_insufficient,
        test_zero_close_today,
        test_high_eq_low,
        test_corrupt_fields,
        test_atr_with_trading_halt,
        test_api_exception,
        test_universe_partial_failure,
        test_to_float_helper,
        test_snapshot_time_default,
        test_empty_universe,
        test_hash_safety_with_raw_payload,
        test_atr_calculation_correctness,
        test_atr_insufficient_data,
        test_above_ma20_signal,
        test_no_today_first,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"▶ test_live_kis_call (--live={'ON' if args.live else 'OFF'})")
    test_live_kis_call(live=args.live)
    print()

    print(f"[ALL PASS] {len(tests)}개 mock 테스트 통과")


if __name__ == "__main__":
    main()
