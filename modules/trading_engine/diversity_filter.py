"""
diversity_filter.py - 매수 후보의 테마/섹터 분산 필터 (Phase 5.5)

순수 함수로 분리하여 단위 테스트 가능.

알고리즘 (1차 + 조건부 2차 패스):
1차 패스: max_per_theme(=2) 적용. 한도 미도달 후보 우선 매수.
2차 패스 (relax_max != None AND 빈 슬롯 + 1차에서 테마 한도로 탈락한 후보 존재 시):
  - 1차 탈락 풀에서 final_score 순으로 추가 선정
  - 테마 한도 relax_max(=3) 까지만 허용
  - 섹터 한도 max_per_sector(=3)는 그대로 유지

시나리오 D (반도체×5, 빈슬롯 5): 한도 3에서 멈춤 — 분산 우선 (사용자 확정).
"""

from __future__ import annotations

from typing import Optional


def apply_diversity_filter(
    candidates: list[dict],
    theme_to_category: dict[str, str],
    initial_theme_counts: dict[str, int],
    initial_sector_counts: dict[str, int],
    available_slots: int,
    max_per_theme: int,
    max_per_sector: int,
    relax_max: Optional[int] = None,
) -> tuple[list[dict], list[dict], bool]:
    """
    매수 후보를 테마/섹터 분산 한도에 따라 필터링.

    Args:
        candidates: AI 통과 후보 (final_score 내림차순 정렬 가정)
        theme_to_category: 테마명 → 섹터(카테고리) 매핑
        initial_theme_counts: 보유 종목 기준 테마별 시작 카운트 (불변, 내부 복사)
        initial_sector_counts: 보유 종목 기준 섹터별 시작 카운트 (불변, 내부 복사)
        available_slots: 빈 슬롯 수 (MAX_POSITIONS - held_count)
        max_per_theme: 1차 패스 테마 한도 (settings.MAX_STOCKS_PER_THEME)
        max_per_sector: 섹터 한도 (settings.MAX_STOCKS_PER_SECTOR)
        relax_max: 2차 패스 테마 한도 (None=2차 비활성, 1-pass만 동작)

    Returns:
        (selected, excluded, relaxation_applied)
        selected: 매수할 종목 (입력 순서 보존)
        excluded: 탈락 종목 (각 항목에 '_reason' 키 추가)
        relaxation_applied: 2차 패스로 추가된 종목이 1개 이상이면 True
    """
    # 카운터 복사 (in-place 부작용 회피)
    theme_counts = dict(initial_theme_counts)
    sector_counts = dict(initial_sector_counts)

    selected_pass1: list[dict] = []
    theme_overflow_pool: list[dict] = []  # 테마 한도로만 탈락한 후보
    excluded: list[dict] = []  # 섹터 한도 등 영구 탈락

    if available_slots <= 0:
        return [], [], False

    # ===== 1차 패스 =====
    for cand in candidates:
        if len(selected_pass1) >= available_slots:
            # 슬롯이 다 찼으면 종료. 남은 후보는 _slot_excluded로 호출자에서 처리.
            break

        s_theme = cand.get("theme", "")
        s_sector = theme_to_category.get(s_theme, "기타")

        # 테마 분산 제한 (빈 테마는 제한 미적용)
        if s_theme:
            t_count = theme_counts.get(s_theme, 0)
            if t_count >= max_per_theme:
                # 1차 탈락 — 2차 패스에서 재검토 가능
                theme_overflow_pool.append(cand)
                continue

        # 섹터 분산 제한
        s_count = sector_counts.get(s_sector, 0)
        if s_count >= max_per_sector:
            excluded.append({
                **cand,
                "_reason": f"섹터 분산 ({s_sector} {s_count}/{max_per_sector})",
            })
            continue

        selected_pass1.append(cand)
        if s_theme:
            theme_counts[s_theme] = theme_counts.get(s_theme, 0) + 1
        sector_counts[s_sector] = s_count + 1

    # ===== 2차 패스 (조건부) =====
    relaxation_applied = False
    relaxed: list[dict] = []
    empty_after_pass1 = available_slots - len(selected_pass1)

    if relax_max is not None and empty_after_pass1 > 0 and theme_overflow_pool:
        for cand in theme_overflow_pool:
            if len(selected_pass1) + len(relaxed) >= available_slots:
                break

            s_theme = cand.get("theme", "")
            s_sector = theme_to_category.get(s_theme, "기타")

            # 2차 한도 체크
            t_count = theme_counts.get(s_theme, 0)
            if t_count >= relax_max:
                excluded.append({
                    **cand,
                    "_reason": f"테마 분산 ({s_theme} {t_count}/{relax_max} 완화)",
                })
                continue

            # 섹터 한도는 유지
            s_count = sector_counts.get(s_sector, 0)
            if s_count >= max_per_sector:
                excluded.append({
                    **cand,
                    "_reason": f"섹터 분산 ({s_sector} {s_count}/{max_per_sector} 완화 후)",
                })
                continue

            relaxed.append({**cand, "_relaxed": True})
            theme_counts[s_theme] = t_count + 1
            sector_counts[s_sector] = s_count + 1

        relaxation_applied = len(relaxed) > 0

        # 2차 패스에서 못 들어간 overflow는 영구 탈락 처리
        consumed_codes = {c.get("code", c.get("stock_code", "")) for c in relaxed}
        excluded_codes = {c.get("code", c.get("stock_code", "")) for c in excluded}
        for cand in theme_overflow_pool:
            code = cand.get("code", cand.get("stock_code", ""))
            if code in consumed_codes or code in excluded_codes:
                continue
            # 슬롯이 다 차서 시도 못한 케이스 — 2차 한도(relax_max) 기준으로 사유 기록
            theme_name = cand.get("theme", "")
            t_now = (
                initial_theme_counts.get(theme_name, 0)
                + sum(1 for c in selected_pass1 if c.get("theme") == theme_name)
                + sum(1 for c in relaxed if c.get("theme") == theme_name)
            )
            excluded.append({
                **cand,
                "_reason": f"테마 분산 ({theme_name} {t_now}/{relax_max} 슬롯 만석)",
            })
    else:
        # 1-pass 모드: theme_overflow_pool 모두 1차 사유로 영구 탈락
        for cand in theme_overflow_pool:
            t_now = theme_counts.get(cand.get("theme", ""), 0)
            excluded.append({
                **cand,
                "_reason": f"테마 분산 ({cand.get('theme', '')} {t_now}/{max_per_theme})",
            })

    selected = selected_pass1 + relaxed
    return selected, excluded, relaxation_applied
