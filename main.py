"""
main.py - 한국 주식 AI 스윙 트레이딩 시스템 메인 엔트리

이 파일은 전체 트레이딩 시스템을 통합하고 실행합니다.

기능:
- 시스템 초기화
- 스케줄러 시작
- 일일 트레이딩 파이프라인 실행
- 장 초반 관찰 및 필터링
- 실시간 모니터링 (분할 익절 + 트레일링 스탑)
- 테마 로테이션 (7일 단위)

실행 방법:
    python main.py              # 전체 시스템 실행
    python main.py --test       # 테스트 모드
    python main.py --manual     # 수동 분석 실행

일일 흐름:
    08:30 - 테마 분석 (크롤링 → 점수화 → 상위 테마 선정)
    09:05 - 종목 스크리닝 (장 시작 후 실시간 데이터) → AI 검증 → 후보 선정
    09:25 - 필터링 후 최종 매수 (5-8개)
    09:26~15:30 - 실시간 모니터링 (분할 익절/트레일링 스탑/손절)
    15:35 - 장 마감 정리

하이브리드 전략:
    - 분할 익절: +10% → 30% 매도, +15% → 30% 매도, +20% → 전량 매도
    - 트레일링 스탑: 최고가 -5%
    - 보유 기간: 수익(+3%) 10영업일, 손실 5영업일
    - 테마 로테이션: 7일 단위, 점수 -20% 시 즉시 변경

작성자: AI Trading System
버전: 2.0.0 (하이브리드 전략 + 테마 로테이션)
"""

import asyncio
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, date, timedelta
from typing import Optional

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# PID 락 파일 경로
PID_FILE = Path(__file__).parent / "trading_system.pid"

from logger import logger
from config import settings, now_kst, is_trading_day, count_trading_days, is_makeup_reselection_day
from database import Database
from scheduler import TradingScheduler

# 모듈 임포트
from modules.theme_analyzer import select_top_themes, ThemeRotator
from modules.stock_screener import run_daily_screening
from modules.ai_verifier import run_daily_verification
from modules.portfolio_optimizer import run_daily_optimization, display_portfolio
from modules.trading_engine import TradingEngine
from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2, SellReason
from modules.trading_engine.sell_lock import sell_lock
from modules.trading_engine.diversity_filter import apply_diversity_filter
from modules.rebalancer import run_daily_rebalancing
from modules.reporter import (
    PerformanceCalculator,
    generate_daily_report,
    TelegramNotifier
)
from modules.morning_filter import MorningScreener, run_morning_observation
from modules.morning_filter.candidate_observer import CandidateObserver
from modules.post_trade_analyzer import PostTradeAnalyzer


def _coalesce_zero(v):
    """SQL NULL → 0, 실제 0/0.0 → 그대로 보존.

    momentum=0.0 (5일 수익률 -10% 정상값)을 NULL과 구분하기 위해 `or 0` 대신 사용.
    """
    return 0 if v is None else v


def _pick_momentum(t: dict):
    """테마 dict에서 momentum 값을 안전하게 추출.

    polling 순서: momentum_score → momentum → 0
    - 정규 재선정 직후 today_themes (scorer.py:658-659): 양쪽 키 보유, momentum_score 우선 매치
    - DB 복원본 (database.py:693, themes 컬럼명): momentum 키만 보유 → 폴백
    - 둘 다 없는 회귀 박제 시나리오: 0 반환

    is None 체크로 momentum_score=0.0 (실제 정상값) 보존.

    의존: scorer.py:658-659 momentum/momentum_score 양쪽 키 동시 출력 (변경 시 동시 수정 필수)
    """
    for key in ("momentum_score", "momentum"):
        v = t.get(key)
        if v is not None:
            return v
    return 0


