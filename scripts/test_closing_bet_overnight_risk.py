"""scripts/test_closing_bet_overnight_risk.py

Phase 1-5b: ``OvernightRiskFilter`` 단위 테스트.

PRD 4-2 매매 중지 + 4-3 비중 축소 + 1-5a DART 통합 검증.

실행:
    venv/bin/python scripts/test_closing_bet_overnight_risk.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.engines.overnight_risk_filter import (
    DEFAULT_FULL_SIZE_FACTOR,
    DEFAULT_REDUCED_SIZE_FACTOR,
    DEFAULT_US_FUTURES_REDUCE_THRESHOLD,
    DEFAULT_US_FUTURES_SKIP_THRESHOLD,
    OvernightRiskAssessment,
    OvernightRiskFilter,
    ZERO_SIZE_FACTOR,
    _safe_float,
    _get_attr_or_key,
)


# ===== Fixtures =====


@dataclass
class MockDartSnapshot:
    """1-5a DartDisclosureSnapshot 의 핵심 필드만 모킹."""
    exclude_by_disclosure: bool = False
    exclusion_reason: str = None
    has_positive_disclosure: bool = False


def _normal_market() -> dict:
    """정상 시장 (모든 임계값 안전)."""
    return {
        "us_futures_change_pct": -0.005,
        "vkospi": 20.0,
        "kospi_change_pct": -0.01,
        "usd_krw_change_pct": 0.005,
    }


# ===== 테스트 =====


def test_normal_market_no_dart():
    """정상 시장 + DART None → can_enter=True, size=1.0."""
    rf = OvernightRiskFilter()
    a = rf.assess("005930", _normal_market(), dart_snapshot=None)
    assert a.can_enter is True
    assert a.final_size_factor == DEFAULT_FULL_SIZE_FACTOR
    assert a.skip_today is False
    assert a.exclude_by_dart is False
    assert "정상" in a.decision_reason
    print(f"  [PASS] 정상 시장 → can_enter=True, size=1.0")


def test_us_futures_skip():
    """미국 선물 -1.5% 이하 → skip_today=True, can_enter=False."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["us_futures_change_pct"] = -0.02
    a = rf.assess("005930", md)
    assert a.skip_today is True
    assert a.can_enter is False
    assert a.final_size_factor == ZERO_SIZE_FACTOR
    assert "미국 선물" in a.skip_reason
    print(f"  [PASS] 미국 선물 -2.0% → skip_today")


def test_us_futures_skip_boundary():
    """미국 선물 -1.5% 정확 (=) → skip (≤ 임계)."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["us_futures_change_pct"] = DEFAULT_US_FUTURES_SKIP_THRESHOLD  # -0.015
    a = rf.assess("005930", md)
    assert a.skip_today is True
    print(f"  [PASS] 미국 선물 -1.5% (경계) → skip (≤)")


def test_us_futures_reduce():
    """미국 선물 -1.0% ~ -1.5% → 비중 50%."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["us_futures_change_pct"] = -0.012
    a = rf.assess("005930", md)
    assert a.skip_today is False
    assert a.position_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    assert a.can_enter is True
    assert a.final_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    print(f"  [PASS] 미국 선물 -1.2% → 비중 50%")


def test_us_futures_reduce_boundary():
    """미국 선물 -1.0% 정확 → 비중 50% (≤ reduce)."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["us_futures_change_pct"] = DEFAULT_US_FUTURES_REDUCE_THRESHOLD  # -0.010
    a = rf.assess("005930", md)
    assert a.position_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    print(f"  [PASS] 미국 선물 -1.0% (경계) → 비중 50%")


def test_vkospi_skip():
    """V-KOSPI 30 이상 → skip_today=True."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["vkospi"] = 35.0
    a = rf.assess("005930", md)
    assert a.skip_today is True
    assert "V-KOSPI" in a.skip_reason
    print(f"  [PASS] V-KOSPI=35 → skip_today")


