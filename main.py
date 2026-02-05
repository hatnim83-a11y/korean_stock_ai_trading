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
    08:30 - 테마 분석 → 테마 로테이션 체크 → 종목 스크리닝 → AI 검증 → 후보 10-15개 선정
    09:00 - 장 초반 관찰 시작 (시초가/수급/거래량 모니터링)
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
import signal
import sys
from datetime import datetime, date
from typing import Optional

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from logger import logger
from config import settings
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


class TradingSystem:
    """
    한국 주식 AI 스윙 트레이딩 시스템
    
    전체 트레이딩 파이프라인을 관리합니다.
    
    일일 흐름:
    1. 08:30 - 테마 분석 → 종목 스크리닝 → AI 검증 → 후보 선정 (10-15개)
    2. 09:00 - 장 초반 관찰 (시초가/수급/거래량 모니터링)
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
        self.today_candidates: list[dict] = []   # 08:30 선정 후보 (10-15개)
        self.today_orders: list[dict] = []       # 09:25 최종 매수 (5-8개)
        self.current_themes: list[dict] = []     # 현재 테마 리스트
        
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
        logger.info(f"   시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
        self.scheduler.on_daily_analysis = self.run_daily_analysis       # 08:30
        self.scheduler.on_morning_observation = self.run_morning_observation  # 09:00
        self.scheduler.on_execute_buy = self.execute_buy_orders          # 09:25
        self.scheduler.on_monitoring_start = self.start_monitoring       # 09:26
        self.scheduler.on_monitoring_stop = self.stop_monitoring         # 15:30
        self.scheduler.on_market_close = self.run_market_close           # 15:35
        self.scheduler.on_daily_report = self.send_daily_report          # 16:00
        self.scheduler.on_theme_check = self.check_theme_rotation        # 08:00 테마 체크
    
    # ===== 일일 분석 파이프라인 =====
    
    async def run_daily_analysis(self) -> dict:
        """
        일일 분석 실행 (08:30)
        
        파이프라인:
        0. 테마 로테이션 체크 (2주 단위)
        1. 테마 분석 → 상위 5개 테마
        2. 종목 스크리닝 → 후보 종목
        3. AI 검증 → 검증 통과 종목
        4. 후보 선정 → 10-15개 (09:00 관찰용)
        
        ※ 최종 매수는 09:25에 장 초반 필터링 후 실행
        
        Returns:
            분석 결과
        """
        logger.info("=" * 70)
        logger.info("🔍 일일 분석 파이프라인 시작 (08:30)")
        logger.info("=" * 70)
        
        start_time = datetime.now()
        
        try:
            # 1. 테마 분석
            logger.info("\n📊 Step 1: 테마 분석")
            from modules.theme_analyzer import (
                crawl_all_themes,
                score_themes,
                select_top_themes
            )
            
            # 테마 크롤링
            raw_themes = crawl_all_themes()
            logger.info(f"   크롤링된 테마: {len(raw_themes)}개")
            
            # 테마 점수화
            scored_themes = score_themes(raw_themes[:20])
            logger.info(f"   점수화 완료: {len(scored_themes)}개")
            
            # 현재 테마 저장 (로테이션 체크용)
            self.current_themes = scored_themes
            
            # 1-2. 테마 로테이션 체크 (2주 단위)
            logger.info("\n🔄 Step 1-2: 테마 로테이션 체크")
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
                # 메인 테마가 없으면 설정
                if self.theme_rotator.current_main_theme is None and scored_themes:
                    self.theme_rotator.set_main_theme(
                        scored_themes[0]['theme'],
                        scored_themes[0]['score']
                    )
            
            # 상위 테마 선정
            themes = select_top_themes(scored_themes, count=5)
            logger.info(f"   선정 테마: {len(themes)}개")
            
            if not themes:
                logger.warning("선정된 테마가 없습니다")
                return {"success": False, "reason": "테마 없음"}
            
            # 2. 종목 스크리닝
            logger.info("\n📈 Step 2: 종목 스크리닝")
            candidates = await asyncio.to_thread(
                run_daily_screening,
                themes=themes
            )
            logger.info(f"   후보 종목: {len(candidates)}개")
            
            if not candidates:
                logger.warning("후보 종목이 없습니다")
                return {"success": False, "reason": "후보 종목 없음"}
            
            # 3. AI 검증
            logger.info("\n🤖 Step 3: AI 검증")
            verified = await asyncio.to_thread(
                run_daily_verification,
                candidates=candidates,
                save_to_db=True,
                use_mock_data=self.test_mode
            )
            logger.info(f"   검증 통과: {len(verified)}개")
            
            if not verified:
                logger.warning("AI 검증 통과 종목이 없습니다")
                return {"success": False, "reason": "AI 검증 통과 없음"}
            
            # 4. 후보 풀 선정 (관찰용, 기존보다 더 많이)
            logger.info("\n📋 Step 4: 관찰 후보 선정")
            
            # 설정된 후보 풀 크기 (기본 15개)
            candidate_pool_size = settings.CANDIDATE_POOL_SIZE
            
            # 현재 잔고 확인
            balance = self.trading_engine.get_balance()
            available_cash = balance.get("cash", settings.TOTAL_CAPITAL)
            
            # 포트폴리오 최적화 (후보 풀)
            optimization_result = await asyncio.to_thread(
                run_daily_optimization,
                verified_stocks=verified[:candidate_pool_size],  # 상위 15개
                capital=available_cash,
                strategy="score_based",
                save_to_db=False,  # 아직 저장 안함
                use_mock_data=self.test_mode
            )
            
            # 후보 저장 (09:00 관찰용)
            self.today_candidates = optimization_result["orders"]
            self.today_portfolio = optimization_result["portfolio"]
            
            # 소요 시간
            elapsed = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"\n✅ 일일 분석 완료 (소요 시간: {elapsed:.1f}초)")
            logger.info(f"   관찰 후보: {len(self.today_candidates)}개")
            logger.info("   └─ 09:00 장 시작 후 실시간 관찰 예정")
            logger.info("   └─ 09:25 필터링 후 최종 매수 실행")
            
            # 후보 목록 출력
            logger.info("\n📋 관찰 대상 종목:")
            for i, order in enumerate(self.today_candidates[:10], 1):
                logger.info(f"   {i}. {order.get('stock_name', order.get('code'))}")
            
            # 알림 발송
            if self.notifier:
                self.notifier.send_message(
                    f"📋 08:30 분석 완료\n"
                    f"- 관찰 후보: {len(self.today_candidates)}개\n"
                    f"- 09:00 장 초반 관찰 시작\n"
                    f"- 09:25 필터링 후 매수 예정"
                )
            
            return {
                "success": True,
                "themes": len(themes),
                "candidates": len(candidates),
                "verified": len(verified),
                "observation_pool": len(self.today_candidates),
                "elapsed": elapsed
            }
            
        except Exception as e:
            logger.error(f"일일 분석 실패: {e}")
            self.notifier.send_error_alert("일일 분석", str(e))
            return {"success": False, "error": str(e)}
    
    # ===== 장 초반 관찰 =====
    
    async def run_morning_observation(self) -> dict:
        """
        장 초반 관찰 실행 (09:00)
        
        후보 종목에 대해 실시간 데이터를 수집합니다.
        실제 필터링은 09:25 매수 시점에 수행됩니다.
        
        Returns:
            관찰 결과
        """
        logger.info("=" * 70)
        logger.info("👀 장 초반 관찰 시작 (09:00)")
        logger.info("=" * 70)
        
        if not self.today_candidates:
            logger.warning("관찰할 후보 종목이 없습니다")
            return {"success": False, "reason": "후보 없음"}
        
        logger.info(f"   관찰 대상: {len(self.today_candidates)}개")
        logger.info("   모니터링 항목:")
        logger.info("     - 시초가 갭 (전일 종가 대비)")
        logger.info("     - 당일 수급 (외국인/기관)")
        logger.info("     - 거래량 추이")
        logger.info("")
        logger.info("   09:25까지 대기 후 필터링 실행...")
        
        # 알림
        if self.notifier:
            self.notifier.send_message(
                f"👀 09:00 장 초반 관찰 시작\n"
                f"- 관찰 대상: {len(self.today_candidates)}개\n"
                f"- 09:25 필터링 후 매수 예정"
            )
        
        return {
            "success": True,
            "candidates": len(self.today_candidates)
        }
    
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
        
        # === 장 초반 필터링 실행 ===
        logger.info(f"\n📊 장 초반 필터링 시작 (후보 {len(self.today_candidates)}개)")
        
        if settings.ENABLE_MORNING_FILTER:
            # 필터링 실행
            filter_result = await asyncio.to_thread(
                self.morning_screener.filter_candidates,
                self.today_candidates,
                settings.MORNING_OBSERVATION_MINUTES
            )
            
            if not filter_result.passed_stocks:
                logger.warning("필터링 통과 종목이 없습니다")
                self.notifier.send_message(
                    "⚠️ 09:25 매수 취소\n"
                    f"- 필터링 통과 종목 없음\n"
                    f"- 갭 제외: {filter_result.gap_excluded}개\n"
                    f"- 수급 제외: {filter_result.supply_excluded}개\n"
                    f"- 거래량 제외: {filter_result.volume_excluded}개"
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
            
            # 매수 알림
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
        
        2주 단위로 메인 테마를 재평가합니다.
        점수 -20% 하락 시 즉시 변경됩니다.
        
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
            
            # 오늘 거래
            today_trades = []  # DB에서 조회 가능
            
            # 성과 지표
            calc = PerformanceCalculator()
            metrics = {
                "sharpe_ratio": 0,
                "mdd": 0,
                "win_rate": 0,
                "total_return": 0
            }
            
            # 리포트 전송
            self.notifier.send_daily_report(positions, metrics)
            
            logger.info("✅ 일일 리포트 발송 완료")
            
        except Exception as e:
            logger.error(f"리포트 발송 실패: {e}")
    
    # ===== 수동 실행 =====
    
    async def run_manual_analysis(self) -> dict:
        """수동 분석 실행"""
        logger.info("🔧 수동 분석 모드")
        return await self.run_daily_analysis()


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


async def main():
    """메인 함수"""
    args = parse_args()
    
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


# ===== 엔트리 포인트 =====

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 한국 주식 AI 스윙 트레이딩 시스템")
    print("=" * 70)
    
    asyncio.run(main())
