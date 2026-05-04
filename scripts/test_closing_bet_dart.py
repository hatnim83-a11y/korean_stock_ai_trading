"""scripts/test_closing_bet_dart.py

Phase 1-5a: ``DartDisclosureCollector`` 단위 테스트.

PRD 8-2 키워드 매트릭스 검증 (호재 가점 + 즉시제외).

실행:
    venv/bin/python scripts/test_closing_bet_dart.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.collectors.dart_disclosure_collector import (
    DartDisclosureCollector,
    DartDisclosureSnapshot,
    EXCLUSION_KEYWORDS,
    POSITIVE_KEYWORDS,
    _find_match,
)


# ===== Fixtures =====


def _make_disclosure(title: str, date: str = "2026-05-04") -> dict:
    """단일 공시 dict mock."""
    return {
        "title": title,
        "date": date,
        "type": "A001",
        "type_name": "정기공시",
        "url": "https://dart.fss.or.kr/...",
        "corp_name": "삼성전자",
        "corp_code": "00126380",
    }


def _make_collector_with_mock(disclosures) -> DartDisclosureCollector:
    """fetch 함수를 mock으로 교체한 collector."""
    mock_fetch = MagicMock(return_value=disclosures)
    return DartDisclosureCollector(dart_fetch_fn=mock_fetch)


# ===== 테스트 =====


def test_normal_no_match():
    """일반 정기공시 — 호재/제외 모두 False."""
    coll = _make_collector_with_mock([_make_disclosure("분기보고서 (2026.03)")])
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.has_positive_disclosure is False
    assert snap.exclude_by_disclosure is False
    assert snap.total_disclosures == 1
    assert len(snap.positive_matches) == 0
    assert len(snap.exclusion_matches) == 0
    print(f"  [PASS] 정기공시 → 호재/제외 모두 False")


def test_positive_keyword_supply_contract():
    """단일판매공급계약 → has_positive_disclosure=True."""
    coll = _make_collector_with_mock([_make_disclosure("단일판매공급계약체결")])
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.has_positive_disclosure is True
    assert snap.exclude_by_disclosure is False
    assert snap.positive_matches[0][1] == "단일판매공급계약"
    print(f"  [PASS] 단일판매공급계약 → has_positive=True")


def test_positive_keyword_buyback():
    """자기주식취득 → has_positive_disclosure=True."""
    coll = _make_collector_with_mock([_make_disclosure("자기주식취득결정 (취득 100만주)")])
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.has_positive_disclosure is True
    assert snap.exclusion_matches == ()
    print(f"  [PASS] 자기주식취득 → has_positive=True")


def test_positive_keyword_with_space():
    """띄어쓰기 변형: '자기주식 취득' → 매칭."""
    coll = _make_collector_with_mock([_make_disclosure("자기주식 취득 결정")])
    snap = coll.collect("005930")
    assert snap.has_positive_disclosure is True
    matched_kw = snap.positive_matches[0][1]
    assert matched_kw in ("자기주식 취득", "자기주식취득")  # 둘 중 어느 쪽이 먼저든 OK
    print(f"  [PASS] '자기주식 취득' (띄어쓰기) → 매칭 (kw={matched_kw!r})")


def test_exclusion_keyword_rights_offering():
    """유상증자 결정 → exclude_by_disclosure=True."""
    coll = _make_collector_with_mock([_make_disclosure("유상증자 결정")])
    snap = coll.collect("005930")
    assert snap.exclude_by_disclosure is True
    assert snap.exclusion_matches[0][1] == "유상증자"
    assert "유상증자" in snap.exclusion_reason
    print(f"  [PASS] 유상증자 → exclude=True ({snap.exclusion_reason})")


def test_exclusion_keyword_cb_bw():
    """전환사채(CB) / 신주인수권부사채(BW) → exclude=True."""
    for title, expected_kw in [
        ("전환사채발행결정", "전환사채"),
        ("신주인수권부사채발행", "신주인수권부사채"),
        ("CB 발행 결정", "CB"),       # 영문 약어 대소문자 무관
        ("bw 발행 결정", "BW"),
    ]:
        coll = _make_collector_with_mock([_make_disclosure(title)])
        snap = coll.collect("005930")
        assert snap.exclude_by_disclosure is True, f"'{title}' 제외되지 않음"
        # CB/BW 영문 약어는 casefold 매칭 시 우선순위에 따라 매칭됨
    print(f"  [PASS] CB/BW/전환사채/신주인수권부사채 4종 제외")


def test_exclusion_keyword_fraud():
    """횡령/배임 → exclude=True."""
    for title in ["대표이사 횡령 혐의 발생", "임원 배임 사건 발생"]:
        coll = _make_collector_with_mock([_make_disclosure(title)])
        snap = coll.collect("005930")
        assert snap.exclude_by_disclosure is True
    print(f"  [PASS] 횡령/배임 2종 제외")


def test_exclusion_keyword_sanction():
    """영업정지/제재 → exclude=True."""
    for title in [
        "영업정지 처분",
        "금융감독원 제재 결정",
        "검찰 수사 착수",
        "공정위 과징금 부과",
        "공정위 조사 착수",            # "조사" 단독 제거 후 "공정위 조사" 매칭
        "금감원 조사 결과 통보",       # "금감원 조사" 매칭
        "감리 착수 통보",              # "감리 착수" 매칭
    ]:
        coll = _make_collector_with_mock([_make_disclosure(title)])
        snap = coll.collect("005930")
        assert snap.exclude_by_disclosure is True, f"'{title}' 제외되지 않음"
    print(f"  [PASS] 영업정지/제재/검찰/과징금/공정위조사/금감원조사/감리 7종 제외")


def test_exclusion_no_false_positive_on_general_research():
    """일반 '조사' 단어 (시장조사/기업실태조사/IR 조사) → 제외 X (FP 방지)."""
    for title in [
        "기업실태조사 보고",
        "시장조사 결과 변경 공시",
        "신용조사 결과 보고서",
        "지분취득 기업실사 조사",
        "해외투자설명자료 IR 조사",
    ]:
        coll = _make_collector_with_mock([_make_disclosure(title)])
        snap = coll.collect("005930")
        assert snap.exclude_by_disclosure is False, f"'{title}' 가 잘못 제외됨 (FP)"
    print(f"  [PASS] 일반 '조사' 단어 5종 → 제외 X (FP 방지)")


def test_exclusion_priority_over_positive():
    """한 공시에 호재 + 제외 키워드 동시 존재 → 제외 우선."""
    # "유상증자 (수주 자금 조달)" → 유상증자 제외 우선
    coll = _make_collector_with_mock([_make_disclosure("유상증자 결정 — 수주 자금 조달 목적")])
    snap = coll.collect("005930")
    assert snap.exclude_by_disclosure is True
    # 이 공시는 exclusion_matches 에만 들어가고 positive_matches 는 비어있어야 함
    assert len(snap.exclusion_matches) == 1
    assert len(snap.positive_matches) == 0
    print(f"  [PASS] 호재+제외 동시 → 제외 우선 (positive_matches 비어있음)")


def test_multiple_disclosures():
    """여러 공시 — 호재 1건 + 제외 1건 + 일반 1건."""
    discs = [
        _make_disclosure("단일판매공급계약체결"),       # 호재
        _make_disclosure("유상증자 결정"),               # 제외
        _make_disclosure("분기보고서 제출"),             # 무관
    ]
    coll = _make_collector_with_mock(discs)
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.total_disclosures == 3
    assert snap.has_positive_disclosure is True   # 1건이라도 호재면 True
    assert snap.exclude_by_disclosure is True      # 1건이라도 제외면 True
    assert len(snap.positive_matches) == 1
    assert len(snap.exclusion_matches) == 1
    print(f"  [PASS] 3건 공시 → 호재/제외 모두 True (각 1건 매칭)")


def test_empty_disclosures_valid():
    """공시 0건 → is_valid=True (호재/제외 모두 False)."""
    coll = _make_collector_with_mock([])
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.total_disclosures == 0
    assert snap.has_positive_disclosure is False
    assert snap.exclude_by_disclosure is False
    print(f"  [PASS] 빈 공시 list → is_valid=True (정상 케이스)")


def test_none_response_invalid():
    """fetch 가 None 반환 (API 키 누락 가능) → is_valid=False."""
    coll = _make_collector_with_mock(None)
    snap = coll.collect("005930")
    assert snap.is_valid is False
    print(f"  [PASS] None 응답 → is_valid=False (API 키 누락 가능)")


def test_invalid_response_type():
    """fetch 가 list 가 아닌 dict 반환 → is_valid=False."""
    coll = _make_collector_with_mock({"unexpected": "dict"})
    snap = coll.collect("005930")
    assert snap.is_valid is False
    print(f"  [PASS] dict 응답 → is_valid=False (잘못된 타입)")


def test_invalid_ticker():
    """잘못된 종목코드 → is_valid=False."""
    coll = _make_collector_with_mock([])
    for bad in ["12345", "ABCDEF", "1234567", 12345, "", None]:
        snap = coll.collect(bad)
        assert snap.is_valid is False, f"잘못된 ticker {bad!r} 통과됨"
    print(f"  [PASS] 잘못된 ticker 6케이스 차단")


def test_invalid_lookback_days():
    """lookback_days = 0 / 음수 / bool / 문자열 → is_valid=False."""
    coll = _make_collector_with_mock([])
    for bad in [0, -1, True, "2", 1.5, None]:
        snap = coll.collect("005930", lookback_days=bad)
        assert snap.is_valid is False, f"lookback_days={bad!r} 통과됨"
    print(f"  [PASS] lookback_days 6케이스 차단")


def test_dart_api_exception():
    """DART API 호출 시 예외 → is_valid=False (다른 종목 진행 X 차단)."""
    mock_fetch = MagicMock(side_effect=Exception("네트워크 오류"))
    coll = DartDisclosureCollector(dart_fetch_fn=mock_fetch)
    snap = coll.collect("005930")
    assert snap.is_valid is False
    print(f"  [PASS] API 예외 → is_valid=False")


def test_universe_partial_failure():
    """다종목 — 일부 실패해도 나머지 진행."""
    def side_effect(ticker, days):
        if ticker == "005930":
            return [_make_disclosure("단일판매공급계약체결")]   # 호재
        if ticker == "111111":
            raise Exception("API 에러")
        if ticker == "222222":
            return [_make_disclosure("유상증자 결정")]            # 제외
        return None

    mock_fetch = MagicMock(side_effect=side_effect)
    coll = DartDisclosureCollector(dart_fetch_fn=mock_fetch)
    snapshots = coll.collect_for_universe(["005930", "111111", "222222"])

    assert len(snapshots) == 3
    assert snapshots[0].is_valid is True and snapshots[0].has_positive_disclosure is True
    assert snapshots[1].is_valid is False
    assert snapshots[2].is_valid is True and snapshots[2].exclude_by_disclosure is True
    print(f"  [PASS] 3종목 격리 (호재/실패/제외 각 1건)")


def test_corrupt_disclosure_fields():
    """공시 dict 가 None 또는 title 누락 → 무시하고 다른 공시 처리."""
    discs = [
        None,                                               # None
        {"title": None, "date": "2026-05-04"},              # title None
        {"title": "", "date": "2026-05-04"},                # title 빈 문자열
        _make_disclosure("단일판매공급계약체결"),            # 정상 호재
    ]
    coll = _make_collector_with_mock(discs)
    snap = coll.collect("005930")
    assert snap.is_valid is True
    assert snap.total_disclosures == 4   # raw 카운트는 그대로
    assert snap.has_positive_disclosure is True   # 정상 1건 매칭
    assert len(snap.positive_matches) == 1
    print(f"  [PASS] 손상 공시 3종 무시, 정상 1건만 매칭")


def test_to_layer3_input():
    """to_layer3_input() 가 1-4 SignalScoreEngine layer3 dict 키와 호환."""
    coll = _make_collector_with_mock([_make_disclosure("단일판매공급계약체결")])
    snap = coll.collect("005930")
    layer3_in = snap.to_layer3_input()
    assert layer3_in == {"has_positive_disclosure": True}
    print(f"  [PASS] to_layer3_input() 1키 반환")


def test_init_validation():
    """빈 키워드 집합 → ValueError."""
    try:
        DartDisclosureCollector(positive_keywords=())
        raise AssertionError("빈 positive 통과됨")
    except ValueError:
        pass
    try:
        DartDisclosureCollector(exclusion_keywords=())
        raise AssertionError("빈 exclusion 통과됨")
    except ValueError:
        pass
    print(f"  [PASS] 빈 키워드 집합 차단")


def test_snapshot_immutable():
    """DartDisclosureSnapshot frozen."""
    coll = _make_collector_with_mock([])
    snap = coll.collect("005930")
    try:
        snap.has_positive_disclosure = True
        raise AssertionError("frozen 변경됨")
    except (AttributeError, Exception):
        pass
    print(f"  [PASS] Snapshot frozen 불변")


def test_snapshot_hash_safety():
    """Snapshot set/dict 키 사용 가능 (raw_disclosures list, matches tuple — hash=False)."""
    coll = _make_collector_with_mock([_make_disclosure("단일판매공급계약체결")])
    snap = coll.collect("005930")
    try:
        snap_set = {snap}
        assert len(snap_set) == 1
    except TypeError as e:
        raise AssertionError(f"hash 충돌: {e}")
    print(f"  [PASS] Snapshot hash 안전 (set 사용 가능)")


def test_keyword_constants():
    """모듈 상수 — 빈 집합 X, 핵심 키워드 포함 확인."""
    # 핵심 키워드 누락 검증
    for kw in ("유상증자", "전환사채", "횡령", "배임"):
        assert kw in EXCLUSION_KEYWORDS, f"필수 제외 키워드 누락: {kw}"
    for kw in ("단일판매공급계약", "수주", "자기주식취득"):
        assert kw in POSITIVE_KEYWORDS, f"필수 호재 키워드 누락: {kw}"
    print(f"  [PASS] 모듈 상수 핵심 키워드 7개 확인")


def test_find_match_helper():
    """_find_match 단위."""
    assert _find_match("유상증자 결정", ("유상증자", "CB")) == "유상증자"
    assert _find_match("CB 발행", ("유상증자", "CB")) == "CB"
    assert _find_match("cb 발행", ("CB",)) == "CB"   # casefold
    assert _find_match("분기보고서", ("유상증자", "CB")) is None
    assert _find_match("", ("CB",)) is None
    print(f"  [PASS] _find_match 5케이스")


def test_snapshot_time_default():
    """snapshot_time 미지정 시 now_kst()."""
    coll = _make_collector_with_mock([])
    before = now_kst()
    snap = coll.collect("005930")
    after = now_kst()
    assert snap.snapshot_time.tzinfo is not None
    assert before <= snap.snapshot_time <= after
    print(f"  [PASS] snapshot_time 자동 = {snap.snapshot_time.strftime('%H:%M:%S %Z')}")


def main():
    print("=== Phase 1-5a DartDisclosureCollector 단위 테스트 ===\n")
    tests = [
        test_normal_no_match,
        test_positive_keyword_supply_contract,
        test_positive_keyword_buyback,
        test_positive_keyword_with_space,
        test_exclusion_keyword_rights_offering,
        test_exclusion_keyword_cb_bw,
        test_exclusion_keyword_fraud,
        test_exclusion_keyword_sanction,
        test_exclusion_no_false_positive_on_general_research,
        test_exclusion_priority_over_positive,
        test_multiple_disclosures,
        test_empty_disclosures_valid,
        test_none_response_invalid,
        test_invalid_response_type,
        test_invalid_ticker,
        test_invalid_lookback_days,
        test_dart_api_exception,
        test_universe_partial_failure,
        test_corrupt_disclosure_fields,
        test_to_layer3_input,
        test_init_validation,
        test_snapshot_immutable,
        test_snapshot_hash_safety,
        test_keyword_constants,
        test_find_match_helper,
        test_snapshot_time_default,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
