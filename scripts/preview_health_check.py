"""
preview_health_check.py - 16:10 일일 헬스체크 메시지 미리보기

운영 DB를 그대로 사용해 헬스체크 헬퍼 11개 + 본체를 호출하지만
텔레그램 전송은 차단(MagicMock)하고 stdout으로만 출력.

5/8 16:10 KST 자연 발화 전에 운영 환경에서 발생할 메시지 미리 확인.

실행:
    python scripts/preview_health_check.py
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import TradingSystem


async def main():
    # __init__ 우회 (KIS API/텔레그램 초기화 비용 회피)
    sys_obj = TradingSystem.__new__(TradingSystem)

    # 인스턴스 변수 (헬퍼들이 참조)
    sys_obj._midweek_dropped_theme = None
    sys_obj._midweek_new_theme = None
    sys_obj._last_theme_rotation_date = None
    sys_obj.trading_paused = False
    sys_obj.today_themes = []

    # 외부 의존 객체는 mock
    sys_obj.scheduler = MagicMock()
    sys_obj.scheduler.is_running = True
    sys_obj.scheduler.scheduler.get_jobs = lambda: list(range(13))  # 13개 잡 가정
    sys_obj.trading_engine = MagicMock()
    sys_obj.trading_engine.get_balance = lambda: {"cash": 8988669}  # mock 잔고

    # 텔레그램 메시지는 stdout으로만 출력
    captured = []
    notifier = MagicMock()
    notifier.send_message = lambda m: captured.append(m)
    sys_obj.notifier = notifier

    await sys_obj.run_daily_health_check()

    print("=" * 60)
    print("📨 텔레그램 메시지 미리보기 (실제 발송 안 됨)")
    print("=" * 60)
    if captured:
        print(captured[0])
        print("=" * 60)
        print(f"메시지 길이: {len(captured[0])}자 (텔레그램 한계 4096)")
    else:
        print("⚠️ 메시지 캡처 실패")


if __name__ == "__main__":
    asyncio.run(main())
