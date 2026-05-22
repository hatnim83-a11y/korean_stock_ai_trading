"""종가베팅 ExitExecutor 매도 결과 텔레그램 알림 (단위 2-5c → 5/22 통일).

emergency_stop / morning_exit / force_close 3종 결과 메시지 포맷 + 발송.
스윙 send_sell_alert 형식 일관성 — 종목별 매수가→매도가/수익금/수익률 표시.

설계 원칙 (entry_notifier 패턴 일관성)
- TelegramReviewBot 의존성 주입 (테스트 시 mock)
- CandidateLogger 의존성 주입 (원가 조회용, None 가능 — 폴백 메시지)
- 봇 비활성 시 graceful (False 반환, 예외 없음)
- 결과 dataclass(ExitResult) 의 ``orders`` 리스트 활용
- dry_run=True 시 제목 prefix `🧪 [DRY-RUN]`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from closing_bet_system.notification.telegram_review_bot import (
    TelegramReviewBot,
    _escape_markdown,
)
from config import now_kst
from logger import logger

if TYPE_CHECKING:
    from closing_bet_system.execution.exit_executor import CandidateExit, ExitResult
    from closing_bet_system.storage.candidate_logger import CandidateLogger


_SEPARATOR = "━" * 22

_CYCLE_LABELS = {
    "emergency_stop": "09:01 EMERGENCY STOP",
    "morning_exit": "09:30 MORNING EXIT",
    "force_close": "10:30 FORCE CLOSE",
}


class ExitNotifier:
    """ExitExecutor 결과 알림 발송기 (스윙 메시지 형식 통일)."""

    def __init__(
        self,
        telegram_bot: Optional[TelegramReviewBot] = None,
        candidate_logger: Optional["CandidateLogger"] = None,
    ):
        self._bot = telegram_bot or TelegramReviewBot()
        self._candidate_logger = candidate_logger

    @property
    def is_enabled(self) -> bool:
        return self._bot.is_enabled

    # ===== Public API =====

    def send_emergency_stop_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """09:01 emergency_stop 결과."""
        return self._send_result(result, cycle="emergency_stop", dry_run=dry_run)

    def send_morning_exit_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """09:30 morning_exit 결과."""
        return self._send_result(result, cycle="morning_exit", dry_run=dry_run)

    def send_force_close_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """10:30 force_close 결과."""
        return self._send_result(result, cycle="force_close", dry_run=dry_run)

    # ===== Internal dispatch =====

    def _send_result(
        self, result: "ExitResult", *, cycle: str, dry_run: bool
    ) -> bool:
        if not self.is_enabled:
            logger.debug(f"[exit_notifier] 봇 비활성 — {cycle} 알림 스킵")
            return False
        label = _CYCLE_LABELS.get(cycle, cycle.upper())
        sent_any = False
        per_ticker_pnls: list[tuple[float, float]] = []  # (profit_abs, profit_rate)

        # 종목별 개별 메시지
        for order in result.orders:
            if not order.submitted and not order.rejection_reason:
                continue
            text, pnl = self._format_per_ticker(
                order,
                trade_date=result.trade_date,
                cycle_label=label,
                dry_run=dry_run,
            )
            ok = bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))
            sent_any = sent_any or ok
            if pnl is not None:
                per_ticker_pnls.append(pnl)

        # 요약 메시지
        summary = self._format_summary(
            result,
            cycle_label=label,
            dry_run=dry_run,
            per_ticker_pnls=per_ticker_pnls,
        )
        ok = bool(self._bot.notifier.send_message(summary, parse_mode="Markdown"))
        return sent_any or ok

    # ===== 종목별 포맷 =====

    def _format_per_ticker(
        self,
        order: "CandidateExit",
        *,
        trade_date: str,
        cycle_label: str,
        dry_run: bool,
    ) -> tuple[str, Optional[tuple[float, float]]]:
        """1종목 매도 메시지 + (profit_abs, profit_rate) 튜플 반환."""
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실 매도"
        ts = now_kst().strftime("%H:%M:%S")
        name_safe = _escape_markdown(order.name)
        ticker_safe = _escape_markdown(order.ticker)
        action_str = (
            order.action.value if order.action and hasattr(order.action, "value")
            else (str(order.action) if order.action else "—")
        )
        action_safe = _escape_markdown(action_str)

        # 거부 케이스
        if order.rejection_reason:
            header = f"⚠️ *종가베팅 매도 거부* — {cycle_label} ({flag})"
            body_lines = [
                f"📉 {name_safe} ({ticker_safe})",
                f"📝 거부 사유: {_escape_markdown(order.rejection_reason)}",
                f"📊 액션: `{action_safe}`",
            ]
            text = "\n".join(
                [
                    header,
                    _SEPARATOR,
                    *body_lines,
                    f"⏰ {ts} KST  |  거래일: {trade_date}",
                ]
            )
            return text, None

        # 체결 정보
        fs = order.fill_status
        executed_price = getattr(fs, "executed_price", None) if fs else None
        executed_shares = getattr(fs, "executed_shares", None) if fs else None

        if dry_run:
            # DRY-RUN: 실 발주 없이 시뮬레이션. fill_status가 mock으로 주입된 경우 사용,
            # 없으면 target_shares 표시. PnL은 산정하지 않음.
            sell_price = int(executed_price) if executed_price else 0
            sell_shares = (
                int(executed_shares) if executed_shares else order.target_shares
            )
            status_label = "DRY-RUN 매도 발화"
        elif not (executed_price and executed_shares and executed_shares > 0):
            sell_price = int(executed_price) if executed_price else 0
            sell_shares = (
                int(executed_shares) if executed_shares else order.target_shares
            )
            status_label = "발주 완료 (미체결)"
        else:
            sell_price = int(executed_price)
            sell_shares = int(executed_shares)
            status_label = "체결 완료"

        # 원가 조회
        buy_price = self._lookup_buy_price(order.candidate_id)
        sell_amount = sell_price * sell_shares

        # 손익 계산 (dry_run은 손익 미산정 — 실 체결 없음)
        if dry_run:
            color = "🧪"
            pnl_lines = ["🧪 DRY-RUN — 손익 미산정 (실 발주 없음)"]
            pnl_tuple = None
        elif buy_price is not None and buy_price > 0 and sell_price > 0:
            profit_per_share = sell_price - buy_price
            profit_abs = profit_per_share * sell_shares
            profit_rate = (sell_price - buy_price) / buy_price * 100
            if profit_abs >= 0:
                color = "🟢"
                pnl_emoji = "📈"
                pnl_label = "수익"
            else:
                color = "🔴"
                pnl_emoji = "📉"
                pnl_label = "손실"
            pnl_lines = [
                f"{pnl_emoji} *손익* — {pnl_label}",
                f"매수가: {int(buy_price):,}원 → 매도가: {sell_price:,}원",
                f"수익금: {profit_abs:+,.0f}원 ({profit_rate:+.2f}%)",
            ]
            pnl_tuple = (float(profit_abs), float(profit_rate))
        else:
            color = "🟡"
            pnl_lines = [
                f"⚠️ 원가 조회 실패 — 손익 미산정 (cid={order.candidate_id})",
            ]
            pnl_tuple = None

        gap_str = ""
        if order.gap_rate is not None:
            gap_str = f"  |  갭률: {order.gap_rate*100:+.2f}%"

        partial_str = " (부분 매도)" if order.is_partial else ""
        header = f"{color} *종가베팅 매도* — {cycle_label} / {status_label}{partial_str} ({flag})"
        body_lines = [
            f"📉 {name_safe} ({ticker_safe})",
            f"💰 {sell_shares}주 × {sell_price:,}원 = {sell_amount:,}원",
            f"📊 액션: `{action_safe}`{gap_str}",
            "",
            *pnl_lines,
        ]
        if order.order_id:
            body_lines.append("")
            body_lines.append(f"📋 주문 ID: `{_escape_markdown(order.order_id)}`")

        text = "\n".join(
            [header, _SEPARATOR, *body_lines, f"⏰ {ts} KST  |  거래일: {trade_date}"]
        )
        return text, pnl_tuple

    # ===== 요약 포맷 =====

    def _format_summary(
        self,
        r: "ExitResult",
        *,
        cycle_label: str,
        dry_run: bool,
        per_ticker_pnls: list[tuple[float, float]],
    ) -> str:
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실 매도"
        action_verb = "DRY-RUN 발화" if dry_run else "체결"
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            f"📊 *종가베팅 {cycle_label}* 종료 ({flag})",
            _SEPARATOR,
            f"⏰ {ts}  |  거래일: {r.trade_date}",
            f"대상 {r.total_targets}건 → {action_verb} {r.filled}건 / 미체결 {r.unfilled}건",
        ]
        if r.action_counts:
            counts_str = ", ".join(
                f"{action}={count}"
                for action, count in r.action_counts.items()
                if count > 0
            )
            if counts_str:
                lines.append(f"📋 액션 분포: {counts_str}")
        if r.cancelled > 0:
            lines.append(f"⚠️ 취소 처리: {r.cancelled}건 (force_close 미체결)")

        # 누적 손익
        if per_ticker_pnls:
            total_pnl = sum(p[0] for p in per_ticker_pnls)
            avg_rate = sum(p[1] for p in per_ticker_pnls) / len(per_ticker_pnls)
            pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
            lines.append(
                f"{pnl_emoji} 누적 손익: *{total_pnl:+,.0f}원* "
                f"(평균 수익률 {avg_rate:+.2f}%, n={len(per_ticker_pnls)})"
            )

        if r.errors:
            first_err = _escape_markdown(r.errors[0][:120])
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {first_err})")
        return "\n".join(lines)

    # ===== 원가 조회 =====

    def _lookup_buy_price(self, candidate_id: int) -> Optional[float]:
        """candidate_id 기준 가중평균 매수가 조회.

        우선순위:
          1. entry_phase1_executed_price + entry_phase2_executed_price 가중평균
          2. entry_phase1_executed_price 단독
          3. entry_price (PRD mark_entered 시 박제)
          4. None (호출자가 폴백 메시지 처리)
        """
        if self._candidate_logger is None:
            return None
        try:
            row = self._candidate_logger.get_candidate(candidate_id)
        except Exception as exc:
            logger.warning(
                f"[exit_notifier] candidate_id={candidate_id} 원가 조회 실패: {exc}"
            )
            return None
        if not row:
            return None

        p1 = row.get("entry_phase1_executed_price")
        s1 = row.get("entry_phase1_executed_shares")
        p2 = row.get("entry_phase2_executed_price")
        s2 = row.get("entry_phase2_executed_shares")
        entry_price = row.get("entry_price")

        if p1 and s1 and p1 > 0 and s1 > 0:
            if p2 and s2 and p2 > 0 and s2 > 0:
                return (float(p1) * int(s1) + float(p2) * int(s2)) / (int(s1) + int(s2))
            return float(p1)
        if entry_price and entry_price > 0:
            return float(entry_price)
        return None
