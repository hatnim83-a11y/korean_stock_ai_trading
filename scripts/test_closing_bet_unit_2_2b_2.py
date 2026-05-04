"""단위 2-2b-2 검증: KIND severity 통합 (universe_filters + universe_provider_v2 + main_orchestrator).

시나리오:
    KI-1:  apply_attribute_filters severity_map 주입 → severity ≥ 3 사전 제외
    KI-2:  apply_attribute_filters severity_map=None → 기존 동작 유지 (회귀)
    KI-3:  apply_attribute_filters severity_map={} → 사전 제외 0건
    KI-4:  severity 1, 2 → 통과 (3 미만)
    KI-5:  severity 3 + 시총 미달 동시 → kind_severity_3 사유 우선 (first-rejection-only)
    KI-6:  apply_all_filters severity_map 전달 정상
    KI-7:  apply_all_filters severity_map=None → 기존 동작 유지
    KI-8:  get_universe_v2_filtered severity_map 인자 통과
    KI-9:  SEVERITY_EXCLUDE_THRESHOLD 모듈 상수 = 3
    KI-10: KIND 사전 제외 후 모든 종목 탈락 시 빈 결과 + rejected dict 정합

실행:
    venv/bin/python scripts/test_closing_bet_unit_2_2b_2.py
"""
from __future__ import annotations

import sys
from datetime import date as date_cls
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import universe_filters as uf
from closing_bet_system.collectors import universe_provider_v2 as uv2


def _mock_market_data(tickers: list[str]) -> dict[str, dict]:
    """모든 종목이 PRD 4-1/4-4 통과하는 정상 데이터.

    close=8000 + 52w_high=10000 → drop=20% (< 30% 임계값) 통과.
    """
    return {
        t: {
            "market_cap": 100_000_000_000,  # 1000억 (>= 500억)
            "close": 8_000,                  # 8천원 (>= 1000원, 52w_high 대비 -20%)
            "change_rate": 1.0,              # +1% (상한가 아님)
            "trading_value": 50_000_000_000, # 500억 (당일, >= 100억)
        }
        for t in tickers
    }


def test_KI_1_severity_사전_제외():
    uf.reset_cache()
    tickers = ["000001", "000002", "000003"]
    severity_map = {"000001": 3, "000002": 1}  # 000001 만 제외

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000), \
         patch.object(uf, "_fetch_avg_value_20d", return_value=10_000_000_000):
        passed, rejected = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=severity_map
        )

    assert "000001" in rejected, f"severity 3 사전 제외 실패: {rejected}"
    assert rejected["000001"] == "kind_severity_3"
    assert "000001" not in passed
    assert "000002" in passed, f"severity 1 종목 통과 실패: {passed}"
    assert "000003" in passed
    print("[PASS] KI-1: severity 3 사전 제외 + severity < 3 통과")


def test_KI_2_severity_map_None_회귀():
    uf.reset_cache()
    tickers = ["000001", "000002"]

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000):
        passed_none, rejected_none = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=None
        )
        uf.reset_cache()
        passed_unset, rejected_unset = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4)
        )

    assert passed_none == passed_unset
    assert rejected_none == rejected_unset
    assert "kind_severity_3" not in str(rejected_none.values())
    print("[PASS] KI-2: severity_map=None / 미지정 → 동일 결과 (회귀 안전)")


def test_KI_3_severity_map_empty():
    uf.reset_cache()
    tickers = ["000001", "000002"]

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000):
        passed, rejected = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map={}
        )

    assert all(t in passed for t in tickers), f"빈 severity_map → 모두 통과 실패: {passed}"
    assert not any("kind_severity" in r for r in rejected.values())
    print("[PASS] KI-3: severity_map={} → 사전 제외 0건")


def test_KI_4_severity_1_2_통과():
    uf.reset_cache()
    tickers = ["000001", "000002"]
    severity_map = {"000001": 1, "000002": 2}  # 둘 다 임계값 3 미만

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000):
        passed, rejected = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=severity_map
        )

    assert "000001" in passed, "severity 1 통과 실패"
    assert "000002" in passed, "severity 2 통과 실패"
    assert not any("kind_severity" in r for r in rejected.values())
    print("[PASS] KI-4: severity 1, 2 → 통과 (임계값 3 미만)")


def test_KI_5_severity_3_first_rejection_only():
    uf.reset_cache()
    tickers = ["000001"]
    severity_map = {"000001": 3}

    # 시총 미달 데이터 — 하지만 severity 3 우선이라 kind 사유로 기록
    market_data = {"000001": {
        "market_cap": 1_000_000_000,  # 10억 (시총 미달)
        "close": 5_000,
        "change_rate": 1.0,
        "trading_value": 50_000_000_000,
    }}

    with patch.object(uf, "_fetch_market_data_bulk", return_value=market_data):
        passed, rejected = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=severity_map
        )

    assert rejected["000001"] == "kind_severity_3", \
        f"first-rejection (kind 우선) 실패: {rejected['000001']}"
    print("[PASS] KI-5: severity 3 + 시총 미달 동시 → kind 사유 우선 (first-rejection-only)")


