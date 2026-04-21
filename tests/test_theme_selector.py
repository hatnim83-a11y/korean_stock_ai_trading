"""
test_theme_selector.py - 테마 선정 로직 검증 (2026-04-21 회전문 사건 대응)

Phase 1+2 변경사항 검증:
- Phase 2-2: 쿨다운 로직 (THEME_DROP_COOLDOWN_ENABLED)
- Phase 1-1/1-2: 모멘텀 보강 factor/clamp (설정 값 확인)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from modules.theme_analyzer.selector import select_themes_with_retention


def _make_theme(name: str, score: float, category: str = "기타") -> dict:
    """테스트용 테마 dict 팩토리"""
    return {
        "name": name,
        "theme": name,
        "total_score": score,
        "score": score,
        "momentum_score": score * 0.4,
        "category": category,
        "stock_count": 10,
        "grade": "A" if score >= 48 else ("B" if score >= 35 else "C"),
    }


def test_cooldown_enabled_blocks_reentry(monkeypatch):
    """쿨다운 ON: retention 탈락한 테마가 상위권에 있어도 신규 후보에서 제외됨"""
    monkeypatch.setattr(settings, "THEME_DROP_COOLDOWN_ENABLED", True)

    # 이번 점수에서 상위권 = 이전 탈락 테마 (회전문 재현 시나리오)
    scored = [
        _make_theme("금융", 45.9),        # 이전 유지, 이번 탈락(<48)
        _make_theme("아이폰", 38.4),      # 이전 유지, 이번 탈락
        _make_theme("조선", 37.6),        # 이전 유지, 이번 탈락
        _make_theme("CXL", 36.1),         # 이전 유지, 이번 탈락
        _make_theme("통신", 49.5),        # 이전 유지, 이번 유지(>=48)
        _make_theme("신규A", 35.0),
        _make_theme("신규B", 34.0),
        _make_theme("신규C", 33.0),
        _make_theme("신규D", 32.0),
    ]
    previous = [
        _make_theme("통신", 88.5),
        _make_theme("금융", 40.0),
        _make_theme("아이폰", 38.4),
        _make_theme("조선", 37.6),
        _make_theme("CXL", 36.2),
    ]

    result = select_themes_with_retention(scored, previous, count=5)
    result_names = {t["name"] for t in result}

    # 통신만 유지, 금융/아이폰/조선/CXL 재진입 불가
    assert "통신" in result_names, "유지 테마가 결과에 포함되어야 함"
    assert "금융" not in result_names, "쿨다운 드롭 테마가 재진입했음"
    assert "아이폰" not in result_names, "쿨다운 드롭 테마가 재진입했음"
    assert "조선" not in result_names, "쿨다운 드롭 테마가 재진입했음"
    assert "CXL" not in result_names, "쿨다운 드롭 테마가 재진입했음"

    # 신규 후보(신규A~D)로 빈 슬롯 채워짐
    assert result_names >= {"통신", "신규A", "신규B", "신규C", "신규D"}, (
        f"신규 테마가 빈 슬롯을 채워야 함: 실제={result_names}"
    )


def test_cooldown_disabled_reproduces_20260421(monkeypatch):
    """쿨다운 OFF: 기존 동작 — 드롭 테마가 즉시 재진입(2026-04-21 사건 재현)"""
    monkeypatch.setattr(settings, "THEME_DROP_COOLDOWN_ENABLED", False)

    scored = [
        _make_theme("금융", 45.9),
        _make_theme("아이폰", 38.4),
        _make_theme("조선", 37.6),
        _make_theme("CXL", 36.1),
        _make_theme("통신", 49.5),
    ]
    previous = [
        _make_theme("통신", 88.5),
        _make_theme("금융", 40.0),
        _make_theme("아이폰", 38.4),
        _make_theme("조선", 37.6),
        _make_theme("CXL", 36.2),
    ]

    result = select_themes_with_retention(scored, previous, count=5)
    result_names = {t["name"] for t in result}

    # 5개 전부 재진입 (사건 재현)
    assert result_names == {"통신", "금융", "아이폰", "조선", "CXL"}, (
        f"쿨다운 OFF일 때 기존 0교체 동작 재현 실패: {result_names}"
    )


def test_cooldown_with_insufficient_candidates(monkeypatch, caplog):
    """쿨다운으로 후보 풀이 부족해도 오류 없이 빈 슬롯 반환 + 경고 로그"""
    monkeypatch.setattr(settings, "THEME_DROP_COOLDOWN_ENABLED", True)

    # 이전 4개 드롭, 이번에 탈락한 4개 + 유지 1개 + 신규 2개만
    scored = [
        _make_theme("통신", 49.5),     # 유지
        _make_theme("금융", 45.9),     # 탈락 (쿨다운)
        _make_theme("아이폰", 38.4),   # 탈락 (쿨다운)
        _make_theme("조선", 37.6),     # 탈락 (쿨다운)
        _make_theme("CXL", 36.1),      # 탈락 (쿨다운)
        _make_theme("신규A", 35.0),
        _make_theme("신규B", 34.0),
    ]
    previous = [
        _make_theme("통신", 88.5),
        _make_theme("금융", 40.0),
        _make_theme("아이폰", 38.4),
        _make_theme("조선", 37.6),
        _make_theme("CXL", 36.2),
    ]

    result = select_themes_with_retention(scored, previous, count=5)
    result_names = {t["name"] for t in result}

    # 유지 1 + 신규 2 = 3개 (5 슬롯 중 2 빈자리)
    assert len(result) == 3, f"후보 고갈 시 3개 선정 기대, 실제 {len(result)}"
    assert result_names == {"통신", "신규A", "신규B"}, (
        f"후보 고갈 시 가용 테마만 선정: {result_names}"
    )


def test_boost_factor_and_clamp_configured():
    """Phase 1-1/1-2: 보강 계수/클램프 상수가 의도대로 설정됐는지"""
    assert hasattr(settings, "THEME_MOMENTUM_BOOST_FACTOR"), (
        "THEME_MOMENTUM_BOOST_FACTOR 상수 누락"
    )
    assert hasattr(settings, "THEME_MOMENTUM_BOOST_CLAMP"), (
        "THEME_MOMENTUM_BOOST_CLAMP 상수 누락"
    )
    assert hasattr(settings, "THEME_ENRICH_TOP_K"), "THEME_ENRICH_TOP_K 상수 누락"
    assert hasattr(settings, "THEME_DROP_COOLDOWN_ENABLED"), (
        "THEME_DROP_COOLDOWN_ENABLED 상수 누락"
    )

    # 기본값 (현재 플랜 기준)
    assert 0 < settings.THEME_MOMENTUM_BOOST_FACTOR <= 1.5
    assert settings.THEME_MOMENTUM_BOOST_CLAMP > 0
    assert settings.THEME_ENRICH_TOP_K >= 15

    # 이번 사건 시뮬레이션: delta -22.8 × factor 0.7 = -15.96 → clamp -8
    raw = -22.8 * settings.THEME_MOMENTUM_BOOST_FACTOR
    clamp = settings.THEME_MOMENTUM_BOOST_CLAMP
    applied = max(-clamp, min(clamp, raw))
    assert abs(applied) <= clamp, f"클램프 동작 실패: raw={raw}, applied={applied}"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
