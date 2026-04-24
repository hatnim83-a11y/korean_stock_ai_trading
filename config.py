"""
config.py - 환경 변수 관리 모듈

이 파일은 시스템의 모든 설정을 관리합니다.
.env 파일에서 민감한 정보(API 키 등)를 로드합니다.

사용법:
    from config import settings
    print(settings.KIS_APP_KEY)
"""

import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


# 프로젝트 루트 디렉토리 경로
PROJECT_ROOT = Path(__file__).parent.absolute()

# ===== KST 타임존 =====
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 한국 시간 반환 (timezone-aware)"""
    return datetime.now(KST)


# ===== 한국 공휴일 =====
try:
    import holidays as _holidays_mod
    _kr_holidays = _holidays_mod.KR()
except ImportError:
    _kr_holidays = None


def is_trading_day(check_date=None) -> bool:
    """한국 증시 거래일 여부 (평일 + 비공휴일)"""
    if check_date is None:
        check_date = now_kst().date()
    if check_date.weekday() >= 5:
        return False
    if _kr_holidays is not None:
        return check_date not in _kr_holidays
    return True  # holidays 미설치 시 평일이면 거래일로 간주


def count_trading_days(from_date, to_date=None) -> int:
    """from_date 다음날부터 to_date까지의 영업일수 (주말/공휴일 제외)

    Args:
        from_date: 시작일 (이 날은 카운트에 포함되지 않음)
        to_date: 종료일 (None이면 오늘 KST)
    """
    if to_date is None:
        to_date = now_kst().date()
    if hasattr(from_date, 'date'):
        from_date = from_date.date()
    if hasattr(to_date, 'date'):
        to_date = to_date.date()
    count = 0
    d = from_date + timedelta(days=1)
    while d <= to_date:
        if is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    return count


_STOCK_CODE_RE = re.compile(r'^\d{6}$')

def validate_stock_code(code: str) -> str:
    """
    종목코드 검증 (6자리 숫자). 유효하지 않으면 ValueError 발생.

    Args:
        code: 종목코드 문자열

    Returns:
        검증된 종목코드 (strip 적용)
    """
    code = str(code).strip()
    if not _STOCK_CODE_RE.match(code):
        raise ValueError(f"유효하지 않은 종목코드: {code!r}")
    return code


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
    
    # ===== Naver Open API (뉴스 검색) =====
    NAVER_CLIENT_ID: str = Field(
        default="",
        description="네이버 Open API Client ID"
    )
    NAVER_CLIENT_SECRET: str = Field(
        default="",
        description="네이버 Open API Client Secret"
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
        default=4_000_000,
        description="총 투자 자본금 (원)"
    )
    MAX_POSITIONS: int = Field(
        default=5,
        description="최대 보유 종목 수"
    )
    MIN_POSITIONS: int = Field(
        default=5,
        description="최소 보유 종목 수 (분산 투자)"
    )
    DAILY_MAX_LOSS: float = Field(
        default=0.03,
        description="일일 최대 손실률 (3% = 0.03, 추가 매매 중단)"
    )

    # ===== 주문 실행 설정 (2026-04-16 공격적 지정가 도입) =====
    ORDER_TYPE_DEFAULT: str = Field(
        default="market",
        description="기본 주문 유형: market(시장가) / limit_aggressive(지정가+매도1호가) / limit(일반지정가). 긴급 롤백은 market으로 복귀"
    )
    LIMIT_AGGRESSIVE_RETRY_TIMEOUT: int = Field(
        default=10,
        description="공격적 지정가: 주문 후 체결 대기 타임아웃 (초)"
    )
    LIMIT_AGGRESSIVE_POLL_INTERVAL: int = Field(
        default=3,
        description="공격적 지정가: 체결 상태 폴링 간격 (초). TIMEOUT 내 ~3회 체크"
    )
    LIMIT_AGGRESSIVE_MAX_RETRIES: int = Field(
        default=12,
        description="공격적 지정가: 최대 재시도 횟수 상한 (TOTAL_TIMEOUT 60s가 먼저 적용되어 실질 최대 6라운드)"
    )
    LIMIT_AGGRESSIVE_TOTAL_TIMEOUT: int = Field(
        default=60,
        description="공격적 지정가: 종목당 총 체결 상한 (초). 5종목 × 60s = 최악 5분 (09:26 모니터링 충돌 방지)"
    )
    LIMIT_AGGRESSIVE_MARGIN_RATIO: float = Field(
        default=1.04,
        description="공격적 지정가 증거금 안전 마진 (실측 1.04배, 4% 여유)"
    )
    LIMIT_AGGRESSIVE_CANCEL_DELAY: float = Field(
        default=0.5,
        description="공격적 지정가: cancel_order 후 상태 재조회까지 대기 (초)"
    )
    LIMIT_AGGRESSIVE_FALLBACK_PREMIUM: float = Field(
        default=1.005,
        description="호가 조회 실패 시 폴백 가격 배수 (expected_price × 배수). Phase 5 관찰 후 조정"
    )
    ABNORMAL_RETRY_WARN_THRESHOLD: int = Field(
        default=5,
        description="재시도가 이 횟수 이상이면 WARN 로그 (이상 징후 탐지)"
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
    # ===== 테마/섹터 분산 제한 =====
    MAX_STOCKS_PER_THEME: int = Field(
        default=2,
        description="동일 테마 최대 보유 종목 수 (3월 동시 손절 방어)"
    )
    MAX_STOCKS_PER_SECTOR: int = Field(
        default=3,
        description="동일 섹터(카테고리) 최대 보유 종목 수"
    )

    # ===== 보유 기간 설정 =====
    MAX_HOLD_DAYS_PROFIT: int = Field(
        default=10,
        description="수익 시 최대 보유 기간 (10영업일)"
    )
    MAX_HOLD_DAYS_LOSS: int = Field(
        default=5,
        description="손실 시 최대 보유 기간 (5영업일)"
    )
    MIN_PROFIT_FOR_LONG_HOLD: float = Field(
        default=0.03,
        description="장기 보유 최소 수익률 (3% 이상)"
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
    # ===== 손절 보호기간 (Grace Period) =====
    GRACE_PERIOD_DAYS: int = Field(
        default=1,
        description="매수 후 보호기간 (hold_days<=N, 당일(0)+다음영업일(1)=2거래일 보호)"
    )
    GRACE_PERIOD_STOP_LOSS: float = Field(
        default=-0.08,
        description="보호기간 중 손절률 (-8%). ATR 손절가 대신 적용"
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
        default=0.20,
        description="2차 익절 시 매도 비율 (20%)"
    )
    PARTIAL_SELL_RATIO_3: float = Field(
        default=0.20,
        description="3차 익절 시 매도 비율 (20%)"
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
    # ----- BE 손절 프리-트레일링 (+5~+8% 방어 공백 제거) -----
    TRAIL_BE_ENABLED: bool = Field(
        default=True,
        description="BE 손절 프리-트레일링 활성화 (+5% 도달 시 매수가 -1% 손절로 상향)"
    )
    TRAIL_BE_ACTIVATE_PCT: float = Field(
        default=0.05,
        description="BE 손절 활성화 수익률 (기본 +5%)"
    )
    TRAIL_BE_STOP_PCT: float = Field(
        default=-0.01,
        description="BE 손절가 오프셋 (기본 매수가 -1%, 슬리피지 완충)"
    )
    TRAIL_ACTIVATION_PCT: float = Field(
        default=0.08,
        description="트레일링 시작 수익률 (+8%)"
    )
    TRAIL_LEVEL1_PCT: float = Field(
        default=0.04,
        description="레벨1 트레일링 (8~15%: 고점 대비 -4%)"
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
    RSI_LOWER_LIMIT: float = Field(
        default=30.0,
        description="RSI 하한선 (과매도)"
    )
    # ===== RSI 동적 조정 (2026-04-24, 제안 #2 Phase A) =====
    RSI_DYNAMIC_ENABLED: bool = Field(
        default=True,
        description="강세장/약세장 RSI 상한 동적 조정 ON/OFF"
    )
    RSI_BULL_THRESHOLD: float = Field(
        default=1.0,
        description="KOSPI 전일 종가 등락률 이 값 이상 → 강세장 판정 (%)"
    )
    RSI_BEAR_THRESHOLD: float = Field(
        default=-1.0,
        description="KOSPI 전일 종가 등락률 이 값 이하 → 약세장 판정 (%)"
    )
    RSI_UPPER_BULL: float = Field(
        default=75.0,
        description="강세장 RSI 상한 (주도주 탈락 방지)"
    )
    RSI_UPPER_NORMAL: float = Field(
        default=70.0,
        description="평시 RSI 상한 (기존 RSI_UPPER_LIMIT와 동일)"
    )
    RSI_UPPER_BEAR: float = Field(
        default=65.0,
        description="약세장 RSI 상한 (보수적 진입)"
    )
    # ===== 테마 슬롯 보장 (2026-04-24, 제안 #3 Phase A) =====
    THEME_MIN_SLOT_ENABLED: bool = Field(
        default=True,
        description="테마당 최소 1개 AI 검증 보장 ON/OFF"
    )
    THEME_SAFETY_FLOOR: float = Field(
        default=25.0,
        description="테마 슬롯 보장 최저 점수 (이 미만이면 보장 없음)"
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
    
    # ===== 주중 테마 교체 설정 =====
    MIDWEEK_REPLACEMENT_ENABLED: bool = Field(
        default=True,
        description="주중 테마 교체 기능 활성화"
    )
    MIDWEEK_MIN_HOLD_DAYS: int = Field(
        default=2,
        description="최소 보유 영업일 (선정 후 이 기간은 교체 불가)"
    )
    MIDWEEK_ABS_FLOOR: float = Field(
        default=38.0,
        description="절대 점수 하한 (B등급 이하, <= 비교)"
    )
    MIDWEEK_RELATIVE_GAP: float = Field(
        default=15.0,
        description="비활성 최상위 후보와의 최소 점수 갭"
    )
    MIDWEEK_CANDIDATE_MIN_SCORE: float = Field(
        default=35.0,
        description="교체 후보 최소 점수"
    )
    MIDWEEK_LOSS_RESCORE_THRESHOLD: float = Field(
        default=50.0,
        description="손실 종목 재평가 통과 기준 (final_score)"
    )

    # ===== 시장 위기 방어 (Market Guard) 설정 =====
    MARKET_GUARD_ENABLED: bool = Field(
        default=True,
        description="시장 위기 방어 기능 ON/OFF"
    )
    MARKET_GUARD_CRISIS_DROP: float = Field(
        default=-2.0,
        description="CRISIS: 코스피 AND 코스닥 동시 이 수치(%) 이하 → 즉시 스킵"
    )
    MARKET_GUARD_DANGER_DROP: float = Field(
        default=-1.0,
        description="DANGER: 양쪽 동시 하락 또는 한쪽 CRISIS급 → 지연 재판단"
    )
    MARKET_GUARD_CAUTION_DROP: float = Field(
        default=-1.0,
        description="CAUTION: 한쪽만 이 수치(%) 이하 → 축소 진입"
    )
    MARKET_GUARD_CAUTION_RATIO: float = Field(
        default=0.7,
        description="CAUTION 시 투자금 비율 (70%)"
    )
    MARKET_GUARD_DELAY_ENABLED: bool = Field(
        default=True,
        description="DANGER 시 지연 재체크 활성화"
    )
    MARKET_GUARD_DELAY_MINUTES: int = Field(
        default=35,
        description="지연 대기 시간(분) — 09:25→10:00"
    )

    # ===== 테마 유동성 검증 설정 =====
    THEME_LIQUIDITY_CHECK_ENABLED: bool = Field(
        default=True,
        description="테마 선정 시 종목 유동성(스크리닝 통과율) 사전 검증 ON/OFF"
    )
    THEME_MIN_PASS_RATE: float = Field(
        default=0.15,
        description="최소 통과율 — 이하면 최대 감점 (15%)"
    )
    THEME_LOW_PASS_RATE: float = Field(
        default=0.25,
        description="저유동성 기준 — 이하면 비례 감점 (25%)"
    )
    THEME_PASS_RATE_PENALTY_MAX: float = Field(
        default=12.0,
        description="통과율 저조 시 최대 감점 (-12점)"
    )
    THEME_PASS_RATE_LOOKBACK_DAYS: int = Field(
        default=7,
        description="통과율 계산 시 참조할 과거 일수"
    )
    THEME_PASS_RATE_MIN_DATA_DAYS: int = Field(
        default=3,
        description="통과율 판단에 필요한 최소 데이터 일수"
    )

    # ===== 테마 로테이션 설정 =====
    THEME_REVIEW_DAYS: int = Field(
        default=7,
        description="테마 재평가 주기 (매주 월요일, 긴급 트리거 시 즉시 변경)"
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

    # ===== 테마 재선정 안전장치 (2026-04-21 회전문 사건 대응) =====
    THEME_MOMENTUM_BOOST_FACTOR: float = Field(
        default=0.7,
        description="화요일 실시간 보강 시 모멘텀 delta → 점수 조정 계수 (기존 하드코딩 1.5)"
    )
    THEME_MOMENTUM_BOOST_CLAMP: float = Field(
        default=8.0,
        description="보강 1회당 총점 변동 상한 (양방향 ±)"
    )
    THEME_ENRICH_TOP_K: int = Field(
        default=30,
        description="화요일 실시간 보강 대상 상위 N개 테마 (기존 15)"
    )
    THEME_DROP_COOLDOWN_ENABLED: bool = Field(
        default=True,
        description="화요일 재선정 시 retention 탈락 테마의 동일 세션 재진입 금지"
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
        default=2.0,
        description="허용 최대 갭하락률 (%) - 초과시 제외 (3월 분석 후 3.0→2.0 강화)"
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
        default=50.0,
        description="최소 체결 강도 (%, 50=중립. 3월 분석 후 45→50 강화)"
    )
    
    # ===== 실시간 관찰 설정 (Observation Loop) =====
    OBSERVATION_INTERVAL_SECONDS: int = Field(
        default=180,
        description="관찰 주기 (초, 3분)"
    )
    OBSERVATION_MAX_CYCLES: int = Field(
        default=6,
        description="최대 관찰 횟수"
    )
    TREND_PRICE_DROP_THRESHOLD: float = Field(
        default=-3.0,
        description="시가 대비 하락 임계값 (%)"
    )
    TREND_PRICE_DOWNTREND_COUNT: int = Field(
        default=3,
        description="연속 하락 횟수 임계값"
    )
    TREND_SUPPLY_REVERSAL_ENABLED: bool = Field(
        default=True,
        description="수급 반전 감지 활성화"
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
    # NOTE: KIS 공식 API는 ws:// 만 제공 (wss:// 미지원, 2026-02 확인)
    # 데이터 자체는 AES256으로 암호화되어 전송됨 (approval_key 기반)
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
