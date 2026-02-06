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

from datetime import datetime, date
from typing import Optional
import json

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
        loss_rate: float
    ) -> bool:
        """손절 알림"""
        text = f"""
🔻 *손절 발동*

📉 {stock_name}
💰 매수가: {buy_price:,}원 → 매도가: {sell_price:,}원
📊 손실: {loss_rate:.2f}%

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
    
    # ===== 리포트 전송 =====
    
    def send_daily_report(
        self,
        portfolio: list[dict],
        metrics: dict,
        themes: list[dict] = None,
        ai_analysis: list[dict] = None,
        today_trades: list[dict] = None
    ) -> bool:
        """
        일일 성과 리포트 전송

        Args:
            portfolio: 포트폴리오
            metrics: 성과 지표
            themes: 오늘 선정된 테마 (선정 이유 포함)
            ai_analysis: AI 분석 결과 (선정 이유 포함)
            today_trades: 오늘 거래 내역

        Returns:
            전송 성공 여부
        """
        # 포트폴리오 통계
        total_value = sum(
            p.get("shares", 0) * p.get("current_price", p.get("buy_price", 0))
            for p in portfolio
        )
        total_cost = sum(
            p.get("shares", 0) * p.get("buy_price", 0)
            for p in portfolio
        )
        total_profit = total_value - total_cost
        profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0

        # 상위/하위 종목
        sorted_positions = sorted(
            portfolio,
            key=lambda x: x.get("profit_rate", 0),
            reverse=True
        )

        best_3 = sorted_positions[:3]
        worst_3 = [p for p in reversed(sorted_positions[-3:]) if p.get("profit_rate", 0) < 0]

        text = f"""📊 *일일 성과 리포트*
📅 {date.today()}

💰 *포트폴리오*
```
총 평가액: {total_value:>12,}원
총 투자액: {total_cost:>12,}원
오늘 수익: {total_profit:>+12,}원
수익률:    {profit_rate:>+11.2f}%
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
                    # AI 분석 이유 찾기
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
                    text += f"  • {t.get('stock_name')}: {t.get('reason', '')[:25]}\n"

        text += "\n🔥 *Best 3*\n"
        for i, p in enumerate(best_3, 1):
            pct = p.get("profit_rate", 0)
            text += f"  {i}. {p.get('stock_name', '')}: {pct:+.1f}%\n"

        if worst_3:
            text += "\n😰 *Worst 3*\n"
            for i, p in enumerate(worst_3, 1):
                pct = p.get("profit_rate", 0)
                text += f"  {i}. {p.get('stock_name', '')}: {pct:+.1f}%\n"

        text += f"""
📈 *성과 지표*
  • 샤프 비율: {metrics.get('sharpe_ratio', 0):.2f}
  • MDD: {metrics.get('mdd', 0):.2%}
  • 승률: {metrics.get('win_rate', 0):.1%}

📊 현재 {len(portfolio)}개 종목 보유 중
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
