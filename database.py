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
from datetime import date, datetime, timedelta
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
            (13, "themes 산업재→금융 재분류", self._migrate_v13),
            (14, "screening_log 관찰 컬럼 2개 추가 (RSI·슬롯 보호)", self._migrate_v14),
            (15, "portfolio buy_message 컬럼 추가", self._migrate_v15),
            (16, "외국인/기관 수급 신호 도입 (daily_supply_snapshot, foreign_top_ranking, supply_score_observation + portfolio/trade_reviews 보강)", self._migrate_v16),
            (17, "분할 진입(Tranche Entry) + 불타기(Pyramid-In) + ATR 트레일링 도입", self._migrate_v17),
            (18, "portfolio is_manual 컬럼 추가 (텔레그램 수동 매수)", self._migrate_v18),
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

    def _migrate_v13(self) -> None:
        """산업재로 잘못 분류된 금융계 테마를 '금융' 카테고리로 이동.

        기존 _CATEGORY_KEYWORDS에서 산업재에 섞여있던 보험/증권/은행/리츠/SPAC 등이
        금융 카테고리로 분리되면서 과거 데이터를 일괄 재분류.
        화이트리스트 방식: 현재 DB에 실제 존재하는 6개 테마명만 정확 매칭하여 거짓 양성 방지.
        """
        target_themes = [
            '금융',
            '생명보험',
            '손해보험',
            '기업인수목적회사(SPAC)',
            '리츠(REITs)',
            '화폐/금융자동화기기(디지털화폐 등)',
        ]
        placeholders = ','.join('?' * len(target_themes))
        with self.get_cursor() as cursor:
            # 1) 사전 감사: 영향 대상 로그
            cursor.execute(
                f"SELECT theme_name, COUNT(*) FROM themes "
                f"WHERE category = '산업재' AND theme_name IN ({placeholders}) "
                f"GROUP BY theme_name",
                target_themes,
            )
            for row in cursor.fetchall():
                logger.info(f"v13 재분류 대상: {row[0]} ({row[1]}행)")

            # 2) 업데이트
            cursor.execute(
                f"UPDATE themes SET category = '금융' "
                f"WHERE category = '산업재' AND theme_name IN ({placeholders})",
                target_themes,
            )

    def _migrate_v14(self) -> None:
        """screening_log 관찰용 컬럼 2개 추가 (Phase A 매수필터 개선).

        - rsi_at_screen: 스크리닝 시점 RSI 값 (RSI 75 동적 통과 종목 사후 추적용)
        - theme_slot_protected: 테마 슬롯 보장에 의해 보존된 종목 플래그 (0/1)

        기본값 NULL/0이라 기존 레코드 영향 없음. 재실행 시 duplicate column 예외는 ignore.
        """
        with self.get_cursor() as cursor:
            try:
                cursor.execute(
                    "ALTER TABLE screening_log ADD COLUMN rsi_at_screen REAL DEFAULT NULL"
                )
            except Exception:
                pass  # 이미 존재하면 무시
            try:
                cursor.execute(
                    "ALTER TABLE screening_log ADD COLUMN theme_slot_protected INTEGER DEFAULT 0"
                )
            except Exception:
                pass

    def _migrate_v15(self) -> None:
        """portfolio 테이블에 buy_message 컬럼 추가.

        매수 시점 텔레그램 알림 메시지 전문(테마/수급/기술지표/AI 사유 등)을 종목별로 보존하여
        대시보드 호버 툴팁에서 매수 컨텍스트를 즉시 확인할 수 있도록 한다.
        """
        with self.get_cursor() as cursor:
            if not self._has_column("portfolio", "buy_message"):
                cursor.execute("ALTER TABLE portfolio ADD COLUMN buy_message TEXT")

    def _migrate_v16(self) -> None:
        """외국인/기관 수급 신호 도입 (2026-05-11 / supply-signal-integration Phase 1-A).

        목적: 종가베팅 시스템(closing_bet_system)이 검증한 외국인 수급 데이터 소스를
        메인 시스템에 이식. T-1 마감 데이터를 17:10에 1회 수집하여 다음날 아침
        KIS 재호출 없이 DB 조회로 종목 선정에 사용.

        신규 테이블:
        - daily_supply_snapshot: 종목별 T-1 외국인/기관 5일 수급 (FHKST01010900 결과)
        - foreign_top_ranking: 외국인/기관 순매수 상위 N (FHPTJ04400000 결과)
        - supply_score_observation: Shadow Run 관측 모드 (계산하되 총점 미반영)

        기존 테이블 보강:
        - portfolio +7 컬럼: 매수 시점 supply 컨텍스트 박제 (자동 trade_reviews 복사용)
        - trade_reviews +9 컬럼: 사후 적중률 측정 + Phase 2 direction_match
        """
        with self.get_cursor() as cursor:
            # 1) daily_supply_snapshot 신규 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_supply_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50),
                    foreign_net_5d REAL,
                    institution_net_5d REAL,
                    individual_net_5d REAL,
                    foreign_ratio REAL,
                    foreign_net_1d REAL,
                    institution_net_1d REAL,
                    close_price REAL,
                    trade_value_5d_avg REAL,
                    source VARCHAR(20) DEFAULT 'FHKST01010900',
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trade_date, stock_code)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_supply_snapshot_date "
                "ON daily_supply_snapshot(trade_date)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_supply_snapshot_code "
                "ON daily_supply_snapshot(stock_code)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_supply_snapshot_date_code "
                "ON daily_supply_snapshot(trade_date, stock_code)"
            )

            # 2) foreign_top_ranking 신규 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS foreign_top_ranking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    kind VARCHAR(20) NOT NULL,
                    rank INTEGER NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50),
                    net_amount REAL,
                    source VARCHAR(20) DEFAULT 'FHPTJ04400000',
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trade_date, kind, rank)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_top_ranking_date_kind "
                "ON foreign_top_ranking(trade_date, kind)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_top_ranking_code "
                "ON foreign_top_ranking(stock_code)"
            )

            # 3) supply_score_observation 신규 테이블 (Shadow Run 관측 모드)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supply_score_observation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    obs_date DATE NOT NULL,
                    theme_name VARCHAR(100) NOT NULL,
                    supply_score_v2 REAL,
                    momentum_score REAL,
                    news_score REAL,
                    ai_score REAL,
                    theme_total_actual REAL,
                    breakdown_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(obs_date, theme_name)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_supply_obs_date "
                "ON supply_score_observation(obs_date)"
            )

            # 4) portfolio 컬럼 추가 (매수 시점 supply 컨텍스트 박제)
            portfolio_columns = [
                ("foreign_net_at_buy", "REAL"),
                ("institution_net_at_buy", "REAL"),
                ("supply_strength_at_buy", "REAL"),
                ("foreign_top_rank_at_buy", "INTEGER"),
                ("institution_consec_days_at_buy", "INTEGER"),
                ("theme_supply_score_at_buy", "REAL"),
                ("ai_supply_signal_at_buy", "TEXT"),
            ]
            for col_name, col_type in portfolio_columns:
                if not self._has_column("portfolio", col_name):
                    cursor.execute(f"ALTER TABLE portfolio ADD COLUMN {col_name} {col_type}")

            # 5) trade_reviews 컬럼 추가 (사후 분석용 + Phase 2 direction_match)
            review_columns = [
                ("foreign_net_5d_at_buy", "REAL"),
                ("institution_net_5d_at_buy", "REAL"),
                ("supply_strength_at_buy", "REAL"),
                ("foreign_top_rank_at_buy", "INTEGER"),
                ("institution_consec_days_at_buy", "INTEGER"),
                ("theme_supply_score_at_buy", "REAL"),
                ("ai_supply_signal_at_buy", "TEXT"),
                ("foreign_direction_match", "INTEGER"),     # Phase 2 라벨링
                ("institution_direction_match", "INTEGER"), # Phase 2 라벨링
            ]
            for col_name, col_type in review_columns:
                if not self._has_column("trade_reviews", col_name):
                    cursor.execute(f"ALTER TABLE trade_reviews ADD COLUMN {col_name} {col_type}")

    def _migrate_v17(self) -> None:
        """분할 진입(Tranche Entry) + 불타기(Pyramid-In) + ATR 트레일링 도입.

        목적: 1차 50% 진입 + 수익률 +5% 도달 시 2차 50% 추가 진입(불타기)으로
        리스크 절반 축소 + 추세 종목 수익 끌기. 분할 익절 임계는 평균단가 기준으로,
        손절/BE/트레일링/2차 트리거는 1차 진입가 기준으로 분리(평균단가 함정 회피).
        트레일링 폭은 `max(고정값, 2.0 × ATR(14))`로 변동성 보호.

        portfolio 테이블에 7개 컬럼 추가:
        - first_buy_price: 1차 진입가 (손절/BE/2차 트리거/트레일링 기준)
        - avg_buy_price: 평균단가 (분할 익절 트리거 기준 — avg 정책 분기)
        - tranche_count: 진입 회차 (1 또는 2)
        - second_tranche_executed: 2차 진입 완료 플래그
        - second_tranche_pending: 1차 진입 후 2차 대기 상태
          (⚠️ DEFAULT 0 — 기존 holding 안전 / 신규 매수에서 명시적 =1 설정)
        - atr_at_buy: ATR(14일) 박제값 (트레일링 폭 계산용)
        - atr_period: ATR 기간 (감사용, DEFAULT 14)

        백필 4문장 (Coder 리뷰 P5 — 기존 holding 200% 매수 사고 차단):
        second_tranche_pending DEFAULT 0 + 백필 UPDATE로 이중 방어.
        만약 DEFAULT를 1로 잘못 설정했다면, 기존 보유 종목이 max_profit_rate >= 5%일 때
        봇 재시작 직후 2차 매수 발화로 100% 추가 진입 위험이 있다.
        본 마이그레이션은 DEFAULT 0 + 명시적 UPDATE 0으로 안전 보장.
        """
        columns = [
            ("first_buy_price", "REAL"),
            ("avg_buy_price", "REAL"),
            ("tranche_count", "INTEGER DEFAULT 1"),
            ("second_tranche_executed", "BOOLEAN DEFAULT 0"),
            ("second_tranche_pending", "BOOLEAN DEFAULT 0"),
            ("atr_at_buy", "REAL"),
            ("atr_period", "INTEGER DEFAULT 14"),
        ]
        with self.get_cursor() as cursor:
            for col_name, col_type in columns:
                if not self._has_column("portfolio", col_name):
                    cursor.execute(f"ALTER TABLE portfolio ADD COLUMN {col_name} {col_type}")

            # 기존 holding 백필 (200% 매수 사고 차단)
            cursor.execute(
                "UPDATE portfolio SET first_buy_price = buy_price WHERE first_buy_price IS NULL"
            )
            cursor.execute(
                "UPDATE portfolio SET avg_buy_price = buy_price WHERE avg_buy_price IS NULL"
            )
            cursor.execute(
                "UPDATE portfolio SET second_tranche_executed = 0 WHERE second_tranche_executed IS NULL"
            )
            cursor.execute(
                "UPDATE portfolio SET second_tranche_pending = 0 "
                "WHERE status='holding' AND second_tranche_pending IS NULL"
            )
            # atr_at_buy는 NULL 유지 (호출 측에서 0.0 폴백 → 트레일링 고정값 사용)

    def _migrate_v18(self) -> None:
        """텔레그램 수동 매수(/buy) 종목 식별용 is_manual 컬럼 추가.

        목적: 사용자가 직접 지정한 수동 매수 종목을 테마 로테이션 매도(화요일 재선정 +
        midweek 교체)에서 제외하기 위함. theme="수동" 마커 방식은 화요일 재선정의
        `h_theme not in selected_themes` 패턴에 걸려 매주 자동 청산되므로 기각하고,
        독립 BOOLEAN 컬럼으로 모든 경로에서 안전하게 식별한다.

        - is_manual: 1=수동 매수(로테이션 매도 제외), 0=자동 매수(기본).
          ⚠️ DEFAULT 0 — 기존 holding 및 자동 매수 종목은 기존 룰 그대로 적용.
          보유기간 만료 매도(run_hold_period_sells)는 is_manual 무관하게 적용된다.
        """
        with self.get_cursor() as cursor:
            if not self._has_column("portfolio", "is_manual"):
                cursor.execute("ALTER TABLE portfolio ADD COLUMN is_manual BOOLEAN DEFAULT 0")
            # 기존 행 백필 (NULL → 0): 기존 종목은 전부 자동 매수로 간주
            cursor.execute(
                "UPDATE portfolio SET is_manual = 0 WHERE is_manual IS NULL"
            )

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

    def get_score_fallback_for_theme(
        self, theme_name: str, target_date: date
    ) -> Optional[dict]:
        """
        selected=1 row의 momentum/ai/news가 0/NULL 박제일 때 폴백 데이터 조회.

        UNIQUE(date, theme_name) 제약으로 같은 (date, theme_name)에 selected=0/1 공존 불가 →
        직전 selected=1 정상값에서 폴백.

        Args:
            theme_name: 테마명
            target_date: 박제 발생한 날짜

        Returns:
            {"momentum", "supply_ratio", "news_count", "ai_sentiment"} dict
            또는 None (폴백 데이터 없음)
        """
        with self.get_cursor() as cursor:
            # 직전 selected=1 정상값 조회 (가장 최근부터)
            cursor.execute("""
                SELECT momentum, supply_ratio, news_count, ai_sentiment
                FROM themes
                WHERE theme_name = ? AND date < ? AND selected = 1
                  AND momentum IS NOT NULL AND momentum > 0
                ORDER BY date DESC LIMIT 1
            """, (theme_name, target_date))
            row = cursor.fetchone()
            if row:
                return dict(row)

            return None

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

    def get_theme_pass_rates(self, days: int = 7) -> dict:
        """
        최근 N일간 테마별 스크리닝 필터 통과율 조회

        screening_log 테이블에서 테마별 통과/탈락 집계를 수행합니다.

        Args:
            days: 참조할 과거 일수 (기본 7일)

        Returns:
            {theme_name: {"total": int, "passed": int, "pass_rate": float, "days_data": int}}
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    SELECT theme,
                           COUNT(*) as total,
                           SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) as passed_count,
                           COUNT(DISTINCT date) as days_data
                    FROM screening_log
                    WHERE date >= date('now', ? || ' days') AND stage = 'filter'
                    GROUP BY theme
                """, (str(-days),))
                rows = cursor.fetchall()

            result = {}
            for row in rows:
                theme = row["theme"]
                total = row["total"]
                passed = row["passed_count"]
                result[theme] = {
                    "total": total,
                    "passed": passed,
                    "pass_rate": passed / total if total > 0 else 0.0,
                    "days_data": row["days_data"],
                }
            logger.info(f"테마 통과율 조회: {len(result)}개 테마 ({days}일 룩백)")
            return result
        except Exception as e:
            logger.warning(f"테마 통과율 조회 실패: {e}")
            return {}

    def get_recent_theme_stock_codes(
        self, theme_name: str, days: int = 14, limit: int = 10
    ) -> list:
        """최근 screening_log에서 특정 테마의 종목코드를 복원 (supply_score_v2 fallback용).

        theme["stocks"]가 비어 supply_score_v2가 강제 0.0이 되는 문제(Phase 1-B½ Shadow
        Run noise)를 완화하기 위해, 최근 N일 screening_log에서 같은 테마로 관측된
        6자리 종목코드를 복원한다. 읽기 전용, 네트워크 미사용.

        Args:
            theme_name: 테마명 (정확 일치)
            days: 룩백 일수 (KST 기준, <=0이면 [])
            limit: 최대 반환 종목 수 (<=0이면 [])

        Returns:
            list[str]: 6자리 종목코드. passed 빈도·최근성·관측횟수 순 정렬.
                blank 테마 / 무데이터 / 예외 시 [].

        Note:
            컷오프는 Python 레벨에서 `now_kst()` 기준으로 계산하므로 SQLite `DATE('now')`
            (서버 UTC) 함정이 없다. KST 15:00~24:00 호출 시에도 당일 관측이 누락되지 않는다.
            `stage='filter'`(screener 단계)만 조회 — 테마→종목 매핑의 authoritative 출처이며
            ai_verify/gap_filter 하위 단계의 passed 부풀림 노이즈를 배제한다
            (`get_theme_pass_rates`와 동일 기준).
            passed=1을 우선 정렬하되, passed 데이터가 없어도 관측된 종목은 반환(보수적).
        """
        if not theme_name or not str(theme_name).strip():
            return []
        if days <= 0 or limit <= 0:
            return []
        try:
            cutoff = (now_kst().date() - timedelta(days=days)).isoformat()
            with self.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT stock_code,
                           MAX(date) AS last_seen,
                           SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed_count,
                           COUNT(*) AS seen_count
                    FROM screening_log
                    WHERE theme = ? AND date >= ? AND stage = 'filter'
                    GROUP BY stock_code
                    ORDER BY passed_count DESC, last_seen DESC, seen_count DESC
                    """,
                    (theme_name, cutoff),
                )
                rows = cursor.fetchall()

            codes: list = []
            for row in rows:
                code = row["stock_code"]
                if isinstance(code, str) and len(code) == 6 and code.isdigit():
                    codes.append(code)
                if len(codes) >= limit:
                    break
            return codes
        except Exception as e:
            logger.warning(f"get_recent_theme_stock_codes 실패 [{theme_name}]: {e}")
            return []

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

    def update_portfolio_buy_message(self, stock_code: str, message: str) -> int:
        """포트폴리오의 가장 최근 holding row에 매수 시점 텔레그램 메시지 저장.

        같은 종목 재매수 케이스에서 옛 행은 status='replaced'로 보존되므로
        가장 최근 holding row(id DESC LIMIT 1)만 갱신한다.

        Returns:
            업데이트된 row 수 (0이면 holding row 없음)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio SET buy_message = ?
                WHERE id = (
                    SELECT id FROM portfolio
                    WHERE stock_code = ? AND status = 'holding'
                    ORDER BY id DESC LIMIT 1
                )
            """, (message, stock_code))
            return cursor.rowcount

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
        """매수 체결 후 holding 포지션 저장 (v17: 분할 진입 컬럼 포함).

        Args:
            position: 포지션 정보 딕셔너리. 필수: stock_code, stock_name, shares, buy_price.
                선택 (v17): first_buy_price, avg_buy_price, atr_at_buy, atr_period,
                            second_tranche_pending, tranche_count.
                미전달 시 first/avg=buy_price 폴백, pending은 TRANCHE_ENTRY_ENABLED 추론.
        """
        shares = position.get('shares')
        buy_price = position.get('buy_price')
        stock_code = position['stock_code']

        # v17 컬럼 폴백 (호환성)
        first_buy_price = position.get('first_buy_price') or buy_price
        avg_buy_price = position.get('avg_buy_price') or buy_price
        atr_at_buy = position.get('atr_at_buy')  # NULL 허용
        atr_period = position.get('atr_period', 14)
        tranche_count = position.get('tranche_count', 1)
        # is_manual: 텔레그램 수동 매수(/buy) 종목 여부. 테마 로테이션 매도 제외용.
        is_manual = 1 if position.get('is_manual') else 0
        # second_tranche_pending: 명시 전달이 우선. 미전달 시 설정 토글 따라 결정.
        if 'second_tranche_pending' in position:
            second_tranche_pending = 1 if position['second_tranche_pending'] else 0
        else:
            try:
                from config import settings as _settings
                second_tranche_pending = 1 if _settings.TRANCHE_ENTRY_ENABLED else 0
            except Exception:
                second_tranche_pending = 0

        with self.get_cursor() as cursor:
            # 같은 종목의 기존 pending/holding 엔트리 정리
            cursor.execute("""
                UPDATE portfolio SET status = 'replaced'
                WHERE stock_code = ? AND status IN ('pending', 'holding')
            """, (stock_code,))

            cursor.execute("""
                INSERT INTO portfolio (
                    date, stock_code, stock_name, theme, weight,
                    shares, buy_price, current_price,
                    stop_loss, take_profit, status,
                    original_shares, buy_date,
                    first_buy_price, avg_buy_price,
                    tranche_count, second_tranche_executed, second_tranche_pending,
                    atr_at_buy, atr_period, is_manual
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'holding', ?, ?,
                          ?, ?, ?, 0, ?, ?, ?, ?)
            """, (
                position.get('date'),
                stock_code,
                position['stock_name'],
                position.get('theme'),
                position.get('weight'),
                shares,
                buy_price,
                buy_price,  # current_price = buy_price 초기값
                position.get('stop_loss'),
                position.get('take_profit'),
                shares,  # original_shares = shares
                position.get('date'),  # buy_date = date
                # v17 컬럼
                first_buy_price,
                avg_buy_price,
                tranche_count,
                second_tranche_pending,
                atr_at_buy,
                atr_period,
                is_manual,
            ))

        logger.info(
            f"포지션 저장: {position['stock_name']} (holding, tranche={tranche_count}, "
            f"first={first_buy_price}, avg={avg_buy_price}, atr={atr_at_buy}, "
            f"pending={second_tranche_pending}, manual={is_manual})"
        )

    def update_portfolio_second_tranche(
        self,
        stock_code: str,
        added_shares: int,
        second_filled_price: float,
        avg_buy_price: float,
    ) -> int:
        """v17 2차 진입(불타기) 체결 후 portfolio 업데이트.

        - shares / original_shares: 누적 가산
        - avg_buy_price: 가중평균으로 갱신
        - tranche_count = 2, second_tranche_executed = 1, second_tranche_pending = 0
        - first_buy_price는 불변 (손절/BE/트레일링 기준 유지)

        Args:
            stock_code: 종목코드
            added_shares: 2차 매수 체결 수량
            second_filled_price: 2차 체결가 (감사용, DB는 avg만 저장)
            avg_buy_price: 호출 측에서 계산한 가중평균 단가

        Returns:
            업데이트된 row 수 (0이면 holding row 없음 — race 경고)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio
                SET shares = shares + ?,
                    original_shares = original_shares + ?,
                    avg_buy_price = ?,
                    tranche_count = 2,
                    second_tranche_executed = 1,
                    second_tranche_pending = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (added_shares, added_shares, avg_buy_price, stock_code))
            rowcount = cursor.rowcount

        if rowcount == 0:
            logger.warning(
                f"[v17] 2차 진입 DB 업데이트 실패: {stock_code} - holding row 없음"
            )
        else:
            logger.info(
                f"[v17] 2차 진입 DB 업데이트: {stock_code} +{added_shares}주 @{second_filled_price}, "
                f"avg={avg_buy_price:.2f}"
            )
        return rowcount

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
        """스크리닝 로그 저장

        log 딕셔너리 선택 키:
            rsi_at_screen (float): 스크리닝 시점 RSI 값 (v14 추가)
            theme_slot_protected (bool/int): 테마 슬롯 보장 플래그 (v14 추가)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR IGNORE INTO screening_log (
                    date, stock_code, stock_name, theme, stage,
                    passed, score, reject_reason, details_json,
                    rsi_at_screen, theme_slot_protected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                log.get('rsi_at_screen'),
                int(bool(log.get('theme_slot_protected', 0))),
            ))

    # ===== 수급 신호 관련 메서드 (v16, supply-signal-integration Phase 1-A) =====

    def save_supply_snapshot(self, trade_date: date, stock_code: str, supply: dict) -> None:
        """종목별 일별 수급 스냅샷 저장 (FHKST01010900 결과).

        supply 딕셔너리 구조:
            foreign_net (int): 외국인 5일 순매수 (원)
            institution_net (int): 기관 5일 순매수 (원)
            individual_net (int): 개인 5일 순매수 (원)
            foreign_ratio (float): 외국인 보유율 (%)
            daily (list[dict]): 일별 상세 (date, foreign, institution, close_price)

        멱등성: UNIQUE(trade_date, stock_code) + INSERT OR REPLACE
        """
        # T-1 당일치 + 5일 평균 거래대금 계산
        daily = supply.get("daily", []) or []
        foreign_1d = None
        institution_1d = None
        close_price = None
        trade_value_5d_avg = None
        if daily:
            first = daily[0] or {}
            foreign_1d_qty = first.get("foreign") or 0
            institution_1d_qty = first.get("institution") or 0
            close_price = first.get("close_price") or 0
            # 일별 순매수금액 = 수량 * 종가
            if close_price:
                foreign_1d = foreign_1d_qty * close_price
                institution_1d = institution_1d_qty * close_price
            # 5일 거래대금 평균 (수량 * 종가 절대값으로 근사 — 정확한 거래대금 별도 TR 필요)
            # 근사적으로 외국인+기관+개인 거래량 × 종가로 추정
            try:
                values = []
                for d in daily:
                    cp = d.get("close_price") or 0
                    if cp <= 0:
                        continue
                    total_qty = abs((d.get("foreign") or 0)) + abs((d.get("institution") or 0)) + abs((d.get("individual") or 0))
                    if total_qty > 0:
                        values.append(total_qty * cp)
                if values:
                    trade_value_5d_avg = sum(values) / len(values)
            except Exception:
                trade_value_5d_avg = None

        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO daily_supply_snapshot (
                    trade_date, stock_code, stock_name,
                    foreign_net_5d, institution_net_5d, individual_net_5d, foreign_ratio,
                    foreign_net_1d, institution_net_1d, close_price, trade_value_5d_avg,
                    source, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                trade_date.isoformat() if isinstance(trade_date, date) else trade_date,
                stock_code,
                supply.get("stock_name"),
                supply.get("foreign_net"),
                supply.get("institution_net"),
                supply.get("individual_net"),
                supply.get("foreign_ratio"),
                foreign_1d,
                institution_1d,
                close_price if close_price else None,
                trade_value_5d_avg,
                supply.get("source", "FHKST01010900"),
            ))

    def save_supply_snapshot_null(self, trade_date: date, stock_code: str,
                                   stock_name: Optional[str] = None) -> None:
        """수집 실패 종목의 NULL 행 저장 (graceful fallback 표시용).

        멱등성: INSERT OR REPLACE
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO daily_supply_snapshot (
                    trade_date, stock_code, stock_name, source, collected_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                trade_date.isoformat() if isinstance(trade_date, date) else trade_date,
                stock_code,
                stock_name,
                "FHKST01010900_FAILED",
            ))

    def save_foreign_top_ranking(self, trade_date: date, kind: str,
                                  items: list[dict]) -> None:
        """외국인/기관 순매수 상위 N 종목 저장 (FHPTJ04400000 결과).

        Args:
            trade_date: 거래일
            kind: 'foreign_buy' / 'foreign_sell' / 'institution_buy' / 'institution_sell'
            items: [{stock_code, stock_name, net_amount}, ...] (rank 순)

        멱등성: UNIQUE(trade_date, kind, rank) + INSERT OR REPLACE
        """
        if not items:
            return
        td = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
        with self.get_cursor() as cursor:
            # 같은 trade_date+kind는 통째로 교체 (rank가 변할 수 있음)
            cursor.execute(
                "DELETE FROM foreign_top_ranking WHERE trade_date = ? AND kind = ?",
                (td, kind)
            )
            for rank_idx, item in enumerate(items, start=1):
                code = item.get("stock_code") or item.get("code")
                if not code:
                    continue
                cursor.execute("""
                    INSERT INTO foreign_top_ranking (
                        trade_date, kind, rank, stock_code, stock_name,
                        net_amount, source, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    td,
                    kind,
                    rank_idx,
                    code,
                    item.get("stock_name"),
                    item.get("net_amount"),
                    item.get("source", "FHPTJ04400000"),
                ))

    def save_supply_score_observation(self, obs_date: date, theme_name: str,
                                       supply_score_v2: float, momentum_score: float,
                                       news_score: float, ai_score: float,
                                       theme_total_actual: float,
                                       breakdown_json: Optional[str] = None) -> None:
        """Shadow Run 관측 모드 — supply_score를 계산하되 총점 미반영, DB에만 기록.

        Phase 1-B½ (Day 6~19)에 운영하며, 14영업일 후 모멘텀×supply 상관계수 r<0.7
        검증 후 Phase 1-C에서 활성화 결정.

        멱등성: UNIQUE(obs_date, theme_name) + INSERT OR REPLACE
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO supply_score_observation (
                    obs_date, theme_name, supply_score_v2, momentum_score,
                    news_score, ai_score, theme_total_actual, breakdown_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                obs_date.isoformat() if isinstance(obs_date, date) else obs_date,
                theme_name,
                supply_score_v2,
                momentum_score,
                news_score,
                ai_score,
                theme_total_actual,
                breakdown_json,
            ))

    def get_supply_snapshot(self, stock_code: str,
                             trade_date: Optional[date] = None) -> Optional[dict]:
        """종목 수급 스냅샷 조회. trade_date=None이면 가장 최근 영업일.

        Returns:
            dict 또는 None (데이터 없음)
        """
        with self.get_cursor() as cursor:
            if trade_date is None:
                cursor.execute("""
                    SELECT * FROM daily_supply_snapshot
                    WHERE stock_code = ?
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code,))
            else:
                td = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
                cursor.execute("""
                    SELECT * FROM daily_supply_snapshot
                    WHERE stock_code = ? AND trade_date = ?
                """, (stock_code, td))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_latest_supply_snapshot_date(self) -> Optional[str]:
        """가장 최근 daily_supply_snapshot trade_date (ISO 문자열) 또는 None."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT MAX(trade_date) FROM daily_supply_snapshot"
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def count_supply_snapshots_for_date(self, trade_date: date) -> int:
        """특정 거래일의 daily_supply_snapshot 행 개수 (운영 모니터링용)."""
        td = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM daily_supply_snapshot WHERE trade_date = ?",
                (td,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_supply_snapshots_bulk(self, stock_codes: list[str],
                                    trade_date: Optional[date] = None) -> dict[str, dict]:
        """다수 종목 수급 스냅샷 일괄 조회 (Phase 1-B, 09:05 스크리닝용).

        Args:
            stock_codes: 종목코드 리스트 (6자리)
            trade_date: 거래일. None이면 각 종목별 가장 최근 영업일 자동 선택.

        Returns:
            {stock_code: snapshot_dict} 딕셔너리. 데이터 없는 종목은 키 누락.
        """
        if not stock_codes:
            return {}
        placeholders = ",".join("?" * len(stock_codes))
        result: dict[str, dict] = {}
        with self.get_cursor() as cursor:
            if trade_date is None:
                # 각 종목별 가장 최근 영업일 (서브쿼리로 MAX)
                cursor.execute(
                    f"""SELECT s.* FROM daily_supply_snapshot s
                        INNER JOIN (
                            SELECT stock_code, MAX(trade_date) AS max_date
                            FROM daily_supply_snapshot
                            WHERE stock_code IN ({placeholders})
                            GROUP BY stock_code
                        ) latest
                        ON s.stock_code = latest.stock_code AND s.trade_date = latest.max_date""",
                    list(stock_codes)
                )
            else:
                td = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
                cursor.execute(
                    f"""SELECT * FROM daily_supply_snapshot
                        WHERE stock_code IN ({placeholders}) AND trade_date = ?""",
                    [*stock_codes, td]
                )
            for row in cursor.fetchall():
                d = dict(row)
                result[d["stock_code"]] = d
        return result

    def get_theme_supply_aggregate(self, stock_codes: list[str],
                                    trade_date: Optional[date] = None) -> dict:
        """테마 종목 리스트의 외국인/기관 평균 + 양수 비율 집계 (Phase 1-B, 08:30 테마 분석용).

        Args:
            stock_codes: 테마에 속한 종목코드 리스트 (보통 상위 5~8개)
            trade_date: 거래일. None이면 각 종목별 가장 최근 영업일.

        Returns:
            {'foreign_avg': float (원), 'institution_avg': float (원),
             'foreign_pos_ratio': float (%, 0~100), 'institution_pos_ratio': float,
             'n': int (실제 스냅샷 있는 종목 수)}
        """
        snapshots = self.get_supply_snapshots_bulk(stock_codes, trade_date)
        if not snapshots:
            return {
                "foreign_avg": 0.0, "institution_avg": 0.0,
                "foreign_pos_ratio": 0.0, "institution_pos_ratio": 0.0,
                "n": 0,
            }
        foreign_vals = [s.get("foreign_net_5d") for s in snapshots.values()
                        if s.get("foreign_net_5d") is not None]
        inst_vals = [s.get("institution_net_5d") for s in snapshots.values()
                     if s.get("institution_net_5d") is not None]

        def _safe_avg(vals: list[float]) -> float:
            return sum(vals) / len(vals) if vals else 0.0

        def _pos_ratio(vals: list[float]) -> float:
            return (100.0 * sum(1 for v in vals if v > 0) / len(vals)) if vals else 0.0

        return {
            "foreign_avg": _safe_avg(foreign_vals),
            "institution_avg": _safe_avg(inst_vals),
            "foreign_pos_ratio": _pos_ratio(foreign_vals),
            "institution_pos_ratio": _pos_ratio(inst_vals),
            "n": len(snapshots),
        }

    def is_in_foreign_top(self, stock_code: str, kind: str = "foreign_buy",
                           trade_date: Optional[date] = None) -> Optional[int]:
        """foreign_top_ranking에서 종목의 순위 반환. 미진입이면 None.

        Args:
            stock_code: 6자리 종목코드
            kind: 'foreign_buy' / 'foreign_sell' / 'institution_buy' / 'institution_sell'
            trade_date: 거래일. None이면 가장 최근 영업일.

        Returns:
            rank (1~) 또는 None
        """
        with self.get_cursor() as cursor:
            if trade_date is None:
                cursor.execute(
                    "SELECT rank FROM foreign_top_ranking "
                    "WHERE stock_code = ? AND kind = ? "
                    "ORDER BY trade_date DESC LIMIT 1",
                    (stock_code, kind)
                )
            else:
                td = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
                cursor.execute(
                    "SELECT rank FROM foreign_top_ranking "
                    "WHERE stock_code = ? AND kind = ? AND trade_date = ?",
                    (stock_code, kind, td)
                )
            row = cursor.fetchone()
            return row[0] if row else None

    def update_portfolio_supply_context(self, stock_code: str, ctx: dict) -> int:
        """매수 직후 portfolio 행에 supply 컨텍스트 박제 (Phase 1-D 매수 hook용).

        Phase 1-B에서는 헬퍼만 추가하고 호출은 Phase 1-D에서. 미리 작성하여
        스키마/시그니처 안정성 확보.

        Args:
            stock_code: 종목코드
            ctx: 다음 화이트리스트 키만 인식 (외 키는 silent ignore — SQL injection 방어):
                - foreign_net_at_buy (REAL): 외국인 5일 순매수 (원). **portfolio 컬럼명**
                - institution_net_at_buy (REAL): 기관 5일 순매수 (원). **portfolio 컬럼명**
                - supply_strength_at_buy (REAL): 0~2 정규화 강도
                - foreign_top_rank_at_buy (INTEGER): 외인 TOP 진입 순위 (미진입 None)
                - institution_consec_days_at_buy (INTEGER): 기관 연속 매수일수
                - theme_supply_score_at_buy (REAL): 테마 supply 점수 (Phase 1-C)
                - ai_supply_signal_at_buy (TEXT): AI 응답의 supply_signal_strength

        Returns:
            UPDATE된 행 수 (보통 1, 종목 미보유 시 0)

        Note:
            **컬럼명 매핑 주의**: trade_reviews는 동일 의미를 `_5d_at_buy` 접미사로 보존
            (예: `foreign_net_5d_at_buy`). Phase 1-D `save_trade_review` 자동 보강 시
            portfolio.foreign_net_at_buy → trade_reviews.foreign_net_5d_at_buy로 매핑 필요.
            (이름 불일치는 v16 마이그레이션 시점 결정 — portfolio는 매수 시점 박제용,
            trade_reviews는 사후 분석 명세상 `5d` 명시).
        """
        if not ctx:
            return 0
        # 화이트리스트로 SQL injection 방어
        allowed = {
            "foreign_net_at_buy", "institution_net_at_buy", "supply_strength_at_buy",
            "foreign_top_rank_at_buy", "institution_consec_days_at_buy",
            "theme_supply_score_at_buy", "ai_supply_signal_at_buy",
        }
        filtered = {k: v for k, v in ctx.items() if k in allowed}
        if not filtered:
            return 0
        sets = ", ".join(f"{k} = ?" for k in filtered.keys())
        sql = f"UPDATE portfolio SET {sets} WHERE stock_code = ? AND status = 'holding'"
        values = list(filtered.values()) + [stock_code]
        with self.get_cursor() as cursor:
            cursor.execute(sql, values)
            return cursor.rowcount

    def get_supply_attribution_data(self, days: int = 14) -> list[dict]:
        """trade_reviews에서 supply 신호 적중률 분석용 데이터 슬라이스 (Phase 2 일일 리포트용).

        Phase 1-B에서는 헬퍼만 추가하고 사용은 Phase 2에서.

        Args:
            days: 최근 N일 매도 (sell_date 기준, KST 영업일)

        Returns:
            list of dict: stock_code, stock_name, theme, profit_rate, hold_days,
                supply_strength_at_buy, foreign_net_5d_at_buy 등

        Note:
            SQLite `DATE('now')`는 서버 UTC 기준이라 KST 15:00~24:00 사이 호출 시
            오늘 매도 데이터가 누락될 수 있음. 따라서 `now_kst()` 기준으로 컷오프 계산.
        """
        cutoff = (now_kst().date() - timedelta(days=days)).isoformat()
        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT stock_code, stock_name, theme, profit_rate, hold_days,
                          foreign_net_5d_at_buy, institution_net_5d_at_buy,
                          supply_strength_at_buy, foreign_top_rank_at_buy,
                          institution_consec_days_at_buy, theme_supply_score_at_buy,
                          ai_supply_signal_at_buy,
                          foreign_direction_match, institution_direction_match
                   FROM trade_reviews
                   WHERE sell_date >= ?
                   ORDER BY sell_date DESC""",
                (cutoff,)
            )
            return [dict(row) for row in cursor.fetchall()]

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
