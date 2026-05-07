"""
verify_theme_restore_keys.py - 변경 1(DB 복원 정규화) 효과 단발 검증

systemd 재시작 없이 즉시 검증:
1. 운영 DB에서 가장 최근 selected=1 5건 조회 (get_top_themes)
2. main.py:184~ normalized 변환 로직 실행 (헬퍼 _coalesce_zero 사용)
3. 결과 dict의 점수 4개 키(momentum/supply_ratio/news_count/ai_sentiment) 존재 + 값 출력
4. _pick_momentum 폴백 체인 정상 매치 검증

실행:
    python scripts/verify_theme_restore_keys.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import _coalesce_zero, _pick_momentum
from database import Database
from config import settings


def main():
    db = Database()
    db.connect()

    try:
        last_date = db.get_last_theme_analysis_date()
        if last_date is None:
            print("❌ themes 테이블에 selected=1 데이터 없음")
            sys.exit(1)

        print(f"📅 가장 최근 selected=1 날짜: {last_date}\n")

        themes_from_db = db.get_top_themes(last_date, count=settings.TOP_THEME_COUNT)
        if not themes_from_db:
            print("❌ get_top_themes 결과 비어있음")
            sys.exit(1)

        print(f"📋 DB row {len(themes_from_db)}건 조회:")
        print(f"{'테마명':<30} {'score':>7} {'momentum':>10} {'ai_sent':>9} {'news':>6} {'supply':>8}")
        print("-" * 80)
        for t in themes_from_db:
            mom = t.get("momentum")
            ai = t.get("ai_sentiment")
            nc = t.get("news_count")
            sp = t.get("supply_ratio")
            print(
                f"{t['theme_name']:<30} "
                f"{t['score']:>7.2f} "
                f"{('NULL' if mom is None else f'{mom:.2f}'):>10} "
                f"{('NULL' if ai is None else f'{ai:.2f}'):>9} "
                f"{('NULL' if nc is None else str(nc)):>6} "
                f"{('NULL' if sp is None else f'{sp:.2f}'):>8}"
            )

        # 박제 감지 + 폴백 시뮬레이션 (main.py:216~)
        print(f"\n🔄 박제 감지 + 폴백 처리 시뮬레이션:")
        print(f"{'테마명':<30} {'박제?':>6} {'폴백 momentum':>13} {'폴백 ai':>8} {'폴백 news':>10}")
        print("-" * 75)
        for t in themes_from_db:
            mom = t.get("momentum")
            ai = t.get("ai_sentiment")
            nc = t.get("news_count")
            is_zero_pinned = (
                (mom is None or mom == 0) and
                (ai is None or ai == 0) and
                (nc is None or nc == 0)
            )
            if is_zero_pinned:
                fb = db.get_score_fallback_for_theme(t["theme_name"], last_date)
                if fb:
                    new_mom = fb.get("momentum") if fb.get("momentum") is not None else mom
                    new_ai = fb.get("ai_sentiment") if fb.get("ai_sentiment") is not None else ai
                    new_nc = fb.get("news_count") if fb.get("news_count") is not None else nc
                    t["momentum"] = new_mom
                    t["supply_ratio"] = fb.get("supply_ratio") if fb.get("supply_ratio") is not None else t.get("supply_ratio")
                    t["news_count"] = new_nc
                    t["ai_sentiment"] = new_ai
                    print(
                        f"{t['theme_name']:<30} "
                        f"{'YES':>6} "
                        f"{new_mom if new_mom is not None else 'N/A':>13} "
                        f"{new_ai if new_ai is not None else 'N/A':>8} "
                        f"{new_nc if new_nc is not None else 'N/A':>10}"
                    )
                else:
                    print(f"{t['theme_name']:<30} {'YES':>6} {'폴백 없음 (NULL 유지)':>34}")
            else:
                print(f"{t['theme_name']:<30} {'NO':>6}")

        # main.py:216~ normalized 변환 시뮬레이션 (폴백 적용된 row 기반)
        normalized = [
            {
                "name": t["theme_name"],
                "theme": t["theme_name"],
                "score": t["score"],
                "total_score": t["score"],
                "url": t.get("url", ""),
                "category": t.get("category", "기타"),
                "momentum": _coalesce_zero(t.get("momentum")),
                "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                "news_count": _coalesce_zero(t.get("news_count")),
                "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
            }
            for t in themes_from_db
        ]

        print(f"\n✅ normalized 변환 결과 {len(normalized)}건:")
        print(f"{'테마명':<30} {'momentum':>10} {'_pick':>8} {'has_keys':>10}")
        print("-" * 70)
        score_keys = {"momentum", "supply_ratio", "news_count", "ai_sentiment"}
        all_have_keys = True
        for t in normalized:
            picked = _pick_momentum(t)
            has_all = score_keys.issubset(t.keys())
            if not has_all:
                all_have_keys = False
            print(
                f"{t['theme']:<30} "
                f"{t['momentum']:>10.2f} "
                f"{picked:>8.2f} "
                f"{'YES' if has_all else 'NO':>10}"
            )

        print()
        if all_have_keys:
            print("✅ 변경 1 효과 검증 PASS — 모든 normalized dict가 점수 4개 키 보유")
        else:
            print("❌ 변경 1 효과 검증 FAIL — 일부 dict에 점수 키 누락")
            sys.exit(2)

        # _pick_momentum 폴백 체인 추가 검증 (가짜 dict들)
        print("\n📋 _pick_momentum 폴백 체인 검증:")
        cases = [
            ({"momentum_score": 24.5, "momentum": 24.5}, 24.5, "정규 재선정 직후 (양쪽 키)"),
            ({"momentum": 22.08}, 22.08, "DB 복원본 (momentum만)"),
            ({"score": 34.4}, 0, "회귀 박제 (둘 다 없음)"),
            ({"momentum_score": 0.0, "momentum": 0.0}, 0.0, "정상 0 케이스 (보존)"),
        ]
        for input_dict, expected, label in cases:
            actual = _pick_momentum(input_dict)
            status = "PASS" if actual == expected else "FAIL"
            print(f"  [{status}] {label}: input={input_dict} → {actual} (예상 {expected})")

    finally:
        db.close()


if __name__ == "__main__":
    main()
