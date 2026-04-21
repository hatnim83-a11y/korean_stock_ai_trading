"""
replay_20260421.py - 2026-04-21 화요일 재선정 사건 재현 스크립트

목적: Phase 1+2 적용 후 2026-04-21 DB 스냅샷으로 재선정 재실행하여
동일 사건(0교체 회전문)이 재발하지 않음을 숫자로 증명.

실행: python scripts/replay_20260421.py

주의:
  - _enrich_tuesday_themes는 live KIS/Claude API를 타므로 본 스크립트에서는
    실행하지 않음 (DB 원본 가중평균 점수 기준으로만 selector 호출)
  - 실제 사건 재현은 "원본 DB 가중평균 + 당시 실시간 보강 후 점수"로 이뤄져야
    완전히 재현되지만, 여기서는 selector 쿨다운 효과만 확인
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from database import get_database
from modules.theme_analyzer.selector import select_themes_with_retention
from modules.theme_analyzer.weekly_aggregator import aggregate_weekly_scores


# 2026-04-21 사건 당시 previous_themes (이전 주 활성 테마)
PREVIOUS_THEMES_20260421 = [
    {"theme": "통신", "total_score": 88.5},
    {"theme": "금융", "total_score": 40.0},
    {"theme": "아이폰", "total_score": 38.4},
    {"theme": "조선", "total_score": 37.6},
    {"theme": "CXL(컴퓨트익스프레스링크)", "total_score": 36.2},
]

# 당시 화요일 08:30 실시간 보강 후 점수 (로그에서 추출)
ENRICHED_SCORES_20260421 = {
    "통신": 49.5,
    "금융": 45.9,
    "아이폰": 38.4,
    "조선": 37.6,
    "CXL(컴퓨트익스프레스링크)": 36.1,
}


def build_scored_themes_from_db() -> list[dict]:
    """2026-04-20 기준 6영업일 가중평균 집계 (weekly_aggregator 재사용)"""
    db = get_database()
    try:
        scored = aggregate_weekly_scores(db, base_date=date(2026, 4, 20))
    finally:
        db.close()

    # 당시 실시간 보강 결과를 수동으로 덮어씌워 "보강 후 상태" 시뮬레이션
    for t in scored:
        name = t.get("theme", t.get("name", ""))
        if name in ENRICHED_SCORES_20260421:
            t["total_score"] = ENRICHED_SCORES_20260421[name]
            t["score"] = ENRICHED_SCORES_20260421[name]
    return scored


def run_scenario(label: str, cooldown_enabled: bool, scored: list[dict]) -> list[str]:
    """쿨다운 ON/OFF 한 번씩 selector 실행 후 결과 이름 반환"""
    # 주의: 테스트/재현 스크립트 전용. 서비스 코드에서 settings 직접 수정 금지.
    # pydantic settings는 실행 중 변경이 제한적이라 object.__setattr__로 우회.
    original = getattr(settings, "THEME_DROP_COOLDOWN_ENABLED", True)
    object.__setattr__(settings, "THEME_DROP_COOLDOWN_ENABLED", cooldown_enabled)

    print()
    print("=" * 70)
    print(f"시나리오: {label}")
    print(f"THEME_DROP_COOLDOWN_ENABLED = {cooldown_enabled}")
    print("=" * 70)

    try:
        result = select_themes_with_retention(
            scored_themes=scored,
            previous_themes=PREVIOUS_THEMES_20260421,
            count=5,
        )
    finally:
        object.__setattr__(settings, "THEME_DROP_COOLDOWN_ENABLED", original)

    names = [t.get("theme", t.get("name", "")) for t in result]
    print()
    print(f"  최종 선정 테마 ({len(names)}개):")
    for i, t in enumerate(result, 1):
        tag = "📌 유지" if t.get("retained") else "🆕 신규"
        score = t.get("total_score", 0)
        print(f"    {i}. {tag} {t.get('theme')} ({score:.1f}점)")
    return names


def main():
    print("=" * 70)
    print("2026-04-21 화요일 재선정 회전문 사건 재현")
    print("=" * 70)

    scored = build_scored_themes_from_db()
    if not scored:
        print("❌ DB에서 2026-04-20 기준 가중평균 데이터를 가져올 수 없습니다.")
        print("   (DB 경로나 날짜 범위를 확인하세요)")
        sys.exit(1)

    print(f"\n가중평균 + 실시간 보강 시뮬레이션: {len(scored)}개 테마")
    top5_preview = [
        f"{t.get('theme')}({t.get('total_score', 0):.1f})" for t in scored[:5]
    ]
    print(f"  상위 5: {', '.join(top5_preview)}")

    # 쿨다운 OFF: 사건 당시 동작 재현
    names_off = run_scenario("쿨다운 OFF (사건 재현)", False, list(scored))

    # DB에서 다시 fresh copy (run_scenario가 dict 변경 가능성)
    scored2 = build_scored_themes_from_db()
    names_on = run_scenario("쿨다운 ON (Phase 2 적용)", True, list(scored2))

    # 비교 리포트
    print()
    print("=" * 70)
    print("비교 결과")
    print("=" * 70)

    dropped_in_original = {"금융", "아이폰", "조선", "CXL(컴퓨트익스프레스링크)"}
    reentered_off = [n for n in names_off if n in dropped_in_original]
    reentered_on = [n for n in names_on if n in dropped_in_original]

    print(f"\n이전 drop 테마 재진입:")
    print(f"  쿨다운 OFF: {len(reentered_off)}개 재진입 → {reentered_off}")
    print(f"  쿨다운 ON : {len(reentered_on)}개 재진입 → {reentered_on}")

    print()
    if len(reentered_on) == 0 and len(reentered_off) > 0:
        print("✅ 검증 성공: 쿨다운 ON일 때 drop 테마 전원 재진입 차단")
        sys.exit(0)
    else:
        print("❌ 검증 실패: 예상된 쿨다운 효과가 관찰되지 않음")
        print(f"   (쿨다운 OFF 재진입 {len(reentered_off)}개, ON {len(reentered_on)}개)")
        sys.exit(1)


if __name__ == "__main__":
    main()
