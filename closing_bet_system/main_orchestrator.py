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
        universe_provider: Optional[Callable[[], list]] = None,
        market_data_provider: Optional[Callable[[], dict]] = None,
        name_lookup: Optional[Callable[[str], str]] = None,
        gate_threshold: int = DEFAULT_GATE_OPERATIONAL_REVIEW,
    ):
        self._flow_collector = flow_collector
        self._price_volume_collector = price_volume_collector
        self._score_engine = score_engine
        self._dart_collector = dart_collector
        self._risk_filter = risk_filter
        self._candidate_logger = candidate_logger
        self._review_bot = review_bot
        self._universe_provider = universe_provider
        self._market_data_provider = market_data_provider
        self._name_lookup = name_lookup
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

        tickers = list(self._safe_call(self._universe_provider, default=[]))
        if not tickers:
            logger.warning("[orchestrator] universe 비어있음 — 파이프라인 스킵")
            return {"date": str(today), "tickers": 0}

        logger.info(f"[orchestrator] 일일 파이프라인 시작 — universe {len(tickers)}건")

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
        gather_results = await asyncio.gather(
            flow_snaps_task, pv_snaps_task, dart_snaps_task,
            return_exceptions=True,
        )
        flow_snaps = gather_results[0] if not isinstance(gather_results[0], Exception) else []
        pv_snaps = gather_results[1] if not isinstance(gather_results[1], Exception) else []
        dart_snaps = gather_results[2] if not isinstance(gather_results[2], Exception) else []
        for label, res in zip(("flow", "price_volume", "dart"), gather_results):
            if isinstance(res, Exception):
                logger.error(f"[orchestrator] {label} collector 예외 (빈 폴백 진행): {res}")

        # 시장 데이터 (Phase 2 HTTP 수집기 도입 시 블로킹 방지 위해 to_thread 로 격리)
        market_data = await asyncio.to_thread(
            self._safe_call, self._market_data_provider, {}
        )

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
                    today, ticker, flow_by_ticker, pv_by_ticker, dart_by_ticker, market_data
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

        sent = await asyncio.to_thread(
            self.review_bot.send_daily_summary,
            today, counts, len(recent_recommended)
        )
        logger.info(
            f"[orchestrator] 일일 요약 발송={sent} — 오늘 {counts}, 누적 {len(recent_recommended)}/{self.gate_threshold}"
        )
        return {
            "date": str(today), "today_counts": counts,
            "cumulative_recommended": len(recent_recommended), "sent": bool(sent),
        }

    async def run_label_yesterday(
        self,
        label_provider: Optional[Callable[[str], dict]] = None,
    ) -> dict:
        """T+1 10:00 사후 라벨링.

        어제(T-1) recommended/entered 후보들에 대해 오늘 09:30 시초/고/저 데이터를
        ``label_provider(ticker)`` 로 가져와 ``candidate_labels`` 에 저장.

        Args:
            label_provider: ``Callable[[ticker], dict]`` — 각 종목의 라벨 dict 반환.
                키: ``next_open_pct``, ``next_morning_high_pct``, ``next_morning_low_pct``,
                ``label_gap_up`` (bool), ``label_morning_exit`` (bool),
                ``label_stop_risk`` (bool), ``label_net_ev_positive`` (bool).
                None 이면 라벨링 스킵 (수집기 미구현 단계).

        Returns:
            ``{"date", "labeled", "errors"}``
        """
        today = now_kst().date()
        yesterday = today - timedelta(days=1)
        if label_provider is None:
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
                label_data = label_provider(ticker)
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

    # ===== 단일 종목 파이프라인 =====

    def _process_ticker(
        self,
        today: date_cls,
        ticker: str,
        flow_by_ticker: dict,
        pv_by_ticker: dict,
        dart_by_ticker: dict,
        market_data: dict,
    ) -> Optional[dict]:
        """1종목 처리 — 점수 → 외부리스크 → DB 저장 → 알림 후보 판단.

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
        # 외부 리스크 (1-5b)
        risk = self.risk_filter.assess(ticker, market_data, dart)

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

    def register_jobs(self, scheduler) -> None:
        """기존 main.py ``scheduler`` 에 종가베팅 잡 3건 등록.

        잡 시간 (PRD 16-3 / Phase 1 단순화):
            - 평일 15:10 → ``run_daily_pipeline``
            - 평일 15:35 → ``run_daily_summary``
            - 평일 10:00 → ``run_label_yesterday`` (T+1 라벨링, label_provider 미설정 시 무동작)

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
        logger.info(
            f"[orchestrator] 종가베팅 잡 3건 등록 — "
            f"pipeline {PIPELINE_SCHEDULE_HOUR:02d}:{PIPELINE_SCHEDULE_MINUTE:02d} / "
            f"summary {SUMMARY_SCHEDULE_HOUR:02d}:{SUMMARY_SCHEDULE_MINUTE:02d} / "
            f"label {LABEL_SCHEDULE_HOUR:02d}:{LABEL_SCHEDULE_MINUTE:02d}"
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
