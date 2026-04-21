"""
telegram_notifier.py - 텔레그램 알림 모듈

이 파일은 텔레그램 봇을 통한 알림 기능을 제공합니다.

주요 기능:
- 메시지 전송
- 일일 리포트 전송
- 매매 알림 전송
- 에러 알림 전송
- 이미지/파일 전송

사용법:
    from modules.reporter.telegram_notifier import TelegramNotifier
    
    notifier = TelegramNotifier()
    notifier.send_message("🚀 시스템 시작!")
    notifier.send_daily_report(portfolio, metrics)
"""

import asyncio
from datetime import datetime, date
from typing import Optional
import json
import time

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst


class TelegramNotifier:
    """
    텔레그램 봇 알림
    
    트레이딩 관련 알림을 텔레그램으로 전송합니다.
    
    Attributes:
        bot_token: 봇 토큰
        chat_id: 채팅 ID
        
    Example:
        >>> notifier = TelegramNotifier()
        >>> notifier.send_message("안녕하세요!")
        >>> notifier.send_daily_report(portfolio, metrics)
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ):
        """
        텔레그램 알리미 초기화
        
        Args:
            bot_token: 텔레그램 봇 토큰
            chat_id: 수신할 채팅 ID
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._enabled = bool(self.bot_token and self.chat_id)
        self._listening = False
        self._rate_limit: dict[int, float] = {}  # chat_id → last command timestamp
        self._rate_limit_seconds = 5
        self._system_ref = None  # main.py TradingSystem 참조 (pause/resume용)

        # 매도 명령어 pending 상태
        self._pending_sell: Optional[dict] = None   # {"stock_code", "quantity", "stock_name"}
        self._pending_sell_all: bool = False
        self._pending_sell_expires: float = 0
        self._confirm_timeout_sec = 30

        if self._enabled:
            logger.info("텔레그램 알림 초기화 완료")
        else:
            logger.warning("텔레그램 설정 없음 (알림 비활성화)")
    
    # ===== 메시지 전송 =====
    
    def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
        disable_notification: bool = False
    ) -> bool:
        """
        텍스트 메시지 전송
        
        Args:
            text: 메시지 내용
            parse_mode: 파싱 모드 ("Markdown" 또는 "HTML")
            disable_notification: 알림 음소거
        
        Returns:
            전송 성공 여부
        """
        if not self._enabled:
            logger.debug(f"[텔레그램 비활성] {text[:50]}...")
            return False

        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification
        }

        try:
            response = httpx.post(url, json=data, timeout=10)
            result = response.json()

            if result.get("ok"):
                logger.debug("텔레그램 메시지 전송 성공")
                return True
            else:
                error_desc = result.get('description', '')
                logger.warning(f"텔레그램 전송 실패 (parse_mode={parse_mode}): {error_desc}")

                # Markdown/HTML 파싱 실패 시 plain text로 재시도
                if parse_mode:
                    logger.info("텔레그램 plain text로 재시도")
                    fallback_data = {
                        "chat_id": self.chat_id,
                        "text": text,
                        "disable_notification": disable_notification
                    }
                    fallback_response = httpx.post(url, json=fallback_data, timeout=10)
                    fallback_result = fallback_response.json()
                    if fallback_result.get("ok"):
                        logger.debug("텔레그램 plain text 전송 성공")
                        return True
                    else:
                        logger.error(f"텔레그램 plain text 전송도 실패: {fallback_result.get('description')}")

                return False

        except Exception as e:
            logger.error(f"텔레그램 전송 오류: {e}")
            return False

    def send_improvement_reminder(self, mode: str) -> bool:
        """
        거래 개선 제안서 생성 시점 리마인더.

        봇 자체는 에이전트를 직접 호출할 수 없으므로
        Claude Code 세션에서 사용자가 `/improve` 를 수동 실행하도록 안내한다.

        Args:
            mode: "weekly" | "monthly"

        Returns:
            전송 성공 여부
        """
        now_str = now_kst().strftime("%Y-%m-%d %H:%M KST")
        if mode == "weekly":
            title = "주간 개선 제안서 생성 시점"
            command = "/improve weekly"
            period = "지난 7일 매매 데이터"
            output = "docs/improvements/YYYY-Www-weekly.md"
        elif mode == "monthly":
            title = "월간 개선 제안서 생성 시점"
            command = "/improve monthly"
            period = "지난 30일 매매 데이터"
            output = "docs/improvements/YYYY-MM-monthly.md"
        else:
            logger.warning(f"알 수 없는 리마인더 모드: {mode}")
            return False

        text = (
            f"📊 *{title}*\n"
            f"`{now_str}`\n\n"
            f"Claude Code 세션에서 다음 명령을 실행하세요:\n"
            f"`{command}`\n\n"
            f"- 기간: {period}\n"
            f"- 산출물: `{output}`"
        )
        return self.send_message(text, parse_mode="Markdown")

    def send_photo(
        self,
        photo_path: str,
        caption: Optional[str] = None
    ) -> bool:
        """
        이미지 전송
        
        Args:
            photo_path: 이미지 파일 경로
            caption: 캡션
        
        Returns:
            전송 성공 여부
        """
        if not self._enabled:
            return False
        
        url = f"{self.base_url}/sendPhoto"
        
        try:
            with open(photo_path, 'rb') as photo:
                files = {"photo": photo}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption or "",
                    "parse_mode": "Markdown"
                }
                
                response = httpx.post(url, data=data, files=files, timeout=30)
                result = response.json()
                
                return result.get("ok", False)
                
        except Exception as e:
            logger.error(f"이미지 전송 오류: {e}")
            return False
    
    # ===== 시스템 알림 =====
    
    def send_system_start(self) -> bool:
        """시스템 시작 알림"""
        text = f"""
🚀 *시스템 시작*

📅 {now_kst().strftime("%Y-%m-%d %H:%M:%S")}
💻 한국 주식 AI 스윙 트레이딩 시스템

✅ 시스템이 정상 시작되었습니다.
"""
        return self.send_message(text)
    
    def send_system_stop(self, reason: str = "") -> bool:
        """시스템 종료 알림"""
        text = f"""
🔴 *시스템 종료*

📅 {now_kst().strftime("%Y-%m-%d %H:%M:%S")}
📝 사유: {reason or "정상 종료"}
"""
        return self.send_message(text)
    
    def send_error_alert(
        self,
        error_type: str,
        message: str,
        details: Optional[str] = None
    ) -> bool:
        """
        에러 알림
        
        Args:
            error_type: 에러 유형
            message: 에러 메시지
            details: 상세 정보
        
        Returns:
            전송 성공 여부
        """
        text = f"""
🚨 *에러 발생*

⚠️ 유형: {error_type}
📝 메시지: {message}
📅 시간: {now_kst().strftime("%H:%M:%S")}
"""
        if details:
            text += f"\n📋 상세:\n```\n{details[:500]}\n```"
        
        return self.send_message(text)
    
    # ===== 매매 알림 =====
    
    def send_buy_alert(
        self,
        stock_name: str,
        stock_code: str,
        quantity: int,
        price: int,
        theme: Optional[str] = None,
        score: Optional[float] = None
    ) -> bool:
        """
        매수 완료 알림
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            quantity: 수량
            price: 매수가
            theme: 테마
            score: 점수
        
        Returns:
            전송 성공 여부
        """
        amount = quantity * price
        
        text = f"""
🟢 *매수 완료*

📈 {stock_name} ({stock_code})
💰 {quantity}주 × {price:,}원 = {amount:,}원
"""
        if theme:
            text += f"🏷️ 테마: {theme}\n"
        if score:
            text += f"⭐ 점수: {score:.1f}\n"
        
        text += f"📅 {now_kst().strftime('%H:%M:%S')}"
        
        return self.send_message(text)
    
    def send_sell_alert(
        self,
        stock_name: str,
        stock_code: str,
        quantity: int,
        buy_price: int,
        sell_price: int,
        reason: str
    ) -> bool:
        """
        매도 완료 알림
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            quantity: 수량
            buy_price: 매수가
            sell_price: 매도가
            reason: 매도 사유
        
        Returns:
            전송 성공 여부
        """
        profit = (sell_price - buy_price) * quantity
        profit_rate = (sell_price - buy_price) / buy_price * 100
        
        # 수익/손실에 따른 이모지
        emoji = "🔺" if profit >= 0 else "🔻"
        color = "🟢" if profit >= 0 else "🔴"
        
        text = f"""
{color} *매도 완료*

📉 {stock_name} ({stock_code})
💰 {quantity}주 × {sell_price:,}원
📝 사유: {reason}

{emoji} *손익*
매수가: {buy_price:,}원
매도가: {sell_price:,}원
수익금: {profit:+,}원 ({profit_rate:+.2f}%)

📅 {now_kst().strftime('%H:%M:%S')}
"""
        return self.send_message(text)
    
    def send_stop_loss_alert(
        self,
        stock_name: str,
        buy_price: int,
        sell_price: int,
        profit_rate: float
    ) -> bool:
        """손절 알림"""
        if profit_rate >= 0:
            pnl_emoji = "🔺"
            pnl_label = "수익"
        else:
            pnl_emoji = "🔻"
            pnl_label = "손실"

        text = f"""
🔻 *손절 발동*

📉 {stock_name}
💰 매수가: {buy_price:,}원 → 매도가: {sell_price:,}원
{pnl_emoji} {pnl_label}: {abs(profit_rate):.2f}%

⚠️ 손절가에 도달하여 자동 매도되었습니다.
"""
        return self.send_message(text)
    
    def send_take_profit_alert(
        self,
        stock_name: str,
        buy_price: int,
        sell_price: int,
        profit_rate: float
    ) -> bool:
        """익절 알림"""
        text = f"""
🔺 *익절 발동*

📈 {stock_name}
💰 매수가: {buy_price:,}원 → 매도가: {sell_price:,}원
📊 수익: {profit_rate:.2f}%

✅ 익절가에 도달하여 자동 매도되었습니다.
"""
        return self.send_message(text)
    
    # ===== 매매 사후 분석 알림 =====

    def send_post_trade_report(self, results: list[dict]) -> bool:
        """
        매매 사후 분석 결과 알림 (정량 데이터 포함)

        Args:
            results: PostTradeAnalyzer.run_daily_analysis() 반환값
                     각 항목에 post_prices(D+1~D+5 주가 리스트), sell_price 포함

        Returns:
            전송 성공 여부
        """
        if not results:
            return False

        text = f"🔍 *매매 사후 분석*\n📅 {now_kst().strftime('%Y-%m-%d')}\n\n"

        for r in results[:5]:
            profit_rate = r.get("profit_rate") or 0
            pnl_emoji = "🔺" if profit_rate >= 0 else "🔻"
            score = r.get("timing_score", 5)
            score_bar = "⭐" * min(score // 2, 5)

            text += (
                f"{pnl_emoji} *{r.get('stock_name', '')}* ({r.get('stock_code', '')})\n"
                f"  수익률: {profit_rate:+.1f}% | 사유: {r.get('sell_reason', '')}\n"
            )

            # 정량 데이터: 매도 후 주가 추이
            prices = r.get("post_prices") or []
            sell_price = r.get("sell_price") or 0

            if prices and sell_price > 0:
                # D+5 종가 (마지막 데이터)
                last = prices[-1]
                d5_close = last.get("close_price", 0)
                d5_change = last.get("change_from_sell", 0)

                # D+1~D+5 중 최고가/최저가
                max_high = max((p.get("high_price", 0) for p in prices), default=0)
                min_low = min((p.get("low_price", 0) for p in prices if p.get("low_price", 0) > 0), default=0)
                max_high_chg = ((max_high - sell_price) / sell_price * 100) if max_high > 0 else 0
                min_low_chg = ((min_low - sell_price) / sell_price * 100) if min_low > 0 else 0

                # 방향 판단
                if d5_change > 2:
                    trend = "↗️ 상승"
                elif d5_change < -2:
                    trend = "↘️ 하락"
                else:
                    trend = "➡️ 횡보"

                text += f"  매도가: {sell_price:,.0f}원 → D+{last['days_after_sell']}: {d5_close:,.0f}원 ({d5_change:+.1f}%) {trend}\n"
                text += f"  D+5 최고: {max_high:,.0f}원 ({max_high_chg:+.1f}%) | 최저: {min_low:,.0f}원 ({min_low_chg:+.1f}%)\n"

                # 일별 추이 요약 (D+1, D+3, D+5 또는 있는 데이터만)
                trail_parts = []
                for p in prices:
                    day = p["days_after_sell"]
                    chg = p.get("change_from_sell", 0)
                    trail_parts.append(f"D+{day} {chg:+.1f}%")
                text += f"  추이: {' → '.join(trail_parts)}\n"

            lesson_safe = (r.get('lesson', 'N/A') or 'N/A').replace('_', '\\_').replace('`', "'")
            text += (
                f"  타이밍: {score}/10 {score_bar} | 평가: {r.get('overall', 'N/A')}\n"
                f"  교훈: {lesson_safe}\n\n"
            )

        if len(results) > 5:
            text += f"... 외 {len(results) - 5}건\n"

        return self.send_message(text)

    # ===== 리포트 전송 =====
    
    def send_daily_report(
        self,
        portfolio: list[dict],
        metrics: dict,
        themes: list[dict] = None,
        ai_analysis: list[dict] = None,
        today_trades: list[dict] = None,
        realized_trades: list[dict] = None,
        total_capital: int = 0
    ) -> bool:
        """
        일일 성과 리포트 전송

        Args:
            portfolio: 포트폴리오
            metrics: 성과 지표
            themes: 오늘 선정된 테마 (선정 이유 포함)
            ai_analysis: AI 분석 결과 (선정 이유 포함)
            today_trades: 오늘 거래 내역
            realized_trades: 전체 매도 기록 (실현 손익 계산용)
            total_capital: 투입 자본금

        Returns:
            전송 성공 여부
        """
        # 포트폴리오 통계 (KIS API: quantity/current_amount/buy_amount)
        total_value = sum(
            p.get("current_amount", 0) or p.get("quantity", p.get("shares", 0)) * p.get("current_price", 0)
            for p in portfolio
        )
        total_cost = sum(
            p.get("buy_amount", 0) or p.get("quantity", p.get("shares", 0)) * p.get("buy_price", 0)
            for p in portfolio
        )
        unrealized_pnl = total_value - total_cost
        unrealized_rate = (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0

        # 실현 손익 계산
        realized_pnl = int(sum(t.get("profit_amount") or 0 for t in (realized_trades or [])))
        realized_wins = [t for t in (realized_trades or []) if (t.get("profit_amount") or 0) > 0]
        realized_losses = [t for t in (realized_trades or []) if (t.get("profit_amount") or 0) < 0]

        # 총 자본 대비 수익률
        cash_remaining = total_capital - total_cost if total_capital > 0 else 0
        current_total = cash_remaining + total_value + realized_pnl
        total_return = ((current_total - total_capital) / total_capital * 100) if total_capital > 0 else 0

        # 상위/하위 종목
        sorted_positions = sorted(
            portfolio,
            key=lambda x: x.get("profit_rate") or 0,
            reverse=True
        )

        best_3 = sorted_positions[:3]
        worst_3 = [p for p in reversed(sorted_positions[-3:]) if (p.get("profit_rate") or 0) < 0]

        text = f"""📊 *일일 성과 리포트*
📅 {now_kst().strftime('%Y-%m-%d')}

💰 *포트폴리오 (보유 {len(portfolio)}종목)*
```
총 평가액: {total_value:>12,}원
총 투자액: {total_cost:>12,}원
평가 손익: {unrealized_pnl:>+12,}원 ({unrealized_rate:+.2f}%)
```
"""
        # 실현 손익
        text += f"""
💵 *실현 손익*
```
실현 수익: {realized_pnl:>+12,}원
  승: {len(realized_wins)}건  패: {len(realized_losses)}건
```
"""
        # 총 자본 대비 수익률
        if total_capital > 0:
            text += f"""
🏦 *투입 자본 대비*
```
투입 자본: {total_capital:>12,}원
현재 자산: {current_total:>12,}원
총 수익률: {total_return:>+11.2f}%
```
"""

        # 테마 선정 이유 추가
        if themes:
            text += "\n🎯 *오늘의 테마*\n"
            for i, t in enumerate(themes[:3], 1):
                theme_name = t.get("theme", t.get("name", ""))
                score = t.get("total_score", t.get("score", 0))
                reason = t.get("selection_reason", "")[:35]
                text += f"  {i}. {theme_name} ({score:.0f}점)\n"
                if reason:
                    text += f"     └ {reason}\n"

        # 오늘 거래 + AI 분석 이유
        if today_trades:
            buys = [t for t in today_trades if t.get("action") == "buy"]
            sells = [t for t in today_trades if t.get("action") == "sell"]

            if buys:
                text += "\n🟢 *오늘 매수*\n"
                for t in buys[:4]:
                    stock_name = t.get('stock_name', '')
                    ai_reason = ""
                    if ai_analysis:
                        for a in ai_analysis:
                            if a.get("stock_code") == t.get("stock_code") or a.get("stock_name") == stock_name:
                                ai_reason = a.get("ai_summary", a.get("reason", ""))[:40]
                                break
                    text += f"  • {stock_name}\n"
                    if ai_reason:
                        text += f"    └ {ai_reason}\n"

            if sells:
                text += "\n🔴 *오늘 매도*\n"
                for t in sells[:3]:
                    pnl = t.get('profit_amount') or t.get('pnl_amount') or 0
                    pnl_str = f" ({pnl:+,}원)" if pnl else ""
                    text += f"  • {t.get('stock_name')}: {t.get('reason', '')[:25]}{pnl_str}\n"

        text += "\n🔥 *Best 3*\n"
        for i, p in enumerate(best_3, 1):
            pct = p.get("profit_rate") or 0
            text += f"  {i}. {p.get('stock_name', '')}: {pct:+.1f}%\n"

        if worst_3:
            text += "\n😰 *Worst 3*\n"
            for i, p in enumerate(worst_3, 1):
                pct = p.get("profit_rate") or 0
                text += f"  {i}. {p.get('stock_name', '')}: {pct:+.1f}%\n"

        text += f"""
📈 *성과 지표*
  • 샤프 비율: {metrics.get('sharpe_ratio', 0):.2f}
  • MDD: {metrics.get('mdd', 0):.2%}
  • 승률: {metrics.get('win_rate', 0):.1%}
"""
        return self.send_message(text)
    
    def send_weekly_report(
        self,
        weekly_data: dict
    ) -> bool:
        """
        주간 리포트 전송
        
        Args:
            weekly_data: 주간 데이터
        
        Returns:
            전송 성공 여부
        """
        start_date = weekly_data.get("start_date", "")
        end_date = weekly_data.get("end_date", "")
        start_value = weekly_data.get("start_value", 0)
        end_value = weekly_data.get("end_value", 0)
        metrics = weekly_data.get("metrics", {})
        
        weekly_profit = end_value - start_value
        weekly_return = (weekly_profit / start_value * 100) if start_value > 0 else 0
        
        emoji = "📈" if weekly_return >= 0 else "📉"
        
        text = f"""
📊 *주간 성과 리포트*
📅 {start_date} ~ {end_date}

{emoji} *주간 성과*
```
주초: {start_value:>12,}원
주말: {end_value:>12,}원
수익: {weekly_profit:>+12,}원
수익률: {weekly_return:>+9.2f}%
```

📈 *누적 성과*
  • 총 수익률: {metrics.get('total_return', 0):+.2%}
  • 샤프 비율: {metrics.get('sharpe_ratio', 0):.2f}
  • MDD: {metrics.get('mdd', 0):.2%}
  • 승률: {metrics.get('win_rate', 0):.1%}
  • 손익비: {metrics.get('payoff_ratio', 0):.2f}
"""
        return self.send_message(text)
    
    # ===== 테스트 =====
    
    def send_test_message(self) -> bool:
        """테스트 메시지 전송"""
        text = f"""
🧪 *테스트 메시지*

📅 {now_kst().strftime("%Y-%m-%d %H:%M:%S")}
✅ 텔레그램 연결 정상!
"""
        return self.send_message(text)

    # ===== 명령어 리스너 =====

    def _is_authorized(self, chat_id: int) -> bool:
        """chat_id가 허가된 사용자인지 확인"""
        try:
            return chat_id == int(self.chat_id)
        except (ValueError, TypeError):
            return False

    def _is_rate_limited(self, chat_id: int) -> bool:
        """chat_id별 레이트 리밋 확인 (쿨다운 내이면 True)"""
        now = time.monotonic()
        last = self._rate_limit.get(chat_id, 0)
        if now - last < self._rate_limit_seconds:
            return True
        self._rate_limit[chat_id] = now
        return False

    async def start_command_listener(self) -> None:
        """
        텔레그램 명령어 리스너 시작 (getUpdates long polling)

        /portfolio 명령어를 수신하면 현재 포트폴리오 현황을 응답합니다.
        """
        if not self._enabled:
            logger.info("텔레그램 비활성 — 명령어 리스너 스킵")
            return

        self._listening = True
        offset = 0
        logger.info("📱 텔레그램 명령어 리스너 시작")

        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        try:
            while self._listening:
                try:
                    url = f"{self.base_url}/getUpdates"
                    resp = await client.post(url, json={
                        "offset": offset, "timeout": 30, "allowed_updates": ["message"]
                    })
                    data = resp.json()

                    if not data.get("ok"):
                        logger.warning(f"getUpdates 실패: {data.get('description', '')}")
                        await asyncio.sleep(5)
                        continue

                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message", {})
                        text = message.get("text", "")
                        chat_id = message.get("chat", {}).get("id")

                        if not chat_id:
                            continue
                        raw_text = text.strip()
                        cmd = raw_text.split()[0].lower() if raw_text else ""

                        # 인가되지 않은 사용자 차단
                        if not self._is_authorized(chat_id):
                            logger.warning(f"⚠️ 미인가 텔레그램 요청 차단: chat_id={chat_id}, cmd={cmd}")
                            continue

                        # 레이트 리밋 초과
                        if self._is_rate_limited(chat_id):
                            logger.debug(f"레이트 리밋: chat_id={chat_id}")
                            continue

                        if cmd == "/portfolio":
                            logger.info(f"📱 /portfolio 명령어 수신 (chat_id={chat_id})")
                            await self._handle_portfolio_command(chat_id)
                        elif cmd == "/pause":
                            logger.info(f"📱 /pause 명령어 수신 (chat_id={chat_id})")
                            self._handle_pause_command(chat_id)
                        elif cmd == "/resume":
                            logger.info(f"📱 /resume 명령어 수신 (chat_id={chat_id})")
                            self._handle_resume_command(chat_id)
                        elif cmd == "/status":
                            logger.info(f"📱 /status 명령어 수신 (chat_id={chat_id})")
                            self._handle_status_command(chat_id)
                        elif cmd == "/sell" and raw_text.lower() != "/sellall":
                            logger.info(f"📱 /sell 명령어 수신 (chat_id={chat_id})")
                            await self._handle_sell_command(chat_id, raw_text)
                        elif cmd == "/sellall":
                            logger.info(f"📱 /sellall 명령어 수신 (chat_id={chat_id})")
                            await self._handle_sell_all_command(chat_id)
                        elif cmd == "/confirm":
                            logger.info(f"📱 /confirm 명령어 수신 (chat_id={chat_id})")
                            await self._handle_confirm_command(chat_id)
                        elif cmd == "/cancel":
                            logger.info(f"📱 /cancel 명령어 수신 (chat_id={chat_id})")
                            self._handle_cancel_command(chat_id)
                        elif cmd in ("/help", "/start"):
                            self._send_to_chat(chat_id,
                                "📋 사용 가능한 명령어\n\n"
                                "/portfolio - 보유 종목 실시간 수익률\n"
                                "/sell [종목코드] [수량] - 개별 종목 매도\n"
                                "/sellall - 전 종목 시장가 매도\n"
                                "/confirm - 매도 확인 (30초 내)\n"
                                "/cancel - 매도 취소\n"
                                "/pause - 매매 일시정지\n"
                                "/resume - 매매 재개\n"
                                "/status - 시스템 상태 확인\n"
                                "/help - 명령어 목록"
                            )

                except httpx.ReadTimeout:
                    # long polling timeout — 정상
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"명령어 리스너 오류: {e}")
                    await asyncio.sleep(5)
        finally:
            try:
                await client.aclose()
            except Exception:
                pass

        logger.info("📱 텔레그램 명령어 리스너 종료")

    def _handle_pause_command(self, chat_id: int) -> None:
        """/pause 명령어 처리 — 매매 일시정지"""
        if not self._system_ref:
            self._send_to_chat(chat_id, "시스템 참조 없음")
            return
        if self._system_ref.trading_paused:
            self._send_to_chat(chat_id, "⏸️ 이미 일시정지 상태입니다.\n/resume 으로 재개")
            return
        self._system_ref.trading_paused = True
        logger.info("⏸️ 매매 일시정지 활성화 (텔레그램 명령)")
        self._send_to_chat(chat_id,
            "⏸️ 매매 일시정지 활성화\n\n"
            "중단 항목:\n"
            "  • 08:30 테마 분석\n"
            "  • 09:05 종목 스크리닝\n"
            "  • 09:25 자동 매수\n\n"
            "유지 항목:\n"
            "  • 보유 종목 모니터링 (손절/트레일링)\n"
            "  • 17:05 일별 테마 수집\n"
            "  • 일일 리포트/사후 분석\n\n"
            "/resume 으로 매매 재개"
        )

    def _handle_resume_command(self, chat_id: int) -> None:
        """/resume 명령어 처리 — 매매 재개"""
        if not self._system_ref:
            self._send_to_chat(chat_id, "시스템 참조 없음")
            return
        if not self._system_ref.trading_paused:
            self._send_to_chat(chat_id, "▶️ 이미 정상 운영 중입니다.")
            return
        self._system_ref.trading_paused = False
        logger.info("▶️ 매매 재개 (텔레그램 명령)")
        self._send_to_chat(chat_id, "▶️ 매매 재개 완료!\n\n다음 스케줄부터 정상 매매합니다.")

    def _handle_status_command(self, chat_id: int) -> None:
        """/status 명령어 처리 — 시스템 상태 확인"""
        status = "⏸️ 일시정지" if (self._system_ref and self._system_ref.trading_paused) else "▶️ 정상 운영"
        positions = 0
        try:
            from database import Database
            db = Database()
            db.connect()
            try:
                cursor = db.conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM portfolio WHERE status='holding'")
                positions = cursor.fetchone()[0]
            finally:
                db.close()
        except Exception:
            pass
        self._send_to_chat(chat_id,
            f"📊 시스템 상태\n\n"
            f"상태: {status}\n"
            f"보유 종목: {positions}개\n"
            f"시각: {now_kst().strftime('%H:%M KST')}\n\n"
            f"/pause - 매매 정지\n"
            f"/resume - 매매 재개"
        )

    # ===== 매도 명령어 핸들러 =====

    async def _handle_sell_command(self, chat_id: int, text: str) -> None:
        """/sell [종목코드] [수량] 명령어 처리"""
        import re
        parts = text.strip().split()

        if len(parts) < 2:
            self._send_to_chat(chat_id, "사용법: /sell [종목코드] [수량]\n예: /sell 005930 50\n수량 생략 시 전량 매도")
            return

        stock_code = parts[1]
        if not re.match(r'^\d{6}$', stock_code):
            self._send_to_chat(chat_id, "종목코드는 6자리 숫자여야 합니다.\n예: /sell 005930")
            return

        # DB 보유 확인
        try:
            from database import Database
            db = Database()
            db.connect()
            try:
                holdings = db.get_portfolio(status="holding")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"/sell DB 조회 오류: {e}")
            self._send_to_chat(chat_id, "DB 조회 오류가 발생했습니다.")
            return

        pos = next((h for h in holdings if h["stock_code"] == stock_code), None)
        if not pos:
            self._send_to_chat(chat_id, f"{stock_code} 종목을 보유하고 있지 않습니다.")
            return

        stock_name = pos["stock_name"]
        total_shares = pos.get("shares") or 0

        # 수량 파싱
        quantity = total_shares
        if len(parts) >= 3:
            try:
                quantity = int(parts[2])
            except ValueError:
                self._send_to_chat(chat_id, "수량은 숫자로 입력해주세요.\n예: /sell 005930 50")
                return

        if quantity <= 0:
            self._send_to_chat(chat_id, "수량은 1 이상이어야 합니다.")
            return

        if quantity > total_shares:
            self._send_to_chat(chat_id, f"보유 수량({total_shares}주)보다 많습니다.")
            return

        # pending 저장 (30초 TTL)
        self._pending_sell = {
            "stock_code": stock_code,
            "quantity": quantity,
            "stock_name": stock_name,
        }
        self._pending_sell_all = False
        self._pending_sell_expires = time.monotonic() + self._confirm_timeout_sec

        qty_text = f"{quantity}주" if quantity < total_shares else f"전량({total_shares}주)"
        self._send_to_chat(chat_id,
            f"🔔 매도 확인 요청\n\n"
            f"{stock_name}({stock_code}) {qty_text} 시장가 매도\n\n"
            f"{self._confirm_timeout_sec}초 내 /confirm 입력 시 실행\n"
            f"/cancel 로 취소"
        )

    async def _handle_sell_all_command(self, chat_id: int) -> None:
        """/sellall 명령어 처리"""
        try:
            from database import Database
            db = Database()
            db.connect()
            try:
                holdings = db.get_portfolio(status="holding")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"/sellall DB 조회 오류: {e}")
            self._send_to_chat(chat_id, "DB 조회 오류가 발생했습니다.")
            return

        if not holdings:
            self._send_to_chat(chat_id, "보유 종목이 없습니다.")
            return

        # pending 저장 (30초 TTL)
        self._pending_sell = None
        self._pending_sell_all = True
        self._pending_sell_expires = time.monotonic() + self._confirm_timeout_sec

        lines = []
        for h in holdings:
            lines.append(f"  - {h['stock_name']} {h.get('shares', 0)}주")

        self._send_to_chat(chat_id,
            f"⚠️ 전 종목 매도 확인\n\n"
            f"보유 {len(holdings)}종목 전량 시장가 매도\n"
            + "\n".join(lines) + "\n\n"
            f"{self._confirm_timeout_sec}초 내 /confirm 입력 시 실행\n"
            f"/cancel 로 취소"
        )

    async def _handle_confirm_command(self, chat_id: int) -> None:
        """/confirm 명령어 처리"""
        has_pending = self._pending_sell is not None or self._pending_sell_all

        if not has_pending:
            self._send_to_chat(chat_id, "대기 중인 매도 주문이 없습니다.\n/sell 또는 /sellall 을 먼저 입력하세요.")
            return

        if time.monotonic() > self._pending_sell_expires:
            self._pending_sell = None
            self._pending_sell_all = False
            self._send_to_chat(chat_id, "⏰ 확인 시간이 만료되었습니다.\n다시 /sell 또는 /sellall 을 입력해주세요.")
            return

        try:
            from web.dashboard_service import execute_sell, execute_sell_all

            if self._pending_sell_all:
                self._pending_sell_all = False
                self._pending_sell_expires = 0
                self._send_to_chat(chat_id, "⏳ 전 종목 매도 실행 중...")
                result = await execute_sell_all(reason="텔레그램 수동 매도")

                if result.get("success"):
                    lines = []
                    for r in result.get("results", []):
                        stock_name = r.get("stock_name", "")
                        success = "✅" if r.get("success") else "❌"
                        lines.append(f"  {success} {stock_name}")
                    self._send_to_chat(chat_id,
                        f"✅ 전 종목 매도 완료\n\n"
                        + "\n".join(lines)
                    )
                else:
                    fail_count = result.get("fail_count", 0)
                    lines = []
                    for r in result.get("results", []):
                        stock_name = r.get("stock_name", "")
                        success = "✅" if r.get("success") else "❌"
                        lines.append(f"  {success} {stock_name}")
                    self._send_to_chat(chat_id,
                        f"⚠️ 매도 부분 실패 ({fail_count}건 실패)\n\n"
                        + "\n".join(lines)
                    )

            elif self._pending_sell:
                sell_info = self._pending_sell
                self._pending_sell = None
                self._pending_sell_expires = 0

                self._send_to_chat(chat_id, f"⏳ {sell_info['stock_name']} 매도 실행 중...")
                result = await execute_sell(
                    sell_info["stock_code"],
                    sell_info["quantity"],
                    reason="텔레그램 수동 매도"
                )

                if result.get("success"):
                    price = result.get("price", 0)
                    msg = f"✅ 매도 완료\n\n{sell_info['stock_name']}({sell_info['stock_code']}) {sell_info['quantity']}주"
                    if price > 0:
                        msg += f"\n체결가: {price:,}원"
                    self._send_to_chat(chat_id, msg)
                else:
                    error_msg = result.get("message", "알 수 없는 오류")
                    self._send_to_chat(chat_id, f"❌ 매도 실패: {error_msg}")

        except Exception as e:
            logger.error(f"/confirm 처리 오류: {e}", exc_info=True)
            self._send_to_chat(chat_id, f"⚠️ 매도 처리 중 오류 발생: {e}")
            self._pending_sell = None
            self._pending_sell_all = False

    def _handle_cancel_command(self, chat_id: int) -> None:
        """/cancel 명령어 처리"""
        had_pending = self._pending_sell is not None or self._pending_sell_all
        self._pending_sell = None
        self._pending_sell_all = False
        self._pending_sell_expires = 0

        if had_pending:
            self._send_to_chat(chat_id, "🚫 매도 주문이 취소되었습니다.")
        else:
            self._send_to_chat(chat_id, "대기 중인 매도 주문이 없습니다.")

    async def _handle_portfolio_command(self, chat_id: int) -> None:
        """
        /portfolio 명령어 처리 — 보유 종목 실시간 수익률 응답

        Args:
            chat_id: 응답할 채팅 ID
        """
        try:
            from database import Database
            from modules.stock_screener.kis_api import KISApi

            db = Database()
            db.connect()
            try:
                holdings = db.get_portfolio(status='holding')
                sell_trades = db.get_all_sell_trades()
            finally:
                db.close()

            # 실현 손익
            realized_pnl = int(sum(t.get("profit_amount") or 0 for t in sell_trades))
            realized_sign = "+" if realized_pnl >= 0 else ""

            if not holdings:
                text = (
                    f"📊 포트폴리오 현황\n"
                    f"📅 {now_kst().strftime('%Y-%m-%d %H:%M KST')}\n\n"
                    f"보유 종목이 없습니다.\n\n"
                    f"💵 실현 손익: {realized_sign}{realized_pnl:,}원 ({len(sell_trades)}건)"
                )
                self._send_to_chat(chat_id, text)
                return

            kis = KISApi()
            lines = []
            total_invest = 0
            total_eval = 0

            for i, h in enumerate(holdings):
                stock_code = h['stock_code']
                stock_name = h['stock_name']
                shares = h.get('shares') or 0
                buy_price = int(h.get('buy_price') or 0)

                # 실시간 가격 조회 (이벤트 루프 블로킹 방지)
                price_info = await asyncio.to_thread(kis.get_current_price, stock_code)
                if price_info:
                    current_price = price_info.get('price', buy_price)
                else:
                    # API 실패 시 DB에 저장된 current_price 폴백
                    current_price = int(h.get('current_price') or buy_price)

                invest = shares * buy_price
                eval_amount = shares * current_price
                profit = eval_amount - invest
                profit_rate = (profit / invest * 100) if invest > 0 else 0

                total_invest += invest
                total_eval += eval_amount

                # 첫 번째/마지막/중간 종목에 따라 구분선
                if i == 0:
                    prefix = "┌"
                elif i == len(holdings) - 1:
                    prefix = "└"
                else:
                    prefix = "├"

                pnl_sign = "+" if profit >= 0 else ""
                lines.append(
                    f"{prefix} {stock_name} ({stock_code})\n"
                    f"{'│' if i < len(holdings) - 1 else ' '} "
                    f"{shares}주 x {buy_price:,}원 -> {current_price:,}원\n"
                    f"{'│' if i < len(holdings) - 1 else ' '} "
                    f"수익: {pnl_sign}{profit:,}원 ({pnl_sign}{profit_rate:.1f}%)"
                )

            unrealized_pnl = total_eval - total_invest
            unrealized_rate = (unrealized_pnl / total_invest * 100) if total_invest > 0 else 0
            unrealized_sign = "+" if unrealized_pnl >= 0 else ""

            # 투입 자본 대비 총 수익률
            total_capital = settings.TOTAL_CAPITAL
            cash_remaining = max(0, total_capital - total_invest)
            current_total = cash_remaining + total_eval + realized_pnl
            total_return = ((current_total - total_capital) / total_capital * 100) if total_capital > 0 else 0
            total_return_sign = "+" if total_return >= 0 else ""

            now_str = now_kst().strftime("%Y-%m-%d %H:%M KST")
            text = (
                f"📊 포트폴리오 현황\n"
                f"📅 {now_str}\n\n"
                + "\n".join(lines)
                + f"\n\n💰 총 투자: {total_invest:,}원\n"
                f"💰 총 평가: {total_eval:,}원\n"
                f"📈 평가 손익: {unrealized_sign}{unrealized_pnl:,}원 ({unrealized_sign}{unrealized_rate:.2f}%)\n"
                f"💵 실현 손익: {realized_sign}{realized_pnl:,}원 ({len(sell_trades)}건)\n\n"
                f"🏦 투입 자본: {total_capital:,}원\n"
                f"🏦 현재 자산: {current_total:,}원 ({total_return_sign}{total_return:.2f}%)"
            )

            self._send_to_chat(chat_id, text)

        except Exception as e:
            logger.error(f"/portfolio 처리 오류: {e}", exc_info=True)
            self._send_to_chat(chat_id, "⚠️ 일시적 오류 — 잠시 후 다시 시도해주세요")

    def _send_to_chat(self, chat_id: int, text: str) -> bool:
        """특정 chat_id로 메시지 전송 (명령어 응답용)"""
        url = f"{self.base_url}/sendMessage"
        data = {"chat_id": chat_id, "text": text}

        try:
            response = httpx.post(url, json=data, timeout=10)
            result = response.json()
            if not result.get("ok"):
                logger.warning(f"chat_id={chat_id} 응답 실패: {result.get('description', '')}")
                return False
            return True
        except Exception as e:
            logger.error(f"chat_id={chat_id} 응답 오류: {e}")
            return False

    def stop_command_listener(self) -> None:
        """명령어 리스너 중지"""
        self._listening = False
        logger.info("📱 텔레그램 명령어 리스너 중지 요청")


