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
    - 보유 기간: 수익(+5%) 14일, 손실 7일
    - 테마 로테이션: 7일 단위, 점수 -20% 시 즉시 변경

작성자: AI Trading System
버전: 2.0.0 (하이브리드 전략 + 테마 로테이션)
"""

import asyncio
import argparse
import json
import os
import signal
import sys
from datetime import datetime, date, timedelta
from typing import Optional

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# PID 락 파일 경로
PID_FILE = Path(__file__).parent / "trading_system.pid"

from logger import logger
from config import settings, now_kst, is_trading_day
from database import Database
from scheduler import TradingScheduler

# 모듈 임포트
from modules.theme_analyzer import select_top_themes, ThemeRotator
from modules.stock_screener import run_daily_screening
from modules.ai_verifier import run_daily_verification
from modules.portfolio_optimizer import run_daily_optimization, display_portfolio
from modules.trading_engine import TradingEngine
from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2
from modules.rebalancer import run_daily_rebalancing
from modules.reporter import (
    PerformanceCalculator,
    generate_daily_report,
    TelegramNotifier
)
from modules.morning_filter import MorningScreener, run_morning_observation
from modules.morning_filter.candidate_observer import CandidateObserver
from modules.post_trade_analyzer import PostTradeAnalyzer


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
        self._listener_task = None                  # 텔레그램 명령어 리스너 태스크
        self._last_theme_rotation_date: Optional[date] = None  # 7일 고정 로테이션
        self.trading_paused = False  # 텔레그램 /pause로 매매 일시정지

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
                # DB 행을 코드 내부 형식으로 정규화 (name+theme 양쪽 키 포함)
                normalized = [
                    {
                        "name": t["theme_name"],
                        "theme": t["theme_name"],
                        "score": t["score"],
                        "total_score": t["score"],
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
        self.scheduler.on_weekly_trade_review = self.run_weekly_trade_review  # 금 17:30 주간 복기
        self.scheduler.on_daily_health_check = self.run_daily_health_check  # 16:10 헬스체크

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

        # 기존 테마가 있고, 선정일로부터 7일 이내면 재사용
        # (화~월 5영업일 사이클: 화요일 선정 → 다음 화요일 전까지 유지)
        # 단, 화요일은 주간 재선정일이므로 항상 재분석 실행
        if self.today_themes and self._last_theme_rotation_date:
            today = now_kst().date()
            days_since_rotation = (today - self._last_theme_rotation_date).days
            is_tuesday = (today.weekday() == 1)
            same_week = (days_since_rotation < 7) and not is_tuesday
            if same_week:
                logger.info(
                    f"🔄 기존 테마 유지 (이번 주 {self._last_theme_rotation_date.strftime('%m/%d')} 선정)"
                )

                # DB 복원 테마에 종목 목록이 없으면 크롤링으로 보충
                if not any(t.get("url") for t in self.today_themes):
                    logger.info("   종목 URL 없음 → 크롤링으로 보충")
                    try:
                        from modules.theme_analyzer import crawl_all_themes
                        from modules.theme_analyzer.crawlers import search_naver_theme
                        from modules.stock_screener.screener import _search_naver_upjong

                        all_crawled = await asyncio.to_thread(crawl_all_themes)
                        crawled_map = {t["name"]: t for t in all_crawled}
                        for t in self.today_themes:
                            t_name = t.get("theme", t.get("name", ""))
                            if t_name in crawled_map:
                                matched = crawled_map[t_name]
                                t["url"] = matched.get("url") or ""
                                t["stock_count"] = matched.get("stock_count", 0)
                                if t.get("url"):
                                    logger.info(f"   ✓ [{t_name}] url 보충 완료 ({t['stock_count']}종목)")
                                    continue
                                logger.warning(f"   [{t_name}] 크롤링 결과에 URL 없음 → 폴백 시도")
                            # 폴백 1: 네이버 테마 개별 검색
                            naver_result = await asyncio.to_thread(search_naver_theme, t_name)
                            if naver_result and naver_result.get("url"):
                                t["url"] = naver_result["url"]
                                t["stock_count"] = naver_result.get("stock_count", 0)
                                logger.info(f"   ✓ [{t_name}] 네이버 검색으로 url 보충 ({t['stock_count']}종목)")
                            else:
                                # 폴백 2: 네이버 업종 검색
                                upjong_url = await asyncio.to_thread(_search_naver_upjong, t_name)
                                if upjong_url:
                                    t["url"] = upjong_url
                                    logger.info(f"   ✓ [{t_name}] 업종 검색으로 url 보충")
                                else:
                                    logger.warning(f"   ✗ [{t_name}] 모든 URL 검색 실패 (09:05 스크리닝에서 재시도)")
                    except Exception as e:
                        logger.error(f"   종목 URL 보충 실패: {e}")

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
                    theme_lines.append(f"  {i}. {t_name} ({t_score:.1f}점, {stock_cnt}종목) 📌유지")
                self.notifier.send_message(
                    f"📊 08:30 테마 분석\n\n"
                    f"🔄 기존 테마 유지 (이번 주 {self._last_theme_rotation_date.strftime('%m/%d')} 선정)\n"
                    + "\n".join(theme_lines)
                    + f"\n\n📅 다음 재평가: {next_review.strftime('%m/%d')} (화) ({days_until_tuesday}일 후)"
                )

                return {"success": True, "themes": len(self.today_themes), "reused": True}

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

            today = now_kst().date()
            is_tuesday = (today.weekday() == 1)

            if is_tuesday:
                logger.info("\n📊 Step 1: 화요일 — 6일 누적 가중 집계")
                scored_themes = aggregate_weekly_scores(self.db, base_date=today - timedelta(days=1))
                if scored_themes:
                    logger.info(f"   가중 집계 완료: {len(scored_themes)}개 테마")
                else:
                    logger.warning("   가중 집계 데이터 없음 → 실시간 크롤링 폴백")
                    is_tuesday = False  # 폴백

            if not is_tuesday:
                logger.info("\n📊 Step 1: 테마 크롤링")
                raw_themes = crawl_all_themes()
                logger.info(f"   크롤링된 테마: {len(raw_themes)}개")
                scored_themes = score_themes(raw_themes[:20])
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
                                logger.warning(f"   ✗ [{t_name}] 모든 URL 검색 실패 (09:05 스크리닝에서 재시도)")
                    logger.info(f"   URL 보충: {supplemented}/{len(themes)}개 테마")
                except Exception as e:
                    logger.error(f"   종목 URL 보충 실패: {e}")

            # 5. DB 저장 (대시보드 테마 탭용)
            try:
                themes_to_save = [
                    {
                        "theme": t.get("theme", t.get("name", "")),
                        "score": t.get("score", 0),
                        "momentum": t.get("momentum_score", 0),
                        "supply_ratio": 0,
                        "news_count": t.get("news_count", 0),
                        "ai_sentiment": t.get("ai_sentiment", 0),
                        "category": t.get("category", "기타"),
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
            logger.info("   └─ 09:05 장 시작 후 종목 스크리닝 예정")

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
                        msg += f"  {idx}. {name} ({score:.1f}점, {diff:+.1f})\n"

                if new_entries:
                    msg += f"\n🆕 신규: {len(new_entries)}개\n"
                    for t in new_entries:
                        idx += 1
                        name = t.get("theme", t.get("name", ""))
                        score = t.get("score", 0)
                        momentum = t.get("avg_change_rate", 0)
                        msg += f"  {idx}. {name} ({score:.1f}점) — 모멘텀 {momentum:+.1f}%\n"

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
                    msg += f"  {i}. {name} ({score:.1f}점)\n"

            msg += f"\n📅 다음 재평가: {next_review.strftime('%m/%d')} (화) ({days_until_tuesday}일 후)\n"
            msg += f"⏰ 09:05 장 시작 후 종목 스크리닝 예정"

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
            self.notifier.send_message("⏸️ 09:05 스크리닝 스킵 (일시정지 중)")
            return {"success": True, "paused": True}

        logger.info("=" * 70)
        logger.info("🔍 종목 스크리닝 시작 (09:05, 장 시작 후)")
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
                    f"⚠️ 09:05 스크리닝 완료 - 후보 종목 없음\n"
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
            logger.info("   └─ 09:25 필터링 후 최종 매수 실행")

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
                    f"🔍 09:05 스크리닝 완료\n\n"
                    f"📊 매수 후보: {len(self.today_candidates)}개\n"
                    f"─────────────────\n"
                    f"{stock_text}\n"
                    f"─────────────────\n"
                    f"⏰ 09:25 필터링 후 매수 예정"
                )

            # 실시간 관찰 시작 (09:05 ~ 09:23)
            if settings.ENABLE_MORNING_FILTER and self.today_candidates:
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
            self.notifier.send_message("⏸️ 09:25 매수 스킵 (일시정지 중)")
            return {"success": True, "paused": True}

        logger.info("=" * 70)
        logger.info("💰 빈 슬롯 매수 실행 (09:25)")
        logger.info("=" * 70)

        # Phase 0: 관찰 완료 대기
        if self._observer_task and not self._observer_task.done():
            logger.info("⏳ 관찰 완료 대기 중...")
            try:
                await asyncio.wait_for(self._observer_task, timeout=settings.MORNING_OBSERVATION_MINUTES * 60)
            except asyncio.TimeoutError:
                logger.warning("관찰 타임아웃 - 가용 데이터로 진행")

        # Phase 1: 안전 체크
        if not self.today_ai_analysis:
            logger.warning("AI 분석 결과 없음 - 매수 스킵")
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

        # Phase 4: 현금 조회
        if self.test_mode:
            available_cash = settings.TOTAL_CAPITAL
        else:
            available_cash = self.trading_engine.get_orderable_cash()
            if available_cash <= 0:
                balance = self.trading_engine.get_balance()
                available_cash = balance.get("cash", 0)
        logger.info(f"   가용 현금: {available_cash:,}원")

        if available_cash < 100_000:
            logger.warning("   현금 부족 (<10만원) - 신규 매수 스킵")
            self._send_buy_summary(current_holdings, [], 0)
            return {"success": True, "held": held_count, "bought": 0}

        # Phase 5: 신규 후보 필터링 (보유 종목 제외)
        new_candidates = [c for c in self.today_candidates if c['stock_code'] not in held_codes]
        logger.info(f"   신규 후보 (보유 제외): {len(new_candidates)}개")

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

        # Phase 6: 포트폴리오 최적화 (슬롯 비례 자본 배분)
        filtered_codes = {c['stock_code'] for c in filtered_new}
        all_ai_candidates = [
            s for s in self.today_ai_analysis
            if s['code'] in filtered_codes
        ]
        new_ai_stocks = all_ai_candidates[:available_slots]
        self._slot_excluded = all_ai_candidates[available_slots:]

        if new_ai_stocks:
            # 가용현금 기반 슬롯 배분 (수익 재투자 반영)
            per_slot_capital = available_cash // available_slots
            target_capital = per_slot_capital * len(new_ai_stocks)
            capital_for_new = min(target_capital, available_cash)
            logger.info(f"   슬롯 배분: {per_slot_capital:,.0f}원/종목 × {len(new_ai_stocks)}종목 = {target_capital:,.0f}원 (가용현금÷빈슬롯)")
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
                        self.notifier.send_buy_alert(
                            order.get("stock_name", ""),
                            order.get("stock_code", ""),
                            order.get("quantity", 0),
                            filled_price
                        )
                        self.today_trades.append({
                            "action": "buy",
                            "stock_code": order.get("stock_code", ""),
                            "stock_name": order.get("stock_name", ""),
                            "shares": order.get("quantity", 0),
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

        if not current_holdings and not new_buy_orders:
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
        if new_buy_orders:
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"\n📥 신규 매수: {bought_count}종목\n")
            for i, o in enumerate(new_buy_orders, 1):
                code = o.get('stock_code', '')
                name = o.get('stock_name', code)
                amount = o.get('amount', 0)
                stop_loss = o.get('stop_loss', 0)
                take_profit = o.get('take_profit', 0)
                price = o.get('price', 0)
                ai = ai_lookup.get(code, {})

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
                    reason_short = reason[:40] if reason else ""
                    ai_line = f"🤖 AI {sentiment:.1f}/10"
                    if conf_pct:
                        ai_line += f" ({conf_pct})"
                    if reason_short:
                        ai_line += f" — {reason_short}"
                    lines.append(ai_line)

                # 손절/목표
                if price and price > 0:
                    sl_pct = ((stop_loss / price) - 1) * 100 if stop_loss else 0
                    tp_pct = ((take_profit / price) - 1) * 100 if take_profit else 0
                    lines.append(f"⚡ 손절 {sl_pct:+.0f}% / 목표 {tp_pct:+.0f}%")
                elif stop_loss or take_profit:
                    lines.append(f"⚡ 손절 {stop_loss:,.0f}원 / 목표 {take_profit:,.0f}원")

                lines.append("")  # 종목 간 빈 줄

        # 탈락 종목
        excluded_lines = []

        # 1) 모닝필터 제외
        for s in getattr(self, '_morning_excluded', []):
            name = s.get('name', s.get('stock_name', s.get('code', '?')))
            gap = s.get('gap_percent', 0)
            reason = f"모닝필터 (갭 {gap:+.1f}%)" if gap else "모닝필터"
            excluded_lines.append(f"  - {name} — {reason}")

        # 2) 슬롯 부족
        for s in getattr(self, '_slot_excluded', []):
            name = s.get('name', s.get('stock_name', s.get('code', '?')))
            excluded_lines.append(f"  - {name} — 슬롯 부족")

        # 3) AI 미통과 (today_ai_analysis 중 ai_passed=False)
        # AI 미통과 종목은 today_ai_analysis에 포함 안됨 (passed만 저장)
        # 대신 run_daily_verification에서 이미 필터됨 — 별도 추적 불필요

        if excluded_lines:
            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"\n❌ 탈락: {len(excluded_lines)}종목")
            lines.extend(excluded_lines)

        self.notifier.send_message("\n".join(lines))
    
    # ===== 모니터링 (V2: 분할 익절 + 트레일링 스탑) =====
    
    async def start_monitoring(self) -> None:
        """실시간 모니터링 시작 (V2)"""
        logger.info("=" * 70)
        logger.info("📊 실시간 모니터링 V2 시작")
        logger.info(f"   - 분할 익절: +{settings.TAKE_PROFIT_1:.0%}/+{settings.TAKE_PROFIT_2:.0%}/+{settings.TAKE_PROFIT_3:.0%}")
        logger.info(f"   - 트레일링 스탑: 최고가 -{settings.TRAILING_STOP_PERCENT:.0%}")
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
        """실시간 모니터링 종료"""
        if self.monitor:
            await self.monitor.stop_monitoring()
            logger.info("📊 모니터링 종료")
    
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
                "price": int(price), "reason": "보유기간 초과"
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

        Returns:
            체크 결과
        """
        logger.info("=" * 70)
        logger.info("🔄 테마 로테이션 체크 (08:00)")
        logger.info("=" * 70)

        try:
            # 현재 테마 정보 출력
            theme_info = self.theme_rotator.get_main_theme_info()
            if theme_info:
                logger.info(f"   현재 테마: {theme_info['theme_name']}")
                today_check = now_kst().date()
                is_review_day = (self._last_theme_rotation_date is None or
                    today_check.weekday() == 1 or
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

            return {"success": True, "theme_info": theme_info}

        except Exception as e:
            logger.error(f"테마 로테이션 체크 실패: {e}")
            return {"success": False, "error": str(e)}
    
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
            prev_peak = previous[0].get("peak_value", capital) if previous else capital
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

            # 2. 점수화 (모멘텀 + 뉴스 + AI 감성)
            scored_themes = await asyncio.to_thread(
                score_themes, raw_themes[:30], True, False  # include_news=True, include_ai=False (비용 절감)
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
                                + BASE_SCORE, 2
                            )
                            theme["score"] = theme["total_score"]
                    # 재정렬
                    scored_themes.sort(key=lambda x: x.get("total_score", 0), reverse=True)
                    logger.info(f"   AI 감성 분석 완료: {len(ai_map)}개 테마")
            except Exception as ai_err:
                logger.warning(f"   AI 감성 분석 스킵: {ai_err}")

            # 4. DB 저장
            themes_to_save = [
                {
                    "theme": t.get("theme", t.get("name", "")),
                    "score": t.get("score", 0),
                    "momentum": t.get("momentum_score", 0),
                    "supply_ratio": 0,
                    "news_count": t.get("news_count", 0),
                    "ai_sentiment": t.get("ai_sentiment", 0),
                    "category": t.get("category", "기타"),
                }
                for t in scored_themes
            ]
            self.db.save_theme_scores(themes_to_save, now_kst().date(), selected=False)
            logger.info(f"   DB 저장 완료: {len(themes_to_save)}개 테마 ({now_kst().date()}, 일별수집)")

        except Exception as e:
            logger.error(f"일별 테마 수집 실패: {e}")

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

    async def run_daily_health_check(self) -> None:
        """일일 시스템 상태 점검 및 텔레그램 보고 (16:10)"""
        logger.info("=" * 60)
        logger.info("🏥 일일 시스템 헬스체크 (16:10)")
        logger.info("=" * 60)

        try:
            issues = []
            info_lines = []
            today_str = str(now_kst().date())

            # 1. 프로세스 상태
            import os
            pid = os.getpid()
            info_lines.append(f"PID: {pid}")

            # 2. DB 상태
            db = Database()
            db.connect()
            try:
                # 보유 포지션
                holdings = db.get_portfolio(status="holding")
                info_lines.append(f"보유종목: {len(holdings)}개")
                for h in holdings:
                    prate = h.get("profit_rate", 0) or 0
                    info_lines.append(f"  {h['stock_name']} {prate:+.1f}%")

                # 오늘 매매
                today_trades = db.get_trades(now_kst().date())
                buys = [t for t in today_trades if t.get("action") == "buy"]
                sells = [t for t in today_trades if t.get("action") == "sell"]
                info_lines.append(f"오늘 매매: 매수 {len(buys)}건, 매도 {len(sells)}건")

                # 실현 손익
                if sells:
                    realized = sum(t.get("profit_amount", 0) or 0 for t in sells)
                    info_lines.append(f"실현 손익: {realized:+,.0f}원")

                # 테마 데이터 수집 확인 (전일분 — 당일 17:05는 아직 미실행)
                cursor = db.conn.cursor()
                yesterday_str = str((now_kst() - timedelta(days=1)).date())
                cursor.execute(
                    "SELECT COUNT(*) FROM themes WHERE date = ?", (yesterday_str,)
                )
                theme_count = cursor.fetchone()[0]
                if theme_count > 0:
                    info_lines.append(f"테마 수집(전일): {theme_count}개 저장됨")
                else:
                    # 월요일이면 금요일 확인
                    if now_kst().weekday() == 0:
                        info_lines.append("테마 수집: 주말 미실행 (정상)")
                    else:
                        issues.append(f"테마 수집 미확인 ({yesterday_str})")

                # 스냅샷 확인
                cursor.execute(
                    "SELECT COUNT(*) FROM daily_snapshots WHERE date = ?", (today_str,)
                )
                snap = cursor.fetchone()[0]
                if snap == 0:
                    issues.append("일일 스냅샷 미저장")

                # screening_log 확인
                cursor.execute(
                    "SELECT COUNT(*) FROM screening_log WHERE date = ?", (today_str,)
                )
                screen_count = cursor.fetchone()[0]
                info_lines.append(f"스크리닝 로그: {screen_count}건")

            finally:
                db.close()

            # 3. API 연결 상태
            # KIS API
            try:
                bal = self.trading_engine.get_balance()
                if bal:
                    cash = bal.get("cash", 0)
                    info_lines.append(f"KIS API: 정상 (가용현금: {cash:,.0f}원)")
                else:
                    issues.append("KIS API: 응답 없음")
            except Exception as e:
                issues.append(f"KIS API: {str(e)[:40]}")

            # Telegram (이미 보내고 있으면 정상)
            info_lines.append("Telegram: 정상 (이 메시지 수신 시)")

            # 4. pause 상태
            if self.trading_paused:
                info_lines.append("매매 상태: ⏸️ 일시정지")
            else:
                info_lines.append("매매 상태: ▶️ 활성")

            # 5. 테마 정보
            if self.today_themes:
                theme_names = [t.get("theme", t.get("name", "?")) for t in self.today_themes]
                info_lines.append(f"활성 테마: {', '.join(theme_names)}")
            else:
                issues.append("활성 테마 없음")

            # 6. 스케줄러 상태
            if self.scheduler.is_running:
                jobs = self.scheduler.scheduler.get_jobs()
                info_lines.append(f"스케줄러: 정상 ({len(jobs)}개 작업)")
            else:
                issues.append("스케줄러 중지됨")

            # 보고서 작성
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
