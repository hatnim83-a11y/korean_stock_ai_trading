"""종가베팅 EntryExecutor 진입 결과 텔레그램 알림.

phase1 / phase2 / 통합 요약 메시지 포맷 + 발송.

설계 원칙:
- TelegramReviewBot 의존성 주입 (테스트 시 mock 가능)
- 봇 비활성 시 graceful (False 반환, 예외 없음)
- 결과 dataclass(Phase1Result/Phase2Result) 를 받아 포맷
- dry_run 표기 명시 (실 발주와 시각 구분)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot
from config import now_kst
from logger import logger

if TYPE_CHECKING:  # 순환 import 차단
    from closing_bet_system.execution.entry_executor import (
        Phase1Result,
        Phase2Result,
    )


_SEPARATOR = "─" * 30


class EntryNotifier:
    """EntryExecutor 결과 알림 발송기.

    Args:
        telegram_bot: ``TelegramReviewBot`` 인스턴스 (의존성 주입).
            None 이면 모듈 import 시점에 싱글톤 자동 로드.
    """

    def __init__(self, telegram_bot: Optional[TelegramReviewBot] = None):
        self._bot = telegram_bot or TelegramReviewBot()

    @property
    def is_enabled(self) -> bool:
        return self._bot.is_enabled

    def send_phase1_result(self, result: "Phase1Result", dry_run: bool) -> bool:
        """1차(정규장) 진입 결과 알림."""
        if not self.is_enabled:
            logger.debug("[entry_notifier] 봇 비활성 — phase1 알림 스킵")
            return False
        text = self._format_phase1(result, dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    def send_phase2_result(self, result: "Phase2Result", dry_run: bool) -> bool:
        """2차(동시호가) 진입 결과 알림."""
        if not self.is_enabled:
            logger.debug("[entry_notifier] 봇 비활성 — phase2 알림 스킵")
            return False
        text = self._format_phase2(result, dry_run=dry_run)
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

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
            f"🚨 *종가베팅 진입 스킵 (MarketGuard)*\n"
            f"{_SEPARATOR}\n"
            f"상태: *{status}*\n"
            f"코스피 {self._fmt_pct(kospi)} / 코스닥 {self._fmt_pct(kosdaq)}\n"
            f"사유: {reason}\n"
            f"시각: {now_kst().strftime('%Y-%m-%d %H:%M:%S KST')}"
        )
        return bool(self._bot.notifier.send_message(text, parse_mode="Markdown"))

    # ===== 내부 포맷 =====

    def _format_phase1(self, r: "Phase1Result", *, dry_run: bool) -> str:
        flag = "🧪 DRY_RUN" if dry_run else "✅ 실 발주"
        ts = now_kst().strftime("%H:%M:%S")
        lines = [
            f"📈 *종가베팅 1차 진입 결과* ({flag})",
            _SEPARATOR,
            f"⏰ {ts} KST  |  거래일: {r.trade_date}",
            f"후보 {r.total_candidates}건 → 발주 {r.submitted}건 / 체결 {r.filled}건",
            f"가격상한 위반 {r.skipped_price_cap}건 / fund_guard 거부 {r.fund_guard_rejected}건",
        ]
        if r.market_guard_status:
            lines.append(f"MarketGuard: {r.market_guard_status}")
        if r.errors:
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {r.errors[0][:80]})")
        return "\n".join(lines)

    def _format_phase2(self, r: "Phase2Result", *, dry_run: bool) -> str:
        flag = "🧪 DRY_RUN" if dry_run else "✅ 실 발주"
        ts = now_kst().strftime("%H:%M:%S")
        lines = [
            f"📈 *종가베팅 2차 진입 결과* ({flag})",
            _SEPARATOR,
            f"⏰ {ts} KST  |  거래일: {r.trade_date}",
            f"대기 {r.eligible}건 → 발주 {r.submitted}건 / 체결 {r.filled}건",
            f"보류(예상체결가 +0.5%) {r.skipped_estimated_price}건",
            f"취소(호가 잔량 <0.8) {r.cancelled_ask_bid}건",
        ]
        if r.errors:
            lines.append(f"⚠️ 에러 {len(r.errors)}건 (첫 건: {r.errors[0][:80]})")
        return "\n".join(lines)

    def _format_pipeline_summary(
        self,
        p1: "Phase1Result",
        p2: Optional["Phase2Result"],
        *,
        dry_run: bool,
    ) -> str:
        flag = "🧪 DRY_RUN" if dry_run else "✅ 실 발주"
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            f"📊 *종가베팅 진입 파이프라인 종료* ({flag})",
            _SEPARATOR,
            f"⏰ {ts}  |  거래일: {p1.trade_date}",
            f"1차: 후보 {p1.total_candidates} → 체결 {p1.filled}건",
        ]
        if p2 is not None:
            lines.append(f"2차: 대기 {p2.eligible} → 체결 {p2.filled}건")
            total = p1.filled + p2.filled
        else:
            lines.append("2차: 비활성 (phase2_enabled=False)")
            total = p1.filled
        lines.append(f"누적 체결: *{total}건*")
        return "\n".join(lines)

    @staticmethod
    def _fmt_pct(value: Optional[float]) -> str:
        if value is None:
            return "N/A"
        return f"{value:+.2f}%"
