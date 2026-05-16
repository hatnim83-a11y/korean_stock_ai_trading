"""종가베팅 ExitExecutor 매도 결과 텔레그램 알림 (단위 2-5c).

emergency_stop / morning_exit / force_close 3종 결과 메시지 포맷 + 발송.

설계 원칙 (entry_notifier 패턴 일관성)
- TelegramReviewBot 의존성 주입 (테스트 시 mock)
- 봇 비활성 시 graceful (False 반환, 예외 없음)
- 결과 dataclass(ExitResult 단위 2-5c) 를 받아 포맷
- dry_run=True 시 제목 prefix `[DRY-RUN]` + 본문 "would have sold" 강제 (P1-3 박제)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot
from config import now_kst
from logger import logger

if TYPE_CHECKING:
    from closing_bet_system.execution.exit_executor import ExitResult


_SEPARATOR = "─" * 30


class ExitNotifier:
    """ExitExecutor 결과 알림 발송기."""

    def __init__(self, telegram_bot: Optional[TelegramReviewBot] = None):
        self._bot = telegram_bot or TelegramReviewBot()

    @property
    def is_enabled(self) -> bool:
        return self._bot.is_enabled

    def send_emergency_stop_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """09:01 emergency_stop 결과 (hard_stop_loss)."""
        if not self.is_enabled:
            logger.debug("[exit_notifier] 봇 비활성 — emergency_stop 알림 스킵")
            return False
        text = self._format_result(result, label="09:01 EMERGENCY STOP", dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    def send_morning_exit_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """09:30 morning_exit 결과 (4단계 매도 액션)."""
        if not self.is_enabled:
            logger.debug("[exit_notifier] 봇 비활성 — morning_exit 알림 스킵")
            return False
        text = self._format_result(result, label="09:30 MORNING EXIT", dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    def send_force_close_result(self, result: "ExitResult", dry_run: bool) -> bool:
        """10:30 force_close 결과 (잔량 시장가 전량)."""
        if not self.is_enabled:
            logger.debug("[exit_notifier] 봇 비활성 — force_close 알림 스킵")
            return False
        text = self._format_result(result, label="10:30 FORCE CLOSE", dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    # ===== 내부 포맷 =====

    def _format_result(
        self,
        r: "ExitResult",
        *,
        label: str,
        dry_run: bool,
    ) -> str:
        flag = "🧪 [DRY-RUN]" if dry_run else "✅ 실 매도"
        ts = now_kst().strftime("%H:%M:%S")
        action_verb = "would have sold" if dry_run else "sold"
        lines = [
            f"📉 *종가베팅 {label}* ({flag})",
            _SEPARATOR,
            f"⏰ {ts} KST  |  거래일: {r.trade_date}",
            f"대상 {r.total_targets}건 → {action_verb} {r.filled}건 / 미체결 {r.unfilled}건",
        ]
        if r.action_counts:
            counts_str = ", ".join(
                f"{action}={count}" for action, count in r.action_counts.items() if count > 0
            )
            if counts_str:
                lines.append(f"액션 분포: {counts_str}")
        if r.cancelled > 0:
            lines.append(f"⚠️ 취소 처리: {r.cancelled}건 (force_close 미체결 취소)")
        if r.errors:
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {r.errors[0][:80]})")
        return "\n".join(lines)