class TradingSystem:
    """
    한국 주식 AI 스윙 트레이딩 시스템
    
    전체 트레이딩 파이프라인을 관리합니다.
    
    일일 흐름:
    1. 08:30 - 테마 분석 (크롤링/점수화/상위 테마 선정)
    2. 09:05 - 종목 스크리닝 (장 시작 후 실시간 데이터) → AI 검증 → 후보 선정
    3. 09:25 - 필터링 후 자동 매수 (5-8개)
    4. 09:26~15:30 - 실시간 모니터링 (손절/익절)
    5. 15:35 - 리밸런싱 준비
    6. 16:00 - 일일 리포트 발송
    
    Example:
        >>> system = TradingSystem()
        >>> system.start()
    """
    
    def __init__(
        self,
        use_mock: bool = None,
        test_mode: bool = False
    ):
        """
        시스템 초기화
        
        Args:
            use_mock: 모의투자 모드
            test_mode: 테스트 모드 (실제 주문 없음)
        """
        self.use_mock = use_mock if use_mock is not None else settings.IS_MOCK
        self.test_mode = test_mode
        
        # 컴포넌트
        self.scheduler = TradingScheduler()
        self.trading_engine = TradingEngine(use_mock_api=test_mode)
        self.monitor: Optional[PortfolioMonitorV2] = None  # V2: 분할 익절 + 트레일링
        self.morning_screener = MorningScreener()  # 장 초반 스크리너
        self.theme_rotator = ThemeRotator()  # 테마 로테이션 (2주 단위)
        self.notifier = TelegramNotifier()
        self.post_trade_analyzer: Optional[PostTradeAnalyzer] = None  # 매매 사후 분석
        self.db = Database()
        
        # 상태
        self.is_running = False
        self.today_portfolio: Optional[dict] = None
        self.today_themes: list[dict] = []       # 08:30 선정 테마 (09:05 스크리닝용)
        self.today_candidates: list[dict] = []   # 09:05 선정 후보 (10-15개)
        self.today_orders: list[dict] = []       # 09:25 최종 매수 (5-8개)
        self.current_themes: list[dict] = []     # 현재 테마 리스트
        self._previous_themes: list[dict] = []  # 직전 선정 테마 (비교용)
        self.today_ai_analysis: list[dict] = []  # AI 분석 결과 (선정 이유 포함)
        self.today_trades: list[dict] = []       # 오늘 거래 내역
        self.observation_result = None              # 실시간 관찰 결과
        self._observer_task = None                  # 관찰 비동기 태스크
        # CRITICAL: Screening↔Buy 동기화 Event (A3안 09:05 매수 race 방지)
        # 초기 set 상태 → 첫 매수 호출 즉시 통과. run_stock_screening 진입 시 clear, 종료 시 set.
        self._screening_done = asyncio.Event()
        self._screening_done.set()
        self._listener_task = None                  # 텔레그램 명령어 리스너 태스크
        self._last_theme_rotation_date: Optional[date] = None  # 7일 고정 로테이션
        self.trading_paused = False  # 텔레그램 /pause로 매매 일시정지

        # 주중 테마 교체 상태
        self._midweek_sell_queue: list[dict] = []       # 09:00 수익 매도 대기열
        self._midweek_loss_queue: list[dict] = []       # 09:10 손실 매도 대기열
        self._midweek_dropped_theme: Optional[str] = None
        self._midweek_new_theme: Optional[dict] = None

        # 시그널 핸들러
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        mode = "모의투자" if self.use_mock else "실전투자"
        logger.info(f"🚀 트레이딩 시스템 초기화 ({mode})")
        logger.info(f"   분할 익절: {settings.TAKE_PROFIT_1:.0%}/{settings.TAKE_PROFIT_2:.0%}/{settings.TAKE_PROFIT_3:.0%}")
        logger.info(f"   트레일링 스탑: 최고가 -{settings.TRAILING_STOP_PERCENT:.0%}")
        logger.info(f"   테마 로테이션: 주간 (매주 월요일)")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        logger.info("\n시스템 종료 신호 수신...")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.stop())
        except RuntimeError:
            # 이벤트 루프가 실행 중이 아닌 경우
            pass
    
    # ===== 시스템 시작/종료 =====
    
    async def start(self) -> None:
        """시스템 시작"""
        logger.info("=" * 70)
        logger.info("🚀 한국 주식 AI 스윙 트레이딩 시스템")
        logger.info("=" * 70)
        logger.info(f"   시작 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST")
        logger.info(f"   모드: {'모의투자' if self.use_mock else '실전투자'}")
        logger.info(f"   테스트: {'활성화' if self.test_mode else '비활성화'}")
        logger.info("=" * 70)
        
        self.is_running = True
        
        # 데이터베이스 초기화
        self._init_database()

        # 7일 로테이션 날짜 복원 (서비스 재시작 시 DB에서 복원)
        last_date = self.db.get_last_theme_analysis_date()
        if last_date:
            self._last_theme_rotation_date = last_date
            # DB에서 최근 테마도 복원
            themes_from_db = self.db.get_top_themes(last_date, count=settings.TOP_THEME_COUNT)
            if themes_from_db:
                # 박제 데이터 폴백: selected=1 row의 momentum/ai/news 모두 0/NULL이면
                # 같은 날 selected=0 일별 수집 또는 직전 정상값에서 보강.
                # 5/4 박제 회귀 같은 케이스 사후 자동 회복 (subsequent restart 시 발동)
                for t in themes_from_db:
                    mom = t.get("momentum")
                    ai = t.get("ai_sentiment")
                    nc = t.get("news_count")
                    is_zero_pinned = (
                        (mom is None or mom == 0) and
                        (ai is None or ai == 0) and
                        (nc is None or nc == 0)
                    )
                    if is_zero_pinned:
                        fb = self.db.get_score_fallback_for_theme(t["theme_name"], last_date)
                        if fb:
                            old_mom = mom
                            t["momentum"] = fb.get("momentum") if fb.get("momentum") is not None else mom
                            t["supply_ratio"] = fb.get("supply_ratio") if fb.get("supply_ratio") is not None else t.get("supply_ratio")
                            t["news_count"] = fb.get("news_count") if fb.get("news_count") is not None else nc
                            t["ai_sentiment"] = fb.get("ai_sentiment") if fb.get("ai_sentiment") is not None else ai
                            logger.info(
                                f"   📊 박제 데이터 폴백: {t['theme_name']} "
                                f"momentum {old_mom}→{t['momentum']}, ai_sentiment →{t['ai_sentiment']}"
                            )

                # DB 행을 코드 내부 형식으로 정규화 (name+theme 양쪽 키 포함)
                # 점수 4개 키(momentum/supply_ratio/news_count/ai_sentiment)도 포함하여
                # 다음 영업일 "기존 테마 유지" 분기 저장 시 0 박제 방지
                # SQL NULL과 실제 0(정상값)을 구분하기 위해 _coalesce_zero 사용
                normalized = [
                    {
                        "name": t["theme_name"],
                        "theme": t["theme_name"],
                        "score": t["score"],
                        "total_score": t["score"],
                        "url": t.get("url", ""),
                        "category": t.get("category", "기타"),
                        "momentum": _coalesce_zero(t.get("momentum")),
                        "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                        "news_count": _coalesce_zero(t.get("news_count")),
                        "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
                    }
                    for t in themes_from_db
                ]
                self.today_themes = normalized
                self._previous_themes = [t.copy() for t in normalized]
                logger.info(f"🔄 테마 로테이션 복원: {last_date} ({len(normalized)}개 테마)")

        # 시스템 시작 알림
        self.notifier.send_system_start()
        
        # 스케줄러 콜백 등록
        self._setup_scheduler_callbacks()
        
        # 스케줄러 시작
        self.scheduler.start()

        # 텔레그램 명령어 리스너 시작 (시스템 참조 연결)
        self.notifier._system_ref = self
        self._listener_task = asyncio.create_task(self.notifier.start_command_listener())

        logger.info("\n✅ 시스템 시작 완료")
        logger.info("📅 스케줄에 따라 자동 실행됩니다.")
        logger.info("   종료하려면 Ctrl+C를 누르세요.\n")

        # 장 중 재시작 시 모니터링 자동 재개
        await self._resume_monitoring_if_needed()

        # 메인 루프
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def _resume_monitoring_if_needed(self) -> None:
        """장 중 재시작 시 보유 포지션이 있으면 모니터링 자동 재개"""
        from datetime import time as dt_time
        now = now_kst()
        market_open = dt_time(9, 26)
        market_close = dt_time(15, 30)

        if not (market_open <= now.time() <= market_close):
            return

        # DB에서 보유 포지션 확인
        db = Database()
        db.connect()
        holdings = db.get_portfolio(status='holding')
        db.close()

        if holdings:
            logger.info(f"🔄 장 중 재시작 감지 — 모니터링 자동 재개 ({len(holdings)}종목 보유 중)")
            await self.start_monitoring()
        else:
            logger.info("장 중 재시작이나 보유 포지션 없음 — 모니터링 스킵")

    async def stop(self) -> None:
        """시스템 종료"""
        if not self.is_running:
            return
        
        logger.info("\n시스템 종료 중...")
        
        self.is_running = False
        
        # 텔레그램 명령어 리스너 종료
        self.notifier.stop_command_listener()
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        # 모니터링 종료
        if self.monitor:
            await self.monitor.stop_monitoring()

        # 스케줄러 종료
        self.scheduler.stop()
        
        # 데이터베이스 종료
        if self.db.conn:
            self.db.close()
        
        # 종료 알림
        self.notifier.send_system_stop("정상 종료")
        
        logger.info("✅ 시스템 종료 완료")
    
    def _init_database(self) -> None:
        """데이터베이스 초기화"""
        try:
            self.db.connect()
            self.db.init_tables()
            logger.info("데이터베이스 초기화 완료")
        except Exception as e:
            logger.error(f"데이터베이스 초기화 실패: {e}")
    
    def _setup_scheduler_callbacks(self) -> None:
        """스케줄러 콜백 등록"""
        self.scheduler.on_theme_analysis = self.run_theme_analysis       # 08:30 테마 분석
        self.scheduler.on_stock_screening = self.run_stock_screening     # 09:05 종목 스크리닝
        self.scheduler.on_execute_buy = self.execute_buy_orders          # 09:25
        self.scheduler.on_monitoring_start = self.start_monitoring       # 09:26
        self.scheduler.on_monitoring_stop = self.stop_monitoring         # 15:30
        self.scheduler.on_market_close = self.run_market_close           # 15:35
        self.scheduler.on_daily_report = self.send_daily_report          # 16:00
        self.scheduler.on_theme_check = self.check_theme_rotation        # 08:00 테마 체크
        self.scheduler.on_post_trade_analysis = self.run_post_trade_analysis  # 17:00 사후 분석
        self.scheduler.on_daily_theme_collection = self.run_daily_theme_collection  # 17:05 일별 테마 수집
        self.scheduler.on_supply_collection = self.run_supply_collection  # 17:10 외국인/기관 수급 수집 (v16)
        self.scheduler.on_weekly_trade_review = self.run_weekly_trade_review  # 금 17:30 주간 복기
        self.scheduler.on_daily_health_check = self.run_daily_health_check  # 16:10 헬스체크
        self.scheduler.on_midweek_sell_profit = self._execute_midweek_profit_sells  # 09:00 주중 교체 수익 매도
        self.scheduler.on_midweek_sell_loss = self._execute_midweek_loss_sells      # 09:10 주중 교체 손실 매도
        self.scheduler.on_hold_period_sell = self.run_hold_period_sells              # 09:15 보유기간 만료 매도

    # ===== 08:30 테마 분석 (장 시작 전) =====

    async def run_theme_analysis(self) -> dict:
        """
        테마 분석 실행 (08:30)

        7일간 동일 테마 유지, 7일째 또는 긴급 트리거 시만 재선정.
        장 시작 전에 테마를 크롤링하고 점수화하여 상위 테마를 선정합니다.
        종목 스크리닝은 09:05에 별도로 실행됩니다.

        Returns:
            테마 분석 결과
        """
        if self.trading_paused:
            logger.info("⏸️ 매매 일시정지 중 — 테마 분석 스킵")
            self.notifier.send_message("⏸️ 08:30 테마 분석 스킵 (매매 일시정지 중)\n/resume 으로 재개")
            return {"success": True, "paused": True}

        logger.info("=" * 70)
        logger.info("📊 테마 분석 시작 (08:30)")
        logger.info("=" * 70)

        # 보정 재선정 판정 (today 1회 계산, 같은 함수 내 재사용)
        today = now_kst().date()
        is_makeup, missed_tue = is_makeup_reselection_day(today)
        # 중복 발화 방어: missed 화요일 이후 이미 재선정 발생했다면 보정 우회
        if (is_makeup and missed_tue and self._last_theme_rotation_date
                and self._last_theme_rotation_date >= missed_tue):
            is_makeup, missed_tue = (False, None)

        # 기존 테마가 있고, 선정일로부터 7일 이내면 재사용
        # (화~월 5영업일 사이클: 화요일 선정 → 다음 화요일 전까지 유지)
        # 단, 화요일은 주간 재선정일이므로 항상 재분석 실행
        # 화요일이 휴장(공휴일/주말)이면 다음 첫 영업일에 보정 재선정
        if self.today_themes and self._last_theme_rotation_date:
            days_since_rotation = (today - self._last_theme_rotation_date).days
            is_tuesday = (today.weekday() == 1)
            same_week = (days_since_rotation < 7) and not is_tuesday and not is_makeup
            if same_week:
                logger.info(
                    f"🔄 기존 테마 유지 (이번 주 {self._last_theme_rotation_date.strftime('%m/%d')} 선정)"
                )

                # URL 또는 stock_count 없는 테마 보충
                needs_supplement = [t for t in self.today_themes
                                    if not t.get("url") or not t.get("stock_count")]
                if needs_supplement:
                    logger.info(f"   URL/종목수 보충 필요 {len(needs_supplement)}개 → 크롤링")
                    try:
                        from modules.theme_analyzer import crawl_all_themes
                        from modules.theme_analyzer.crawlers import search_naver_theme
                        from modules.stock_screener.screener import _search_naver_upjong

                        all_crawled = await asyncio.to_thread(crawl_all_themes)
                        crawled_map = {t["name"]: t for t in all_crawled}
                        for t in needs_supplement:
                            t_name = t.get("theme", t.get("name", ""))
                            if t_name in crawled_map:
                                matched = crawled_map[t_name]
                                if not t.get("url"):
                                    t["url"] = matched.get("url") or ""
                                t["stock_count"] = matched.get("stock_count", 0) or t.get("stock_count", 0)
                                if t.get("url") and t.get("stock_count"):
                                    logger.info(f"   ✓ [{t_name}] 보충 완료 ({t['stock_count']}종목)")
                                    continue
                            if not t.get("url"):
                                # 폴백 1: 네이버 테마 개별 검색
                                naver_result = await asyncio.to_thread(search_naver_theme, t_name)
                                if naver_result and naver_result.get("url"):
                                    t["url"] = naver_result["url"]
                                    t["stock_count"] = naver_result.get("stock_count", 0) or t.get("stock_count", 0)
                                    logger.info(f"   ✓ [{t_name}] 네이버 검색으로 보충 ({t['stock_count']}종목)")
                                else:
                                    # 폴백 2: 네이버 업종 검색
                                    upjong_url = await asyncio.to_thread(_search_naver_upjong, t_name)
                                    if upjong_url:
                                        t["url"] = upjong_url
                                        logger.info(f"   ✓ [{t_name}] 업종 검색으로 url 보충")
                                    else:
                                        logger.warning(f"   ✗ [{t_name}] 모든 URL 검색 실패 ({settings.screening_time_str} 스크리닝에서 재시도)")
                    except Exception as e:
                        logger.error(f"   종목 URL/종목수 보충 실패: {e}")

                for t in self.today_themes:
                    t_name = t.get("theme", t.get("name", ""))
                    t_score = t.get("score", 0)
                    logger.info(f"   - {t_name} ({t_score:.1f}점)")

                # 다음 화요일 계산
                days_until_tuesday = (1 - today.weekday()) % 7
                if days_until_tuesday == 0:
                    days_until_tuesday = 7
                next_review = today + timedelta(days=days_until_tuesday)

                theme_lines = []
                for i, t in enumerate(self.today_themes, 1):
                    t_name = t.get("theme", t.get("name", ""))
                    t_score = t.get("score", 0)
                    stock_cnt = t.get("stock_count", 0) or len(t.get("stocks", []))
                    # 주중 교체 표시
                    if self._midweek_new_theme and t_name == self._midweek_new_theme.get("theme_name"):
                        tag = "🆕교체"
                    else:
                        tag = "📌유지"
                    theme_lines.append(f"  {i}. {t_name} ({t_score:.1f}점, {stock_cnt}종목) {tag}")

                header = "🔄 기존 테마 유지"
                if self._midweek_dropped_theme:
                    header = f"🔄 주중 교체 반영 (❌{self._midweek_dropped_theme})"
                header += f" (이번 주 {self._last_theme_rotation_date.strftime('%m/%d')} 선정)"

                self.notifier.send_message(
                    f"📊 08:30 테마 분석\n\n"
                    f"{header}\n"
                    + "\n".join(theme_lines)
                    + f"\n\n📅 다음 재평가: {next_review.strftime('%m/%d')} (화) ({days_until_tuesday}일 후)"
                )

                # 서비스 재시작 시 테마 복원을 위해 당일 날짜로 DB 저장
                try:
                    # today_themes 출처에 따라 momentum 키명이 다름:
                    # - 정규 재선정 직후 (scorer 결과): momentum_score + momentum 양쪽 키 보유
                    # - DB 복원본 (get_top_themes): momentum 키만 보유
                    # _pick_momentum이 폴백 체인으로 양쪽 처리 (is None 체크로 실제 0 보존)
                    themes_to_save = [
                        {
                            "theme": t.get("theme", t.get("name", "")),
                            "score": t.get("score", 0),
                            "momentum": _pick_momentum(t),
                            "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                            "news_count": _coalesce_zero(t.get("news_count")),
                            "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
                            "category": t.get("category", "기타"),
                            "url": t.get("url", ""),
                        }
                        for t in self.today_themes
                    ]
                    self.db.save_theme_scores(themes_to_save, today, selected=True)
                except Exception as e:
                    logger.warning(f"   ⚠️ 테마 DB 동기화 실패 (기능 영향 없음): {e}")

                return {"success": True, "themes": len(self.today_themes), "reused": True}

        # 정규 재선정 분기 진입 — 보정 발화 시 운영자 알림
        # (콜드 스타트 시점에는 알림 생략: _last_theme_rotation_date 없으면 어차피 첫 정규 선정)
        if is_makeup and missed_tue and self._last_theme_rotation_date:
            _kor_dow = ['월', '화', '수', '목', '금', '토', '일'][today.weekday()]
            logger.info(
                f"🔁 화요일 보정 재선정 발동 "
                f"(직전 화요일 {missed_tue} 휴장 → 오늘 {today} {_kor_dow})"
            )
            self.notifier.send_message(
                f"🔁 화요일 보정 재선정\n"
                f"휴장: {missed_tue.strftime('%m/%d')} (화)\n"
                f"오늘({today.strftime('%m/%d')} {_kor_dow}) 재선정 실행"
            )

        start_time = now_kst()

        try:
            # 1. 화요일: 6일 누적 가중 집계 사용, 그 외: 실시간 크롤링
            from modules.theme_analyzer import (
                crawl_all_themes,
                score_themes,
                select_top_themes,
                select_themes_with_retention,
                aggregate_weekly_scores,
            )

            # today는 함수 시작부에서 이미 계산됨 (보정 재선정 판정과 공유)
            is_tuesday = (today.weekday() == 1)

            # 유동성 통과율 조회 (screening_log 기반)
            pass_rates = {}
            if settings.THEME_LIQUIDITY_CHECK_ENABLED:
                try:
                    pass_rates = self.db.get_theme_pass_rates(settings.THEME_PASS_RATE_LOOKBACK_DAYS)
                except Exception as e:
                    logger.warning(f"유동성 통과율 조회 실패 (무시): {e}")

            if is_tuesday:
                logger.info("\n📊 Step 1: 화요일 — 6일 누적 가중 집계")
                scored_themes = aggregate_weekly_scores(self.db, base_date=today - timedelta(days=1))
                if scored_themes:
                    logger.info(f"   가중 집계 완료: {len(scored_themes)}개 테마")
                    # 실시간 보강: 상위 15개 테마의 모멘텀 + 뉴스 + AI 보정
                    scored_themes = await self._enrich_tuesday_themes(scored_themes)
                    # 유동성 통과율 보정 (화요일 가중 집계에도 적용)
                    if pass_rates:
                        from modules.theme_analyzer.scorer import calculate_liquidity_penalty
                        for t in scored_themes:
                            t_name = t.get("theme", t.get("name", ""))
                            penalty, note = calculate_liquidity_penalty(
                                t_name, pass_rates,
                                min_pass_rate=settings.THEME_MIN_PASS_RATE,
                                low_pass_rate=settings.THEME_LOW_PASS_RATE,
                                penalty_max=settings.THEME_PASS_RATE_PENALTY_MAX,
                                min_data_days=settings.THEME_PASS_RATE_MIN_DATA_DAYS,
                            )
                            if penalty < 0:
                                t["total_score"] = round(t.get("total_score", 0) + penalty, 2)
                                t["score"] = t["total_score"]
                                t["liquidity_penalty"] = round(penalty, 2)
                                t["liquidity_note"] = note
                                t["pass_rate"] = pass_rates.get(t_name, {}).get("pass_rate")
                                logger.info(f"   [{t_name}] 유동성 보정: {penalty:+.1f}점 ({note})")
                            else:
                                t["liquidity_penalty"] = 0.0
                                t["liquidity_note"] = ""
                                t["pass_rate"] = pass_rates.get(t_name, {}).get("pass_rate")
                        # 보정 후 재정렬
                        scored_themes.sort(key=lambda x: x.get("total_score", 0), reverse=True)
                else:
                    logger.warning("   가중 집계 데이터 없음 → 실시간 크롤링 폴백")
                    is_tuesday = False  # 폴백

            if not is_tuesday:
                logger.info("\n📊 Step 1: 테마 크롤링")
                raw_themes = crawl_all_themes()
                logger.info(f"   크롤링된 테마: {len(raw_themes)}개")
                scored_themes = score_themes(raw_themes[:20], pass_rates=pass_rates)
                logger.info(f"   점수화 완료: {len(scored_themes)}개")

            # 현재 테마 저장
            self.current_themes = scored_themes

            # 3. 테마 로테이션 체크
            logger.info("\n🔄 Step 2: 테마 로테이션 체크")
            should_rotate, reason = self.theme_rotator.check_rotation_needed(scored_themes)
            logger.info(f"   로테이션 필요: {should_rotate} (이유: {reason})")

            if should_rotate:
                self.theme_rotator.select_new_main_theme(scored_themes)
            else:
                if self.theme_rotator.current_main_theme is None and scored_themes:
                    self.theme_rotator.set_main_theme(
                        scored_themes[0]['theme'],
                        scored_themes[0]['score']
                    )

            # 4. 상위 테마 선정 (화요일: 유지+교체, 그 외: 전체 신규)
            if is_tuesday and self._previous_themes:
                logger.info(f"\n🔄 Step 3: 기존 테마 유지 판별 ({len(self._previous_themes)}개 검토)")
                themes = select_themes_with_retention(
                    scored_themes,
                    previous_themes=self._previous_themes,
                    count=settings.TOP_THEME_COUNT,
                )
            else:
                themes = select_top_themes(scored_themes, count=settings.TOP_THEME_COUNT)
            self.today_themes = themes  # 09:05 스크리닝에서 사용
            self._last_theme_rotation_date = now_kst().date()  # 로테이션 날짜 기록
            logger.info(f"   선정 테마: {len(themes)}개")

            # 4-1. 화요일 DB 집계 테마는 URL이 없으므로 크롤링으로 보충
            if is_tuesday and themes and not any(t.get("url") for t in themes):
                logger.info("\n📦 Step 4: 종목 URL 크롤링 보충")
                try:
                    from modules.theme_analyzer.crawlers import search_naver_theme
                    from modules.stock_screener.screener import _search_naver_upjong

                    all_crawled = await asyncio.to_thread(crawl_all_themes)
                    crawled_map = {t["name"]: t for t in all_crawled}
                    supplemented = 0
                    for t in themes:
                        t_name = t.get("theme", t.get("name", ""))
                        if t_name in crawled_map:
                            matched = crawled_map[t_name]
                            t["url"] = matched.get("url") or ""
                            t["stock_count"] = matched.get("stock_count", 0)
                            if t.get("url"):
                                supplemented += 1
                                logger.info(f"   ✓ [{t_name}] url 보충 완료 ({t['stock_count']}종목)")
                                continue
                            logger.warning(f"   [{t_name}] 크롤링 결과에 URL 없음 → 폴백 시도")
                        # 폴백 1: 네이버 테마 개별 검색
                        naver_result = await asyncio.to_thread(search_naver_theme, t_name)
                        if naver_result and naver_result.get("url"):
                            t["url"] = naver_result["url"]
                            t["stock_count"] = naver_result.get("stock_count", 0)
                            supplemented += 1
                            logger.info(f"   ✓ [{t_name}] 네이버 검색으로 url 보충 ({t['stock_count']}종목)")
                        else:
                            # 폴백 2: 네이버 업종 검색
                            upjong_url = await asyncio.to_thread(_search_naver_upjong, t_name)
                            if upjong_url:
                                t["url"] = upjong_url
                                supplemented += 1
                                logger.info(f"   ✓ [{t_name}] 업종 검색으로 url 보충")
                            else:
                                logger.warning(f"   ✗ [{t_name}] 모든 URL 검색 실패 ({settings.screening_time_str} 스크리닝에서 재시도)")
                    logger.info(f"   URL 보충: {supplemented}/{len(themes)}개 테마")
                except Exception as e:
                    logger.error(f"   종목 URL 보충 실패: {e}")

            # 5. 전일 수급비율 DB 조회 (장 시작 전 — API 호출 불필요)
            try:
                prev_supply = {}
                with self.db.get_cursor() as cursor:
                    cursor.execute(
                        "SELECT theme_name, supply_ratio FROM themes "
                        "WHERE selected = 0 AND supply_ratio > 0 "
                        "ORDER BY date DESC LIMIT 100"
                    )
                    for row in cursor.fetchall():
                        name = row["theme_name"]
                        if name not in prev_supply:
                            prev_supply[name] = row["supply_ratio"]
                matched = 0
                for t in themes:
                    t_name = t.get("theme", t.get("name", ""))
                    if t_name in prev_supply:
                        t["supply_ratio"] = prev_supply[t_name]
                        matched += 1
                if matched:
                    logger.info(f"   전일 수급비율 매칭: {matched}/{len(themes)}개 테마")
            except Exception as e:
                logger.warning(f"   전일 수급비율 조회 실패: {e}")

            # 6. DB 저장 (대시보드 테마 탭용, url 포함)
            try:
                themes_to_save = [
                    {
                        "theme": t.get("theme", t.get("name", "")),
                        "score": t.get("score", 0),
                        "momentum": t.get("momentum_score", 0),
                        "supply_ratio": t.get("supply_ratio", 0),
                        "news_count": t.get("news_count", 0),
                        "ai_sentiment": t.get("ai_sentiment", 0),
                        "category": t.get("category", "기타"),
                        "url": t.get("url", ""),
                    }
                    for t in themes
                ]
                self.db.save_theme_scores(themes_to_save, now_kst().date(), selected=True)
                logger.info(f"   DB 저장 완료: {len(themes_to_save)}개 테마 (selected=True)")
            except Exception as e:
                logger.error(f"   테마 DB 저장 실패: {e}")

            if not themes:
                logger.warning("선정된 테마가 없습니다")
                return {"success": False, "reason": "테마 없음"}

            # 테마 목록 출력
            for t in themes:
                t_name = t.get("theme", "")
                t_score = t.get("score", 0)
                logger.info(f"   - {t_name} ({t_score:.1f}점)")

            elapsed = (now_kst() - start_time).total_seconds()
            logger.info(f"\n✅ 테마 분석 완료 ({elapsed:.1f}초)")
            logger.info(f"   └─ {settings.screening_time_str} 장 시작 후 종목 스크리닝 예정")

            # 이전 테마와 비교하여 상세 보고
            prev_names = {t.get("theme", t.get("name", "")) for t in self._previous_themes}
            curr_names = {t.get("theme", t.get("name", "")) for t in themes}
            had_previous = bool(self._previous_themes)

            maintained = []
            new_entries = []
            dropped = []

            for t in themes:
                name = t.get("theme", t.get("name", ""))
                if name in prev_names:
                    prev_score = next(
                        (p.get("score", 0) for p in self._previous_themes
                         if p.get("theme", p.get("name", "")) == name), 0
                    )
                    maintained.append((t, prev_score))
                else:
                    new_entries.append(t)

            for t in self._previous_themes:
                name = t.get("theme", t.get("name", ""))
                if name not in curr_names:
                    dropped.append(t)

            self._previous_themes = [t.copy() for t in themes]

            # 메시지 구성
            is_emergency = should_rotate and ("급락" in reason or "급등" in reason)
            # 다음 화요일 계산
            today = now_kst().date()
            days_until_tuesday = (1 - today.weekday()) % 7
            if days_until_tuesday == 0:
                days_until_tuesday = 7
            next_review = today + timedelta(days=days_until_tuesday)

            msg = "📊 08:30 테마 분석\n\n"

            if is_emergency:
                msg += f"⚡ 긴급 테마 변경! (사유: {reason})\n\n"
            elif had_previous:
                day_names = ["월", "화", "수", "목", "금", "토", "일"]
                today_day = day_names[now_kst().weekday()]
                msg += f"🔄 {today_day}요일 — 주간 테마 재선정\n\n"
            else:
                msg += f"🎯 신규 테마 선정: {len(themes)}개\n\n"

            if had_previous:
                idx = 0
                if maintained:
                    msg += f"📌 유지: {len(maintained)}개\n"
                    for t, prev_score in maintained:
                        idx += 1
                        name = t.get("theme", t.get("name", ""))
                        score = t.get("score", 0)
                        diff = score - prev_score
                        liq = t.get("liquidity_note", "")
                        pr = t.get("pass_rate")
                        pr_str = f" 통과율{pr:.0%}" if pr is not None else ""
                        liq_warn = f" {liq}" if liq else ""
                        msg += f"  {idx}. {name} ({score:.1f}점, {diff:+.1f}){pr_str}{liq_warn}\n"

                if new_entries:
                    msg += f"\n🆕 신규: {len(new_entries)}개\n"
                    for t in new_entries:
                        idx += 1
                        name = t.get("theme", t.get("name", ""))
                        score = t.get("score", 0)
                        momentum = t.get("avg_change_rate", 0)
                        liq = t.get("liquidity_note", "")
                        pr = t.get("pass_rate")
                        pr_str = f" 통과율{pr:.0%}" if pr is not None else ""
                        liq_warn = f" {liq}" if liq else ""
                        msg += f"  {idx}. {name} ({score:.1f}점) — 모멘텀 {momentum:+.1f}%{pr_str}{liq_warn}\n"

                if dropped:
                    msg += f"\n❌ 탈락: {len(dropped)}개\n"
                    for t in dropped:
                        name = t.get("theme", t.get("name", ""))
                        prev_score = t.get("score", 0)
                        curr_info = next(
                            (s for s in scored_themes
                             if s.get("theme", s.get("name", "")) == name), None
                        )
                        if curr_info:
                            curr_score = curr_info.get("score", 0)
                            diff = curr_score - prev_score
                            msg += f"  • {name} ({curr_score:.1f}점, {diff:+.1f}) — 점수 하락\n"
                        else:
                            msg += f"  • {name} — 이번 주 상위 테마 아님\n"
            else:
                for i, t in enumerate(themes[:5], 1):
                    name = t.get("theme", t.get("name", ""))
                    score = t.get("score", 0)
                    pr = t.get("pass_rate")
                    pr_str = f" 통과율{pr:.0%}" if pr is not None else ""
                    liq = t.get("liquidity_note", "")
                    liq_warn = f" {liq}" if liq else ""
                    msg += f"  {i}. {name} ({score:.1f}점){pr_str}{liq_warn}\n"

            msg += f"\n📅 다음 재평가: {next_review.strftime('%m/%d')} (화) ({days_until_tuesday}일 후)\n"
            msg += f"⏰ {settings.screening_time_str} 장 시작 후 종목 스크리닝 예정"

            self.notifier.send_message(msg)

            return {
                "success": True,
                "themes": len(themes),
                "elapsed": elapsed
            }

        except Exception as e:
            logger.error(f"테마 분석 실패: {e}")
            self.notifier.send_error_alert("테마 분석", str(e))
            return {"success": False, "error": str(e)}

    # ===== 09:05 종목 스크리닝 (장 시작 후) =====

    async def run_stock_screening(self) -> dict:
        """
        종목 스크리닝 실행 (09:05)

        장 시작 후 실시간 데이터로 종목 스크리닝 → AI 검증 → 후보 선정.
        08:30 테마 분석에서 선정된 테마를 사용합니다.

        Returns:
            스크리닝 결과
        """
        if self.trading_paused:
            logger.info("⏸️ 매매 일시정지 중 — 종목 스크리닝 스킵")
            self.notifier.send_message(f"⏸️ {settings.screening_time_str} 스크리닝 스킵 (일시정지 중)")
            return {"success": True, "paused": True}

        # CRITICAL: A3안 09:05 매수와 동기화 — screening 진입 시 Event clear, 종료 시 set (finally)
        self._screening_done.clear()
        try:
            return await self._run_stock_screening_impl()
        finally:
            self._screening_done.set()

    async def _run_stock_screening_impl(self) -> dict:
        """run_stock_screening 본문 — Event 보장을 위해 wrapper에서 try/finally로 호출"""
        logger.info("=" * 70)
        logger.info(f"🔍 종목 스크리닝 시작 ({settings.screening_time_str}, 장 시작 후)")
        logger.info("=" * 70)

        start_time = now_kst()

        # 08:30 테마 분석 결과 확인
        themes = getattr(self, 'today_themes', None)
        if not themes:
            logger.warning("선정된 테마가 없습니다 (08:30 테마 분석 확인 필요)")
            return {"success": False, "reason": "테마 없음"}

        # 장중 재시작 방어: DB 복원 테마에 url이 없으면 재분석 실행
        if not any(t.get("url") for t in themes):
            logger.warning("today_themes에 url 없음 — 테마 재분석 실행")
            await self.run_theme_analysis()
            themes = getattr(self, 'today_themes', None)
            if not themes:
                return {"success": False, "reason": "테마 재분석 실패"}

        logger.info(f"   대상 테마: {len(themes)}개")
        for t in themes:
            logger.info(f"   - {t.get('name', t.get('theme', '?'))}")

        try:
            # 1. 종목 스크리닝 (실시간 데이터)
            logger.info("\n📈 Step 1: 종목 스크리닝 (실시간 데이터)")
            candidates = await asyncio.to_thread(
                run_daily_screening,
                themes=themes
            )
            logger.info(f"   후보 종목: {len(candidates)}개")

            if not candidates:
                logger.warning("후보 종목이 없습니다")
                self.notifier.send_message(
                    f"⚠️ {settings.screening_time_str} 스크리닝 완료 - 후보 종목 없음\n"
                    f"- {len(themes)}개 테마에서 통과 종목 없음"
                )
                return {"success": False, "reason": "후보 종목 없음"}

            # 2. AI 검증
            logger.info("\n🤖 Step 2: AI 검증")
            verified = await asyncio.to_thread(
                run_daily_verification,
                candidates=candidates,
                save_to_db=True,
                use_mock_data=self.test_mode
            )
            logger.info(f"   검증 통과: {len(verified)}개")

            self.today_ai_analysis = verified

            if not verified:
                logger.warning("AI 검증 통과 종목이 없습니다")
                self.notifier.send_message(
                    f"⚠️ {settings.screening_time_str} 스크리닝 완료 - AI 검증 통과 0개\n\n"
                    f"📊 필터 통과: {len(candidates)}개 종목\n"
                    f"🤖 AI 검증 통과: 0개\n"
                    f"─────────────────\n"
                    f"금일 매수 후보 없음"
                )
                return {"success": False, "reason": "AI 검증 통과 없음"}

            # 3. 포트폴리오 최적화 및 후보 선정
            logger.info("\n📋 Step 3: 매수 후보 선정")

            candidate_pool_size = settings.CANDIDATE_POOL_SIZE
            # 리밸런싱 대비: 전체 자본 기준으로 넓은 후보풀 생성
            # 실제 매수 수량/금액은 09:25 execute_buy_orders()에서 재계산
            available_cash = settings.TOTAL_CAPITAL
            logger.info(f"   후보풀 자본 기준: {available_cash:,}원 (리밸런싱 대비 전체 자본)")

            optimization_result = await asyncio.to_thread(
                run_daily_optimization,
                verified_stocks=verified[:candidate_pool_size],
                capital=available_cash,
                strategy="score_based",
                save_to_db=False,
                use_mock_data=self.test_mode
            )

            self.today_candidates = optimization_result["orders"]
            self.today_portfolio = optimization_result["portfolio"]

            # 보유 종목 제외 (이미 가지고 있는 종목은 후보에서 빼기)
            current_holdings = self.db.get_portfolio(status='holding')
            held_codes = {h['stock_code'] for h in current_holdings}
            if held_codes:
                before_count = len(self.today_candidates)
                self.today_candidates = [c for c in self.today_candidates if c['stock_code'] not in held_codes]
                excluded = before_count - len(self.today_candidates)
                if excluded > 0:
                    logger.info(f"   보유 종목 제외: {excluded}개 (보유: {[h['stock_name'] for h in current_holdings]})")

            elapsed = (now_kst() - start_time).total_seconds()

            logger.info(f"\n✅ 종목 스크리닝 완료 ({elapsed:.1f}초)")
            logger.info(f"   매수 후보: {len(self.today_candidates)}개")
            logger.info(f"   └─ {settings.buy_time_str} 필터링 후 최종 매수 실행")

            # 후보 목록 출력
            logger.info("\n📋 매수 대상 종목:")
            for i, order in enumerate(self.today_candidates[:10], 1):
                stock_name = order.get('stock_name', order.get('stock_code', 'N/A'))
                stock_code = order.get('stock_code', '')
                amount = order.get('amount', 0)
                logger.info(f"   {i}. {stock_name} ({stock_code}) - {amount:,}원")

            # 알림 발송
            if self.notifier:
                stock_list = []
                for i, order in enumerate(self.today_candidates[:8], 1):
                    stock_name = order.get('stock_name', 'N/A')
                    stock_code = order.get('stock_code', '')
                    amount = order.get('amount', 0)
                    theme = order.get('theme', '')
                    score = order.get('final_score', 0)
                    stock_list.append(
                        f"{i}. {stock_name} ({stock_code})\n"
                        f"   └ {theme} | {amount:,}원 | 점수:{score:.1f}"
                    )
                stock_text = "\n".join(stock_list)

                self.notifier.send_message(
                    f"🔍 {settings.screening_time_str} 스크리닝 완료\n\n"
                    f"📊 매수 후보: {len(self.today_candidates)}개\n"
                    f"─────────────────\n"
                    f"{stock_text}\n"
                    f"─────────────────\n"
                    f"⏰ {settings.buy_time_str} 필터링 후 매수 예정"
                )

            # 주중 교체: 손실 종목 재평가 (탈락 테마 손실 종목을 스크리닝으로 평가)
            if self._midweek_loss_queue and self._midweek_dropped_theme:
                await self._rescore_midweek_loss_stocks()

            # 실시간 관찰 시작 (09:05 ~ 09:23, EARLY_BUY 모드는 관찰 미생성)
            # A3안: 09:05 매수 시 관찰 시간 자체가 없으므로 observer task 생성 안 함.
            if (
                settings.ENABLE_MORNING_FILTER
                and not settings.EARLY_BUY_ENABLED
                and self.today_candidates
            ):
                observer = CandidateObserver(self.today_candidates, self.notifier)
                self._observer_task = asyncio.create_task(
                    self._run_observation(observer)
                )
                logger.info("👁️ 실시간 관찰 루프 시작 (09:23까지)")

            return {
                "success": True,
                "candidates": len(candidates),
                "verified": len(verified),
                "buy_candidates": len(self.today_candidates),
                "elapsed": elapsed
            }

        except Exception as e:
            logger.error(f"종목 스크리닝 실패: {e}")
            self.notifier.send_error_alert("종목 스크리닝", str(e))
            return {"success": False, "error": str(e)}
    
    async def _rescore_midweek_loss_stocks(self) -> None:
        """
        주중 교체 손실 종목 재평가

        탈락 테마의 손실 종목을 screen_stocks_in_theme로 개별 평가.
        final_score >= MIDWEEK_LOSS_RESCORE_THRESHOLD이면 유지 (큐에서 제거).
        """
        logger.info("\n🔄 주중 교체 — 손실 종목 재평가")
        threshold = settings.MIDWEEK_LOSS_RESCORE_THRESHOLD
        keep_list = []
        drop_list = []

        from modules.stock_screener.screener import screen_stocks_in_theme

        # 탈락 테마 정보 (스크리닝용)
        dropped_theme = {
            "name": self._midweek_dropped_theme,
            "score": 0,
        }

        stock_codes = [s["stock_code"] for s in self._midweek_loss_queue]

        try:
            passed, failed = await asyncio.to_thread(
                screen_stocks_in_theme,
                theme=dropped_theme,
                stock_codes=stock_codes,
                max_stocks=len(stock_codes),
            )

            # passed 종목의 final_score 확인
            passed_map = {p["code"]: p.get("final_score", 0) for p in passed}

            for stock in self._midweek_loss_queue:
                code = stock["stock_code"]
                score = passed_map.get(code, 0)
                if score >= threshold:
                    keep_list.append(stock)
                    logger.info(
                        f"   ✅ {stock['stock_name']} — 재평가 통과 "
                        f"({score:.1f} >= {threshold}) → 유지"
                    )
                else:
                    drop_list.append(stock)
                    logger.info(
                        f"   ❌ {stock['stock_name']} — 재평가 탈락 "
                        f"({score:.1f} < {threshold}) → {settings.midweek_loss_time_str} 매도"
                    )

        except Exception as e:
            logger.error(f"   손실 종목 재평가 실패: {e} — 전부 매도 대상으로 유지")
            drop_list = list(self._midweek_loss_queue)
            keep_list = []

        # 큐 업데이트: 유지 종목은 제거, 탈락 종목만 남김
        self._midweek_loss_queue = drop_list

        if keep_list:
            self.notifier.send_message(
                f"🔄 주중 교체 — 손실 종목 재평가 결과\n\n"
                f"✅ 유지: {len(keep_list)}개 (기존 손절/트레일링 적용)\n"
                + "\n".join(f"  - {s['stock_name']} ({s['profit_rate']:+.1%})" for s in keep_list)
                + (f"\n\n❌ {settings.midweek_loss_time_str} 매도 예정: {len(drop_list)}개\n"
                   + "\n".join(f"  - {s['stock_name']} ({s['profit_rate']:+.1%})" for s in drop_list)
                   if drop_list else "")
            )

    async def _run_observation(self, observer: CandidateObserver):
        """관찰 루프 실행 헬퍼"""
        try:
            self.observation_result = await observer.start_observation()
        except Exception as e:
            logger.error(f"관찰 실패: {e}")
            self.observation_result = None

    # ===== 매수 실행 =====

    async def execute_buy_orders(self) -> dict:
        """
        빈 슬롯 매수 실행 (09:25)

        보유 종목은 손절/트레일링으로만 매도 (모니터링 담당).
        여기서는 빈 슬롯에만 AI 추천 종목을 신규 매수한다.

        Returns:
            실행 결과
        """
        if self.trading_paused:
            logger.info("⏸️ 매매 일시정지 중 — 매수 스킵")
            self.notifier.send_message(f"⏸️ {settings.buy_time_str} 매수 스킵 (일시정지 중)")
            return {"success": True, "paused": True}

        # Phase 0.5: 시장 위기 방어 (Market Guard)
        cash_ratio = 1.0
        if settings.MARKET_GUARD_ENABLED:
            from modules.market_guard import MarketGuard, MarketStatus
            guard = MarketGuard()
            market_status, market_info = await asyncio.to_thread(guard.check)
            kr = market_info.get("kospi_rate") or 0
            kd = market_info.get("kosdaq_rate") or 0
            reason = market_info.get("reason", "")

            if market_status == MarketStatus.CRISIS:
                msg = f"🚨 시장 폭락 감지 — 매수 전면 스킵\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%\n사유: {reason}"
                logger.warning(msg)
                self.notifier.send_message(msg)
                return {"success": True, "market_guard": "crisis", "skipped": True}

            elif market_status == MarketStatus.DANGER:
                if settings.MARKET_GUARD_DELAY_ENABLED:
                    # A3안: EARLY_BUY 모드는 35분 → 15분으로 단축 (09:05+15=09:20 재체크, A3 의미 보존)
                    delay_min = (
                        settings.EARLY_BUY_MARKET_GUARD_DELAY_MINUTES
                        if settings.EARLY_BUY_ENABLED
                        else settings.MARKET_GUARD_DELAY_MINUTES
                    )
                    msg = f"⚠️ 시장 급락 감지 — {delay_min}분 후 재체크\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%"
                    logger.warning(msg)
                    self.notifier.send_message(msg)
                    await asyncio.sleep(delay_min * 60)
                    market_status, market_info = await asyncio.to_thread(guard.check)
                    kr = market_info.get("kospi_rate") or 0
                    kd = market_info.get("kosdaq_rate") or 0
                    if market_status in (MarketStatus.CRISIS, MarketStatus.DANGER):
                        msg = f"🚫 시장 미회복 — 금일 매수 스킵\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%"
                        logger.warning(msg)
                        self.notifier.send_message(msg)
                        return {"success": True, "market_guard": "danger_no_recovery", "skipped": True}
                    elif market_status == MarketStatus.CAUTION:
                        cash_ratio = settings.MARKET_GUARD_CAUTION_RATIO
                        reason = market_info.get("reason", "")
                        msg = f"📈 시장 부분 회복 — {int(cash_ratio*100)}% 축소 진입\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%\n사유: {reason}"
                    else:
                        reason = market_info.get("reason", "")
                        msg = f"📈 시장 회복 — 정상 진입\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%\n사유: {reason}"
                    logger.info(msg)
                    self.notifier.send_message(msg)
                else:
                    msg = f"🚫 시장 급락 — 매수 스킵 (지연 비활성)\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%"
                    logger.warning(msg)
                    self.notifier.send_message(msg)
                    return {"success": True, "market_guard": "danger", "skipped": True}

            elif market_status == MarketStatus.CAUTION:
                cash_ratio = settings.MARKET_GUARD_CAUTION_RATIO
                msg = f"⚠️ 시장 약세 — {int(cash_ratio*100)}% 축소 진입\nKOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%"
                logger.info(msg)
                self.notifier.send_message(msg)

            else:
                logger.info(f"[MarketGuard] 시장 정상 — KOSPI {kr:+.2f}% / KOSDAQ {kd:+.2f}%")

        logger.info("=" * 70)
        logger.info(f"💰 빈 슬롯 매수 실행 ({settings.buy_time_str})")
        logger.info("=" * 70)

        # 매수 요약용 상태 초기화 (이전 날 데이터 누출 방지)
        self._held_excluded = []
        self._today_sold_excluded = []
        self._morning_excluded = []
        self._diversity_excluded = []
        self._slot_excluded = []
        self._buy_skip_reason = ''
        self._theme_relaxation_applied = False
        self._theme_relaxation_count = 0

        # CRITICAL Phase 0-pre: Screening↔Buy 동기화 (A3안 09:05 매수 race 방지)
        # 스크리닝 진행 중이면 _screening_done.set() 까지 대기 (최대 180초). 초과 시 alert + 진행.
        if not self._screening_done.is_set():
            logger.info("⏳ 종목 스크리닝 완료 대기 중...")
            try:
                await asyncio.wait_for(self._screening_done.wait(), timeout=180)
                logger.info("   ✅ 스크리닝 완료 — 매수 진행")
            except asyncio.TimeoutError:
                err_msg = f"🚨 {settings.buy_time_str} 매수: 스크리닝 180초 timeout — 가용 데이터로 진행"
                logger.error(err_msg)
                self.notifier.send_message(err_msg)

        # Phase 0: 관찰 완료 대기 (EARLY_BUY 모드는 observer 미생성이라 즉시 통과)
        if self._observer_task and not self._observer_task.done():
            logger.info("⏳ 관찰 완료 대기 중...")
            timeout_min = (
                settings.EARLY_BUY_OBSERVATION_MINUTES
                if settings.EARLY_BUY_ENABLED
                else settings.MORNING_OBSERVATION_MINUTES
            )
            try:
                await asyncio.wait_for(self._observer_task, timeout=timeout_min * 60)
            except asyncio.TimeoutError:
                logger.warning("관찰 타임아웃 - 가용 데이터로 진행")

        # Phase 1: 안전 체크
        if not self.today_ai_analysis:
            logger.warning("AI 분석 결과 없음 - 매수 스킵")
            self.notifier.send_message(
                f"⚠️ {settings.buy_time_str} 매수 스킵\n\n"
                f"AI 분석 결과가 없습니다\n"
                f"{settings.screening_time_str} 스크리닝 결과를 확인하세요"
            )
            return {"success": False, "reason": "AI 분석 없음"}

        # Phase 2: 현재 보유 종목 로드
        db = Database()
        db.connect()
        current_holdings = db.get_portfolio(status='holding')
        db.close()
        held_codes = {h['stock_code'] for h in current_holdings}
        held_count = len(current_holdings)
        logger.info(f"   현재 보유: {held_count}종목 {[h['stock_name'] for h in current_holdings]}")

        # Phase 3: 가용 슬롯 계산
        available_slots = settings.MAX_POSITIONS - held_count
        logger.info(f"   가용 슬롯: {available_slots} (MAX={settings.MAX_POSITIONS}, 보유={held_count})")

        if available_slots <= 0:
            logger.info("   슬롯 없음 - 풀 포지션 유지")
            self._send_buy_summary(current_holdings, [], 0)
            return {"success": True, "held": held_count, "bought": 0}

        if not self.today_candidates:
            logger.warning("   매수 후보 없음 - 신규 매수 스킵")
            self._send_buy_summary(current_holdings, [], 0)
            return {"success": True, "held": held_count, "bought": 0}

        # Phase 4: 현금 + 총자산 조회
        if self.test_mode:
            available_cash = settings.TOTAL_CAPITAL
            total_capital = settings.TOTAL_CAPITAL
        else:
            available_cash = self.trading_engine.get_orderable_cash()
            balance = self.trading_engine.get_balance()
            if available_cash <= 0:
                available_cash = balance.get("cash", 0)
            # 총자산: KIS 총평가액 (현금 + 보유 평가액 합산)
            total_capital = balance.get("total_value", 0)
            if total_capital <= 0:
                # 폴백: 초기 자본 또는 가용현금 중 큰 값
                total_capital = max(settings.TOTAL_CAPITAL, available_cash)
        # Market Guard 투자금 축소 적용 (available_cash에만, total_capital은 객관 기준 유지)
        if cash_ratio < 1.0:
            available_cash = int(available_cash * cash_ratio)
            logger.info(f"   [MarketGuard] 투자금 {int(cash_ratio*100)}% 축소 적용")

        logger.info(f"   가용 현금: {available_cash:,}원 | 총자산: {total_capital:,}원")

        if available_cash < 100_000:
            logger.warning("   현금 부족 (<10만원) - 신규 매수 스킵")
            self._buy_skip_reason = f"현금 부족 ({available_cash:,.0f}원)"
            self._send_buy_summary(current_holdings, [], 0)
            return {"success": True, "held": held_count, "bought": 0}

        # Phase 5: 신규 후보 필터링 (보유 종목 + 당일 매도 종목 제외)
        today_sold_codes = {t['stock_code'] for t in self.today_trades if t.get('action') == 'sell'}
        exclude_codes = held_codes | today_sold_codes
        self._held_excluded = [c for c in self.today_candidates if c['stock_code'] in held_codes]
        self._today_sold_excluded = [c for c in self.today_candidates if c['stock_code'] in today_sold_codes and c['stock_code'] not in held_codes]
        new_candidates = [c for c in self.today_candidates if c['stock_code'] not in exclude_codes]
        logger.info(f"   신규 후보 (보유 제외): {len(new_candidates)}개")
        if today_sold_codes:
            logger.info(f"   당일 매도 제외: {[c['stock_name'] for c in self._today_sold_excluded]}")

        # 모닝 필터 적용
        self._morning_excluded = []
        if settings.ENABLE_MORNING_FILTER and new_candidates:
            filter_result = await asyncio.to_thread(
                self.morning_screener.filter_candidates,
                new_candidates,
                settings.MORNING_OBSERVATION_MINUTES,
                self.observation_result
            )
            filtered_new = filter_result.passed_stocks if filter_result.passed_stocks else []
            self._morning_excluded = filter_result.excluded_stocks or []
            logger.info(f"   모닝 필터 통과: {len(filtered_new)}개")
        else:
            filtered_new = new_candidates

        if not filtered_new:
            logger.warning("   필터 통과 종목 없음 - 신규 매수 스킵")
            self._send_buy_summary(current_holdings, [], 0)
            return {"success": True, "held": held_count, "bought": 0}

        # Phase 5.5: 테마/섹터 분산 제한
        # 테마명 → 카테고리(섹터) 매핑 구축
        theme_to_category = {}
        for t in self.today_themes:
            t_name = t.get("theme", t.get("name", ""))
            t_cat = t.get("category", "기타")
            if t_name:
                theme_to_category[t_name] = t_cat

        # 기존 보유 종목의 테마/섹터별 카운트
        theme_counts = {}
        sector_counts = {}
        for h in current_holdings:
            h_theme = h.get("theme", "")
            if h_theme:
                theme_counts[h_theme] = theme_counts.get(h_theme, 0) + 1
                h_sector = theme_to_category.get(h_theme, "기타")
                sector_counts[h_sector] = sector_counts.get(h_sector, 0) + 1

        # Phase 6: 포트폴리오 최적화 (슬롯 비례 자본 배분)
        filtered_codes = {c['stock_code'] for c in filtered_new}
        all_ai_candidates = [
            s for s in self.today_ai_analysis
            if s['code'] in filtered_codes
        ]

        # 테마/섹터 분산 필터 적용 (헬퍼 호출 — 1차 한도 + 조건부 2차 한도)
        relax_max = (
            settings.MAX_STOCKS_PER_THEME_RELAXED
            if settings.THEME_SLOT_RELAXATION_ENABLED
            else None
        )
        diversified_candidates, diversity_excluded, relax_applied = apply_diversity_filter(
            candidates=all_ai_candidates,
            theme_to_category=theme_to_category,
            initial_theme_counts=theme_counts,
            initial_sector_counts=sector_counts,
            available_slots=available_slots,
            max_per_theme=settings.MAX_STOCKS_PER_THEME,
            max_per_sector=settings.MAX_STOCKS_PER_SECTOR,
            relax_max=relax_max,
        )
        self._diversity_excluded.extend(diversity_excluded)
        self._theme_relaxation_applied = relax_applied
        self._theme_relaxation_count = sum(1 for c in diversified_candidates if c.get("_relaxed"))

        # 분산 필터 결과 로그
        for ex in diversity_excluded:
            logger.info(f"   분산 제한 제외: {ex.get('name')} — {ex.get('_reason')}")
        if relax_applied:
            logger.info(
                f"   ⚙️ 테마 슬롯 한도 상향 적용: {self._theme_relaxation_count}건 "
                f"({settings.MAX_STOCKS_PER_THEME}→{settings.MAX_STOCKS_PER_THEME_RELAXED})"
            )

        new_ai_stocks = diversified_candidates  # 헬퍼가 available_slots까지만 반환
        self._slot_excluded = []  # 헬퍼가 슬롯 초과를 직접 처리하므로 비워둠

        if new_ai_stocks:
            # 가용현금 기반 슬롯 배분 (수익 재투자 반영)
            # 종목당 상한: (총자산 × SWING_CAPITAL_RATIO) ÷ MAX_POSITIONS
            # 2026-05-23 동적 자본 분리: 스윙 90% (잔여 10% + idle은 종가베팅 풀)
            # 환경변수 SWING_CAPITAL_RATIO 또는 기본 0.9 (롤백 시 1.0)
            swing_capital_ratio = float(os.getenv("SWING_CAPITAL_RATIO", "0.9"))
            swing_capital_pool = int(total_capital * swing_capital_ratio)
            max_per_stock = swing_capital_pool // settings.MAX_POSITIONS
            per_slot_capital = min(available_cash // available_slots, max_per_stock)
            logger.info(
                f"   💰 스윙 자본 풀: 총자산 {int(total_capital):,}원 × "
                f"{swing_capital_ratio:.0%} = {swing_capital_pool:,}원 / "
                f"{settings.MAX_POSITIONS}슬롯 → 종목당 상한 {max_per_stock:,.0f}원"
            )

            # v17 분할 진입: 1차 진입은 TRANCHE_FIRST_RATIO(기본 50%)만 사용.
            # 나머지는 모니터에서 +5% 도달 시 2차 진입(불타기)으로 활용 (보유 종료까지 대기).
            if settings.TRANCHE_ENTRY_ENABLED:
                tranche_ratio = settings.TRANCHE_FIRST_RATIO
                per_slot_capital_full = per_slot_capital
                per_slot_capital = int(per_slot_capital * tranche_ratio)
                logger.info(
                    f"   🔀 분할 진입 활성: 1차 {tranche_ratio*100:.0f}% 사용 "
                    f"({per_slot_capital_full:,.0f}원 → {per_slot_capital:,.0f}원/종목, "
                    f"2차 trigger=+{settings.PYRAMID_TRIGGER_PCT*100:.0f}% first 기준)"
                )

            target_capital = per_slot_capital * len(new_ai_stocks)
            capital_for_new = min(target_capital, available_cash)
            logger.info(f"   슬롯 배분: {per_slot_capital:,.0f}원/종목 × {len(new_ai_stocks)}종목 (상한: {max_per_stock:,.0f}원, 총자산÷{settings.MAX_POSITIONS})")
            logger.info(f"   실제 배분: {capital_for_new:,.0f}원 (가용: {available_cash:,.0f}원)")

            optimization_result = await asyncio.to_thread(
                run_daily_optimization,
                verified_stocks=new_ai_stocks,
                capital=capital_for_new,
                strategy="score_based",
                save_to_db=False,
                use_mock_data=self.test_mode
            )
            new_buy_orders = optimization_result.get("orders", [])
        else:
            new_buy_orders = []

        logger.info(f"   최적화 후 매수 대상: {len(new_buy_orders)}개")

        # Phase 7: 매수 실행
        bought_count = 0
        if new_buy_orders:
            if self.test_mode:
                logger.info(f"   [테스트 모드] 매수 스킵: {len(new_buy_orders)}건")
                bought_count = len(new_buy_orders)
                self.today_orders = new_buy_orders
            else:
                buy_result = await asyncio.to_thread(
                    self.trading_engine.execute_portfolio,
                    new_buy_orders,
                    save_to_db=True
                )
                self.today_orders = new_buy_orders

                for order in buy_result.get("orders", []):
                    if order.get("success"):
                        bought_count += 1
                        filled_price = order.get("filled_price") or order.get("price", 0)
                        quantity = order.get("quantity", 0)
                        requested_qty = order.get("requested_quantity", quantity)
                        # v17: 분할 진입 활성 시 1차 라벨 + ATR 박제값 표시
                        tranche_label = None
                        atr_value = None
                        if settings.TRANCHE_ENTRY_ENABLED:
                            tranche_label = f"1/2 진입 ({settings.TRANCHE_FIRST_RATIO*100:.0f}%)"
                            try:
                                from modules.atr_calculator import compute_atr
                                atr_value = compute_atr(order.get("stock_code", ""), period=settings.ATR_PERIOD)
                            except Exception:
                                pass
                        self.notifier.send_buy_alert(
                            order.get("stock_name", ""),
                            order.get("stock_code", ""),
                            quantity,
                            filled_price,
                            tranche_label=tranche_label,
                            atr_at_buy=atr_value if atr_value and atr_value > 0 else None,
                        )
                        # limit_aggressive 부분체결 경고
                        remaining = order.get("remaining_shares", 0)
                        if remaining and remaining > 0:
                            self.notifier.send_message(
                                f"⚠️ 부분체결 경고\n"
                                f"종목: {order.get('stock_name', '')} ({order.get('stock_code', '')})\n"
                                f"요청: {requested_qty}주 / 체결: {quantity}주 / 미체결: {remaining}주\n"
                                f"재시도: {order.get('retry_count', 0)}회 / 소요: {order.get('elapsed_seconds', 0)}s\n"
                                f"메시지: {order.get('message', '')}"
                            )
                        self.today_trades.append({
                            "action": "buy",
                            "stock_code": order.get("stock_code", ""),
                            "stock_name": order.get("stock_name", ""),
                            "shares": quantity,
                            "price": filled_price
                        })

        # Phase 8: 텔레그램 요약 발송
        self._send_buy_summary(current_holdings, new_buy_orders, bought_count)

        logger.info(f"\n✅ 매수 완료: 보유={held_count} 신규={bought_count}")
        return {
            "success": True,
            "held": held_count,
            "bought": bought_count,
            "available_slots": available_slots
        }

    def _send_buy_summary(
        self,
        current_holdings: list[dict],
        new_buy_orders: list[dict],
        bought_count: int
    ) -> None:
        """09:25 매수 리포트 텔레그램 발송"""
        from config import now_kst
        lines = [f"💰 {now_kst().strftime('%H:%M')} 매수 리포트\n"]

        # 보유 유지 종목
        if current_holdings:
            lines.append(f"📦 보유 유지: {len(current_holdings)}종목")
            for h in current_holdings:
                lines.append(f"  - {h['stock_name']} ({h['stock_code']})")

        # --- 탈락 종목 수집 (조기 리턴 전에 반드시 수집) ---
        excluded_lines = []

        # 1) 보유 중복 제외
        for s in getattr(self, '_held_excluded', []):
            name = s.get('stock_name', s.get('name', s.get('stock_code', '?')))
            excluded_lines.append(f"  - {name} — 보유 중복")

        # 1-1) 당일 매도 제외
        for s in getattr(self, '_today_sold_excluded', []):
            name = s.get('stock_name', s.get('name', s.get('stock_code', '?')))
            excluded_lines.append(f"  - {name} — 당일 매도")

        # 2) 모닝필터 제외 (갭/수급/거래량/체결강도/트렌드)
        for s in getattr(self, '_morning_excluded', []):
            name = s.get('stock_name', s.get('name', s.get('stock_code', '?')))
            reason = s.get('exclusion_reason', '')
            if not reason:
                gap = s.get('gap_percent', 0)
                reason = f"갭 {gap:+.1f}%" if gap else "모닝필터"
            excluded_lines.append(f"  - {name} — {reason}")

        # 3) 테마/섹터 분산 제외
        for s in getattr(self, '_diversity_excluded', []):
            name = s.get('name', s.get('stock_name', s.get('code', '?')))
            reason = s.get('_reason', '분산 제한')
            excluded_lines.append(f"  - {name} — {reason}")

        # 4) 슬롯 부족
        for s in getattr(self, '_slot_excluded', []):
            name = s.get('name', s.get('stock_name', s.get('code', '?')))
            excluded_lines.append(f"  - {name} — 슬롯 부족")

        # --- 매수 없음 + 탈락 사유 표시 ---
        if not new_buy_orders:
            # 파이프라인 요약
            screening_count = len(self.today_candidates) if self.today_candidates else 0
            if screening_count > 0:
                lines.append(f"\n📋 스크리닝 {screening_count}개 → 매수 0개")

            # 조기 종료 사유 (현금 부족 등)
            skip_reason = getattr(self, '_buy_skip_reason', '')
            if skip_reason:
                lines.append(f"⚠️ 사유: {skip_reason}")
                self._buy_skip_reason = ''  # 1회성 소비

            if excluded_lines:
                lines.append(f"\n❌ 탈락: {len(excluded_lines)}종목")
                lines.extend(excluded_lines)
            elif not current_holdings and screening_count == 0:
                lines.append("보유 0개, 매수 후보 없음")

            self.notifier.send_message("\n".join(lines))
            return

        # AI 분석 lookup
        ai_lookup = {s['code']: s for s in self.today_ai_analysis} if self.today_ai_analysis else {}

        # 기업개요 크롤링 (매수 종목만)
        overviews = {}
        if new_buy_orders:
            try:
                from modules.stock_screener.kis_api import KISApi
                api = KISApi()
                for o in new_buy_orders:
                    code = o.get('stock_code', '')
                    if code:
                        overviews[code] = api.get_company_overview(code)
            except Exception as e:
                logger.debug(f"기업개요 조회 실패: {e}")

        # 신규 매수 상세
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━")
        if getattr(self, "_theme_relaxation_applied", False):
            lines.append(
                f"⚙️ 테마 슬롯 한도 상향 적용: "
                f"{getattr(self, '_theme_relaxation_count', 0)}건 "
                f"({settings.MAX_STOCKS_PER_THEME}→{settings.MAX_STOCKS_PER_THEME_RELAXED})"
            )
        lines.append(f"\n📥 신규 매수: {bought_count}종목\n")
        per_stock_messages: dict[str, str] = {}
        for i, o in enumerate(new_buy_orders, 1):
            code = o.get('stock_code', '')
            name = o.get('stock_name', code)
            amount = o.get('amount', 0)
            stop_loss = o.get('stop_loss', 0)
            take_profit = o.get('take_profit', 0)
            price = o.get('price', 0)
            ai = ai_lookup.get(code, {})

            block_start = len(lines)
            lines.append(f"{i}. {name} ({code}) — {amount:,}원")

            # 테마
            theme = ai.get('theme', '')
            if theme:
                lines.append(f"🏷 테마: {theme}")

            # 기업개요
            overview = overviews.get(code, '')
            if overview:
                lines.append(f"🏢 {overview}")

            # 수급 (억원 변환)
            foreign = ai.get('foreign_net', 0)
            institution = ai.get('institution_net', 0)
            if foreign or institution:
                f_str = f"{foreign / 1e8:+,.0f}억" if foreign else "0"
                i_str = f"{institution / 1e8:+,.0f}억" if institution else "0"
                lines.append(f"📊 수급: 외국인 {f_str} / 기관 {i_str}")

            # 기술 지표
            rsi = ai.get('rsi', 0)
            vol_ratio = ai.get('volume_ratio', 0)
            ma = ai.get('ma_alignment', '')
            indicators = []
            if rsi:
                indicators.append(f"RSI {rsi:.0f}")
            if ma:
                ma_mark = "✓" if ma in ("bullish", "정배열") else "✗"
                indicators.append(f"MA정배열 {ma_mark}")
            if vol_ratio:
                indicators.append(f"거래량 {vol_ratio:.1f}배")
            if indicators:
                lines.append(f"📈 {' | '.join(indicators)}")

            # AI 분석
            sentiment = ai.get('ai_sentiment', 0)
            confidence = ai.get('ai_confidence', 0)
            reason = ai.get('ai_reason', '')
            if sentiment:
                conf_pct = f"{confidence * 100:.0f}%" if confidence else ""
                ai_line = f"🤖 AI {sentiment:.1f}/10"
                if conf_pct:
                    ai_line += f" ({conf_pct})"
                if reason:
                    ai_line += f" — {reason}"
                lines.append(ai_line)

            # 손절/목표
            if price and price > 0:
                sl_pct = ((stop_loss / price) - 1) * 100 if stop_loss else 0
                tp_pct = ((take_profit / price) - 1) * 100 if take_profit else 0
                lines.append(f"⚡ 손절 {sl_pct:+.0f}% / 목표 {tp_pct:+.0f}%")
            elif stop_loss or take_profit:
                lines.append(f"⚡ 손절 {stop_loss:,.0f}원 / 목표 {take_profit:,.0f}원")

            # 종목별 메시지 블록 추출 (빈 줄 추가 전 시점까지)
            if code:
                per_stock_messages[code] = "\n".join(lines[block_start:]).rstrip()

            lines.append("")  # 종목 간 빈 줄

        # 매수 메시지 DB 저장 (송신 실패와 무관하게 보존)
        for code, msg in per_stock_messages.items():
            try:
                rowcount = self.db.update_portfolio_buy_message(code, msg)
                if rowcount == 0:
                    logger.warning(f"buy_message UPDATE 0 row: {code} (holding row 없음)")
            except Exception as e:
                logger.warning(f"buy_message 저장 실패 {code}: {e}")

        # 탈락 종목 (매수 있는 경우에도 표시)
        if excluded_lines:
            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"\n❌ 탈락: {len(excluded_lines)}종목")
            lines.extend(excluded_lines)

        final_msg = "\n".join(lines)
        if len(final_msg) > 4000:
            final_msg = final_msg[:4000] + "\n…(메시지 길이 초과로 일부 생략)"
        self.notifier.send_message(final_msg)
    
    # ===== 모니터링 (V2: 분할 익절 + 트레일링 스탑) =====
    
    async def start_monitoring(self) -> None:
        """실시간 모니터링 시작 (V2)

        09:00 조기 가동(Phase 1) 도입 후 본 함수는 재호출 가능:
        - 09:00 첫 호출: 기존 보유분 모니터링 시작
        - 09:26 재호출: 09:25 매수분 포함 모니터 재시작 (KIS WebSocket이 활성 세션에
          동적 SUBSCRIBE 메시지 전송 메커니즘이 없어 stop+start 패턴 사용 — 5~10초 공백)
        """
        # 이미 가동 중이면 종료 후 재시작 (신규 매수분 포함 위해)
        if self.monitor and getattr(self.monitor, "_running", False):
            logger.info("📊 모니터링 재시작 (신규 매수분 포함)")
            await self.stop_monitoring()

        logger.info("=" * 70)
        logger.info("📊 실시간 모니터링 V2 시작")
        logger.info(f"   - 분할 익절: +{settings.TAKE_PROFIT_1:.0%}/+{settings.TAKE_PROFIT_2:.0%}/+{settings.TAKE_PROFIT_3:.0%}")
        logger.info(f"   - 트레일링 스탑: L1 -{settings.TRAIL_LEVEL1_PCT:.0%} / L2 -{settings.TRAIL_LEVEL2_PCT:.0%} / L3 -{settings.TRAIL_LEVEL3_PCT:.0%}")
        logger.info(f"   - 보유 기간: 수익 {settings.MAX_HOLD_DAYS_PROFIT}일, 손실 {settings.MAX_HOLD_DAYS_LOSS}일")
        logger.info("=" * 70)

        self.monitor = PortfolioMonitorV2(use_mock=self.test_mode)

        # 포지션 로드
        self.monitor.load_positions_from_db()

        # 콜백 설정
        self.monitor.on_stop_loss = self._on_stop_loss
        self.monitor.on_partial_profit = self._on_partial_profit
        self.monitor.on_trailing_stop = self._on_trailing_stop
        self.monitor.on_trailing_level_change = self._on_trailing_level_change
        self.monitor.on_max_hold_sell = self._on_max_hold_sell
        self.monitor.on_sell_failed = self._on_sell_failed

        # 모니터링 시작 (백그라운드)
        asyncio.create_task(self.monitor.start_monitoring())

    async def stop_monitoring(self) -> None:
        """실시간 모니터링 종료 (15:30 또는 재시작 시점)

        15:30 정상 종료에서는 sell_lock 일괄 해제 (다음 거래일 09:00 가동 시 깨끗한 상태).
        재시작 시점(09:26)에서도 호출되지만 같은 거래일 내라 clear는 영향 없음 (sell_lock 비어있음).
        """
        if self.monitor:
            await self.monitor.stop_monitoring()
            logger.info("📊 모니터링 종료")
        # SellLock 일괄 해제 — 재호출에 안전 (비어있어도 noop)
        cleared = sell_lock.clear_all()
        if cleared > 0:
            logger.info(f"   [SellLock] 일괄 해제: {cleared}건")
        # v17 BuyLock 일괄 해제 — 2차 진입 발주 도중 예외 시 누수 차단 (2차 안전망)
        try:
            from modules.trading_engine.buy_lock import buy_lock
            buy_cleared = buy_lock.clear_all()
            if buy_cleared > 0:
                logger.info(f"   [v17 BuyLock] 일괄 해제: {buy_cleared}건")
        except Exception as e:
            logger.debug(f"[v17] BuyLock clear_all 실패: {e}")
    
    def _on_stop_loss(self, position, price) -> None:
        """손절 발동 콜백"""
        try:
            profit_rate = position.profit_rate * 100
            pnl_amount = int((price - position.buy_price) * position.remaining_shares)
            self.notifier.send_message(
                f"🔻 손절 발동\n\n"
                f"📉 {position.stock_name} ({position.stock_code})\n"
                f"💰 매수가: {int(position.buy_price):,}원 → 매도가: {int(price):,}원\n"
                f"📊 수량: {position.remaining_shares}주\n"
                f"🔻 손실: {profit_rate:.2f}% ({pnl_amount:+,}원)\n"
                f"📅 보유일: {position.hold_days}일\n\n"
                f"⚠️ 손절가에 도달하여 자동 매도되었습니다."
            )
            self.today_trades.append({
                "action": "sell", "stock_code": position.stock_code,
                "stock_name": position.stock_name, "shares": position.remaining_shares,
                "price": int(price), "reason": "손절"
            })
        except Exception as e:
            logger.error(f"_on_stop_loss 콜백 오류: {e}")

    def _on_partial_profit(self, position, price, stage: int, sell_shares: int) -> None:
        """분할 익절 발동 콜백"""
        try:
            profit_rate = position.profit_rate * 100
            pnl_amount = int((price - position.buy_price) * sell_shares)
            threshold = {1: settings.TAKE_PROFIT_1, 2: settings.TAKE_PROFIT_2, 3: settings.TAKE_PROFIT_3}

            self.notifier.send_message(
                f"🔺 {stage}차 익절 발동!\n\n"
                f"📈 {position.stock_name} ({position.stock_code})\n"
                f"💰 매수가: {int(position.buy_price):,}원 → 현재가: {int(price):,}원\n"
                f"📊 매도: {sell_shares}주 (수익금: {pnl_amount:+,}원)\n"
                f"   남은 수량: {position.remaining_shares}/{position.shares}주\n"
                f"🔺 수익률: {profit_rate:+.2f}%\n"
                f"📈 최고가: {int(position.highest_price):,}원 (최대 {position.max_profit_rate * 100:+.1f}%)\n"
                f"📅 보유일: {position.hold_days}일\n\n"
                f"✅ +{threshold[stage]:.0%} 도달 → {stage}차 분할 익절 실행"
            )
            self.today_trades.append({
                "action": "sell", "stock_code": position.stock_code,
                "stock_name": position.stock_name, "shares": sell_shares,
                "price": int(price), "reason": f"{stage}차 익절"
            })
        except Exception as e:
            logger.error(f"_on_partial_profit 콜백 오류: {e}")

    def _on_trailing_stop(self, position, price) -> None:
        """트레일링 스탑 발동 콜백"""
        try:
            profit_rate = position.profit_rate * 100
            pnl_emoji = "🔺" if profit_rate >= 0 else "🔻"
            pnl_label = "수익" if profit_rate >= 0 else "손실"
            pnl_amount = int((price - position.buy_price) * position.remaining_shares)
            level = position.trailing_level
            level_pct = {
                1: f"{settings.TRAIL_LEVEL1_PCT:.0%}",
                2: f"{settings.TRAIL_LEVEL2_PCT:.0%}",
                3: f"{settings.TRAIL_LEVEL3_PCT:.0%}",
            }.get(level, "?%")

            self.notifier.send_message(
                f"📉 트레일링 스탑 발동 L{level}\n\n"
                f"📊 {position.stock_name} ({position.stock_code})\n"
                f"💰 매수가: {int(position.buy_price):,}원 → 매도가: {int(price):,}원\n"
                f"📈 최고가: {int(position.highest_price):,}원 (최대 {position.max_profit_rate * 100:+.1f}%)\n"
                f"📊 수량: {position.remaining_shares}주\n"
                f"{pnl_emoji} {pnl_label}: {abs(profit_rate):.2f}% ({pnl_amount:+,}원)\n"
                f"📅 보유일: {position.hold_days}일\n\n"
                f"✅ 고점 대비 -{level_pct} 하락 → L{level} 트레일링 매도"
            )
            self.today_trades.append({
                "action": "sell", "stock_code": position.stock_code,
                "stock_name": position.stock_name, "shares": position.remaining_shares,
                "price": int(price), "reason": f"트레일링L{level}"
            })
        except Exception as e:
            logger.error(f"_on_trailing_stop 콜백 오류: {e}")

    def _on_trailing_level_change(self, position, old_level: int, new_level: int) -> None:
        """트레일링 레벨 변경 콜백 (L0→L1, L1→L2, L2→L3)"""
        try:
            profit_rate = position.profit_rate * 100
            level_info = {
                1: ("L1 활성화",
                    f"+{settings.TRAIL_ACTIVATION_PCT:.0%}",
                    f"고점 -{settings.TRAIL_LEVEL1_PCT:.0%}",
                    "본전 손절 설정"),
                2: ("L2 격상",
                    f"+{settings.TRAIL_LEVEL2_THRESHOLD:.0%}",
                    f"고점 -{settings.TRAIL_LEVEL2_PCT:.0%}",
                    "트레일링 강화"),
                3: ("L3 격상",
                    f"+{settings.TRAIL_LEVEL3_THRESHOLD:.0%}",
                    f"고점 -{settings.TRAIL_LEVEL3_PCT:.0%}",
                    "최대 수익 보호"),
            }
            title, threshold, trail, desc = level_info.get(new_level, ("레벨 변경", "?", "?", ""))

            self.notifier.send_message(
                f"📊 트레일링 {title}\n\n"
                f"📈 {position.stock_name} ({position.stock_code})\n"
                f"💰 매수가: {int(position.buy_price):,}원 → 현재가: {int(position.current_price):,}원\n"
                f"🔺 수익: +{profit_rate:.1f}% (기준: {threshold})\n"
                f"📈 최고가: {int(position.highest_price):,}원\n"
                f"🛡️ 트레일링: {trail} (스탑: {int(position.trailing_stop or 0):,}원)\n"
                f"📅 보유일: {position.hold_days}일\n\n"
                f"ℹ️ L{old_level} → L{new_level}: {desc}",
                disable_notification=True
            )
        except Exception as e:
            logger.error(f"_on_trailing_level_change 콜백 오류: {e}")

    def _on_max_hold_sell(self, position, price) -> None:
        """보유기간 초과 매도 콜백"""
        try:
            profit_rate = position.profit_rate * 100
            pnl_emoji = "🔺" if profit_rate >= 0 else "🔻"
            pnl_amount = int((price - position.buy_price) * position.remaining_shares)
            threshold_pct = settings.MIN_PROFIT_FOR_LONG_HOLD * 100
            max_days = settings.MAX_HOLD_DAYS_PROFIT if profit_rate >= threshold_pct else settings.MAX_HOLD_DAYS_LOSS

            self.notifier.send_message(
                f"⏰ 보유기간 초과 매도\n\n"
                f"📊 {position.stock_name} ({position.stock_code})\n"
                f"💰 매수가: {int(position.buy_price):,}원 → 매도가: {int(price):,}원\n"
                f"📊 수량: {position.remaining_shares}주\n"
                f"{pnl_emoji} 수익: {profit_rate:+.2f}% ({pnl_amount:+,}원)\n"
                f"📅 보유: {position.hold_days}일 / 최대 {max_days}일\n\n"
                f"⚠️ 최대 보유기간 초과로 자동 매도되었습니다."
            )
            self.today_trades.append({
                "action": "sell", "stock_code": position.stock_code,
                "stock_name": position.stock_name, "shares": position.remaining_shares,
                "price": int(price), "reason": SellReason.MAX_HOLD_DAYS.value
            })
        except Exception as e:
            logger.error(f"_on_max_hold_sell 콜백 오류: {e}")

    def _on_sell_failed(self, position, sell_type: str, reason: str) -> None:
        """매도 실패 콜백 (분할 익절 수량 부족, 주문 실패 등)"""
        try:
            profit_rate = position.profit_rate * 100
            self.notifier.send_message(
                f"🚨 매도 실패 알림\n\n"
                f"📊 {position.stock_name} ({position.stock_code})\n"
                f"❌ 유형: {sell_type}\n"
                f"📝 원인: {reason}\n"
                f"💰 현재가: {int(position.current_price):,}원 ({profit_rate:+.1f}%)\n"
                f"📊 보유: {position.remaining_shares}/{position.shares}주\n"
                f"📅 보유일: {position.hold_days}일\n\n"
                f"⚠️ 수동 확인이 필요합니다."
            )
        except Exception as e:
            logger.error(f"_on_sell_failed 콜백 오류: {e}")
    
    # ===== 테마 로테이션 =====
    
    async def check_theme_rotation(self) -> dict:
        """
        테마 로테이션 체크 (08:00)

        7일 단위로 메인 테마를 재평가합니다.
        점수 -20% 하락 또는 +15% 급등 시 즉시 변경됩니다.
        주중 교체: 전일 일별 점수 기반으로 탈락 테마 1개 교체.

        Returns:
            체크 결과
        """
        logger.info("=" * 70)
        logger.info("🔄 테마 로테이션 체크 (08:00)")
        logger.info("=" * 70)

        # 주중 교체 상태 초기화 (매일 리셋)
        self._midweek_sell_queue = []
        self._midweek_loss_queue = []
        self._midweek_dropped_theme = None
        self._midweek_new_theme = None

        try:
            # 현재 테마 정보 출력
            theme_info = self.theme_rotator.get_main_theme_info()
            if theme_info:
                logger.info(f"   현재 테마: {theme_info['theme_name']}")
                today_check = now_kst().date()
                is_makeup_day, _missed_check = is_makeup_reselection_day(today_check)
                # 중복 발화 방어: missed 이후 이미 재선정 발생했다면 보정 우회
                if (is_makeup_day and _missed_check and self._last_theme_rotation_date
                        and self._last_theme_rotation_date >= _missed_check):
                    is_makeup_day = False
                is_review_day = (self._last_theme_rotation_date is None or
                    today_check.weekday() == 1 or
                    is_makeup_day or
                    (today_check - self._last_theme_rotation_date).days >= 7)
                review_status = "오늘 재평가 예정" if is_review_day else f"보유 {theme_info['days_held']}일"
                logger.info(f"   상태: {review_status}")
                logger.info(f"   점수 변화: {theme_info['score_change_rate']:+.1%}")

                # 긴급 트리거: -20% 하락 또는 +15% 급등 시 강제 재선정
                score_change = theme_info.get('score_change_rate', 0)
                if score_change <= settings.THEME_CHANGE_THRESHOLD or score_change >= settings.THEME_SURGE_THRESHOLD:
                    logger.warning(
                        f"⚠️ 긴급 트리거 발동! 점수 변화: {score_change:+.1%} → 다음 08:30 강제 재선정"
                    )
                    self._last_theme_rotation_date = None  # 리셋 → 다음 08:30 강제 재선정
            else:
                logger.info("   메인 테마 미설정")

            # ===== 주중 테마 교체 판단 =====
            self._check_midweek_replacement()

            return {"success": True, "theme_info": theme_info}

        except Exception as e:
            logger.error(f"테마 로테이션 체크 실패: {e}")
            return {"success": False, "error": str(e)}

    def _check_midweek_replacement(self) -> None:
        """
        주중 테마 교체 판단 (check_theme_rotation에서 호출)

        조건:
        1. 기능 활성화 (MIDWEEK_REPLACEMENT_ENABLED)
        2. 화요일이 아님 (정규 재선정일)
        3. 활성 테마가 있음
        4. 최소 보유 기간(2영업일) 경과
        5. 전일 일별 점수 DB에 데이터 존재
        6. 절대 하한 + 상대 열세 조건 충족
        """
        if not settings.MIDWEEK_REPLACEMENT_ENABLED:
            logger.info("   주중 교체: 비활성화 상태")
            return

        today = now_kst().date()

        # 화요일은 정규 재선정일이므로 스킵
        # 화요일 휴장 시 보정 재선정일도 동일하게 미드위크 교체 스킵 (정규 재선정과 충돌 방지)
        is_makeup_today, missed_check = is_makeup_reselection_day(today)
        if (is_makeup_today and missed_check and self._last_theme_rotation_date
                and self._last_theme_rotation_date >= missed_check):
            is_makeup_today = False
        if today.weekday() == 1 or is_makeup_today:
            if today.weekday() == 1:
                label = "화요일"
            else:
                label = f"보정일(직전 화 {missed_check or 'N/A'} 휴장)"
            logger.info(f"   주중 교체: {label}(정규 재선정일) — 스킵")
            return

        # 활성 테마 확인
        if not self.today_themes:
            logger.info("   주중 교체: 활성 테마 없음 — 스킵")
            return

        # 최소 보유 기간 체크
        if not self._last_theme_rotation_date:
            logger.info("   주중 교체: 선정 날짜 불명 — 스킵")
            return
        days_held = count_trading_days(self._last_theme_rotation_date, today)
        if days_held < settings.MIDWEEK_MIN_HOLD_DAYS:
            logger.info(
                f"   주중 교체: 보유 {days_held}일 < 최소 {settings.MIDWEEK_MIN_HOLD_DAYS}일 — 스킵"
            )
            return

        # 전일 일별 점수 조회 (timedelta는 모듈 상단에서 import 됨)
        yesterday = today - timedelta(days=1)
        # 주말 보정: 금요일→월요일인 경우 금요일 데이터 사용
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)

        daily_scores = self.db.get_daily_theme_scores(yesterday)
        if not daily_scores:
            logger.info(f"   주중 교체: 전일({yesterday}) 일별 점수 없음 — 스킵")
            return

        # 전일 점수 맵 (테마명 → 점수)
        daily_score_map = {s["theme_name"]: s["score"] for s in daily_scores}

        # 활성 테마별 전일 점수 매핑 + 탈락 후보 검출
        active_names = set()
        active_categories = []
        candidates_for_drop = []

        for t in self.today_themes:
            t_name = t.get("theme", t.get("name", ""))
            active_names.add(t_name)
            active_categories.append(t.get("category", "기타"))

            yesterday_score = daily_score_map.get(t_name)
            if yesterday_score is None:
                logger.debug(f"   [{t_name}] 전일 점수 없음 — 교체 대상에서 제외")
                continue

            # 절대 하한 체크
            if yesterday_score <= settings.MIDWEEK_ABS_FLOOR:
                candidates_for_drop.append({
                    "name": t_name,
                    "yesterday_score": yesterday_score,
                    "theme_data": t,
                })

        if not candidates_for_drop:
            logger.info("   주중 교체: 절대 하한 미달 테마 없음 — 교체 없음")
            self.notifier.send_message(
                f"🔄 08:00 주중 교체 점검\n\n"
                f"✅ 교체 없음 — 전 테마 양호\n"
                f"📊 활성 {len(self.today_themes)}개 테마 모두 B등급({settings.MIDWEEK_ABS_FLOOR}점) 이상"
            )
            return

        # 비활성 최상위 후보 점수 (교체 후보 선정)
        from modules.theme_analyzer.selector import select_replacement_candidate
        replacement = select_replacement_candidate(
            daily_scores=daily_scores,
            active_theme_names=active_names,
            active_categories=active_categories,
            min_score=settings.MIDWEEK_CANDIDATE_MIN_SCORE,
        )

        if not replacement:
            logger.info("   주중 교체: 적합한 교체 후보 없음 — 교체 없음")
            worst = min(candidates_for_drop, key=lambda x: x["yesterday_score"])
            self.notifier.send_message(
                f"🔄 08:00 주중 교체 점검\n\n"
                f"⚠️ 부진 테마 있으나 교체 불가\n"
                f"📉 {worst['name']} ({worst['yesterday_score']:.1f}점)\n"
                f"❌ 적합한 교체 후보 없음"
            )
            return

        best_candidate_score = replacement["score"]

        # 상대 열세 조건: 각 탈락 후보에 대해 점수 갭 체크
        drop_target = None
        for c in sorted(candidates_for_drop, key=lambda x: x["yesterday_score"]):
            gap = best_candidate_score - c["yesterday_score"]
            if gap >= settings.MIDWEEK_RELATIVE_GAP:
                drop_target = c
                break  # 가장 낮은 점수부터 확인, 첫 매치 탈락

        if not drop_target:
            worst = min(candidates_for_drop, key=lambda x: x["yesterday_score"])
            gap = best_candidate_score - worst["yesterday_score"]
            logger.info(
                f"   주중 교체: 상대 열세 조건 미충족 "
                f"(최저 {worst['yesterday_score']:.1f}점 vs "
                f"후보 {best_candidate_score:.1f}점, "
                f"갭 {gap:.1f} "
                f"< {settings.MIDWEEK_RELATIVE_GAP})"
            )
            self.notifier.send_message(
                f"🔄 08:00 주중 교체 점검\n\n"
                f"⚠️ 부진 테마 있으나 교체 불가\n"
                f"📉 {worst['name']} ({worst['yesterday_score']:.1f}점)\n"
                f"📈 후보: {replacement['theme_name']} ({best_candidate_score:.1f}점)\n"
                f"❌ 점수 갭 {gap:.1f} < 기준 {settings.MIDWEEK_RELATIVE_GAP} — 유지"
            )
            return

        # ===== 교체 확정 =====
        dropped_name = drop_target["name"]
        self._midweek_dropped_theme = dropped_name
        self._midweek_new_theme = replacement

        logger.info(f"   🔄 주중 교체 확정!")
        logger.info(f"      ❌ 탈락: {dropped_name} (전일 {drop_target['yesterday_score']:.1f}점)")
        logger.info(f"      ✅ 교체: {replacement['theme_name']} (전일 {best_candidate_score:.1f}점)")

        # today_themes 갱신: 탈락 테마 제거 + 교체 테마 추가
        self.today_themes = [
            t for t in self.today_themes
            if t.get("theme", t.get("name", "")) != dropped_name
        ]
        # replacement는 db.get_daily_theme_scores 결과 (themes 테이블 SELECT *) →
        # momentum/supply_ratio/news_count/ai_sentiment 컬럼 보유 (NULL 가능)
        # _coalesce_zero로 NULL → 0 폴백, 다음 영업일 "기존 테마 유지" 분기 0 박제 방지
        self.today_themes.append({
            "name": replacement["theme_name"],
            "theme": replacement["theme_name"],
            "score": replacement["score"],
            "total_score": replacement["score"],
            "category": replacement.get("category", "기타"),
            "url": replacement.get("url", ""),
            "momentum": _coalesce_zero(replacement.get("momentum")),
            "supply_ratio": _coalesce_zero(replacement.get("supply_ratio")),
            "news_count": _coalesce_zero(replacement.get("news_count")),
            "ai_sentiment": _coalesce_zero(replacement.get("ai_sentiment")),
        })

        # DB themes 테이블에 전체 활성 테마 selected=1 마킹 (교체 결과 반영)
        # today_themes는 정규 재선정 결과(momentum_score 키) 또는 DB 복원본(momentum 키) 혼재 →
        # _pick_momentum 폴백 체인 + _coalesce_zero NULL 방어
        try:
            all_active = [
                {
                    "theme": t.get("theme", t.get("name", "")),
                    "score": t.get("score", 0),
                    "momentum": _pick_momentum(t),
                    "supply_ratio": _coalesce_zero(t.get("supply_ratio")),
                    "news_count": _coalesce_zero(t.get("news_count")),
                    "ai_sentiment": _coalesce_zero(t.get("ai_sentiment")),
                    "category": t.get("category", "기타"),
                    "url": t.get("url", ""),
                }
                for t in self.today_themes
            ]
            self.db.save_theme_scores(all_active, target_date=today, selected=True)
            logger.info(f"      ✅ DB themes 테이블에 전체 활성 테마 {len(all_active)}개 selected=1 마킹 완료")

            # 탈락 테마 이전 날짜 selected=0 마킹 (대시보드 표시용)
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE themes SET selected = 0 WHERE theme_name = ? AND date != ? AND selected = 1",
                    (dropped_name, today)
                )
            logger.info(f"      ✅ DB themes 테이블에서 {dropped_name} 이전 날짜 selected=0 마킹 완료")
        except Exception as e:
            logger.warning(f"      ⚠️ DB selected 마킹 실패 (기능 영향 없음): {e}")

        # 탈락 테마 보유 종목 분류 (수익/손실)
        holdings = self.db.get_portfolio(status='holding')
        for h in holdings:
            h_theme = h.get("theme", "")
            if h_theme != dropped_name:
                continue

            profit_rate = h.get("profit_rate", 0) or 0
            stock_info = {
                "stock_code": h["stock_code"],
                "stock_name": h["stock_name"],
                "shares": h.get("shares", 0),
                "buy_price": h.get("buy_price", 0),
                "profit_rate": profit_rate,
                "theme": dropped_name,
                "buy_date": h.get("buy_date") or h.get("date"),  # trade_review hold_days 계산용
            }

            if profit_rate > 0:
                self._midweek_sell_queue.append(stock_info)
                logger.info(f"      📈 {h['stock_name']} ({profit_rate:+.1%}) → 09:00 매도")
            else:
                self._midweek_loss_queue.append(stock_info)
                logger.info(f"      📉 {h['stock_name']} ({profit_rate:+.1%}) → {settings.screening_time_str} 재평가")

        # 텔레그램 알림
        profit_lines = []
        for s in self._midweek_sell_queue:
            profit_lines.append(f"  📈 {s['stock_name']} ({s['profit_rate']:+.1%}) → 09:00 매도")
        loss_lines = []
        for s in self._midweek_loss_queue:
            loss_lines.append(f"  📉 {s['stock_name']} ({s['profit_rate']:+.1%}) → {settings.screening_time_str} 재평가")

        position_text = ""
        if profit_lines or loss_lines:
            position_text = "\n\n탈락 종목 처리:\n" + "\n".join(profit_lines + loss_lines)

        self.notifier.send_message(
            f"🔄 주중 테마 교체 (08:00)\n\n"
            f"❌ 탈락: {dropped_name} (전일 {drop_target['yesterday_score']:.1f}점)\n"
            f"✅ 교체: {replacement['theme_name']} (전일 {best_candidate_score:.1f}점)"
            f"{position_text}\n\n"
            f"빈 슬롯은 {settings.buy_time_str}에 {replacement['theme_name']} 종목으로 채움"
        )
    
    # ===== trade_review 적재 헬퍼 (monitor 우회 매도용) =====

    @staticmethod
    def _compute_hold_days(buy_date_str: str, today_date) -> int:
        """buy_date 문자열 → 영업일 보유기간 계산 (실패 시 0 반환)."""
        if not buy_date_str:
            return 0
        try:
            buy_d = datetime.strptime(str(buy_date_str), "%Y-%m-%d").date()
            return count_trading_days(buy_d, today_date)
        except Exception:
            return 0

    def _save_trade_review_for_main_sell(
        self,
        *,
        trade_id: Optional[int],
        stock_code: str,
        stock_name: str,
        buy_price: float,
        sell_price: float,
        shares: int,
        profit_rate_pct: float,
        profit_amount: float,
        sell_reason: str,
        buy_date: str,
        theme: str,
        hold_days: int,
        position_states: dict,
    ) -> None:
        """monitor를 우회하는 매도 경로(보유기간/midweek)용 trade_review 적재.

        portfolio_monitor_v2._close_position_in_db 와 동일한 컬럼 셋으로 INSERT.
        실패해도 매도 자체는 영향받지 않도록 try/except로 격리.
        """
        try:
            state = position_states.get(stock_code, {}) or {}
            max_profit = state.get("max_profit_rate") or 0
            trailing_lv = state.get("trailing_level") or 0
            self.db.save_trade_review({
                "trade_id": trade_id,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "buy_date": buy_date,
                "sell_date": str(now_kst().date()),
                "buy_price": buy_price,
                "sell_price": sell_price,
                "shares": shares,
                "hold_days": hold_days,
                "profit_rate": profit_rate_pct,
                "profit_amount": profit_amount,
                "sell_reason": sell_reason,
                "strategy_type": PortfolioMonitorV2._classify_strategy(sell_reason),
                "trailing_level": trailing_lv,
                "max_profit_during_hold": round(max_profit * 100, 2),
                "theme": theme,
            })
        except Exception as e:
            logger.error(f"trade_review 저장 실패 ({stock_name}): {e}")

    # ===== 보유기간 만료 매도 (09:15) =====

    async def run_hold_period_sells(self) -> None:
        """
        보유기간 만료 종목 매도 (09:15)

        09:25 매수 전에 실행하여 빈 슬롯을 확보한다.
        DB에서 포트폴리오를 읽어 영업일 기준 보유기간 초과 종목을 매도.
        """
        logger.info(f"⏰ 보유기간 만료 체크 ({settings.hold_period_time_str})")

        try:
            portfolio = self.db.get_portfolio("holding")
        except Exception as e:
            logger.error(f"포트폴리오 조회 실패: {e}")
            return

        if not portfolio:
            logger.info("   보유 종목 없음 — 스킵")
            return

        # KIS API로 실시간 가격 조회 (DB의 current_price는 전일 종가일 수 있음)
        live_prices: dict[str, float] = {}
        try:
            from modules.stock_screener.kis_api import KISApi
            kis = KISApi()
            for pos in portfolio:
                code = pos["stock_code"]
                price_info = kis.get_current_price(code)
                if price_info and price_info.get("price", 0) > 0:
                    live_prices[code] = price_info["price"]
                    logger.info(f"   실시간 가격 조회: {pos.get('stock_name')} "
                                f"DB={pos.get('current_price', 0):,.0f}원 → "
                                f"실시간={price_info['price']:,}원")
        except Exception as e:
            logger.warning(f"   실시간 가격 조회 실패 (DB 가격으로 폴백): {e}")

        # position_state에서 remaining_shares 조회 (분할 익절 반영)
        position_states = self.db.get_all_position_states()

        # 영업일 기준 보유일수 계산
        today = now_kst().date()
        sell_targets = []

        for pos in portfolio:
            buy_date_raw = pos.get("buy_date") or pos.get("date")
            if not buy_date_raw:
                continue

            if isinstance(buy_date_raw, str):
                buy_date = datetime.strptime(buy_date_raw, "%Y-%m-%d").date()
            elif isinstance(buy_date_raw, datetime):
                buy_date = buy_date_raw.date()
            else:
                buy_date = buy_date_raw

            # 영업일 카운팅 (매수 다음날부터)
            hold_biz_days = count_trading_days(buy_date, today)

            buy_price = pos.get("buy_price") or pos.get("price", 0)
            stock_code = pos["stock_code"]
            # 실시간 가격 우선, 없으면 DB 가격 폴백
            current_price = live_prices.get(stock_code, pos.get("current_price", buy_price))
            profit_rate = (current_price - buy_price) / buy_price if buy_price > 0 else 0

            if profit_rate >= settings.MIN_PROFIT_FOR_LONG_HOLD:
                max_days = settings.MAX_HOLD_DAYS_PROFIT
            else:
                max_days = settings.MAX_HOLD_DAYS_LOSS

            if hold_biz_days >= max_days:
                # position_state에서 remaining_shares 우선 조회 (분할 익절 반영)
                state = position_states.get(stock_code, {})
                remaining = state.get("remaining_shares") or pos.get("shares", 0)

                sell_targets.append({
                    "stock_code": stock_code,
                    "stock_name": pos.get("stock_name", stock_code),
                    "quantity": remaining,
                    "buy_price": buy_price,
                    "hold_days": hold_biz_days,
                    "max_days": max_days,
                    "profit_rate": profit_rate,
                    "reason": SellReason.MAX_HOLD_DAYS.value,
                    "buy_date": str(buy_date),  # trade_review용
                    "theme": pos.get("theme", ""),  # trade_review용
                })

        if not sell_targets:
            logger.info("   보유기간 만료 종목 없음")
            return

        logger.info(f"   보유기간 만료 {len(sell_targets)}종목 매도 실행")
        for t in sell_targets:
            logger.info(f"   - {t['stock_name']} ({t['stock_code']}) "
                        f"보유 {t['hold_days']}영업일/{t['max_days']}일 "
                        f"수익률 {t['profit_rate']:.1%}")

        if self.test_mode:
            for t in sell_targets:
                logger.info(f"   [테스트] {t['stock_name']} 매도 스킵")
            return

        # SellLock: 09:00 조기 모니터링과 race 봉쇄 — acquire 실패한 종목은 모니터가 처리 중이므로 스킵
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            acquired_targets = []
            for t in sell_targets:
                if sell_lock.acquire(t["stock_code"], owner="hold_period"):
                    acquired_targets.append(t)
                else:
                    logger.warning(
                        f"   [SellLock] {t['stock_name']} ({t['stock_code']}) 보유기간 매도 skip — "
                        f"{sell_lock.owner(t['stock_code'])} 이 매도 처리 중"
                    )
            sell_targets = acquired_targets
            if not sell_targets:
                logger.info("   SellLock으로 모든 종목 스킵 — 보유기간 매도 종료")
                return

        # 매도 실행
        try:
            result = await asyncio.to_thread(
                self.trading_engine.execute_sell_orders,
                sell_targets,
                save_to_db=False,
            )

            sold_count = 0
            for order in result.get("orders", []):
                stock_code = order.get("stock_code", "")
                stock_name = order.get("stock_name", "")
                if not order.get("success"):
                    logger.error(f"   ❌ {stock_name} 매도 실패: {order}")
                    continue

                sold_count += 1
                filled_price = order.get("filled_price", order.get("price", 0))
                quantity = order.get("quantity", 0)
                buy_price = order.get("buy_price", 0)
                actual_profit_rate = (filled_price - buy_price) / buy_price if buy_price > 0 else 0
                actual_profit_amount = (filled_price - buy_price) * quantity

                logger.info(f"   ✅ {stock_name} {quantity}주 매도 완료 (@{filled_price:,})")

                # DB 기록 (profit_rate는 퍼센트 형태로 저장 — portfolio_monitor_v2와 일치)
                reason = SellReason.MAX_HOLD_DAYS.value
                target = next((t for t in sell_targets if t["stock_code"] == stock_code), {})
                self.db.close_position(stock_code, reason)
                trade_id = self.db.save_trade({
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "action": "sell",
                    "shares": quantity,
                    "price": filled_price,
                    "amount": (filled_price or 0) * quantity,
                    "reason": reason,
                    "profit_rate": actual_profit_rate * 100,
                    "profit_amount": actual_profit_amount,
                    "buy_price": buy_price,
                    "filled_price": filled_price,
                    "slippage": order.get("slippage"),
                })

                # trade_review 적재 (portfolio_monitor_v2._close_position_in_db와 동일 패턴)
                self._save_trade_review_for_main_sell(
                    trade_id=trade_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    buy_price=buy_price,
                    sell_price=filled_price,
                    shares=quantity,
                    profit_rate_pct=actual_profit_rate * 100,
                    profit_amount=actual_profit_amount,
                    sell_reason=reason,
                    buy_date=target.get("buy_date", ""),
                    theme=target.get("theme", ""),
                    hold_days=target.get("hold_days", 0),
                    position_states=position_states,
                )

                # 모니터 포지션 제거 (09:15는 모니터 시작 전이므로 보통 None)
                if self.monitor:
                    self.monitor.remove_position(stock_code)

                # 텔레그램 알림
                pnl_emoji = "🔺" if actual_profit_rate >= 0 else "🔻"
                self.notifier.send_message(
                    f"⏰ 보유기간 초과 매도 ({settings.hold_period_time_str})\n\n"
                    f"📊 {stock_name} ({stock_code})\n"
                    f"💰 매수가: {int(buy_price):,}원 → 매도가: {int(filled_price):,}원\n"
                    f"📊 수량: {quantity}주\n"
                    f"{pnl_emoji} 수익: {actual_profit_rate:+.2%} ({int(actual_profit_amount):+,}원)\n"
                    f"📅 보유: {target.get('hold_days', '?')}영업일 / 최대 {target.get('max_days', '?')}일\n\n"
                    f"⚠️ 보유기간 만료 → {settings.buy_time_str} 매수에서 슬롯 활용"
                )

                self.today_trades.append({
                    "action": "sell", "stock_code": stock_code,
                    "stock_name": stock_name, "shares": quantity,
                    "price": int(filled_price), "reason": SellReason.MAX_HOLD_DAYS.value
                })

            logger.info(f"   보유기간 매도 완료: {sold_count}/{len(sell_targets)}건")

        except Exception as e:
            logger.error(f"   보유기간 매도 오류: {e}")
            self.notifier.send_error_alert(
                "보유기간 만료 매도",
                f"매도 실행 중 오류 발생 — 수동 확인 필요\n{e}"
            )

    # ===== 주중 교체 매도 =====

    async def _execute_midweek_profit_sells(self) -> None:
        """
        주중 교체 수익 종목 매도 (09:00)

        _midweek_sell_queue에 있는 수익 종목을 시장가 매도.
        교체가 없는 날에는 큐가 비어있으므로 즉시 종료.
        """
        if not self._midweek_sell_queue:
            return

        logger.info("=" * 70)
        logger.info("🔄 주중 교체 — 수익 종목 매도 (09:00)")
        logger.info("=" * 70)

        reason = "주중 테마 교체 (수익 청산)"

        if self.test_mode:
            for stock in self._midweek_sell_queue:
                logger.info(f"   [테스트] {stock['stock_name']} ({stock['stock_code']}) {stock['shares']}주 매도 스킵")
            self._midweek_sell_queue.clear()
            return

        # execute_sell_orders 형식으로 변환
        sell_orders = [
            {
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "quantity": s["shares"],
                "buy_price": s.get("buy_price", 0),
                "reason": reason,
            }
            for s in self._midweek_sell_queue
        ]

        # 매도 전 큐 스냅샷 (review 메타 보강용)
        queue_snapshot = {s["stock_code"]: s for s in self._midweek_sell_queue}
        position_states = self.db.get_all_position_states()
        today = now_kst().date()

        # SellLock: 09:00 동시 발화 race — acquire 실패한 종목은 모니터가 처리 중이므로 스킵
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            acquired_orders = []
            for o in sell_orders:
                if sell_lock.acquire(o["stock_code"], owner="midweek_profit"):
                    acquired_orders.append(o)
                else:
                    logger.warning(
                        f"   [SellLock] {o['stock_name']} ({o['stock_code']}) 주중교체 수익매도 skip — "
                        f"{sell_lock.owner(o['stock_code'])} 이 매도 처리 중"
                    )
            sell_orders = acquired_orders
            if not sell_orders:
                logger.info("   SellLock으로 모든 종목 스킵 — 주중교체 수익매도 종료")
                self._midweek_sell_queue.clear()
                return

        try:
            result = await asyncio.to_thread(
                self.trading_engine.execute_sell_orders,
                sell_orders,
                save_to_db=False,  # 아래에서 직접 DB 처리
            )

            sold_count = 0
            for order in result.get("orders", []):
                stock_code = order.get("stock_code", "")
                stock_name = order.get("stock_name", "")
                if not order.get("success"):
                    logger.error(f"   ❌ {stock_name} 매도 실패: {order}")
                    continue

                sold_count += 1
                filled_price = order.get("filled_price", order.get("price", 0))
                quantity = order.get("quantity", 0)
                buy_price = order.get("buy_price", 0)
                profit_rate_pct = (order.get("profit_rate", 0) or 0) * 100
                profit_amount = order.get("profit_amount", 0) or 0
                logger.info(f"   ✅ {stock_name} {quantity}주 매도 완료 (@{filled_price:,})")

                # DB 기록
                self.db.close_position(stock_code, reason)
                trade_id = self.db.save_trade({
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "action": "sell",
                    "shares": quantity,
                    "price": filled_price,
                    "amount": (filled_price or 0) * quantity,
                    "reason": reason,
                    "profit_rate": profit_rate_pct,
                    "profit_amount": profit_amount,
                    "buy_price": buy_price,
                    "filled_price": filled_price,
                    "slippage": order.get("slippage"),
                })

                # trade_review 적재
                snap = queue_snapshot.get(stock_code, {})
                buy_date_str = snap.get("buy_date", "") or ""
                hold_days = self._compute_hold_days(buy_date_str, today)
                self._save_trade_review_for_main_sell(
                    trade_id=trade_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    buy_price=buy_price,
                    sell_price=filled_price,
                    shares=quantity,
                    profit_rate_pct=profit_rate_pct,
                    profit_amount=profit_amount,
                    sell_reason=reason,
                    buy_date=buy_date_str,
                    theme=snap.get("theme", ""),
                    hold_days=hold_days,
                    position_states=position_states,
                )

                # 모니터 포지션 제거
                if self.monitor:
                    self.monitor.remove_position(stock_code)

            logger.info(f"   주중 교체 수익 매도 완료: {sold_count}/{len(sell_orders)}건")

        except Exception as e:
            logger.error(f"   주중 교체 수익 매도 오류: {e}")
            self.notifier.send_error_alert(
                "주중 교체 수익 매도",
                f"매도 실행 중 오류 발생 — 수동 확인 필요\n{e}"
            )

        self._midweek_sell_queue.clear()

    async def _execute_midweek_loss_sells(self) -> None:
        """
        주중 교체 손실 종목 매도 (09:10)

        _midweek_loss_queue에서 09:05 재평가 탈락한 종목만 매도.
        재평가 통과 종목은 이미 큐에서 제거됨 (run_stock_screening에서 처리).
        """
        if not self._midweek_loss_queue:
            return

        logger.info("=" * 70)
        logger.info(f"🔄 주중 교체 — 재평가 탈락 종목 매도 ({settings.midweek_loss_time_str})")
        logger.info("=" * 70)

        reason = "주중 테마 교체 (재평가 탈락)"

        if self.test_mode:
            for stock in self._midweek_loss_queue:
                logger.info(f"   [테스트] {stock['stock_name']} ({stock['stock_code']}) {stock['shares']}주 매도 스킵")
            self._midweek_loss_queue.clear()
            return

        sell_orders = [
            {
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "quantity": s["shares"],
                "buy_price": s.get("buy_price", 0),
                "reason": reason,
            }
            for s in self._midweek_loss_queue
        ]

        # 매도 전 큐 스냅샷 (review 메타 보강용)
        queue_snapshot = {s["stock_code"]: s for s in self._midweek_loss_queue}
        position_states = self.db.get_all_position_states()
        today = now_kst().date()

        # SellLock: 09:10 매도 잡 — acquire 실패한 종목은 모니터가 처리 중이므로 스킵
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            acquired_orders = []
            for o in sell_orders:
                if sell_lock.acquire(o["stock_code"], owner="midweek_loss"):
                    acquired_orders.append(o)
                else:
                    logger.warning(
                        f"   [SellLock] {o['stock_name']} ({o['stock_code']}) 주중교체 손실매도 skip — "
                        f"{sell_lock.owner(o['stock_code'])} 이 매도 처리 중"
                    )
            sell_orders = acquired_orders
            if not sell_orders:
                logger.info("   SellLock으로 모든 종목 스킵 — 주중교체 손실매도 종료")
                self._midweek_loss_queue.clear()
                return

        try:
            result = await asyncio.to_thread(
                self.trading_engine.execute_sell_orders,
                sell_orders,
                save_to_db=False,
            )

            sold_count = 0
            for order in result.get("orders", []):
                stock_code = order.get("stock_code", "")
                stock_name = order.get("stock_name", "")
                if not order.get("success"):
                    logger.error(f"   ❌ {stock_name} 매도 실패: {order}")
                    continue

                sold_count += 1
                filled_price = order.get("filled_price", order.get("price", 0))
                quantity = order.get("quantity", 0)
                buy_price = order.get("buy_price", 0)
                profit_rate_pct = (order.get("profit_rate", 0) or 0) * 100
                profit_amount = order.get("profit_amount", 0) or 0
                logger.info(f"   ✅ {stock_name} {quantity}주 매도 완료 (@{filled_price:,})")

                self.db.close_position(stock_code, reason)
                trade_id = self.db.save_trade({
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "action": "sell",
                    "shares": quantity,
                    "price": filled_price,
                    "amount": (filled_price or 0) * quantity,
                    "reason": reason,
                    "profit_rate": profit_rate_pct,
                    "profit_amount": profit_amount,
                    "buy_price": buy_price,
                    "filled_price": filled_price,
                    "slippage": order.get("slippage"),
                })

                # trade_review 적재
                snap = queue_snapshot.get(stock_code, {})
                buy_date_str = snap.get("buy_date", "") or ""
                hold_days = self._compute_hold_days(buy_date_str, today)
                self._save_trade_review_for_main_sell(
                    trade_id=trade_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    buy_price=buy_price,
                    sell_price=filled_price,
                    shares=quantity,
                    profit_rate_pct=profit_rate_pct,
                    profit_amount=profit_amount,
                    sell_reason=reason,
                    buy_date=buy_date_str,
                    theme=snap.get("theme", ""),
                    hold_days=hold_days,
                    position_states=position_states,
                )

                if self.monitor:
                    self.monitor.remove_position(stock_code)

            logger.info(f"   주중 교체 손실 매도 완료: {sold_count}/{len(sell_orders)}건")

        except Exception as e:
            logger.error(f"   주중 교체 손실 매도 오류: {e}")
            self.notifier.send_error_alert(
                "주중 교체 손실 매도",
                f"매도 실행 중 오류 발생 — 수동 확인 필요\n{e}"
            )

        self._midweek_loss_queue.clear()

    # ===== 장 마감 =====

    async def run_market_close(self) -> None:
        """장 마감 정리 (15:35)"""
        logger.info("장 마감 정리")

        # 미체결 주문 취소
        try:
            self.trading_engine.cancel_all_pending()
        except Exception as e:
            logger.error(f"미체결 취소 실패: {e}")

        # 포트폴리오 현황 출력
        try:
            if self.monitor:
                self.monitor.display_status()
                self.monitor._update_db_prices()
        except Exception as e:
            logger.error(f"포지션 상태 갱신 실패: {e}")

        # 일일 스냅샷 저장
        self._save_daily_snapshot()

    def _save_daily_snapshot(self) -> None:
        """장 마감 시 daily_snapshots 저장"""
        db = None
        try:
            db = Database()
            db.connect()

            today = now_kst().date()

            # 보유 종목
            holdings = db.get_portfolio(status="holding")
            sell_trades = db.get_all_sell_trades()

            # 실현 손익 계산
            today_sell = [t for t in sell_trades if str(t.get("date")) == str(today)]
            realized_pnl_today = sum(t.get("profit_amount") or 0 for t in today_sell)
            realized_pnl_cumulative = sum(t.get("profit_amount") or 0 for t in sell_trades)

            # 미실현 손익 계산
            total_invested = sum((h.get("shares", 0)) * (h.get("buy_price", 0)) for h in holdings)
            total_eval = sum((h.get("shares", 0)) * (h.get("current_price") or h.get("buy_price", 0)) for h in holdings)
            unrealized_pnl = total_eval - total_invested

            # 매매 건수
            today_trades = db.get_trades(today)
            buy_count = sum(1 for t in today_trades if t.get("action") == "buy")
            sell_count = sum(1 for t in today_trades if t.get("action") == "sell")

            # 승패 (전체 누적)
            win_count = sum(1 for t in sell_trades if (t.get("profit_amount") or 0) > 0)
            loss_count = sum(1 for t in sell_trades if (t.get("profit_amount") or 0) <= 0)
            total_trades = win_count + loss_count
            win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0

            # 수익률
            capital = settings.TOTAL_CAPITAL
            cash_balance = max(0, capital - total_invested)
            current_total = cash_balance + total_eval + realized_pnl_cumulative
            cumulative_return = ((current_total - capital) / capital * 100) if capital > 0 else 0

            # MDD: 전일 peak 가져와서 비교
            previous = db.get_daily_snapshots(days=1)
            prev_peak = previous[0].get("peak_value", current_total) if previous else current_total
            peak_value = max(prev_peak, current_total)
            mdd = ((current_total - peak_value) / peak_value * 100) if peak_value > 0 else 0

            # 일별 수익률
            prev_total = previous[0].get("total_capital", capital) if previous else capital
            daily_return = ((current_total - prev_total) / prev_total * 100) if prev_total > 0 else 0

            # 포지션 JSON
            positions_json = json.dumps([
                {"code": h["stock_code"], "name": h["stock_name"],
                 "shares": h.get("shares", 0), "buy_price": h.get("buy_price", 0),
                 "current_price": h.get("current_price", 0)}
                for h in holdings
            ], ensure_ascii=False)

            db.save_daily_snapshot({
                "date": str(today),
                "total_capital": current_total,
                "cash_balance": cash_balance,
                "total_invested": total_invested,
                "total_eval": total_eval,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl_today": realized_pnl_today,
                "realized_pnl_cumulative": realized_pnl_cumulative,
                "daily_return": round(daily_return, 4),
                "cumulative_return": round(cumulative_return, 4),
                "mdd": round(mdd, 4),
                "peak_value": peak_value,
                "num_positions": len(holdings),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "win_count_cumulative": win_count,
                "loss_count_cumulative": loss_count,
                "win_rate": round(win_rate, 2),
                "positions_json": positions_json,
            })

            logger.info(f"일일 스냅샷 저장: 수익률 {cumulative_return:+.2f}%, MDD {mdd:.2f}%")

        except Exception as e:
            logger.error(f"일일 스냅샷 저장 실패: {e}")
        finally:
            if db:
                db.close()
    
    # ===== 일일 리포트 =====
    
    async def send_daily_report(self) -> None:
        """일일 리포트 발송 (16:00)"""
        logger.info("일일 리포트 생성")

        try:
            # 현재 포트폴리오
            balance = self.trading_engine.get_balance()
            positions = balance.get("positions", [])

            # 전체 매도 기록 및 스냅샷 조회
            db = Database()
            db.connect()
            try:
                realized_trades = db.get_all_sell_trades()
                snapshots = db.get_daily_snapshots(days=90)
                self._aggregate_strategy_stats(db)
            finally:
                db.close()

            # 성과 지표 계산
            calc = PerformanceCalculator()
            portfolio_values = [
                {"date": s["date"], "value": s.get("total_capital", settings.TOTAL_CAPITAL)}
                for s in reversed(snapshots)
            ] if snapshots else []
            metrics = calc.calculate_all_metrics(
                trades=realized_trades,
                portfolio_values=portfolio_values,
                initial_capital=settings.TOTAL_CAPITAL
            )

            # 리포트 전송
            self.notifier.send_daily_report(
                portfolio=positions,
                metrics=metrics,
                themes=self.current_themes,
                ai_analysis=self.today_ai_analysis,
                today_trades=self.today_trades,
                realized_trades=realized_trades,
                total_capital=settings.TOTAL_CAPITAL
            )

            logger.info("일일 리포트 발송 완료")

        except Exception as e:
            logger.error(f"리포트 발송 실패: {e}")

    # ===== 17:05 일별 테마 데이터 수집 =====

    async def _enrich_tuesday_themes(self, scored_themes: list[dict]) -> list[dict]:
        """
        화요일 08:30 실시간 보강: DB 가중평균에 실시간 모멘텀·뉴스·AI를 반영

        상위 settings.THEME_ENRICH_TOP_K(기본 30)개 테마에 대해:
        1. URL 확보 (DB → 크롤링 보충)
        2. 종목 매핑 → KIS API 5일 수익률
        3. 뉴스 수집 → AI 감성분석
        4. DB 점수와 차이가 크면 조건부 보정
           - 모멘텀 delta × THEME_MOMENTUM_BOOST_FACTOR → ±THEME_MOMENTUM_BOOST_CLAMP 클램프

        실패 시 원본 scored_themes 그대로 반환 (폴백 안전).
        """
        try:
            from modules.theme_analyzer.crawlers import (
                crawl_naver_theme_stocks, crawl_theme_news, search_naver_theme, _random_delay
            )
            from modules.theme_analyzer.scorer import _get_kis_api, _calculate_theme_momentum
            from modules.theme_analyzer.ai_analyzer import analyze_themes_sync
            from modules.theme_analyzer.scorer import calculate_ai_sentiment_score

            top_k = scored_themes[:settings.THEME_ENRICH_TOP_K]
            logger.info(
                f"\n🔄 Step 1-B: 화요일 실시간 보강 ({len(top_k)}개 테마, "
                f"factor={settings.THEME_MOMENTUM_BOOST_FACTOR}, "
                f"clamp=±{settings.THEME_MOMENTUM_BOOST_CLAMP})"
            )
            enrich_start = now_kst()

            # 1. URL 없는 테마 보충
            for t in top_k:
                if not t.get("url"):
                    result = await asyncio.to_thread(
                        search_naver_theme, t.get("theme", t.get("name", ""))
                    )
                    if result and result.get("url"):
                        t["url"] = result["url"]

            # 2. 종목 매핑 + KIS API 모멘텀
            kis = _get_kis_api()
            momentum_updates = 0
            adjustments_log: list[float] = []
            clamped_count = 0
            for t in top_k:
                url = t.get("url", "")
                if not url:
                    continue
                try:
                    stocks = await asyncio.to_thread(crawl_naver_theme_stocks, url)
                    t["stocks"] = [s["code"] for s in stocks[:3]]
                    if t["stocks"]:
                        realtime_momentum = await asyncio.to_thread(
                            _calculate_theme_momentum, t, kis
                        )
                        # 실측값은 보정 발화 여부와 무관하게 표시 채널에 저장
                        # (메시지 빌더가 avg_change_rate 읽음 — 미설정 시 +0.0% 회귀)
                        t["avg_change_rate"] = round(realtime_momentum, 2)
                        t["momentum_realtime"] = round(realtime_momentum, 2)
                        db_momentum = t.get("momentum", 0) or 0
                        momentum_delta = realtime_momentum - db_momentum
                        if abs(momentum_delta) > 3.0:
                            raw_adjustment = momentum_delta * settings.THEME_MOMENTUM_BOOST_FACTOR
                            clamp = settings.THEME_MOMENTUM_BOOST_CLAMP
                            adjustment = max(-clamp, min(clamp, raw_adjustment))
                            if adjustment != raw_adjustment:
                                clamped_count += 1
                            t["total_score"] = round(t.get("total_score", 0) + adjustment, 2)
                            t["score"] = t["total_score"]
                            momentum_updates += 1
                            adjustments_log.append(adjustment)
                            logger.info(
                                f"   [{t.get('theme', '')}] 모멘텀 보정: "
                                f"DB {db_momentum:+.1f} → 실시간 {realtime_momentum:+.1f} "
                                f"(delta {momentum_delta:+.1f}, raw {raw_adjustment:+.1f}, "
                                f"적용 {adjustment:+.1f})"
                            )
                except Exception as e:
                    logger.debug(f"   [{t.get('theme', '')}] 종목 매핑/모멘텀 실패: {e}")

            # 3. 뉴스 수집 + AI 감성분석
            ai_updates = 0
            for t in top_k:
                t_name = t.get("theme", t.get("name", ""))
                try:
                    news_data = await asyncio.to_thread(crawl_theme_news, t_name)
                    if news_data and news_data.get("text"):
                        t["news"] = news_data["text"]
                        t["news_count"] = news_data.get("count", t.get("news_count", 0))
                except Exception as e:
                    logger.debug(f"   [{t_name}] 뉴스 수집 실패: {e}")

            # AI 일괄 분석 (news 텍스트가 있는 테마만)
            themes_for_ai = [t for t in top_k if t.get("news") and len(t["news"]) >= 50]
            if themes_for_ai:
                try:
                    ai_results = await asyncio.to_thread(analyze_themes_sync, themes_for_ai)
                    if ai_results:
                        ai_map = {r["theme_name"]: r for r in ai_results if r and "theme_name" in r}
                        for t in top_k:
                            t_name = t.get("theme", t.get("name", ""))
                            if t_name in ai_map:
                                realtime_ai = ai_map[t_name].get("score", 0)
                                db_ai = t.get("ai_sentiment", 0) or 0
                                ai_delta = realtime_ai - db_ai
                                if abs(ai_delta) > 2.0:
                                    ai_score_new = calculate_ai_sentiment_score(realtime_ai)
                                    ai_score_old = calculate_ai_sentiment_score(db_ai) if db_ai else 0
                                    adjustment = ai_score_new - ai_score_old
                                    t["total_score"] = round(t.get("total_score", 0) + adjustment, 2)
                                    t["score"] = t["total_score"]
                                    t["ai_sentiment"] = realtime_ai
                                    t["ai_score"] = ai_score_new
                                    ai_updates += 1
                                    logger.info(
                                        f"   [{t_name}] AI 보정: "
                                        f"DB {db_ai:.1f} → 실시간 {realtime_ai:.1f} "
                                        f"(점수 {adjustment:+.1f})"
                                    )
                except Exception as e:
                    logger.warning(f"   AI 분석 실패 (폴백): {e}")

            # 재정렬
            scored_themes.sort(key=lambda x: x.get("total_score", 0), reverse=True)
            elapsed = (now_kst() - enrich_start).total_seconds()
            if adjustments_log:
                avg_adj = sum(adjustments_log) / len(adjustments_log)
                min_adj = min(adjustments_log)
                max_adj = max(adjustments_log)
                logger.info(
                    f"   [Enrich] 모멘텀 조정: avg={avg_adj:+.2f}, "
                    f"min={min_adj:+.2f}, max={max_adj:+.2f}, clamped={clamped_count}"
                )
            logger.info(
                f"   실시간 보강 완료: 모멘텀 {momentum_updates}건, "
                f"AI {ai_updates}건 보정 ({elapsed:.1f}초)"
            )

        except Exception as e:
            logger.warning(f"   화요일 실시간 보강 실패 (DB 가중평균 유지): {e}")

        return scored_themes

    async def run_daily_theme_collection(self) -> None:
        """일별 테마 데이터 수집 (17:05, 장 마감 후)

        매일 테마 크롤링 + 뉴스 + AI 감성 점수를 수집하여 DB에 저장.
        화요일 가중 집계 시 이 데이터를 사용한다.
        """
        logger.info("=" * 60)
        logger.info("📊 일별 테마 데이터 수집 시작 (17:05)")
        logger.info("=" * 60)

        try:
            from modules.theme_analyzer import crawl_all_themes, score_themes

            # 1. 전체 테마 크롤링 (정규화 적용)
            raw_themes = await asyncio.to_thread(crawl_all_themes)
            logger.info(f"   크롤링된 테마: {len(raw_themes)}개")

            # 2. 유동성 통과율 조회 + 점수화 (모멘텀 + 뉴스 + 유동성 감점)
            pass_rates = {}
            if settings.THEME_LIQUIDITY_CHECK_ENABLED:
                try:
                    pass_rates = self.db.get_theme_pass_rates(settings.THEME_PASS_RATE_LOOKBACK_DAYS)
                except Exception as e:
                    logger.warning(f"   유동성 통과율 조회 실패 (무시): {e}")

            scored_themes = await asyncio.to_thread(
                score_themes, raw_themes[:30], True, False, pass_rates  # include_news=True, include_ai=False + 유동성
            )
            logger.info(f"   점수화 완료: {len(scored_themes)}개 테마")

            # 3. AI 감성 분석 (상위 20개)
            try:
                from modules.theme_analyzer.ai_analyzer import analyze_themes_sync
                from modules.theme_analyzer.scorer import calculate_ai_sentiment_score
                top_20 = scored_themes[:20]
                ai_results = await asyncio.to_thread(analyze_themes_sync, top_20)
                if ai_results:
                    # list[dict] → {theme_name: dict} 변환
                    ai_map = {r["theme_name"]: r for r in ai_results if r and "theme_name" in r}
                    for theme in scored_themes:
                        name = theme.get("theme", theme.get("name", ""))
                        if name in ai_map:
                            ai_val = ai_map[name].get("score", 0)
                            theme["ai_sentiment"] = ai_val
                            theme["ai_score"] = calculate_ai_sentiment_score(ai_val)
                            # 총점 재계산 (과열 감점 + BASE_SCORE 반영)
                            from modules.theme_analyzer.scorer import BASE_SCORE
                            theme["total_score"] = round(
                                theme.get("momentum", 0)
                                + theme.get("overheat_penalty", 0)
                                + theme.get("news_score", 0)
                                + theme["ai_score"]
                                + theme.get("bonus_score", 0)
                                + BASE_SCORE
                                + theme.get("liquidity_penalty", 0), 2
                            )
                            theme["score"] = theme["total_score"]
                    # 재정렬
                    scored_themes.sort(key=lambda x: x.get("total_score", 0), reverse=True)
                    logger.info(f"   AI 감성 분석 완료: {len(ai_map)}개 테마")
            except Exception as ai_err:
                logger.warning(f"   AI 감성 분석 스킵: {ai_err}")

            # 4. 수급비율 계산 (상위 15개, URL 있는 테마)
            try:
                from modules.theme_analyzer.scorer import calculate_theme_supply_ratio
                from modules.stock_screener.kis_api import KISApi
                kis = KISApi()
                supply_count = 0
                for t in scored_themes[:15]:
                    url = t.get("url", "")
                    if not url:
                        continue
                    ratio = await asyncio.to_thread(
                        calculate_theme_supply_ratio, url, kis
                    )
                    t["supply_ratio"] = ratio
                    if ratio > 0:
                        supply_count += 1
                logger.info(f"   수급비율 계산: {supply_count}/{min(len(scored_themes), 15)}개 테마")
            except Exception as e:
                logger.warning(f"   수급비율 계산 스킵: {e}")

            # 5. DB 저장 (url 포함)
            themes_to_save = [
                {
                    "theme": t.get("theme", t.get("name", "")),
                    "score": t.get("score", 0),
                    "momentum": t.get("momentum_score", 0),
                    "supply_ratio": t.get("supply_ratio", 0),
                    "news_count": t.get("news_count", 0),
                    "ai_sentiment": t.get("ai_sentiment", 0),
                    "category": t.get("category", "기타"),
                    "url": t.get("url", ""),
                }
                for t in scored_themes
            ]
            self.db.save_theme_scores(themes_to_save, now_kst().date(), selected=False)
            logger.info(f"   DB 저장 완료: {len(themes_to_save)}개 테마 ({now_kst().date()}, 일별수집)")

        except Exception as e:
            logger.error(f"일별 테마 수집 실패: {e}")

    # ===== 17:10 외국인/기관 수급 수집 (v16, Phase 1-A) =====

    async def run_supply_collection(self) -> None:
        """외국인/기관 수급 데이터 수집 (17:10, 장 마감 후).

        종가베팅 시스템이 검증한 데이터 소스(FHKST01010900 + FHPTJ04400000)를 이용하여
        T-1 수급을 DB에 저장. 다음날 아침 KIS 재호출 없이 supply 신호 활용 가능.

        Phase 1-A: 데이터 파이프라인만. 점수/필터/AI 통합은 Phase 1-B~D에서 단계적 진행.

        토글: SUPPLY_SIGNAL_ENABLED=False 시 즉시 비활성화.

        18:00 재시도 잡(supply_collection_retry)이 같은 메서드를 호출할 때를 대비하여,
        오늘 이미 일정 행 수 이상 수집되었으면 스킵 (중복 호출 방어).
        """
        if not settings.SUPPLY_SIGNAL_ENABLED:
            logger.info("[supply_collection] SUPPLY_SIGNAL_ENABLED=False — 잡 스킵")
            return

        # 18:00 재시도 잡 중복 호출 방어: 오늘 이미 충분한 행이 저장되었으면 skip
        # 임계값: universe의 50% 이상 (보수적). count_supply_snapshots_for_date 사용.
        try:
            today = now_kst().date()
            existing = self.db.count_supply_snapshots_for_date(today)
            # 50건 이상이면 1차 수집이 의미있게 완료된 것으로 간주 (재시도 불필요)
            if existing >= 50:
                logger.info(
                    f"[supply_collection] 오늘 ({today}) 이미 {existing}건 수집됨 → 재시도 스킵"
                )
                return
        except Exception as e:
            logger.warning(f"[supply_collection] 사전 행 수 확인 실패 (계속 진행): {e}")

        logger.info("=" * 60)
        logger.info("📊 외국인/기관 수급 수집 시작")
        logger.info("=" * 60)

        try:
            from modules.supply_collector import SupplyCollector
            from modules.stock_screener.kis_api import KISApi

            # KIS 인스턴스 (토큰은 _shared_token으로 다른 인스턴스와 공유)
            kis = KISApi()

            # market_provider는 종가베팅 모듈에 있음. 로드 실패해도 종목별 수급은 계속 진행.
            market_provider = None
            try:
                from closing_bet_system.collectors.kis_market_provider import (
                    get_kis_market_provider,
                )
                market_provider = get_kis_market_provider()
            except Exception as mp_err:
                logger.warning(f"market_provider 로드 실패 (외인 TOP 스킵): {mp_err}")

            collector = SupplyCollector(self.db, kis, market_provider=market_provider)
            trade_date = now_kst().date()
            result = await asyncio.to_thread(collector.run_collection, trade_date)

            # 텔레그램 알림
            msg = (
                f"📊 수급 수집 완료 ({result.get('trade_date')})\n"
                f"- daily_supply_snapshot: {result.get('success', 0)}/{result.get('codes', 0)} "
                f"(실패 {result.get('failure', 0)})\n"
                f"- foreign_top_ranking: 외인 {result.get('foreign_ranking_count', 0)}건 / "
                f"기관 {result.get('institution_ranking_count', 0)}건\n"
                f"- 처리 시간: {result.get('duration_sec', 0)}초"
            )
            try:
                self.notifier.send_message(msg)
            except Exception as tg_err:
                logger.warning(f"수급 수집 알림 발송 실패: {tg_err}")

            logger.info(f"수급 수집 완료: {result}")

        except Exception as e:
            logger.error(f"수급 수집 실패: {e}", exc_info=True)
            try:
                self.notifier.send_error_alert("수급 수집 (17:10)", str(e))
            except Exception:
                pass

    # ===== 17:00 매매 사후 분석 =====

    async def run_post_trade_analysis(self) -> None:
        """매도 후 사후 분석 (17:00)"""
        logger.info("=" * 60)
        logger.info("🔍 매매 사후 분석 시작 (17:00)")
        logger.info("=" * 60)

        try:
            if self.post_trade_analyzer is None:
                self.post_trade_analyzer = PostTradeAnalyzer(db=self.db)

            results = await asyncio.to_thread(self.post_trade_analyzer.run_daily_analysis)

            if results:
                logger.info(f"매매 사후 분석 완료: {len(results)}건")
                self.notifier.send_post_trade_report(results)
            else:
                logger.info("매매 사후 분석: 대상 없음")

        except Exception as e:
            logger.error(f"매매 사후 분석 실패: {e}")

    # ===== 금 17:30 주간 매매 복기 =====

    async def run_weekly_trade_review(self) -> None:
        """주간 매매 복기 (금요일 17:30)"""
        logger.info("=" * 60)
        logger.info("📝 주간 매매 복기 시작 (금 17:30)")
        logger.info("=" * 60)

        try:
            if self.post_trade_analyzer is None:
                self.post_trade_analyzer = PostTradeAnalyzer(db=self.db)

            summary = await asyncio.to_thread(self.post_trade_analyzer.generate_weekly_summary)

            if summary:
                logger.info(f"주간 복기 완료: 평균 타이밍 {summary.get('avg_timing_score', 0)}/10")
                # 텔레그램으로 주간 복기 리포트 발송
                report_text = (
                    f"📝 *주간 매매 복기*\n"
                    f"📅 {now_kst().strftime('%Y-%m-%d')}\n\n"
                    f"📊 *주간 패턴*\n{summary.get('weekly_pattern', 'N/A')}\n\n"
                    f"💡 *파라미터 제안*\n"
                )
                for s in summary.get("parameter_suggestions", []):
                    report_text += f"  • {s}\n"
                report_text += f"\n⚠️ *다음 주 주의*\n{summary.get('next_week_caution', 'N/A')}"
                report_text += f"\n\n⭐ 평균 타이밍 점수: {summary.get('avg_timing_score', 0)}/10"
                self.notifier.send_message(report_text)
            else:
                logger.info("주간 복기: 분석 대상 없음")

        except Exception as e:
            logger.error(f"주간 매매 복기 실패: {e}")

    def _aggregate_strategy_stats(self, db: Database) -> None:
        """trade_reviews에서 전략별 성과 집계 -> strategy_stats 저장"""
        try:
            today = str(now_kst().date())
            with db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT strategy_type,
                           COUNT(*) as trade_count,
                           SUM(CASE WHEN profit_amount > 0 THEN 1 ELSE 0 END) as win_count,
                           SUM(CASE WHEN profit_amount <= 0 THEN 1 ELSE 0 END) as loss_count,
                           SUM(profit_amount) as total_pnl,
                           AVG(profit_rate) as avg_profit_rate,
                           AVG(hold_days) as avg_hold_days
                    FROM trade_reviews
                    WHERE sell_date = ?
                    GROUP BY strategy_type
                """, (today,))
                rows = cursor.fetchall()

            for row in rows:
                r = dict(row)
                total = r["trade_count"]
                wins = r["win_count"] or 0
                db.save_strategy_stats({
                    "date": today,
                    "strategy_type": r["strategy_type"],
                    "trade_count": total,
                    "win_count": wins,
                    "loss_count": r["loss_count"] or 0,
                    "win_rate": round(wins / total * 100, 2) if total > 0 else 0,
                    "total_pnl": r["total_pnl"] or 0,
                    "avg_profit_rate": round(r["avg_profit_rate"] or 0, 2),
                    "avg_hold_days": round(r["avg_hold_days"] or 0, 1),
                })
            if rows:
                logger.info(f"전략별 성과 집계: {len(rows)}개 전략 유형")
        except Exception as e:
            logger.error(f"전략별 성과 집계 실패: {e}")
    
    # ===== 16:10 일일 시스템 헬스체크 =====
    # 헬퍼 메서드들은 각자 dict를 반환:
    #   {"info": str | None, "issue": str | None}
    # info는 정상 상태 표시(없으면 메시지 생략), issue는 이상 항목

    def _hc_theme_score_pinning(self, db: Database, today_str: str) -> dict:
        """🔴 테마 점수 박제 회귀 감지 (selected=1 momentum/ai/news 모두 0/NULL)

        2026-05-04~07 박제 회귀(theme-score-zero-fix)의 모니터링 항목.
        3건 이상 발견 시 즉시 알림.
        """
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT theme_name, momentum, ai_sentiment, news_count "
                "FROM themes WHERE date=? AND selected=1",
                (today_str,)
            )
            rows = cursor.fetchall()
            if not rows:
                return {"info": None, "issue": None}

            # 박제 분류:
            # - zero_pinned: 셋 다 실제 0 (회귀 의심) → issue
            # - null_only: 셋 다 NULL (폴백 대기, 정상 흐름) → info
            # - mixed_zero: 일부만 0 (정상 momentum_score=0 케이스 등) → 정상 처리
            zero_pinned = [
                r for r in rows
                if r[1] == 0 and r[2] == 0 and r[3] == 0
            ]
            null_only = [
                r for r in rows
                if r[1] is None and r[2] is None and r[3] is None
            ]
            normal_count = len(rows) - len(zero_pinned) - len(null_only)

            if len(zero_pinned) >= 3:
                names = ", ".join(r[0] for r in zero_pinned[:3])
                return {
                    "info": None,
                    "issue": f"테마 점수 박제 의심 {len(zero_pinned)}건 (셋 다 0): {names}",
                }
            info = f"테마 점수: 정상 {normal_count}건"
            if null_only:
                info += f" / NULL {len(null_only)}건 (폴백 대기, 다음 트리거 자동 복원)"
            return {"info": info, "issue": None}
        except Exception as e:
            return {"info": None, "issue": f"테마 박제 체크 실패: {str(e)[:40]}"}

    def _hc_screening_log_stages(self, db: Database, today_str: str) -> dict:
        """🟡 screening_log 3단계 stage 적재 검증 (filter / gap_filter / ai_verify)"""
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT stage, COUNT(*) FROM screening_log WHERE date=? GROUP BY stage",
                (today_str,)
            )
            stages = dict(cursor.fetchall())
            filter_n = stages.get("filter", 0)
            gap_n = stages.get("gap_filter", 0)
            ai_n = stages.get("ai_verify", 0)

            if filter_n == 0:
                if not is_trading_day(now_kst().date()):
                    return {"info": "스크리닝: 휴장일 (정상)", "issue": None}
                return {"info": None, "issue": "screening_log filter stage 0건"}

            return {
                "info": f"스크리닝 로그: filter={filter_n} / gap={gap_n} / ai={ai_n}",
                "issue": None,
            }
        except Exception as e:
            return {"info": None, "issue": f"screening_log stage 체크 실패: {str(e)[:40]}"}

    def _hc_phase_a_metrics(self, db: Database, today_str: str) -> dict:
        """🟡 Phase A: theme_slot_protected 발동 + RSI 동적 임계값 분포"""
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT COUNT(*), AVG(rsi_at_screen), MAX(rsi_at_screen) "
                "FROM screening_log WHERE date=? AND stage='filter' "
                "AND rsi_at_screen IS NOT NULL",
                (today_str,)
            )
            row = cursor.fetchone()
            total, avg_rsi, max_rsi = row[0], row[1], row[2]

            cursor.execute(
                "SELECT COUNT(*) FROM screening_log "
                "WHERE date=? AND theme_slot_protected=1 AND passed=1",
                (today_str,)
            )
            protected = cursor.fetchone()[0]

            if total == 0:
                return {"info": None, "issue": None}

            info = f"Phase A: 슬롯 보장 {protected}건"
            if avg_rsi is not None:
                info += f" / RSI 평균 {avg_rsi:.1f} 최대 {max_rsi:.1f}"
            return {"info": info, "issue": None}
        except Exception as e:
            return {"info": None, "issue": f"Phase A 체크 실패: {str(e)[:40]}"}

    def _hc_midweek_replacement(self) -> dict:
        """🟡 midweek 교체 발생 알림"""
        if self._midweek_dropped_theme:
            new_name = (
                self._midweek_new_theme.get("theme_name")
                if self._midweek_new_theme else "?"
            )
            return {
                "info": f"주중 교체: ❌{self._midweek_dropped_theme} → 🆕{new_name}",
                "issue": None,
            }
        return {"info": None, "issue": None}

    def _hc_makeup_reselection(self) -> dict:
        """🟡 보정 재선정 발화 감지 (오늘이 보정일이면 발화 여부 표시)"""
        try:
            today = now_kst().date()
            is_makeup, missed_tue = is_makeup_reselection_day(today)
            if not is_makeup or missed_tue is None:
                return {"info": None, "issue": None}
            rotation_date = self._last_theme_rotation_date
            if rotation_date and rotation_date >= missed_tue:
                return {
                    "info": f"보정 재선정: 발화함 (직전 화 {missed_tue} 휴장)",
                    "issue": None,
                }
            return {
                "info": None,
                "issue": (
                    f"보정 재선정 미발화 의심 (오늘 보정일이나 "
                    f"_last_theme_rotation_date={rotation_date} < {missed_tue})"
                ),
            }
        except Exception as e:
            return {"info": None, "issue": f"보정 재선정 체크 실패: {str(e)[:40]}"}

    def _hc_trade_review_coverage(self, db: Database, today_str: str) -> dict:
        """🟢 trade_reviews 누락 감지 (오늘 SELL vs review 카운트)"""
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM trades WHERE date=? AND action='sell'",
                (today_str,)
            )
            sell_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM trade_reviews WHERE DATE(created_at)=?",
                (today_str,)
            )
            review_count = cursor.fetchone()[0]

            if sell_count == 0:
                return {"info": None, "issue": None}
            if review_count < sell_count:
                return {
                    "info": None,
                    "issue": (
                        f"trade_reviews 누락 의심 "
                        f"(SELL {sell_count}건 vs review {review_count}건)"
                    ),
                }
            return {"info": f"매도 review: {review_count}/{sell_count}건 적재", "issue": None}
        except Exception as e:
            return {"info": None, "issue": f"trade_reviews 체크 실패: {str(e)[:40]}"}

    def _hc_slippage_coverage(self, db: Database, today_str: str) -> dict:
        """🟢 매도 slippage 측정 적재율"""
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT COUNT(*), SUM(CASE WHEN slippage IS NULL THEN 1 ELSE 0 END) "
                "FROM trades WHERE date=? AND action='sell'",
                (today_str,)
            )
            row = cursor.fetchone()
            total = row[0]
            null_count = row[1] or 0
            if total == 0:
                return {"info": None, "issue": None}
            if null_count == total:
                return {
                    "info": None,
                    "issue": f"slippage 미측정 ({null_count}/{total}건 NULL)",
                }
            return {
                "info": f"매도 slippage: {total - null_count}/{total}건 측정",
                "issue": None,
            }
        except Exception as e:
            return {"info": None, "issue": f"slippage 체크 실패: {str(e)[:40]}"}

    def _hc_systemd_dashboard(self) -> dict:
        """🟢 trading_dashboard 서비스 active 검증"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "trading_dashboard"],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            if status == "active":
                return {"info": "trading_dashboard: active", "issue": None}
            return {"info": None, "issue": f"trading_dashboard: {status}"}
        except Exception as e:
            return {"info": None, "issue": f"systemctl 체크 실패: {str(e)[:40]}"}

    def _hc_disk_usage(self) -> dict:
        """🟢 디스크 사용률"""
        try:
            usage = shutil.disk_usage(str(Path(__file__).parent))
            used_pct = (usage.used / usage.total) * 100
            free_gb = usage.free / (1024 ** 3)
            if used_pct > 90:
                return {
                    "info": None,
                    "issue": f"디스크 사용률 {used_pct:.0f}% (잔여 {free_gb:.1f}GB)",
                }
            return {
                "info": f"디스크: {used_pct:.0f}% 사용 (잔여 {free_gb:.1f}GB)",
                "issue": None,
            }
        except Exception as e:
            return {"info": None, "issue": f"디스크 체크 실패: {str(e)[:40]}"}

    def _hc_closing_bet_universe(self, today_str: str) -> dict:
        """🔵 종가베팅 candidates 오늘 건수"""
        try:
            import sqlite3
            cb_db = Path(__file__).parent / "data" / "closing_bet.db"
            if not cb_db.exists():
                return {"info": None, "issue": None}

            conn = sqlite3.connect(str(cb_db))
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='candidates'"
                )
                if not cursor.fetchone():
                    return {"info": None, "issue": None}
                cursor.execute(
                    "SELECT COUNT(*) FROM candidates WHERE trade_date=?", (today_str,)
                )
                count = cursor.fetchone()[0]
            finally:
                conn.close()

            if count == 0:
                return {"info": None, "issue": None}
            return {"info": f"종가베팅 candidates: {count}건", "issue": None}
        except Exception:
            return {"info": None, "issue": None}  # 종가베팅은 옵션, 실패 무시

    def _hc_sell_lock_residual(self) -> dict:
        """🔵 SellLock 잔존 락 카운트 (15:30 stop_monitoring clear_all 검증)"""
        try:
            snap = sell_lock.snapshot()
            count = len(snap)
            if count == 0:
                return {"info": "SellLock: 잔존 락 0건", "issue": None}
            # 16:10 시점은 장 마감(15:30) 이후 — clear_all 발동했으면 0이어야 함
            return {
                "info": None,
                "issue": (
                    f"SellLock 잔존 의심 (장 마감 후 {count}건 — "
                    f"15:30 clear_all 미발동 가능): {list(snap.keys())[:3]}"
                ),
            }
        except Exception as e:
            return {"info": None, "issue": f"SellLock 체크 실패: {str(e)[:40]}"}

    async def run_daily_health_check(self) -> None:
        """일일 시스템 상태 점검 및 텔레그램 보고 (16:10 KST)"""
        logger.info("=" * 60)
        logger.info("🏥 일일 시스템 헬스체크 (16:10)")
        logger.info("=" * 60)

        try:
            issues: list[str] = []
            info_lines: list[str] = []
            today_str = str(now_kst().date())

            # ===== 1. 프로세스 PID =====
            info_lines.append(f"PID: {os.getpid()}")

            # ===== 2. DB 기반 항목 =====
            db = Database()
            db.connect()
            try:
                # 보유 포지션
                holdings = db.get_portfolio(status="holding")
                info_lines.append(f"보유종목: {len(holdings)}개")
                for h in holdings:
                    prate = (h.get("profit_rate", 0) or 0) * 100
                    info_lines.append(f"  {h['stock_name']} {prate:+.1f}%")

                # 오늘 매매 + 실현 손익
                today_trades = db.get_trades(now_kst().date())
                buys = [t for t in today_trades if t.get("action") == "buy"]
                sells = [t for t in today_trades if t.get("action") == "sell"]
                info_lines.append(f"오늘 매매: 매수 {len(buys)}건, 매도 {len(sells)}건")
                if sells:
                    realized = sum(t.get("profit_amount", 0) or 0 for t in sells)
                    info_lines.append(f"실현 손익: {realized:+,.0f}원")

                # 전일 테마 수집 확인 (공휴일 false alarm 방지: is_trading_day 사용)
                cursor = db.conn.cursor()
                yesterday_date = (now_kst() - timedelta(days=1)).date()
                yesterday_str = str(yesterday_date)
                cursor.execute(
                    "SELECT COUNT(*) FROM themes WHERE date = ?", (yesterday_str,)
                )
                theme_count = cursor.fetchone()[0]
                if theme_count > 0:
                    info_lines.append(f"테마 수집(전일): {theme_count}개")
                elif not is_trading_day(yesterday_date):
                    info_lines.append(f"테마 수집: 전일 휴장 (정상, {yesterday_str})")
                else:
                    issues.append(f"테마 수집 미확인 ({yesterday_str})")

                # 스냅샷 확인
                cursor.execute(
                    "SELECT COUNT(*) FROM daily_snapshots WHERE date = ?", (today_str,)
                )
                if cursor.fetchone()[0] == 0:
                    issues.append("일일 스냅샷 미저장")

                # 신규 헬퍼 호출 (DB 의존 항목)
                # 2026-05-07 추가: 박제/3-stage/Phase A/midweek/makeup/review/slippage
                for hc in (
                    self._hc_theme_score_pinning(db, today_str),
                    self._hc_screening_log_stages(db, today_str),
                    self._hc_phase_a_metrics(db, today_str),
                    self._hc_trade_review_coverage(db, today_str),
                    self._hc_slippage_coverage(db, today_str),
                ):
                    if hc.get("info"):
                        info_lines.append(hc["info"])
                    if hc.get("issue"):
                        issues.append(hc["issue"])
            finally:
                db.close()

            # ===== 3. KIS API =====
            try:
                bal = self.trading_engine.get_balance()
                if bal:
                    cash = bal.get("cash", 0)
                    info_lines.append(f"KIS API: 정상 (가용현금: {cash:,.0f}원)")
                else:
                    issues.append("KIS API: 응답 없음")
            except Exception as e:
                issues.append(f"KIS API: {str(e)[:40]}")

            # ===== 4. Telegram (수신 자체가 정상 증거) =====
            info_lines.append("Telegram: 정상 (이 메시지 수신 시)")

            # ===== 5. pause 상태 =====
            if self.trading_paused:
                info_lines.append("매매 상태: ⏸️ 일시정지")
            else:
                info_lines.append("매매 상태: ▶️ 활성")

            # ===== 6. 활성 테마 =====
            if self.today_themes:
                theme_names = [t.get("theme", t.get("name", "?")) for t in self.today_themes]
                info_lines.append(f"활성 테마: {', '.join(theme_names)}")
            else:
                issues.append("활성 테마 없음")

            # ===== 7. 스케줄러 =====
            if self.scheduler.is_running:
                jobs = self.scheduler.scheduler.get_jobs()
                info_lines.append(f"스케줄러: 정상 ({len(jobs)}개 작업)")
            else:
                issues.append("스케줄러 중지됨")

            # ===== 8. 신규 헬퍼 (DB 비의존) =====
            # 2026-05-07 추가: midweek/makeup/dashboard/disk/closing-bet/sell-lock
            for hc in (
                self._hc_midweek_replacement(),
                self._hc_makeup_reselection(),
                self._hc_systemd_dashboard(),
                self._hc_disk_usage(),
                self._hc_closing_bet_universe(today_str),
                self._hc_sell_lock_residual(),
            ):
                if hc.get("info"):
                    info_lines.append(hc["info"])
                if hc.get("issue"):
                    issues.append(hc["issue"])

            # ===== 보고서 작성 =====
            status = "⚠️ 이상 발견" if issues else "✅ 정상"
            msg = f"🏥 *일일 헬스체크* ({now_kst().strftime('%m/%d %H:%M')})\n\n"
            msg += f"상태: {status}\n\n"

            msg += "📊 *시스템 정보*\n"
            for line in info_lines:
                msg += f"  {line}\n"

            if issues:
                msg += f"\n⚠️ *이상 항목* ({len(issues)}건)\n"
                for issue in issues:
                    msg += f"  • {issue}\n"
            else:
                msg += "\n모든 항목 정상 운영 중"

            self.notifier.send_message(msg)
            logger.info(f"헬스체크 완료: {status} ({len(issues)}건 이상)")

        except Exception as e:
            logger.error(f"헬스체크 실패: {e}")
            try:
                self.notifier.send_message(f"🏥 헬스체크 실패: {e}")
            except Exception:
                pass

    # ===== 수동 실행 =====

    async def run_manual_analysis(self) -> dict:
        """
        수동 전체 파이프라인 (테마 → 스크리닝 → 매수 → 모니터링)

        스케줄러 없이 즉시 전체 프로세스를 실행합니다.
        매수 성공 시 장 마감까지 실시간 모니터링을 유지합니다.
        """
        logger.info("=" * 70)
        logger.info("🔧 수동 전체 파이프라인 실행")
        logger.info(f"   모드: {'모의투자' if self.use_mock else '🔴 실전투자'}")
        logger.info(f"   테스트: {'ON (주문 미실행)' if self.test_mode else 'OFF (실제 주문)'}")
        logger.info("=" * 70)

        # DB 초기화
        self._init_database()
        self.is_running = True

        # 시스템 시작 알림
        self.notifier.send_message(
            f"🔧 수동 파이프라인 시작\n"
            f"모드: {'모의' if self.use_mock else '실전'}\n"
            f"테스트: {'ON' if self.test_mode else 'OFF'}"
        )

        # 1. 테마 분석
        theme_result = await self.run_theme_analysis()
        if not theme_result.get("success"):
            return theme_result

        # 2. 종목 스크리닝 + AI 검증 + 포트폴리오 최적화
        screening_result = await self.run_stock_screening()
        if not screening_result.get("success"):
            return screening_result

        # 3. 매수 실행
        buy_result = await self.execute_buy_orders()
        if not buy_result.get("success"):
            logger.warning(f"매수 실행 결과: {buy_result}")
            return buy_result

        logger.info("\n" + "=" * 70)
        logger.info("✅ 매수 완료 — 실시간 모니터링 시작")
        logger.info("   종료: Ctrl+C")
        logger.info("=" * 70)

        # 4. 실시간 모니터링 (장 마감까지)
        await self.start_monitoring()

        try:
            while self.is_running:
                await asyncio.sleep(10)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("\n수동 모니터링 종료 요청")
        finally:
            await self.stop()

        return buy_result


