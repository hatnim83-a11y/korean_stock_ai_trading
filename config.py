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
from pydantic import Field, computed_field
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


def previous_trading_day(ref_date=None):
    """ref_date 직전의 거래일(영업일)을 반환 (주말/공휴일 walk-back).

    예: 월요일 입력 → 금요일, 수요일(휴장 가정) 입력 → 화요일.
    종가베팅 청산 잡이 "직전 거래일 진입분"을 정확히 조회하기 위함
    (달력 어제만 쓰면 금/휴장 직전 진입분이 영구 누락됨).

    Args:
        ref_date: 기준일 (None이면 오늘 KST). datetime 전달 시 .date()로 정규화.
    Returns:
        date: ref_date 직전의 첫 거래일
    """
    if ref_date is None:
        ref_date = now_kst().date()
    elif hasattr(ref_date, 'date'):
        ref_date = ref_date.date()
    d = ref_date - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


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
    SELL_SLIPPAGE_WARN_THRESHOLD: float = Field(
        default=2.0,
        description="매도 슬리피지 절댓값(%)이 이 값 초과면 WARN 로그 (갭다운/유동성 부족 징후)"
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
    THEME_SLOT_RELAXATION_ENABLED: bool = Field(
        default=True,
        description="빈 슬롯 + 후보 부족 시 테마 한도 일시 상향 ON/OFF"
    )
    MAX_STOCKS_PER_THEME_RELAXED: int = Field(
        default=3,
        description="2차 패스 테마 한도 (1차 후 빈 슬롯 남고 후보 모자랄 때만 적용)"
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
    
    # ===== 분할 익절 설정 (v17: avg_buy_price 기준으로 트리거, 임계/비율 상향) =====
    TAKE_PROFIT_1: float = Field(
        default=0.12,
        description="1차 익절률 (+12%, avg_buy_price 기준 — v17 +10→+12 상향, 수익 끌기)"
    )
    TAKE_PROFIT_2: float = Field(
        default=0.20,
        description="2차 익절률 (+20%, avg_buy_price 기준 — v17 +15→+20 상향)"
    )
    TAKE_PROFIT_3: float = Field(
        default=0.30,
        description="3차 익절률 (+30%, avg_buy_price 기준 — v17 +20→+30 상향)"
    )
    PARTIAL_SELL_RATIO_1: float = Field(
        default=0.25,
        description="1차 익절 시 매도 비율 (25% — v17 30→25 축소, 잔여 trend로)"
    )
    PARTIAL_SELL_RATIO_2: float = Field(
        default=0.20,
        description="2차 익절 시 매도 비율 (20%)"
    )
    PARTIAL_SELL_RATIO_3: float = Field(
        default=0.20,
        description="3차 익절 시 매도 비율 (20%, 잔여 35%)"
    )
    PARTIAL_PROFIT_BASE: str = Field(
        default="avg",
        description=(
            "분할 익절 트리거 기준 ('avg' 또는 'first'). "
            "'avg'=평균단가 기준(권장, v17 기본), 'first'=1차 진입가 기준(롤백용). "
            "손절/BE/트레일링/2차 트리거는 항상 first_buy_price 기준 (avg 정책 분기)"
        )
    )
    PARTIAL_PROFIT_EARLY_MONITORING_ENABLED: bool = Field(
        default=True,
        description="09:00 조기 모니터링 + SellLock 활성화 (False=09:26 legacy)"
    )
    # ----- 누락 종목 자동 편입 스윕 (2026-06-10 매수↔모니터 재시작 레이스 심층 방어) -----
    MONITOR_MISSING_SWEEP_ENABLED: bool = Field(
        default=True,
        description=(
            "모니터 루프에서 'DB holding ∖ 메모리 포지션' 차집합을 주기 탐지해 "
            "지각 체결/누락 종목을 자동 편입(load_positions_from_db 재호출). "
            "어떤 경로로든 누락이 생겨도 다음 사이클에 복구되며 텔레그램 1회 경보. "
            "False=스윕 미수행(롤백, 재시작 체이닝+cron 안전망만 의존)"
        )
    )
    MONITOR_MISSING_SWEEP_INTERVAL_SEC: int = Field(
        default=60,
        description="누락 종목 스윕 주기(초). 모니터 루프에서 이 간격마다 차집합 탐지"
    )

    # ===== 분할 진입 + 불타기 (v17 Tranche Entry + Pyramid-In) =====
    TRANCHE_ENTRY_ENABLED: bool = Field(
        default=False,
        description=(
            "분할 진입 + 불타기 마스터 토글. "
            "True=1차 50% 매수 후 +5% 도달 시 2차 50% 추가 진입. "
            "False=기존 단일 진입 100% (롤백). "
            "⚠️ default=False (안전 default). 사용자 명시 승인 + 1주 dry-run 후 활성화 권장"
        )
    )
    TRANCHE_FIRST_RATIO: float = Field(
        default=0.5,
        description="1차 진입 비율 (50% — 종목당 자금 중 1차에 사용)"
    )
    MANUAL_BUY_ENABLED: bool = Field(
        default=True,
        description=(
            "텔레그램 /buy 수동 매수 명령어 마스터 토글. "
            "True=사용자가 /buy [종목코드]로 슬롯에 직접 진입 가능(분할진입 룰+시장가, "
            "이후 기존 모니터링으로 자동 청산, 테마 로테이션 매도는 제외). "
            "False=/buy 명령 비활성화(롤백)."
        )
    )
    MANUAL_BUY_CUTOFF: str = Field(
        default="15:10",
        description=(
            "수동 매수(/buy) 마감 시각 (HH:MM, KST). 이 시각 이후 /buy 거부 — "
            "종가베팅 자본 보호 구간(15:10 pipeline 준비) 및 v17 2차 진입 시간 가드(15:15)와 정합."
        )
    )
    TRANCHE_SECOND_RATIO: float = Field(
        default=0.5,
        description="2차 진입(불타기) 비율 (50% — +5% 도달 시 추가 매수)"
    )
    PYRAMID_TRIGGER_PCT: float = Field(
        default=0.05,
        description=(
            "2차 진입(불타기) 트리거 수익률 (+5%, first_buy_price 기준). "
            "BE 손절 활성화(+5%)와 동기화 — 1차분 보호 확보 후 2차 진입"
        )
    )
    PYRAMID_MAX_WAIT_DAYS: int = Field(
        default=10,
        description="2차 진입 대기 최대 영업일 (보유기간과 동일, 1차 매도 시 자동 취소)"
    )
    TRANCHE_SECOND_AMOUNT_MODE: str = Field(
        default="mirror_first",
        description=(
            "2차 진입 금액 산정 모드. "
            "'mirror_first'=1차 매수금 그대로(권장, 균등 진입), "
            "'orderable_cash'=KIS 현재가용 현금 기준 재산정"
        )
    )
    DRY_RUN_PYRAMID: bool = Field(
        default=True,
        description=(
            "2차 진입 dry-run 모드. True=KIS 실발주 차단(시뮬레이션만, 로그/알림만 발화). "
            "⚠️ default=True (안전 default). 1주 검증 후 False 전환. "
            "TRANCHE_ENTRY_ENABLED=True 상태에서만 의미 있음(1차 진입은 영향 X)"
        )
    )

    # ===== ATR 기반 트레일링 (v17 — max(고정값, 2.0×ATR)) =====
    TRAILING_USE_ATR: bool = Field(
        default=True,
        description=(
            "ATR 기반 트레일링 활성화. "
            "True=trailing_pct = max(TRAIL_LEVELn_PCT, ATR_MULTIPLIER × atr_at_buy / first_buy_price). "
            "False=고정값만 사용 (롤백)"
        )
    )
    ATR_PERIOD: int = Field(
        default=14,
        description="ATR 계산 기간 (전통적 표준 14일, True Range 14일 평균)"
    )
    TRAILING_ATR_CAP_PCT: float = Field(
        default=0.08,
        description=(
            "ATR 트레일링 폭 상한(cap). effective_pct = max(고정, min(ATR항, cap)). "
            "고ATR 종목에서 트레일링 폭이 폭발(예 22.5%)해 손절처럼 작동하는 것을 차단. "
            "0=상한 없음(롤백). 고정 레벨 최대값(L1=0.04)보다 크게 설정해야 하한이 무력화되지 않음. "
            "2026-06-16 피에스케이홀딩스 사건 대응 (focus:trailing 제안서)"
        )
    )
    TRAIL_BE_FLOOR_ENABLED: bool = Field(
        default=True,
        description=(
            "트레일링 활성 시에도 BE/stop_loss를 바닥으로 보존. "
            "True=trailing_stop이 stop_loss_price보다 낮으면 손절 체크를 양보하지 않고 BE 손절 진행. "
            "False=기존 무조건 양보(롤백). ATR cap(TRAILING_ATR_CAP_PCT)과 독립 토글."
        )
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
    # ----- 매도 실패 무한 루프 차단 (2026-05-18 Phase 2) -----
    MAX_SELL_FAILURES: int = Field(
        default=3,
        description=(
            "매도 진입점에서 같은 종목 연속 실패 허용 횟수. "
            "초과 시 모니터링 메모리에서 강제 제거 + 텔레그램 1회 알림 → "
            "monitor_state.json 잔재/KIS 잔량 불일치로 인한 무한 매도 실패 도배 봉쇄"
        )
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
        description=(
            "ATR 배수 (트레일링 폭 계산 + 기존 손절 계산 공용). "
            "v17 트레일링: effective_pct = max(고정값, ATR_MULTIPLIER × atr_at_buy / first_buy_price)"
        )
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
    THEME_MAKEUP_RESELECTION_ENABLED: bool = Field(
        default=True,
        description="화요일이 휴장(공휴일/주말)이면 다음 첫 영업일에 1회 보정 재선정 발화"
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
        default=3.5,
        description="허용 최대 갭상승률 (%) - 초과시 제외 (2026-06-02 분석: 3.0~3.5% 구간 과차단 회수, 3.0→3.5)"
    )
    MAX_GAP_DOWN_PERCENT: float = Field(
        default=2.0,
        description="허용 최대 갭하락률 (%) - 초과시 제외 (3월 분석 후 3.0→2.0 강화)"
    )
    ENABLE_DYNAMIC_GAP: bool = Field(
        default=True,
        description="동적 갭 기준 활성화 (시장 상황에 따라 자동 조정)"
    )
    GAP_REGIME_PER_MARKET: bool = Field(
        default=False,
        description="갭 regime 판정을 종목 소속시장(코스피/코스닥) 개별 지수로 (2026-06-02 분석). False=두 지수 평균(기존)"
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

    # ===== 아침 매수 조기 진입 (A3안, EARLY_BUY) =====
    # 분봉 시뮬: 09:05 진입 +1.91% vs 09:25 -2.81% (격차 +4.72%p). GS리테일(5/8) +18.75% vs -5% 손절.
    # v17 분할진입(1차 50%)이 1차 진입 리스크 분산 → 공격적 진입 정당화.
    # 마스터 토글 1개 + 보조 4개로 시간 변경 + observer 미생성 + 거래량 OFF + Market Guard 단축 자동 연동.
    EARLY_BUY_ENABLED: bool = Field(
        default=False,
        description=(
            "아침 매수 09:05 조기 진입 마스터 토글. "
            "True=스케줄 09:00/01/02/02/05/06 재배치 + observer 미생성 + 거래량 필터 OFF + Market Guard 지연 단축. "
            "False=09:25 legacy 100%. ⚠️ default=False (안전). 1주 dry-run 후 활성화 권장."
        )
    )
    EARLY_BUY_OBSERVATION_MINUTES: int = Field(
        default=0,
        description="EARLY_BUY_ENABLED=True 시 사용할 관찰 시간 (0=관찰 task 미생성 + Phase 0 즉시 통과)"
    )
    EARLY_BUY_DISABLE_VOLUME_FILTER: bool = Field(
        default=True,
        description=(
            "09:05 매수 시 거래량 필터 비활성. "
            "5분 데이터에서 expected_volume = avg × (5/390) = 1.28% 라 90%+ 탈락 위험."
        )
    )
    EARLY_BUY_DISABLE_STRENGTH_FILTER: bool = Field(
        default=False,
        description=(
            "09:05 매수 시 체결강도 필터 비활성. "
            "default=False (체결강도는 5분치도 의미 있어 보존 — strategy-planner 권고)."
        )
    )
    EARLY_BUY_MARKET_GUARD_DELAY_MINUTES: int = Field(
        default=15,
        description=(
            "EARLY_BUY_ENABLED=True + Market Guard DANGER 시 지연(분). "
            "기존 35분(legacy) → 09:05+15=09:20 재체크로 단축, A3안 의미 보존."
        )
    )

    # ===== 시간 문자열 property (메시지/로그 변수화용) =====
    # EARLY_BUY_ENABLED 에 따라 09:05/09:25 자동 결정. 호출 측에서 settings.buy_time_str 등 사용.
    @computed_field
    @property
    def buy_time_str(self) -> str:
        return "09:05" if self.EARLY_BUY_ENABLED else "09:25"

    @computed_field
    @property
    def screening_time_str(self) -> str:
        return "09:02" if self.EARLY_BUY_ENABLED else "09:05"

    @computed_field
    @property
    def monitoring_time_str(self) -> str:
        # early_buy 시 실제 잡은 09:10 (scheduler.py monitoring_minute=10) — 매수 완료 여유
        # (2026-06-10 매수↔모니터 재시작 레이스: 09:06→09:10 후행, 표시 문자열도 정합)
        return "09:10" if self.EARLY_BUY_ENABLED else "09:26"

    @computed_field
    @property
    def hold_period_time_str(self) -> str:
        return "09:02" if self.EARLY_BUY_ENABLED else "09:15"

    @computed_field
    @property
    def midweek_loss_time_str(self) -> str:
        return "09:01" if self.EARLY_BUY_ENABLED else "09:10"

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

    # ===== 외국인/기관 수급 신호 도입 (v16, supply-signal-integration Phase 1-A) =====
    # 종가베팅 시스템이 검증한 외국인 수급 데이터 소스(HHPTJ04160200, FHPTJ04400000, 네이버 frgn.naver)를
    # 메인 시스템에 이식. T-1 마감 데이터를 17:10에 1회 수집하여 다음날 아침 KIS 재호출 없이 사용.
    SUPPLY_SIGNAL_ENABLED: bool = Field(
        default=True,
        description="외국인/기관 수급 신호 활성화 (False면 17:10 잡 미등록 + AI 프롬프트 미사용)"
    )
    SUPPLY_COLLECT_HOUR: int = Field(
        default=17,
        description="수급 수집 시각 시 (KST)"
    )
    SUPPLY_COLLECT_MINUTE: int = Field(
        default=10,
        description="수급 수집 시각 분 (KST). 17:05 일별테마수집 직후, 종가베팅 19:27 잡과 간격 확보"
    )
    SUPPLY_UNIVERSE_TOP_MARKET_CAP: int = Field(
        default=200,
        description="수급 수집 시총 상위 종목 수 (0이면 테마+보유만, 약 200종목 합집합 시 22초 소요)"
    )
    SUPPLY_RANKING_TOP_N: int = Field(
        default=200,
        description="FHPTJ04400000 외국인/기관 순매수 TOP 수집 개수"
    )
    SUPPLY_THEME_TOP_K: int = Field(
        default=10,
        description="수급 수집 시 universe에 포함할 테마 상위 K개"
    )
    SUPPLY_STOCK_LOOKBACK_DAYS: int = Field(
        default=7,
        description="테마 종목 후보 룩백 일수 (stocks/screening_log JOIN)"
    )
    SUPPLY_RANKING_CALL_SLEEP_SEC: float = Field(
        default=0.3,
        description="FHPTJ04400000 외인/기관 호출 사이 추가 sleep (rate limit 보호)"
    )
    SUPPLY_RETRY_JOB_HOUR: int = Field(
        default=18,
        description="17:10 잡 전체 실패 시 자동 재시도 잡 시 (0이면 미등록)"
    )
    SUPPLY_RETRY_JOB_MINUTE: int = Field(
        default=0,
        description="17:10 잡 전체 실패 시 자동 재시도 잡 분"
    )

    # ===== Phase 1-B½/1-C 수급 점수 활성화 토글 =====
    SUPPLY_SCORE_OBSERVE_ONLY: bool = Field(
        default=True,
        description="Phase 1-B½ Shadow Run — True면 supply_score_v2 계산하되 테마 총점에 미반영 (관측만, supply_score_observation 테이블 저장). Phase 1-C 진입 시 False로 전환"
    )
    SUPPLY_SCORE_MAX: float = Field(
        default=0.0,
        description="테마 점수 가산 최대값 (점수). Phase 1-B½에서는 0.0 (총점 미반영), Phase 1-C Day 20 2.5 → Day 21 5.0 단계 상향"
    )
    SUPPLY_SCORE_TOP_N: int = Field(
        default=5,
        description="테마당 supply_score_v2 계산에 사용할 상위 N개 종목 (외인 5일 누적 절댓값 기준)"
    )
    SUPPLY_INTENSITY_REF_BIL: float = Field(
        default=10.0,
        description="supply_score_v2 정규화 기준액 (억원). SIGNED=True 시 ±이 값 범위로 양선형 매핑 (avg=0→max/2, avg=+ref→max, avg=-ref→0). 2026-06-06 Shadow Run 분포 분석 결과 30→10 하향 (실측 양수 외인 net 다수가 +5~+60억 범위)"
    )
    SUPPLY_SCORE_SIGNED: bool = Field(
        default=True,
        description="True면 음수 외인 net에도 점수 부여 (양선형 매핑, avg=0→max/2). False면 기존 동작 (음수→0, 양수만 비례). 2026-06-06 추가 — Shadow Run 분포 차별성 개선용"
    )
    SUPPLY_OUTLIER_CAP_BIL: float = Field(
        default=100.0,
        description="theme_avg_net_bil outlier cap (억원). use_ratio=False(absolute 모드)에서만 의미. 2026-06-15 결과: 대형주 -7.78조원이 outlier가 아닌 정상 데이터로 확인 → ratio 모드 도입으로 절대값 cap 의존성 제거"
    )
    SUPPLY_USE_RATIO: bool = Field(
        default=True,
        description="2026-06-15 추가 — True면 종목 거래대금 대비 비율(foreign_net_5d / (trade_value_5d_avg × 5))로 정규화. 대형주(삼성전자 등)와 중소형주를 동일 척도로 비교 가능. False면 절대값 기반 (SUPPLY_INTENSITY_REF_BIL 사용)"
    )
    SUPPLY_REF_RATIO: float = Field(
        default=0.15,
        description="use_ratio=True일 때 정규화 기준 비율. 5일 거래대금의 ±이 비율에 도달하면 만점/0점 매핑 (signed 양선형). 2026-06-15 6/10~6/12 실측 분포(-30%~+30% 정규분포) 기반 15% 채택"
    )
    SUPPLY_STRENGTH_ENABLED: bool = Field(
        default=False,
        description="Phase 1-C 종목 필터(filters.py) supply_strength 키 가산 활성화. Phase 1-B½에서는 False"
    )
    SUPPLY_UNIVERSE_TOP_N: int = Field(
        default=30,
        description="universe 내부 외인 동적 TOP 측정 시 상위 N개 종목 (KIS TOP30 대비 비교용, Phase 1-C 결정에 사용)"
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

    # ===== Off-VM Dead-man's-switch (2026-06-05 incident P0-A) =====
    # 봇이 외부 헬스체크(healthchecks.io 스타일)에 주기 ping → 봇/VM 다운 시 외부에서 무신호 감지·경보
    HEALTHCHECK_ENABLED: bool = Field(
        default=False,
        description="외부 헬스체크 ping 활성화 (opt-in). True+URL 설정 시 interval 잡 등록"
    )
    HEALTHCHECK_PING_URL: str = Field(
        default="",
        description="헬스체크 ping URL (예: https://hc-ping.com/<uuid>). 시크릿 취급 — .env 관리"
    )
    HEALTHCHECK_PING_INTERVAL_MIN: int = Field(
        default=5,
        description="ping 주기(분). 외부 서비스 grace는 interval+여유로 설정 권장"
    )
    HEALTHCHECK_PING_TIMEOUT_SEC: int = Field(
        default=5,
        description="ping HTTP 타임아웃(초). 트레이딩 무간섭 위해 짧게 유지"
    )

    # ===== 누락 핵심 잡 탐지 + 장중 비정상 재시작 경보 (2026-06-05 incident P0-B) =====
    JOB_RECOVERY_ALERT_ENABLED: bool = Field(
        default=True,
        description="EVENT_JOB_MISSED 핵심 잡 누락 경보 + 장중 비정상 재시작 경보 (순수 관측/알림, 트레이딩 무개입)"
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


def is_makeup_reselection_day(today, target_weekday: int = 1):
    """
    오늘이 '직전 target_weekday(기본 화요일) 휴장 이후 첫 영업일'인지 판정.

    화요일이 공휴일/주말이라 정규 테마 재선정이 스킵됐을 때, 다음 첫 영업일에
    1회 보정 재선정을 발화시키기 위한 순수 헬퍼 함수.

    Args:
        today: 판정할 날짜 (date)
        target_weekday: 정규 재선정 요일 (기본 1=화요일, weekday() 기준)

    Returns:
        (is_makeup, missed_date) — 보정일 여부와 직전 누락된 target_weekday 날짜
    """
    if not settings.THEME_MAKEUP_RESELECTION_ENABLED:
        return (False, None)
    if not is_trading_day(today):
        return (False, None)
    delta = (today.weekday() - target_weekday) % 7 or 7
    missed = today - timedelta(days=delta)
    if is_trading_day(missed):
        return (False, None)
    cursor = missed + timedelta(days=1)
    while cursor < today:
        if is_trading_day(cursor):
            return (False, None)
        cursor += timedelta(days=1)
    return (True, missed)


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
