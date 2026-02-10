"""
main.py - 한국 주식 AI 스윙 트레이딩 시스템 메인 엔트리

이 파일은 전체 트레이딩 시스템을 통합하고 실행합니다.

기능:
- 시스템 초기화
- 스케줄러 시작
- 일일 트레이딩 파이프라인 실행
- 장 초반 관찰 및 필터링
- 실시간 모니터링 (분할 익절 + 트레일링 스탑)
- 테마 로테이션 (2주 단위)

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
    - 테마 로테이션: 2주 단위, 점수 -20% 시 즉시 변경

작성자: AI Trading System
버전: 2.0.0 (하이브리드 전략 + 테마 로테이션)
"""

import asyncio
import argparse
import os
import signal
import sys
from datetime import datetime, date
from typing import Optional

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# PID 락 파일 경로
PID_FILE = Path(__file__).parent / "trading_system.pid"

from logger import logger
from config import settings, now_kst
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
        self.db = Database()
        
        # 상태
        self.is_running = False
        self.today_portfolio: Optional[dict] = None
        self.today_themes: list[dict] = []       # 08:30 선정 테마 (09:05 스크리닝용)
        self.today_candidates: list[dict] = []   # 09:05 선정 후보 (10-15개)
        self.today_orders: list[dict] = []       # 09:25 최종 매수 (5-8개)
        self.current_themes: list[dict] = []     # 현재 테마 리스트
        self.today_ai_analysis: list[dict] = []  # AI 분석 결과 (선정 이유 포함)
        self.today_trades: list[dict] = []       # 오늘 거래 내역
        self.observation_result = None              # 실시간 관찰 결과
        self._observer_task = None                  # 관찰 비동기 태스크
        self._last_theme_rotation_date: Optional[date] = None  # 7일 고정 로테이션

        # 시그널 핸들러
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        mode = "모의투자" if self.use_mock else "실전투자"
        logger.info(f"🚀 트레이딩 시스템 초기화 ({mode})")
        logger.info(f"   분할 익절: {settings.TAKE_PROFIT_1:.0%}/{settings.TAKE_PROFIT_2:.0%}/{settings.TAKE_PROFIT_3:.0%}")
        logger.info(f"   트레일링 스탑: 최고가 -{settings.TRAILING_STOP_PERCENT:.0%}")
        logger.info(f"   테마 로테이션: {settings.THEME_REVIEW_DAYS}일 단위")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        logger.info("\n시스템 종료 신호 수신...")
        asyncio.create_task(self.stop())
    
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
        
        # 시스템 시작 알림
        self.notifier.send_system_start()
        
        # 스케줄러 콜백 등록
        self._setup_scheduler_callbacks()
        
        # 스케줄러 시작
        self.scheduler.start()
        
        logger.info("\n✅ 시스템 시작 완료")
        logger.info("📅 스케줄에 따라 자동 실행됩니다.")
        logger.info("   종료하려면 Ctrl+C를 누르세요.\n")
        
        # 메인 루프
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def stop(self) -> None:
        """시스템 종료"""
        if not self.is_running:
            return
        
        logger.info("\n시스템 종료 중...")
        
        self.is_running = False
        
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
        logger.info("=" * 70)
        logger.info("📊 테마 분석 시작 (08:30)")
        logger.info("=" * 70)

        # 기존 테마가 있고, 7일 미경과면 재사용
        if self.today_themes and self._last_theme_rotation_date:
            days_since = (date.today() - self._last_theme_rotation_date).days
            if days_since < settings.THEME_REVIEW_DAYS:
                logger.info(
                    f"🔄 기존 테마 유지 ({days_since}일차/{settings.THEME_REVIEW_DAYS}일)"
                )
                for t in self.today_themes:
                    t_name = t.get("theme", t.get("name", ""))
                    t_score = t.get("score", 0)
                    logger.info(f"   - {t_name} ({t_score:.1f}점)")
                return {"success": True, "themes": len(self.today_themes), "reused": True}

        start_time = now_kst()

        try:
            # 1. 테마 크롤링
            logger.info("\n📊 Step 1: 테마 크롤링")
            from modules.theme_analyzer import (
                crawl_all_themes,
                score_themes,
                select_top_themes
            )

            raw_themes = crawl_all_themes()
            logger.info(f"   크롤링된 테마: {len(raw_themes)}개")

            # 2. 테마 점수화 (모멘텀 중심)
            scored_themes = score_themes(raw_themes[:20])
            logger.info(f"   점수화 완료: {len(scored_themes)}개")

            # 현재 테마 저장
            self.current_themes = scored_themes

            # 3. 테마 로테이션 체크
            logger.info("\n🔄 Step 2: 테마 로테이션 체크")
            should_rotate, reason = self.theme_rotator.check_rotation_needed(scored_themes)
            logger.info(f"   로테이션 필요: {should_rotate} (이유: {reason})")

            if should_rotate:
                new_theme = self.theme_rotator.select_new_main_theme(scored_themes)
                if new_theme:
                    self.notifier.send_message(
                        f"🔄 테마 로테이션!\n"
                        f"- 새 테마: {new_theme['theme']}\n"
                        f"- 점수: {new_theme['score']:.1f}\n"
                        f"- 이유: {reason}"
                    )
            else:
                if self.theme_rotator.current_main_theme is None and scored_themes:
                    self.theme_rotator.set_main_theme(
                        scored_themes[0]['theme'],
                        scored_themes[0]['score']
                    )

            # 4. 상위 테마 선정 (config에서 읽기)
            themes = select_top_themes(scored_themes, count=settings.TOP_THEME_COUNT)
            self.today_themes = themes  # 09:05 스크리닝에서 사용
            self._last_theme_rotation_date = date.today()  # 로테이션 날짜 기록
            logger.info(f"   선정 테마: {len(themes)}개")

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

            # 알림
            theme_list = []
            for t in themes[:5]:
                t_name = t.get("theme", "")
                t_score = t.get("score", 0)
                theme_list.append(f"  • {t_name}({t_score:.1f}점)")
            theme_text = "\n".join(theme_list)

            self.notifier.send_message(
                f"📊 08:30 테마 분석 완료\n\n"
                f"🎯 선정 테마: {len(themes)}개\n"
                f"{theme_text}\n\n"
                f"⏰ 09:05 장 시작 후 종목 스크리닝 예정"
            )

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
        logger.info("=" * 70)
        logger.info("🔍 종목 스크리닝 시작 (09:05, 장 시작 후)")
        logger.info("=" * 70)

        start_time = now_kst()

        # 08:30 테마 분석 결과 확인
        themes = getattr(self, 'today_themes', None)
        if not themes:
            logger.warning("선정된 테마가 없습니다 (08:30 테마 분석 확인 필요)")
            return {"success": False, "reason": "테마 없음"}

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
            # 실제 주문가능금액 조회 (예수금이 아닌 KIS API 매수가능조회)
            orderable_cash = self.trading_engine.get_orderable_cash()
            if orderable_cash > 0:
                available_cash = orderable_cash
                logger.info(f"   주문가능현금: {available_cash:,}원")
            else:
                balance = self.trading_engine.get_balance()
                available_cash = balance.get("cash", settings.TOTAL_CAPITAL)
                logger.info(f"   예수금 기준: {available_cash:,}원 (주문가능금액 조회 실패)")

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
        자동 매수 실행 (09:25)
        
        장 초반 필터링을 수행한 후 최종 매수를 실행합니다.
        
        필터링 기준:
        1. 시초가 갭 ±3% 이내
        2. 외국인+기관 순매수
        3. 거래량 정상
        
        Returns:
            실행 결과
        """
        logger.info("=" * 70)
        logger.info("💰 자동 매수 실행 (09:25)")
        logger.info("=" * 70)
        
        if not self.today_candidates:
            logger.warning("매수할 후보 종목이 없습니다")
            return {"success": False, "reason": "후보 없음"}
        
        # === 관찰 완료 대기 ===
        if self._observer_task and not self._observer_task.done():
            logger.info("⏳ 관찰 완료 대기 중...")
            try:
                await asyncio.wait_for(self._observer_task, timeout=30)
            except asyncio.TimeoutError:
                logger.warning("관찰 타임아웃 - 가용 데이터로 진행")

        # === 장 초반 필터링 실행 ===
        logger.info(f"\n📊 장 초반 필터링 시작 (후보 {len(self.today_candidates)}개)")

        if settings.ENABLE_MORNING_FILTER:
            # 필터링 실행 (관찰 결과 전달)
            filter_result = await asyncio.to_thread(
                self.morning_screener.filter_candidates,
                self.today_candidates,
                settings.MORNING_OBSERVATION_MINUTES,
                self.observation_result
            )
            
            if not filter_result.passed_stocks:
                logger.warning("필터링 통과 종목이 없습니다")
                self.notifier.send_message(
                    "⚠️ 09:25 매수 취소\n"
                    f"- 필터링 통과 종목 없음\n"
                    f"- 갭 제외: {filter_result.gap_excluded}개\n"
                    f"- 수급 제외: {filter_result.supply_excluded}개\n"
                    f"- 거래량 제외: {filter_result.volume_excluded}개\n"
                    f"- 트렌드 제외: {filter_result.trend_excluded}개"
                )
                return {"success": False, "reason": "필터링 통과 없음"}
            
            # 필터링 통과 종목으로 최종 주문 구성
            self.today_orders = filter_result.passed_stocks
            
            logger.info(f"\n✅ 필터링 완료")
            logger.info(f"   초기 후보: {filter_result.initial_count}개")
            logger.info(f"   최종 선정: {filter_result.final_count}개")
        else:
            # 필터 비활성화 시 후보 전체 사용
            self.today_orders = self.today_candidates
            logger.info("   [필터 비활성화] 후보 전체 매수")
        
        # === 매수 실행 ===
        logger.info(f"\n💰 매수 주문 실행: {len(self.today_orders)}건")
        
        if self.test_mode:
            logger.info("   [테스트 모드] 실제 주문 미실행")
            return {"success": True, "test_mode": True, "orders": len(self.today_orders)}
        
        try:
            result = await asyncio.to_thread(
                self.trading_engine.execute_portfolio,
                self.today_orders,
                save_to_db=True
            )
            
            # 매수 알림 및 거래 내역 저장
            success_count = 0
            for order in result.get("orders", []):
                if order.get("success"):
                    success_count += 1
                    self.notifier.send_buy_alert(
                        order.get("stock_name", ""),
                        order.get("stock_code", ""),
                        order.get("quantity", 0),
                        order.get("price", 0)
                    )
                    # 거래 내역 저장 (일일 리포트용)
                    self.today_trades.append({
                        "action": "buy",
                        "stock_code": order.get("stock_code", ""),
                        "stock_name": order.get("stock_name", ""),
                        "shares": order.get("quantity", 0),
                        "price": order.get("price", 0)
                    })

            # 결과 알림
            self.notifier.send_message(
                f"✅ 09:25 매수 완료\n"
                f"- 주문: {len(self.today_orders)}건\n"
                f"- 성공: {success_count}건"
            )

            return result
            
        except Exception as e:
            logger.error(f"매수 실행 실패: {e}")
            self.notifier.send_error_alert("매수 실행", str(e))
            return {"success": False, "error": str(e)}
    
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
        
        # 모니터링 시작 (백그라운드)
        asyncio.create_task(self.monitor.start_monitoring())
    
    async def stop_monitoring(self) -> None:
        """실시간 모니터링 종료"""
        if self.monitor:
            await self.monitor.stop_monitoring()
            logger.info("📊 모니터링 종료")
    
    def _on_stop_loss(self, position, price) -> None:
        """손절 발동 콜백"""
        self.notifier.send_stop_loss_alert(
            position.stock_name,
            int(position.buy_price),
            int(price),
            position.profit_rate * 100
        )
    
    def _on_partial_profit(self, position, price, stage: int) -> None:
        """분할 익절 발동 콜백"""
        self.notifier.send_message(
            f"🔺 {stage}차 익절 발동!\n"
            f"- 종목: {position.stock_name}\n"
            f"- 현재가: {int(price):,}원\n"
            f"- 수익률: {position.profit_rate * 100:+.1f}%\n"
            f"- 남은 수량: {position.remaining_shares}/{position.shares}주"
        )
    
    def _on_trailing_stop(self, position, price) -> None:
        """트레일링 스탑 발동 콜백"""
        self.notifier.send_message(
            f"📉 트레일링 스탑 발동!\n"
            f"- 종목: {position.stock_name}\n"
            f"- 현재가: {int(price):,}원\n"
            f"- 최고가: {int(position.highest_price):,}원\n"
            f"- 수익률: {position.profit_rate * 100:+.1f}%\n"
            f"- 보유일: {position.hold_days}일"
        )
    
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
                logger.info(f"   보유 일수: {theme_info['days_held']}일 / {settings.THEME_REVIEW_DAYS}일")
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
        logger.info("📋 장 마감 정리")
        
        # 미체결 주문 취소
        self.trading_engine.cancel_all_pending()
        
        # 포트폴리오 현황 출력
        if self.monitor:
            self.monitor.display_status()
        
        # 리밸런싱 준비 (다음날 분석용)
        # 실제 리밸런싱은 다음날 분석 시 수행
    
    # ===== 일일 리포트 =====
    
    async def send_daily_report(self) -> None:
        """일일 리포트 발송 (16:00)"""
        logger.info("📊 일일 리포트 생성")

        try:
            # 현재 포트폴리오
            balance = self.trading_engine.get_balance()
            positions = balance.get("positions", [])

            # 성과 지표
            calc = PerformanceCalculator()
            metrics = {
                "sharpe_ratio": 0,
                "mdd": 0,
                "win_rate": 0,
                "total_return": 0
            }

            # 리포트 전송 (테마 선정 이유 + AI 분석 이유 포함)
            self.notifier.send_daily_report(
                portfolio=positions,
                metrics=metrics,
                themes=self.current_themes,         # 테마 선정 이유
                ai_analysis=self.today_ai_analysis, # AI 종목 선정 이유
                today_trades=self.today_trades      # 오늘 거래 내역
            )

            logger.info("✅ 일일 리포트 발송 완료")

        except Exception as e:
            logger.error(f"리포트 발송 실패: {e}")
    
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
