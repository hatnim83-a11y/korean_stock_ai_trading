"""
logger.py - 로깅 시스템 설정 모듈

이 파일은 loguru를 사용하여 구조화된 로깅을 제공합니다.
파일/콘솔 로깅, 로그 로테이션, 에러 알림 등을 관리합니다.

사용법:
    from logger import logger
    
    logger.info("일반 정보 메시지")
    logger.debug("디버그 메시지")
    logger.warning("경고 메시지")
    logger.error("에러 메시지")
    logger.critical("심각한 에러 메시지")
"""

import sys
from pathlib import Path
from loguru import logger

# 기본 설정 제거 (loguru의 기본 stderr 출력 제거)
logger.remove()


def setup_logger(
    log_level: str = "INFO",
    log_path: str = "logs",
    enable_console: bool = True,
    enable_file: bool = True
):
    """
    로거 초기화 및 설정
    
    Args:
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_path: 로그 파일 저장 경로
        enable_console: 콘솔 출력 활성화 여부
        enable_file: 파일 출력 활성화 여부
    
    Example:
        >>> from logger import setup_logger
        >>> setup_logger(log_level="DEBUG", log_path="./logs")
    """
    
    # 로그 디렉토리 생성
    log_dir = Path(log_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 기존 핸들러 모두 제거
    logger.remove()
    
    # ===== 로그 포맷 정의 =====
    # 콘솔용 포맷 (컬러 포함)
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    
    # 파일용 포맷 (컬러 제외)
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )
    
    # ===== 콘솔 핸들러 =====
    if enable_console:
        logger.add(
            sys.stderr,
            format=console_format,
            level=log_level,
            colorize=True,
            backtrace=True,
            diagnose=True
        )
    
    # ===== 파일 핸들러 =====
    if enable_file:
        # 1. 일반 로그 (INFO 이상)
        logger.add(
            log_dir / "system_{time:YYYY-MM-DD}.log",
            format=file_format,
            level="INFO",
            rotation="00:00",     # 매일 자정에 새 파일
            retention="30 days",  # 30일 보관
            compression="gz",     # 압축
            encoding="utf-8",
            enqueue=True          # 비동기 쓰기
        )
        
        # 2. 에러 로그 (ERROR 이상)
        logger.add(
            log_dir / "error_{time:YYYY-MM-DD}.log",
            format=file_format,
            level="ERROR",
            rotation="00:00",
            retention="90 days",  # 에러는 90일 보관
            compression="gz",
            encoding="utf-8",
            enqueue=True
        )
        
        # 3. 트레이딩 전용 로그 (매매 기록)
        logger.add(
            log_dir / "trading_{time:YYYY-MM-DD}.log",
            format=file_format,
            level="INFO",
            rotation="00:00",
            retention="365 days",  # 매매 기록은 1년 보관
            compression="gz",
            encoding="utf-8",
            filter=lambda record: "trading" in record["extra"],  # trading 태그만
            enqueue=True
        )
        
        # 4. 디버그 로그 (개발 시에만)
        if log_level == "DEBUG":
            logger.add(
                log_dir / "debug_{time:YYYY-MM-DD}.log",
                format=file_format,
                level="DEBUG",
                rotation="100 MB",   # 100MB마다 로테이션
                retention="7 days",  # 7일 보관
                compression="gz",
                encoding="utf-8",
                enqueue=True
            )
    
    logger.info("🚀 로깅 시스템 초기화 완료")
    logger.info(f"로그 레벨: {log_level}")
    logger.info(f"로그 경로: {log_dir.absolute()}")


def get_trading_logger():
    """
    트레이딩 전용 로거 반환
    
    매매 관련 로그는 별도 파일에 저장됩니다.
    
    Returns:
        트레이딩 태그가 붙은 로거
        
    Example:
        >>> trading_logger = get_trading_logger()
        >>> trading_logger.info("매수 주문 실행: 삼성전자 10주")
    """
    return logger.bind(trading=True)


