"""scripts/test_closing_bet_orchestrator.py

Phase 1-8: ``MainOrchestrator`` 통합 테스트.

전체 1-2~1-7 모듈을 mock 으로 주입하고 일일 파이프라인 동작 검증.

실행:
    venv/bin/python scripts/test_closing_bet_orchestrator.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from config import now_kst
from closing_bet_system.collectors.dart_disclosure_collector import DartDisclosureSnapshot
from closing_bet_system.collectors.kis_intraday_flow_collector import IntradayFlowSnapshot
from closing_bet_system.collectors.kis_price_volume_collector import PriceVolumeSnapshot
from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine
from closing_bet_system.engines.overnight_risk_filter import OvernightRiskFilter
from closing_bet_system.engines.signal_score_engine import SignalScoreEngine
from closing_bet_system.main_orchestrator import (
    DEFAULT_GATE_OPERATIONAL_REVIEW,
    EMERGENCY_STOP_SCHEDULE_HOUR,
    EMERGENCY_STOP_SCHEDULE_MINUTE,
    ENTRY_PHASE2_HOUR,
    ENTRY_PHASE2_MINUTE,
    ENTRY_PIPELINE_SCHEDULE_HOUR,
    ENTRY_PIPELINE_SCHEDULE_MINUTE,
    LABEL_SCHEDULE_HOUR,
    LABEL_SCHEDULE_MINUTE,
    MainOrchestrator,
    MORNING_EXIT_HOUR,
    MORNING_EXIT_MINUTE,
    MORNING_FORCE_CLOSE_HOUR,
    MORNING_FORCE_CLOSE_MINUTE,
    PIPELINE_SCHEDULE_HOUR,
    PIPELINE_SCHEDULE_MINUTE,
    SUMMARY_SCHEDULE_HOUR,
    SUMMARY_SCHEDULE_MINUTE,
)
from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot
from closing_bet_system.storage.candidate_logger import CandidateLogger
from closing_bet_system.storage.db import ClosingBetDatabase


# ===== Fixtures =====


def _make_orchestrator() -> tuple[MainOrchestrator, ClosingBetDatabase, Path, MagicMock]:
    """실제 1-1/1-4/1-5b/1-6/1-7 + mock 1-2/1-3/1-5a 로 통합 테스트."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="orch_test_"))
    db_path = tmp_dir / "test.db"
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()

    # 실제 엔진
    cost_eng = CostSlippageEngine(settings={"cost": {
        "buy_commission": 0.00015, "sell_commission": 0.00015,
        "transaction_tax": 0.0018, "estimated_slippage": 0.001,
        "safety_margin": 0.005,
    }})
    # 테스트는 Phase 2 가중치(L1=1.0) 가정 — Phase 1 weighted max=7 + L2+L3=5 라 ALERT 안 나옴.
    # 실 운영의 Phase 1 보수성은 1-4 단위 테스트에서 별도 검증.
    score_eng = SignalScoreEngine(layer1_weight=1.0)
    risk_filter = OvernightRiskFilter()
    cand_logger = CandidateLogger(db=db, cost_engine=cost_eng)

    # mock TelegramReviewBot (실제 발송 차단)
    mock_notifier = MagicMock()
    mock_notifier._enabled = True
    mock_notifier.send_message = MagicMock(return_value=True)
    review_bot = TelegramReviewBot(notifier=mock_notifier)

    # mock collectors (Layer 1+2+DART 입력 준비)
    mock_flow = MagicMock()
    mock_pv = MagicMock()
    mock_dart = MagicMock()

    orch = MainOrchestrator(
        flow_collector=mock_flow,
        price_volume_collector=mock_pv,
        score_engine=score_eng,
        dart_collector=mock_dart,
        risk_filter=risk_filter,
        candidate_logger=cand_logger,
        review_bot=review_bot,
        universe_provider=lambda: ["005930", "000660"],
        market_data_provider=lambda: {
            "us_futures_change_pct": -0.005, "vkospi": 20.0,
            "kospi_change_pct": -0.01, "usd_krw_change_pct": 0.005,
            "kospi_above_200ma": True,
        },
        name_lookup=lambda t: {"005930": "삼성전자", "000660": "SK하이닉스"}.get(t, "(미상)"),
    )
    return orch, db, tmp_dir, mock_notifier


def _cleanup(db, tmp_dir):
    db.close()
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _flow_snap(ticker: str, valid: bool = True) -> IntradayFlowSnapshot:
    return IntradayFlowSnapshot(
        ticker=ticker, snapshot_time=now_kst(),
        is_valid=valid,
        inst_net_buy_estimated=1e9, foreign_net_buy_3d=5e9,
    )


