"""
run_now.py - 수동 즉시 실행 스크립트

스케줄러를 거치지 않고 분석→매수→모니터링 파이프라인을 즉시 실행합니다.
실행 후 모니터링은 15:30까지 자동으로 유지됩니다.

사용법:
    python run_now.py --real     # 실전 투자
    python run_now.py --test     # 테스트 모드 (주문 안함)
"""

import asyncio
import argparse
import sys
from datetime import datetime

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from logger import logger
from config import now_kst
from main import TradingSystem


async def run_pipeline():
    parser = argparse.ArgumentParser(description="수동 즉시 실행")
    parser.add_argument("--real", action="store_true", help="실전투자 모드")
    parser.add_argument("--test", action="store_true", help="테스트 모드")
    args = parser.parse_args()

    system = TradingSystem(
        use_mock=not args.real,
        test_mode=args.test
    )

    # DB 초기화
    system._init_database()

    logger.info("=" * 70)
    logger.info("🔧 수동 즉시 실행 파이프라인")
    logger.info(f"   시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   모드: {'실전투자' if args.real else '모의투자'}")
    logger.info("=" * 70)

    # Step 1: 테마 로테이션 체크
    logger.info("\n[1/5] 테마 로테이션 체크...")
    try:
        await system.check_theme_rotation()
    except Exception as e:
        logger.warning(f"테마 로테이션 체크 실패 (계속 진행): {e}")

    # Step 2: 일일 분석 (08:30 작업)
    logger.info("\n[2/5] 일일 분석 실행...")
    analysis = await system.run_daily_analysis()
    if not analysis.get("success"):
        logger.error(f"분석 실패: {analysis}")
        return

    logger.info(f"   분석 완료: 후보 {analysis.get('observation_pool', 0)}개")

    # Step 3: 장 초반 관찰 (09:00 작업)
    logger.info("\n[3/5] 장 초반 관찰...")
    await system.run_morning_observation()

    # Step 4: 매수 실행 (09:25 작업)
    logger.info("\n[4/5] 매수 실행...")
    buy_result = await system.execute_buy_orders()
    logger.info(f"   매수 결과: {buy_result}")

    # Step 5: 모니터링 시작 (09:26~15:30)
    logger.info("\n[5/5] 실시간 모니터링 시작...")
    logger.info("   15:30까지 모니터링 유지 (Ctrl+C로 종료)")

    try:
        await system.start_monitoring()
    except Exception as e:
        logger.warning(f"모니터링 시작 실패: {e}")

    # 15:30까지 대기 (모니터가 없어도 유지)
    import pytz
    from datetime import time as dt_time
    kst = pytz.timezone("Asia/Seoul")

    try:
        while True:
            now_kst = datetime.now(kst)
            # 15:30 KST 이후면 종료
            if now_kst.time() >= dt_time(15, 30):
                logger.info("15:30 도달 - 모니터링 종료")
                break
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("\n수동 종료...")

    # 정리
    try:
        await system.stop_monitoring()
    except Exception:
        pass
    try:
        await system.run_market_close()
    except Exception:
        pass
    try:
        await system.send_daily_report()
    except Exception:
        pass

    logger.info("\n✅ 수동 실행 완료")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
