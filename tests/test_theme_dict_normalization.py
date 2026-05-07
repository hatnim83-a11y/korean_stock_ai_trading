"""
test_theme_dict_normalization.py - 테마 dict 정규화 핫픽스 단위 테스트

검증 대상:
- main.py 헬퍼 _coalesce_zero(v) — SQL NULL → 0, 실제 0/0.0 보존
- main.py 헬퍼 _pick_momentum(t) — momentum_score → momentum 폴백 체인 (is None 체크)
- main.py:184~ DB 복원 정규화 — themes_from_db → today_themes, 점수 4개 키 보존
- main.py:438 "기존 테마 유지" 저장 — _pick_momentum 폴백 체인 정상 매치
- main.py:1988~ midweek 교체 — replacement dict에 점수 4개 키 보유

실행:
    pytest tests/test_theme_dict_normalization.py -v
또는 직접:
    python tests/test_theme_dict_normalization.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import _coalesce_zero, _pick_momentum
from database import Database


# ============================================================
# TC-5: _coalesce_zero — NULL/0/0.0/실수 구분
# ============================================================
def test_coalesce_zero_distinguishes_null_and_zero():
    """SQL NULL → 0, 실제 0/0.0 → 그대로 보존"""
    # NULL은 0으로 변환
    assert _coalesce_zero(None) == 0

    # 실제 0 (정수) — 그대로 보존
    assert _coalesce_zero(0) == 0
    assert _coalesce_zero(0) is not None

    # 실제 0.0 (실수) — 그대로 보존 (5일 수익률 -10% 정상 케이스)
    assert _coalesce_zero(0.0) == 0.0

    # 양수 실수 — 그대로 보존
    assert _coalesce_zero(22.08) == 22.08

    # 음수 실수 — 그대로 보존 (이론상 발생 안 하지만 방어)
    assert _coalesce_zero(-5.0) == -5.0

    # 정수 양수 — 그대로 보존
    assert _coalesce_zero(100) == 100


# ============================================================
# TC-2: _pick_momentum — 폴백 체인 4 케이스
# ============================================================
def test_pick_momentum_prefers_momentum_score():
    """정규 재선정 직후: momentum_score + momentum 양쪽 보유 → momentum_score 우선 매치"""
    # scorer.py:658-659 출력 형태
    t = {"momentum_score": 24.5, "momentum": 24.5, "score": 65}
    assert _pick_momentum(t) == 24.5


def test_pick_momentum_falls_back_to_momentum():
    """DB 복원본: momentum 키만 보유 → 폴백 매치"""
    # database.py:693 dict(row) 결과 형태 (themes 컬럼명)
    t = {"theme_name": "AI반도체", "momentum": 22.08, "score": 34.4}
    assert _pick_momentum(t) == 22.08


def test_pick_momentum_returns_zero_when_both_missing():
    """둘 다 없는 회귀 박제 시나리오 → 0 반환"""
    # 박제 직전 today_themes (score만 있고 momentum 키 누락)
    t = {"theme": "X", "score": 34.4, "category": "기타"}
    assert _pick_momentum(t) == 0


def test_pick_momentum_preserves_actual_zero():
    """momentum_score=0.0 정상 케이스 — 0이 그대로 반환되어야 함 (NULL과 구분)"""
    # 5일 수익률 -10% → momentum_score = 0.0 정상 산출
    t = {"momentum_score": 0.0, "momentum": 0.0}
    result = _pick_momentum(t)
    assert result == 0.0
    assert result is not None  # NULL과 구분됨


def test_pick_momentum_handles_null_momentum_score_with_valid_momentum():
    """momentum_score=None이지만 momentum=값 — momentum으로 폴백"""
    # 이론적으로는 거의 발생 안 하나 방어 검증
    t = {"momentum_score": None, "momentum": 18.7}
    assert _pick_momentum(t) == 18.7


# ============================================================
# TC-1, TC-3: DB 복원 정규화 (실제 :memory: SQLite)
# ============================================================
def _make_in_memory_db() -> Database:
    """테스트용 in-memory DB 생성 + 테이블 초기화."""
    db = Database(db_path=":memory:")
    db.connect()
    db.init_tables()
    return db


def test_db_restore_preserves_momentum():
    """TC-1: DB row {momentum=22.08, ai_sentiment=7.67, news_count=100} → 복원 시 보존"""
    db = _make_in_memory_db()
    try:
        target_date = date(2026, 5, 1)
        # 정상 데이터로 INSERT
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO themes (
                    date, theme_name, score, momentum, supply_ratio,
                    news_count, ai_sentiment, category, selected, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (target_date, "AI반도체", 34.4, 22.08, 0.5, 100, 7.67, "반도체", 1, "http://x"))

        # get_top_themes로 복원
        rows = db.get_top_themes(target_date, count=1)
        assert len(rows) == 1
        assert rows[0]["momentum"] == 22.08
        assert rows[0]["ai_sentiment"] == 7.67
        assert rows[0]["news_count"] == 100

        # main.py:216~ normalized 변환 시뮬레이션
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
            for t in rows
        ]

        assert normalized[0]["momentum"] == 22.08
        assert normalized[0]["ai_sentiment"] == 7.67
        assert normalized[0]["news_count"] == 100
        assert normalized[0]["supply_ratio"] == 0.5

        # _pick_momentum이 momentum 키로 폴백 매치 (momentum_score 키 없음)
        assert _pick_momentum(normalized[0]) == 22.08
    finally:
        db.close()


def test_db_restore_with_null_columns():
    """TC-3: NULL 컬럼 → normalized.momentum=0 (None 아님), 후속 save_theme_scores TypeError 없음"""
    db = _make_in_memory_db()
    try:
        target_date = date(2026, 5, 7)
        # momentum/ai_sentiment/news_count 컬럼 NULL로 INSERT (소급 복구 시뮬레이션)
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO themes (
                    date, theme_name, score, momentum, supply_ratio,
                    news_count, ai_sentiment, category, selected, url
                ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
            """, (target_date, "AI반도체", 34.4, "반도체", 1, ""))

        rows = db.get_top_themes(target_date, count=1)
        assert rows[0]["momentum"] is None
        assert rows[0]["ai_sentiment"] is None
        assert rows[0]["news_count"] is None

        # normalized 변환
        normalized = [
            {
                "momentum": _coalesce_zero(t.get("momentum")),
                "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                "news_count": _coalesce_zero(t.get("news_count")),
                "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
                "score": t["score"],
                "theme": t["theme_name"],
            }
            for t in rows
        ]
        # NULL → 0으로 강제, None 아님
        assert normalized[0]["momentum"] == 0
        assert normalized[0]["momentum"] is not None
        assert normalized[0]["ai_sentiment"] == 0

        # 후속 save_theme_scores 호출 시 TypeError 없는지 검증
        save_dict = [{
            "theme": normalized[0]["theme"],
            "score": normalized[0]["score"],
            "momentum": _pick_momentum(normalized[0]),
            "supply_ratio": _coalesce_zero(normalized[0].get("supply_ratio")),
            "news_count": _coalesce_zero(normalized[0].get("news_count")),
            "ai_sentiment": _coalesce_zero(normalized[0].get("ai_sentiment")),
            "category": "기타",
            "url": "",
        }]
        # 정상 호출되어야 함 (TypeError 없음)
        db.save_theme_scores(save_dict, date(2026, 5, 8), selected=True)

        # 저장 결과 검증
        new_rows = db.get_top_themes(date(2026, 5, 8), count=1)
        assert new_rows[0]["momentum"] == 0  # NULL → 0 폴백
        assert new_rows[0]["ai_sentiment"] == 0
    finally:
        db.close()