def _pv_snap(ticker: str, *, valid: bool = True, atr_overheat: float = 1.0) -> PriceVolumeSnapshot:
    return PriceVolumeSnapshot(
        ticker=ticker, snapshot_time=now_kst(),
        is_valid=valid,
        close=70_000, high=71_000, low=69_500, open=70_500, volume=5_000_000,
        prev_close=70_000, atr14=1500.0, ma20=70_100, vol_avg20=1_000_000,
        close_strength=0.95, upper_shadow_atr=0.5,
        last_30min_vwap_position=None, closing_buy_sell_ratio=1.5,
        volume_surprise=3.0, atr_overheat=atr_overheat,
        above_ma20=True,
    )


def _dart_snap(ticker: str, *, exclude: bool = False, positive: bool = True) -> DartDisclosureSnapshot:
    return DartDisclosureSnapshot(
        ticker=ticker, snapshot_time=now_kst(),
        is_valid=True,
        positive_matches=(("단일판매공급계약체결", "단일판매공급계약"),) if positive else (),
        exclusion_matches=(("유상증자 결정", "유상증자"),) if exclude else (),
        has_positive_disclosure=positive and not exclude,
        exclude_by_disclosure=exclude,
        exclusion_reason="DART 즉시제외: 유상증자" if exclude else None,
    )


# ===== 테스트 =====


def test_lazy_init_defaults():
    """모든 의존성 None → property 접근 시 default 자동 생성 (실제 인스턴스 화 X 단순 import)."""
    orch = MainOrchestrator()
    # 단순히 attribute 가 lazy property 로 정의되어 있는지 확인
    assert hasattr(orch, "score_engine")
    assert hasattr(orch, "risk_filter")
    # gate_threshold 기본값
    assert orch.gate_threshold == DEFAULT_GATE_OPERATIONAL_REVIEW
    print(f"  [PASS] lazy property + default gate={orch.gate_threshold}")


def test_pipeline_normal_alert():
    """정상 시나리오 — 005930 만점 → ALERT 후보 1건 알림."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"), _flow_snap("000660"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930"), _pv_snap("000660"),
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930", positive=True), _dart_snap("000660", positive=True),
        ])

        result = asyncio.run(orch.run_daily_pipeline())
        # 평일/거래일이면 valid > 0 (테스트 일자가 거래일이 아니면 skipped)
        if result.get("skipped") == "non-trading-day":
            print(f"  [SKIP] 오늘 휴장 — {result}")
            return
        assert result["tickers"] == 2
        assert result["valid"] == 2
        assert result["rejected_by_filter"] == 0
        # ALERT 후보 (Phase 1 weighted=7 → ALERT)
        assert result["alerted"] >= 1   # 점수 만점 케이스
        # Telegram 발송 (헤더 1 + 종목 N)
        assert mock_notifier.send_message.call_count >= 1
        print(f"  [PASS] pipeline 정상 — valid={result['valid']}, alerted={result['alerted']}, "
              f"telegram_calls={mock_notifier.send_message.call_count}")
    finally:
        _cleanup(db, tmp)


def test_pipeline_dart_excluded():
    """DART 즉시제외 종목 → rejected_filter."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930"),
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930", exclude=True),
        ])
        orch._universe_provider = lambda: ["005930"]

        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장 — {result}")
            return
        assert result["valid"] == 1
        assert result["rejected_by_filter"] == 1
        assert result["alerted"] == 0
        # DB 에서 status 확인
        rows = orch.candidate_logger.get_candidates_in_period(
            date.today(), date.today(), status="rejected_filter"
        )
        assert len(rows) == 1
        assert "유상증자" in rows[0]["rejection_reason"]
        print(f"  [PASS] DART 즉시제외 → rejected_filter (DB 확인)")
    finally:
        _cleanup(db, tmp)


