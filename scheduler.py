"""
scheduler.py - APScheduler 스케줄링 모듈

이 파일은 트레이딩 시스템의 자동 스케줄링을 관리합니다.

일정:
- 08:00 - 테마 로테이션 체크 (2주 단위)
- 08:30 - 일일 분석 시작 (테마 분석 → 종목 스크리닝 → AI 검증 → 후보 선정)
- 09:00 - 장 초반 관찰 시작 (시초가/수급/거래량 모니터링)
- 09:25 - 자동 매수 실행 (필터링 후 최종 선정)
- 09:26~15:30 - 실시간 모니터링 (분할 익절/트레일링 스탑/손절)
- 15:35 - 장 마감 정리 (리밸런싱 준비)
- 16:00 - 일일 리포트 발송

하이브리드 전략:
- 분할 익절: +10% → 30%, +15% → 30%, +20% → 전량
- 트레일링 스탑: 최고가 -5%
- 보유 기간: 수익 14일, 손실 7일
- 테마 로테이션: 2주 단위

사용법:
    from scheduler import TradingScheduler
    
    scheduler = TradingScheduler()
    scheduler.start()
"""

import asyncio
from datetime import datetime, time as dt_time, date
from typing import Optional, Callable
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from logger import logger
from config import settings
from database import Database


