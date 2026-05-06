"""
test_diversity_filter.py - 매수 후보 테마/섹터 분산 필터 단위 테스트

파일 직접 실행: python tests/test_diversity_filter.py

시나리오 A: 5슬롯, 반도체×3 + 2차전지 + 원전 → 5종목 (반도체 한도 3 적용)
시나리오 B: 3슬롯, 반도체×4 → 3종목 (반도체 3에서 멈춤)
시나리오 C: 5슬롯, 반도체×2 + 원전 → 3종목 (relaxation_applied=False, 후보 부족)
시나리오 D: 5슬롯, 반도체×5 → 3종목 (한도 3에서 멈춤, 잔여 2슬롯 의도적 비움)
시나리오 E: 5슬롯, 반도체×2 + 섹터X×3 → 5종목 (1차 통과, relaxation_applied=False)
시나리오 F: 5슬롯, 반도체×3 + 같은 섹터 다른 테마×3 (섹터 충돌) → 일부만
시나리오 G: 0슬롯 → 빈 리스트 안전 종료
시나리오 H: 동일 final_score 동률 → 입력 순서 보존
시나리오 I: relax_max=None (1-pass 모드) → 1차 결과만
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.diversity_filter import apply_diversity_filter


def _theme_to_cat() -> dict[str, str]:
    return {
        "반도체": "반도체",
        "2차전지": "2차전지/에너지",
        "원전": "산업재",
        "바이오": "바이오",
        "테마X": "섹터X",
        "테마Y": "섹터Y",
        "테마Z": "섹터X",  # 다른 테마, 같은 섹터
    }


def test_a_5slots_three_semi_plus_two():
    """시나리오 A: 빈슬롯 5, 반도체×3 + 2차전지 + 원전 → 5종목 (반도체 한도 3 적용)"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "S3", "name": "반도체C", "theme": "반도체"},
        {"code": "B1", "name": "2차전지A", "theme": "2차전지"},
        {"code": "N1", "name": "원전A", "theme": "원전"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["S1", "S2", "B1", "N1", "S3"], f"기대 [S1,S2,B1,N1,S3], 실제 {codes}"
    assert relax is True
    assert any(c.get("_relaxed") for c in selected if c["code"] == "S3")
    print("  [PASS] A: 반도체 3 + 2차전지 + 원전 (1차 통과 후 2차 추가)")


def test_b_3slots_semi_only():
    """시나리오 B: 빈슬롯 3, 반도체×4 → 3종목 (반도체 한도 3 도달)"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "S3", "name": "반도체C", "theme": "반도체"},
        {"code": "S4", "name": "반도체D", "theme": "반도체"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=3, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["S1", "S2", "S3"], f"기대 [S1,S2,S3], 실제 {codes}"
    assert relax is True
    print("  [PASS] B: 반도체 3개 (한도 3에서 멈춤)")


def test_c_3candidates_5slots_no_relax():
    """시나리오 C: 빈슬롯 5, 반도체×2 + 원전 = 3 → 3종목 (후보 부족, relax 비적용)"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "N1", "name": "원전A", "theme": "원전"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["S1", "S2", "N1"], f"기대 [S1,S2,N1], 실제 {codes}"
    assert relax is False, "후보가 부족해서 2차 패스 비활성"
    print("  [PASS] C: 후보 부족 시 1차 결과만, relax_applied=False")


def test_d_only_semi_5_caps_at_3():
    """시나리오 D: 빈슬롯 5, 반도체×5 → 3종목 (한도 3에서 멈춤, 잔여 2슬롯 비움)"""
    candidates = [
        {"code": f"S{i}", "name": f"반도체{i}", "theme": "반도체"}
        for i in range(1, 6)
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["S1", "S2", "S3"], f"기대 [S1,S2,S3], 실제 {codes}"
    assert relax is True
    # 시나리오 D 사용자 의도: 분산 우선 — S4/S5는 영구 탈락
    excluded_codes = {c["code"] for c in excluded}
    assert "S4" in excluded_codes and "S5" in excluded_codes
    print("  [PASS] D: 반도체 한도 3에서 멈춤, S4/S5 영구 탈락")


def test_e_sector_collision_no_relax():
    """시나리오 E: 빈슬롯 5, 반도체×2 + 섹터X×3 (섹터 한도 충돌) → 5종목, relax 비적용"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "X1", "name": "X1", "theme": "테마X"},
        {"code": "X2", "name": "X2", "theme": "테마X"},
        {"code": "Z1", "name": "Z1", "theme": "테마Z"},  # 같은 섹터X
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    # 1차에서 5개 모두 통과 (테마X 2 + 테마Z 1 = 섹터X 3, 섹터 한도 미초과)
    assert len(codes) == 5
    assert codes == ["S1", "S2", "X1", "X2", "Z1"], f"실제 {codes}"
    assert relax is False
    print("  [PASS] E: 1차로 5종목 채워짐, relax_applied=False")


def test_f_relax_blocked_by_sector():
    """시나리오 F: 빈슬롯 5, 반도체×3 (relax 시도) + 섹터X×3 (이미 1차에서 한도) → 부분만"""
    candidates = [
        {"code": "X1", "name": "X1", "theme": "테마X"},
        {"code": "X2", "name": "X2", "theme": "테마X"},
        {"code": "Z1", "name": "Z1", "theme": "테마Z"},  # 섹터X 3번째
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "S3", "name": "반도체C", "theme": "반도체"},  # relax 후보
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    # 1차: X1 X2 Z1 S1 S2 = 5 → 슬롯 다 참 → S3 1차에서 시도 못함 (break)
    # 따라서 selected는 [X1,X2,Z1,S1,S2] 5종목, relax_applied=False
    assert codes == ["X1", "X2", "Z1", "S1", "S2"], f"실제 {codes}"
    assert relax is False, "1차로 빈 슬롯 다 채워짐"
    print("  [PASS] F: 1차로 슬롯 다 채워지면 2차 미동작")


def test_g_zero_slots():
    """시나리오 G: 빈슬롯 0 → 빈 리스트, 안전 종료"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=0, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    assert selected == []
    assert excluded == []
    assert relax is False
    print("  [PASS] G: 빈슬롯 0이면 빈 리스트 반환")


def test_h_input_order_preserved():
    """시나리오 H: 동일 final_score 동률 (호출자 입력 순서 보존)"""
    candidates = [
        {"code": "A", "name": "A", "theme": "반도체"},
        {"code": "B", "name": "B", "theme": "2차전지"},
        {"code": "C", "name": "C", "theme": "원전"},
        {"code": "D", "name": "D", "theme": "바이오"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=4, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["A", "B", "C", "D"], f"입력 순서 보존 실패: {codes}"
    print("  [PASS] H: 입력 순서 보존")


def test_i_relax_max_none_equivalent_to_1pass():
    """시나리오 I: relax_max=None이면 기존 1-pass 동작과 동일"""
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
        {"code": "S2", "name": "반도체B", "theme": "반도체"},
        {"code": "S3", "name": "반도체C", "theme": "반도체"},  # 1차 탈락
        {"code": "B1", "name": "2차전지", "theme": "2차전지"},
    ]
    selected, excluded, relax = apply_diversity_filter(
        candidates, _theme_to_cat(), {}, {},
        available_slots=5, max_per_theme=2, max_per_sector=3, relax_max=None,
    )
    codes = [c["code"] for c in selected]
    assert codes == ["S1", "S2", "B1"], f"실제 {codes}"
    assert relax is False
    excluded_codes = {c["code"] for c in excluded}
    assert "S3" in excluded_codes
    print("  [PASS] I: relax_max=None → 1-pass 동작")


def test_initial_counts_not_mutated():
    """초기 카운터가 in-place 수정되지 않음 (불변)"""
    initial = {"반도체": 1}
    candidates = [
        {"code": "S1", "name": "반도체A", "theme": "반도체"},
    ]
    apply_diversity_filter(
        candidates, _theme_to_cat(), initial, {},
        available_slots=3, max_per_theme=2, max_per_sector=3, relax_max=3,
    )
    assert initial == {"반도체": 1}, f"initial 변경됨: {initial}"
    print("  [PASS] initial_counts 불변 검증")


def run_all_tests() -> bool:
    print("=" * 60)
    print("apply_diversity_filter 단위 테스트")
    print("=" * 60)

    tests = [
        test_a_5slots_three_semi_plus_two,
        test_b_3slots_semi_only,
        test_c_3candidates_5slots_no_relax,
        test_d_only_semi_5_caps_at_3,
        test_e_sector_collision_no_relax,
        test_f_relax_blocked_by_sector,
        test_g_zero_slots,
        test_h_input_order_preserved,
        test_i_relax_max_none_equivalent_to_1pass,
        test_initial_counts_not_mutated,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test.__name__}: {e}")
            failed += 1

    print("=" * 60)
    print(f"  결과: {passed} passed, {failed} failed / {len(tests)} total")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
