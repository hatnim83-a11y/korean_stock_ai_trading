"""종가베팅 ExitExecutor — Phase 2 자동매도 모듈 (단위 2-5c).

PRD 10-1 시가 액션 매트릭스 5단계 (trailing_stop은 단위 2-5g 분리):
- emergency_stop  : 시가 ≤ -1% (hard_stop_loss) — 09:01 즉시 전량 시장가
- gap_up_high     : 시가 ≥ +2% — 09:30 50% 시초가 + 잔여 10:30 시장가
- gap_up_low      : +0.5% ≤ 시가 < +2% — 09:30 시초가 100% 시장가
- flat            : -0.5% < 시가 < +0.5% — 09:30 시초가 100% 시장가
- weak_gap_down   : -1% < 시가 ≤ -0.5% — 09:30 시초가 100% 시장가

설계 요점 (사전 리뷰 P0/P1 반영)
- P0-1 시뮬 정합: 모든 액션 시초가 시장가 매도로 통일 → phase25_simulator open_pct 가정 일치
- P0-2 phase1 only `log_exit` LookupError: `mark_entered_phase1_only` 선행 호출 (entry_price 박제)
- P0-3 09:01 emergency_stop 시점: 메인 봇 09:00 monitoring_start_early race 회피 (60초 오프셋)
- P1-2 idempotency: emergency_stop 발주 직후 `exit_in_progress` 플래그 즉시 박제 → 09:30 morning_exit skip
- P1-4 force_close 미체결 취소 순서: cancel_order → 취소 확인 → 시장가 재발주
- P1-5 sell_lock 재사용 + owner 네임스페이스: `"closing_bet:emergency_stop|morning_exit|force_close"`
- P1-3 dry_run 정책 entry_executor 일관성: KIS sell + log_exit 모두 건너뜀 + simulated_exit 로그 + 텔레그램 "[DRY-RUN]"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from closing_bet_system.collectors.morning_price_collector import (
    MorningPriceCollector,
    MorningPriceSnapshot,
)
from closing_bet_system.execution.exit_target_query import (
    ExitTarget,
    select_exit_targets,
)
from closing_bet_system.execution.fill_checker import FillChecker, FillStatus
from closing_bet_system.notification.exit_notifier import ExitNotifier
from closing_bet_system.storage.candidate_logger import CandidateLogger
from config import now_kst
from logger import logger
from modules.trading_engine.kis_order_api import KISOrderApi
from modules.trading_engine.sell_lock import sell_lock as default_sell_lock


# ===== Settings + Enum =====


class ExitAction(Enum):
    """PRD 10-1 매도 액션 5단계 (trailing_stop 단위 2-5g 분리)."""

    EMERGENCY_STOP = "emergency_stop"
    GAP_UP_HIGH = "gap_up_high"
    GAP_UP_LOW = "gap_up_low"
    FLAT = "flat"
    WEAK_GAP_DOWN = "weak_gap_down"


@dataclass(frozen=True)
class ExitExecutorSettings:
    """ExitExecutor 동작 파라미터 (frozen — runtime 변경 차단)."""

    enabled: bool = False
    dry_run: bool = True
    emergency_stop_enabled: bool = True
    use_sell_lock: bool = True

    # PRD 10-1 박제값 (settings.yaml exit:* 정합)
    hard_stop_loss: float = -0.01           # ≤ -1% → emergency_stop
    gap_up_high_threshold: float = 0.02     # ≥ +2% → gap_up_high
    gap_up_low_threshold: float = 0.005     # ≥ +0.5% → gap_up_low
    flat_lower: float = -0.005              # < flat 하단 → weak_gap_down
    gap_up_high_partial_ratio: float = 0.6  # gap_up_high 1차 매도 비율 (50%, 단위 2-5c 50/50)

    # 매도 발주 폴링 + idempotency
    polling_interval_sec: float = 5.0
    fill_check_deadline_sec: float = 60.0   # 매도는 시장가라 빠름, 1분 cut
    cancel_confirm_deadline_sec: float = 30.0
    order_submit_sleep_sec: float = 0.5


# ===== Result dataclass =====


@dataclass
class CandidateExit:
    """단일 종목 매도 발주/체결 내역."""

    candidate_id: int
    ticker: str
    name: str
    action: Optional[ExitAction] = None
    target_shares: int = 0
    submitted: bool = False
    order_id: Optional[str] = None
    fill_status: Optional[FillStatus] = None
    rejection_reason: Optional[str] = None    # sell_lock_busy / price_lookup_fail / submit_fail / phase1_mark_fail
    is_partial: bool = False
    gap_rate: Optional[float] = None


@dataclass
class ExitResult:
    """매도 사이클 결과 요약 (per-cycle: emergency_stop / morning_exit / force_close)."""

    trade_date: str
    cycle: str                           # "emergency_stop" / "morning_exit" / "force_close"
    total_targets: int = 0
    filled: int = 0
    unfilled: int = 0
    cancelled: int = 0                   # force_close 미체결 취소 건수
    orders: list[CandidateExit] = field(default_factory=list)
    action_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ===== Main class =====


def map_action(gap_rate: float, settings: ExitExecutorSettings) -> ExitAction:
    """시가 갭률 → ExitAction 매핑 (PRD 10-1 5단계)."""
    if gap_rate <= settings.hard_stop_loss:
        return ExitAction.EMERGENCY_STOP
    if gap_rate >= settings.gap_up_high_threshold:
        return ExitAction.GAP_UP_HIGH
    if gap_rate >= settings.gap_up_low_threshold:
        return ExitAction.GAP_UP_LOW
    if gap_rate >= settings.flat_lower:
        return ExitAction.FLAT
    return ExitAction.WEAK_GAP_DOWN


class ExitExecutor:
    """종가베팅 자동 매도 실행기 (단위 2-5c).

    의존성 주입 — 모든 외부 협력자 mockable.
    """

    def __init__(
        self,
        kis_order_api: KISOrderApi,
        candidate_logger: CandidateLogger,
        morning_price_collector: MorningPriceCollector,
        fill_checker: FillChecker,
        exit_notifier: ExitNotifier,
        settings: ExitExecutorSettings,
        sell_lock=None,
    ):
        self.kis_order_api = kis_order_api
        self.candidate_logger = candidate_logger
        self.morning_price_collector = morning_price_collector
        self.fill_checker = fill_checker
        self.exit_notifier = exit_notifier
        self.settings = settings
        self.sell_lock = sell_lock if sell_lock is not None else default_sell_lock
        # P1-4 force_close 미체결 취소용 — ticker → (order_id, qty) 메모리 추적.
        # 단위 2-5c 범위 안에서 cancel_order 호출 가능. 단위 2-5g 후속에서 DB 박제 가능.
        self._pending_exit_orders: dict[str, tuple[str, int]] = {}

    # ===== Public API: 3개 잡 =====

    async def execute_emergency_stop(self, trade_date: str) -> ExitResult:
        """09:01 emergency_stop — hard_stop_loss(-1% 이하)만 즉시 전량 시장가.

        흐름: enabled 가드 → 매도 대상 select → 시가 조회 → emergency 필터 →
        sell_lock acquire → phase1 only mark → 발주 → fill 폴링 → log_exit.
        """
        result = ExitResult(trade_date=trade_date, cycle="emergency_stop")
        if not self.settings.enabled or not self.settings.emergency_stop_enabled:
            logger.info("[exit_executor] emergency_stop 스킵 — enabled/emergency_stop_enabled=False")
            return result

        targets = await asyncio.to_thread(
            select_exit_targets, self.candidate_logger, trade_date,
        )
        if not targets:
            logger.info(f"[exit_executor] emergency_stop 대상 0건 (trade_date={trade_date})")
            await asyncio.to_thread(
                self.exit_notifier.send_emergency_stop_result, result, self.settings.dry_run,
            )
            return result

        for target in targets:
            try:
                exit_order = await self._process_emergency_stop(target, trade_date)
                if exit_order is None:
                    continue   # emergency 분기 아님 → 09:30 morning_exit로 위임
                result.orders.append(exit_order)
                result.total_targets += 1
                self._update_action_counts(result, exit_order)
                if exit_order.submitted and exit_order.fill_status and exit_order.fill_status.executed_shares > 0:
                    result.filled += 1
                else:
                    result.unfilled += 1
            except Exception as exc:
                msg = f"{target.ticker}: {type(exc).__name__}: {exc}"
                logger.error(f"[exit_executor] emergency_stop 처리 실패: {msg}")
                result.errors.append(msg)

        self._log_swing_capital_return(result, cycle="emergency_stop")
        await asyncio.to_thread(
            self.exit_notifier.send_emergency_stop_result, result, self.settings.dry_run,
        )
        return result

    async def execute_morning_exit(self, trade_date: str) -> ExitResult:
        """09:30 morning_exit — gap_up_high / gap_up_low / flat / weak_gap_down 4단계.

        emergency_stop 이미 처리된 종목(`exit_time IS NOT NULL` 또는 sell_lock busy)은 제외.
        """
        result = ExitResult(trade_date=trade_date, cycle="morning_exit")
        if not self.settings.enabled:
            logger.info("[exit_executor] morning_exit 스킵 — enabled=False")
            return result

        targets = await asyncio.to_thread(
            select_exit_targets, self.candidate_logger, trade_date,
        )
        if not targets:
            logger.info(f"[exit_executor] morning_exit 대상 0건 (trade_date={trade_date})")
            await asyncio.to_thread(
                self.exit_notifier.send_morning_exit_result, result, self.settings.dry_run,
            )
            return result

        for target in targets:
            try:
                exit_order = await self._process_morning_exit(target, trade_date)
                if exit_order is None:
                    continue
                result.orders.append(exit_order)
                result.total_targets += 1
                self._update_action_counts(result, exit_order)
                if exit_order.submitted and exit_order.fill_status and exit_order.fill_status.executed_shares > 0:
                    result.filled += 1
                else:
                    result.unfilled += 1
            except Exception as exc:
                msg = f"{target.ticker}: {type(exc).__name__}: {exc}"
                logger.error(f"[exit_executor] morning_exit 처리 실패: {msg}")
                result.errors.append(msg)

        self._log_swing_capital_return(result, cycle="morning_exit")
        await asyncio.to_thread(
            self.exit_notifier.send_morning_exit_result, result, self.settings.dry_run,
        )
        return result

    async def execute_force_close(self, trade_date: str) -> ExitResult:
        """10:30 force_close — 미매도 잔량 시장가 전량 매도.

        P1-4 순서: 미체결 주문 취소 → 취소 확인 (cancel_confirm_deadline_sec) → 시장가 재발주.
        """
        result = ExitResult(trade_date=trade_date, cycle="force_close")
        if not self.settings.enabled:
            logger.info("[exit_executor] force_close 스킵 — enabled=False")
            return result

        targets = await asyncio.to_thread(
            select_exit_targets, self.candidate_logger, trade_date,
        )
        if not targets:
            logger.info(f"[exit_executor] force_close 대상 0건 (trade_date={trade_date})")
            await asyncio.to_thread(
                self.exit_notifier.send_force_close_result, result, self.settings.dry_run,
            )
            return result

        for target in targets:
            try:
                exit_order = await self._process_force_close(target, trade_date)
                if exit_order is None:
                    continue
                result.orders.append(exit_order)
                result.total_targets += 1
                if exit_order.fill_status and exit_order.fill_status.executed_shares > 0:
                    result.filled += 1
                else:
                    result.unfilled += 1
            except Exception as exc:
                msg = f"{target.ticker}: {type(exc).__name__}: {exc}"
                logger.error(f"[exit_executor] force_close 처리 실패: {msg}")
                result.errors.append(msg)

        self._log_swing_capital_return(result, cycle="force_close")
        await asyncio.to_thread(
            self.exit_notifier.send_force_close_result, result, self.settings.dry_run,
        )
        return result

    # ===== 스윙 자본 환원 로그 (2026-05-23 동적 자본 분리) =====

    def _log_swing_capital_return(self, result: ExitResult, *, cycle: str) -> None:
        """매도 cycle 종료 시 KIS 예수금 환원 영향 로그.

        실제 환원은 KIS 자동(매도 체결 시 예수금 증가). 본 로그는 운영 추적용.
        다음 영업일 09:25 스윙 매수 시 환원분 자연 인식.
        """
        if result.filled <= 0:
            return
        # 체결된 주문의 매도금액 합산 (executed_price × executed_shares)
        total_returned = 0
        for o in result.orders:
            fs = o.fill_status
            if fs and getattr(fs, "executed_price", 0) and getattr(fs, "executed_shares", 0) > 0:
                total_returned += int(fs.executed_price * fs.executed_shares)
        flag = "(DRY-RUN, 실 환원 없음)" if self.settings.dry_run else "(KIS 예수금 자동 반영)"
        logger.info(
            f"[exit_executor] 스윙 자본 환원 — {cycle} 체결 {result.filled}건 "
            f"매도금액 합산 약 {total_returned:,}원 {flag} "
            f"→ 다음 영업일 09:25 스윙 매수 시 자연 인식 (동적 자본 분리)"
        )

    # ===== 내부 매도 처리 =====

    async def _process_emergency_stop(
        self,
        target: ExitTarget,
        trade_date: str,
    ) -> Optional[CandidateExit]:
        """단일 종목 emergency_stop 처리 — emergency 분기 아니면 None 반환."""
        snap = await self.morning_price_collector.get_snapshot(target.ticker)
        if snap is None:
            return None   # 가격 조회 실패 → 09:30 morning_exit 위임

        entry_price = self._resolve_entry_price(target)
        if entry_price is None or entry_price <= 0:
            return None   # phase1 미체결 → 매도 대상 아님

        gap_rate = (snap.open_price - entry_price) / entry_price
        action = map_action(gap_rate, self.settings)
        if action != ExitAction.EMERGENCY_STOP:
            return None   # emergency 분기 아님 → 09:30 morning_exit 위임

        # sell_lock acquire (owner = "closing_bet:emergency_stop")
        owner = "closing_bet:emergency_stop"
        if self.settings.use_sell_lock:
            acquired = self.sell_lock.acquire(target.ticker, owner)
            if not acquired:
                return CandidateExit(
                    candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                    action=action, gap_rate=gap_rate,
                    rejection_reason="sell_lock_busy",
                )

        return await self._execute_market_sell(
            target=target,
            snap=snap,
            action=action,
            gap_rate=gap_rate,
            quantity=target.total_shares,
            owner=owner,
        )

    async def _process_morning_exit(
        self,
        target: ExitTarget,
        trade_date: str,
    ) -> Optional[CandidateExit]:
        """단일 종목 morning_exit 처리 (4단계: emergency 제외)."""
        snap = await self.morning_price_collector.get_snapshot(target.ticker)
        if snap is None:
            return CandidateExit(
                candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                rejection_reason="price_lookup_fail",
            )

        entry_price = self._resolve_entry_price(target)
        if entry_price is None or entry_price <= 0:
            return CandidateExit(
                candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                rejection_reason="entry_price_missing",
            )

        gap_rate = (snap.open_price - entry_price) / entry_price
        action = map_action(gap_rate, self.settings)
        if action == ExitAction.EMERGENCY_STOP:
            return None   # emergency 는 09:01 잡에서 처리, 09:30은 skip

        # sell_lock — emergency_stop가 이미 잠궜으면 busy
        owner = "closing_bet:morning_exit"
        if self.settings.use_sell_lock:
            acquired = self.sell_lock.acquire(target.ticker, owner)
            if not acquired:
                return CandidateExit(
                    candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                    action=action, gap_rate=gap_rate,
                    rejection_reason="sell_lock_busy",
                )

        # GAP_UP_HIGH 만 50% 분할, 나머지는 전량
        if action == ExitAction.GAP_UP_HIGH:
            partial_qty = max(int(target.total_shares * self.settings.gap_up_high_partial_ratio), 1)
            exit_order = await self._execute_market_sell(
                target=target, snap=snap, action=action, gap_rate=gap_rate,
                quantity=partial_qty, owner=owner,
            )
            exit_order.is_partial = True
            return exit_order

        return await self._execute_market_sell(
            target=target, snap=snap, action=action, gap_rate=gap_rate,
            quantity=target.total_shares, owner=owner,
        )

    async def _process_force_close(
        self,
        target: ExitTarget,
        trade_date: str,
    ) -> Optional[CandidateExit]:
        """단일 종목 force_close 처리 — 미체결 취소 → 시장가 재발주."""
        snap = await self.morning_price_collector.get_snapshot(target.ticker)
        if snap is None:
            return CandidateExit(
                candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                rejection_reason="price_lookup_fail",
            )

        # sell_lock 강제 release (이전 잡 잠금 해제) — force_close는 최후 안전망
        owner = "closing_bet:force_close"
        if self.settings.use_sell_lock:
            self.sell_lock.release(target.ticker)   # 강제 release
            acquired = self.sell_lock.acquire(target.ticker, owner)
            if not acquired:
                # 동시 다른 잡이 acquire 했으면 skip
                return CandidateExit(
                    candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                    rejection_reason="sell_lock_busy",
                )

        # P1-4 미체결 주문 취소 → 취소 확인 → 시장가 재발주 순서
        # 메모리 추적된 매도 ODNO 있으면 cancel_order 호출 (09:30 morning_exit 미체결 잔량 회수)
        pending = self._pending_exit_orders.get(target.ticker)
        cancelled = False
        if pending and not self.settings.dry_run:
            pending_odno, pending_qty = pending
            try:
                cancel_result = await asyncio.to_thread(
                    self.kis_order_api.cancel_order,
                    pending_odno, target.ticker, pending_qty,
                )
                if cancel_result.get("success"):
                    logger.info(
                        f"[exit_executor] force_close 미체결 취소: "
                        f"{target.ticker} odno={pending_odno} qty={pending_qty}"
                    )
                    # 취소 확정 대기 (max cancel_confirm_deadline_sec)
                    await self._wait_cancel_confirm(pending_odno)
                    cancelled = True
            except Exception as exc:
                logger.warning(
                    f"[exit_executor] force_close cancel_order 예외 {target.ticker}: "
                    f"{type(exc).__name__}: {exc}"
                )
            finally:
                self._pending_exit_orders.pop(target.ticker, None)

        # remaining_shares = total_shares (10:30 시점에 아직 exit_time NULL → 미체결)
        entry_price = self._resolve_entry_price(target)
        if entry_price is None or entry_price <= 0:
            return CandidateExit(
                candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
                rejection_reason="entry_price_missing",
            )
        gap_rate = (snap.open_price - entry_price) / entry_price

        exit_order = await self._execute_market_sell(
            target=target, snap=snap, action=ExitAction.FLAT,   # force_close action은 단순 분류
            gap_rate=gap_rate, quantity=target.total_shares, owner=owner,
        )
        if cancelled:
            exit_order.rejection_reason = exit_order.rejection_reason or "force_close_after_cancel"
        return exit_order

    # ===== 매도 발주 + 폴링 + log_exit =====

    async def _execute_market_sell(
        self,
        target: ExitTarget,
        snap: MorningPriceSnapshot,
        action: ExitAction,
        gap_rate: float,
        quantity: int,
        owner: str,
    ) -> CandidateExit:
        """시장가 매도 발주 + 체결 폴링 + log_exit 호출."""
        exit_order = CandidateExit(
            candidate_id=target.candidate_id, ticker=target.ticker, name=target.name,
            action=action, target_shares=quantity, gap_rate=gap_rate,
        )

        # phase1 only 매도 시 mark_entered_phase1_only 선행 호출 (P0-2)
        if target.is_phase1_only:
            try:
                await asyncio.to_thread(
                    self.candidate_logger.mark_entered_phase1_only, target.candidate_id,
                )
            except (LookupError, ValueError) as exc:
                logger.error(
                    f"[exit_executor] phase1 only mark 실패 cid={target.candidate_id}: {exc}"
                )
                exit_order.rejection_reason = "phase1_mark_fail"
                return exit_order

        # dry_run 분기 (P1-3)
        if self.settings.dry_run:
            logger.info(
                f"[exit_executor] DRY_RUN {action.value}: {target.ticker} {quantity}주 "
                f"시가 {snap.open_price:,}원 (gap={gap_rate:+.3%}, owner={owner})"
            )
            exit_order.submitted = True
            return exit_order

        # 실 매도 발주 (시장가)
        sell_result = await asyncio.to_thread(
            self.kis_order_api.sell_market_order, target.ticker, quantity,
        )
        if not sell_result.get("success") or not sell_result.get("order_id"):
            exit_order.rejection_reason = "submit_fail"
            return exit_order

        exit_order.order_id = sell_result["order_id"]
        exit_order.submitted = True
        # P1-4: 매도 ODNO 메모리 추적 — force_close 가 미체결 잔량 cancel_order 호출 시 사용
        self._pending_exit_orders[target.ticker] = (exit_order.order_id, quantity)
        await asyncio.sleep(self.settings.order_submit_sleep_sec)

        # fill_checker 폴링
        fill = await self._poll_fill(exit_order.order_id)
        exit_order.fill_status = fill
        # 전량 체결 시 추적 dict 에서 제거 (force_close 불필요)
        if fill is not None and fill.is_filled:
            self._pending_exit_orders.pop(target.ticker, None)
        if fill is None or fill.executed_shares <= 0:
            return exit_order

        # log_exit 호출 (cost_engine 비용 분해 + UPDATE)
        try:
            await asyncio.to_thread(
                self.candidate_logger.log_exit,
                target.candidate_id,
                float(fill.executed_price),
                None,                      # exit_time = now_kst()
                int(fill.executed_shares),
            )
        except (LookupError, ValueError) as exc:
            logger.error(
                f"[exit_executor] log_exit 실패 cid={target.candidate_id}: {exc}"
            )
            exit_order.rejection_reason = "log_exit_fail"
        return exit_order

    async def _wait_cancel_confirm(self, order_id: str) -> bool:
        """KIS 주문 취소 확정 대기 (P1-4). max cancel_confirm_deadline_sec.

        Returns:
            True = 취소 확정 (raw_status 변경 또는 fill 정지), False = deadline 도달.
        """
        deadline = self.settings.cancel_confirm_deadline_sec
        interval = self.settings.polling_interval_sec
        elapsed = 0.0
        while elapsed < deadline:
            status = await self.fill_checker.get_fill_status(order_id)
            # KIS 취소 응답이 raw_status="취소" 또는 fill 정지로 나타남
            if status is None or (status.executed_shares == 0 and status.remaining_shares == 0):
                return True
            await asyncio.sleep(interval)
            elapsed += interval
        logger.warning(
            f"[exit_executor] _wait_cancel_confirm deadline 도달 order_id={order_id}"
        )
        return False

    async def _poll_fill(self, order_id: str) -> Optional[FillStatus]:
        """fill_checker 폴링 (entry_executor 패턴 재사용)."""
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
        if last_status:
            logger.warning(
                f"[exit_executor] deadline 도달 — {order_id} "
                f"executed={last_status.executed_shares}/{last_status.order_qty}"
            )
        return last_status

    # ===== 헬퍼 =====

    def _resolve_entry_price(self, target: ExitTarget) -> Optional[float]:
        """entry_price 결정 — entered 면 가중평균, phase1 only 면 phase1_executed_price."""
        if target.candidate_status == "entered" and target.entry_price:
            return float(target.entry_price)
        if target.is_phase1_only and target.phase1_executed_price:
            return float(target.phase1_executed_price)
        return None

    @staticmethod
    def _update_action_counts(result: ExitResult, exit_order: CandidateExit) -> None:
        if exit_order.action is None:
            return
        key = exit_order.action.value
        result.action_counts[key] = result.action_counts.get(key, 0) + 1