class TradingScheduler:
    """
    트레이딩 스케줄러
    
    APScheduler를 사용하여 일일 트레이딩 작업을 자동화합니다.
    
    Attributes:
        scheduler: APScheduler 인스턴스
        is_running: 실행 중 여부
        
    스케줄:
        - 08:30 - 일일 분석
        - 09:00 - 자동 매수
        - 09:00~15:30 - 모니터링
        - 15:35 - 장 마감 정리
        - 16:00 - 일일 리포트
    """
    
    def __init__(self):
        """스케줄러 초기화"""
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.is_running = False
        
        # 작업 콜백
        self.on_theme_check: Optional[Callable] = None          # 08:00 테마 로테이션 체크
        self.on_daily_analysis: Optional[Callable] = None       # 08:30 일일 분석
        self.on_morning_observation: Optional[Callable] = None  # 09:00 장 초반 관찰
        self.on_execute_buy: Optional[Callable] = None          # 09:25 자동 매수
        self.on_market_close: Optional[Callable] = None         # 15:35 장 마감 정리
        self.on_daily_report: Optional[Callable] = None         # 16:00 일일 리포트
        self.on_monitoring_start: Optional[Callable] = None     # 09:26 모니터링 시작
        self.on_monitoring_stop: Optional[Callable] = None      # 15:30 모니터링 종료
        
        # 이벤트 리스너
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        
        logger.info("트레이딩 스케줄러 초기화 (V2: 하이브리드 전략)")
    
    def _on_job_executed(self, event):
        """작업 실행 완료 이벤트"""
        logger.debug(f"작업 완료: {event.job_id}")
    
    def _on_job_error(self, event):
        """작업 에러 이벤트"""
        logger.error(f"작업 에러: {event.job_id} - {event.exception}")
    
    # ===== 스케줄 등록 =====
    
    def setup_schedules(self) -> None:
        """기본 스케줄 등록"""
        
        # 0. 테마 로테이션 체크 (08:00) - 2주 단위
        self.scheduler.add_job(
            self._run_theme_check,
            CronTrigger(hour=8, minute=0, day_of_week='mon-fri'),
            id='theme_check',
            name='테마 로테이션 체크',
            replace_existing=True
        )
        
        # 1. 일일 분석 (08:30) - 테마/종목 분석, 후보 선정
        self.scheduler.add_job(
            self._run_daily_analysis,
            CronTrigger(hour=8, minute=30, day_of_week='mon-fri'),
            id='daily_analysis',
            name='일일 분석 (후보 선정)',
            replace_existing=True
        )
        
        # 2. 장 초반 관찰 시작 (09:00) - 시초가/수급/거래량 관찰
        self.scheduler.add_job(
            self._run_morning_observation,
            CronTrigger(hour=9, minute=0, day_of_week='mon-fri'),
            id='morning_observation',
            name='장 초반 관찰',
            replace_existing=True
        )
        
        # 3. 자동 매수 (09:25) - 필터링 후 최종 매수
        self.scheduler.add_job(
            self._run_execute_buy,
            CronTrigger(hour=9, minute=25, day_of_week='mon-fri'),
            id='execute_buy',
            name='자동 매수',
            replace_existing=True
        )
        
        # 4. 모니터링 시작 (09:26)
        self.scheduler.add_job(
            self._run_monitoring_start,
            CronTrigger(hour=9, minute=26, day_of_week='mon-fri'),
            id='monitoring_start',
            name='모니터링 시작',
            replace_existing=True
        )
        
        # 5. 모니터링 종료 (15:30)
        self.scheduler.add_job(
            self._run_monitoring_stop,
            CronTrigger(hour=15, minute=30, day_of_week='mon-fri'),
            id='monitoring_stop',
            name='모니터링 종료',
            replace_existing=True
        )
        
        # 6. 장 마감 정리 (15:35)
        self.scheduler.add_job(
            self._run_market_close,
            CronTrigger(hour=15, minute=35, day_of_week='mon-fri'),
            id='market_close',
            name='장 마감 정리',
            replace_existing=True
        )
        
        # 7. 일일 리포트 (16:00)
        self.scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=16, minute=0, day_of_week='mon-fri'),
            id='daily_report',
            name='일일 리포트',
            replace_existing=True
        )
        
        logger.info("스케줄 등록 완료")
        self._print_schedules()
    
    def _print_schedules(self) -> None:
        """등록된 스케줄 출력"""
        jobs = self.scheduler.get_jobs()
        
        logger.info("\n📅 등록된 스케줄:")
        for job in jobs:
            logger.info(f"   - {job.name}: {job.trigger}")
    
    # ===== 작업 실행 =====
    
    async def _run_theme_check(self) -> None:
        """08:00 - 테마 로테이션 체크"""
        logger.info("=" * 60)
        logger.info("🔄 테마 로테이션 체크 (08:00)")
        logger.info("=" * 60)
        
        try:
            if self.on_theme_check:
                await self.on_theme_check()
            else:
                logger.warning("테마 체크 콜백 미등록")
                
        except Exception as e:
            logger.error(f"테마 체크 실패: {e}")
            self._send_error_notification("테마 체크", str(e))
    
    async def _run_daily_analysis(self) -> None:
        """08:30 - 일일 분석 실행"""
        logger.info("=" * 60)
        logger.info("🔍 일일 분석 시작 (08:30)")
        logger.info("=" * 60)
        
        try:
            if self.on_daily_analysis:
                await self.on_daily_analysis()
            else:
                logger.warning("일일 분석 콜백 미등록")
                
        except Exception as e:
            logger.error(f"일일 분석 실패: {e}")
            self._send_error_notification("일일 분석", str(e))
    
    async def _run_morning_observation(self) -> None:
        """09:00 - 장 초반 관찰 시작"""
        logger.info("=" * 60)
        logger.info("👀 장 초반 관찰 시작 (09:00)")
        logger.info("   └─ 시초가/수급/거래량 모니터링 중...")
        logger.info("   └─ 09:25까지 관찰 후 필터링 예정")
        logger.info("=" * 60)
        
        try:
            if self.on_morning_observation:
                await self.on_morning_observation()
            else:
                logger.warning("장 초반 관찰 콜백 미등록")
                
        except Exception as e:
            logger.error(f"장 초반 관찰 실패: {e}")
            self._send_error_notification("장 초반 관찰", str(e))
    
    async def _run_execute_buy(self) -> None:
        """09:25 - 자동 매수 실행 (관찰 후)"""
        logger.info("=" * 60)
        logger.info("💰 자동 매수 실행 (09:25)")
        logger.info("   └─ 장 초반 필터링 완료 후 최종 매수")
        logger.info("=" * 60)
        
        try:
            if self.on_execute_buy:
                await self.on_execute_buy()
            else:
                logger.warning("매수 실행 콜백 미등록")
                
        except Exception as e:
            logger.error(f"자동 매수 실패: {e}")
            self._send_error_notification("자동 매수", str(e))
    
    async def _run_monitoring_start(self) -> None:
        """09:26 - 모니터링 시작"""
        logger.info("📊 실시간 모니터링 시작")
        
        try:
            if self.on_monitoring_start:
                await self.on_monitoring_start()
                
        except Exception as e:
            logger.error(f"모니터링 시작 실패: {e}")
    
    async def _run_monitoring_stop(self) -> None:
        """15:30 - 모니터링 종료"""
        logger.info("📊 실시간 모니터링 종료")
        
        try:
            if self.on_monitoring_stop:
                await self.on_monitoring_stop()
                
        except Exception as e:
            logger.error(f"모니터링 종료 실패: {e}")
    
    async def _run_market_close(self) -> None:
        """15:35 - 장 마감 정리"""
        logger.info("=" * 60)
        logger.info("📋 장 마감 정리 (15:35)")
        logger.info("=" * 60)
        
        try:
            if self.on_market_close:
                await self.on_market_close()
                
        except Exception as e:
            logger.error(f"장 마감 정리 실패: {e}")
    
    async def _run_daily_report(self) -> None:
        """16:00 - 일일 리포트"""
        logger.info("=" * 60)
        logger.info("📊 일일 리포트 발송 (16:00)")
        logger.info("=" * 60)
        
        try:
            if self.on_daily_report:
                await self.on_daily_report()
                
        except Exception as e:
            logger.error(f"일일 리포트 실패: {e}")
    
    def _send_error_notification(self, task: str, error: str) -> None:
        """에러 알림 전송"""
        try:
            from modules.reporter.telegram_notifier import TelegramNotifier
            notifier = TelegramNotifier()
            notifier.send_error_alert("스케줄 에러", f"{task} 실패: {error}")
        except:
            pass
    
    # ===== 스케줄러 제어 =====
    
    def start(self) -> None:
        """스케줄러 시작"""
        if self.is_running:
            logger.warning("스케줄러가 이미 실행 중입니다")
            return
        
        self.setup_schedules()
        self.scheduler.start()
        self.is_running = True
        
        logger.info("🚀 트레이딩 스케줄러 시작")
    
    def stop(self) -> None:
        """스케줄러 종료"""
        if not self.is_running:
            return
        
        self.scheduler.shutdown(wait=False)
        self.is_running = False
        
        logger.info("⏹️ 트레이딩 스케줄러 종료")
    
    def pause(self) -> None:
        """스케줄러 일시정지"""
        self.scheduler.pause()
        logger.info("⏸️ 스케줄러 일시정지")
    
    def resume(self) -> None:
        """스케줄러 재개"""
        self.scheduler.resume()
        logger.info("▶️ 스케줄러 재개")
    
    # ===== 수동 실행 =====
    
    async def run_now(self, job_id: str) -> None:
        """작업 즉시 실행"""
        job = self.scheduler.get_job(job_id)
        
        if job:
            logger.info(f"즉시 실행: {job.name}")
            await job.func()
        else:
            logger.error(f"작업을 찾을 수 없습니다: {job_id}")
    
    def get_next_run_time(self, job_id: str) -> Optional[datetime]:
        """다음 실행 시간 조회"""
        job = self.scheduler.get_job(job_id)
        return job.next_run_time if job else None
    
    def get_status(self) -> dict:
        """스케줄러 상태 조회"""
        jobs = self.scheduler.get_jobs()
        
        job_list = []
        for job in jobs:
            try:
                next_run = str(job.next_run_time) if hasattr(job, 'next_run_time') and job.next_run_time else None
            except:
                next_run = None
            
            job_list.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run
            })
        
        return {
            "is_running": self.is_running,
            "job_count": len(jobs),
            "jobs": job_list
        }


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📅 스케줄러 테스트")
    print("=" * 60)
    
    scheduler = TradingScheduler()
    
    # 스케줄 설정
    scheduler.setup_schedules()
    
    # 상태 확인
    status = scheduler.get_status()
    print(f"\n등록된 작업: {status['job_count']}개")
    
    for job in status["jobs"]:
        print(f"  - {job['name']}: 다음 실행 {job['next_run']}")
    
    print("\n" + "=" * 60)
    print("✅ 스케줄러 테스트 완료!")
    print("=" * 60)
