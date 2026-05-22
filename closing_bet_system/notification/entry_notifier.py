"""종가베팅 EntryExecutor 진입 결과 텔레그램 알림.

단위 2-4c (5/15) 집계만 발송 → 5/22 스윙 수준 통일:
- 종목당 개별 메시지 + per-cycle 요약 메시지 분리 발송
- 손익 표시는 매도 알림에서 (exit_notifier)

설계 원칙:
- TelegramReviewBot 의존성 주입 (테스트 시 mock 가능)
- 봇 비활성 시 graceful (False 반환, 예외 없음)
- 결과 dataclass(Phase1Result/Phase2Result) 의 ``orders`` 리스트 활용
- dry_run 표기 명시 (실 발주와 시각 구분)
- 텔레그램 rate limit (30 msg/sec) 여유 — 1진입 2~3종목 가정
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from closing_bet_system.notification.telegram_review_bot import (
    TelegramReviewBot,
    _escape_markdown,
)
from config import now_kst
from logger import logger

if TYPE_CHECKING:  # 순환 import 차단
    from closing_bet_system.execution.entry_executor import (
        CandidateOrder,
        Phase1Result,
        Phase2Result,
    )


_SEPARATOR = "━" * 22


class EntryNotifier:
    """EntryExecutor 결과 알림 발송기 (스윙 메시지 형식 통일).

    Args:
        telegram_bot: ``TelegramReviewBot`` 인스턴스 (의존성 주입).
            None 이면 모듈 import 시점에 싱글톤 자동 로드.
    """

    def __init__(self, telegram_bot: Optional[TelegramReviewBot] = None):
        self._bot = telegram_bot or TelegramReviewBot()

    @property
    def is_enabled(self) -> bool:
        return self._bot.is_enabled

    # ===== Public API =====

    def send_phase1_result(self, result: "Phase1Result", dry_run: bool) -> bool:
        """1차(정규장) 진입 결과 알림 — 종목별 개별 메시지 + 요약 1건."""
        if not self.is_enabled:
            logger.debug("[entry_notifier] 봇 비활성 — phase1 알림 스킵")
            return False
        sent_any = False
        for order in result.orders:
            if not order.submitted and not order.rejection_reason:
                continue
            text = self._format_phase1_per_ticker(
                order, trade_date=result.trade_date, dry_run=dry_run
            )
            ok = bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))
            sent_any = sent_any or ok
        summary = self._format_phase1_summary(result, dry_run=dry_run)
        ok = bool(self._bot.notifier.send_message(summary, parse_mode="Markdown"))
        return sent_any or ok

    def send_phase2_result(self, result: "Phase2Result", dry_run: bool) -> bool:
        """2차(동시호가) 진입 결과 알림 — 종목별 개별 메시지 + 요약 1건."""
        if not self.is_enabled:
            logger.debug("[entry_notifier] 봇 비활성 — phase2 알림 스킵")
            return False
        sent_any = False
        for order in result.orders:
            if not order.submitted and not order.rejection_reason:
                continue
            text = self._format_phase2_per_ticker(
                order, trade_date=result.trade_date, dry_run=dry_run
            )
            ok = bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))
            sent_any = sent_any or ok
        summary = self._format_phase2_summary(result, dry_run=dry_run)
        ok = bool(self._bot.notifier.send_message(summary, parse_mode="Markdown"))
        return sent_any or ok

    def send_pipeline_summary(
        self,
        phase1: "Phase1Result",
        phase2: Optional["Phase2Result"],
        dry_run: bool,
    ) -> bool:
        """run_entry_pipeline 통합 요약 알림 (phase1+phase2 종료 후)."""
        if not self.is_enabled:
            return False
        text = self._format_pipeline_summary(phase1, phase2, dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    def send_market_guard_skip(self, status: str, info: dict) -> bool:
        """MarketGuard CRISIS 로 전체 스킵 시 알림."""
        if not self.is_enabled:
            return False
        kospi = info.get("kospi_rate")
        kosdaq = info.get("kosdaq_rate")
        reason = info.get("reason", "")
        text = (
            f"🚨 *종가베팅 진입 스킵 — MarketGuard*\n"
            f"{_SEPARATOR}\n"
            f"상태: *{_escape_markdown(status)}*\n"
            f"코스피: {self._fmt_pct(kospi)}  |  코스닥: {self._fmt_pct(kosdaq)}\n"
            f"사유: {_escape_markdown(reason)}\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S KST')}"
        )
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    # ===== 내부 포맷 — phase1 =====

    def _format_phase1_per_ticker(
        self,
        order: "CandidateOrder",
        *,
        trade_date: str,
        dry_run: bool,
    ) -> str:
        """phase1 1종목 매수 메시지 (스윙 send_buy_alert 패턴)."""
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실발주"
        ts = now_kst().strftime("%H:%M:%S")
        name_safe = _escape_markdown(order.name)
        ticker_safe = _escape_markdown(order.ticker)

        if order.rejection_reason:
            header = f"⚠️ *종가베팅 1차 매수 거부* ({flag})"
            body_lines = [
                f"📈 {name_safe} ({ticker_safe})",
                f"📝 거부 사유: {_escape_markdown(order.rejection_reason)}",
            ]
        else:
            fs = order.fill_status
            executed_price = getattr(fs, "executed_price", None) if fs else None
            executed_shares = getattr(fs, "executed_shares", None) if fs else None
            if dry_run:
                price = order.target_price
                shares = order.quantity
                status_label = "DRY-RUN 매수 발화"
            elif executed_price and executed_shares and executed_shares > 0:
                price = int(executed_price)
                shares = int(executed_shares)
                status_label = "체결 완료"
            else:
                price = order.target_price
                shares = order.quantity
                status_label = "발주 완료 (미체결)"

            amount = price * shares
            header = f"🟢 *종가베팅 1차 매수* — {status_label} ({flag})"
            body_lines = [
                f"📈 {name_safe} ({ticker_safe})",
                f"💰 {shares}주 × {price:,}원 = {amount:,}원",
            ]
            if order.order_id:
                body_lines.append(f"📋 주문 ID: `{_escape_markdown(order.order_id)}`")

        return "\n".join(
            [header, _SEPARATOR, *body_lines, f"⏰ {ts} KST  |  거래일: {trade_date}"]
        )

    def _format_phase1_summary(self, r: "Phase1Result", *, dry_run: bool) -> str:
        """phase1 종합 요약."""
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실발주"
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            f"📊 *종가베팅 1차 진입 종료* ({flag})",
            _SEPARATOR,
            f"⏰ {ts}  |  거래일: {r.trade_date}",
            f"후보 {r.total_candidates}건 → 발주 {r.submitted}건 / 체결 {r.filled}건",
            f"가격상한 위반 {r.skipped_price_cap}건  |  fund_guard 거부 {r.fund_guard_rejected}건",
        ]
        if r.market_guard_status:
            lines.append(f"🛡️ MarketGuard: *{_escape_markdown(r.market_guard_status)}*")
        if r.errors:
            first_err = _escape_markdown(r.errors[0][:120])
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {first_err})")
        return "\n".join(lines)

    # ===== 내부 포맷 — phase2 =====

    def _format_phase2_per_ticker(
        self,
        order: "CandidateOrder",
        *,
        trade_date: str,
        dry_run: bool,
    ) -> str:
        """phase2 1종목 매수 메시지."""
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실발주"
        ts = now_kst().strftime("%H:%M:%S")
        name_safe = _escape_markdown(order.name)
        ticker_safe = _escape_markdown(order.ticker)

        if order.rejection_reason:
            header = f"⚠️ *종가베팅 2차 매수 거부* ({flag})"
            body_lines = [
                f"📈 {name_safe} ({ticker_safe})",
                f"📝 거부 사유: {_escape_markdown(order.rejection_reason)}",
            ]
        else:
            fs = order.fill_status
            executed_price = getattr(fs, "executed_price", None) if fs else None
            executed_shares = getattr(fs, "executed_shares", None) if fs else None
            if dry_run:
                price = order.target_price
                shares = order.quantity
                status_label = "DRY-RUN 매수 발화"
            elif executed_price and executed_shares and executed_shares > 0:
                price = int(executed_price)
                shares = int(executed_shares)
                status_label = "체결 완료 (동시호가)"
            else:
                price = order.target_price
                shares = order.quantity
                status_label = "발주 완료 (미체결)"

            amount = price * shares
            header = f"🟢 *종가베팅 2차 매수* — {status_label} ({flag})"
            body_lines = [
                f"📈 {name_safe} ({ticker_safe})",
                f"💰 {shares}주 × {price:,}원 = {amount:,}원",
            ]
            if order.order_id:
                body_lines.append(f"📋 주문 ID: `{_escape_markdown(order.order_id)}`")

        return "\n".join(
            [header, _SEPARATOR, *body_lines, f"⏰ {ts} KST  |  거래일: {trade_date}"]
        )

    def _format_phase2_summary(self, r: "Phase2Result", *, dry_run: bool) -> str:
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실발주"
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            f"📊 *종가베팅 2차 진입 종료* ({flag})",
            _SEPARATOR,
            f"⏰ {ts}  |  거래일: {r.trade_date}",
            f"대기 {r.eligible}건 → 발주 {r.submitted}건 / 체결 {r.filled}건",
            f"보류(예상체결가 +0.5%) {r.skipped_estimated_price}건  |  취소(호가 잔량 <0.8) {r.cancelled_ask_bid}건",
        ]
        if r.errors:
            first_err = _escape_markdown(r.errors[0][:120])
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {first_err})")
        return "\n".join(lines)

    # ===== 통합 파이프라인 요약 =====

    def _format_pipeline_summary(
        self,
        p1: "Phase1Result",
        p2: Optional["Phase2Result"],
        *,
        dry_run: bool,
    ) -> str:
        flag = "🧪 DRY-RUN" if dry_run else "✅ 실발주"
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            f"📊 *종가베팅 진입 파이프라인 종료* ({flag})",
            _SEPARATOR,
            f"⏰ {ts}  |  거래일: {p1.trade_date}",
            f"1차: 후보 {p1.total_candidates} → 발주 {p1.submitted} / 체결 *{p1.filled}건*",
        ]
        if p2 is not None:
            lines.append(
                f"2차: 대기 {p2.eligible} → 발주 {p2.submitted} / 체결 *{p2.filled}건*"
            )
            total = p1.filled + p2.filled
        else:
            lines.append("2차: 비활성 (phase2_enabled=False)")
            total = p1.filled
        lines.append(f"📦 누적 체결: *{total}건*")
        if p1.market_guard_status:
            lines.append(f"🛡️ MarketGuard: *{_escape_markdown(p1.market_guard_status)}*")
        return "\n".join(lines)

    # ===== 유틸 =====

    @staticmethod
    def _fmt_pct(value: Optional[float]) -> str:
        if value is None:
            return "N/A"
        return f"{value:+.2f}%"
