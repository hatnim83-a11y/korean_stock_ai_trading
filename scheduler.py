"""
scheduler.py - APScheduler 스케줄링 모듈

이 파일은 트레이딩 시스템의 자동 스케줄링을 관리합니다.

일정:
- 08:00 - 테마 로테이션 체크 (2주 단위)
- 08:30 - 테마 분석 (크롤링 → 점수화 → 상위 테마 선정)
- 09:05 - 종목 스크리닝 (장 시작 후 실시간 데이터 기반)
- 09:25 - 자동 매수 실행 (필터링 후 최종 선정)
- 09:26~15:30 - 실시간 모니터링 (분할 익절/트레일링 스탑/손절)
- 15:35 - 장 마감 정리 (리밸런싱 준비)
- 16:00 - 일일 리포트 발송

하이브리드 전략:
- 분할 익절: +10% → 30%, +15% → 30%, +20% → 전량
- 트레일링 스탑: 최고가 -5%
- 보유 기간: 수익 10영업일, 손실 5영업일
- 테마 로테이션: 2주 단위

사용법:
    from scheduler import TradingScheduler
    
    scheduler = TradingScheduler()
    scheduler.start()
"""

import asyncio
import functools
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
from config import settings, now_kst, is_trading_day
from database import Database

# CronTrigger용 KST timezone 문자열
_KST_TZ = "Asia/Seoul"