def test_vkospi_skip_with_us_futures_skip():
    """미국 선물 + V-KOSPI 모두 위반 → 이유 누적."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["us_futures_change_pct"] = -0.02
    md["vkospi"] = 35.0
    a = rf.assess("005930", md)
    assert a.skip_today is True
    assert "미국 선물" in a.skip_reason
    assert "V-KOSPI" in a.skip_reason
    print(f"  [PASS] 미국 선물 + V-KOSPI 모두 위반 → 이유 누적")


def test_usd_krw_alert():
    """USD/KRW +1.5% 이상 → 비중 50%."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["usd_krw_change_pct"] = 0.02
    a = rf.assess("005930", md)
    assert a.position_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    assert any("USD/KRW" in w for w in a.market_warnings)
    print(f"  [PASS] USD/KRW +2.0% → 비중 50%")


def test_kospi_alert():
    """KOSPI -2% 이상 하락 → 비중 50%."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["kospi_change_pct"] = -0.025
    a = rf.assess("005930", md)
    assert a.position_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    assert any("KOSPI" in w for w in a.market_warnings)
    print(f"  [PASS] KOSPI -2.5% → 비중 50%")


def test_multiple_reduce_signals():
    """다중 비중 축소 신호 → size_factor 는 최소값 (50%)."""
    rf = OvernightRiskFilter()
    md = {
        "us_futures_change_pct": -0.012,    # 50%
        "vkospi": 20.0,
        "kospi_change_pct": -0.025,          # 50%
        "usd_krw_change_pct": 0.02,           # 50%
    }
    a = rf.assess("005930", md)
    assert a.position_size_factor == DEFAULT_REDUCED_SIZE_FACTOR
    # warnings 3건 (미국선물 + USD/KRW + KOSPI)
    assert sum(1 for w in a.market_warnings if "비중" in w) == 3
    print(f"  [PASS] 다중 비중 축소 신호 → 50% 유지 (3개 warning)")


def test_dart_exclusion_only():
    """시장 정상 + DART 즉시제외 → can_enter=False."""
    rf = OvernightRiskFilter()
    snap = MockDartSnapshot(
        exclude_by_disclosure=True,
        exclusion_reason="DART 즉시제외: '유상증자' in '유상증자 결정'",
    )
    a = rf.assess("005930", _normal_market(), dart_snapshot=snap)
    assert a.skip_today is False
    assert a.exclude_by_dart is True
    assert a.can_enter is False
    assert a.final_size_factor == ZERO_SIZE_FACTOR
    assert "DART 즉시제외" in a.decision_reason
    print(f"  [PASS] DART 즉시제외 → can_enter=False")


def test_dart_positive_disclosure():
    """DART 호재만 → can_enter=True + has_positive_disclosure=True."""
    rf = OvernightRiskFilter()
    snap = MockDartSnapshot(has_positive_disclosure=True)
    a = rf.assess("005930", _normal_market(), dart_snapshot=snap)
    assert a.can_enter is True
    assert a.has_positive_disclosure is True
    assert a.exclude_by_dart is False
    print(f"  [PASS] DART 호재 → can_enter=True, has_positive=True")


def test_skip_priority_over_dart_exclusion():
    """시장 매매 중단 > DART 즉시제외 (skip 이 우선)."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["vkospi"] = 35.0   # skip
    snap = MockDartSnapshot(exclude_by_disclosure=True, exclusion_reason="유상증자")
    a = rf.assess("005930", md, dart_snapshot=snap)
    assert a.skip_today is True
    assert a.exclude_by_dart is True   # 정보는 보존
    assert a.can_enter is False
    # decision_reason 에는 시장 매매 중단이 우선
    assert "매매 중단" in a.decision_reason or "V-KOSPI" in a.decision_reason
    print(f"  [PASS] skip > DART 우선순위 (decision_reason=시장 매매 중단)")


def test_missing_market_data_phase1():
    """Phase 1 데이터 결손 정책: None 시 해당 룰 비활성, 진입 가능 유지."""
    rf = OvernightRiskFilter()
    md = {
        "us_futures_change_pct": None,
        "vkospi": None,
        "kospi_change_pct": None,
        "usd_krw_change_pct": None,
    }
    a = rf.assess("005930", md)
    assert a.skip_today is False
    assert a.can_enter is True
    assert a.position_size_factor == DEFAULT_FULL_SIZE_FACTOR
    # warnings 에 "데이터 없음" 2건 (미국선물 + V-KOSPI)
    no_data_count = sum(1 for w in a.market_warnings if "데이터 없음" in w)
    assert no_data_count == 2
    print(f"  [PASS] 모든 시장 데이터 None → 진입 가능 (룰 비활성, warning 2건)")