def test_pipeline_atr_overheat_excluded():
    """atr_overheat > 1.8 → score EXCLUDED → rejected_filter."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930", atr_overheat=2.0),  # 하드 필터 위반
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930"),
        ])
        orch._universe_provider = lambda: ["005930"]

        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장 — {result}")
            return
        assert result["rejected_by_filter"] == 1
        rows = orch.candidate_logger.get_candidates_in_period(
            date.today(), date.today(), status="rejected_filter"
        )
        assert len(rows) == 1
        assert "atr_overheat" in rows[0]["rejection_reason"]
        print(f"  [PASS] atr_overheat 하드필터 → rejected_filter")
    finally:
        _cleanup(db, tmp)


def test_pipeline_market_skip():
    """시장 매매중단 (V-KOSPI 35) → 모든 종목 rejected_filter."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"), _flow_snap("000660"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930"), _pv_snap("000660"),
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930"), _dart_snap("000660"),
        ])
        orch._market_data_provider = lambda: {
            "us_futures_change_pct": -0.005, "vkospi": 35.0,  # skip
            "kospi_change_pct": -0.01, "usd_krw_change_pct": 0.005,
        }

        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장 — {result}")
            return
        assert result["rejected_by_filter"] == 2
        assert result["alerted"] == 0
        print(f"  [PASS] V-KOSPI 매매중단 → 모든 종목 rejected (2/2)")
    finally:
        _cleanup(db, tmp)