# ===== 편의 함수 =====

def send_telegram_message(text: str) -> bool:
    """텔레그램 메시지 전송 (편의 함수)"""
    notifier = TelegramNotifier()
    return notifier.send_message(text)


def send_telegram_error(error_type: str, message: str) -> bool:
    """텔레그램 에러 알림 (편의 함수)"""
    notifier = TelegramNotifier()
    return notifier.send_error_alert(error_type, message)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📱 텔레그램 알림 테스트")
    print("=" * 60)
    
    notifier = TelegramNotifier()
    
    if not notifier._enabled:
        print("\n⚠️ 텔레그램 설정이 없습니다.")
        print("   .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정하세요.")
        print("\n메시지 생성 테스트만 진행합니다:")
    
    # 일일 리포트 예시 생성
    test_portfolio = [
        {"stock_name": "삼성전자", "shares": 10, "buy_price": 75000, "current_price": 77000, "profit_rate": 2.67},
        {"stock_name": "SK하이닉스", "shares": 5, "buy_price": 195000, "current_price": 190000, "profit_rate": -2.56},
        {"stock_name": "LG에너지솔루션", "shares": 2, "buy_price": 420000, "current_price": 450000, "profit_rate": 7.14},
    ]
    
    test_metrics = {
        "sharpe_ratio": 1.85,
        "mdd": -0.08,
        "win_rate": 0.65,
        "total_return": 0.045,
        "payoff_ratio": 2.1
    }
    
    print("\n일일 리포트 메시지 예시:")
    print("-" * 40)
    
    # 메시지 내용만 출력
    total_value = sum(p["shares"] * p["current_price"] for p in test_portfolio)
    total_cost = sum(p["shares"] * p["buy_price"] for p in test_portfolio)
    print(f"총 평가액: {total_value:,}원")
    print(f"총 투자액: {total_cost:,}원")
    print(f"수익금: {total_value - total_cost:+,}원")
    
    if notifier._enabled:
        print("\n테스트 메시지 전송 중...")
        result = notifier.send_test_message()
        print(f"결과: {'성공' if result else '실패'}")
    
    print("\n" + "=" * 60)
    print("✅ 텔레그램 알림 테스트 완료!")
    print("=" * 60)