def test_corrupt_market_data():
    """비정상 입력 (문자열/NaN/inf/bool) → None 처리, 룰 비활성."""
    rf = OvernightRiskFilter()
    md = {
        "us_futures_change_pct": "down",
        "vkospi": float("nan"),
        "kospi_change_pct": float("inf"),
        "usd_krw_change_pct": True,
    }
    a = rf.assess("005930", md)
    assert a.skip_today is False
    assert a.position_size_factor == DEFAULT_FULL_SIZE_FACTOR
    print(f"  [PASS] 비정상 입력 4종 → None 처리, 영향 없음")


def test_empty_market_data():
    """market_data = None 또는 {} → 모든 룰 비활성."""
    rf = OvernightRiskFilter()
    a1 = rf.assess("005930", market_data=None)
    a2 = rf.assess("005930", market_data={})
    for a in (a1, a2):
        assert a.skip_today is False
        assert a.can_enter is True
        assert a.position_size_factor == DEFAULT_FULL_SIZE_FACTOR
    print(f"  [PASS] market_data None/{{}} → 진입 가능")


def test_init_validation():
    """__init__ 임계값 순서 검증 + 음수/양수 검증."""
    # us_futures skip >= reduce (잘못된 순서)
    try:
        OvernightRiskFilter(us_futures_skip_threshold=-0.005, us_futures_reduce_threshold=-0.010)
        raise AssertionError("순서 위반 통과됨")
    except ValueError:
        pass
    # reduced_size_factor 범위 위반
    try:
        OvernightRiskFilter(reduced_size_factor=1.5)
        raise AssertionError("size factor 1.5 통과됨")
    except ValueError:
        pass
    try:
        OvernightRiskFilter(reduced_size_factor=0.0)
        raise AssertionError("size factor 0 통과됨")
    except ValueError:
        pass
    # vkospi 음수
    try:
        OvernightRiskFilter(vkospi_skip_threshold=-1)
        raise AssertionError("vkospi 음수 통과됨")
    except ValueError:
        pass
    # USD/KRW 음수
    try:
        OvernightRiskFilter(usd_krw_alert_threshold=-0.01)
        raise AssertionError("USD/KRW 음수 통과됨")
    except ValueError:
        pass
    # KOSPI 양수
    try:
        OvernightRiskFilter(kospi_alert_threshold=0.01)
        raise AssertionError("KOSPI 양수 통과됨")
    except ValueError:
        pass
    print(f"  [PASS] __init__ 검증 6케이스 차단")


def test_from_settings():
    """settings.yaml external_risk 섹션 → 엔진 생성."""
    er = {
        "us_futures_skip_threshold": -0.015,
        "us_futures_reduce_threshold": -0.010,
        "usd_krw_alert_threshold": 0.015,
        "kospi_alert_threshold": -0.02,
        "vkospi_skip_threshold": 30,
    }
    rf = OvernightRiskFilter.from_settings(er)
    assert rf.us_futures_skip_threshold == -0.015
    assert rf.vkospi_skip_threshold == 30.0

    # 빈 dict → 모든 default
    rf2 = OvernightRiskFilter.from_settings({})
    assert rf2.us_futures_skip_threshold == DEFAULT_US_FUTURES_SKIP_THRESHOLD

    # 비-dict
    try:
        OvernightRiskFilter.from_settings("invalid")
        raise AssertionError("비-dict 통과됨")
    except TypeError:
        pass
    print(f"  [PASS] from_settings 정상/빈/타입오류 3케이스")


def test_dart_snapshot_dict_compat():
    """dart_snapshot 이 dict 형태로 들어와도 동작 (duck-typing)."""
    rf = OvernightRiskFilter()
    snap_dict = {
        "exclude_by_disclosure": True,
        "exclusion_reason": "DART: 유상증자",
        "has_positive_disclosure": False,
    }
    a = rf.assess("005930", _normal_market(), dart_snapshot=snap_dict)
    assert a.exclude_by_dart is True
    assert a.can_enter is False
    print(f"  [PASS] dart_snapshot dict 형태 호환 (duck-typing)")