def test_KI_6_apply_all_filters_severity_전달():
    uf.reset_cache()
    tickers = ["000001", "000002"]
    severity_map = {"000001": 3}

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000), \
         patch.object(uf, "_fetch_avg_value_20d", return_value=10_000_000_000):
        passed, rejected = uf.apply_all_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=severity_map
        )

    assert "000001" in rejected
    assert rejected["000001"] == "kind_severity_3"
    assert "000002" in passed
    print("[PASS] KI-6: apply_all_filters → severity_map 전달 정상")


def test_KI_7_apply_all_filters_severity_None_회귀():
    uf.reset_cache()
    tickers = ["000001"]

    with patch.object(uf, "_fetch_market_data_bulk", return_value=_mock_market_data(tickers)), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000), \
         patch.object(uf, "_fetch_avg_value_20d", return_value=10_000_000_000):
        passed, rejected = uf.apply_all_filters(tickers, today=date_cls(2026, 5, 4))

    # severity_map 미지정 → 기존 동작 (시총/주가 등으로만 평가)
    assert "000001" in passed
    print("[PASS] KI-7: apply_all_filters severity_map 미지정 → 기존 동작 (회귀)")


def test_KI_8_get_universe_v2_filtered_severity_전달():
    uv2.reset_cache()
    uf.reset_cache()
    severity_map = {"000001": 3}

    # universe v2 출처 결과를 ["000001", "000002"] 로 모킹
    with patch.object(uv2, "get_universe_v2", return_value=["000001", "000002"]), \
         patch.object(uf, "_fetch_market_data_bulk",
                       return_value=_mock_market_data(["000001", "000002"])), \
         patch.object(uf, "_fetch_52w_high", return_value=10_000), \
         patch.object(uf, "_fetch_avg_value_20d", return_value=10_000_000_000):
        result = uv2.get_universe_v2_filtered(severity_map=severity_map)

    assert "000001" not in result, "severity 3 사전 제외 실패"
    assert "000002" in result, "severity < 3 통과 실패"
    print("[PASS] KI-8: get_universe_v2_filtered severity_map 인자 통과 + 사전 제외")


def test_KI_9_severity_threshold_상수():
    assert uf.SEVERITY_EXCLUDE_THRESHOLD == 3, \
        f"SEVERITY_EXCLUDE_THRESHOLD 값 변경 의심: {uf.SEVERITY_EXCLUDE_THRESHOLD}"
    # kind_alert_collector 와 동기 검증
    from closing_bet_system.collectors.kind_alert_collector import (
        SEVERITY_EXCLUDE_THRESHOLD as KAC_THRESHOLD,
    )
    assert uf.SEVERITY_EXCLUDE_THRESHOLD == KAC_THRESHOLD, \
        "universe_filters 와 kind_alert_collector 임계값 불일치"
    print("[PASS] KI-9: SEVERITY_EXCLUDE_THRESHOLD = 3 (양 모듈 동기)")


def test_KI_10_모두_탈락_시_정합():
    uf.reset_cache()
    tickers = ["000001", "000002"]
    severity_map = {"000001": 3, "000002": 3}  # 모두 제외

    with patch.object(uf, "_fetch_market_data_bulk", return_value={}):
        passed, rejected = uf.apply_attribute_filters(
            tickers, today=date_cls(2026, 5, 4), severity_map=severity_map
        )

    assert passed == [], f"전부 탈락 시 빈 list 실패: {passed}"
    assert len(rejected) == 2
    assert all(r.startswith("kind_severity") for r in rejected.values())
    print("[PASS] KI-10: 모든 종목 KIND 사전 제외 → passed=[], rejected 정합")


# ===== 실행 =====


def main():
    tests = [
        test_KI_1_severity_사전_제외,
        test_KI_2_severity_map_None_회귀,
        test_KI_3_severity_map_empty,
        test_KI_4_severity_1_2_통과,
        test_KI_5_severity_3_first_rejection_only,
        test_KI_6_apply_all_filters_severity_전달,
        test_KI_7_apply_all_filters_severity_None_회귀,
        test_KI_8_get_universe_v2_filtered_severity_전달,
        test_KI_9_severity_threshold_상수,
        test_KI_10_모두_탈락_시_정합,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    total = len(tests)
    if failed == 0:
        print(f"\n✅ {total}/{total} 시나리오 PASS")
        return 0
    else:
        print(f"\n❌ {failed}/{total} 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
