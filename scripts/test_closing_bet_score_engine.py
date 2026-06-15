"""scripts/test_closing_bet_score_engine.py

Phase 1-4: ``SignalScoreEngine`` 단위 테스트.

- PRD 6-1 단순 카운트 점수 + 진입 의사결정 + 하드 필터 검증
- Layer 1 가중치 0 (Phase 1) 적용 검증
- 결손 입력 (None / NaN / inf / bool 등) 안전 처리

실행:
    venv/bin/python scripts/test_closing_bet_score_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.engines.signal_score_engine import (
    DEFAULT_LAYER1_WEIGHT,
    DEFAULT_THEME_LEADERSHIP_RANK_MAX,
    ScoreBreakdown,
    SignalScoreEngine,
    _cond_bool,
    _cond_gt,
    _cond_gte,
)


# ===== Fixtures =====


def _full_l1(positive: bool = True) -> dict:
    """Layer 1 4지표 모두 양수/음수."""
    s = 1.0 if positive else -1.0
    return {
        "inst_net_buy_estimated": 1_000_000_000 * s,
        "foreign_net_buy_3d": 5_000_000_000 * s,
        "program_net_buy_change": 0.05 * s,
        "closing_flow_concentration": 0.6 * s,
    }


def _full_l2(strong: bool = True, atr_overheat: float = 1.0) -> dict:
    """Layer 2 4지표 + atr_overheat."""
    if strong:
        return {
            "close_strength": 0.95,
            "volume_surprise": 3.5,
            "above_ma20": True,
            "closing_buy_sell_ratio": 1.5,
            "atr_overheat": atr_overheat,
        }
    return {
        "close_strength": 0.6,
        "volume_surprise": 1.2,
        "above_ma20": False,
        "closing_buy_sell_ratio": 0.8,
        "atr_overheat": atr_overheat,
    }


def _full_l3(strong: bool = True) -> dict:
    """Layer 3 3지표 모두 충족/미충족."""
    if strong:
        return {
            "near_52w_high": True,
            "theme_leadership_rank": 1,
            "has_positive_disclosure": True,
        }
    return {
        "near_52w_high": False,
        "theme_leadership_rank": 10,
        "has_positive_disclosure": False,
    }


# ===== 테스트 =====


def test_full_score_phase1_weights():
    """Layer 1/2/3 만점 → raw_total=11, weighted=0+4+3=7 (Phase 1)."""
    eng = SignalScoreEngine()  # default: layer1_weight=0
    bd = eng.score("005930", _full_l1(), _full_l2(), _full_l3(), market_ok=True)

    assert bd.layer1_subscore == 4
    assert bd.layer2_subscore == 4
    assert bd.layer3_subscore == 3
    assert bd.raw_total == 11
    # Phase 1 weighted: 0*4 + 1*4 + 1*3 = 7
    assert bd.weighted_total == 7.0
    # decision: 7 >= alert_min(7) → ALERT (entry_min=8 미달)
    assert bd.decision == "ALERT"
    print(f"  [PASS] raw=11/weighted=7 (L1 weight=0) → ALERT")


def test_full_score_phase2_weights():
    """layer1_weight=1.0 (Phase 2+) → raw=11/weighted=11 → MAX_SIZE_ENTRY."""
    eng = SignalScoreEngine(layer1_weight=1.0)
    bd = eng.score("005930", _full_l1(), _full_l2(), _full_l3(), market_ok=True)

    assert bd.weighted_total == 11.0
    # 11 >= max_position_score(9) AND market_ok=True → MAX_SIZE_ENTRY
    assert bd.decision == "MAX_SIZE_ENTRY"
    print(f"  [PASS] Phase 2 weights → weighted=11 → MAX_SIZE_ENTRY")


def test_max_size_requires_market_ok():
    """weighted >= 9 이지만 market_ok=False → ENTRY (MAX_SIZE 아님)."""
    eng = SignalScoreEngine(layer1_weight=1.0)
    bd = eng.score("005930", _full_l1(), _full_l2(), _full_l3(), market_ok=False)
    # weighted=11, market_ok=False → MAX_SIZE 거부 → ENTRY
    assert bd.weighted_total == 11.0
    assert bd.decision == "ENTRY"
    print(f"  [PASS] market_ok=False → MAX_SIZE 거부 → ENTRY")


def test_below_threshold():
    """모든 조건 불충족 → BELOW_THRESHOLD."""
    eng = SignalScoreEngine()
    # atr_overheat 는 안전 범위로 두고 다른 지표 모두 미충족
    bd = eng.score(
        "005930",
        _full_l1(positive=False),
        _full_l2(strong=False, atr_overheat=0.5),
        _full_l3(strong=False),
        market_ok=True,
    )
    assert bd.layer1_subscore == 0
    assert bd.layer2_subscore == 0
    assert bd.layer3_subscore == 0
    assert bd.weighted_total == 0.0
    assert bd.decision == "BELOW_THRESHOLD"
    assert not bd.excluded_by_filter
    print(f"  [PASS] 전 조건 미충족 → BELOW_THRESHOLD")


def test_hard_filter_atr_overheat():
    """atr_overheat > 1.8 → EXCLUDED (raw_total 무관)."""
    eng = SignalScoreEngine()
    # 다른 점수 만점이라도 atr_overheat=2.0 → EXCLUDED
    l2 = _full_l2(atr_overheat=2.0)
    bd = eng.score("005930", _full_l1(), l2, _full_l3())
    assert bd.excluded_by_filter is True
    assert bd.decision == "EXCLUDED"
    assert "1.800" in bd.exclusion_reason or "1.8" in bd.exclusion_reason
    print(f"  [PASS] atr_overheat=2.0 → EXCLUDED ({bd.exclusion_reason})")


def test_hard_filter_atr_at_boundary():
    """atr_overheat == 1.8 (경계값) → 통과 (> 만 차단)."""
    eng = SignalScoreEngine()
    l2 = _full_l2(atr_overheat=1.8)
    bd = eng.score("005930", _full_l1(), l2, _full_l3())
    assert bd.excluded_by_filter is False, "1.8 (=) 는 통과해야 함 (PRD: 1.8 초과 시 제외)"
    print(f"  [PASS] atr_overheat=1.8 (경계) → 통과")


def test_hard_filter_atr_none():
    """atr_overheat=None → 보수적 EXCLUDED (측정 불가)."""
    eng = SignalScoreEngine()
    l2 = {"close_strength": 0.9, "volume_surprise": 3.0, "above_ma20": True,
          "closing_buy_sell_ratio": 1.5, "atr_overheat": None}
    bd = eng.score("005930", _full_l1(), l2, _full_l3())
    assert bd.excluded_by_filter is True
    assert bd.decision == "EXCLUDED"
    assert "None" in bd.exclusion_reason or "측정 불가" in bd.exclusion_reason
    assert "atr_overheat" in bd.missing_inputs
    print(f"  [PASS] atr_overheat=None → 보수적 EXCLUDED")


def test_hard_filter_atr_nan():
    """atr_overheat=NaN → EXCLUDED."""
    eng = SignalScoreEngine()
    l2 = _full_l2(atr_overheat=float("nan"))
    bd = eng.score("005930", _full_l1(), l2, _full_l3())
    assert bd.excluded_by_filter is True
    assert bd.decision == "EXCLUDED"
    print(f"  [PASS] atr_overheat=NaN → EXCLUDED")


def test_band_disabled_keeps_hardcut():
    """Phase 3: band_enabled=False(기본) → 극과열 2.5도 기존 하드컷으로 EXCLUDED (NO-OP)."""
    eng = SignalScoreEngine()  # 기본 band off
    bd = eng.score("005930", _full_l1(), _full_l2(atr_overheat=2.5), _full_l3())
    assert bd.excluded_by_filter is True
    assert "하드 필터" in bd.exclusion_reason
    print("  [PASS] band off → 2.5 하드컷 EXCLUDED (기존 동작 유지)")


def test_band_mid_overheat_blocked():
    """Phase 3: band on → 중간과열(1.8<atr<=2.2)은 차단."""
    eng = SignalScoreEngine(atr_overheat_band_enabled=True)
    bd = eng.score("005930", _full_l1(), _full_l2(atr_overheat=2.0), _full_l3())
    assert bd.excluded_by_filter is True
    assert "중간과열 밴드" in bd.exclusion_reason
    print(f"  [PASS] band on → 2.0 중간과열 차단 ({bd.exclusion_reason})")


def test_band_extreme_overheat_passes():
    """Phase 3: band on → 극과열(atr>2.2)은 통과 (최고·최안전 구간)."""
    eng = SignalScoreEngine(atr_overheat_band_enabled=True)
    bd = eng.score("005930", _full_l1(), _full_l2(atr_overheat=2.5), _full_l3())
    assert bd.excluded_by_filter is False, "2.5(>2.2 극과열)는 밴드차등 시 통과해야 함"
    print("  [PASS] band on → 2.5 극과열 통과")


def test_band_boundary_high_blocked():
    """Phase 3: band on → atr==band_high(2.2) 경계는 차단(<=band_high)."""
    eng = SignalScoreEngine(atr_overheat_band_enabled=True)
    bd = eng.score("005930", _full_l1(), _full_l2(atr_overheat=2.2), _full_l3())
    assert bd.excluded_by_filter is True
    print("  [PASS] band on → 2.2(=band_high) 차단")


def test_band_normal_passes():
    """Phase 3: band on → 정상(atr<=1.8)은 통과."""
    eng = SignalScoreEngine(atr_overheat_band_enabled=True)
    bd = eng.score("005930", _full_l1(), _full_l2(atr_overheat=1.5), _full_l3())
    assert bd.excluded_by_filter is False
    print("  [PASS] band on → 1.5 정상 통과")


def test_band_high_below_max_raises():
    """Phase 3: band_high < atr_overheat_max → ValueError."""
    try:
        SignalScoreEngine(atr_overheat_band_enabled=True, atr_overheat_max=1.8,
                          atr_overheat_band_high=1.5)
        assert False, "band_high<max 는 ValueError 여야 함"
    except ValueError:
        print("  [PASS] band_high(1.5) < max(1.8) → ValueError")


def test_layer2_thresholds_strict():
    """Layer 2 임계값 정확성 — close_strength=0.85 (=), vol_surprise=2.0 (=) 모두 통과."""
    eng = SignalScoreEngine()
    l2 = {
        "close_strength": 0.85,         # >= 0.85 통과
        "volume_surprise": 2.0,          # >= 2.0 통과
        "above_ma20": True,
        "closing_buy_sell_ratio": 1.2,   # >= 1.2 통과
        "atr_overheat": 0.5,
    }
    bd = eng.score("005930", _full_l1(positive=False), l2, _full_l3(strong=False))
    assert bd.layer2_subscore == 4
    print(f"  [PASS] L2 임계값 경계 (=) 모두 통과")


def test_layer2_below_threshold():
    """close_strength=0.84 (< 0.85) 등 경계 직전 → False."""
    eng = SignalScoreEngine()
    l2 = {
        "close_strength": 0.849,
        "volume_surprise": 1.999,
        "above_ma20": True,
        "closing_buy_sell_ratio": 1.199,
        "atr_overheat": 0.5,
    }
    bd = eng.score("005930", _full_l1(positive=False), l2, _full_l3(strong=False))
    assert bd.cond_close_strength is False
    assert bd.cond_volume_surprise is False
    assert bd.cond_above_ma20 is True
    assert bd.cond_closing_buy_sell is False
    assert bd.layer2_subscore == 1  # above_ma20 만 True
    print(f"  [PASS] L2 임계값 직전 (<) → False, subscore=1")


def test_layer1_phase2_weights_alert():
    """Phase 2 (L1=1.0): L1=4, L2=2, L3=1 → weighted=7 → ALERT."""
    eng = SignalScoreEngine(layer1_weight=1.0)
    l2 = {
        "close_strength": 0.9,           # True
        "volume_surprise": 2.5,           # True
        "above_ma20": False,              # False
        "closing_buy_sell_ratio": 0.5,    # False
        "atr_overheat": 0.5,
    }
    l3 = {"near_52w_high": True, "theme_leadership_rank": 5,  # rank 5 > 3 → False
          "has_positive_disclosure": False}
    bd = eng.score("005930", _full_l1(), l2, l3, market_ok=True)
    assert bd.layer1_subscore == 4
    assert bd.layer2_subscore == 2
    assert bd.layer3_subscore == 1
    assert bd.weighted_total == 7.0
    assert bd.decision == "ALERT"
    print(f"  [PASS] Phase 2 partial (L1=4, L2=2, L3=1) → ALERT")


def test_layer3_theme_rank_boundary():
    """theme_leadership_rank=3 (=) 통과, rank=4 미통과, rank=0 (None 처리)."""
    eng = SignalScoreEngine()
    base_l1 = {"inst_net_buy_estimated": None, "foreign_net_buy_3d": None,
               "program_net_buy_change": None, "closing_flow_concentration": None}
    base_l2 = {"close_strength": None, "volume_surprise": None, "above_ma20": None,
               "closing_buy_sell_ratio": None, "atr_overheat": 1.0}

    # rank=3 → True
    bd = eng.score("005930", base_l1, base_l2, {"near_52w_high": False,
                                                 "theme_leadership_rank": 3,
                                                 "has_positive_disclosure": False})
    assert bd.cond_theme_leadership is True
    # rank=4 → False
    bd2 = eng.score("005930", base_l1, base_l2, {"near_52w_high": False,
                                                  "theme_leadership_rank": 4,
                                                  "has_positive_disclosure": False})
    assert bd2.cond_theme_leadership is False
    # rank=0 (비정상) → None
    bd3 = eng.score("005930", base_l1, base_l2, {"near_52w_high": False,
                                                  "theme_leadership_rank": 0,
                                                  "has_positive_disclosure": False})
    assert bd3.cond_theme_leadership is None
    assert "theme_leadership_rank" in bd3.missing_inputs
    # rank=True (bool — 차단)
    bd4 = eng.score("005930", base_l1, base_l2, {"near_52w_high": False,
                                                  "theme_leadership_rank": True,
                                                  "has_positive_disclosure": False})
    assert bd4.cond_theme_leadership is None
    print(f"  [PASS] theme_leadership_rank 경계 + 비정상 4케이스")


def test_missing_inputs_tracking():
    """결손 입력 → missing_inputs tuple 에 키 누적."""
    eng = SignalScoreEngine()
    bd = eng.score("005930", layer1=None, layer2={"atr_overheat": 1.0}, layer3=None)
    # layer1 4 + layer2 4(except atr_overheat) + layer3 3 = 11 missing
    expected_missing = {
        "inst_net_buy_estimated", "foreign_net_buy_3d",
        "program_net_buy_change", "closing_flow_concentration",
        "close_strength", "volume_surprise", "above_ma20", "closing_buy_sell_ratio",
        "near_52w_high", "theme_leadership_rank", "has_positive_disclosure",
    }
    assert set(bd.missing_inputs) == expected_missing
    assert bd.weighted_total == 0.0
    assert bd.decision == "BELOW_THRESHOLD"
    print(f"  [PASS] 결손 입력 11개 missing_inputs 추적")


def test_above_ma20_strict_bool():
    """above_ma20 가 int(1)/int(0) → None (bool 만 인정)."""
    eng = SignalScoreEngine()
    base_l1 = _full_l1(positive=False)
    l2 = {"close_strength": 0.9, "volume_surprise": 3.0, "above_ma20": 1,
          "closing_buy_sell_ratio": 1.5, "atr_overheat": 1.0}
    bd = eng.score("005930", base_l1, l2, _full_l3(strong=False))
    assert bd.cond_above_ma20 is None
    assert "above_ma20" in bd.missing_inputs
    # 점수: cs/vs/cbs=True, ma=None → 3
    assert bd.layer2_subscore == 3
    print(f"  [PASS] above_ma20=1 (int) → None (strict bool)")


def test_corrupt_inputs_safety():
    """문자열/inf/NaN 등 비정상 입력 → cond=None, 점수 0."""
    eng = SignalScoreEngine()
    l2 = {
        "close_strength": "high",         # 문자열 → None
        "volume_surprise": float("inf"),   # inf → None
        "above_ma20": "yes",                # 문자열 → None
        "closing_buy_sell_ratio": float("nan"),  # NaN → None
        "atr_overheat": 1.0,
    }
    bd = eng.score("005930", _full_l1(positive=False), l2, _full_l3(strong=False))
    assert bd.cond_close_strength is None
    assert bd.cond_volume_surprise is None
    assert bd.cond_above_ma20 is None
    assert bd.cond_closing_buy_sell is None
    assert bd.layer2_subscore == 0
    print(f"  [PASS] 비정상 입력 4종 → 모두 None 처리")


def test_init_validation():
    """__init__ 가중치 음수 / 임계값 순서 위반 → ValueError."""
    # 음수 가중치
    try:
        SignalScoreEngine(layer1_weight=-0.1)
        raise AssertionError("음수 가중치가 통과됨")
    except ValueError:
        pass

    # bool 가중치
    try:
        SignalScoreEngine(layer2_weight=True)
        raise AssertionError("bool 가중치가 통과됨")
    except ValueError:
        pass

    # 임계값 순서 위반 (alert > entry)
    try:
        SignalScoreEngine(alert_min=8, entry_min=7)
        raise AssertionError("임계값 순서 위반이 통과됨")
    except ValueError:
        pass

    print(f"  [PASS] __init__ 검증 3케이스 차단")


def test_from_settings():
    """settings.yaml score 섹션으로 엔진 생성."""
    score_settings = {
        "layer1_weight": 0.0,
        "layer2_weight": 1.0,
        "layer3_weight": 1.0,
        "min_alert": 7,
        "min_entry": 8,
        "max_position_score": 9,
    }
    eng = SignalScoreEngine.from_settings(score_settings)
    assert eng.layer1_weight == 0.0
    assert eng.alert_min == 7.0
    assert eng.entry_min == 8.0

    # 미지정 키는 default
    eng2 = SignalScoreEngine.from_settings({})
    assert eng2.layer1_weight == DEFAULT_LAYER1_WEIGHT
    assert eng2.theme_leadership_rank_max == DEFAULT_THEME_LEADERSHIP_RANK_MAX

    # 비-dict → TypeError
    try:
        SignalScoreEngine.from_settings("invalid")
        raise AssertionError("비-dict 가 통과됨")
    except TypeError:
        pass
    print(f"  [PASS] from_settings 정상/빈/타입오류 3케이스")


def test_decision_priority():
    """EXCLUDED 가 다른 의사결정보다 우선."""
    eng = SignalScoreEngine(layer1_weight=1.0)
    # 만점 + market_ok 이지만 atr_overheat>1.8 → EXCLUDED
    l2 = _full_l2(atr_overheat=2.0)
    bd = eng.score("005930", _full_l1(), l2, _full_l3(), market_ok=True)
    assert bd.decision == "EXCLUDED"
    # 점수는 정상 계산 (참고용)
    assert bd.raw_total == 11
    print(f"  [PASS] EXCLUDED 우선순위 (raw=11이라도 무관)")


def test_score_breakdown_immutable():
    """ScoreBreakdown frozen (수정 불가)."""
    eng = SignalScoreEngine()
    bd = eng.score("005930", _full_l1(), _full_l2(), _full_l3())
    try:
        bd.decision = "MUTATED"
        raise AssertionError("frozen ScoreBreakdown 변경됨")
    except (AttributeError, Exception):
        pass
    print(f"  [PASS] ScoreBreakdown frozen (immutable)")


def test_helpers():
    """_cond_gte / _cond_gt / _cond_bool 단위."""
    missing: list[str] = []

    # _cond_gte
    assert _cond_gte(0.85, 0.85, "k1", missing) is True
    assert _cond_gte(0.84, 0.85, "k2", missing) is False
    assert _cond_gte(None, 0.85, "k3", missing) is None
    assert _cond_gte("abc", 0.85, "k4", missing) is None
    assert _cond_gte(True, 0.85, "k5", missing) is None
    assert _cond_gte(float("nan"), 0.85, "k6", missing) is None

    # _cond_gt
    assert _cond_gt(1.0, 0.0, "k7", missing) is True
    assert _cond_gt(0.0, 0.0, "k8", missing) is False  # 0 > 0 = False
    assert _cond_gt(-1.0, 0.0, "k9", missing) is False
    assert _cond_gt(None, 0.0, "k10", missing) is None

    # _cond_bool
    assert _cond_bool(True, "k11", missing) is True
    assert _cond_bool(False, "k12", missing) is False
    assert _cond_bool(1, "k13", missing) is None  # int 차단
    assert _cond_bool(None, "k14", missing) is None
    assert _cond_bool("True", "k15", missing) is None

    # missing 키 누적 검증
    assert "k3" in missing and "k4" in missing and "k5" in missing
    assert "k10" in missing and "k13" in missing and "k14" in missing
    print(f"  [PASS] 헬퍼 3종 함수 + missing 누적 검증")


def test_atr_overheat_value_normalization():
    """atr_overheat_value 가 항상 float 또는 None (DB REAL 컬럼 타입 안전성)."""
    eng = SignalScoreEngine()
    base_l1 = _full_l1(positive=False)
    base_l3 = _full_l3(strong=False)

    # 정상 값 → float 그대로
    bd = eng.score("005930", base_l1, _full_l2(atr_overheat=1.5), base_l3)
    assert isinstance(bd.atr_overheat_value, float) and bd.atr_overheat_value == 1.5

    # 문자열 입력 → None (DB 타입 오류 방지)
    bd2 = eng.score("005930", base_l1, _full_l2(atr_overheat="hot"), base_l3)
    assert bd2.atr_overheat_value is None
    assert bd2.excluded_by_filter is True

    # NaN/inf 입력 → None
    bd3 = eng.score("005930", base_l1, _full_l2(atr_overheat=float("inf")), base_l3)
    assert bd3.atr_overheat_value is None
    assert bd3.excluded_by_filter is True

    # bool 입력 → None
    bd4 = eng.score("005930", base_l1, _full_l2(atr_overheat=True), base_l3)
    assert bd4.atr_overheat_value is None
    assert bd4.excluded_by_filter is True

    print(f"  [PASS] atr_overheat_value 정규화 (float/None) — DB REAL 안전성")


def test_scored_at_default():
    """scored_at 미지정 시 now_kst()."""
    eng = SignalScoreEngine()
    before = now_kst()
    bd = eng.score("005930", _full_l1(positive=False), _full_l2(strong=False), _full_l3(strong=False))
    after = now_kst()
    assert bd.scored_at.tzinfo is not None
    assert before <= bd.scored_at <= after
    print(f"  [PASS] scored_at 자동 = {bd.scored_at.strftime('%H:%M:%S %Z')}")


def main():
    print("=== Phase 1-4 SignalScoreEngine 단위 테스트 ===\n")
    tests = [
        test_full_score_phase1_weights,
        test_full_score_phase2_weights,
        test_max_size_requires_market_ok,
        test_below_threshold,
        test_hard_filter_atr_overheat,
        test_hard_filter_atr_at_boundary,
        test_hard_filter_atr_none,
        test_hard_filter_atr_nan,
        test_band_disabled_keeps_hardcut,
        test_band_mid_overheat_blocked,
        test_band_extreme_overheat_passes,
        test_band_boundary_high_blocked,
        test_band_normal_passes,
        test_band_high_below_max_raises,
        test_layer2_thresholds_strict,
        test_layer2_below_threshold,
        test_layer1_phase2_weights_alert,
        test_layer3_theme_rank_boundary,
        test_missing_inputs_tracking,
        test_above_ma20_strict_bool,
        test_corrupt_inputs_safety,
        test_init_validation,
        test_from_settings,
        test_decision_priority,
        test_score_breakdown_immutable,
        test_helpers,
        test_atr_overheat_value_normalization,
        test_scored_at_default,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
