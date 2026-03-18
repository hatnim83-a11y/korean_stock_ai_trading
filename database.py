"""
database.py - SQLite 데이터베이스 관리 모듈

이 파일은 시스템의 모든 데이터베이스 작업을 관리합니다.
- 테이블 생성 및 관리
- 스키마 마이그레이션 (버전 관리)
- 데이터 CRUD 작업
- 커넥션 풀 관리

사용법:
    from database import Database

    db = Database()
    db.connect()
    db.save_theme_scores(themes, date.today())
    portfolio = db.get_portfolio()
    db.close()
"""

import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from logger import logger
from config import now_kst


class Database:
    """
    SQLite 데이터베이스 관리 클래스

    Attributes:
        db_path: 데이터베이스 파일 경로
        conn: SQLite 연결 객체

    Example:
        >>> db = Database("data/trading.db")
        >>> db.connect()
        >>> db.init_tables()
        >>> db.close()
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        데이터베이스 초기화

        Args:
            db_path: DB 파일 경로 (None이면 config에서 로드)
        """
        if db_path is None:
            try:
                from config import settings
                db_path = settings.DATABASE_PATH
            except ImportError:
                db_path = "data/trading.db"

        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None

        # 데이터 디렉토리 생성
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> None:
        """
        데이터베이스 연결

        연결 후 row_factory를 설정하여 딕셔너리 형태로 데이터 반환
        """
        try:
            self.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,  # 멀티스레드 환경 지원
                timeout=30.0  # 락 대기 시간
            )
            # 쿼리 결과를 딕셔너리 형태로 반환
            self.conn.row_factory = sqlite3.Row
            # WAL 모드 활성화 (동시 읽기/쓰기 성능 향상)
            self.conn.execute("PRAGMA journal_mode=WAL")
            # 외래키 제약 활성화
            self.conn.execute("PRAGMA foreign_keys=ON")

            logger.info(f"데이터베이스 연결 성공: {self.db_path}")

        except sqlite3.Error as e:
            logger.error(f"데이터베이스 연결 실패: {e}")
            raise

    def close(self) -> None:
        """데이터베이스 연결 종료"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("데이터베이스 연결 종료")

    @contextmanager
    def get_cursor(self):
        """
        커서를 반환하는 컨텍스트 매니저

        자동으로 커밋/롤백 처리

        Example:
            >>> with db.get_cursor() as cursor:
            >>>     cursor.execute("SELECT * FROM themes")
            >>>     rows = cursor.fetchall()
        """
        if not self.conn:
            raise RuntimeError("데이터베이스가 연결되지 않았습니다. connect()를 먼저 호출하세요.")

        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"데이터베이스 작업 실패: {e}")
            raise
        finally:
            cursor.close()

    # ===== 스키마 마이그레이션 =====

    def _has_column(self, table: str, column: str) -> bool:
        """테이블에 특정 컬럼이 있는지 확인"""
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cursor.fetchall()]
            return column in columns
        finally:
            cursor.close()

    def _get_schema_version(self) -> int:
        """현재 스키마 버전 반환"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
            if not cursor.fetchone():
                return 0
            cursor.execute("SELECT MAX(version) as v FROM schema_version")
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0
        finally:
            cursor.close()

    def _migrate(self) -> None:
        """스키마 마이그레이션 (멱등). init_tables() 끝에서 호출."""
        # schema_version 테이블 생성
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        current = self._get_schema_version()
        migrations = [
            (1, "position_state 테이블", self._migrate_v1),
            (2, "portfolio 컬럼 추가", self._migrate_v2),
            (3, "trades 컬럼 추가", self._migrate_v3),
            (4, "daily_snapshots 테이블", self._migrate_v4),
            (5, "trade_reviews 테이블", self._migrate_v5),
            (6, "strategy_stats 테이블", self._migrate_v6),
            (7, "screening_log 테이블", self._migrate_v7),
            (8, "신규 인덱스 추가", self._migrate_v8),
            (9, "post_trade_prices 테이블", self._migrate_v9),
            (10, "themes에 category 컬럼 추가", self._migrate_v10),
            (11, "themes에 selected 컬럼 추가", self._migrate_v11),
            (12, "themes에 url 컬럼 추가", self._migrate_v12),
        ]

        pending = [(v, desc, fn) for v, desc, fn in migrations if v > current]
        if not pending:
            return

        # 마이그레이션 전 DB 백업 (WAL/SHM 포함)
        backup_path = self.db_path.with_suffix(f".bak.{now_kst().strftime('%Y%m%d_%H%M%S')}")
        try:
            shutil.copy2(str(self.db_path), str(backup_path))
            wal_path = Path(str(self.db_path) + "-wal")
            shm_path = Path(str(self.db_path) + "-shm")
            if wal_path.exists():
                shutil.copy2(str(wal_path), str(backup_path) + "-wal")
            if shm_path.exists():
                shutil.copy2(str(shm_path), str(backup_path) + "-shm")
            logger.info(f"DB 백업 완료: {backup_path}")
        except Exception as e:
            logger.warning(f"DB 백업 실패 (마이그레이션 계속): {e}")

        for version, description, migrate_fn in pending:
            try:
                migrate_fn()
                with self.get_cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                        (version, description)
                    )
                logger.info(f"마이그레이션 v{version} 적용: {description}")
            except Exception as e:
                logger.error(f"마이그레이션 v{version} 실패: {e}")
                raise

    def _migrate_v1(self) -> None:
        """Phase 1-A: position_state 테이블 (monitor_state.json 대체)"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS position_state (
                    stock_code VARCHAR(10) PRIMARY KEY,
                    current_price REAL DEFAULT 0,
                    highest_price REAL DEFAULT 0,
                    trailing_active BOOLEAN DEFAULT 0,
                    trailing_level INTEGER DEFAULT 0,
                    trailing_stop_price REAL,
                    max_profit_rate REAL DEFAULT 0,
                    partial_1_executed BOOLEAN DEFAULT 0,
                    partial_2_executed BOOLEAN DEFAULT 0,
                    partial_3_executed BOOLEAN DEFAULT 0,
                    remaining_shares INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _migrate_v2(self) -> None:
        """Phase 1-B: portfolio 컬럼 추가"""
        columns = [
            ("original_shares", "INTEGER"),
            ("buy_date", "DATE"),
            ("partial_1_executed", "BOOLEAN DEFAULT 0"),
            ("partial_2_executed", "BOOLEAN DEFAULT 0"),
            ("partial_3_executed", "BOOLEAN DEFAULT 0"),
            ("trailing_active", "BOOLEAN DEFAULT 0"),
            ("trailing_level", "INTEGER DEFAULT 0"),
            ("trailing_stop", "REAL"),
            ("highest_price", "REAL"),
            ("max_profit_rate", "REAL DEFAULT 0"),
        ]
        with self.get_cursor() as cursor:
            for col_name, col_type in columns:
                if not self._has_column("portfolio", col_name):
                    cursor.execute(f"ALTER TABLE portfolio ADD COLUMN {col_name} {col_type}")
            # 기존 데이터 보정
            cursor.execute("UPDATE portfolio SET original_shares = shares WHERE original_shares IS NULL")
            cursor.execute("UPDATE portfolio SET buy_date = date WHERE buy_date IS NULL")

    def _migrate_v3(self) -> None:
        """Phase 1-C: trades 컬럼 추가"""
        columns = [
            ("buy_price", "REAL"),
            ("filled_price", "REAL"),
            ("slippage", "REAL"),
            ("remaining_shares", "INTEGER"),
        ]
        with self.get_cursor() as cursor:
            for col_name, col_type in columns:
                if not self._has_column("trades", col_name):
                    cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")

    def _migrate_v4(self) -> None:
        """Phase 2-A: daily_snapshots 테이블"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL UNIQUE,
                    total_capital REAL,
                    cash_balance REAL DEFAULT 0,
                    total_invested REAL DEFAULT 0,
                    total_eval REAL DEFAULT 0,
                    unrealized_pnl REAL DEFAULT 0,
                    realized_pnl_today REAL DEFAULT 0,
                    realized_pnl_cumulative REAL DEFAULT 0,
                    daily_return REAL DEFAULT 0,
                    cumulative_return REAL DEFAULT 0,
                    mdd REAL DEFAULT 0,
                    peak_value REAL DEFAULT 0,
                    num_positions INTEGER DEFAULT 0,
                    buy_count INTEGER DEFAULT 0,
                    sell_count INTEGER DEFAULT 0,
                    win_count_cumulative INTEGER DEFAULT 0,
                    loss_count_cumulative INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    positions_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _migrate_v5(self) -> None:
        """Phase 2-B: trade_reviews 테이블"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    buy_date DATE,
                    sell_date DATE,
                    buy_price REAL,
                    sell_price REAL,
                    shares INTEGER,
                    hold_days INTEGER,
                    profit_rate REAL,
                    profit_amount REAL,
                    sell_reason VARCHAR(50),
                    strategy_type VARCHAR(30),
                    trailing_level INTEGER,
                    max_profit_during_hold REAL,
                    theme VARCHAR(50),
                    ai_review TEXT,
                    lesson_learned TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trade_id) REFERENCES trades(id)
                )
            """)

    def _migrate_v6(self) -> None:
        """Phase 2-C: strategy_stats 테이블"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    strategy_type VARCHAR(30) NOT NULL,
                    trade_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_profit_rate REAL DEFAULT 0,
                    avg_hold_days REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, strategy_type)
                )
            """)

    def _migrate_v7(self) -> None:
        """Phase 3-A: screening_log 테이블"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS screening_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    theme VARCHAR(50),
                    stage VARCHAR(30) NOT NULL,
                    passed BOOLEAN DEFAULT 0,
                    score REAL,
                    reject_reason TEXT,
                    details_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, stock_code, stage)
                )
            """)

    def _migrate_v8(self) -> None:
        """신규 테이블 인덱스 추가"""
        with self.get_cursor() as cursor:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_reviews_stock ON trade_reviews(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_reviews_sell_date ON trade_reviews(sell_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategy_stats_date ON strategy_stats(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_screening_log_date ON screening_log(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_screening_log_stock ON screening_log(stock_code)")

    def _migrate_v9(self) -> None:
        """post_trade_prices 테이블 (매도 후 주가 추이)"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS post_trade_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    sell_date DATE NOT NULL,
                    check_date DATE NOT NULL,
                    days_after_sell INTEGER NOT NULL,
                    close_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume INTEGER,
                    change_from_sell REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (review_id) REFERENCES trade_reviews(id),
                    UNIQUE(review_id, check_date)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_trade_prices_review ON post_trade_prices(review_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_trade_prices_stock ON post_trade_prices(stock_code)")

    def _migrate_v10(self) -> None:
        """themes 테이블에 category 컬럼 추가"""
        with self.get_cursor() as cursor:
            try:
                cursor.execute("ALTER TABLE themes ADD COLUMN category VARCHAR(20) DEFAULT '기타'")
            except Exception:
                pass  # 이미 존재하면 무시

    def _migrate_v11(self) -> None:
        """themes 테이블에 selected 컬럼 추가 (주간 선정 vs 일별 수집 구분)"""
        with self.get_cursor() as cursor:
            try:
                cursor.execute("ALTER TABLE themes ADD COLUMN selected BOOLEAN DEFAULT 0")
            except Exception:
                pass  # 이미 존재하면 무시

    def _migrate_v12(self) -> None:
        """themes 테이블에 url 컬럼 추가 (화요일 실시간 보강용)"""
        with self.get_cursor() as cursor:
            try:
                cursor.execute("ALTER TABLE themes ADD COLUMN url VARCHAR(200) DEFAULT ''")
            except Exception:
                pass  # 이미 존재하면 무시

    def init_tables(self) -> None:
        """
        모든 테이블 생성

        이미 존재하는 테이블은 무시됩니다 (IF NOT EXISTS 사용)
        """
        with self.get_cursor() as cursor:
            # ===== 1. 테마 점수 이력 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    theme_name VARCHAR(50) NOT NULL,
                    score REAL NOT NULL,
                    momentum REAL,
                    supply_ratio REAL,
                    news_count INTEGER,
                    ai_sentiment REAL,
                    category VARCHAR(20) DEFAULT '기타',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    -- 인덱스를 위한 유니크 제약
                    UNIQUE(date, theme_name)
                )
            """)

            # ===== 2. 종목 스크리닝 이력 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    theme VARCHAR(50),
                    supply_score REAL,
                    technical_score REAL,
                    ai_sentiment REAL,
                    ai_reason TEXT,
                    final_score REAL,
                    selected BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    UNIQUE(date, stock_code)
                )
            """)

            # ===== 3. 포트폴리오 현황 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    theme VARCHAR(50),
                    weight REAL,
                    shares INTEGER,
                    buy_price REAL,
                    current_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    profit_rate REAL,
                    profit_amount REAL,
                    status VARCHAR(20) DEFAULT 'holding',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ===== 4. 매매 기록 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    time TIME,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    action VARCHAR(10) NOT NULL,
                    shares INTEGER,
                    price REAL,
                    amount REAL,
                    reason VARCHAR(50),
                    profit_rate REAL,
                    profit_amount REAL,
                    order_id VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ===== 5. 성과 지표 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL UNIQUE,
                    total_value REAL,
                    total_cost REAL,
                    cash REAL,
                    daily_return REAL,
                    cumulative_return REAL,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL,
                    mdd REAL,
                    sharpe_ratio REAL,
                    num_positions INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ===== 6. 시스템 상태 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    message TEXT,
                    error_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ===== 인덱스 생성 =====
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_themes_date ON themes(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_date ON stocks(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_code ON stocks(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code)")

        # 스키마 마이그레이션 실행
        self._migrate()

        logger.info("데이터베이스 테이블 초기화 완료")

    # ===== 테마 관련 메서드 =====

    def save_theme_scores(self, themes: list[dict], target_date: date, selected: bool = False) -> None:
        """
        테마 점수 저장

        Args:
            themes: 테마 리스트 [{'theme': '2차전지', 'score': 87.5, 'category': '신성장', ...}, ...]
            target_date: 날짜
            selected: True=주간 선정 테마, False=일별 수집 데이터
        """
        with self.get_cursor() as cursor:
            # 주간 선정 저장 시 기존 selected 플래그 초기화 (같은 날짜)
            if selected:
                cursor.execute(
                    "UPDATE themes SET selected = 0 WHERE date = ? AND selected = 1",
                    (target_date,)
                )

            for theme in themes:
                if not selected:
                    # 일별 수집: 기존 selected=1 행은 절대 수정하지 않음
                    # (주간 가중평균 점수와 일별 단일일 점수는 성격이 다르므로 덮어쓰면 안 됨)
                    cursor.execute(
                        "SELECT selected FROM themes WHERE date = ? AND theme_name = ?",
                        (target_date, theme['theme'])
                    )
                    existing = cursor.fetchone()
                    if existing and existing[0] == 1:
                        continue

                cursor.execute("""
                    INSERT OR REPLACE INTO themes (
                        date, theme_name, score, momentum, supply_ratio,
                        news_count, ai_sentiment, category, selected, url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    theme['theme'],
                    theme['score'],
                    theme.get('momentum', 0),
                    theme.get('supply_ratio', 0),
                    theme.get('news_count', 0),
                    theme.get('ai_sentiment', 0),
                    theme.get('category', '기타'),
                    1 if selected else 0,
                    theme.get('url', ''),
                ))

        logger.info(f"{len(themes)}개 테마 점수 저장 완료 ({target_date}, selected={selected})")

    def get_top_themes(self, target_date: date, count: int = 5) -> list[dict]:
        """
        상위 테마 조회 (주간 선정 테마 우선)

        Args:
            target_date: 조회할 날짜
            count: 조회할 테마 수

        Returns:
            테마 리스트 (점수 순)
        """
        with self.get_cursor() as cursor:
            # 먼저 selected=1인 주간 선정 테마 조회
            cursor.execute("""
                SELECT * FROM themes
                WHERE date = ? AND selected = 1
                ORDER BY score DESC
                LIMIT ?
            """, (target_date, count))
            rows = cursor.fetchall()

            # selected 테마가 없으면 (v11 이전 데이터) 기존 방식 폴백
            if not rows:
                cursor.execute("""
                    SELECT * FROM themes
                    WHERE date = ?
                    ORDER BY score DESC
                    LIMIT ?
                """, (target_date, count))
                rows = cursor.fetchall()

            return [dict(row) for row in rows]

    def get_last_theme_analysis_date(self) -> Optional[date]:
        """
        마지막 테마 선정 날짜 조회 (서비스 재시작 시 7일 로테이션 복원용)

        selected=1인 주간 선정 테마의 최신 날짜를 반환.
        v11 이전 데이터(selected 컬럼 없거나 모두 0)인 경우 전체 MAX(date) 폴백.

        Returns:
            마지막 선정 날짜 또는 None
        """
        try:
            with self.get_cursor() as cursor:
                # 주간 선정 테마(selected=1)의 최신 날짜 우선
                cursor.execute("SELECT MAX(date) as last_date FROM themes WHERE selected = 1")
                row = cursor.fetchone()
                if row and row["last_date"]:
                    from datetime import datetime as dt
                    return dt.strptime(row["last_date"], "%Y-%m-%d").date()

                # 폴백: v11 이전 데이터
                cursor.execute("SELECT MAX(date) as last_date FROM themes")
                row = cursor.fetchone()
                if row and row["last_date"]:
                    from datetime import datetime as dt
                    return dt.strptime(row["last_date"], "%Y-%m-%d").date()
        except Exception as e:
            logger.debug(f"마지막 테마 분석 날짜 조회 실패: {e}")
        return None

    def get_daily_theme_scores(self, target_date: date) -> list[dict]:
        """
        전일 일별 수집 점수 조회 (selected=0)

        주중 교체 판단에 사용: 일별 17:05에 수집된 데이터에서
        각 테마의 점수를 내림차순으로 반환.

        Args:
            target_date: 조회할 날짜

        Returns:
            테마 리스트 (점수 내림차순) [{'theme_name': ..., 'score': ..., ...}, ...]
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM themes
                WHERE date = ? AND selected = 0
                ORDER BY score DESC
            """, (target_date,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    # ===== 종목 관련 메서드 =====

    def save_screened_stocks(self, stocks: list[dict], target_date: date) -> None:
        """
        스크리닝된 종목 저장

        Args:
            stocks: 종목 리스트
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            for stock in stocks:
                cursor.execute("""
                    INSERT OR REPLACE INTO stocks (
                        date, stock_code, stock_name, theme, supply_score,
                        technical_score, ai_sentiment, ai_reason, final_score, selected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    stock['stock_code'],
                    stock['stock_name'],
                    stock.get('theme'),
                    stock.get('supply_score'),
                    stock.get('technical_score'),
                    stock.get('ai_sentiment'),
                    stock.get('ai_reason'),
                    stock.get('final_score'),
                    stock.get('selected', False)
                ))

        logger.info(f"{len(stocks)}개 종목 스크리닝 결과 저장 완료")

    # ===== 포트폴리오 관련 메서드 =====

    def save_portfolio(self, portfolio: list[dict], target_date: date) -> None:
        """
        포트폴리오 저장

        Args:
            portfolio: 포트폴리오 종목 리스트
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            for position in portfolio:
                cursor.execute("""
                    INSERT INTO portfolio (
                        date, stock_code, stock_name, theme, weight, shares,
                        buy_price, stop_loss, take_profit, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    position['stock_code'],
                    position['stock_name'],
                    position.get('theme'),
                    position.get('weight'),
                    position.get('shares'),
                    position.get('buy_price'),
                    position.get('stop_loss'),
                    position.get('take_profit'),
                    position.get('status', 'holding')
                ))

        logger.info(f"포트폴리오 {len(portfolio)}개 종목 저장 완료")

    def get_portfolio(self, status: str = "holding") -> list[dict]:
        """
        현재 포트폴리오 조회

        Args:
            status: 상태 필터 ('holding' 또는 'closed')

        Returns:
            포트폴리오 종목 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM portfolio
                WHERE status = ?
                ORDER BY created_at DESC
            """, (status,))

            rows = cursor.fetchall()
            portfolio = [dict(row) for row in rows]

        logger.info(f"포트폴리오 조회: {len(portfolio)}개 종목 ({status})")
        return portfolio

    def update_portfolio_price(
        self,
        stock_code: str,
        current_price: float,
        profit_rate: float,
        profit_amount: float
    ) -> None:
        """
        포트폴리오 현재가 업데이트

        Args:
            stock_code: 종목코드
            current_price: 현재가
            profit_rate: 수익률
            profit_amount: 수익금액
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio
                SET current_price = ?, profit_rate = ?, profit_amount = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (current_price, profit_rate, profit_amount, stock_code))

    def save_holding_position(self, position: dict) -> None:
        """
        매수 체결 후 holding 포지션 저장

        Args:
            position: 포지션 정보 딕셔너리
        """
        shares = position.get('shares')
        with self.get_cursor() as cursor:
            # 같은 종목의 기존 pending/holding 엔트리 정리
            cursor.execute("""
                UPDATE portfolio SET status = 'replaced'
                WHERE stock_code = ? AND status IN ('pending', 'holding')
            """, (position['stock_code'],))

            cursor.execute("""
                INSERT INTO portfolio (
                    date, stock_code, stock_name, theme, weight,
                    shares, buy_price, current_price,
                    stop_loss, take_profit, status,
                    original_shares, buy_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'holding', ?, ?)
            """, (
                position.get('date'),
                position['stock_code'],
                position['stock_name'],
                position.get('theme'),
                position.get('weight'),
                shares,
                position.get('buy_price'),
                position.get('buy_price'),  # current_price = buy_price 초기값
                position.get('stop_loss'),
                position.get('take_profit'),
                shares,  # original_shares = shares
                position.get('date'),  # buy_date = date
            ))

        logger.info(f"포지션 저장: {position['stock_name']} (holding)")

    def update_portfolio_shares(self, stock_code: str, new_shares: int) -> None:
        """
        포트폴리오 보유 수량 업데이트 (분할 매도 시)

        Args:
            stock_code: 종목코드
            new_shares: 업데이트할 수량
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio
                SET shares = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (new_shares, stock_code))
            if cursor.rowcount == 0:
                logger.warning(f"포지션 수량 업데이트 실패: {stock_code} - holding 상태 row 없음")
            else:
                logger.info(f"포지션 수량 업데이트: {stock_code} -> {new_shares}주")

    def update_portfolio_partial_state(
        self, stock_code: str, partial_1: bool, partial_2: bool, partial_3: bool,
        trailing_active: bool = False, trailing_level: int = 0,
        trailing_stop: Optional[float] = None, highest_price: Optional[float] = None,
        max_profit_rate: float = 0
    ) -> None:
        """portfolio 테이블의 partial/trailing 상태 업데이트"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio
                SET partial_1_executed = ?, partial_2_executed = ?, partial_3_executed = ?,
                    trailing_active = ?, trailing_level = ?, trailing_stop = ?,
                    highest_price = ?, max_profit_rate = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (
                partial_1, partial_2, partial_3,
                trailing_active, trailing_level, trailing_stop,
                highest_price, max_profit_rate,
                stock_code
            ))

    def close_position(self, stock_code: str, reason: str) -> None:
        """
        포지션 청산 (상태 변경)

        Args:
            stock_code: 종목코드
            reason: 청산 사유 (손절/익절/수급이탈)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio
                SET status = 'closed', updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (stock_code,))

        logger.info(f"포지션 청산: {stock_code} ({reason})")

    # ===== position_state CRUD =====

    def upsert_position_state(self, stock_code: str, state: dict) -> None:
        """position_state UPSERT (30초마다 호출)"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO position_state (
                    stock_code, current_price, highest_price,
                    trailing_active, trailing_level, trailing_stop_price,
                    max_profit_rate,
                    partial_1_executed, partial_2_executed, partial_3_executed,
                    remaining_shares, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code) DO UPDATE SET
                    current_price = excluded.current_price,
                    highest_price = excluded.highest_price,
                    trailing_active = excluded.trailing_active,
                    trailing_level = excluded.trailing_level,
                    trailing_stop_price = excluded.trailing_stop_price,
                    max_profit_rate = excluded.max_profit_rate,
                    partial_1_executed = excluded.partial_1_executed,
                    partial_2_executed = excluded.partial_2_executed,
                    partial_3_executed = excluded.partial_3_executed,
                    remaining_shares = excluded.remaining_shares,
                    last_updated = CURRENT_TIMESTAMP
            """, (
                stock_code,
                state.get("current_price", 0),
                state.get("highest_price", 0),
                state.get("trailing_active", False),
                state.get("trailing_level", 0),
                state.get("trailing_stop_price"),
                state.get("max_profit_rate", 0),
                state.get("partial_1_executed", False),
                state.get("partial_2_executed", False),
                state.get("partial_3_executed", False),
                state.get("remaining_shares", 0),
            ))

    def get_all_position_states(self) -> dict:
        """전체 position_state 조회 (복원용)

        Returns:
            {stock_code: {state dict}, ...}
        """
        result = {}
        try:
            with self.get_cursor() as cursor:
                cursor.execute("SELECT * FROM position_state")
                for row in cursor.fetchall():
                    result[row["stock_code"]] = dict(row)
        except Exception as e:
            logger.debug(f"position_state 조회 실패: {e}")
        return result

    def delete_position_state(self, stock_code: str) -> None:
        """position_state 삭제 (포지션 청산 시)"""
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM position_state WHERE stock_code = ?", (stock_code,))

    # ===== 매매 기록 관련 메서드 =====

    def save_trade(self, trade: dict) -> Optional[int]:
        """
        매매 기록 저장

        Args:
            trade: 매매 정보 딕셔너리

        Returns:
            생성된 trade ID (lastrowid)
        """
        trade_id = None
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trades (
                    date, time, stock_code, stock_name, action, shares,
                    price, amount, reason, profit_rate, profit_amount, order_id,
                    buy_price, filled_price, slippage, remaining_shares
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get('date', now_kst().date()),
                trade.get('time', now_kst().strftime("%H:%M:%S")),
                trade['stock_code'],
                trade['stock_name'],
                trade['action'],
                trade.get('shares'),
                trade.get('price'),
                trade.get('amount'),
                trade.get('reason'),
                trade.get('profit_rate'),
                trade.get('profit_amount'),
                trade.get('order_id'),
                trade.get('buy_price'),
                trade.get('filled_price'),
                trade.get('slippage'),
                trade.get('remaining_shares'),
            ))
            trade_id = cursor.lastrowid

        action_emoji = "buy" if trade['action'] == 'buy' else "sell"
        logger.info(f"매매 기록 저장: {action_emoji} {trade['stock_name']}")
        return trade_id

    def get_trades(self, target_date: date) -> list[dict]:
        """
        특정 날짜의 매매 기록 조회

        Args:
            target_date: 조회할 날짜

        Returns:
            매매 기록 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trades
                WHERE date = ?
                ORDER BY time DESC
            """, (target_date,))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_all_sell_trades(self) -> list[dict]:
        """전체 매도 기록 조회 (실현 손익 계산용)"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trades
                WHERE action = 'sell'
                ORDER BY date DESC, time DESC
            """)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    # ===== 성과 지표 관련 메서드 =====

    def save_performance(self, performance: dict, target_date: date) -> None:
        """
        일일 성과 지표 저장

        Args:
            performance: 성과 지표 딕셔너리
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO performance (
                    date, total_value, total_cost, cash, daily_return,
                    cumulative_return, win_count, loss_count, win_rate,
                    mdd, sharpe_ratio, num_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                target_date,
                performance.get('total_value'),
                performance.get('total_cost'),
                performance.get('cash'),
                performance.get('daily_return'),
                performance.get('cumulative_return'),
                performance.get('win_count', 0),
                performance.get('loss_count', 0),
                performance.get('win_rate'),
                performance.get('mdd'),
                performance.get('sharpe_ratio'),
                performance.get('num_positions')
            ))

        logger.info(f"일일 성과 저장: {target_date}")

    def get_performance_history(self, days: int = 30) -> list[dict]:
        """
        최근 N일간 성과 이력 조회

        Args:
            days: 조회할 일수

        Returns:
            성과 이력 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM performance
                ORDER BY date DESC
                LIMIT ?
            """, (days,))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    # ===== daily_snapshots =====

    def save_daily_snapshot(self, snapshot: dict) -> None:
        """일일 스냅샷 저장 (장 마감 시)"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO daily_snapshots (
                    date, total_capital, cash_balance, total_invested, total_eval,
                    unrealized_pnl, realized_pnl_today, realized_pnl_cumulative,
                    daily_return, cumulative_return, mdd, peak_value,
                    num_positions, buy_count, sell_count,
                    win_count_cumulative, loss_count_cumulative, win_rate,
                    positions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot['date'],
                snapshot.get('total_capital'),
                snapshot.get('cash_balance', 0),
                snapshot.get('total_invested', 0),
                snapshot.get('total_eval', 0),
                snapshot.get('unrealized_pnl', 0),
                snapshot.get('realized_pnl_today', 0),
                snapshot.get('realized_pnl_cumulative', 0),
                snapshot.get('daily_return', 0),
                snapshot.get('cumulative_return', 0),
                snapshot.get('mdd', 0),
                snapshot.get('peak_value', 0),
                snapshot.get('num_positions', 0),
                snapshot.get('buy_count', 0),
                snapshot.get('sell_count', 0),
                snapshot.get('win_count_cumulative', 0),
                snapshot.get('loss_count_cumulative', 0),
                snapshot.get('win_rate', 0),
                snapshot.get('positions_json'),
            ))
        logger.info(f"일일 스냅샷 저장: {snapshot['date']}")

    def get_daily_snapshots(self, days: int = 90) -> list[dict]:
        """최근 N일 스냅샷 조회"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM daily_snapshots
                ORDER BY date DESC
                LIMIT ?
            """, (days,))
            return [dict(row) for row in cursor.fetchall()]

    # ===== trade_reviews =====

    def save_trade_review(self, review: dict) -> None:
        """매매 복기 저장"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trade_reviews (
                    trade_id, stock_code, stock_name, buy_date, sell_date,
                    buy_price, sell_price, shares, hold_days,
                    profit_rate, profit_amount, sell_reason,
                    strategy_type, trailing_level, max_profit_during_hold,
                    theme
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review.get('trade_id'),
                review['stock_code'],
                review['stock_name'],
                review.get('buy_date'),
                review.get('sell_date'),
                review.get('buy_price'),
                review.get('sell_price'),
                review.get('shares'),
                review.get('hold_days'),
                review.get('profit_rate'),
                review.get('profit_amount'),
                review.get('sell_reason'),
                review.get('strategy_type'),
                review.get('trailing_level'),
                review.get('max_profit_during_hold'),
                review.get('theme'),
            ))
        logger.info(f"매매 복기 저장: {review['stock_name']}")

    def get_pending_trade_reviews(self) -> list[dict]:
        """AI 복기가 아직 안 된 trade_reviews 조회"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trade_reviews
                WHERE ai_review IS NULL
                ORDER BY sell_date DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_trade_review_ai(self, review_id: int, ai_review: str, lesson: str) -> None:
        """trade_review에 AI 평가 업데이트"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE trade_reviews
                SET ai_review = ?, lesson_learned = ?
                WHERE id = ?
            """, (ai_review, lesson, review_id))

    # ===== post_trade_prices =====

    def save_post_trade_prices(self, review_id: int, prices: list[dict]) -> None:
        """매도 후 주가 추이 저장 (INSERT OR IGNORE로 중복 방지)"""
        with self.get_cursor() as cursor:
            for p in prices:
                cursor.execute("""
                    INSERT OR IGNORE INTO post_trade_prices (
                        review_id, stock_code, sell_date, check_date,
                        days_after_sell, close_price, high_price, low_price,
                        volume, change_from_sell
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    review_id,
                    p['stock_code'],
                    p['sell_date'],
                    p['check_date'],
                    p['days_after_sell'],
                    p.get('close_price'),
                    p.get('high_price'),
                    p.get('low_price'),
                    p.get('volume'),
                    p.get('change_from_sell'),
                ))
        logger.debug(f"매도 후 주가 저장: review_id={review_id}, {len(prices)}건")

    def get_post_trade_prices(self, review_id: int) -> list[dict]:
        """특정 trade_review의 매도 후 주가 추이 조회"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM post_trade_prices
                WHERE review_id = ?
                ORDER BY days_after_sell ASC
            """, (review_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_reviews_ready_for_analysis(self, min_days: int = 5) -> list[dict]:
        """매도 후 min_days 이상 경과한 미분석 trade_reviews 조회"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trade_reviews
                WHERE ai_review IS NULL
                  AND sell_date IS NOT NULL
                  AND julianday('now') - julianday(sell_date) >= ?
                ORDER BY sell_date ASC
            """, (min_days,))
            return [dict(row) for row in cursor.fetchall()]

    # ===== strategy_stats =====

    def save_strategy_stats(self, stats: dict) -> None:
        """전략별 성과 저장"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO strategy_stats (
                    date, strategy_type, trade_count, win_count, loss_count,
                    win_rate, total_pnl, avg_profit_rate, avg_hold_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stats['date'],
                stats['strategy_type'],
                stats.get('trade_count', 0),
                stats.get('win_count', 0),
                stats.get('loss_count', 0),
                stats.get('win_rate', 0),
                stats.get('total_pnl', 0),
                stats.get('avg_profit_rate', 0),
                stats.get('avg_hold_days', 0),
            ))

    def get_strategy_stats(self, days: int = 30) -> list[dict]:
        """최근 N일 전략별 성과 조회"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM strategy_stats
                ORDER BY date DESC
                LIMIT ?
            """, (days * 10,))  # 전략 유형별 여러 행이므로
            return [dict(row) for row in cursor.fetchall()]

    # ===== screening_log =====

    def save_screening_log(self, log: dict) -> None:
        """스크리닝 로그 저장"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR IGNORE INTO screening_log (
                    date, stock_code, stock_name, theme, stage,
                    passed, score, reject_reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log['date'],
                log['stock_code'],
                log['stock_name'],
                log.get('theme'),
                log['stage'],
                log.get('passed', False),
                log.get('score'),
                log.get('reject_reason'),
                log.get('details_json'),
            ))

    # ===== 시스템 상태 관련 메서드 =====

    def log_system_status(self, status: str, message: str = "") -> None:
        """
        시스템 상태 로깅

        Args:
            status: 상태 (running, stopped, error)
            message: 상태 메시지
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO system_status (date, status, message)
                VALUES (?, ?, ?)
            """, (now_kst().date(), status, message))


# ===== 편의 함수 =====

def get_database() -> Database:
    """
    데이터베이스 인스턴스 반환 (싱글톤 패턴)

    Returns:
        연결된 Database 인스턴스
    """
    db = Database()
    db.connect()
    db.init_tables()
    return db


# 직접 실행 시 테이블 초기화
if __name__ == "__main__":
    print("=" * 50)
    print("데이터베이스 초기화")
    print("=" * 50)

    db = Database()
    db.connect()
    db.init_tables()

    # 스키마 버전 확인
    ver = db._get_schema_version()
    print(f"\n스키마 버전: v{ver}")

    # 저장된 데이터 확인
    top_themes = db.get_top_themes(date.today())
    print(f"\n저장된 테마: {len(top_themes)}개")
    for theme in top_themes:
        print(f"  - {theme['theme_name']}: {theme['score']}점")

    db.close()

    print("\n데이터베이스 초기화 완료!")
    print("=" * 50)