def test_pipeline_invalid_pv_snapshot():
    """Layer 2 결손 (is_valid=False) → 스킵 (DB 미저장)."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930", valid=False),
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930"),
        ])
        orch._universe_provider = lambda: ["005930"]

        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장 — {result}")
            return
        assert result["valid"] == 0
        # DB 에 저장 자체 안됨
        rows = orch.candidate_logger.get_candidates_in_period(
            date.today(), date.today()
        )
        assert len(rows) == 0
        print(f"  [PASS] Layer 2 invalid → 스킵, DB 미저장")
    finally:
        _cleanup(db, tmp)


def test_pipeline_empty_universe():
    """universe 빈 리스트 → 즉시 종료, 무동작."""
    orch, db, tmp, _ = _make_orchestrator()
    try:
        orch._universe_provider = lambda: []
        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped") == "non-trading-day":
            print(f"  [SKIP] 휴장")
            return
        assert result["tickers"] == 0
        print(f"  [PASS] 빈 universe → 무동작")
    finally:
        _cleanup(db, tmp)


def test_pipeline_per_ticker_exception_isolation():
    """한 종목 처리 예외 → 다른 종목 진행, errors 카운트 +1."""
    orch, db, tmp, _ = _make_orchestrator()
    try:
        orch.flow_collector.collect_for_universe = MagicMock(return_value=[
            _flow_snap("005930"), _flow_snap("000660"),
        ])
        orch.price_volume_collector.collect_for_universe = MagicMock(return_value=[
            _pv_snap("005930"), _pv_snap("000660"),
        ])
        orch.dart_collector.collect_for_universe = MagicMock(return_value=[
            _dart_snap("005930"), _dart_snap("000660"),
        ])
        # name_lookup 이 005930 만 예외 발생
        def bad_lookup(t):
            if t == "005930":
                raise RuntimeError("lookup 실패")
            return "SK하이닉스"
        orch._name_lookup = bad_lookup

        result = asyncio.run(orch.run_daily_pipeline())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장")
            return
        # 005930은 _safe_name_lookup 에서 "(미상)" 폴백되므로 처리 정상 → errors 0
        # (즉 _safe_name_lookup 이 예외 격리)
        assert result["errors"] == 0
        assert result["valid"] == 2
        print(f"  [PASS] name_lookup 예외 격리 (_safe_name_lookup)")
    finally:
        _cleanup(db, tmp)


def test_daily_summary():
    """일일 요약 — DB 집계 + 텔레그램 발송."""
    orch, db, tmp, mock_notifier = _make_orchestrator()
    try:
        # 후보 3건 등록 (recommended)
        for i, t in enumerate(["005930", "000660", "035420"]):
            orch.candidate_logger.log_recommended(date.today(), t, f"종목{i}")

        result = asyncio.run(orch.run_daily_summary())
        if result.get("skipped"):
            print(f"  [SKIP] 휴장")
            return
        assert result["sent"] is True
        assert result["today_counts"]["recommended"] == 3
        assert result["cumulative_recommended"] == 3
        # Telegram 발송 1회
        mock_notifier.send_message.assert_called_once()
        print(f"  [PASS] 일일 요약 발송 (recommended=3, 누적=3)")
    finally:
        _cleanup(db, tmp)


def test_label_yesterday_no_provider():
    """label_provider=None → 라벨링 스킵."""
    orch, db, tmp, _ = _make_orchestrator()
    try:
        result = asyncio.run(orch.run_label_yesterday(label_provider=None))
        # 휴장이라도 provider None 분기 우선 검사
        assert result.get("skipped") == "no provider" or result.get("labeled") == 0
        print(f"  [PASS] label_provider=None → 스킵")
    finally:
        _cleanup(db, tmp)


def test_label_yesterday_with_provider():
    """label_provider 주입 → 어제 후보 라벨링."""
    from datetime import timedelta
    orch, db, tmp, _ = _make_orchestrator()
    try:
        yesterday = date.today() - timedelta(days=1)
        # 어제 추천 후보 2건
        cid1 = orch.candidate_logger.log_recommended(yesterday, "005930", "삼성전자")
        cid2 = orch.candidate_logger.log_recommended(yesterday, "000660", "SK하이닉스")

        def mock_label_provider(ticker):
            return {
                "next_open_pct": 0.005, "next_morning_high_pct": 0.012,
                "next_morning_low_pct": -0.003,
                "label_gap_up": True, "label_morning_exit": True,
                "label_stop_risk": False, "label_net_ev_positive": True,
            }
        result = asyncio.run(orch.run_label_yesterday(label_provider=mock_label_provider))
        assert result["labeled"] == 2
        assert result["errors"] == 0
        # DB 검증
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM candidate_labels WHERE candidate_id IN (?, ?)",
                        (cid1, cid2))
            rows = cur.fetchall()
        assert len(rows) == 2
        print(f"  [PASS] 라벨링 2건 저장")
    finally:
        _cleanup(db, tmp)


def test_register_jobs():
    """APScheduler 잡 등록 — 8건 등록 + 신규 3개 매도 잡 트리거 검증 (단위 2-5d 보강)."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    orch = MainOrchestrator()
    sched = AsyncIOScheduler(timezone="Asia/Seoul")
    orch.register_jobs(sched)

    jobs = sched.get_jobs()
    assert len(jobs) == 8, f"잡 8건 등록 기대, got {len(jobs)}: {[j.id for j in jobs]}"
    job_ids = {j.id for j in jobs}
    expected_ids = {
        "closing_bet_daily_pipeline",
        "closing_bet_daily_summary",
        "closing_bet_label_yesterday",
        "closing_bet_flow_reliability",
        "closing_bet_entry_pipeline",
        "closing_bet_emergency_stop",      # 단위 2-5d
        "closing_bet_morning_exit",        # 단위 2-5d
        "closing_bet_morning_force_close", # 단위 2-5d
    }
    assert job_ids == expected_ids, f"잡 ID 불일치 — missing={expected_ids - job_ids}"

    # entry_pipeline 트리거 검증 (단위 2-4d)
    entry_job = sched.get_job("closing_bet_entry_pipeline")
    field_map = {f.name: f for f in entry_job.trigger.fields}
    assert str(field_map["hour"]) == str(ENTRY_PIPELINE_SCHEDULE_HOUR)
    assert str(field_map["minute"]) == str(ENTRY_PIPELINE_SCHEDULE_MINUTE)
    assert "mon-fri" in str(field_map["day_of_week"])
    assert str(entry_job.trigger.timezone) == "Asia/Seoul"
    assert entry_job.misfire_grace_time == 120
    assert entry_job.coalesce is True

    # 단위 2-5d 신규 3개 잡 트리거 검증
    emergency_job = sched.get_job("closing_bet_emergency_stop")
    emergency_fields = {f.name: f for f in emergency_job.trigger.fields}
    assert str(emergency_fields["hour"]) == str(EMERGENCY_STOP_SCHEDULE_HOUR)
    assert str(emergency_fields["minute"]) == str(EMERGENCY_STOP_SCHEDULE_MINUTE)
    assert emergency_job.misfire_grace_time == 60      # P1-5 정책
    assert emergency_job.coalesce is True

    morning_exit_job = sched.get_job("closing_bet_morning_exit")
    morning_fields = {f.name: f for f in morning_exit_job.trigger.fields}
    assert str(morning_fields["hour"]) == str(MORNING_EXIT_HOUR)
    assert str(morning_fields["minute"]) == str(MORNING_EXIT_MINUTE)
    assert morning_exit_job.misfire_grace_time == 300  # P1-5 정책
    assert morning_exit_job.coalesce is True

    force_close_job = sched.get_job("closing_bet_morning_force_close")
    force_fields = {f.name: f for f in force_close_job.trigger.fields}
    assert str(force_fields["hour"]) == str(MORNING_FORCE_CLOSE_HOUR)
    assert str(force_fields["minute"]) == str(MORNING_FORCE_CLOSE_MINUTE)
    assert force_close_job.misfire_grace_time == 120   # P1-5 정책
    assert force_close_job.coalesce is True

    print(f"  [PASS] register_jobs 8건 등록 + entry_pipeline + 매도 3개 트리거 검증")
    try:
        if getattr(sched, "running", False):
            sched.shutdown(wait=False)
    except Exception:
        pass


