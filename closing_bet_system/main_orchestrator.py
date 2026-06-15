"""closing_bet_system.main_orchestrator

PRD 16-3 — Phase 1 알림형 통합 오케스트레이터.

**책임**:
1. 1-2~1-7 모듈을 의존성 주입으로 묶어 일일 파이프라인 실행
2. PRD 16-3 시간표 (Phase 1 알림형 단순화):
   - 14:30 / 15:00 Layer 1+2 수집 (1-2, 1-3)
   - 15:10 외부 리스크 (1-5a, 1-5b)
   - 15:15 점수 산출 + DB 저장 + 알림 (1-4, 1-6, 1-7)
   - 15:35 일일 요약 (1-7)
   - T+1 10:00 사후 라벨링 (1-6 log_labels)
3. APScheduler 잡 등록 헬퍼 (`register_jobs(scheduler)`) — main.py 통합용
4. standalone 단발 실행 (`run_daily_pipeline_now()`) — 모의투자/디버깅 용

**책임 외**:
- 후보 universe 산출 (PRD 5-Layer 3 테마 모멘텀) → ``universe_provider`` 의존성 주입
- 시장 데이터 수집 (미국선물/V-KOSPI/USD-KRW/KOSPI) → ``market_data_provider`` 의존성 주입
- 종목명 조회 → ``name_lookup`` 의존성 주입
- 자동매수 / 동시호가 진입 → Phase 2 ``entry_executor``
- 실제 매도 실행 → Phase 2 ``morning_exit_manager``

**Phase 1 정책 (PRD 17 Phase 1)**:
- "자동매수 절대 금지" — 본 모듈은 후보 등록 + 알림만. 진입 의사결정은 사용자 수동.
- ``rejected_filter`` 자동 (DART 즉시제외 / atr_overheat>1.8 / 시장 매매중단)
- ``entered`` / ``log_exit`` 는 Phase 2 자동화 시점부터.
- ``log_labels`` (T+1 라벨링) 는 Phase 1 포함 — PRD 16-3 시간표 T+1 10:00 잡.

**의존성 주입 (모두 미지정 시 싱글톤 자동 로드)**:
- KisIntradayFlowCollector (1-2)
- KisPriceVolumeCollector (1-3)
- SignalScoreEngine (1-4)
- DartDisclosureCollector (1-5a)
- OvernightRiskFilter (1-5b)
- CandidateLogger (1-6)
- TelegramReviewBot (1-7)

**비동기 패턴**:
KIS API / DART API 모두 동기 httpx 기반이라 ``asyncio.to_thread`` 로 감쌈
(AsyncIOScheduler 이벤트 루프 블로킹 방지).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst, is_trading_day


# ===== 시간표 상수 (PRD 16-3, Phase 1 알림형 단순화) =====

# Phase 1 은 14:30/15:00 Layer 수집을 한 번에 묶어 15:10 직전 1회 실행 (수집/스코어링 일관성)
# Phase 2 부터 분리 (15:00 1차, 15:20 2차 → 신뢰도 추적용)
PIPELINE_SCHEDULE_HOUR = 15
PIPELINE_SCHEDULE_MINUTE = 10

# 일일 요약 (15:35)
SUMMARY_SCHEDULE_HOUR = 15
SUMMARY_SCHEDULE_MINUTE = 35

# T+1 사후 라벨링 (10:00 — KRX 09:30 시초/고/저 확정 후)
LABEL_SCHEDULE_HOUR = 10
LABEL_SCHEDULE_MINUTE = 0

# 단위 2-3 (Phase 2 옵션 H, 2026-05-11) — flow 신뢰도 매칭 (19:27 — 네이버 18~19시 갱신 후)
FLOW_RELIABILITY_SCHEDULE_HOUR = 19
FLOW_RELIABILITY_SCHEDULE_MINUTE = 27

# 단위 2-4d (2026-05-15) — EntryExecutor 진입 파이프라인 (15:18 phase1 → 15:25 phase2)
# 단일 async 잡: phase1 → sleep_until 15:25 → phase2. race 봉쇄 + run_daily_pipeline(15:10) 종료 후 시작.
ENTRY_PIPELINE_SCHEDULE_HOUR = 15
ENTRY_PIPELINE_SCHEDULE_MINUTE = 18
ENTRY_PHASE2_HOUR = 15
ENTRY_PHASE2_MINUTE = 25

# 단위 2-5d (2026-05-16) — ExitExecutor 자동매도 3개 잡
# 09:01 emergency_stop (메인 봇 09:00 monitoring_start_early + midweek_sell_profit race 회피)
# 09:02 morning_exit (2026-05-26 09:30→09:02: 스윙 09:05 매수 전 매도 자금 환원)
# 10:30 force_close (미체결 잔량 시장가 전량 + 09:02 미체결 취소)
EMERGENCY_STOP_SCHEDULE_HOUR = 9
EMERGENCY_STOP_SCHEDULE_MINUTE = 1
MORNING_EXIT_HOUR = 9
MORNING_EXIT_MINUTE = 2  # 2026-05-26: 09:30 → 09:02. EARLY_BUY_ENABLED=True 시 스윙 매수가 09:05라
                         # 종가베팅 매도 자금이 스윙 매수에 활용되려면 09:05 직전 매도 필요.
                         # KIS ord_psbl_cash는 매도 체결 즉시 가산되므로 자금 흐름 정합.
MORNING_FORCE_CLOSE_HOUR = 10
MORNING_FORCE_CLOSE_MINUTE = 30

# 1-9 운영 점검 게이트 (settings.yaml gate.operational_review 와 동기화)
DEFAULT_GATE_OPERATIONAL_REVIEW = 30


# ===== 메인 클래스 =====


class MainOrchestrator:
    """종가베팅 Phase 1 통합 파이프라인.

    Args:
        flow_collector / price_volume_collector / score_engine / dart_collector /
        risk_filter / candidate_logger / review_bot:
            1-2~1-7 인스턴스. None 이면 각 모듈 default 자동 생성.
        universe_provider: ``Callable[[], list[str]]`` — 평가 대상 ticker 리스트.
            None 이면 빈 리스트 → 파이프라인 무동작.
        market_data_provider: ``Callable[[], dict]`` — 시장 데이터 dict.
            None 이면 ``{}`` (1-5b Phase 1 결손 정책으로 룰 비활성).
        name_lookup: ``Callable[[str], str]`` — ticker → 종목명. None 이면 "(미상)".
        gate_threshold: 1-9 운영 점검 게이트 건수 (default 30).
    """

    def __init__(
        self,
        *,
        flow_collector: Optional[Any] = None,
        price_volume_collector: Optional[Any] = None,
        score_engine: Optional[Any] = None,
        dart_collector: Optional[Any] = None,
        risk_filter: Optional[Any] = None,
        candidate_logger: Optional[Any] = None,
        review_bot: Optional[Any] = None,
        orderbook_collector: Optional[Any] = None,
        kind_collector: Optional[Any] = None,
        universe_provider: Optional[Callable[[], list]] = None,
        market_data_provider: Optional[Callable[[], dict]] = None,
        name_lookup: Optional[Callable[[str], str]] = None,
        label_provider: Optional[Callable[[str], dict]] = None,
        gate_threshold: int = DEFAULT_GATE_OPERATIONAL_REVIEW,
    ):
        self._flow_collector = flow_collector
        self._price_volume_collector = price_volume_collector
        self._score_engine = score_engine
        self._dart_collector = dart_collector
        self._risk_filter = risk_filter
        self._candidate_logger = candidate_logger
        self._review_bot = review_bot
        self._orderbook_collector = orderbook_collector
        self._kind_collector = kind_collector
        self._entry_executor = None    # 단위 2-4d: lazy 로딩 (settings.yaml entry_executor 섹션)
        self._exit_executor = None     # 단위 2-5d: lazy 로딩 (settings.yaml morning_exit 섹션)
        self._universe_provider = universe_provider
        self._market_data_provider = market_data_provider
        self._name_lookup = name_lookup
        self._label_provider = label_provider
        self.gate_threshold = int(gate_threshold)

    # ===== 의존성 지연 로딩 =====

    @property
    def flow_collector(self):
        if self._flow_collector is None:
            from closing_bet_system.collectors.kis_intraday_flow_collector import (
                KisIntradayFlowCollector,
            )
            self._flow_collector = KisIntradayFlowCollector()
        return self._flow_collector

    @property
    def price_volume_collector(self):
        if self._price_volume_collector is None:
            from closing_bet_system.collectors.kis_price_volume_collector import (
                KisPriceVolumeCollector,
            )
            self._price_volume_collector = KisPriceVolumeCollector()
        return self._price_volume_collector

    @property
    def score_engine(self):
        if self._score_engine is None:
            from closing_bet_system.engines.signal_score_engine import SignalScoreEngine
            from closing_bet_system.storage.db import _load_settings
            settings = _load_settings()
            self._score_engine = SignalScoreEngine.from_settings(
                settings.get("score", {})
            )
        return self._score_engine

    @property
    def dart_collector(self):
        if self._dart_collector is None:
            from closing_bet_system.collectors.dart_disclosure_collector import (
                DartDisclosureCollector,
            )
            self._dart_collector = DartDisclosureCollector()
        return self._dart_collector

    @property
    def risk_filter(self):
        if self._risk_filter is None:
            from closing_bet_system.engines.overnight_risk_filter import OvernightRiskFilter
            from closing_bet_system.storage.db import _load_settings
            settings = _load_settings()
            self._risk_filter = OvernightRiskFilter.from_settings(
                settings.get("external_risk", {})
            )
        return self._risk_filter

    @property
    def candidate_logger(self):
        if self._candidate_logger is None:
            from closing_bet_system.storage.candidate_logger import CandidateLogger
            self._candidate_logger = CandidateLogger()
        return self._candidate_logger

    @property
    def review_bot(self):
        if self._review_bot is None:
            from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot
            self._review_bot = TelegramReviewBot()
        return self._review_bot

    @property
    def orderbook_collector(self):
        if self._orderbook_collector is None:
            from closing_bet_system.collectors.kis_orderbook_collector import (
                KisOrderbookCollector,
            )
            self._orderbook_collector = KisOrderbookCollector()
        return self._orderbook_collector

    @property
    def entry_executor(self):
        """단위 2-4c EntryExecutor 지연 로딩.

        settings.yaml `entry_executor` 섹션을 EntryExecutorSettings 로 매핑.
        Phase 2 자동매매 진입 활성화 시 (`enabled=True`) `run_entry_pipeline` 에서 사용.
        의존성: KISOrderApi / FundGuard / CandidateLogger / VWAPCollector /
        EstimatedPriceCollector / KisOrderbookCollector / FillChecker / MarketGuard /
        EntryNotifier — 전부 lazy import + 본 orchestrator 의 기존 collectors 재사용.
        """
        if getattr(self, "_entry_executor", None) is None:
            from closing_bet_system.collectors.estimated_price_collector import (
                EstimatedPriceCollector,
            )
            from closing_bet_system.collectors.vwap_collector import VWAPCollector
            from closing_bet_system.execution.entry_executor import (
                EntryExecutor,
                EntryExecutorSettings,
            )
            from closing_bet_system.execution.fill_checker import FillChecker
            from closing_bet_system.infra.fund_guard import FundGuard
            from closing_bet_system.notification.entry_notifier import EntryNotifier
            from closing_bet_system.storage.db import _load_settings
            from modules.market_guard import MarketGuard
            from modules.stock_screener.kis_api import KISApi
            from modules.trading_engine.kis_order_api import KISOrderApi

            yaml_settings = _load_settings().get("entry_executor", {})
            # 2026-05-29: score_threshold_crisis 신규 키 로드 (CAUTION/DANGER 시 적용)
            #   default = score_threshold 값 (무변화 폴백, 미설정 시 NORMAL과 동일하게 동작)
            _score_threshold = int(yaml_settings.get("score_threshold", 2))
            _score_threshold_crisis = int(
                yaml_settings.get("score_threshold_crisis", _score_threshold)
            )
            ee_settings = EntryExecutorSettings(
                enabled=bool(yaml_settings.get("enabled", False)),
                dry_run=bool(yaml_settings.get("dry_run", True)),
                score_threshold=_score_threshold,
                score_threshold_crisis=_score_threshold_crisis,
                position_ratio=float(yaml_settings.get("position_ratio", 0.7)),
                phase1_ratio=float(yaml_settings.get("phase1_ratio", 0.5)),
                phase2_enabled=bool(yaml_settings.get("phase2_enabled", True)),
                polling_interval_sec=float(yaml_settings.get("polling_interval_sec", 5)),
                fill_check_deadline_sec=float(
                    yaml_settings.get("fill_check_deadline_sec", 300)
                ),
                fallback_to_next_candidate=bool(
                    yaml_settings.get("fallback_to_next_candidate", True)
                ),
                market_guard_enabled=bool(
                    yaml_settings.get("market_guard_enabled", True)
                ),
            )

            kis_order_api = KISOrderApi()
            kis_api = KISApi()
            self._entry_executor = EntryExecutor(
                kis_order_api=kis_order_api,
                fund_guard=FundGuard(),
                candidate_logger=self.candidate_logger,
                vwap_collector=VWAPCollector(kis_api),
                estimated_price_collector=EstimatedPriceCollector(kis_order_api),
                orderbook_collector=self.orderbook_collector,
                fill_checker=FillChecker(kis_order_api),
                market_guard=MarketGuard(),
                entry_notifier=EntryNotifier(),
                settings=ee_settings,
            )
        return self._entry_executor

    @property
    def exit_executor(self):
        """단위 2-5c ExitExecutor 지연 로딩.

        settings.yaml `morning_exit` 섹션을 ExitExecutorSettings 로 매핑.
        Phase 2 자동매도 활성화 시(`enabled=True`) 3개 잡(09:01 / 09:30 / 10:30)에서 사용.
        의존성: KISOrderApi / CandidateLogger / MorningPriceCollector / FillChecker /
        ExitNotifier — 단위 2-4 EntryExecutor 와 동일 KISOrderApi/CandidateLogger 인스턴스 공유.
        """
        if self._exit_executor is None:
            from closing_bet_system.collectors.morning_price_collector import (
                MorningPriceCollector,
            )
            from closing_bet_system.execution.exit_executor import (
                ExitExecutor,
                ExitExecutorSettings,
            )
            from closing_bet_system.execution.fill_checker import FillChecker
            from closing_bet_system.notification.exit_notifier import ExitNotifier
            from closing_bet_system.storage.db import _load_settings
            from modules.stock_screener.kis_api import KISApi
            from modules.trading_engine.kis_order_api import KISOrderApi

            yaml_settings = _load_settings().get("morning_exit", {})
            exit_cfg = _load_settings().get("exit", {})
            ee_settings = ExitExecutorSettings(
                enabled=bool(yaml_settings.get("enabled", False)),
                dry_run=bool(yaml_settings.get("dry_run", True)),
                emergency_stop_enabled=bool(
                    yaml_settings.get("emergency_stop_enabled", True)
                ),
                use_sell_lock=bool(yaml_settings.get("use_sell_lock", True)),
                hard_stop_loss=float(exit_cfg.get("hard_stop_loss", -0.01)),
                gap_up_high_threshold=float(exit_cfg.get("gap_up_high_threshold", 0.02)),
                gap_up_low_threshold=float(exit_cfg.get("gap_up_low_threshold", 0.005)),
                flat_lower=float(exit_cfg.get("flat_lower", -0.005)),
                gap_up_high_partial_ratio=float(
                    exit_cfg.get("gap_up_high_partial_ratio", 0.6)
                ),
                polling_interval_sec=float(
                    yaml_settings.get("polling_interval_sec", 5.0)
                ),
                fill_check_deadline_sec=float(
                    yaml_settings.get("fill_check_deadline_sec", 60.0)
                ),
                cancel_confirm_deadline_sec=float(
                    yaml_settings.get("cancel_confirm_deadline_sec", 30.0)
                ),
                open_limit_sell_enabled=bool(
                    yaml_settings.get("open_limit_sell_enabled", False)
                ),
                limit_fill_deadline_sec=float(
                    yaml_settings.get("limit_fill_deadline_sec", 30.0)
                ),
            )

            kis_order_api = KISOrderApi()
            kis_api = KISApi()
            self._exit_executor = ExitExecutor(
                kis_order_api=kis_order_api,
                candidate_logger=self.candidate_logger,
                morning_price_collector=MorningPriceCollector(kis_api),
                fill_checker=FillChecker(kis_order_api),
                exit_notifier=ExitNotifier(candidate_logger=self.candidate_logger),
                settings=ee_settings,
            )
        return self._exit_executor

    @property
    def kind_collector(self):
        if self._kind_collector is None:
            from closing_bet_system.collectors.kind_alert_collector import KindAlertCollector
            # 단위 2-2b (2026-05-04): KindNaverProvider 주입 — 네이버 금융 시장경보 5 페이지 fetch.
            # provider 호출 실패 시 KindAlertCollector 가 빈 dict + is_valid=False 폴백.
            try:
                from closing_bet_system.collectors.kind_naver_provider import fetch_kind_alerts
                self._kind_collector = KindAlertCollector(provider=fetch_kind_alerts)
            except Exception as e:
                logger.warning(
                    f"[orchestrator] KindNaverProvider import 실패 — provider 미주입 폴백: {e}"
                )
                self._kind_collector = KindAlertCollector()
        return self._kind_collector

    # ===== 메인 파이프라인 =====

    async def run_daily_pipeline(self) -> dict:
        """일일 후보 수집 + 스코어링 + DB + 알림 (15:10 트리거).

        Phase 1 단순화: Layer 1+2 수집 + 외부 리스크 + 점수 + DB + 배치 알림 한 번에.

        Returns:
            ``{"date", "tickers", "valid", "rejected_by_filter", "alerted", "errors"}`` 통계
        """
        today = now_kst().date()
        if not is_trading_day(today):
            logger.info(f"[orchestrator] {today} 휴장일 — 파이프라인 스킵")
            return {"date": str(today), "skipped": "non-trading-day"}

        # 단위 2-2b: KIND 시장경보 우선 수집 (universe 전) — severity ≥ 3 사전 제외용.
        # Phase 2-2 인터페이스 + KindNaverProvider 주입 시 활성. 실패는 graceful (severity_map={}).
        kind_snapshot = None
        kind_severity_map: dict[str, int] = {}
        try:
            kind_snapshot = await asyncio.to_thread(self.kind_collector.collect)
            if kind_snapshot is not None and getattr(kind_snapshot, "severity_map", None):
                kind_severity_map = dict(kind_snapshot.severity_map)
        except Exception as e:
            logger.error(f"[orchestrator] kind_collector 예외 (빈 폴백 진행): {e}")

        # universe_provider 호출 — severity_map 인자를 지원하면 전달, 미지원 시 무인자 호출 폴백.
        # `_safe_call` 시그니처 보존 위해 lambda 로 wrap.
        # TypeError 폴백 발동 시 KIND 필터가 무력화되므로 warning 로그로 명시적 알림 (운영 관찰성).
        def _call_universe_provider() -> list:
            if self._universe_provider is None:
                return []
            try:
                return self._universe_provider(severity_map=kind_severity_map)
            except TypeError as e:
                # severity_map 인자 미지원 (v1 호환) — 무인자 호출 + KIND 무력화 경고
                logger.warning(
                    f"[orchestrator] universe_provider severity_map 미지원 — "
                    f"KIND 사전 제외 비활성 (v1 호환 폴백): {e}"
                )
                return self._universe_provider()

        tickers = list(self._safe_call(_call_universe_provider, default=[]))
        if not tickers:
            logger.warning("[orchestrator] universe 비어있음 — 파이프라인 스킵")
            return {"date": str(today), "tickers": 0}

        logger.info(
            f"[orchestrator] 일일 파이프라인 시작 — universe {len(tickers)}건"
            + (f" (KIND 사전 제외 candidates {len(kind_severity_map)}종목)" if kind_severity_map else "")
        )

        # ===== 1. 데이터 수집 (병렬 to_thread + return_exceptions) =====
        # KIS rate_limit 으로 인해 collector 내부에서 순차 처리 → 외부에서는 단순 to_thread.
        # **return_exceptions=True**: 한 collector 실패가 전체 파이프라인 중단시키지 않도록 격리.
        # 실패한 collector 는 빈 리스트로 폴백 → 부분 데이터로 진행 (per-ticker 단위로 자연 스킵).
        flow_snaps_task = asyncio.to_thread(
            self.flow_collector.collect_for_universe, tickers
        )
        pv_snaps_task = asyncio.to_thread(
            self.price_volume_collector.collect_for_universe, tickers
        )
        # DART 는 별도 API (httpx) 라 병렬 가능
        dart_snaps_task = asyncio.to_thread(
            self.dart_collector.collect_for_universe, tickers
        )
        # Phase 2-1: 호가 1단계 스냅샷 (KIS inquire_asking_price)
        orderbook_snaps_task = asyncio.to_thread(
            self.orderbook_collector.collect_for_universe, tickers
        )
        gather_results = await asyncio.gather(
            flow_snaps_task, pv_snaps_task, dart_snaps_task, orderbook_snaps_task,
            return_exceptions=True,
        )
        flow_snaps = gather_results[0] if not isinstance(gather_results[0], Exception) else []
        pv_snaps = gather_results[1] if not isinstance(gather_results[1], Exception) else []
        dart_snaps = gather_results[2] if not isinstance(gather_results[2], Exception) else []
        orderbook_snaps = gather_results[3] if not isinstance(gather_results[3], Exception) else []
        for label, res in zip(("flow", "price_volume", "dart", "orderbook"), gather_results):
            if isinstance(res, Exception):
                logger.error(f"[orchestrator] {label} collector 예외 (빈 폴백 진행): {res}")

        # Phase 2-1: 호가 스냅샷 DB INSERT (signal/risk 점수에는 미반영, Phase 2-3 활용)
        if orderbook_snaps:
            try:
                from closing_bet_system.collectors.kis_orderbook_collector import (
                    insert_snapshots as _insert_orderbook,
                )
                inserted = await asyncio.to_thread(_insert_orderbook, orderbook_snaps)
                logger.info(
                    f"[orchestrator] orderbook 스냅샷 DB 저장 — {inserted}/{len(orderbook_snaps)}건"
                )
            except Exception as e:
                logger.error(f"[orchestrator] orderbook DB 저장 예외 (파이프라인 계속): {e}")

        # 시장 데이터 (Phase 2 HTTP 수집기 도입 시 블로킹 방지 위해 to_thread 로 격리)
        market_data = await asyncio.to_thread(
            self._safe_call, self._market_data_provider, {}
        )

        # 단위 2-2b: KIND 시장경보 스냅샷 — 이미 universe_provider 호출 전에 수집됨.
        # 종목별 OvernightRiskFilter 단계에서 그대로 활용 (kind_snapshot 변수 재사용).

        # ===== 2. 종목별 스코어링 + DB + 알림 후보 수집 =====
        # snapshot 인덱싱
        flow_by_ticker = {s.ticker: s for s in flow_snaps}
        pv_by_ticker = {s.ticker: s for s in pv_snaps}
        dart_by_ticker = self._index_dart_snaps(dart_snaps, tickers)

        stats = {
            "date": str(today), "tickers": len(tickers),
            "valid": 0, "rejected_by_filter": 0, "alerted": 0, "errors": 0,
        }
        candidates_for_alert: list[dict] = []

        for ticker in tickers:
            try:
                processed = self._process_ticker(
                    today, ticker, flow_by_ticker, pv_by_ticker, dart_by_ticker, market_data,
                    kind_snapshot=kind_snapshot,
                )
                if processed is None:
                    continue
                stats["valid"] += 1
                if processed.get("rejected"):
                    stats["rejected_by_filter"] += 1
                if processed.get("alert_payload"):
                    candidates_for_alert.append(processed["alert_payload"])
                    stats["alerted"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"[orchestrator] {ticker} 처리 예외: {e}", exc_info=True)

        # ===== 3. 배치 알림 발송 (Phase 1: 일방향 push) =====
        if candidates_for_alert:
            sent = await asyncio.to_thread(
                self.review_bot.send_batch_alert, candidates_for_alert
            )
            stats["alert_sent"] = sent
        else:
            stats["alert_sent"] = 0

        logger.info(
            f"[orchestrator] 파이프라인 완료 — {stats['valid']}/{stats['tickers']} 유효, "
            f"제외 {stats['rejected_by_filter']}건, 알림 {stats['alerted']}건 "
            f"(발송 {stats['alert_sent']}건), 예외 {stats['errors']}건"
        )
        return stats

    async def run_daily_summary(self) -> dict:
        """15:35 일일 요약 (DB 집계 + 텔레그램 발송)."""
        today = now_kst().date()
        if not is_trading_day(today):
            return {"date": str(today), "skipped": "non-trading-day"}

        # 30일 누적 recommended (게이트 진척도)
        end = today
        start = end - timedelta(days=30)
        counts = await asyncio.to_thread(
            self.candidate_logger.count_by_status, today, today
        )
        recent_recommended = await asyncio.to_thread(
            self.candidate_logger.get_candidates_in_period,
            start, end, "recommended"
        )

        # 오늘 청산된 포지션 (T-1 진입 → T 청산) PnL 조회
        closed_today = await asyncio.to_thread(
            self.candidate_logger.get_closed_today, today
        )

        sent = await asyncio.to_thread(
            self.review_bot.send_daily_summary,
            today, counts, len(recent_recommended), closed_today
        )
        logger.info(
            f"[orchestrator] 일일 요약 발송={sent} — 오늘 {counts}, 청산 {len(closed_today)}건, "
            f"누적 {len(recent_recommended)}/{self.gate_threshold}"
        )
        return {
            "date": str(today), "today_counts": counts,
            "cumulative_recommended": len(recent_recommended), "sent": bool(sent),
        }

    async def run_label_yesterday(
        self,
        label_provider: Optional[Callable[[str], dict]] = None,
        for_date: Optional[date_cls] = None,
    ) -> dict:
        """T+1 10:00 사후 라벨링.

        어제(T-1) recommended/entered 후보들에 대해 오늘 09:30 시초/고/저 데이터를
        ``label_provider(ticker)`` 로 가져와 ``candidate_labels`` 에 저장.

        명시 인자가 없으면 ``__init__`` 의 ``label_provider`` 폴백 사용 — APScheduler 가
        인자 없이 호출하므로 인스턴스 의존성으로 자동 주입됨.

        Args:
            label_provider: ``Callable[[ticker], dict]`` — 각 종목의 라벨 dict 반환.
                키: ``next_open_pct``, ``next_morning_high_pct``, ``next_morning_low_pct``,
                ``label_gap_up`` (bool), ``label_morning_exit`` (bool),
                ``label_stop_risk`` (bool), ``label_net_ev_positive`` (bool).
                None 이면 라벨링 스킵 (수집기 미구현 단계).
            for_date: 특정 날짜 후보를 라벨링 (수동 백필용). None 이면 직전 영업일 자동 산출.

        Returns:
            ``{"date", "labeled", "errors"}``
        """
        today = now_kst().date()
        if for_date is not None:
            yesterday = for_date
        else:
            # 직전 영업일 (월요일 → 금요일, 공휴일 건너뛰기)
            yesterday = today - timedelta(days=1)
            while not is_trading_day(yesterday):
                yesterday = yesterday - timedelta(days=1)
        # 인스턴스 폴백 (APScheduler 가 인자 없이 호출하는 경로)
        provider = label_provider if label_provider is not None else self._label_provider
        if provider is None:
            logger.info("[orchestrator] label_provider 미설정 — 라벨링 스킵")
            return {"date": str(yesterday), "labeled": 0, "errors": 0, "skipped": "no provider"}

        # 어제 recommended (entered 포함) 후보 조회
        rows = await asyncio.to_thread(
            self.candidate_logger.get_candidates_in_period,
            yesterday, yesterday
        )
        labeled = 0
        errors = 0
        for row in rows:
            ticker = row.get("ticker")
            cid = row.get("candidate_id")
            try:
                # provider 내부 KIS 재시도(time.sleep)가 이벤트루프를 블로킹하지 않도록 to_thread 위임
                label_data = await asyncio.to_thread(provider, ticker)
                if not label_data:
                    continue
                await asyncio.to_thread(
                    self.candidate_logger.log_labels,
                    cid,
                    label_data.get("next_open_pct"),
                    label_data.get("next_morning_high_pct"),
                    label_data.get("next_morning_low_pct"),
                    label_data.get("label_gap_up"),
                    label_data.get("label_morning_exit"),
                    label_data.get("label_stop_risk"),
                    label_data.get("label_net_ev_positive"),
                )
                labeled += 1
            except Exception as e:
                errors += 1
                logger.error(f"[orchestrator] {ticker} 라벨링 예외: {e}")

        logger.info(
            f"[orchestrator] 라벨링 완료 — {labeled}/{len(rows)} 저장, 예외 {errors}"
        )
        return {"date": str(yesterday), "labeled": labeled, "errors": errors}

    # ===== 단위 2-3 (Phase 2 옵션 H) — flow 신뢰도 매칭 잡 =====

    async def run_flow_reliability_check(
        self,
        for_date: Optional[date_cls] = None,
    ) -> dict:
        """매일 19:27 — KIS 추정치 vs 네이버 확정값 방향 일치율 매칭.

        흐름:
            1. 직전 영업일(또는 ``for_date``) 산출
            2. 해당 일자 candidates 조회 → ticker + candidate_features.{inst,foreign}_net_buy_estimated
            3. 네이버 frgn.naver 크롤링 (services.flow_reliability_tracker.fetch_naver_confirmed_flow)
            4. 각 ticker 별 ``candidate_logger.log_flow_reliability`` 호출 (UNIQUE 충돌 시 REPLACE)

        Args:
            for_date: 특정 영업일 강제 지정 (수동 백필용). None 이면 직전 영업일 자동 산출.

        Returns:
            ``{"date", "n_candidates", "n_estimated", "n_confirmed", "n_logged", "errors"}``

            카운터 의미:
                - ``n_candidates``: 해당 일자 candidates 총 건수
                - ``n_estimated``: inst/foreign estimated 중 1개라도 보유한 candidate 수
                  (네이버 호출 대상 ticker 수와 동일)
                - ``n_confirmed``: 네이버 확정값 수집 성공 ticker 수
                - ``n_logged``: flow_data_reliability INSERT 성공 행 수 (= n_candidates,
                  estimated 없는 candidate도 NULL,NULL 로 저장)
                - ``errors``: log_flow_reliability 호출 예외 발생 건수

        주의:
            네이버 frgn.naver 갱신 시점 = T일 18~19시 KST. 19:27 잡은 그 이후 호출이라
            확정값 반영. 단 네이버 갱신 지연 시 ``n_confirmed=0`` 으로 graceful (로그만).

            **foreign_direction_match 는 본 단위에서 항상 NULL** — estimated 가
            ``foreign_net_buy_3d`` (3일 누적, 원) vs confirmed 가 당일 1일 (주) 로 기간
            불일치. 후속 단위에서 candidate_features 컬럼 또는 confirmed 합산 방식 재설계.
        """
        from closing_bet_system.services.flow_reliability_tracker import (
            fetch_naver_confirmed_flow,
        )

        today = now_kst().date()
        if for_date is not None:
            target_date = for_date
        else:
            # 직전 영업일 (run_label_yesterday 와 동일 패턴)
            target_date = today - timedelta(days=1)
            while not is_trading_day(target_date):
                target_date = target_date - timedelta(days=1)

        # 1) 후보 + features 조회 (raw SQL — candidate_features 에 ticker 없음, candidates JOIN)
        rows = await asyncio.to_thread(
            self._fetch_candidates_with_features, target_date
        )
        n_candidates = len(rows)
        if n_candidates == 0:
            logger.info(
                f"[orchestrator] flow_reliability — {target_date} 후보 0건, 스킵"
            )
            return {
                "date": str(target_date),
                "n_candidates": 0, "n_estimated": 0, "n_confirmed": 0,
                "n_logged": 0, "errors": 0,
            }

        # 2) estimated 보유 ticker 리스트
        tickers_with_estimate = [
            r["ticker"] for r in rows
            if r.get("inst_net_buy_estimated") is not None
            or r.get("foreign_net_buy_3d") is not None
        ]
        n_estimated = len(tickers_with_estimate)

        # 3) 네이버 확정값 수집 (이벤트 루프 블로킹 방지 — to_thread)
        confirmed_map = await asyncio.to_thread(
            fetch_naver_confirmed_flow, target_date, tickers_with_estimate
        )
        n_confirmed = len(confirmed_map)

        # 4) candidate 별 매칭 INSERT
        # estimated 없는 candidate (features 누락 또는 모두 None)도 NULL,NULL로 저장 →
        # 향후 IS NULL 쿼리로 재처리 대상 식별 용이.
        logged = 0
        errors = 0
        for r in rows:
            ticker = r["ticker"]
            inst_est = r.get("inst_net_buy_estimated")
            # foreign_net_buy_3d (3일 누적, 원) vs 네이버 frgn_qty (당일, 주) — 기간 불일치.
            # 부호 비교 무의미하므로 foreign_est 강제 None → foreign_direction_match=NULL.
            # 후속 단위 (예: foreign_net_buy_today 신설 또는 네이버 3일 합산) 에서 재설계.
            foreign_est = None
            confirmed = confirmed_map.get(ticker, {})
            inst_conf = confirmed.get("inst_confirmed_qty")
            foreign_conf = confirmed.get("foreign_confirmed_qty")

            try:
                await asyncio.to_thread(
                    self.candidate_logger.log_flow_reliability,
                    target_date, ticker,
                    inst_est, inst_conf,
                    foreign_est, foreign_conf,
                )
                logged += 1
            except Exception as e:
                errors += 1
                logger.error(f"[orchestrator] {ticker} flow_reliability 저장 실패: {e}")

        logger.info(
            f"[orchestrator] flow_reliability {target_date} — "
            f"후보 {n_candidates} / 추정치 보유 {n_estimated} / 확정값 수집 {n_confirmed} / "
            f"저장 {logged} / 예외 {errors}"
        )
        return {
            "date": str(target_date),
            "n_candidates": n_candidates,
            "n_estimated": n_estimated,
            "n_confirmed": n_confirmed,
            "n_logged": logged,
            "errors": errors,
        }

    def _fetch_candidates_with_features(self, target_date: date_cls) -> list[dict]:
        """단위 2-3 헬퍼 — target_date candidates + candidate_features JOIN.

        candidate_features 에 ticker 컬럼 없음 → candidates 와 candidate_id JOIN 필수.
        """
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT c.candidate_id, c.ticker,
                       cf.inst_net_buy_estimated, cf.foreign_net_buy_3d
                FROM candidates c
                LEFT JOIN candidate_features cf ON cf.candidate_id = c.candidate_id
                WHERE c.trade_date = ?
                ORDER BY c.candidate_id
                """,
                (target_date.isoformat(),),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ===== 단일 종목 파이프라인 =====

    def _process_ticker(
        self,
        today: date_cls,
        ticker: str,
        flow_by_ticker: dict,
        pv_by_ticker: dict,
        dart_by_ticker: dict,
        market_data: dict,
        *,
        kind_snapshot: Optional[Any] = None,
    ) -> Optional[dict]:
        """1종목 처리 — 점수 → 외부리스크 → DB 저장 → 알림 후보 판단.

        Args:
            kind_snapshot: Phase 2-2 ``KindAlertSnapshot`` (None 허용 — KIND 평가 스킵)

        Returns:
            None: 데이터 결손으로 스킵
            dict: ``{"candidate_id", "decision", "rejected", "alert_payload"}``
        """
        flow = flow_by_ticker.get(ticker)
        pv = pv_by_ticker.get(ticker)
        dart = dart_by_ticker.get(ticker)

        # Layer 2 가 핵심 — 미수집 / 무효 시 스킵
        if pv is None or not getattr(pv, "is_valid", False):
            logger.debug(f"[orchestrator] {ticker} Layer 2 결손 — 스킵")
            return None

        # Layer dict 구성
        layer1 = flow.to_layer1_features() if (flow and getattr(flow, "is_valid", False)) else {}
        layer2 = pv.to_layer2_features()
        # PRD 6-1 Layer 2 점수 4조건 중 "20일선 위" 는 to_layer2_features() 외부 키
        layer2["above_ma20"] = getattr(pv, "above_ma20", None)
        # Layer 3: 1-5a DART has_positive_disclosure 만 가용. 52주/테마 rank 는 Phase 2 collector.
        layer3 = dart.to_layer3_input() if dart else {}

        # 점수 산출
        score_bd = self.score_engine.score(ticker, layer1, layer2, layer3)
        # 외부 리스크 (1-5b + Phase 2-2 KIND)
        risk = self.risk_filter.assess(ticker, market_data, dart, kind_alerts=kind_snapshot)

        # DB 저장 (recommended INSERT)
        name = self._safe_name_lookup(ticker)
        cid = self.candidate_logger.log_recommended(today, ticker, name, score_bd)
        # candidate_features INSERT
        market_regime_features = self._extract_market_regime(market_data)
        self.candidate_logger.log_features(
            cid, layer1=layer1, layer2=layer2, layer3=layer3,
            market_regime=market_regime_features,
        )

        # rejected_filter 결정 (score EXCLUDED OR risk can_enter=False)
        rejected = False
        if score_bd.decision == "EXCLUDED":
            self.candidate_logger.mark_rejected_by_filter(
                cid, score_bd.exclusion_reason or "score EXCLUDED"
            )
            rejected = True
        elif not risk.can_enter:
            self.candidate_logger.mark_rejected_by_filter(
                cid, risk.decision_reason or "risk skip/exclude"
            )
            rejected = True

        # 알림 후보: ALERT 이상이고 rejected 가 아니면
        alert_payload = None
        if not rejected and score_bd.decision in ("ALERT", "ENTRY", "MAX_SIZE_ENTRY"):
            alert_payload = {
                "ticker": ticker, "name": name,
                "score_breakdown": score_bd,
                "dart_snapshot": dart,
                "risk_assessment": risk,
            }

        return {
            "candidate_id": cid, "decision": score_bd.decision,
            "rejected": rejected, "alert_payload": alert_payload,
        }

    # ===== 헬퍼 =====

    @staticmethod
    def _safe_call(fn: Optional[Callable], default: Any) -> Any:
        """provider 호출 + 예외 격리."""
        if fn is None:
            return default
        try:
            result = fn()
            return result if result is not None else default
        except Exception as e:
            logger.error(f"[orchestrator] provider 호출 예외: {e}")
            return default

    def _safe_name_lookup(self, ticker: str) -> str:
        if self._name_lookup is None:
            return "(미상)"
        try:
            name = self._name_lookup(ticker)
            return name if isinstance(name, str) and name.strip() else "(미상)"
        except Exception:
            return "(미상)"

    @staticmethod
    def _index_dart_snaps(dart_snaps: Any, tickers: list) -> dict:
        """1-5a collect_for_universe 가 list 또는 dict 둘 다 가능 → ticker→snap 매핑."""
        if isinstance(dart_snaps, dict):
            return dart_snaps
        if isinstance(dart_snaps, list):
            return {getattr(s, "ticker", None): s for s in dart_snaps if s is not None}
        return {}

    @staticmethod
    def _extract_market_regime(market_data: dict) -> dict:
        """market_data → candidate_features.market_regime 5컬럼 매핑."""
        if not isinstance(market_data, dict):
            return {}
        return {
            "kospi_above_200ma": market_data.get("kospi_above_200ma"),
            "vkospi": market_data.get("vkospi"),
            "foreign_5d_cumulative": market_data.get("foreign_5d_cumulative"),
            "us_futures_change": market_data.get("us_futures_change_pct"),
            "usd_krw_change": market_data.get("usd_krw_change_pct"),
        }

    # ===== APScheduler 통합 =====

    async def run_entry_pipeline(self) -> dict:
        """단위 2-4d 진입 파이프라인 — 15:18 phase1 → sleep until 15:25 → phase2.

        단일 async 잡으로 phase1/phase2 를 묶어 race 위험 봉쇄. settings.yaml
        `entry_executor.enabled=false` 시 즉시 무동작 (kill switch).

        Returns:
            ``{"phase1": Phase1Result-asdict, "phase2": Phase2Result-asdict-or-None,
               "trade_date": str, "skipped": bool}``
        """
        if not self.entry_executor.settings.enabled:
            logger.info("[orchestrator] run_entry_pipeline 스킵 — enabled=False")
            return {"skipped": True, "reason": "enabled=False"}

        trade_date = now_kst().date().isoformat()
        logger.info(f"[orchestrator] run_entry_pipeline 시작 trade_date={trade_date}")

        # Phase 1 (15:18)
        phase1_result = await self.entry_executor.execute_phase1(trade_date)

        # 15:25 까지 sleep (cron 이 15:18 fire 후 자연 대기)
        await self._sleep_until_kst(ENTRY_PHASE2_HOUR, ENTRY_PHASE2_MINUTE)

        # Phase 2 (15:25)
        phase2_result = await self.entry_executor.execute_phase2(trade_date)

        # 통합 요약 알림
        try:
            await asyncio.to_thread(
                self.entry_executor.entry_notifier.send_pipeline_summary,
                phase1_result, phase2_result,
                self.entry_executor.settings.dry_run,
            )
        except Exception as exc:
            logger.error(f"[orchestrator] send_pipeline_summary 실패: {exc}")

        return {
            "skipped": False,
            "trade_date": trade_date,
            "phase1": {
                "submitted": phase1_result.submitted,
                "filled": phase1_result.filled,
                "skipped_price_cap": phase1_result.skipped_price_cap,
                "fund_guard_rejected": phase1_result.fund_guard_rejected,
                "market_guard_status": phase1_result.market_guard_status,
            },
            "phase2": (
                {
                    "submitted": phase2_result.submitted,
                    "filled": phase2_result.filled,
                    "skipped_estimated_price": phase2_result.skipped_estimated_price,
                    "cancelled_ask_bid": phase2_result.cancelled_ask_bid,
                }
                if phase2_result is not None
                else None
            ),
        }

    async def _sleep_until_kst(self, hour: int, minute: int) -> None:
        """현재 KST 시각 → 목표 시각까지 sleep. 이미 지났으면 즉시 반환."""
        now = now_kst()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (target - now).total_seconds()
        if delta <= 0:
            logger.info(
                f"[orchestrator] _sleep_until_kst({hour:02d}:{minute:02d}) — "
                f"이미 경과 (delta={delta:.1f}s), 즉시 진행"
            )
            return
        logger.info(
            f"[orchestrator] _sleep_until_kst({hour:02d}:{minute:02d}) — "
            f"{delta:.1f}초 대기"
        )
        await asyncio.sleep(delta)

    async def run_emergency_stop_check(self) -> dict:
        """단위 2-5d — 09:01 emergency_stop 잡 (hard_stop_loss ≤ -1% 즉시 손절).

        메인 봇 09:00 monitoring_start_early + midweek_sell_profit 종료 후 60초 오프셋.
        settings.yaml `morning_exit.enabled=false` 또는 `emergency_stop_enabled=false` 시 즉시 무동작.
        """
        if not self.exit_executor.settings.enabled:
            logger.info("[orchestrator] run_emergency_stop_check 스킵 — enabled=False")
            return {"skipped": True, "reason": "enabled=False"}
        if not self.exit_executor.settings.emergency_stop_enabled:
            logger.info("[orchestrator] run_emergency_stop_check 스킵 — emergency_stop_enabled=False")
            return {"skipped": True, "reason": "emergency_stop_enabled=False"}

        # T-1 = 어제 진입한 후보 (오늘 매도). 휴장 보정은 단위 2-5g 후속.
        trade_date = (now_kst().date() - timedelta(days=1)).isoformat()
        logger.info(f"[orchestrator] run_emergency_stop_check 시작 trade_date={trade_date}")
        result = await self.exit_executor.execute_emergency_stop(trade_date)
        return {
            "skipped": False, "trade_date": trade_date, "cycle": "emergency_stop",
            "total_targets": result.total_targets, "filled": result.filled,
            "unfilled": result.unfilled,
        }

    async def run_morning_exit(self) -> dict:
        """단위 2-5d — 09:30 morning_exit 잡 (PRD 10-1 4단계 매도 액션).

        emergency_stop 처리된 종목(exit_time NOT NULL)은 자동 제외.
        """
        if not self.exit_executor.settings.enabled:
            logger.info("[orchestrator] run_morning_exit 스킵 — enabled=False")
            return {"skipped": True, "reason": "enabled=False"}

        trade_date = (now_kst().date() - timedelta(days=1)).isoformat()
        logger.info(f"[orchestrator] run_morning_exit 시작 trade_date={trade_date}")
        result = await self.exit_executor.execute_morning_exit(trade_date)
        return {
            "skipped": False, "trade_date": trade_date, "cycle": "morning_exit",
            "total_targets": result.total_targets, "filled": result.filled,
            "unfilled": result.unfilled, "action_counts": result.action_counts,
        }

    async def run_morning_force_close(self) -> dict:
        """단위 2-5d — 10:30 morning_force_close 잡 (잔량 시장가 전량).

        P1-4 순서: 09:30 미체결 cancel_order → 취소 확인 → 시장가 재발주.
        ExitExecutor 의 _pending_exit_orders 메모리 dict 활용.
        """
        if not self.exit_executor.settings.enabled:
            logger.info("[orchestrator] run_morning_force_close 스킵 — enabled=False")
            return {"skipped": True, "reason": "enabled=False"}

        trade_date = (now_kst().date() - timedelta(days=1)).isoformat()
        logger.info(f"[orchestrator] run_morning_force_close 시작 trade_date={trade_date}")
        result = await self.exit_executor.execute_force_close(trade_date)
        return {
            "skipped": False, "trade_date": trade_date, "cycle": "force_close",
            "total_targets": result.total_targets, "filled": result.filled,
            "unfilled": result.unfilled, "cancelled": result.cancelled,
        }

    def register_jobs(self, scheduler) -> None:
        """기존 main.py ``scheduler`` 에 종가베팅 잡 4건 등록.

        잡 시간 (PRD 16-3 / Phase 1 + 단위 2-3 옵션 H):
            - 평일 15:10 → ``run_daily_pipeline``
            - 평일 15:35 → ``run_daily_summary``
            - 평일 10:00 → ``run_label_yesterday`` (T+1 라벨링, label_provider 미설정 시 무동작)
            - 평일 19:27 → ``run_flow_reliability_check`` (네이버 18~19시 갱신 후 매칭, 단위 2-3)

        Args:
            scheduler: ``apscheduler.schedulers.asyncio.AsyncIOScheduler`` 인스턴스.
        """
        from apscheduler.triggers.cron import CronTrigger
        kst = "Asia/Seoul"
        scheduler.add_job(
            self.run_daily_pipeline,
            CronTrigger(
                hour=PIPELINE_SCHEDULE_HOUR, minute=PIPELINE_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_daily_pipeline",
            replace_existing=True,
        )
        scheduler.add_job(
            self.run_daily_summary,
            CronTrigger(
                hour=SUMMARY_SCHEDULE_HOUR, minute=SUMMARY_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_daily_summary",
            replace_existing=True,
        )
        scheduler.add_job(
            self.run_label_yesterday,
            CronTrigger(
                hour=LABEL_SCHEDULE_HOUR, minute=LABEL_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_label_yesterday",
            replace_existing=True,
        )
        scheduler.add_job(
            self.run_flow_reliability_check,
            CronTrigger(
                hour=FLOW_RELIABILITY_SCHEDULE_HOUR,
                minute=FLOW_RELIABILITY_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_flow_reliability",
            replace_existing=True,
        )
        # 단위 2-4d: EntryExecutor 진입 파이프라인 (15:18 phase1 → 15:25 phase2)
        # settings.yaml entry_executor.enabled=false 면 잡은 등록되지만 함수 진입 시 즉시 return.
        scheduler.add_job(
            self.run_entry_pipeline,
            CronTrigger(
                hour=ENTRY_PIPELINE_SCHEDULE_HOUR,
                minute=ENTRY_PIPELINE_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_entry_pipeline",
            replace_existing=True,
            misfire_grace_time=120,   # 15:18 미스 시 최대 2분 늦게 발화
            coalesce=True,
        )
        # 단위 2-5d: ExitExecutor 자동매도 잡 3건 (09:01 emergency / 09:30 morning / 10:30 force_close)
        # settings.yaml morning_exit.enabled=false 면 함수 진입 시 즉시 return.
        scheduler.add_job(
            self.run_emergency_stop_check,
            CronTrigger(
                hour=EMERGENCY_STOP_SCHEDULE_HOUR,
                minute=EMERGENCY_STOP_SCHEDULE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_emergency_stop",
            replace_existing=True,
            misfire_grace_time=60,   # 1분 grace (60초 이후엔 09:30 morning_exit 위임)
            coalesce=True,
        )
        scheduler.add_job(
            self.run_morning_exit,
            CronTrigger(
                hour=MORNING_EXIT_HOUR, minute=MORNING_EXIT_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_morning_exit",
            replace_existing=True,
            misfire_grace_time=300,   # 5분 grace, 10:30 force_close 가 안전망
            coalesce=True,
        )
        scheduler.add_job(
            self.run_morning_force_close,
            CronTrigger(
                hour=MORNING_FORCE_CLOSE_HOUR, minute=MORNING_FORCE_CLOSE_MINUTE,
                day_of_week="mon-fri", timezone=kst,
            ),
            id="closing_bet_morning_force_close",
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True,
        )
        logger.info(
            f"[orchestrator] 종가베팅 잡 8건 등록 — "
            f"pipeline {PIPELINE_SCHEDULE_HOUR:02d}:{PIPELINE_SCHEDULE_MINUTE:02d} / "
            f"summary {SUMMARY_SCHEDULE_HOUR:02d}:{SUMMARY_SCHEDULE_MINUTE:02d} / "
            f"label {LABEL_SCHEDULE_HOUR:02d}:{LABEL_SCHEDULE_MINUTE:02d} / "
            f"flow_reliability {FLOW_RELIABILITY_SCHEDULE_HOUR:02d}:"
            f"{FLOW_RELIABILITY_SCHEDULE_MINUTE:02d} / "
            f"entry_pipeline {ENTRY_PIPELINE_SCHEDULE_HOUR:02d}:"
            f"{ENTRY_PIPELINE_SCHEDULE_MINUTE:02d} / "
            f"emergency_stop {EMERGENCY_STOP_SCHEDULE_HOUR:02d}:"
            f"{EMERGENCY_STOP_SCHEDULE_MINUTE:02d} / "
            f"morning_exit {MORNING_EXIT_HOUR:02d}:{MORNING_EXIT_MINUTE:02d} / "
            f"morning_force_close {MORNING_FORCE_CLOSE_HOUR:02d}:{MORNING_FORCE_CLOSE_MINUTE:02d}"
        )


# ===== standalone 단발 실행 (디버깅 / 모의투자) =====


async def run_daily_pipeline_now(orchestrator: Optional[MainOrchestrator] = None) -> dict:
    """orchestrator 없이 단발 실행 — 의존성 모두 default."""
    orch = orchestrator or MainOrchestrator()
    return await orch.run_daily_pipeline()


def main():
    """CLI 진입점 — ``python -m closing_bet_system.main_orchestrator --pipeline-now``."""
    import argparse
    parser = argparse.ArgumentParser(description="종가베팅 통합 오케스트레이터")
    parser.add_argument("--pipeline-now", action="store_true",
                        help="일일 파이프라인 즉시 실행 (universe_provider 없으면 무동작)")
    parser.add_argument("--summary-now", action="store_true",
                        help="일일 요약 즉시 발송")
    args = parser.parse_args()

    orch = MainOrchestrator()
    if args.pipeline_now:
        result = asyncio.run(orch.run_daily_pipeline())
        print(f"[OK] 파이프라인 결과: {result}")
    elif args.summary_now:
        result = asyncio.run(orch.run_daily_summary())
        print(f"[OK] 요약 결과: {result}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
