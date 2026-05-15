"""종가베팅 EntryExecutor — Phase 2 자동매매 진입 모듈 (단위 2-4c).

PRD 9-1 (2분할 진입) / 9-2 (가격 상한) / 9-3 (예상체결가/잔량 비율 보류·취소)
정합 자동매매 진입 로직.

설계 요점
---------
- 옵션 A (mark_entered 1회 호출): phase2 완료 시 phase1+phase2 가중 평균
  체결가 + 누적 금액으로 ``CandidateLogger.mark_entered`` 1회만 호출.
  phase1만 체결되고 phase2 실패한 경우 candidate_status='recommended' 유지 +
  ``entry_phase1_executed_shares > 0`` 으로 50% 보유 상태를 식별.
- 단위 잡 통합 (run_entry_pipeline): main_orchestrator 가 phase1 → 15:25 sleep →
  phase2 를 하나의 async 잡에서 실행. race 위험 없음.
- dry_run 단일 토글: KIS 호출 직전 분기. subclass 패턴 X.
- Phase 0.5 (MarketGuard): CRISIS → 전체 스킵 / DANGER → 사용자 정책상 본
  단위에서는 CAUTION 과 동일하게 비율 축소 (지연 재체크는 단위 2-4 범위 외).
- 체결 확인: fill_checker 5초 폴링 + deadline 5분 cut. 부분 체결도 반영.
- idempotency: 발주 직후 ODNO 즉시 DB 저장 → timeout 시 재시작 후 fill_checker
  재호출로 사후 보강 가능.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from closing_bet_system.collectors.estimated_price_collector import (
    EstimatedPriceCollector,
)
from closing_bet_system.collectors.kis_orderbook_collector import (
    KisOrderbookCollector,
)
from closing_bet_system.collectors.vwap_collector import (
    InsufficientDataError,
    VWAPCollector,
)
from closing_bet_system.execution.fill_checker import FillChecker, FillStatus
from closing_bet_system.execution.price_utils import align_to_tick
from closing_bet_system.infra.fund_guard import FundGuard
from closing_bet_system.notification.entry_notifier import EntryNotifier
from closing_bet_system.storage.candidate_logger import CandidateLogger
from config import now_kst
from logger import logger
from modules.market_guard import MarketGuard, MarketStatus
from modules.trading_engine.kis_order_api import KISOrderApi

if TYPE_CHECKING:
    from datetime import datetime


# ===== Settings =====


@dataclass(frozen=True)
class EntryExecutorSettings:
    """EntryExecutor 동작 파라미터 (frozen — runtime 변경 차단)."""

    enabled: bool = False
    dry_run: bool = True
    score_threshold: int = 2
    position_ratio: float = 0.70           # PRD 기본 자금 한도 × 0.70 (사용자 결정)
    phase1_ratio: float = 0.50              # phase1 50% / phase2 50%
    phase2_enabled: bool = True
    polling_interval_sec: float = 5.0
    fill_check_deadline_sec: float = 300.0  # 5분 컷
    fallback_to_next_candidate: bool = True  # PRD 9-2 정합

    # 가격 상한 (PRD 9-2/9-3 박제, 백테스트 정합)
    vwap_premium: float = 0.005
    estimated_price_premium: float = 0.002
    estimated_price_reject_pct: float = 0.005
    ask_bid_ratio_min: float = 0.8

    # MarketGuard 분기
    market_guard_enabled: bool = True
    caution_ratio_multiplier: float = 0.5   # CAUTION 시 position_ratio × 0.5

    # KIS rate limit 여유
    order_submit_sleep_sec: float = 0.5

    def base_position_ratio(self) -> float:
        """phase 무관 진입 비율 (예: 0.7)."""
        return self.position_ratio


# ===== Result dataclass =====


@dataclass
class CandidateOrder:
    """단일 종목 발주/체결 내역."""

    candidate_id: int
    ticker: str
    name: str
    target_price: int
    quantity: int
    submitted: bool = False
    order_id: Optional[str] = None
    fill_status: Optional[FillStatus] = None
    rejection_reason: Optional[str] = None  # fund_guard / price_cap / submit_fail


@dataclass
class Phase1Result:
    """1차 진입 결과 요약."""

    trade_date: str
    total_candidates: int = 0
    submitted: int = 0
    filled: int = 0
    skipped_price_cap: int = 0
    fund_guard_rejected: int = 0
    market_guard_status: Optional[str] = None
    orders: list[CandidateOrder] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Phase2Result:
    """2차(동시호가) 진입 결과 요약."""

    trade_date: str
    eligible: int = 0
    submitted: int = 0
    filled: int = 0
    skipped_estimated_price: int = 0
    cancelled_ask_bid: int = 0
    orders: list[CandidateOrder] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ===== Main class =====


class EntryExecutor:
    """종가베팅 자동 진입 실행기 (단위 2-4c).

    의존성 주입 패턴: 모든 외부 협력자(KIS API / FundGuard / MarketGuard /
    CandidateLogger / collectors / fill_checker / telegram)를 생성자 인자로 받아
    테스트 시 mock 가능.
    """

    def __init__(
        self,
        kis_order_api: KISOrderApi,
        fund_guard: FundGuard,
        candidate_logger: CandidateLogger,
        vwap_collector: VWAPCollector,
        estimated_price_collector: EstimatedPriceCollector,
        orderbook_collector: KisOrderbookCollector,
        fill_checker: FillChecker,
        market_guard: MarketGuard,
        entry_notifier: EntryNotifier,
        settings: EntryExecutorSettings,
    ):
        self.kis_order_api = kis_order_api
        self.fund_guard = fund_guard
        self.candidate_logger = candidate_logger
        self.vwap_collector = vwap_collector
        self.estimated_price_collector = estimated_price_collector
        self.orderbook_collector = orderbook_collector
        self.fill_checker = fill_checker
        self.market_guard = market_guard
        self.entry_notifier = entry_notifier
        self.settings = settings

    # ===== Public API =====

    async def execute_phase1(self, trade_date: str) -> Phase1Result:
        """15:18 1차(정규장 50%) 진입.

        흐름: enabled 가드 → MarketGuard → 후보 select → 종목 루프
            (가격 상한 → 호가 정렬 → fund_guard → 발주 → ODNO 저장 → 폴링) →
            텔레그램 알림.
        """
        result = Phase1Result(trade_date=trade_date)

        if not self.settings.enabled:
            logger.info("[entry_executor] phase1 스킵 — settings.enabled=False")
            return result

        # Phase 0.5: MarketGuard
        ratio_mult = 1.0
        if self.settings.market_guard_enabled:
            status, info = await asyncio.to_thread(self.market_guard.check)
            result.market_guard_status = status.value
            if status == MarketStatus.CRISIS:
                logger.warning("[entry_executor] phase1 전체 스킵 — MarketGuard CRISIS")
                await asyncio.to_thread(
                    self.entry_notifier.send_market_guard_skip, status.value, info
                )
                return result
            if status in (MarketStatus.CAUTION, MarketStatus.DANGER):
                ratio_mult = self.settings.caution_ratio_multiplier
                logger.warning(
                    f"[entry_executor] phase1 진입 비율 축소 ×{ratio_mult} — "
                    f"MarketGuard {status.value}"
                )

        # Phase 1: 후보 select
        candidates = await asyncio.to_thread(
            self._select_phase1_candidates, trade_date
        )
        result.total_candidates = len(candidates)
        if not candidates:
            logger.info(f"[entry_executor] phase1 대상 후보 0건 (trade_date={trade_date})")
            await asyncio.to_thread(
                self.entry_notifier.send_phase1_result, result, self.settings.dry_run
            )
            return result

        # Phase 2: 후보 루프
        for cand in candidates:
            try:
                order = await self._process_phase1_candidate(cand, ratio_mult)
                result.orders.append(order)
                if order.rejection_reason == "fund_guard":
                    result.fund_guard_rejected += 1
                elif order.rejection_reason == "price_cap":
                    result.skipped_price_cap += 1
                elif order.submitted:
                    result.submitted += 1
                    if order.fill_status and order.fill_status.executed_shares > 0:
                        result.filled += 1
            except Exception as exc:
                msg = f"{cand['ticker']}: {type(exc).__name__}: {exc}"
                logger.error(f"[entry_executor] phase1 후보 처리 실패: {msg}")
                result.errors.append(msg)

        # Phase 4: 알림
        await asyncio.to_thread(
            self.entry_notifier.send_phase1_result, result, self.settings.dry_run
        )
        return result

    async def execute_phase2(self, trade_date: str) -> Optional[Phase2Result]:
        """15:25 2차(동시호가 50%) 진입.

        Step 0 폴백 시 settings.phase2_enabled=False → 즉시 None 반환.
        """
        if not self.settings.enabled:
            logger.info("[entry_executor] phase2 스킵 — settings.enabled=False")
            return None
        if not self.settings.phase2_enabled:
            logger.info("[entry_executor] phase2 비활성 — phase2_enabled=False")
            return None

        result = Phase2Result(trade_date=trade_date)

        # Phase 1: phase1 체결 종목 select
        eligibles = await asyncio.to_thread(
            self._select_phase2_candidates, trade_date
        )
        result.eligible = len(eligibles)
        if not eligibles:
            logger.info(f"[entry_executor] phase2 대상 0건 (trade_date={trade_date})")
            await asyncio.to_thread(
                self.entry_notifier.send_phase2_result, result, self.settings.dry_run
            )
            return result

        # Phase 2: 후보 루프
        for cand in eligibles:
            try:
                order = await self._process_phase2_candidate(cand)
                result.orders.append(order)
                if order.rejection_reason == "estimated_price":
                    result.skipped_estimated_price += 1
                elif order.rejection_reason == "ask_bid":
                    result.cancelled_ask_bid += 1
                elif order.submitted:
                    result.submitted += 1
                    if order.fill_status and order.fill_status.executed_shares > 0:
                        result.filled += 1
            except Exception as exc:
                msg = f"{cand['ticker']}: {type(exc).__name__}: {exc}"
                logger.error(f"[entry_executor] phase2 후보 처리 실패: {msg}")
                result.errors.append(msg)

        # Phase 3: mark_entered 옵션 A — phase1+phase2 가중 평균 1회 호출
        await asyncio.to_thread(self._finalize_mark_entered, trade_date)

        # Phase 4: 알림
        await asyncio.to_thread(
            self.entry_notifier.send_phase2_result, result, self.settings.dry_run
        )
        return result

    # ===== Phase 1 internals =====

    async def _process_phase1_candidate(
        self,
        cand: dict,
        ratio_mult: float,
    ) -> CandidateOrder:
        """단일 종목 phase1 처리."""
        ticker = cand["ticker"]
        name = cand.get("name", "")
        candidate_id = cand["candidate_id"]

        # 1. 분봉 VWAP + 당일 고가 (vwap_collector)
        try:
            vwap_snap = await self.vwap_collector.get_snapshot(ticker)
        except InsufficientDataError:
            vwap_snap = None
            logger.warning(f"[entry_executor] {ticker} VWAP 데이터 부족 — high만 사용")

        if vwap_snap is None or vwap_snap.high <= 0:
            return CandidateOrder(
                candidate_id=candidate_id, ticker=ticker, name=name,
                target_price=0, quantity=0,
                rejection_reason="price_cap",
            )

        # PRD 9-2 가격 상한 = min(vwap × 1.005, 당일 고가)
        candidates_price = [vwap_snap.high]
        if vwap_snap.vwap is not None and vwap_snap.vwap > 0:
            candidates_price.append(
                vwap_snap.vwap * (1.0 + self.settings.vwap_premium)
            )
        max_price = min(candidates_price)
        target_price = align_to_tick(int(max_price), "buy")

        order = CandidateOrder(
            candidate_id=candidate_id, ticker=ticker, name=name,
            target_price=target_price, quantity=0,
        )

        if target_price <= 0:
            order.rejection_reason = "price_cap"
            return order

        # 2. 금액 계산 (phase1 = 50%, MarketGuard 축소 반영)
        amount = self._compute_order_amount(
            ratio=self.settings.base_position_ratio()
            * self.settings.phase1_ratio
            * ratio_mult,
        )
        quantity = max(amount // target_price, 0)
        if quantity <= 0:
            order.rejection_reason = "price_cap"
            return order
        order.quantity = quantity

        # 3. fund_guard
        order_amount_int = int(target_price * quantity + 0.5)
        allowed, reason = await asyncio.to_thread(
            self.fund_guard.allow_order, ticker, order_amount_int
        )
        if not allowed:
            await asyncio.to_thread(
                self.candidate_logger.mark_rejected_by_filter,
                candidate_id, f"fund_guard:{reason}",
            )
            order.rejection_reason = "fund_guard"
            return order

        # 4. dry_run 분기
        if self.settings.dry_run:
            logger.info(
                f"[entry_executor] DRY_RUN phase1: {ticker} {quantity}주 "
                f"@ {target_price:,}원 (총 {order_amount_int:,}원)"
            )
            order.submitted = True
            return order

        # 5. 실 발주
        result = await asyncio.to_thread(
            self.kis_order_api.buy_limit_order, ticker, quantity, target_price
        )
        if not result.get("success") or not result.get("order_id"):
            order.rejection_reason = "submit_fail"
            return order

        order.order_id = result["order_id"]
        order.submitted = True
        # ODNO 즉시 저장 (idempotency)
        await asyncio.to_thread(
            self._save_phase_order_id, candidate_id, 1, order.order_id
        )
        await asyncio.sleep(self.settings.order_submit_sleep_sec)

        # 6. fill_checker 폴링
        fill = await self._poll_fill(order.order_id)
        order.fill_status = fill
        if fill is not None and fill.executed_shares > 0:
            await asyncio.to_thread(
                self._save_phase_fill, candidate_id, 1, fill,
            )
        return order

    def _select_phase1_candidates(self, trade_date: str) -> list[dict]:
        """오늘 recommended + score >= threshold 후보 (DESC by total_score)."""
        threshold = self.settings.score_threshold
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id, ticker, name, total_score
                FROM candidates
                WHERE trade_date=?
                  AND candidate_status='recommended'
                  AND total_score >= ?
                ORDER BY total_score DESC, candidate_id ASC
                """,
                (trade_date, threshold),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ===== Phase 2 internals =====

    async def _process_phase2_candidate(self, cand: dict) -> CandidateOrder:
        """단일 종목 phase2 처리."""
        ticker = cand["ticker"]
        name = cand.get("name", "")
        candidate_id = cand["candidate_id"]
        phase1_price = float(cand.get("entry_phase1_executed_price") or 0.0)
        phase1_shares = int(cand.get("entry_phase1_executed_shares") or 0)
        if phase1_price <= 0 or phase1_shares <= 0:
            # _select_phase2_candidates 가드로 도달 불가지만, 방어적 처리.
            # reason 을 'estimated_price' 로 두면 단위 2-5 디버깅 시 오인식.
            return CandidateOrder(
                candidate_id=candidate_id, ticker=ticker, name=name,
                target_price=0, quantity=0,
                rejection_reason="phase1_not_filled",
            )

        # 1. 예상체결가 + 호가 잔량
        ep_snap = await self.estimated_price_collector.get_estimated_price(ticker)

        # PRD 9-3 보류: 예상체결가 > phase1_avg × (1 + reject_pct)
        if ep_snap and ep_snap.estimated_price is not None and ep_snap.estimated_price > 0:
            threshold = phase1_price * (1.0 + self.settings.estimated_price_reject_pct)
            if ep_snap.estimated_price > threshold:
                logger.info(
                    f"[entry_executor] phase2 보류: {ticker} "
                    f"예상체결가 {ep_snap.estimated_price:,} > {threshold:,.0f}"
                )
                return CandidateOrder(
                    candidate_id=candidate_id, ticker=ticker, name=name,
                    target_price=0, quantity=0,
                    rejection_reason="estimated_price",
                )

        # PRD 9-3 취소: ask_total / bid_total < 0.8
        if ep_snap is not None:
            bid_total = float(ep_snap.bid_total or 0.0)
            ask_total = float(ep_snap.ask_total or 0.0)
            if bid_total > 0 and ask_total > 0:
                ratio = ask_total / bid_total
                if ratio < self.settings.ask_bid_ratio_min:
                    logger.info(
                        f"[entry_executor] phase2 취소: {ticker} ask/bid={ratio:.2f}"
                    )
                    return CandidateOrder(
                        candidate_id=candidate_id, ticker=ticker, name=name,
                        target_price=0, quantity=0,
                        rejection_reason="ask_bid",
                    )

        # 2. 가격 상한 재계산 = min(vwap × 1.005, 당일 고가, 예상체결가 × 1.002)
        try:
            vwap_snap = await self.vwap_collector.get_snapshot(ticker)
        except InsufficientDataError:
            vwap_snap = None

        candidates_price = []
        if vwap_snap and vwap_snap.high > 0:
            candidates_price.append(vwap_snap.high)
        if vwap_snap and vwap_snap.vwap is not None and vwap_snap.vwap > 0:
            candidates_price.append(
                vwap_snap.vwap * (1.0 + self.settings.vwap_premium)
            )
        if ep_snap and ep_snap.estimated_price is not None and ep_snap.estimated_price > 0:
            candidates_price.append(
                ep_snap.estimated_price * (1.0 + self.settings.estimated_price_premium)
            )
        if not candidates_price:
            return CandidateOrder(
                candidate_id=candidate_id, ticker=ticker, name=name,
                target_price=0, quantity=0,
                rejection_reason="estimated_price",
            )
        max_price = min(candidates_price)
        target_price = align_to_tick(int(max_price), "buy")

        order = CandidateOrder(
            candidate_id=candidate_id, ticker=ticker, name=name,
            target_price=target_price, quantity=0,
        )
        if target_price <= 0:
            order.rejection_reason = "estimated_price"
            return order

        # 3. 금액: phase1 과 동일 금액 단위로 (phase2_ratio = 1 - phase1_ratio)
        amount = self._compute_order_amount(
            ratio=self.settings.base_position_ratio()
            * (1.0 - self.settings.phase1_ratio),
        )
        quantity = max(amount // target_price, 0)
        if quantity <= 0:
            order.rejection_reason = "estimated_price"
            return order
        order.quantity = quantity

        # 4. fund_guard (phase2 추가 금액 검사)
        order_amount_int = int(target_price * quantity + 0.5)
        allowed, reason = await asyncio.to_thread(
            self.fund_guard.allow_order, ticker, order_amount_int
        )
        if not allowed:
            await asyncio.to_thread(
                self.candidate_logger.mark_rejected_by_filter,
                candidate_id, f"fund_guard_phase2:{reason}",
            )
            order.rejection_reason = "fund_guard"
            return order

        # 5. dry_run / 실 발주
        if self.settings.dry_run:
            logger.info(
                f"[entry_executor] DRY_RUN phase2: {ticker} {quantity}주 @ {target_price:,}원"
            )
            order.submitted = True
            return order

        result = await asyncio.to_thread(
            self.kis_order_api.buy_limit_order, ticker, quantity, target_price
        )
        if not result.get("success") or not result.get("order_id"):
            order.rejection_reason = "submit_fail"
            return order
        order.order_id = result["order_id"]
        order.submitted = True
        await asyncio.to_thread(
            self._save_phase_order_id, candidate_id, 2, order.order_id
        )
        await asyncio.sleep(self.settings.order_submit_sleep_sec)
        fill = await self._poll_fill(order.order_id)
        order.fill_status = fill
        if fill is not None and fill.executed_shares > 0:
            await asyncio.to_thread(
                self._save_phase_fill, candidate_id, 2, fill,
            )
        return order

    def _select_phase2_candidates(self, trade_date: str) -> list[dict]:
        """phase1 체결된 후보 (entry_phase1_executed_shares > 0 AND phase2 NULL)."""
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id, ticker, name,
                       entry_phase1_order_id,
                       entry_phase1_executed_price,
                       entry_phase1_executed_shares,
                       entry_phase2_order_id,
                       entry_phase2_executed_shares
                FROM candidates
                WHERE trade_date=?
                  AND candidate_status='recommended'
                  AND COALESCE(entry_phase1_executed_shares, 0) > 0
                  AND entry_phase2_order_id IS NULL
                ORDER BY total_score DESC, candidate_id ASC
                """,
                (trade_date,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ===== Shared helpers =====

    def _compute_order_amount(self, *, ratio: float) -> int:
        """주문 금액 = 총자산 × capital_ratio × max_position_per_stock × ratio.

        FundGuard 의 capital_ratio / max_position_per_stock 를 그대로 사용.
        ratio = position_ratio × phase 비율 (× market_guard 축소).
        """
        cfg = self.fund_guard.config
        total_value = self.fund_guard._get_total_value()
        if total_value <= 0:
            return 0
        base = total_value * cfg.capital_ratio * cfg.max_position_per_stock
        return int(base * ratio)

    async def _poll_fill(self, order_id: str) -> Optional[FillStatus]:
        """fill_checker 폴링. deadline 도달 시 마지막 상태 반환."""
        deadline = self.settings.fill_check_deadline_sec
        interval = self.settings.polling_interval_sec
        elapsed = 0.0
        last_status: Optional[FillStatus] = None
        while elapsed < deadline:
            status = await self.fill_checker.get_fill_status(order_id)
            if status is not None:
                last_status = status
                if status.is_filled:
                    return status
            await asyncio.sleep(interval)
            elapsed += interval
        if last_status is not None:
            logger.warning(
                f"[entry_executor] deadline 도달 — {order_id} "
                f"executed={last_status.executed_shares}/{last_status.order_qty}"
            )
        else:
            logger.error(f"[entry_executor] {order_id} fill_status 응답 없음 (deadline)")
        return last_status

    def _save_phase_order_id(
        self, candidate_id: int, phase: int, order_id: str
    ) -> None:
        """ODNO 즉시 저장 (idempotency 확보)."""
        column = "entry_phase1_order_id" if phase == 1 else "entry_phase2_order_id"
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE candidates SET {column}=? WHERE candidate_id=?",
                (order_id, candidate_id),
            )

    def _save_phase_fill(
        self, candidate_id: int, phase: int, fill: FillStatus
    ) -> None:
        """체결가/체결수량 저장 (mark_entered 와 별도 — phase1/phase2 박제)."""
        if phase == 1:
            price_col = "entry_phase1_executed_price"
            shares_col = "entry_phase1_executed_shares"
        else:
            price_col = "entry_phase2_executed_price"
            shares_col = "entry_phase2_executed_shares"
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE candidates SET {price_col}=?, {shares_col}=? WHERE candidate_id=?",
                (float(fill.executed_price), int(fill.executed_shares), candidate_id),
            )

    def _finalize_mark_entered(self, trade_date: str) -> None:
        """옵션 A: phase2 종료 후 phase1+phase2 가중 평균 → mark_entered 1회 호출.

        - phase1 만 체결 (phase2 미체결/취소): 단위 2-5 morning_exit_manager 가
          ``entry_phase1_executed_shares > 0 AND entry_phase2_executed_shares IS NULL``
          쿼리로 매도 식별. 본 함수는 phase2 도 체결된 종목만 mark_entered 호출.
        - 가중 평균: (p1_price × p1_shares + p2_price × p2_shares) / 총수량
        - 누적 amount: target_price × 총수량 근사 — 정확한 평균체결가 × 총수량
        """
        with self.candidate_logger.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id,
                       entry_phase1_executed_price AS p1_price,
                       entry_phase1_executed_shares AS p1_shares,
                       entry_phase2_executed_price AS p2_price,
                       entry_phase2_executed_shares AS p2_shares
                FROM candidates
                WHERE trade_date=?
                  AND candidate_status='recommended'
                  AND COALESCE(entry_phase1_executed_shares, 0) > 0
                  AND COALESCE(entry_phase2_executed_shares, 0) > 0
                """,
                (trade_date,),
            )
            rows = [dict(row) for row in cursor.fetchall()]

        for r in rows:
            try:
                p1_price = float(r["p1_price"] or 0.0)
                p1_shares = int(r["p1_shares"] or 0)
                p2_price = float(r["p2_price"] or 0.0)
                p2_shares = int(r["p2_shares"] or 0)
                total_shares = p1_shares + p2_shares
                if total_shares <= 0 or p1_price <= 0 or p2_price <= 0:
                    continue
                avg_price = (
                    (p1_price * p1_shares + p2_price * p2_shares) / total_shares
                )
                total_amount = avg_price * total_shares
                self.candidate_logger.mark_entered(
                    candidate_id=r["candidate_id"],
                    entry_price=avg_price,
                    entry_amount=total_amount,
                )
            except (ValueError, LookupError, sqlite3.Error) as exc:
                logger.error(
                    f"[entry_executor] mark_entered 실패 cid={r['candidate_id']}: {exc}"
                )
