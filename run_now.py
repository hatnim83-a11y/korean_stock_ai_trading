"""
run_now.py - 수동 즉시 실행 스크립트

스케줄러를 거치지 않고 분석→매수→모니터링 파이프라인을 즉시 실행합니다.
실행 후 모니터링은 15:30까지 자동으로 유지됩니다.

사용법:
    python run_now.py --real     # 실전 투자
    python run_now.py --test     # 테스트 모드 (주문 안함)

주의: systemd 서비스(trading_system)가 실행 중이면 이중 봇 방지를 위해
      자동으로 서비스를 중지한 후 수동 실행합니다.
"""

import asyncio
import argparse
import subprocess
import sys
from datetime import datetime

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from logger import logger
from config import now_kst
from main import TradingSystem, acquire_pid_lock, release_pid_lock, PID_FILE


def check_systemd_service() -> bool:
    """systemd 서비스 실행 여부 확인. True면 실행 중."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "trading_system"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def stop_systemd_service() -> bool:
    """systemd 서비스 중지. 성공 시 True."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", "trading_system"],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] 서비스 중지 실패: {e}")
        return False


def start_systemd_service() -> bool:
    """systemd 서비스 시작. 성공 시 True."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", "trading_system"],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] 서비스 시작 실패: {e}")
        return False


async def run_pipeline():
    parser = argparse.ArgumentParser(description="수동 즉시 실행")
    parser.add_argument("--real", action="store_true", help="실전투자 모드")
    parser.add_argument("--test", action="store_true", help="테스트 모드")
    args = parser.parse_args()

    # === 이중 봇 방지 ===
    service_was_running = False
    if check_systemd_service():
        print("[WARNING] systemd 서비스(trading_system)가 실행 중입니다.")
        print("   이중 봇 방지를 위해 서비스를 중지합니다...")
        if stop_systemd_service():
            service_was_running = True
            print("   [OK] 서비스 중지 완료")
        else:
            print("   [ERROR] 서비스 중지 실패. sudo 권한을 확인하세요.")
            print("   수동 중지: sudo systemctl stop trading_system")
            sys.exit(1)

    # PID 락 확인
    if not acquire_pid_lock():
        old_pid = PID_FILE.read_text().strip() if PID_FILE.exists() else "?"
        print(f"[ERROR] 트레이딩 시스템이 이미 실행 중입니다 (PID: {old_pid})")
        print(f"   확인: ps -p {old_pid}")
        print(f"   강제 해제: rm {PID_FILE}")
        sys.exit(1)

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
    from datetime import time as dt_time

    try:
        while True:
            current = now_kst()
            # 15:30 KST 이후면 종료
            if current.time() >= dt_time(15, 30):
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

    # PID 락 해제
    release_pid_lock()

    # systemd 서비스 복원
    if service_was_running:
        logger.info("\n🔄 systemd 서비스를 재시작합니다...")
        if start_systemd_service():
            logger.info("   [OK] 서비스 재시작 완료")
        else:
            logger.warning("   [WARNING] 서비스 재시작 실패!")
            logger.warning("   수동 시작: sudo systemctl start trading_system")

    logger.info("\n✅ 수동 실행 완료")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