def _skip_on_holiday(func):
    """휴장일 스킵 데코레이터"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not is_trading_day():
            logger.info(f"휴장일 - {func.__name__} 스킵 ({now_kst().date()})")
            return
        return await func(*args, **kwargs)
    return wrapper


class TradingScheduler:
    """
    트레이딩 스케줄러
    
    APScheduler를 사용하여 일일 트레이딩 작업을 자동화합니다.
    
    Attributes:
        scheduler: APScheduler 인스턴스
        is_running: 실행 중 여부

    스케줄:
        - 08:30 - 테마 분석
        - 09:05 - 종목 스크리닝
        - 09:25 - 자동 매수
        - 09:26~15:30 - 모니터링
        - 15:35 - 장 마감 정리
        - 16:00 - 일일 리포트
    """
    
    def __init__(self):
        """스케줄러 초기화"""
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.is_running = False
        
        # 작업 콜백
        self.on_theme_check: Optional[Callable] = None          # 08:00 테마 로테이션 체크
        self.on_theme_analysis: Optional[Callable] = None       # 08:30 테마 분석
        self.on_stock_screening: Optional[Callable] = None      # 09:05 종목 스크리닝
        self.on_execute_buy: Optional[Callable] = None          # 09:25 자동 매수
        self.on_market_close: Optional[Callable] = None         # 15:35 장 마감 정리
        self.on_daily_report: Optional[Callable] = None         # 16:00 일일 리포트
        self.on_monitoring_start: Optional[Callable] = None     # 09:26 모니터링 시작
        self.on_monitoring_stop: Optional[Callable] = None      # 15:30 모니터링 종료
        self.on_post_trade_analysis: Optional[Callable] = None  # 17:00 매매 사후 분석
        self.on_daily_theme_collection: Optional[Callable] = None  # 17:05 일별 테마 수집
        self.on_weekly_trade_review: Optional[Callable] = None  # 금 17:30 주간 복기
        self.on_daily_health_check: Optional[Callable] = None  # 16:10 일일 헬스체크
        self.on_midweek_sell_profit: Optional[Callable] = None  # 09:00 주중 교체 수익 매도
        self.on_midweek_sell_loss: Optional[Callable] = None    # 09:10 주중 교체 손실 매도
        self.on_hold_period_sell: Optional[Callable] = None     # 09:15 보유기간 만료 매도
        
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
        
        # 0. 테마 로테이션 체크 (KST 08:00) - 2주 단위
        self.scheduler.add_job(
            self._run_theme_check,
            CronTrigger(hour=8, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
            id='theme_check',
            name='테마 로테이션 체크',
            replace_existing=True
        )

        # 1. 테마 분석 (KST 08:30) - 장 시작 전 테마 크롤링/점수화/선정
        self.scheduler.add_job(
            self._run_theme_analysis,
            CronTrigger(hour=8, minute=30, day_of_week='mon-fri', timezone=_KST_TZ),
            id='theme_analysis',
            name='테마 분석',
            replace_existing=True
        )

        # 2. 종목 스크리닝 (KST 09:05) - 장 시작 후 실시간 데이터 기반
        self.scheduler.add_job(
            self._run_stock_screening,
            CronTrigger(hour=9, minute=5, day_of_week='mon-fri', timezone=_KST_TZ),
            id='stock_screening',
            name='종목 스크리닝 (09:05)',
            replace_existing=True
        )

        # 3. 자동 매수 (KST 09:25) - 필터링 후 최종 매수
        self.scheduler.add_job(
            self._run_execute_buy,
            CronTrigger(hour=9, minute=25, day_of_week='mon-fri', timezone=_KST_TZ),
            id='execute_buy',
            name='자동 매수',
            replace_existing=True
        )

        # 4. 모니터링 시작 (KST 09:26)
        self.scheduler.add_job(
            self._run_monitoring_start,
            CronTrigger(hour=9, minute=26, day_of_week='mon-fri', timezone=_KST_TZ),
            id='monitoring_start',
            name='모니터링 시작',
            replace_existing=True
        )

        # 5. 모니터링 종료 (KST 15:30)
        self.scheduler.add_job(
            self._run_monitoring_stop,
            CronTrigger(hour=15, minute=30, day_of_week='mon-fri', timezone=_KST_TZ),
            id='monitoring_stop',
            name='모니터링 종료',
            replace_existing=True
        )

        # 6. 장 마감 정리 (KST 15:35)
        self.scheduler.add_job(
            self._run_market_close,
            CronTrigger(hour=15, minute=35, day_of_week='mon-fri', timezone=_KST_TZ),
            id='market_close',
            name='장 마감 정리',
            replace_existing=True
        )

        # 7. 일일 리포트 (KST 16:00)
        self.scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=16, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
            id='daily_report',
            name='일일 리포트',
            replace_existing=True
        )

        # 8. 일일 헬스체크 (KST 16:10)
        self.scheduler.add_job(
            self._run_daily_health_check,
            CronTrigger(hour=16, minute=10, day_of_week='mon-fri', timezone=_KST_TZ),
            id='daily_health_check',
            name='일일 헬스체크',
            replace_existing=True
        )

        # 9. 주중 교체 수익 매도 (KST 09:00)
        self.scheduler.add_job(
            self._run_midweek_sell_profit,
            CronTrigger(hour=9, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
            id='midweek_sell_profit',
            name='주중 교체 수익 매도',
            replace_existing=True
        )

        # 10. 주중 교체 손실 매도 (KST 09:10)
        self.scheduler.add_job(
            self._run_midweek_sell_loss,
            CronTrigger(hour=9, minute=10, day_of_week='mon-fri', timezone=_KST_TZ),
            id='midweek_sell_loss',
            name='주중 교체 손실 매도',
            replace_existing=True
        )

        # 10-1. 보유기간 만료 매도 (KST 09:15) - 09:25 매수 전 슬롯 확보
        self.scheduler.add_job(
            self._run_hold_period_sell,
            CronTrigger(hour=9, minute=15, day_of_week='mon-fri', timezone=_KST_TZ),
            id='hold_period_sell',
            name='보유기간 만료 매도 (09:15)',
            replace_existing=True
        )

        # 11. 매매 사후 분석 (KST 17:00)
        self.scheduler.add_job(
            self._run_post_trade_analysis,
            CronTrigger(hour=17, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
            id='post_trade_analysis',
            name='매매 사후 분석',
            replace_existing=True
        )

        # 12. 일별 테마 데이터 수집 (KST 17:05, 장 마감 후)
        self.scheduler.add_job(
            self._run_daily_theme_collection,
            CronTrigger(hour=17, minute=5, day_of_week='mon-fri', timezone=_KST_TZ),
            id='daily_theme_collection',
            name='일별 테마 수집',
            replace_existing=True
        )

        # 13. 주간 매매 복기 (KST 금요일 17:30)
        self.scheduler.add_job(
            self._run_weekly_trade_review,
            CronTrigger(hour=17, minute=30, day_of_week='fri', timezone=_KST_TZ),
            id='weekly_trade_review',
            name='주간 매매 복기',
            replace_existing=True
        )

        # 14. 주간 개선 제안서 리마인더 (KST 금요일 17:45 - 17:30 주간 복기 이후)
        # 봇이 직접 에이전트 호출 불가 → 사용자에게 수동 실행 안내
        self.scheduler.add_job(
            self._run_improvement_reminder_weekly,
            CronTrigger(hour=17, minute=45, day_of_week='fri', timezone=_KST_TZ),
            id='improvement_reminder_weekly',
            name='주간 개선 제안서 리마인더',
            replace_existing=True
        )

        # 15. 월간 개선 제안서 리마인더 (KST 매월 1일 09:02, 주말/공휴일 포함)
        # - 평일 09:00 midweek_sell_profit 매도 잡과 로그 혼선 방지 위해 09:02로 오프셋
        # - 1일이 주말/공휴일이어도 발송 (사용자가 수동 실행 여부를 판단)
        self.scheduler.add_job(
            self._run_improvement_reminder_monthly,
            CronTrigger(day='1', hour=9, minute=2, timezone=_KST_TZ),
            id='improvement_reminder_monthly',
            name='월간 개선 제안서 리마인더',
            replace_existing=True
        )

        # 16. 종가베팅 시스템 잡 (Phase 1 알림형, 2026-05-04 도입)
        # - PRD 16-3 시간표: 15:10 pipeline / 15:35 summary / 10:00 T+1 라벨링
        # - Phase 1 = placeholder universe (빈 리스트) → 잡 등록되지만 무동작 (Phase 2 collector 도입 시 활성화)
        # - 통합 실패 시 메인 시스템 영향 없도록 try/except 격리
        self._setup_closing_bet_jobs()

        logger.info("스케줄 등록 완료")
        self._print_schedules()

    def _setup_closing_bet_jobs(self) -> None:
        """종가베팅 시스템 잡 등록 (Phase 1: 알림형 / placeholder providers).

        실패 시 main 시스템 영향 없도록 격리. Phase 2 진입 시
        universe_provider / market_data_provider / name_lookup / label_provider 실 구현체 주입 필요.

        주: PHASE 1 정책상 자동매수 절대 금지 — orchestrator는 mark_entered/log_exit 미호출.
        """
        try:
            from closing_bet_system.main_orchestrator import MainOrchestrator
            from closing_bet_system.infra.name_lookup import get_name as _cb_get_name
            from closing_bet_system.collectors.universe_provider import (
                get_universe as _cb_get_universe,
            )
            from closing_bet_system.collectors.market_data_provider import (
                get_market_data as _cb_get_market_data,
            )
            from closing_bet_system.collectors.label_provider import (
                get_label as _cb_get_label,
            )
            self._closing_bet_orch = MainOrchestrator(
                # Phase 1 carryover 단위 A/B/C/D 적용. weekly_loss_limit (E) 는 fund_guard 내부.
                universe_provider=_cb_get_universe,       # 스윙 top_themes → 네이버 크롤링 → list[ticker]
                market_data_provider=_cb_get_market_data, # KOSPI(KIS) + V-KOSPI/미선물/USD-KRW(yfinance)
                name_lookup=_cb_get_name,                 # KIS get_stock_name + 캐시
                label_provider=_cb_get_label,             # T+1 사후 라벨링 (KIS get_daily_price + cost_engine)
            )
            self._closing_bet_orch.register_jobs(self.scheduler)
            logger.info(
                "🎯 종가베팅 시스템 잡 등록 완료 (Phase 1 알림형, providers 4종 활성)"
            )
        except Exception as e:
            logger.error(
                f"⚠️ 종가베팅 잡 등록 실패 (메인 시스템은 정상 진행): {e}",
                exc_info=True,
            )

    def _print_schedules(self) -> None:
        """등록된 스케줄 출력 (KST 기준)"""
        jobs = self.scheduler.get_jobs()

        logger.info("\n📅 등록된 스케줄 (KST 기준):")
        for job in jobs:
            next_run = ""
            if hasattr(job, 'next_run_time') and job.next_run_time:
                next_run = f" → 다음 실행: {job.next_run_time.strftime('%m-%d %H:%M KST')}"
            logger.info(f"   - {job.name}: {job.trigger}{next_run}")
    
    # ===== 작업 실행 =====

    @_skip_on_holiday
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
    
    @_skip_on_holiday
    async def _run_theme_analysis(self) -> None:
        """08:30 - 테마 분석 (장 시작 전)"""
        logger.info("=" * 60)
        logger.info("📊 테마 분석 시작 (08:30)")
        logger.info("   └─ 테마 크롤링 → 점수화 → 상위 테마 선정")
        logger.info("=" * 60)

        try:
            if self.on_theme_analysis:
                await self.on_theme_analysis()
            else:
                logger.warning("테마 분석 콜백 미등록")

        except Exception as e:
            logger.error(f"테마 분석 실패: {e}")
            self._send_error_notification("테마 분석", str(e))

    @_skip_on_holiday
    async def _run_stock_screening(self) -> None:
        """09:05 - 종목 스크리닝 (장 시작 후)"""
        logger.info("=" * 60)
        logger.info("🔍 종목 스크리닝 시작 (09:05)")
        logger.info("   └─ 장 시작 후 실시간 데이터로 스크리닝")
        logger.info("   └─ 수급/기술/재무/유동성 필터 적용")
        logger.info("=" * 60)

        try:
            if self.on_stock_screening:
                await self.on_stock_screening()
            else:
                logger.warning("종목 스크리닝 콜백 미등록")

        except Exception as e:
            logger.error(f"종목 스크리닝 실패: {e}")
            self._send_error_notification("종목 스크리닝", str(e))
    
    @_skip_on_holiday
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
    
    @_skip_on_holiday
    async def _run_monitoring_start(self) -> None:
        """09:26 - 모니터링 시작"""
        logger.info("📊 실시간 모니터링 시작")
        
        try:
            if self.on_monitoring_start:
                await self.on_monitoring_start()
                
        except Exception as e:
            logger.error(f"모니터링 시작 실패: {e}")
    
    @_skip_on_holiday
    async def _run_monitoring_stop(self) -> None:
        """15:30 - 모니터링 종료"""
        logger.info("📊 실시간 모니터링 종료")
        
        try:
            if self.on_monitoring_stop:
                await self.on_monitoring_stop()
                
        except Exception as e:
            logger.error(f"모니터링 종료 실패: {e}")
    
    @_skip_on_holiday
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
    
    @_skip_on_holiday
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
    
    @_skip_on_holiday
    async def _run_post_trade_analysis(self) -> None:
        """17:00 - 매매 사후 분석"""
        logger.info("=" * 60)
        logger.info("🔍 매매 사후 분석 시작 (17:00)")
        logger.info("=" * 60)

        try:
            if self.on_post_trade_analysis:
                await self.on_post_trade_analysis()
            else:
                logger.warning("매매 사후 분석 콜백 미등록")

        except Exception as e:
            logger.error(f"매매 사후 분석 실패: {e}")
            self._send_error_notification("매매 사후 분석", str(e))

    @_skip_on_holiday
    async def _run_daily_theme_collection(self) -> None:
        """17:05 - 일별 테마 데이터 수집 (모멘텀 + 뉴스 + AI 감성)"""
        logger.info("=" * 60)
        logger.info("📊 일별 테마 데이터 수집 시작 (17:05)")
        logger.info("=" * 60)

        try:
            if self.on_daily_theme_collection:
                await self.on_daily_theme_collection()
            else:
                logger.warning("일별 테마 수집 콜백 미등록")

        except Exception as e:
            logger.error(f"일별 테마 수집 실패: {e}")
            self._send_error_notification("일별 테마 수집", str(e))

    @_skip_on_holiday
    async def _run_midweek_sell_profit(self) -> None:
        """09:00 - 주중 교체 수익 종목 매도"""
        try:
            if self.on_midweek_sell_profit:
                await self.on_midweek_sell_profit()
        except Exception as e:
            logger.error(f"주중 교체 수익 매도 실패: {e}")
            self._send_error_notification("주중 교체 수익 매도", str(e))

    @_skip_on_holiday
    async def _run_midweek_sell_loss(self) -> None:
        """09:10 - 주중 교체 손실 종목 매도"""
        try:
            if self.on_midweek_sell_loss:
                await self.on_midweek_sell_loss()
        except Exception as e:
            logger.error(f"주중 교체 손실 매도 실패: {e}")
            self._send_error_notification("주중 교체 손실 매도", str(e))

    @_skip_on_holiday
    async def _run_hold_period_sell(self) -> None:
        """09:15 - 보유기간 만료 종목 매도 (09:25 매수 전 슬롯 확보)"""
        try:
            if self.on_hold_period_sell:
                await self.on_hold_period_sell()
        except Exception as e:
            logger.error(f"보유기간 만료 매도 실패: {e}")
            self._send_error_notification("보유기간 만료 매도", str(e))

    @_skip_on_holiday
    async def _run_daily_health_check(self) -> None:
        """16:10 - 일일 시스템 헬스체크"""
        try:
            if self.on_daily_health_check:
                await self.on_daily_health_check()
            else:
                logger.warning("일일 헬스체크 콜백 미등록")
        except Exception as e:
            logger.error(f"일일 헬스체크 실패: {e}")
            self._send_error_notification("일일 헬스체크", str(e))

    @_skip_on_holiday
    async def _run_weekly_trade_review(self) -> None:
        """금요일 17:30 - 주간 매매 복기"""
        logger.info("=" * 60)
        logger.info("📝 주간 매매 복기 시작 (금 17:30)")
        logger.info("=" * 60)

        try:
            if self.on_weekly_trade_review:
                await self.on_weekly_trade_review()
            else:
                logger.warning("주간 매매 복기 콜백 미등록")

        except Exception as e:
            logger.error(f"주간 매매 복기 실패: {e}")
            self._send_error_notification("주간 매매 복기", str(e))

    async def _run_improvement_reminder_weekly(self) -> None:
        """금요일 17:45 - 주간 개선 제안서 리마인더 (공휴일에도 발송)"""
        logger.info("📣 주간 개선 제안서 리마인더 (금 17:45)")
        try:
            from modules.reporter.telegram_notifier import TelegramNotifier
            ok = TelegramNotifier().send_improvement_reminder("weekly")
            if not ok:
                logger.warning("주간 개선 리마인더 전송 실패 (텔레그램 응답 오류)")
        except Exception as e:
            logger.error(f"주간 개선 리마인더 전송 실패: {e}")
            self._send_error_notification("주간 개선 리마인더", str(e))

    async def _run_improvement_reminder_monthly(self) -> None:
        """매월 1일 09:02 - 월간 개선 제안서 리마인더 (주말/공휴일 포함 발송)"""
        logger.info("📣 월간 개선 제안서 리마인더 (매월 1일 09:02)")
        try:
            from modules.reporter.telegram_notifier import TelegramNotifier
            ok = TelegramNotifier().send_improvement_reminder("monthly")
            if not ok:
                logger.warning("월간 개선 리마인더 전송 실패 (텔레그램 응답 오류)")
        except Exception as e:
            logger.error(f"월간 개선 리마인더 전송 실패: {e}")
            self._send_error_notification("월간 개선 리마인더", str(e))

    def _send_error_notification(self, task: str, error: str) -> None:
        """에러 알림 전송"""
        try:
            from modules.reporter.telegram_notifier import TelegramNotifier
            notifier = TelegramNotifier()
            notifier.send_error_alert("스케줄 에러", f"{task} 실패: {error}")
        except Exception:
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
            except Exception:
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