# ===== CLI 인터페이스 =====

def parse_args():
    """명령줄 인수 파싱"""
    parser = argparse.ArgumentParser(
        description="한국 주식 AI 스윙 트레이딩 시스템"
    )
    
    parser.add_argument(
        "--test",
        action="store_true",
        help="테스트 모드 (실제 주문 없음)"
    )
    
    parser.add_argument(
        "--manual",
        action="store_true",
        help="수동 분석 실행 (스케줄러 없이)"
    )
    
    parser.add_argument(
        "--real",
        action="store_true",
        help="실전투자 모드 (주의!)"
    )
    
    return parser.parse_args()


def acquire_pid_lock() -> bool:
    """PID 락 파일 획득. 이미 실행 중이면 False 반환."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # 프로세스가 실제로 살아있는지 확인
            os.kill(old_pid, 0)
            # 살아있으면 이중 실행
            return False
        except (ProcessLookupError, ValueError):
            # 프로세스가 죽었거나 PID 파일이 깨진 경우 → 락 재획득
            pass
        except PermissionError:
            # 다른 유저 프로세스가 살아있음
            return False

    PID_FILE.write_text(str(os.getpid()))
    return True


def release_pid_lock():
    """PID 락 파일 해제."""
    try:
        if PID_FILE.exists():
            # 자기 PID인 경우에만 삭제
            stored_pid = int(PID_FILE.read_text().strip())
            if stored_pid == os.getpid():
                PID_FILE.unlink()
    except Exception:
        pass


async def main():
    """메인 함수"""
    args = parse_args()

    # PID 락 체크 (이중 실행 방지)
    if not acquire_pid_lock():
        old_pid = PID_FILE.read_text().strip() if PID_FILE.exists() else "?"
        print(f"[ERROR] 트레이딩 시스템이 이미 실행 중입니다 (PID: {old_pid})")
        print(f"   확인: ps -p {old_pid}")
        print(f"   강제 해제: rm {PID_FILE}")
        sys.exit(1)

    try:
        # 시스템 초기화
        system = TradingSystem(
            use_mock=not args.real,
            test_mode=args.test
        )

        if args.manual:
            # 수동 분석
            result = await system.run_manual_analysis()
            print(f"\n분석 결과: {result}")
        else:
            # 전체 시스템 실행
            await system.start()
    finally:
        release_pid_lock()


# ===== 엔트리 포인트 =====

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 한국 주식 AI 스윙 트레이딩 시스템")
    print("=" * 70)
    
    asyncio.run(main())
