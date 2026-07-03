"""closing_bet_system.notification.telegram_review_bot

PRD 16-3 — 종가베팅 후보 텔레그램 알림 봇 (Phase 1: 알림형).

**책임**:
1. 1-4 ScoreBreakdown + 1-5a DartSnapshot + 1-5b RiskAssessment 통합 → 마크다운 메시지 포맷
2. 단일/배치 알림 발송 (15:15 트리거)
3. 일일 요약 발송 (15:35 마감 후)
4. 봇 비활성 (.env 미설정) 시 fail-safe — 메시지 무시, 시스템 동작 영향 X

**책임 외**:
- 신규 봇 인스턴스 생성 → 0-B ``infra/telegram_client.get_telegram_notifier()``
- 사용자 인터랙션 (확인 버튼) → Phase 2 ``telegram_interactive_bot``
- 텔레그램 명령어 폴링 → Phase 2 (Phase 1 은 일방향 push)
- 후보 DB 저장 → 1-6 ``CandidateLogger``

**Phase 1 정책 (PRD 16-3)**:
- "Telegram 발송 (Phase 1: 정보만 / Phase 2+: 확인 버튼)"
- 자동매수 절대 금지 — 메시지 본문에 "수동 매수 검토" 문구 명시
- 알림 빈도: 15:15 1회 + 15:35 일일 요약 1회 (예상)

**메시지 형식 결정**:
- Markdown (TelegramNotifier 기본). Telegram 의 Markdown 은 ``*굵게*`` / ``_기울임_`` / ``\\`코드\\``` 지원.
- ``_`` (밑줄) 가 식별자에 포함되면 escape 필요 → ``_escape_markdown()`` 헬퍼.

**duck-typing 입력** (1-2~1-5b 와 결합도 최소화):
ScoreBreakdown / DartDisclosureSnapshot / OvernightRiskAssessment 모두 dataclass 또는 dict
양방향 지원. 키 누락 시 None / "—" 표기.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

from closing_bet_system.infra.telegram_client import get_telegram_notifier


# ===== 메시지 상수 =====

_HEADER = "🔔 *종가베팅 후보 알림*"
_SEPARATOR = "━" * 22
_PHASE1_FOOTER = (
    "\n\n⚠️ *Phase 1: 알림만 — 자동매수 비활성*\n"
    "수동 매수 검토 후 진행하세요."
)

# 1-9 운영 점검 게이트 건수 (settings.yaml gate.operational_review 와 동기화 유지)
_GATE_OPERATIONAL_REVIEW = 30


# ===== 메인 클래스 =====


class TelegramReviewBot:
    """종가베팅 후보 알림 봇.

    Args:
        notifier: ``TelegramNotifier`` 인스턴스 (의존성 주입). None 이면
            ``infra.telegram_client.get_telegram_notifier()`` 싱글톤 자동 로드.

    Examples:
        >>> bot = TelegramReviewBot()
        >>> bot.is_enabled
        True
        >>> bot.send_alert(
        ...     ticker="005930", name="삼성전자",
        ...     score_breakdown=score_bd,
        ...     dart_snapshot=dart_snap,
        ...     risk_assessment=risk_assess,
        ... )
        True
    """

    def __init__(self, notifier: Optional[Any] = None):
        self._notifier = notifier

    @property
    def notifier(self) -> Any:
        """TelegramNotifier 지연 로딩."""
        if self._notifier is None:
            self._notifier = get_telegram_notifier()
        return self._notifier

    @property
    def is_enabled(self) -> bool:
        """봇 활성 여부 — .env 미설정 시 False."""
        return bool(getattr(self.notifier, "_enabled", False))

    # ===== 메시지 발송 =====

    def send_test_message(self) -> bool:
        """테스트 메시지 발송 (1-7 헬스체크용).

        Returns:
            발송 성공 여부 (봇 비활성 시 False).
        """
        if not self.is_enabled:
            logger.warning("[telegram_review_bot] 봇 비활성 — send_test_message 스킵")
            return False
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        text = (
            f"✅ *종가베팅 시스템 테스트*\n"
            f"{_SEPARATOR}\n"
            f"신규 봇 토큰 정상 작동 확인\n"
            f"시각: {ts}\n"
            f"Phase 1 (알림형) 운용 중"
        )
        return bool(self.notifier.send_message(text, parse_mode="Markdown"))

    def send_alert(
        self,
        ticker: str,
        name: str,
        score_breakdown: Any = None,
        dart_snapshot: Any = None,
        risk_assessment: Any = None,
        rank: Optional[int] = None,
        total: Optional[int] = None,
    ) -> bool:
        """단일 종목 후보 알림 발송.

        Args:
            ticker: 6자리 종목코드
            name: 종목명
            score_breakdown: 1-4 ``ScoreBreakdown`` (또는 dict)
            dart_snapshot: 1-5a ``DartDisclosureSnapshot`` (또는 dict, None 가능)
            risk_assessment: 1-5b ``OvernightRiskAssessment`` (또는 dict, None 가능)
            rank: 배치 발송 시 순위 (예: 1/5)
            total: 배치 총 종목 수

        Returns:
            발송 성공 여부.
        """
        if not self.is_enabled:
            logger.debug(f"[telegram_review_bot] 봇 비활성 — {ticker} 알림 스킵")
            return False
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            logger.warning(f"[telegram_review_bot] 잘못된 ticker: {ticker!r}")
            return False
        if not isinstance(name, str) or not name.strip():
            name = "(이름 없음)"

        text = self.format_alert_message(
            ticker=ticker, name=name.strip(),
            score_breakdown=score_breakdown,
            dart_snapshot=dart_snapshot,
            risk_assessment=risk_assessment,
            rank=rank, total=total,
        )
        return bool(self.notifier.send_message(text, parse_mode="Markdown"))

    def send_batch_alert(self, candidates: list[dict]) -> int:
        """여러 종목 배치 알림 (15:15 트리거).

        Args:
            candidates: ``[{"ticker", "name", "score_breakdown", "dart_snapshot",
                "risk_assessment"}, ...]`` 형태 리스트

        Returns:
            성공 발송 건수.
        """
        if not self.is_enabled:
            logger.warning(f"[telegram_review_bot] 봇 비활성 — 배치 {len(candidates)}건 스킵")
            return 0
        if not candidates:
            return 0
        # 헤더 (배치 요약)
        summary = (
            f"{_HEADER}\n"
            f"{_SEPARATOR}\n"
            f"📅 {now_kst().strftime('%Y-%m-%d')} 15:15 후보 *{len(candidates)}건*"
        )
        self.notifier.send_message(summary, parse_mode="Markdown")

        success = 0
        for i, c in enumerate(candidates, start=1):
            ok = self.send_alert(
                ticker=c.get("ticker", ""),
                name=c.get("name", ""),
                score_breakdown=c.get("score_breakdown"),
                dart_snapshot=c.get("dart_snapshot"),
                risk_assessment=c.get("risk_assessment"),
                rank=i, total=len(candidates),
            )
            if ok:
                success += 1
        logger.info(
            f"[telegram_review_bot] 배치 알림 {success}/{len(candidates)}건 발송 완료"
        )
        return success

    def send_daily_summary(
        self,
        trade_date: Any,
        status_counts: dict,
        recommended_count: int = 0,
        closed_positions: Optional[list[dict]] = None,
        phase1_fills: Optional[list[dict]] = None,
    ) -> bool:
        """일일 요약 (15:35 마감 후, 스윙 send_daily_report 형식 통일).

        Args:
            trade_date: 거래일 (date / str / datetime)
            status_counts: 1-6 ``count_by_status()`` 결과 dict
            recommended_count: 누적 recommended 건수 (1-9 게이트 진척도)
            closed_positions: 오늘 청산된 포지션 리스트 (각 dict: ticker, name,
                entry_price_avg, exit_price, qty, profit_abs, profit_rate).
                None 또는 빈 리스트 시 PnL 섹션 생략.
            phase1_fills: 오늘 phase1 체결 리스트. 옵션 A 설계상
                candidate_status='recommended'로 남는 phase1-only 체결을 일일요약에
                별도 표시하기 위한 값.

        Returns:
            발송 성공 여부.
        """
        if not self.is_enabled:
            return False
        date_str = _to_date_label(trade_date)
        rec = status_counts.get("recommended", 0)
        ent = status_counts.get("entered", 0)
        rej_f = status_counts.get("rejected_filter", 0)
        rej_m = status_counts.get("rejected_manual", 0)

        lines = [
            f"📊 *종가베팅 일일 요약*",
            _SEPARATOR,
            f"📅 {date_str}",
            "",
            "📋 *후보 통계*",
            f"• Recommended: {rec}건",
            f"• Entered: {ent}건",
            f"• Rejected (필터): {rej_f}건",
            f"• Rejected (수동): {rej_m}건",
        ]

        # 옵션 A phase1-only 체결은 candidate_status='recommended'로 남을 수 있으므로
        # entered 집계와 별도로 실제 체결 현황을 표시한다.
        if phase1_fills:
            total_amount = sum(float(f.get("total_amount") or 0) for f in phase1_fills)
            total_shares = sum(int(f.get("total_shares") or 0) for f in phase1_fills)
            lines.extend([
                "",
                f"🟢 *Phase1 체결*: {len(phase1_fills)}건 / {total_amount:,.0f}원 / {total_shares:,}주",
            ])
            for fill in phase1_fills[:5]:
                tk = _escape_markdown(fill.get("ticker", ""))
                nm = _escape_markdown(fill.get("name", ""))
                shares = int(fill.get("total_shares") or 0)
                avg_price = float(fill.get("avg_entry_price") or 0)
                status = _escape_markdown(fill.get("candidate_status", ""))
                order_id = _escape_markdown(fill.get("entry_phase1_order_id", ""))
                phase_label = "phase1-only" if fill.get("phase1_only") else "phase1+phase2"
                line = f"• {nm} ({tk}): {shares:,}주 @ {avg_price:,.0f}원 — `{phase_label}` / status `{status}`"
                if order_id:
                    line += f" / 주문 `{order_id}`"
                lines.append(line)
            if len(phase1_fills) > 5:
                lines.append(f"… +{len(phase1_fills) - 5}건 더")

        # 오늘 청산된 포지션 PnL 섹션 (선택적)
        if closed_positions:
            # 전체 합산 (표시는 5건만, 누적은 전체)
            all_pnl_pairs = [
                (float(p["profit_abs"]), float(p["profit_rate"]))
                for p in closed_positions
                if p.get("profit_abs") is not None
                and p.get("profit_rate") is not None
            ]
            lines.append("")
            lines.append(f"💼 *오늘 청산 종목 PnL* ({len(closed_positions)}건)")
            for pos in closed_positions[:5]:
                tk = _escape_markdown(pos.get("ticker", ""))
                nm = _escape_markdown(pos.get("name", ""))
                pa = pos.get("profit_abs")
                pr = pos.get("profit_rate")
                if pa is None or pr is None:
                    continue
                pa = float(pa)
                pr = float(pr)
                pnl_emoji = "🟢" if pa >= 0 else "🔴"
                lines.append(
                    f"{pnl_emoji} {nm} ({tk}): {pa:+,.0f}원 ({pr:+.2f}%)"
                )
            if len(closed_positions) > 5:
                lines.append(f"… +{len(closed_positions) - 5}건 더")
            if all_pnl_pairs:
                total_pnl_all = sum(p[0] for p in all_pnl_pairs)
                avg_rate_all = sum(p[1] for p in all_pnl_pairs) / len(all_pnl_pairs)
                total_emoji = "🟢" if total_pnl_all >= 0 else "🔴"
                lines.append("")
                lines.append(
                    f"{total_emoji} *오늘 총 손익* ({len(all_pnl_pairs)}건 전체): "
                    f"{total_pnl_all:+,.0f}원 (평균 {avg_rate_all:+.2f}%)"
                )

        lines.append("")
        lines.append(
            f"📈 누적 recommended: *{recommended_count}건* / 게이트 {_GATE_OPERATIONAL_REVIEW}건"
        )
        text = "\n".join(lines)
        return bool(self.notifier.send_message(text, parse_mode="Markdown"))

    def send_system_start(self, info: Optional[dict] = None) -> bool:
        """종가베팅 시스템 시작 알림 (스윙 send_system_start 패턴).

        Args:
            info: 추가 정보 dict (예: dry_run 상태, capital_ratio 등). 옵션.

        Returns:
            발송 성공 여부.
        """
        if not self.is_enabled:
            return False
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            "🚀 *종가베팅 시스템 시작*",
            _SEPARATOR,
            f"⏰ {ts}",
        ]
        if info:
            dr_entry = info.get("entry_dry_run")
            dr_exit = info.get("exit_dry_run")
            cap = info.get("capital_ratio")
            if dr_entry is not None:
                lines.append(
                    f"📈 매수: {'🧪 DRY-RUN' if dr_entry else '✅ 실발주'}"
                )
            if dr_exit is not None:
                lines.append(
                    f"📉 매도: {'🧪 DRY-RUN' if dr_exit else '✅ 실 매도'}"
                )
            if cap is not None:
                lines.append(f"💰 자금 비중: {float(cap)*100:.1f}%")
        return bool(self.notifier.send_message("\n".join(lines), parse_mode="Markdown"))

    def send_system_stop(self, reason: str = "") -> bool:
        """종가베팅 시스템 중지 알림.

        Args:
            reason: 중지 사유 (옵션).

        Returns:
            발송 성공 여부.
        """
        if not self.is_enabled:
            return False
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
        lines = [
            "🛑 *종가베팅 시스템 중지*",
            _SEPARATOR,
            f"⏰ {ts}",
        ]
        if reason:
            lines.append(f"📝 사유: {_escape_markdown(reason)}")
        return bool(self.notifier.send_message("\n".join(lines), parse_mode="Markdown"))

    # ===== 메시지 포맷 =====

    def format_alert_message(
        self,
        ticker: str,
        name: str,
        score_breakdown: Any = None,
        dart_snapshot: Any = None,
        risk_assessment: Any = None,
        rank: Optional[int] = None,
        total: Optional[int] = None,
    ) -> str:
        """단일 종목 알림 마크다운 본문 생성 (테스트/디버깅 용도로 단독 호출 가능)."""
        ticker_safe = _escape_markdown(ticker)
        name_safe = _escape_markdown(name)

        # 헤더
        header = f"📊 *{ticker_safe} {name_safe}*"
        if rank is not None and total is not None:
            header = f"📊 *{ticker_safe} {name_safe}* `[{rank}/{total}]`"

        lines = [header, _SEPARATOR]

        # 점수
        raw = _gak(score_breakdown, "raw_total", None)
        weighted = _gak(score_breakdown, "weighted_total", None)
        decision = _gak(score_breakdown, "decision", "?")
        l1 = _gak(score_breakdown, "layer1_subscore", None)
        l2 = _gak(score_breakdown, "layer2_subscore", None)
        l3 = _gak(score_breakdown, "layer3_subscore", None)

        if raw is not None:
            score_line = f"점수: *{raw}/11* (weighted={_fmt_num(weighted)}) → `{decision}`"
        else:
            score_line = f"점수: — → `{decision}`"
        lines.append(score_line)
        lines.append(
            f"─ L1(수급): {_fmt_int(l1)}/4 ｜ "
            f"L2(가격): {_fmt_int(l2)}/4 ｜ "
            f"L3(모멘텀): {_fmt_int(l3)}/3"
        )

        # ATR 과열 (하드 필터 표시)
        atr_oh = _gak(score_breakdown, "atr_overheat_value", None)
        excluded = _gak(score_breakdown, "excluded_by_filter", False)
        if atr_oh is not None:
            mark = "🚫" if excluded else "✅"
            atr_oh_str = _fmt_num(atr_oh)  # str/non-float 방어 (duck-typing dict 허용)
            lines.append(f"─ atr\\_overheat: `{atr_oh_str}` {mark}")

        # DART
        if dart_snapshot is not None:
            has_pos = _gak(dart_snapshot, "has_positive_disclosure", False)
            excl_dart = _gak(dart_snapshot, "exclude_by_disclosure", False)
            pos_matches = _gak(dart_snapshot, "positive_matches", ())
            excl_matches = _gak(dart_snapshot, "exclusion_matches", ())
            if excl_dart and excl_matches:
                kw = excl_matches[0][1] if isinstance(excl_matches[0], (tuple, list)) else "?"
                lines.append(f"─ DART: 🚫 즉시제외 — `{_escape_markdown(str(kw))}`")
            elif has_pos and pos_matches:
                kw = pos_matches[0][1] if isinstance(pos_matches[0], (tuple, list)) else "?"
                lines.append(f"─ DART: ✅ 호재 — `{_escape_markdown(str(kw))}`")
            else:
                lines.append("─ DART: 일반 (호재/제외 없음)")

        # 외부 리스크 (1-5b)
        if risk_assessment is not None:
            can_enter = _gak(risk_assessment, "can_enter", True)
            final_size = _gak(risk_assessment, "final_size_factor", None)
            reason = _gak(risk_assessment, "decision_reason", "")
            if not can_enter:
                lines.append(f"─ 외부리스크: 🚫 진입 불가 — {_escape_markdown(reason[:80])}")
            elif final_size is not None and final_size < 1.0:
                lines.append(
                    f"─ 외부리스크: ⚠️ 비중 축소 ({final_size*100:.0f}%) — "
                    f"{_escape_markdown(reason[:60])}"
                )
            else:
                lines.append("─ 외부리스크: ✅ 정상 시장")

        return "\n".join(lines) + _PHASE1_FOOTER


# ===== 헬퍼 =====


def _gak(obj: Any, name: str, default: Any) -> Any:
    """getattr-or-key (1-5b/1-6 패턴)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _escape_markdown(text: Any) -> str:
    """Telegram Markdown(v1) 의 특수문자 escape.

    (v1 Markdown 만 지원 — TelegramNotifier 기본). v1 의 활성 특수문자: ``_*[`\\``.
    """
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("\\", "\\\\")
         .replace("_", "\\_")
         .replace("*", "\\*")
         .replace("[", "\\[")
         .replace("]", "\\]")
         .replace("`", "\\`")
    )


def _fmt_num(value: Any) -> str:
    """float / int → 보기 좋은 문자열. None → '—'."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_int(value: Any) -> str:
    """int 표시. None → '—'."""
    if value is None:
        return "—"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "—"


def _to_date_label(value: Any) -> str:
    """date/datetime/str → "YYYY-MM-DD" 표시."""
    if value is None:
        return now_kst().strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()[:10]
        except Exception:
            return str(value)
    return str(value)
