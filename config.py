"""
config.py - 환경 변수 관리 모듈

이 파일은 시스템의 모든 설정을 관리합니다.
.env 파일에서 민감한 정보(API 키 등)를 로드합니다.

사용법:
    from config import settings
    print(settings.KIS_APP_KEY)
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


# 프로젝트 루트 디렉토리 경로
PROJECT_ROOT = Path(__file__).parent.absolute()


class Settings(BaseSettings):
    """
    시스템 설정 클래스
    
    모든 환경 변수를 관리하며, .env 파일에서 자동으로 로드됩니다.
    Pydantic을 사용하여 타입 검증 및 기본값 설정을 수행합니다.
    """
    
    # ===== KIS API (한국투자증권) =====
    KIS_APP_KEY: str = Field(
        default="",
        description="한국투자증권 API 앱 키"
    )
    KIS_APP_SECRET: str = Field(
        default="",
        description="한국투자증권 API 앱 시크릿"
    )
    KIS_ACCOUNT_NO: str = Field(
        default="",
        description="한국투자증권 계좌번호 (예: 12345678-01)"
    )
    KIS_CANO: str = Field(
        default="",
        description="종합계좌번호 (8자리)"
    )
    KIS_ACNT_PRDT_CD: str = Field(
        default="01",
        description="계좌상품코드 (2자리)"
    )
    
    # ===== 모의투자/실전투자 구분 =====
    IS_MOCK: bool = Field(
        default=True,
        description="모의투자 여부 (True: 모의투자, False: 실전투자)"
    )
    
    # ===== Claude API (Anthropic) =====
    ANTHROPIC_API_KEY: str = Field(
        default="",
        description="Anthropic Claude API 키"
    )
    CLAUDE_MODEL: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="사용할 Claude 모델"
    )
    
    # ===== Telegram Bot =====
    TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="텔레그램 봇 토큰"
    )
    TELEGRAM_CHAT_ID: str = Field(
        default="",
        description="텔레그램 채팅 ID"
    )
    
    # ===== DART API (공시 정보) =====
    DART_API_KEY: str = Field(
        default="",
        description="DART 공시 API 키"
    )
    
    # ===== 데이터베이스 =====
    DATABASE_PATH: str = Field(
        default=str(PROJECT_ROOT / "data" / "trading.db"),
        description="SQLite 데이터베이스 파일 경로"
    )
    
    # ===== 로깅 =====
    LOG_LEVEL: str = Field(
        default="INFO",
        description="로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    LOG_PATH: str = Field(
        default=str(PROJECT_ROOT / "logs"),
        description="로그 파일 저장 디렉토리"
    )
    
    # ===== 트레이딩 설정 =====
    TOTAL_CAPITAL: int = Field(
        default=10_000_000,
        description="총 투자 자본금 (원)"
    )
    MAX_POSITIONS: int = Field(
        default=10,
        description="최대 보유 종목 수 (5-10개)"
    )
    MIN_POSITIONS: int = Field(
        default=5,
        description="최소 보유 종목 수 (분산 투자)"
    )
    DAILY_MAX_LOSS: float = Field(
        default=0.03,
        description="일일 최대 손실률 (3% = 0.03, 추가 매매 중단)"
    )
    
    # ===== 포트폴리오 제약 조건 =====
    MIN_POSITION_WEIGHT: float = Field(
        default=0.05,
        description="종목당 최소 투자 비중 (5%)"
    )
    MAX_POSITION_WEIGHT: float = Field(
        default=0.25,
        description="종목당 최대 투자 비중 (25%)"
    )
    MAX_THEME_WEIGHT: float = Field(
        default=0.40,
        description="테마당 최대 투자 비중 (40%)"
    )

    # ===== 보유 기간 설정 =====
    MAX_HOLD_DAYS_PROFIT: int = Field(
        default=14,
        description="수익 시 최대 보유 기간 (14일)"
    )
    MAX_HOLD_DAYS_LOSS: int = Field(
        default=7,
        description="손실 시 최대 보유 기간 (7일)"
    )
    MIN_PROFIT_FOR_LONG_HOLD: float = Field(
        default=0.05,
        description="장기 보유 최소 수익률 (5% 이상)"
    )
    MIN_PROFIT_TO_IGNORE_SUPPLY: float = Field(
        default=0.10,
        description="수급 이탈 무시 최소 수익률 (10% 이상)"
    )
    
    # ===== 손절/익절 설정 =====
    DEFAULT_STOP_LOSS: float = Field(
        default=-0.05,
        description="기본 손절률 (-5%)"
    )
    STOP_LOSS_FAST: float = Field(
        default=-0.07,
        description="빠른 손절률 (-7%, 급락 시)"
    )
    DEFAULT_TAKE_PROFIT: float = Field(
        default=0.15,
        description="기본 익절률 (+15%)"
    )
    
    # ===== 분할 익절 설정 =====
    TAKE_PROFIT_1: float = Field(
        default=0.10,
        description="1차 익절률 (+10%)"
    )
    TAKE_PROFIT_2: float = Field(
        default=0.15,
        description="2차 익절률 (+15%)"
    )
    TAKE_PROFIT_3: float = Field(
        default=0.20,
        description="3차 익절률 (+20%)"
    )
    PARTIAL_SELL_RATIO_1: float = Field(
        default=0.30,
        description="1차 익절 시 매도 비율 (30%)"
    )
    PARTIAL_SELL_RATIO_2: float = Field(
        default=0.30,
        description="2차 익절 시 매도 비율 (30%)"
    )
    
    # ===== 트레일링 스탑 =====
    ENABLE_TRAILING_STOP: bool = Field(
        default=True,
        description="트레일링 스탑 활성화"
    )
    TRAILING_STOP_PERCENT: float = Field(
        default=0.05,
        description="트레일링 스탑 비율 (최고가 대비 -5%)"
    )

    # ===== 이익 추종 전략 (Let Profits Run) =====
    ENABLE_PROFIT_TRAILING: bool = Field(
        default=True,
        description="이익 추종 전략 활성화 (단계별 트레일링)"
    )
    TRAIL_ACTIVATION_PCT: float = Field(
        default=0.08,
        description="트레일링 시작 수익률 (+8%)"
    )
    TRAIL_LEVEL1_PCT: float = Field(
        default=0.05,
        description="레벨1 트레일링 (8~15%: 고점 대비 -5%)"
    )
    TRAIL_LEVEL2_THRESHOLD: float = Field(
        default=0.15,
        description="레벨2 진입 수익률 (+15%)"
    )
    TRAIL_LEVEL2_PCT: float = Field(
        default=0.03,
        description="레벨2 트레일링 (15~25%: 고점 대비 -3%)"
    )
    TRAIL_LEVEL3_THRESHOLD: float = Field(
        default=0.25,
        description="레벨3 진입 수익률 (+25%)"
    )
    TRAIL_LEVEL3_PCT: float = Field(
        default=0.02,
        description="레벨3 트레일링 (25%+: 고점 대비 -2%)"
    )

    ATR_MULTIPLIER: float = Field(
        default=2.0,
        description="ATR 기반 손절 계산 시 배수"
    )
    
    # ===== 스크리닝 조건 =====
    MIN_TRADING_VALUE: int = Field(
        default=5_000_000_000,
        description="최소 거래대금 (50억원)"
    )
    RSI_UPPER_LIMIT: float = Field(
        default=75.0,
        description="RSI 상한선 (과열 방지)"
    )
    RSI_LOWER_LIMIT: float = Field(
        default=30.0,
        description="RSI 하한선 (과매도)"
    )
    VOLUME_RATIO_MIN: float = Field(
        default=1.2,
        description="거래량 비율 하한 (20일 평균 대비)"
    )
    MAX_DEBT_RATIO: float = Field(
        default=200.0,
        description="최대 부채비율 (%)"
    )
    
    # ===== API 호출 제한 =====
    KIS_API_DELAY: float = Field(
        default=0.11,
        description="KIS API 호출 간 대기 시간 (초)"
    )
    CLAUDE_CONCURRENT_LIMIT: int = Field(
        default=5,
        description="Claude API 동시 호출 제한"
    )
    
    # ===== 테마 선정 =====
    TOP_THEME_COUNT: int = Field(
        default=5,
        description="선정할 상위 테마 수"
    )
    MIN_THEME_STOCK_COUNT: int = Field(
        default=8,
        description="테마 최소 종목 수 (8개 이상)"
    )
    MIN_THEME_AVG_MARKET_CAP: int = Field(
        default=100_000_000_000,
        description="테마 평균 시가총액 최소 기준 (1000억원)"
    )
    
    # ===== 테마 로테이션 설정 =====
    THEME_REVIEW_DAYS: int = Field(
        default=7,
        description="메인 테마 재평가 주기 (7일, 14일 대비 +75% 수익)"
    )
    THEME_CHANGE_THRESHOLD: float = Field(
        default=-0.20,
        description="테마 점수 하락 임계값 (-20%, 즉시 변경)"
    )
    THEME_SURGE_THRESHOLD: float = Field(
        default=0.15,
        description="테마 급등 임계값 (+15%, 즉시 진입)"
    )
    
    THEME_BLACKLIST: list = Field(
        default=[
            "마리화나", "대마", "낙태", "피임",
            "카지노", "도박", "경마", "복권",
            "겨울", "여름", "태풍", "장마", "폭염", "한파",
            "日제품", "트럼프", "러시아", "북한",
            "담배", "주류업", "소주", "맥주"
        ],
        description="제외할 테마 목록 (블랙리스트)"
    )
    
    # ===== 장 초반 관찰 설정 (Morning Filter) =====
    ENABLE_MORNING_FILTER: bool = Field(
        default=True,
        description="장 초반 관찰 필터 활성화 여부"
    )
    MORNING_OBSERVATION_MINUTES: int = Field(
        default=20,
        description="장 시작 후 관찰 시간 (분)"
    )
    CANDIDATE_POOL_SIZE: int = Field(
        default=15,
        description="사전 분석 후보 종목 수 (관찰용)"
    )
    
    # 시초가 갭 필터
    MAX_GAP_UP_PERCENT: float = Field(
        default=3.0,
        description="허용 최대 갭상승률 (%) - 초과시 제외"
    )
    MAX_GAP_DOWN_PERCENT: float = Field(
        default=3.0,
        description="허용 최대 갭하락률 (%) - 초과시 제외"
    )
    ENABLE_DYNAMIC_GAP: bool = Field(
        default=True,
        description="동적 갭 기준 활성화 (시장 상황에 따라 자동 조정)"
    )
    
    # 당일 수급 필터
    MIN_MORNING_NET_BUY: int = Field(
        default=0,
        description="최소 당일 순매수 금액 (원) - 0 이상이면 매수세"
    )
    REQUIRE_FOREIGN_BUY: bool = Field(
        default=False,
        description="외국인 순매수 필수 여부"
    )
    REQUIRE_INSTITUTION_BUY: bool = Field(
        default=False,
        description="기관 순매수 필수 여부"
    )
    
    # 거래량 필터
    MIN_VOLUME_RATIO: float = Field(
        default=0.5,
        description="최소 거래량 비율 (20일 평균 대비) - 0.5 = 50%"
    )
    
    # 체결 강도 필터
    ENABLE_STRENGTH_FILTER: bool = Field(
        default=True,
        description="체결 강도 필터 활성화 여부"
    )
    MIN_STRENGTH: float = Field(
        default=45.0,
        description="최소 체결 강도 (%, 50=중립, 45=약간 매도우위도 허용)"
    )
    
    # ===== 스케줄 시간 =====
    SCHEDULE_THEME_ANALYSIS: str = Field(
        default="08:30",
        description="테마 분석 시작 시간"
    )
    SCHEDULE_STOCK_SCREENING: str = Field(
        default="08:35",
        description="수급 스크리닝 시작 시간"
    )
    SCHEDULE_AI_VERIFICATION: str = Field(
        default="08:40",
        description="AI 검증 시작 시간"
    )
    SCHEDULE_PORTFOLIO_OPTIMIZATION: str = Field(
        default="08:50",
        description="포트폴리오 최적화 시작 시간"
    )
    SCHEDULE_AUTO_BUY: str = Field(
        default="09:00",
        description="자동 매수 실행 시간"
    )
    SCHEDULE_DAILY_REPORT: str = Field(
        default="15:30",
        description="일일 리포트 생성 시간"
    )
    
    class Config:
        """Pydantic 설정"""
        # .env 파일 경로 설정
        env_file = str(PROJECT_ROOT / ".env")
        env_file_encoding = "utf-8"
        # 대소문자 구분 안 함
        case_sensitive = False
        # 추가 필드 허용
        extra = "allow"


def get_kis_base_url(is_mock: bool = True) -> str:
    """
    KIS API 기본 URL 반환
    
    Args:
        is_mock: 모의투자 여부 (True: 모의투자, False: 실전투자)
    
    Returns:
        KIS API 기본 URL
        
    Example:
        >>> get_kis_base_url(is_mock=True)
        'https://openapivts.koreainvestment.com:29443'
        
        >>> get_kis_base_url(is_mock=False)
        'https://openapi.koreainvestment.com:9443'
    """
    if is_mock:
        return "https://openapivts.koreainvestment.com:29443"
    else:
        return "https://openapi.koreainvestment.com:9443"


def get_kis_websocket_url(is_mock: bool = True) -> str:
    """
    KIS WebSocket URL 반환
    
    Args:
        is_mock: 모의투자 여부
    
    Returns:
        KIS WebSocket URL
    """
    if is_mock:
        return "ws://ops.koreainvestment.com:31000"
    else:
        return "ws://ops.koreainvestment.com:21000"


# ===== 설정 싱글톤 인스턴스 =====
# 다른 모듈에서 'from config import settings'로 사용
settings = Settings()


# ===== 디버깅용 출력 함수 =====
def print_settings():
    """
    현재 설정 값 출력 (디버깅용)
    
    주의: API 키 등 민감한 정보는 마스킹 처리됨
    """
    print("=" * 50)
    print("📋 현재 시스템 설정")
    print("=" * 50)
    print(f"모의투자 모드: {settings.IS_MOCK}")
    print(f"KIS API URL: {get_kis_base_url(settings.IS_MOCK)}")
    print(f"KIS APP KEY: {'*' * 8 + settings.KIS_APP_KEY[-4:] if len(settings.KIS_APP_KEY) > 4 else '(미설정)'}")
    print(f"계좌번호: {settings.KIS_ACCOUNT_NO or '(미설정)'}")
    print(f"Claude 모델: {settings.CLAUDE_MODEL}")
    print(f"총 자본금: {settings.TOTAL_CAPITAL:,}원")
    print(f"최대 포지션: {settings.MAX_POSITIONS}개")
    print(f"DB 경로: {settings.DATABASE_PATH}")
    print(f"로그 경로: {settings.LOG_PATH}")
    print(f"로그 레벨: {settings.LOG_LEVEL}")
    print("=" * 50)


# 직접 실행 시 설정 확인
if __name__ == "__main__":
    print_settings()