# ============================================================
# TC-4: midweek 교체 replacement dict 점수 키 보유
# ============================================================
def test_midweek_replacement_dict_has_score_keys():
    """midweek 교체 시 today_themes.append 후 dict에 점수 4개 키 보유"""
    # main.py:1988~ 시뮬레이션 — replacement는 db.get_daily_theme_scores 결과 형태
    replacement = {
        "theme_name": "조선",
        "score": 55.3,
        "momentum": 18.7,
        "supply_ratio": 1.3,
        "news_count": 45,
        "ai_sentiment": 6.5,
        "category": "산업재",
        "url": "http://shipbuilding",
    }

    new_dict = {
        "name": replacement["theme_name"],
        "theme": replacement["theme_name"],
        "score": replacement["score"],
        "total_score": replacement["score"],
        "category": replacement.get("category", "기타"),
        "url": replacement.get("url", ""),
        "momentum": _coalesce_zero(replacement.get("momentum")),
        "supply_ratio": _coalesce_zero(replacement.get("supply_ratio")),
        "news_count": _coalesce_zero(replacement.get("news_count")),
        "ai_sentiment": _coalesce_zero(replacement.get("ai_sentiment")),
    }

    # 점수 4개 키 모두 보유 + 정상값
    assert new_dict["momentum"] == 18.7
    assert new_dict["supply_ratio"] == 1.3
    assert new_dict["news_count"] == 45
    assert new_dict["ai_sentiment"] == 6.5

    # 다음 영업일 "기존 테마 유지" 저장 시 _pick_momentum 폴백 체인 정상 매치
    # (momentum 키만 있고 momentum_score 없음 — DB 복원본과 동일 형태)
    assert _pick_momentum(new_dict) == 18.7


