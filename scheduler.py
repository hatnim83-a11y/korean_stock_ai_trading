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
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from logger import logger
from config import settings, now_kst, is_trading_day
from database import Database

# CronTrigger용 KST timezone 문자열
_KST_TZ = "Asia/Seoul"

# P0-B: 누락 시 경보할 핵심(매매 직결) 잡 후보. setup_schedules 후 실제 등록 잡과 교집합으로 확정.
# (monitoring_start_early는 PARTIAL_PROFIT_EARLY_MONITORING_ENABLED off 시 미등록 → 교집합에서 자동 제외)
CANDIDATE_CORE_JOB_IDS = frozenset({
    'theme_check', 'theme_analysis', 'stock_screening', 'execute_buy',
    'monitoring_start_early', 'monitoring_start', 'monitoring_stop',
    'hold_period_sell', 'midweek_sell_profit', 'midweek_sell_loss',
})


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
        # misfire 방어 (2026-06-01 버그픽스):
        # APScheduler 기본 misfire_grace_time=1초 → 매월 1일 등 09:02 동시 발화(스크리닝/매도/
        # 리마인더 + 모니터 tick) 시 이벤트루프 부하로 스크리닝 잡이 1.68초 지연되어 폐기됨 →
        # 매수 단계가 빈 AI 분석으로 스킵되는 장애 발생.
        # grace=60초: 관측된 지연(1.68초) 대비 35배 마진으로 이벤트루프 혼잡을 흡수하면서,
        #   잡 최소 간격(EARLY_BUY 모드 09:00/09:01/09:02 = 1분)·매도→매수 의존 간격(3분)·
        #   종가베팅 pipeline→entry 간격(8분)을 침범하지 않아 순서 역전 위험이 없음.
        #   (300초로 넓히면 매도 잡이 다음 슬롯/매수 잡을 추월해 슬롯 확보 전 매수가 발생할 수 있음)
        # coalesce=True: 프로세스 장기 정지 후 재기동 시 누락분을 최신 1회로 합쳐 중복 실행 방지.
        # max_instances=1: 동일 잡 중복 실행 차단 (일일 cron이라 무해, 안전망).
        self.scheduler = AsyncIOScheduler(
            timezone="Asia/Seoul",
            job_defaults={
                'misfire_grace_time': 60,
                'coalesce': True,
                'max_instances': 1,
            },
        )
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
        self.on_supply_collection: Optional[Callable] = None  # 17:10 외국인/기관 수급 수집 (v16)
        self.on_weekly_trade_review: Optional[Callable] = None  # 금 17:30 주간 복기
        self.on_daily_health_check: Optional[Callable] = None  # 16:10 일일 헬스체크
        self.on_midweek_sell_profit: Optional[Callable] = None  # 09:00 주중 교체 수익 매도
        self.on_midweek_sell_loss: Optional[Callable] = None    # 09:10 주중 교체 손실 매도
        self.on_hold_period_sell: Optional[Callable] = None     # 09:15 보유기간 만료 매도
        self.on_healthcheck_ping: Optional[Callable] = None     # interval off-VM dead-man's-switch ping (비매매·24h)
        self.on_job_missed_alert: Optional[Callable] = None     # P0-B: 핵심 잡 misfire 누락 경보 콜백 (async)

        # 이벤트 리스너
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

        # P0-B: 핵심 잡 누락(misfire) 경보 리스너 (기본 ON, 토글 off 시 미등록)
        self._missed_dedup: set = set()
        self.core_job_ids: set = set(CANDIDATE_CORE_JOB_IDS)  # setup_schedules 후 등록 잡과 교집합으로 좁힘
        if getattr(settings, 'JOB_RECOVERY_ALERT_ENABLED', True):
            self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)

        logger.info("트레이딩 스케줄러 초기화 (V2: 하이브리드 전략)")
    
    def _on_job_executed(self, event):
        """작업 실행 완료 이벤트"""
        logger.debug(f"작업 완료: {event.job_id}")
    
    def _on_job_error(self, event):
        """작업 에러 이벤트"""
        logger.error(f"작업 에러: {event.job_id} - {event.exception}")

    def _on_job_missed(self, event):
        """P0-B: 핵심 잡 누락(misfire) 이벤트 → 텔레그램 경보 스케줄.

        2026-06-05 freeze 때 08:00~09:06 잡이 misfire 폐기됐으나 운영자 무감지였던 공백 대응.
        주의:
        - MISSED는 잡 실행 *전*에 발화 → 잡의 @_skip_on_holiday를 우회하므로 여기서 is_trading_day() 직접 가드
        - 리스너는 APScheduler가 BaseException 격리하나, 무음 실패 방지 위해 try/except + logger.error
        - 코루틴 콜백은 이벤트루프에 안전 스케줄 (동기 잡 추가 대비 loop.is_running 방어)
        """
        try:
            if not is_trading_day():
                return
            if event.job_id not in self.core_job_ids:
                return
            key = (event.job_id, getattr(event, 'scheduled_run_time', None))
            if key in self._missed_dedup:
                return
            if len(self._missed_dedup) > 200:
                self._missed_dedup.clear()
            self._missed_dedup.add(key)
            logger.error(f"⚠️ 핵심 잡 누락(misfire): {event.job_id} 예정={getattr(event, 'scheduled_run_time', '?')}")
            if self.on_job_missed_alert:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.on_job_missed_alert(event.job_id, getattr(event, 'scheduled_run_time', None)))
                else:
                    logger.error("이벤트루프 미실행 — 누락 경보 스케줄 불가")
        except Exception as e:
            logger.error(f"_on_job_missed 예외(무시): {e}")

    # ===== 스케줄 등록 =====
    
    def setup_schedules(self) -> None:
        """기본 스케줄 등록"""

        # ===== 아침 매수 조기 진입 토글 (A3안) =====
        # EARLY_BUY_ENABLED=True 시 7개 잡 시간 자동 재배치 (스크리닝/매수/모니터링/매도 잡)
        # 09:00 monitoring_start_early + midweek_sell_profit (유지)
        # 09:01 midweek_sell_loss / 09:02 stock_screening + hold_period_sell / 09:05 execute_buy / 09:10 monitoring_start
        early_buy = settings.EARLY_BUY_ENABLED
        screening_minute = 2 if early_buy else 5
        buy_minute = 5 if early_buy else 25
        # 매수 잡(buy_minute)과의 간격 = 매수 루프 최대 소요시간 안전마진.
        # 2026-06-10 사건: 매수 루프 ~82초가 1분(09:05→09:06) 간격을 초과 →
        # 재시작이 DB 저장 전 발화하여 신규 체결분(031980) 영구 누락.
        # cron 재시작은 이제 "안전망" 역할이고, 1차 정합 보장은 매수 완료 직후
        # execute_buy_orders() 말미의 start_monitoring() 체이닝(main.py)이 담당한다.
        # → 이중 race를 없애기 위해 cron 시각을 매수 완료 이후로 충분히 후행시킨다.
        monitoring_minute = 10 if early_buy else 26
        hold_period_minute = 2 if early_buy else 15
        midweek_loss_minute = 1 if early_buy else 10

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
            CronTrigger(hour=9, minute=screening_minute, day_of_week='mon-fri', timezone=_KST_TZ),
            id='stock_screening',
            name=f'종목 스크리닝 ({settings.screening_time_str})',
            replace_existing=True
        )

        # 3. 자동 매수 (KST 09:25) - 필터링 후 최종 매수
        self.scheduler.add_job(
            self._run_execute_buy,
            CronTrigger(hour=9, minute=buy_minute, day_of_week='mon-fri', timezone=_KST_TZ),
            id='execute_buy',
            name='자동 매수',
            replace_existing=True
        )

        # 4-0. 조기 모니터링 시작 (KST 09:00) — 09:00 직후 급등락 분할익절 시그널 캡처
        # PARTIAL_PROFIT_EARLY_MONITORING_ENABLED=False일 때는 등록 스킵 (09:26만 활성)
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            self.scheduler.add_job(
                self._run_monitoring_start,
                CronTrigger(hour=9, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
                id='monitoring_start_early',
                name='조기 모니터링 시작 (09:00)',
                replace_existing=True
            )

        # 4. 모니터링 재시작 (early_buy 09:10 / legacy 09:26) — 매수분 포함 재시작 (안전망)
        # 1차 정합은 매수 완료 직후 execute_buy_orders() 말미 start_monitoring() 체이닝이 보장.
        # 이 cron 잡은 체이닝이 매수 잡 예외 등으로 누락된 경우를 대비한 멱등 안전망이다.
        # 옵션 D-1: KIS WebSocket 동적 SUBSCRIBE 미지원 → start_monitoring()이 stop+start 패턴으로 재시작
        self.scheduler.add_job(
            self._run_monitoring_start,
            CronTrigger(hour=9, minute=monitoring_minute, day_of_week='mon-fri', timezone=_KST_TZ),
            id='monitoring_start',
            name=f'모니터링 재시작 ({settings.monitoring_time_str}, 신규 매수분 포함)',
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
            CronTrigger(hour=9, minute=midweek_loss_minute, day_of_week='mon-fri', timezone=_KST_TZ),
            id='midweek_sell_loss',
            name='주중 교체 손실 매도',
            replace_existing=True
        )

        # 10-1. 보유기간 만료 매도 (KST 09:15) - 09:25 매수 전 슬롯 확보
        self.scheduler.add_job(
            self._run_hold_period_sell,
            CronTrigger(hour=9, minute=hold_period_minute, day_of_week='mon-fri', timezone=_KST_TZ),
            id='hold_period_sell',
            name=f'보유기간 만료 매도 ({settings.hold_period_time_str})',
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

        # 12-1. 외국인/기관 수급 수집 (KST 17:10, v16 supply-signal-integration Phase 1-A)
        # T-1 수급 데이터를 DB에 1회 수집 → 다음날 아침 KIS 재호출 없이 supply 신호 활용.
        # SUPPLY_SIGNAL_ENABLED=False 시 잡 등록은 되지만 핸들러가 즉시 return (run_supply_collection 내부 가드).
        # 시각은 config.SUPPLY_COLLECT_HOUR/MINUTE 로 조정 가능 (기본 17:10).
        self.scheduler.add_job(
            self._run_supply_collection,
            CronTrigger(
                hour=settings.SUPPLY_COLLECT_HOUR,
                minute=settings.SUPPLY_COLLECT_MINUTE,
                day_of_week='mon-fri',
                timezone=_KST_TZ,
            ),
            id='supply_collection',
            name='외국인/기관 수급 수집',
            replace_existing=True
        )

        # 12-2. 외국인/기관 수급 수집 재시도 (KST 18:00, 17:10 잡 실패 대비)
        # 17:10 잡이 KIS 인증 실패 등으로 전체 실패한 경우의 안전망.
        # 단일 종목 retry는 _collect_one_stock 내부 3회 retry가 처리하므로 여기서는 잡 자체 재실행.
        # SUPPLY_RETRY_JOB_HOUR=0이면 미등록.
        if settings.SUPPLY_RETRY_JOB_HOUR > 0:
            self.scheduler.add_job(
                self._run_supply_collection,
                CronTrigger(
                    hour=settings.SUPPLY_RETRY_JOB_HOUR,
                    minute=settings.SUPPLY_RETRY_JOB_MINUTE,
                    day_of_week='mon-fri',
                    timezone=_KST_TZ,
                ),
                id='supply_collection_retry',
                name='외국인/기관 수급 수집 재시도 (18:00)',
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

        # (P0-A) Off-VM dead-man's-switch ping (2026-06-05 incident)
        self._setup_healthcheck_job()

        # 16. 종가베팅 시스템 잡 (Phase 1 알림형, 2026-05-04 도입)
        # - PRD 16-3 시간표: 15:10 pipeline / 15:35 summary / 10:00 T+1 라벨링
        # - Phase 1 = placeholder universe (빈 리스트) → 잡 등록되지만 무동작 (Phase 2 collector 도입 시 활성화)
        # - 통합 실패 시 메인 시스템 영향 없도록 try/except 격리
        self._setup_closing_bet_jobs()

        # P0-B: 실제 등록된 잡과 교집합으로 핵심 잡 목록 확정 (토글/EARLY_BUY 변동 무관)
        registered = {j.id for j in self.scheduler.get_jobs()}
        self.core_job_ids = set(CANDIDATE_CORE_JOB_IDS) & registered
        logger.info(f"누락 경보 대상 핵심 잡 {len(self.core_job_ids)}개: {sorted(self.core_job_ids)}")

        logger.info("스케줄 등록 완료")
        self._print_schedules()

    def _setup_healthcheck_job(self) -> None:
        """Off-VM dead-man's-switch ping 잡 등록 (2026-06-05 incident P0-A).

        - opt-in: HEALTHCHECK_ENABLED=True + URL 설정 시에만 등록
        - IntervalTrigger(분 주기), 비매매·24h (휴장일 스킵 안 함 — 무신호 오경보 방지)
        - 등록 실패가 메인 스케줄에 영향 주지 않도록 격리
        """
        try:
            if not settings.HEALTHCHECK_ENABLED:
                return
            url = settings.HEALTHCHECK_PING_URL
            if not url or not url.strip():
                logger.warning("HEALTHCHECK_ENABLED=True 이나 HEALTHCHECK_PING_URL 미설정 → ping 잡 미등록")
                return
            interval_min = max(1, int(settings.HEALTHCHECK_PING_INTERVAL_MIN))
            self.scheduler.add_job(
                self._run_healthcheck_ping,
                IntervalTrigger(minutes=interval_min, timezone=_KST_TZ),
                id='healthcheck_ping',
                name=f'Off-VM 헬스체크 ping ({interval_min}분 주기)',
                replace_existing=True
            )
            logger.info(f"🩺 Off-VM 헬스체크 ping 등록 ({interval_min}분 주기)")
        except Exception as e:
            logger.error(f"⚠️ 헬스체크 ping 잡 등록 실패 (메인 시스템은 정상 진행): {e}")

    def _setup_closing_bet_jobs(self) -> None:
        """종가베팅 시스템 잡 등록 (Phase 1: 알림형 / placeholder providers).

        실패 시 main 시스템 영향 없도록 격리. Phase 2 진입 시
        universe_provider / market_data_provider / name_lookup / label_provider 실 구현체 주입 필요.

        주: PHASE 1 정책상 자동매수 절대 금지 — orchestrator는 mark_entered/log_exit 미호출.
        """
        try:
            from closing_bet_system.main_orchestrator import MainOrchestrator
            from closing_bet_system.infra.name_lookup import get_name as _cb_get_name
            from closing_bet_system.collectors.universe_provider_v2 import (
                get_universe_v2_filtered as _cb_get_universe,
            )
            from closing_bet_system.collectors.market_data_provider import (
                get_market_data as _cb_get_market_data,
            )
            from closing_bet_system.collectors.label_provider import (
                get_label as _cb_get_label,
            )
            self._closing_bet_orch = MainOrchestrator(
                # Phase 1 carryover 단위 A/B/C/D 적용. weekly_loss_limit (E) 는 fund_guard 내부.
                # universe v2 (단위 2-9a/2-9b, 2026-05-04): PRD 16-3 다중 출처(스윙 테마 + pykrx 거래대금/등락률/외국인) + PRD 4-1 속성 필터 + 4-4 유동성 필터
                universe_provider=_cb_get_universe,       # universe v2: 4 출처 합집합 → PRD 4-1/4-4 하드 필터 → list[ticker]
                market_data_provider=_cb_get_market_data, # KOSPI(KIS) + V-KOSPI/미선물/USD-KRW(yfinance)
                name_lookup=_cb_get_name,                 # KIS get_stock_name + 캐시
                label_provider=_cb_get_label,             # T+1 사후 라벨링 (KIS get_daily_price + cost_engine)
            )
            self._closing_bet_orch.register_jobs(self.scheduler)
            logger.info(
                "🎯 종가베팅 시스템 잡 등록 완료 (Phase 1 알림형, universe v2 + providers 4종 활성)"
            )
        except Exception as e:
            logger.error(
                f"⚠️ 종가베팅 잡 등록 실패 (메인 시스템은 정상 진행): {e}",
                exc_info=True,
            )

    def _print_schedules(self) -> None:
        """등록된 스케줄 출력 (KST 기준)"""
        jobs = self.scheduler.get_jobs()

        logger.info(f"\n📅 등록된 스케줄 (KST 기준) — EARLY_BUY_ENABLED={settings.EARLY_BUY_ENABLED}")
        for job in jobs:
            next_run = ""
            if hasattr(job, 'next_run_time') and job.next_run_time:
                next_run = f" → 다음 실행: {job.next_run_time.strftime('%m-%d %H:%M KST')}"
            logger.info(f"   - {job.name}: {job.trigger}{next_run}")
    
    # ===== 작업 실행 =====

    # 주의: @_skip_on_holiday 미적용 — dead-man's-switch는 휴장일/주말/장외에도 ping 해야
    # 외부 서비스가 "정상"으로 판단한다. 데코레이터를 붙이면 휴장일 ping 중단 → 오경보.
    async def _run_healthcheck_ping(self) -> None:
        """interval - off-VM dead-man's-switch ping (비매매, 24시간)."""
        try:
            if self.on_healthcheck_ping:
                await self.on_healthcheck_ping()
            # 콜백 미등록 시 조용히 패스 (잡 자체가 ENABLED일 때만 등록되므로 정상 경로 아님)
        except Exception as e:
            # ping 잡은 트레이딩에 영향 주면 안 됨 — 예외 격리, 에러알림 미발송(스팸 방지)
            logger.warning(f"healthcheck ping 잡 예외(무시): {e}")

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
        logger.info(f"🔍 종목 스크리닝 시작 ({settings.screening_time_str})")
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
        logger.info(f"💰 자동 매수 실행 ({settings.buy_time_str})")
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
    async def _run_supply_collection(self) -> None:
        """17:10 - 외국인/기관 수급 수집 (v16, supply-signal-integration Phase 1-A).

        SUPPLY_SIGNAL_ENABLED=False 시 main.run_supply_collection 내부에서 즉시 return.
        """
        logger.info("=" * 60)
        logger.info("📊 외국인/기관 수급 수집 시작 (17:10)")
        logger.info("=" * 60)

        try:
            if self.on_supply_collection:
                await self.on_supply_collection()
            else:
                logger.warning("수급 수집 콜백 미등록")

        except Exception as e:
            logger.error(f"수급 수집 실패: {e}")
            self._send_error_notification("외국인/기관 수급 수집 (17:10)", str(e))

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
