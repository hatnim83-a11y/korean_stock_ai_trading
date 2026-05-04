"""단위 D 검증: label_provider.

시나리오:
    LP-1: 정상 — open=+0.7%, high=+1.5%, low=-1.2% → gap_up=T, morning_exit=T, stop_risk=F
    LP-2: 갭다운 — open=-0.3%, low=-2.0% → gap_up=F, stop_risk=T
    LP-3: 보합 — open=0%, high=+0.3%, low=-0.3% → 모두 False
    LP-4: 무효 ticker → None
    LP-5: KIS 예외 → None
    LP-6: 일봉 데이터 1행만 → None
    LP-7: 어제 종가 0 → None
    LP-8: 일봉 키 누락 → 해당 pct None, 라벨 None
    LP-9: minimum_target_return 예외 → morning_exit/net_ev_positive None, 다른 라벨 정상
    LP-10: orchestrator 통합 — register_jobs(scheduler) → run_label_yesterday 가 self._label_provider 사용
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import label_provider as lp


def _mock_kis(rows):
    """get_kis_api() 가 반환할 fake KISApi mock."""
    fake = MagicMock()
    fake.get_daily_price.return_value = rows
    return fake


def test_LP_1_정상_gap_up_morning_exit():
    """어제 close=10000, 오늘 open=10070(+0.7%), high=10150(+1.5%), low=9880(-1.2%)."""
    rows = [
        {"date": "20260504", "open": 10070, "high": 10150, "low": 9880, "close": 10100, "volume": 100000},
        {"date": "20260503", "open": 9900, "high": 10050, "low": 9850, "close": 10000, "volume": 90000},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result is not None
    assert result["next_open_pct"] == 0.007
    assert result["next_morning_high_pct"] == 0.015
    assert abs(result["next_morning_low_pct"] - (-0.012)) < 1e-9
    assert result["label_gap_up"] is True
    # minimum_target_return ~ 0.0091 → high 0.015 > 0.0091
    assert result["label_morning_exit"] is True
    assert result["label_net_ev_positive"] is True
    assert result["label_stop_risk"] is False  # -0.012 > -0.015
    print(f"[PASS] LP-1: 정상 — gap_up=T, morning_exit=T, stop_risk=F (low={result['next_morning_low_pct']:.4f})")


def test_LP_2_갭다운_stop_risk():
    """어제 close=10000, 오늘 open=9970(-0.3%), high=10000(0%), low=9800(-2.0%)."""
    rows = [
        {"date": "20260504", "open": 9970, "high": 10000, "low": 9800, "close": 9850, "volume": 100000},
        {"date": "20260503", "open": 9900, "high": 10050, "low": 9850, "close": 10000, "volume": 90000},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result["label_gap_up"] is False  # -0.003 < 0.005
    assert result["label_stop_risk"] is True  # -0.02 <= -0.015
    assert result["label_morning_exit"] is False  # 0.0 < 0.0091
    assert result["label_net_ev_positive"] is False
    print(f"[PASS] LP-2: 갭다운 — gap_up=F, stop_risk=T (low=-2%)")


def test_LP_3_보합():
    rows = [
        {"date": "20260504", "open": 10000, "high": 10030, "low": 9970, "close": 10010, "volume": 100000},
        {"date": "20260503", "open": 9900, "high": 10050, "low": 9850, "close": 10000, "volume": 90000},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result["label_gap_up"] is False
    assert result["label_stop_risk"] is False
    assert result["label_morning_exit"] is False
    print("[PASS] LP-3: 보합 — 모든 라벨 False")


def test_LP_4_무효_ticker():
    for invalid in ["abc", "12345", "1234567", "", None, 100000]:
        result = lp.get_label(invalid)  # type: ignore[arg-type]
        assert result is None, f"LP-4 FAIL: {invalid!r} → {result}"
    print("[PASS] LP-4: 무효 ticker → None")


def test_LP_5_KIS_예외():
    fake = MagicMock()
    fake.get_daily_price.side_effect = RuntimeError("KIS 장애")
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=fake):
        result = lp.get_label("005930")
    assert result is None
    print("[PASS] LP-5: KIS 예외 → None")


def test_LP_6_일봉_1행():
    rows = [{"date": "20260504", "open": 10000, "high": 10100, "low": 9900, "close": 10050}]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result is None
    print("[PASS] LP-6: 일봉 1행 → None (전일 비교 불가)")


def test_LP_7_어제_종가_0():
    rows = [
        {"date": "20260504", "open": 10000, "high": 10100, "low": 9900, "close": 10050},
        {"date": "20260503", "open": 0, "high": 0, "low": 0, "close": 0},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result is None
    print("[PASS] LP-7: 어제 종가 0 → None")


def test_LP_8_일봉_키_누락():
    """오늘 high/low 누락 → 해당 pct None, 라벨 None."""
    rows = [
        {"date": "20260504", "open": 10070, "close": 10100},  # high/low 누락
        {"date": "20260503", "close": 10000},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result is not None
    assert result["next_open_pct"] == 0.007
    assert result["next_morning_high_pct"] is None
    assert result["next_morning_low_pct"] is None
    assert result["label_gap_up"] is True  # open 정상
    assert result["label_stop_risk"] is None
    assert result["label_morning_exit"] is None
    assert result["label_net_ev_positive"] is None
    print("[PASS] LP-8: 일봉 high/low 누락 → 해당 라벨 None, gap_up 정상")


def test_LP_9_minimum_target_예외():
    rows = [
        {"date": "20260504", "open": 10070, "high": 10150, "low": 9880, "close": 10100},
        {"date": "20260503", "close": 10000},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)), \
         patch.object(lp, "_safe_minimum_target_return", return_value=None):
        result = lp.get_label("005930")
    assert result["label_gap_up"] is True
    assert result["label_stop_risk"] is False
    assert result["label_morning_exit"] is None  # cost_engine 실패
    assert result["label_net_ev_positive"] is None
    print("[PASS] LP-9: cost_engine 예외 → morning_exit/ev_positive None")


def test_LP_10b_정렬_방향_위반():
    """KIS 정렬 방향이 비정상 (오름차순) → None 반환."""
    rows = [
        # 의도적으로 오래된 날짜를 첫 행에 넣음
        {"date": "20260501", "open": 9000, "high": 9100, "low": 8900, "close": 9000},
        {"date": "20260502", "open": 9000, "high": 9200, "low": 8950, "close": 9100},
    ]
    with patch("closing_bet_system.infra.kis_client.get_kis_api", return_value=_mock_kis(rows)):
        result = lp.get_label("005930")
    assert result is None, "LP-10b FAIL: 오름차순 데이터 통과"
    print("[PASS] LP-10b: KIS 정렬 방향 위반 (오름차순) → None")


def test_LP_10_orchestrator_통합():
    """orchestrator self._label_provider 폴백 경로 검증."""
    from closing_bet_system.main_orchestrator import MainOrchestrator
    fake_provider = MagicMock(return_value={
        "next_open_pct": 0.005,
        "next_morning_high_pct": 0.012,
        "next_morning_low_pct": -0.005,
        "label_gap_up": True,
        "label_morning_exit": True,
        "label_stop_risk": False,
        "label_net_ev_positive": True,
    })
    fake_logger = MagicMock()
    fake_logger.get_candidates_in_period.return_value = [
        {"candidate_id": 1, "ticker": "005930"},
        {"candidate_id": 2, "ticker": "000660"},
    ]
    fake_logger.log_labels = MagicMock()
    orch = MainOrchestrator(
        candidate_logger=fake_logger,
        label_provider=fake_provider,
    )
    # APScheduler 처럼 인자 없이 호출
    result = asyncio.run(orch.run_label_yesterday())
    assert result["labeled"] == 2, f"LP-10 FAIL: {result}"
    assert fake_provider.call_count == 2
    assert fake_logger.log_labels.call_count == 2
    print("[PASS] LP-10: orchestrator self._label_provider 폴백 — 2건 라벨링")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 D 검증: label_provider")
    print("=" * 60)
    test_LP_1_정상_gap_up_morning_exit()
    test_LP_2_갭다운_stop_risk()
    test_LP_3_보합()
    test_LP_4_무효_ticker()
    test_LP_5_KIS_예외()
    test_LP_6_일봉_1행()
    test_LP_7_어제_종가_0()
    test_LP_8_일봉_키_누락()
    test_LP_9_minimum_target_예외()
    test_LP_10b_정렬_방향_위반()
    test_LP_10_orchestrator_통합()
    print("\n" + "=" * 60)
    print("✅ 단위 D 11 시나리오 모두 PASS")
    print("=" * 60)
