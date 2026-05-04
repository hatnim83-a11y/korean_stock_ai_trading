"""scripts/test_closing_bet_flow_collector.py

Phase 1-2: ``KisIntradayFlowCollector`` 단위 테스트.

- Mock KIS API 로 응답 파싱 검증 (대부분의 케이스)
- 실거래 종목 1개로 sanity check (선택 — ``--live`` 플래그)

실행:
    venv/bin/python scripts/test_closing_bet_flow_collector.py          # Mock only
    venv/bin/python scripts/test_closing_bet_flow_collector.py --live   # + 실제 KIS 호출 1회
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.collectors.kis_intraday_flow_collector import (
    IntradayFlowSnapshot,
    KisIntradayFlowCollector,
    _to_float,
)


# ===== Mock Fixtures =====


def _make_mock_payload(
    ticker: str = "005930",
    daily_count: int = 5,
    inst_qty_today: int = 100_000,
    close_price: int = 70_000,
    foreign_qty_per_day: int = 50_000,
    foreign_ratio: float = 55.2,
    today_first: bool = True,
) -> dict:
    """KIS get_investor_trading 응답 모킹.

    Args:
        today_first: True 면 daily[0] 를 오늘 KST 날짜로 설정 (정상 케이스).
            False 면 의도적으로 과거 날짜 ("20260101") 로 stale 케이스 시뮬레이션.
    """
    daily = []
    today_str = now_kst().strftime("%Y%m%d") if today_first else "20260101"
    for i in range(daily_count):
        daily.append({
            "date": today_str if i == 0 and today_first else f"2026010{i + 1}",
            "foreign": foreign_qty_per_day,
            "institution": inst_qty_today if i == 0 else 30_000,
            "individual": -50_000,
            "close_price": close_price,
        })
    return {
        "code": ticker,
        "foreign_net": foreign_qty_per_day * close_price * daily_count,
        "institution_net": inst_qty_today * close_price,
        "individual_net": -50_000 * close_price * daily_count,
        "foreign_ratio": foreign_ratio,
        "daily": daily,
    }


def _make_collector_with_mock(payload) -> KisIntradayFlowCollector:
    mock_kis = MagicMock()
    mock_kis.get_investor_trading.return_value = payload
    return KisIntradayFlowCollector(kis_api=mock_kis)


# ===== 테스트 =====


def test_normal_snapshot():
    """정상 응답 → 4지표 중 가용 2개 채워짐 + is_today=True."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.is_today is True, "오늘 KST 날짜로 만든 mock 인데 is_today=False"
    assert snap.ticker == "005930"
    # 당일 기관: 100,000 × 70,000 = 7,000,000,000
    assert snap.inst_net_buy_estimated == 100_000 * 70_000
    # 3일 외국인: 50,000 × 70,000 × 3 = 10,500,000,000
    assert snap.foreign_net_buy_3d == 50_000 * 70_000 * 3
    assert snap.foreign_days_collected == 3
    # Phase 2 미구현
    assert snap.program_net_buy_change is None
    assert snap.closing_flow_concentration is None
    # 보조
    assert snap.foreign_ratio == 55.2
    print(f"  [PASS] is_today=True, inst_net={snap.inst_net_buy_estimated:,.0f}원, "
          f"foreign_3d={snap.foreign_net_buy_3d:,.0f}원 ({snap.foreign_days_collected}일치)")