def test_midweek_replacement_with_null_columns_falls_back_to_zero():
    """midweek 교체 시 replacement에 NULL 컬럼 있어도 안전 (0 폴백)"""
    replacement = {
        "theme_name": "X",
        "score": 50.0,
        "momentum": None,  # DB에서 NULL 반환된 케이스
        "supply_ratio": None,
        "news_count": None,
        "ai_sentiment": None,
    }

    new_dict = {
        "momentum": _coalesce_zero(replacement.get("momentum")),
        "supply_ratio": _coalesce_zero(replacement.get("supply_ratio")),
        "news_count": _coalesce_zero(replacement.get("news_count")),
        "ai_sentiment": _coalesce_zero(replacement.get("ai_sentiment")),
    }

    assert new_dict["momentum"] == 0
    assert new_dict["momentum"] is not None
    assert new_dict["ai_sentiment"] == 0


# ============================================================
# TC-6: get_score_fallback_for_theme — 박제 자동 폴백
# UNIQUE(date, theme_name) 제약으로 같은 (date, theme_name) 공존 불가 → 직전 selected=1 정상값 폴백
# ============================================================
def test_fallback_uses_recent_selected_one_when_no_same_day():
    """2순위: 같은 날 selected=0 없으면 직전 selected=1 정상값에서 폴백"""
    db = _make_in_memory_db()
    try:
        # 4/29: selected=1 정상
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO themes (
                    date, theme_name, score, momentum, supply_ratio,
                    news_count, ai_sentiment, category, selected, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date(2026, 4, 29), "AI반도체", 34.4, 22.08, 0.5, 100, 7.67, "반도체", 1, ""))
            # 5/7: selected=1 박제 (같은 날 selected=0 없음)
            cur.execute("""
                INSERT INTO themes (
                    date, theme_name, score, momentum, supply_ratio,
                    news_count, ai_sentiment, category, selected, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date(2026, 5, 7), "AI반도체", 34.4, 0, 0, 0, 0, "반도체", 1, ""))

        fb = db.get_score_fallback_for_theme("AI반도체", date(2026, 5, 7))
        assert fb is not None
        assert fb["momentum"] == 22.08
        assert fb["ai_sentiment"] == 7.67
    finally:
        db.close()


def test_fallback_returns_none_when_no_data():
    """폴백 데이터 자체가 없으면 None 반환"""
    db = _make_in_memory_db()
    try:
        # 박제 row만 존재, 폴백 데이터 없음
        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO themes (
                    date, theme_name, score, momentum, supply_ratio,
                    news_count, ai_sentiment, category, selected, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date(2026, 5, 7), "신규테마", 30.0, 0, 0, 0, 0, "기타", 1, ""))

        fb = db.get_score_fallback_for_theme("신규테마", date(2026, 5, 7))
        assert fb is None
    finally:
        db.close()


# ============================================================
# 회귀 시나리오 통합 검증 — 박제 패턴 재현 + 차단 확인
# ============================================================
def test_regression_scenario_zero_pinning_blocked():
    """5/4 박제 시나리오 — DB 복원 후 다음 영업일 저장 시 momentum 보존되는지"""
    db = _make_in_memory_db()
    try:
        # 1. 4/29 정규 재선정 결과 (정상 momentum)
        regular_save = [{
            "theme": "AI반도체",
            "score": 34.4,
            "momentum": 22.08,  # 정규 재선정 분기는 momentum_score를 momentum 컬럼으로 저장 (database.py:651)
            "supply_ratio": 0.5,
            "news_count": 100,
            "ai_sentiment": 7.67,
            "category": "반도체",
            "url": "http://x",
        }]
        db.save_theme_scores(regular_save, date(2026, 4, 29), selected=True)

        # 2. systemd 재시작 시뮬레이션 — DB 복원 (main.py:184~)
        from_db = db.get_top_themes(date(2026, 4, 29), count=1)
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
            for t in from_db
        ]
        today_themes = normalized

        # 3. 5/4 (월) 08:30 "기존 테마 유지" 저장 (main.py:438~)
        themes_to_save = [
            {
                "theme": t.get("theme", t.get("name", "")),
                "score": t.get("score", 0),
                "momentum": _pick_momentum(t),
                "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                "news_count": _coalesce_zero(t.get("news_count")),
                "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
                "category": t.get("category", "기타"),
                "url": t.get("url", ""),
            }
            for t in today_themes
        ]
        db.save_theme_scores(themes_to_save, date(2026, 5, 4), selected=True)

        # 4. 5/4 selected=1 row의 momentum이 박제(0) 아닌 정상값(22.08)으로 저장됐는지
        result = db.get_top_themes(date(2026, 5, 4), count=1)
        assert result[0]["momentum"] == 22.08, f"박제 회귀 발생! momentum={result[0]['momentum']}"
        assert result[0]["ai_sentiment"] == 7.67
        assert result[0]["news_count"] == 100
    finally:
        db.close()


# ============================================================
# 직접 실행
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        ("TC-5 _coalesce_zero", test_coalesce_zero_distinguishes_null_and_zero),
        ("TC-2a _pick_momentum prefers momentum_score", test_pick_momentum_prefers_momentum_score),
        ("TC-2b _pick_momentum falls back to momentum", test_pick_momentum_falls_back_to_momentum),
        ("TC-2c _pick_momentum returns zero when both missing", test_pick_momentum_returns_zero_when_both_missing),
        ("TC-2d _pick_momentum preserves actual zero", test_pick_momentum_preserves_actual_zero),
        ("TC-2e _pick_momentum handles null momentum_score", test_pick_momentum_handles_null_momentum_score_with_valid_momentum),
        ("TC-1 DB restore preserves momentum", test_db_restore_preserves_momentum),
        ("TC-3 DB restore with NULL columns", test_db_restore_with_null_columns),
        ("TC-4a midweek replacement dict has score keys", test_midweek_replacement_dict_has_score_keys),
        ("TC-4b midweek replacement with NULL columns", test_midweek_replacement_with_null_columns_falls_back_to_zero),
        ("TC-6a fallback uses recent selected=1 정상값", test_fallback_uses_recent_selected_one_when_no_same_day),
        ("TC-6b fallback returns None when no data", test_fallback_returns_none_when_no_data),
        ("Regression: zero pinning blocked", test_regression_scenario_zero_pinning_blocked),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n총 {len(tests)}건 — PASS {passed} / FAIL {failed}")
    sys.exit(0 if failed == 0 else 1)