# ===== 컨텍스트 로거 유틸리티 =====
def log_with_context(task: str):
    """
    컨텍스트가 포함된 로거 반환
    
    Args:
        task: 작업 이름 (예: "theme_analysis", "stock_screening")
    
    Returns:
        컨텍스트가 바인딩된 로거
        
    Example:
        >>> theme_logger = log_with_context("theme_analysis")
        >>> theme_logger.info("테마 분석 시작")
        # 출력: ... | theme_analysis | 테마 분석 시작
    """
    return logger.bind(task=task)


# ===== 성능 측정 데코레이터 =====
def log_execution_time(func):
    """
    함수 실행 시간을 로깅하는 데코레이터
    
    Example:
        @log_execution_time
        def slow_function():
            time.sleep(2)
            return "done"
        
        # 출력: slow_function 실행 완료 (2.00초)
    """
    import functools
    import time
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.debug(f"{func.__name__} 실행 완료 ({elapsed:.2f}초)")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"{func.__name__} 실행 실패 ({elapsed:.2f}초): {e}")
            raise
    
    return wrapper


async def log_execution_time_async(func):
    """
    비동기 함수 실행 시간을 로깅하는 데코레이터
    
    Example:
        @log_execution_time_async
        async def slow_async_function():
            await asyncio.sleep(2)
            return "done"
    """
    import functools
    import time
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.debug(f"{func.__name__} 실행 완료 ({elapsed:.2f}초)")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"{func.__name__} 실행 실패 ({elapsed:.2f}초): {e}")
            raise
    
    return wrapper


# ===== 에러 알림 함수 =====
async def notify_critical_error(error_message: str, details: str = ""):
    """
    심각한 에러 발생 시 텔레그램 알림 전송
    
    Args:
        error_message: 에러 메시지
        details: 상세 정보 (스택 트레이스 등)
    
    Note:
        이 함수는 modules/reporter/telegram_notifier.py가
        구현된 후에 실제 알림이 전송됩니다.
    """
    logger.critical(f"🚨 {error_message}")
    if details:
        logger.critical(f"상세: {details}")
    
    # TODO: 텔레그램 알림 연동
    # from modules.reporter.telegram_notifier import send_telegram_message
    # await send_telegram_message(f"🚨 시스템 에러\n{error_message}\n{details}")


# ===== 초기화 =====
# 모듈 import 시 기본 설정으로 초기화
# config.py가 로드된 후에 setup_logger()로 재설정 가능
def _initialize_default_logger():
    """기본 로거 초기화 (모듈 로드 시 자동 실행)"""
    try:
        # config가 있으면 설정 사용
        from config import settings
        setup_logger(
            log_level=settings.LOG_LEVEL,
            log_path=settings.LOG_PATH
        )
    except ImportError:
        # config가 없으면 기본값 사용
        setup_logger(
            log_level="INFO",
            log_path="logs"
        )


# 모듈 로드 시 기본 초기화
_initialize_default_logger()


# 직접 실행 시 테스트
if __name__ == "__main__":
    print("=" * 50)
    print("로거 테스트")
    print("=" * 50)
    
    # 각 레벨 테스트
    logger.debug("디버그 메시지 - 상세한 개발 정보")
    logger.info("정보 메시지 - 일반적인 시스템 정보")
    logger.warning("경고 메시지 - 주의가 필요한 상황")
    logger.error("에러 메시지 - 문제가 발생함")
    logger.critical("심각 메시지 - 즉시 조치 필요")
    
    # 트레이딩 로거 테스트
    trading_logger = get_trading_logger()
    trading_logger.info("📈 매수 주문: 삼성전자 10주 @ 75,000원")
    trading_logger.info("📉 매도 주문: SK하이닉스 5주 @ 150,000원")
    
    # 컨텍스트 로거 테스트
    theme_log = log_with_context("theme_analysis")
    theme_log.info("테마 분석 시작")
    
    print("=" * 50)
    print("로거 테스트 완료 - logs 폴더를 확인하세요")
    print("=" * 50)