def test_to_layer1_features_keys():
    """to_layer1_features() 가 candidate_features schema_v1 컬럼명과 일치."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")
    features = snap.to_layer1_features()

    expected_keys = {
        "inst_net_buy_estimated",
        "foreign_net_buy_3d",
        "program_net_buy_change",
        "closing_flow_concentration",
    }
    assert set(features.keys()) == expected_keys
    print(f"  [PASS] Layer 1 features 키 4개 candidate_features schema 일치")


def test_invalid_ticker():
    """5자리/영문/정수 ticker → is_valid=False."""
    coll = _make_collector_with_mock({})
    for bad in ["12345", "ABCDEF", "123456789", 12345]:
        snap = coll.collect_snapshot(bad)
        assert snap.is_valid is False, f"잘못된 ticker {bad!r} 가 유효 처리됨"
    print(f"  [PASS] 잘못된 ticker 4케이스 차단")


def test_empty_response():
    """빈 응답 / daily 누락."""
    for bad_payload in [None, {}, {"code": "005930"}, {"daily": []}]:
        coll = _make_collector_with_mock(bad_payload)
        snap = coll.collect_snapshot("005930")
        assert snap.is_valid is False, f"빈 응답 {bad_payload!r} 처리 실패"
    print(f"  [PASS] 빈/누락 응답 4케이스 is_valid=False 처리")


def test_partial_daily():
    """daily 가 1일치만 있는 경우 — 외국인 3일 합계는 1일치만 (foreign_days_collected=1)."""
    payload = _make_mock_payload(daily_count=1)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.foreign_net_buy_3d == 50_000 * 70_000
    assert snap.foreign_days_collected == 1
    print(f"  [PASS] daily 1일치 → foreign_3d={snap.foreign_net_buy_3d:,.0f}원 "
          f"(days_collected={snap.foreign_days_collected})")


def test_stale_date():
    """daily[0] 가 과거 날짜 → is_today=False (정상이라도 경고)."""
    payload = _make_mock_payload(today_first=False)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.is_today is False, f"stale 날짜인데 is_today=True"
    # 데이터 자체는 파싱 (값은 사용 가능, Phase 2 신뢰도 추적이 사후 평가)
    assert snap.inst_net_buy_estimated is not None
    print(f"  [PASS] stale_date → is_today=False (Phase 2 신뢰도 추적 신호)")


def test_zero_close_price():
    """close_price=0 (장 시작 직후 미체결) → inst_net_buy_estimated=None."""
    payload = _make_mock_payload(close_price=0)
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    assert snap.is_valid is True
    assert snap.inst_net_buy_estimated is None, "close_price=0 인데 inst_net 이 0/숫자로 채워짐"
    # foreign_3d 도 close 0 이므로 합산 안됨 → None
    assert snap.foreign_net_buy_3d is None
    assert snap.foreign_days_collected == 0
    print(f"  [PASS] close_price=0 → inst_net=None, foreign_3d=None (정상값과 구분)")


def test_corrupt_daily_fields():
    """daily 항목에 None / 빈 문자열 섞여 있어도 안전."""
    payload = {
        "code": "005930",
        "foreign_ratio": 55.0,
        "daily": [
            {"date": "20260102", "foreign": 50_000, "institution": None,
             "individual": -50_000, "close_price": 70_000},
            {"date": "20260101", "foreign": "", "institution": 30_000,
             "individual": -50_000, "close_price": 70_000},
            {"date": "20251231", "foreign": 50_000, "institution": 30_000,
             "individual": -50_000, "close_price": 70_000},
        ],
    }
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")
    assert snap.is_valid is True
    # institution 이 None 이므로 inst_net_buy_estimated = None
    assert snap.inst_net_buy_estimated is None
    # 외국인 daily[0]=50000, daily[1]="" (skip), daily[2]=50000 → 2일치만
    assert snap.foreign_net_buy_3d == 50_000 * 70_000 * 2
    print(f"  [PASS] 손상 필드 안전 처리 — inst=None, foreign_3d={snap.foreign_net_buy_3d:,.0f}원")


def test_api_exception():
    """KIS API 호출 시 예외 → is_valid=False (전체 종목 스캔 중단 X)."""
    mock_kis = MagicMock()
    mock_kis.get_investor_trading.side_effect = Exception("네트워크 오류")
    coll = KisIntradayFlowCollector(kis_api=mock_kis)
    snap = coll.collect_snapshot("005930")
    assert snap.is_valid is False
    print(f"  [PASS] API 예외 → is_valid=False (collect_for_universe 격리)")


def test_universe_partial_failure():
    """다종목 수집 — 일부 실패해도 다른 종목 계속 진행."""
    mock_kis = MagicMock()
    # ticker 005930 만 정상, 나머지 2개 빈 응답
    payload_ok = _make_mock_payload(ticker="005930")

    def side_effect(ticker, days=5):
        if ticker == "005930":
            return payload_ok
        if ticker == "111111":
            raise Exception("API 에러")
        return None  # ticker == "222222"

    mock_kis.get_investor_trading.side_effect = side_effect

    coll = KisIntradayFlowCollector(kis_api=mock_kis)
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
    print(f"  [PASS] _to_float 8케이스 안전 변환")


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
    """frozen=True + raw_payload(dict) hash 안전성 — set/dict 키 사용 가능."""
    payload = _make_mock_payload()
    coll = _make_collector_with_mock(payload)
    snap = coll.collect_snapshot("005930")

    # raw_payload 가 dict 인데 frozen → field(hash=False) 처리됨. hash() 호출 가능해야 함.
    try:
        snap_set = {snap}
        assert len(snap_set) == 1
    except TypeError as e:
        raise AssertionError(f"frozen + dict field hash 충돌: {e}")
    print(f"  [PASS] frozen + raw_payload(dict) hash 안전 (set 사용 가능)")


def test_live_kis_call(live: bool = False):
    """실거래 종목 1개로 KIS API 한 번 호출 (선택)."""
    if not live:
        print(f"  [SKIP] --live 플래그 시 활성화")
        return

    coll = KisIntradayFlowCollector()  # 기본 KIS 싱글톤
    snap = coll.collect_snapshot("005930")
    print(f"  [LIVE] is_valid={snap.is_valid}, "
          f"inst={snap.inst_net_buy_estimated and f'{snap.inst_net_buy_estimated:,.0f}원'}, "
          f"foreign_3d={snap.foreign_net_buy_3d and f'{snap.foreign_net_buy_3d:,.0f}원'}, "
          f"foreign_ratio={snap.foreign_ratio}%")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실거래 KIS 호출 sanity check")
    args = parser.parse_args()

    print("=== Phase 1-2 KisIntradayFlowCollector 단위 테스트 ===\n")
    tests = [
        test_normal_snapshot,
        test_to_layer1_features_keys,
        test_invalid_ticker,
        test_empty_response,
        test_partial_daily,
        test_stale_date,
        test_zero_close_price,
        test_corrupt_daily_fields,
        test_api_exception,
        test_universe_partial_failure,
        test_to_float_helper,
        test_snapshot_time_default,
        test_empty_universe,
        test_hash_safety_with_raw_payload,
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
