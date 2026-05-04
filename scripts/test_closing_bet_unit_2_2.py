"""단위 2-2 검증: kind_alert_collector.

시나리오:
    KA-1: provider 미주입 → 빈 dict + is_valid=True
    KA-2: provider 정상 → alerts dict + severity_map 정확
    KA-3: provider 예외 → is_valid=False, error_msg 채움
    KA-4: provider 반환 dict 아님 → is_valid=False
    KA-5: severity 매핑 (4 단계 한글명 → 1~3)
    KA-6: 무효 ticker (5자리, 영문) 자동 제외
    KA-7: 무효 level (한글명 외) 자동 제외
    KA-8: severity_for(ticker) 헬퍼
    KA-9: OvernightRiskFilter 통합 — severity 3 (위험) → can_enter=False
    KA-10: severity 2 (경고) → reduced_size=0.5
    KA-11: severity 1 (주의) → 통과 (size 영향 없음, warnings에 추가)
    KA-12: kind_alerts=None → 영향 없음 (기존 호출처 호환)
    KA-13: ticker 미존재 → 영향 없음 (정상 통과)
    KA-14: dict 입력 (한글명) 호환 → severity 추출
    KA-15: dict 입력 (정수) 호환 → severity 추출
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors.kind_alert_collector import (
    KindAlertCollector,
    KindAlertSnapshot,
    ALERT_LEVEL_TO_SEVERITY,
)
from closing_bet_system.engines.overnight_risk_filter import (
    OvernightRiskFilter,
    _resolve_kind_severity,
)


def test_KA_1_provider_미주입():
    c = KindAlertCollector()
    snap = c.collect()
    assert snap.is_valid is True
    assert snap.alerts == {}
    assert snap.severity_map == {}
    print("[PASS] KA-1: provider 미주입 → 빈 dict + is_valid=True")


def test_KA_2_provider_정상():
    c = KindAlertCollector(provider=lambda: {
        "005930": "주의", "000660": "경고", "066570": "위험",
    })
    snap = c.collect()
    assert snap.is_valid is True
    assert snap.alerts == {"005930": "주의", "000660": "경고", "066570": "위험"}
    assert snap.severity_map == {"005930": 1, "000660": 2, "066570": 3}
    print("[PASS] KA-2: provider 정상 → 3 종목 alerts/severity_map")


def test_KA_3_provider_예외():
    c = KindAlertCollector(provider=lambda: (_ for _ in ()).throw(RuntimeError("KIND 사이트 차단")))
    snap = c.collect()
    assert snap.is_valid is False
    assert snap.alerts == {}
    assert "provider_exception" in (snap.error_msg or "")
    print("[PASS] KA-3: provider 예외 → is_valid=False")


def test_KA_4_provider_반환_dict_아님():
    c = KindAlertCollector(provider=lambda: ["005930"])  # list 반환 (오류)
    snap = c.collect()
    assert snap.is_valid is False
    assert snap.error_msg == "provider_returned_non_dict"
    print("[PASS] KA-4: provider 반환 dict 아님 → is_valid=False")


def test_KA_5_severity_매핑_4단계():
    expected = {
        "투자주의": 1, "주의": 1,
        "투자경고": 2, "경고": 2,
        "투자위험": 3, "위험": 3,
        "매매거래정지": 3, "거래정지": 3, "정지": 3,
    }
    for level, sev in expected.items():
        assert ALERT_LEVEL_TO_SEVERITY[level] == sev
    print(f"[PASS] KA-5: severity 매핑 {len(expected)} 한글명 정확")


def test_KA_6_무효_ticker_자동_제외():
    c = KindAlertCollector(provider=lambda: {
        "005930": "주의",          # 정상
        "abc": "경고",              # 영문
        "12345": "위험",            # 5자리
        "1234567": "주의",          # 7자리
        "": "주의",                 # 빈
        None: "주의",               # None (TypeError 회피)
    })
    snap = c.collect()
    assert snap.alerts == {"005930": "주의"}
    print("[PASS] KA-6: 무효 ticker 5건 자동 제외 → 1건만 통과")


def test_KA_7_무효_level_자동_제외():
    c = KindAlertCollector(provider=lambda: {
        "005930": "주의",         # 정상
        "000660": "알수없음",     # 정의 안 된 한글명
        "066570": 1,              # 정수 (str 아님)
        "100001": None,           # None
    })
    snap = c.collect()
    assert snap.alerts == {"005930": "주의"}
    print("[PASS] KA-7: 무효 level 3건 자동 제외 → 1건만 통과")


def test_KA_8_severity_for():
    snap = KindAlertSnapshot(
        snapshot_date=None,  # type: ignore[arg-type]
        is_valid=True,
        alerts={"005930": "위험"},
        severity_map={"005930": 3},
    )
    assert snap.severity_for("005930") == 3
    assert snap.severity_for("000660") == 0  # 미존재
    print("[PASS] KA-8: severity_for(ticker) 헬퍼 정상")


def test_KA_9_RiskFilter_severity_3_제외():
    rf = OvernightRiskFilter()
    snap = KindAlertSnapshot(snapshot_date=None, is_valid=True, severity_map={"005930": 3})  # type: ignore[arg-type]
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None, kind_alerts=snap)
    assert assess.can_enter is False
    assert assess.final_size_factor == 0.0
    assert "KIND" in assess.decision_reason
    print(f"[PASS] KA-9: severity 3 → can_enter=False ({assess.decision_reason[:40]}...)")


def test_KA_10_RiskFilter_severity_2_축소():
    rf = OvernightRiskFilter()
    snap = KindAlertSnapshot(snapshot_date=None, is_valid=True, severity_map={"005930": 2})  # type: ignore[arg-type]
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None, kind_alerts=snap)
    assert assess.can_enter is True
    assert assess.final_size_factor == 0.5
    assert any("KIND 투자경고" in w for w in assess.market_warnings)
    print(f"[PASS] KA-10: severity 2 → can_enter=True, size=0.5")


def test_KA_11_RiskFilter_severity_1_통과():
    rf = OvernightRiskFilter()
    snap = KindAlertSnapshot(snapshot_date=None, is_valid=True, severity_map={"005930": 1})  # type: ignore[arg-type]
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None, kind_alerts=snap)
    assert assess.can_enter is True
    assert assess.final_size_factor == 1.0  # 영향 없음
    assert any("KIND 투자주의" in w for w in assess.market_warnings)
    print("[PASS] KA-11: severity 1 → 통과, warnings에만 추가")


def test_KA_12_kind_alerts_None():
    """kind_alerts=None 기존 호출처 호환 (회귀 테스트)."""
    rf = OvernightRiskFilter()
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None, kind_alerts=None)
    assert assess.can_enter is True
    assert assess.final_size_factor == 1.0
    # KIND warning 없어야 함
    assert not any("KIND" in w for w in assess.market_warnings)
    print("[PASS] KA-12: kind_alerts=None → 기존 동작 유지 (회귀)")


def test_KA_13_ticker_미존재():
    rf = OvernightRiskFilter()
    snap = KindAlertSnapshot(snapshot_date=None, is_valid=True, severity_map={"000660": 3})  # type: ignore[arg-type]
    # 다른 ticker 평가
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None, kind_alerts=snap)
    assert assess.can_enter is True  # ticker 미존재 → 영향 없음
    assert not any("KIND" in w for w in assess.market_warnings)
    print("[PASS] KA-13: ticker 미존재 → 영향 없음")


def test_KA_14_dict_한글명_입력():
    """RiskFilter.assess에 dict (ticker→한글명) 직접 전달."""
    rf = OvernightRiskFilter()
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None,
                       kind_alerts={"005930": "위험"})
    assert assess.can_enter is False
    print("[PASS] KA-14: dict (한글명) 입력 → severity 추출 정상")


def test_KA_15_dict_정수_입력():
    rf = OvernightRiskFilter()
    assess = rf.assess(ticker="005930", market_data={}, dart_snapshot=None,
                       kind_alerts={"005930": 2})
    assert assess.can_enter is True
    assert assess.final_size_factor == 0.5
    print("[PASS] KA-15: dict (정수) 입력 → severity 추출 정상")


def test_KA_16_resolve_severity_헬퍼():
    """_resolve_kind_severity 직접 검증."""
    assert _resolve_kind_severity(None, "005930") == 0
    assert _resolve_kind_severity({}, "005930") == 0
    assert _resolve_kind_severity({"005930": 3}, "005930") == 3
    assert _resolve_kind_severity({"005930": "주의"}, "005930") == 1
    snap = KindAlertSnapshot(snapshot_date=None, is_valid=True, severity_map={"005930": 2})  # type: ignore[arg-type]
    assert _resolve_kind_severity(snap, "005930") == 2
    print("[PASS] KA-16: _resolve_kind_severity — None/dict/snapshot 모두 OK")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 2-2 검증: kind_alert_collector")
    print("=" * 60)
    test_KA_1_provider_미주입()
    test_KA_2_provider_정상()
    test_KA_3_provider_예외()
    test_KA_4_provider_반환_dict_아님()
    test_KA_5_severity_매핑_4단계()
    test_KA_6_무효_ticker_자동_제외()
    test_KA_7_무효_level_자동_제외()
    test_KA_8_severity_for()
    test_KA_9_RiskFilter_severity_3_제외()
    test_KA_10_RiskFilter_severity_2_축소()
    test_KA_11_RiskFilter_severity_1_통과()
    test_KA_12_kind_alerts_None()
    test_KA_13_ticker_미존재()
    test_KA_14_dict_한글명_입력()
    test_KA_15_dict_정수_입력()
    test_KA_16_resolve_severity_헬퍼()
    print("\n" + "=" * 60)
    print("✅ 단위 2-2 16 시나리오 모두 PASS")
    print("=" * 60)