def test_extract_market_regime():
    """market_data → candidate_features.market_regime 5컬럼 매핑."""
    md = {
        "us_futures_change_pct": -0.005, "vkospi": 20.0,
        "kospi_change_pct": -0.01, "usd_krw_change_pct": 0.005,
        "kospi_above_200ma": True, "foreign_5d_cumulative": 1e10,
    }
    result = MainOrchestrator._extract_market_regime(md)
    assert result["kospi_above_200ma"] is True
    assert result["us_futures_change"] == -0.005
    assert result["vkospi"] == 20.0
    assert result["foreign_5d_cumulative"] == 1e10
    # 비-dict
    assert MainOrchestrator._extract_market_regime("invalid") == {}
    assert MainOrchestrator._extract_market_regime(None) == {}
    print(f"  [PASS] market_regime 5컬럼 매핑 + 비-dict 안전")


def test_safe_call():
    """provider 호출 + 예외 격리."""
    assert MainOrchestrator._safe_call(None, default=[]) == []
    assert MainOrchestrator._safe_call(lambda: ["a"], default=[]) == ["a"]
    assert MainOrchestrator._safe_call(lambda: None, default=[]) == []
    assert MainOrchestrator._safe_call(
        lambda: (_ for _ in ()).throw(RuntimeError("x")), default=["fallback"]
    ) == ["fallback"]
    print(f"  [PASS] _safe_call 4케이스")


def test_index_dart_snaps():
    """list / dict / None 모두 안전 처리."""
    snaps_list = [_dart_snap("005930"), _dart_snap("000660")]
    indexed = MainOrchestrator._index_dart_snaps(snaps_list, ["005930", "000660"])
    assert "005930" in indexed and "000660" in indexed

    snaps_dict = {"005930": _dart_snap("005930")}
    indexed2 = MainOrchestrator._index_dart_snaps(snaps_dict, ["005930"])
    assert indexed2 == snaps_dict

    assert MainOrchestrator._index_dart_snaps(None, []) == {}
    assert MainOrchestrator._index_dart_snaps("invalid", []) == {}
    print(f"  [PASS] _index_dart_snaps list/dict/None/invalid")


def test_schedule_constants():
    """스케줄 시간 상수 — PRD 16-3 / PRD 10-1 일치 (HOUR + MINUTE 둘 다)."""
    assert PIPELINE_SCHEDULE_HOUR == 15
    assert PIPELINE_SCHEDULE_MINUTE == 10
    assert SUMMARY_SCHEDULE_HOUR == 15
    assert SUMMARY_SCHEDULE_MINUTE == 35
    assert LABEL_SCHEDULE_HOUR == 10
    assert LABEL_SCHEDULE_MINUTE == 0
    # 단위 2-4d 신규: ENTRY 상수
    assert ENTRY_PIPELINE_SCHEDULE_HOUR == 15
    assert ENTRY_PIPELINE_SCHEDULE_MINUTE == 18
    assert ENTRY_PHASE2_HOUR == 15
    assert ENTRY_PHASE2_MINUTE == 25
    # 단위 2-5d 신규: EXIT 상수 (P0-3 race 회피 09:01 박제 검증)
    assert EMERGENCY_STOP_SCHEDULE_HOUR == 9
    assert EMERGENCY_STOP_SCHEDULE_MINUTE == 1, \
        f"emergency_stop 09:01 박제 필수 (메인 봇 09:00 race 회피), got {EMERGENCY_STOP_SCHEDULE_MINUTE}"
    assert MORNING_EXIT_HOUR == 9
    assert MORNING_EXIT_MINUTE == 30
    assert MORNING_FORCE_CLOSE_HOUR == 10
    assert MORNING_FORCE_CLOSE_MINUTE == 30
    print(f"  [PASS] 스케줄 상수 16건 검증 (entry 5건 + exit 6건 + 기본 5건)")


def main():
    print("=== Phase 1-8 MainOrchestrator 통합 테스트 ===\n")
    tests = [
        test_lazy_init_defaults,
        test_pipeline_normal_alert,
        test_pipeline_dart_excluded,
        test_pipeline_atr_overheat_excluded,
        test_pipeline_market_skip,
        test_pipeline_invalid_pv_snapshot,
        test_pipeline_empty_universe,
        test_pipeline_per_ticker_exception_isolation,
        test_daily_summary,
        test_label_yesterday_no_provider,
        test_label_yesterday_with_provider,
        test_register_jobs,
        test_extract_market_regime,
        test_safe_call,
        test_index_dart_snaps,
        test_schedule_constants,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
