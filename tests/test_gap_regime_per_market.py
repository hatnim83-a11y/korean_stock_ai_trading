"""
test_gap_regime_per_market.py - 종목 소속시장별 갭 regime 회귀 방지 테스트

2026-06-02 진입 깔때기 완화 (GAP_REGIME_PER_MARKET) + 2026-06-04 KSQ150 핫픽스 대응:
- classify_market: KIS rprs_mrkt_kor_name → kospi/kosdaq 정확 분류
  (코스닥150은 "KSQ150"으로 반환되어 "KOSDAQ" 문자열이 없음 → KSQ 처리 필수)
- get_dynamic_gap_for_market: 종목 소속시장 개별 지수 등락률로 밴드 산출
- 분열장(코스피↔코스닥 반대 방향)에서 시장별 밴드가 독립적으로 결정되는지
- 6/2·6/4 라이브 시나리오 재현

실행:
    pytest tests/test_gap_regime_per_market.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.morning_filter.dynamic_gap import (
    DynamicGapCalculator,
    MarketCondition,
    classify_market,
)
from modules.morning_filter.gap_filter import GapFilter


# 테스트는 config 변경과 무관하게 결정적이도록 base 값을 명시 주입
BASE_UP = 3.5
BASE_DOWN = 2.0


def _calc():
    return DynamicGapCalculator(
        base_gap_up=BASE_UP, base_gap_down=BASE_DOWN, enable_dynamic=True
    )


# ===== classify_market =====

@pytest.mark.parametrize("market_name,expected", [
    ("KSQ150", "kosdaq"),          # 코스닥150 (핵심 핫픽스 케이스)
    ("KSQ", "kosdaq"),             # 코스닥 약어
    ("KOSDAQ", "kosdaq"),          # 일반 코스닥
    ("코스닥", "kosdaq"),           # 한글
    ("KOSDAQ GLOBAL", "kosdaq"),   # 코스닥 글로벌
    ("ksq150", "kosdaq"),          # 소문자 (대소문자 무시)
    ("KOSPI", "kospi"),
    ("KOSPI200", "kospi"),
    ("KONEX", "kospi"),            # 코넥스 → 기본(kospi)
    ("ETF", "kospi"),             # ETF → 기본(kospi)
    ("", "kospi"),                # 빈 문자열 폴백
    (None, "kospi"),              # None 폴백
])
def test_classify_market(market_name, expected):
    assert classify_market(market_name) == expected


# ===== get_dynamic_gap_for_market 밴드 산출 =====

def test_band_kospi_bullish_normal_vol():
    # KOSPI +1.44% → bullish, |1.44|<2.0 normal → up=3.5+1.0=4.5, down=2.0+0.5=2.5
    cond = MarketCondition(kospi_change=1.44, kosdaq_change=-1.64)
    cfg = _calc().get_dynamic_gap_for_market("kospi", cond)
    assert cfg.max_gap_up == 4.5
    assert cfg.max_gap_down == 2.5


def test_band_kosdaq_bearish_normal_vol():
    # KOSDAQ -1.64% → bearish, normal → up=3.5-1.0=2.5, down=2.0-0.5=1.5
    cond = MarketCondition(kospi_change=1.44, kosdaq_change=-1.64)
    cfg = _calc().get_dynamic_gap_for_market("kosdaq", cond)
    assert cfg.max_gap_up == 2.5
    assert cfg.max_gap_down == 1.5


def test_band_kosdaq_bullish_high_vol():
    # KOSDAQ +2.28% → bullish, |2.28|>=2.0 high → up=3.5+1.0+0.5=5.0
    cond = MarketCondition(kospi_change=-1.83, kosdaq_change=2.28)
    cfg = _calc().get_dynamic_gap_for_market("kosdaq", cond)
    assert cfg.max_gap_up == 5.0


def test_band_independence_in_split_market():
    """분열장: 같은 condition에서 코스피/코스닥 밴드가 독립적으로 갈려야 함(핵심 가치)."""
    cond = MarketCondition(kospi_change=-1.83, kosdaq_change=2.28)
    calc = _calc()
    kospi = calc.get_dynamic_gap_for_market("kospi", cond)
    kosdaq = calc.get_dynamic_gap_for_market("kosdaq", cond)
    # 코스피 약세(좁은 밴드) vs 코스닥 강세(넓은 밴드)
    assert kospi.max_gap_up == 2.5
    assert kosdaq.max_gap_up == 5.0
    assert kosdaq.max_gap_up > kospi.max_gap_up


def test_unknown_market_falls_back_to_kospi_band():
    # classify_market("") → kospi → 코스피 밴드 적용
    cond = MarketCondition(kospi_change=-1.83, kosdaq_change=2.28)
    calc = _calc()
    mkt = classify_market("")
    cfg = calc.get_dynamic_gap_for_market(mkt, cond)
    assert mkt == "kospi"
    assert cfg.max_gap_up == 2.5


def test_enable_dynamic_false_returns_base():
    calc = DynamicGapCalculator(
        base_gap_up=BASE_UP, base_gap_down=BASE_DOWN, enable_dynamic=False
    )
    cond = MarketCondition(kospi_change=2.0, kosdaq_change=-2.0)
    cfg = calc.get_dynamic_gap_for_market("kosdaq", cond)
    assert cfg.max_gap_up == BASE_UP
    assert cfg.max_gap_down == BASE_DOWN


# ===== 라이브 시나리오 재현 (classify_market + 밴드 + GapFilter 통합) =====

def _gap_pass(market_name, gap_pct, cond):
    """후보 1종목이 per-market 갭 필터를 통과하는지."""
    calc = _calc()
    mkt = classify_market(market_name)
    cfg = calc.get_dynamic_gap_for_market(mkt, cond)
    gf = GapFilter(max_gap_up=cfg.max_gap_up, max_gap_down=cfg.max_gap_down)
    prev = 10000.0
    openp = prev * (1 + gap_pct / 100)
    return gf.check("000000", prev, openp, market_name).passed


def test_scenario_0602():
    """6/2: KOSPI +1.44 / KOSDAQ -1.64. 코스피 종목 회수, 코스닥 갭상승 차단."""
    cond = MarketCondition(kospi_change=1.44, kosdaq_change=-1.64)
    assert _gap_pass("KOSPI200", 3.30, cond) is True    # 삼성전자 +3.30 < 4.5
    assert _gap_pass("KOSPI200", -2.16, cond) is True   # 현대건설 -2.16 > -2.5
    assert _gap_pass("KSQ150", 4.63, cond) is False     # 올릭스 +4.63 > 2.5(코스닥 약세)
    assert _gap_pass("KOSDAQ", 4.05, cond) is False     # 코스텍시스 +4.05 > 2.5


def test_scenario_0604_ksq150_hotfix():
    """6/4: KOSPI -1.83 / KOSDAQ +2.28. KSQ150 올릭스가 코스닥 5.0 밴드로 통과해야 함(핫픽스 핵심)."""
    cond = MarketCondition(kospi_change=-1.83, kosdaq_change=2.28)
    # 올릭스 KSQ150 +4.17 < 5.0(코스닥 강세) → 통과 (버그였다면 코스피 2.5로 탈락)
    assert _gap_pass("KSQ150", 4.17, cond) is True
    # 코스텍시스 KOSDAQ +1.65 < 5.0 → 통과
    assert _gap_pass("KOSDAQ", 1.65, cond) is True
    # 코스피 종목은 좁은 밴드로 차단
    assert _gap_pass("KOSPI200", 3.40, cond) is False   # 삼성물산 +3.40 > 2.5
    assert _gap_pass("KOSPI200", -1.64, cond) is False  # 현대건설 -1.64 < -1.5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
