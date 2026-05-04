"""단위 B 검증: universe_provider.

시나리오:
    UV-1: 정상 — 5 테마 × 4 종목 → universe 20건 (중복 없음)
    UV-2: 캐시 히트 — 같은 거래일 두 번째 호출 시 크롤링 호출 0회
    UV-3: 빈 테마 (DB 조회 실패) → 빈 리스트
    UV-4: url 없는 테마 스킵 — 1개 테마만 url 있어도 다른 4개는 스킵
    UV-5: 크롤링 예외 → 해당 테마는 빈 리스트, 다른 테마는 정상 진행
    UV-6: 중복 종목 (테마 간) → set 으로 중복 제거
    UV-7: 무효 종목코드 (5자리, 0015G0) → 정규식 필터로 제외
    UV-8: hard_cap 도달 시 잘라냄
    UV-9: 스윙 보유 종목 제외
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import universe_provider as up


def _make_themes(n: int) -> list[dict]:
    return [
        {"name": f"테마{i}", "url": f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={100+i}"}
        for i in range(1, n + 1)
    ]


def _make_stocks(prefix: str, count: int) -> list[dict]:
    """6자리 코드 mock 종목 리스트."""
    return [{"code": f"{prefix}{i:04d}", "name": f"종목{i}", "price": 1000 + i, "change_rate": 1.0}
            for i in range(count)]


def test_UV_1_정상_5테마_4종목():
    up.reset_cache()
    themes = _make_themes(5)
    # 테마별 4종목씩 (중복 없도록 prefix 다르게)
    stocks_by_url = {
        themes[i]["url"]: _make_stocks(f"{i:02d}", 5) for i in range(5)
    }
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               side_effect=lambda url: stocks_by_url[url]):
        universe = up.get_universe()
    assert len(universe) == 20, f"UV-1 FAIL: {len(universe)}건 (20 기대)"
    assert len(set(universe)) == 20, "UV-1 FAIL: 중복 발생"
    assert all(c.isdigit() and len(c) == 6 for c in universe), "UV-1 FAIL: 6자리 위반"
    print(f"[PASS] UV-1: 정상 5테마×4종목 → {len(universe)}건")


def test_UV_2_캐시_히트():
    up.reset_cache()
    themes = _make_themes(2)
    stocks_by_url = {themes[i]["url"]: _make_stocks(f"{i:02d}", 4) for i in range(2)}
    crawl_mock = MagicMock(side_effect=lambda url: stocks_by_url[url])
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks", crawl_mock):
        u1 = up.get_universe()
        u2 = up.get_universe()
    assert u1 == u2
    # 크롤링은 첫 호출에서만 (2 테마 × 1번 = 2회), 두 번째는 캐시
    assert crawl_mock.call_count == 2, (
        f"UV-2 FAIL: 크롤링 {crawl_mock.call_count}회 (2회 기대)"
    )
    print("[PASS] UV-2: 캐시 히트 — 두 번째 호출 시 크롤링 0회")


def test_UV_3_빈_DB():
    up.reset_cache()
    with patch.object(up, "_fetch_top_themes", return_value=[]), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()):
        universe = up.get_universe()
    assert universe == []
    print("[PASS] UV-3: DB 빈 결과 → 빈 리스트")


def test_UV_4_url_없는_테마_스킵():
    up.reset_cache()
    themes = [
        {"name": "테마A", "url": ""},  # 스킵
        {"name": "테마B"},               # 스킵 (url 키 없음)
        {"name": "테마C", "url": "https://example.com/theme/1"},
    ]
    stocks_by_url = {themes[2]["url"]: _make_stocks("99", 3)}
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               side_effect=lambda url: stocks_by_url.get(url, [])):
        universe = up.get_universe(stocks_per_theme=3)
    assert len(universe) == 3, f"UV-4 FAIL: {universe}"
    print("[PASS] UV-4: url 없는 테마 스킵 — 1테마 3종목만")


def test_UV_5_크롤링_예외_격리():
    up.reset_cache()
    themes = _make_themes(3)
    def _flaky(url):
        if "101" in url:
            return _make_stocks("11", 2)
        if "102" in url:
            raise RuntimeError("네이버 차단")
        return _make_stocks("33", 2)
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               side_effect=_flaky):
        universe = up.get_universe(stocks_per_theme=2)
    assert len(universe) == 4, f"UV-5 FAIL: {universe}"
    print("[PASS] UV-5: 크롤링 예외 격리 — 다른 테마 정상 진행")


def test_UV_6_중복_종목_제거():
    up.reset_cache()
    themes = _make_themes(2)
    # 두 테마가 같은 종목 1개 공유
    stocks_by_url = {
        themes[0]["url"]: [{"code": "100001", "name": "A"}, {"code": "100002", "name": "B"}],
        themes[1]["url"]: [{"code": "100001", "name": "A"}, {"code": "100003", "name": "C"}],
    }
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               side_effect=lambda url: stocks_by_url[url]):
        universe = up.get_universe()
    assert universe == ["100001", "100002", "100003"], f"UV-6 FAIL: {universe}"
    print("[PASS] UV-6: 테마 간 중복 종목 제거")


def test_UV_7_무효_종목코드_필터():
    up.reset_cache()
    themes = _make_themes(1)
    bad_stocks = [
        {"code": "0015G0", "name": "무효1"},   # 영문 포함
        {"code": "12345", "name": "무효2"},     # 5자리
        {"code": "1234567", "name": "무효3"},   # 7자리
        {"code": None, "name": "무효4"},         # None
        {"code": "100001", "name": "정상1"},
        {"code": "100002", "name": "정상2"},
    ]
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               return_value=bad_stocks):
        universe = up.get_universe()
    assert universe == ["100001", "100002"], f"UV-7 FAIL: {universe}"
    print("[PASS] UV-7: 무효 종목코드 정규식 필터 (4건 제외)")


def test_UV_8_hard_cap():
    up.reset_cache()
    themes = _make_themes(5)
    stocks_by_url = {themes[i]["url"]: _make_stocks(f"{i:02d}", 10) for i in range(5)}
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value=set()), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               side_effect=lambda url: stocks_by_url[url]):
        universe = up.get_universe(stocks_per_theme=10, hard_cap=15)
    assert len(universe) == 15, f"UV-8 FAIL: {len(universe)}건"
    print(f"[PASS] UV-8: hard_cap=15 적용 → {len(universe)}건 잘라냄")


def test_UV_9_스윙_보유_제외():
    up.reset_cache()
    themes = _make_themes(1)
    stocks = [
        {"code": "100001", "name": "스윙보유1"},
        {"code": "100002", "name": "정상1"},
        {"code": "100003", "name": "스윙보유2"},
    ]
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings",
                      return_value={"100001", "100003"}), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               return_value=stocks):
        universe = up.get_universe()
    assert universe == ["100002"], f"UV-9 FAIL: {universe}"
    print("[PASS] UV-9: 스윙 보유 종목 제외")


def test_UV_10_exclude_swing_holdings_off():
    """스윙 제외 옵션 끄면 보유 종목도 universe 에 포함."""
    up.reset_cache()
    themes = _make_themes(1)
    stocks = [{"code": "100001", "name": "보유"}, {"code": "100002", "name": "신규"}]
    with patch.object(up, "_fetch_top_themes", return_value=themes), \
         patch.object(up, "_fetch_swing_holdings", return_value={"100001"}), \
         patch("modules.theme_analyzer.crawlers.crawl_naver_theme_stocks",
               return_value=stocks):
        universe = up.get_universe(exclude_swing_holdings=False)
    assert universe == ["100001", "100002"], f"UV-10 FAIL: {universe}"
    print("[PASS] UV-10: exclude_swing_holdings=False → 보유 종목 포함")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 B 검증: universe_provider")
    print("=" * 60)
    test_UV_1_정상_5테마_4종목()
    test_UV_2_캐시_히트()
    test_UV_3_빈_DB()
    test_UV_4_url_없는_테마_스킵()
    test_UV_5_크롤링_예외_격리()
    test_UV_6_중복_종목_제거()
    test_UV_7_무효_종목코드_필터()
    test_UV_8_hard_cap()
    test_UV_9_스윙_보유_제외()
    test_UV_10_exclude_swing_holdings_off()
    print("\n" + "=" * 60)
    print("✅ 단위 B 10 시나리오 모두 PASS")
    print("=" * 60)