def test_universe_assessment():
    """assess_for_universe — 시장 1회 + 종목별 DART 평가."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    snaps = {
        "005930": MockDartSnapshot(),                                          # 정상
        "000660": MockDartSnapshot(exclude_by_disclosure=True,
                                    exclusion_reason="유상증자"),               # DART 제외
        "035420": MockDartSnapshot(has_positive_disclosure=True),               # 호재
    }
    results = rf.assess_for_universe(md, snaps)
    assert len(results) == 3
    assert results["005930"].can_enter is True
    assert results["000660"].can_enter is False
    assert results["035420"].can_enter is True
    assert results["035420"].has_positive_disclosure is True
    print(f"  [PASS] universe 3종목 평가 (정상/제외/호재)")


def test_universe_market_skip():
    """시장 skip 시 모든 종목 can_enter=False."""
    rf = OvernightRiskFilter()
    md = _normal_market()
    md["vkospi"] = 35.0
    snaps = {
        "005930": MockDartSnapshot(),
        "000660": MockDartSnapshot(),
    }
    results = rf.assess_for_universe(md, snaps)
    for ticker, a in results.items():
        assert a.skip_today is True
        assert a.can_enter is False
        assert a.final_size_factor == ZERO_SIZE_FACTOR
    print(f"  [PASS] 시장 skip → 모든 종목 can_enter=False")


def test_assessment_immutable():
    """OvernightRiskAssessment frozen."""
    rf = OvernightRiskFilter()
    a = rf.assess("005930", _normal_market())
    try:
        a.can_enter = False
        raise AssertionError("frozen 변경됨")
    except (AttributeError, Exception):
        pass
    print(f"  [PASS] Assessment frozen 불변")


def test_helpers():
    """_safe_float / _get_attr_or_key."""
    assert _safe_float(1.5) == 1.5
    assert _safe_float("1.5") == 1.5
    assert _safe_float(None) is None
    assert _safe_float(True) is None
    assert _safe_float("nan") is None  # str "nan" → float("nan") → NaN → None
    assert _safe_float(float("nan")) is None
    assert _safe_float(float("inf")) is None
    assert _safe_float("abc") is None

    # _get_attr_or_key — dict
    assert _get_attr_or_key({"k": 1}, "k", -1) == 1
    assert _get_attr_or_key({"k": 1}, "missing", -1) == -1
    # _get_attr_or_key — object
    snap = MockDartSnapshot(has_positive_disclosure=True)
    assert _get_attr_or_key(snap, "has_positive_disclosure", False) is True
    assert _get_attr_or_key(snap, "missing", -1) == -1
    print(f"  [PASS] 헬퍼 _safe_float (8) + _get_attr_or_key (4)")


def test_assessed_at_default():
    """assessed_at 미지정 시 now_kst()."""
    rf = OvernightRiskFilter()
    before = now_kst()
    a = rf.assess("005930", _normal_market())
    after = now_kst()
    assert a.assessed_at.tzinfo is not None
    assert before <= a.assessed_at <= after
    print(f"  [PASS] assessed_at 자동 = {a.assessed_at.strftime('%H:%M:%S %Z')}")


def main():
    print("=== Phase 1-5b OvernightRiskFilter 단위 테스트 ===\n")
    tests = [
        test_normal_market_no_dart,
        test_us_futures_skip,
        test_us_futures_skip_boundary,
        test_us_futures_reduce,
        test_us_futures_reduce_boundary,
        test_vkospi_skip,
        test_vkospi_skip_with_us_futures_skip,
        test_usd_krw_alert,
        test_kospi_alert,
        test_multiple_reduce_signals,
        test_dart_exclusion_only,
        test_dart_positive_disclosure,
        test_skip_priority_over_dart_exclusion,
        test_missing_market_data_phase1,
        test_corrupt_market_data,
        test_empty_market_data,
        test_init_validation,
        test_from_settings,
        test_dart_snapshot_dict_compat,
        test_universe_assessment,
        test_universe_market_skip,
        test_assessment_immutable,
        test_helpers,
        test_assessed_at_default,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
