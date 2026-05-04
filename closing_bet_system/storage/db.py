"""closing_bet_system.storage.db

종가베팅 시스템 전용 SQLite 관리.
- 별도 DB 파일 (기본: data/closing_bet.db)
- 마이그레이션 패턴은 프로젝트 루트 ``database.py:150`` 의 ``_migrate()`` 와 동일
- v1 마이그레이션: candidates / candidate_features / candidate_labels / flow_data_reliability 4 테이블

직접 실행 (DB 초기화):
    venv/bin/python -m closing_bet_system.storage.db --init
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import yaml

# 프로젝트 루트를 path에 추가 (단독 실행 대응)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


_DEFAULT_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
)
_DEFAULT_DB_PATH = "data/closing_bet.db"


def _load_settings(settings_path: Optional[Path] = None) -> dict:
    """settings.yaml 로드. 파일 없음 / YAML 문법 오류 시 빈 dict 반환."""
    path = settings_path or _DEFAULT_SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"settings.yaml 없음 (폴백 기본값 사용): {path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"settings.yaml YAML 문법 오류 (폴백 기본값 사용): {path} — {e}")
        return {}


def _resolve_db_path(settings: dict) -> Path:
    """settings.yaml의 db.path를 프로젝트 루트 기준 절대 경로로 변환."""
    rel_path = settings.get("db", {}).get("path", _DEFAULT_DB_PATH)
    db_path = Path(rel_path)
    if not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    return db_path


class ClosingBetDatabase:
    """종가베팅 시스템 SQLite 관리.

    프로젝트 루트의 :class:`database.Database` 와 동일한 마이그레이션 패턴을 사용한다.
    별도 DB 파일이지만 schema_version 기반 멱등 마이그레이션 + 자동 백업.
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            settings = _load_settings()
            self.db_path = _resolve_db_path(settings)
        else:
            self.db_path = Path(db_path)
            if not self.db_path.is_absolute():
                self.db_path = _PROJECT_ROOT / self.db_path

        self.conn: Optional[sqlite3.Connection] = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> None:
        try:
            self.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0,
            )
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            logger.info(f"종가베팅 DB 연결: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"종가베팅 DB 연결 실패: {e}")
            raise

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("종가베팅 DB 연결 종료")

    @contextmanager
    def get_cursor(self):
        if not self.conn:
            raise RuntimeError("DB가 연결되지 않았습니다. connect()를 먼저 호출하세요.")
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except Exception as e:
            # sqlite3.Error 외 LookupError / ValueError (호출 측이 cursor.execute 후 raise)
            # 케이스에서도 rollback 보장 — 트랜잭션 미정 상태 누적 방지.
            # 1-6 candidate_logger 의 mark_entered / log_exit 가 rowcount==0 시 LookupError 발생.
            self.conn.rollback()
            logger.error(f"종가베팅 DB 작업 실패: {e}")
            raise
        finally:
            cursor.close()

    # ===== Public API =====

    def init_tables(self) -> None:
        """스키마 초기화 + 마이그레이션. 멱등."""
        if not self.conn:
            self.connect()
        self._migrate()

    # ===== Migration Internals =====

    def _has_table(self, table: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _get_schema_version(self) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if not cursor.fetchone():
                return 0
            cursor.execute("SELECT MAX(version) AS v FROM schema_version")
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0
        finally:
            cursor.close()

    def _migrate(self) -> None:
        """멱등 마이그레이션. database.py:150 패턴과 동일."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        current = self._get_schema_version()
        migrations = [
            (1, "Phase 0-A: candidates/features/labels/flow_reliability 4테이블", self._migrate_v1),
        ]

        pending = [(v, d, fn) for v, d, fn in migrations if v > current]
        if not pending:
            return

        # DB 백업 (WAL/SHM 포함). 신규 빈 DB는 스킵 (current==0이면 백업할 데이터 없음).
        if self.db_path.exists() and self.db_path.stat().st_size > 0 and current > 0:
            backup_path = self.db_path.with_suffix(
                f".bak.{now_kst().strftime('%Y%m%d_%H%M%S')}"
            )
            try:
                shutil.copy2(str(self.db_path), str(backup_path))
                wal_path = Path(str(self.db_path) + "-wal")
                shm_path = Path(str(self.db_path) + "-shm")
                if wal_path.exists():
                    shutil.copy2(str(wal_path), str(backup_path) + "-wal")
                if shm_path.exists():
                    shutil.copy2(str(shm_path), str(backup_path) + "-shm")
                logger.info(f"종가베팅 DB 백업 완료: {backup_path}")
            except Exception as e:
                logger.warning(f"DB 백업 실패 (마이그레이션 계속): {e}")

        for version, description, migrate_fn in pending:
            try:
                migrate_fn()
                with self.get_cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                        (version, description),
                    )
                logger.info(f"종가베팅 DB 마이그레이션 v{version} 적용: {description}")
            except Exception as e:
                logger.error(f"종가베팅 DB 마이그레이션 v{version} 실패: {e}")
                raise

    def _migrate_v1(self) -> None:
        """v1: 4테이블 + 인덱스 (PRD 11-1 스키마)."""
        with self.get_cursor() as cursor:
            # candidates: 후보 마스터 (모든 status 저장)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT NOT NULL,
                    candidate_status TEXT NOT NULL,
                    rejection_reason TEXT,

                    layer1_score INTEGER,
                    layer2_score INTEGER,
                    layer3_score INTEGER,
                    external_risk_score INTEGER,
                    total_score INTEGER,

                    entry_price REAL,
                    entry_amount REAL,
                    entry_time TIMESTAMP,

                    exit_price REAL,
                    exit_time TIMESTAMP,

                    buy_commission REAL,
                    sell_commission REAL,
                    transaction_tax REAL,
                    estimated_slippage REAL,
                    net_pnl_pct REAL,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    CHECK (candidate_status IN (
                        'recommended', 'entered', 'rejected_filter', 'rejected_manual'
                    ))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_trade_date ON candidates(trade_date)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_ticker ON candidates(ticker)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(candidate_status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_date_status "
                "ON candidates(trade_date, candidate_status)"
            )

            # candidate_features: 진입 시점 피처 스냅샷
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_features (
                    candidate_id INTEGER PRIMARY KEY,

                    -- Layer 1 수급
                    inst_net_buy_estimated REAL,
                    foreign_net_buy_3d REAL,
                    program_net_buy_change REAL,
                    closing_flow_concentration REAL,

                    -- Layer 2 가격/거래량
                    close_strength REAL,
                    upper_shadow_atr REAL,
                    last_30min_vwap_position REAL,
                    closing_buy_sell_ratio REAL,
                    volume_surprise REAL,
                    atr_overheat REAL,

                    -- Layer 3 모멘텀
                    days_from_52w_high INTEGER,
                    relative_strength_5d REAL,
                    theme_leadership_rank INTEGER,

                    -- 시장 레짐
                    kospi_above_200ma BOOLEAN,
                    vkospi REAL,
                    foreign_5d_cumulative REAL,
                    us_futures_change REAL,
                    usd_krw_change REAL,

                    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
                        ON DELETE CASCADE
                )
                """
            )

            # candidate_labels: T+1 09:30 데이터로 사후 채움
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_labels (
                    candidate_id INTEGER PRIMARY KEY,

                    next_open_pct REAL,
                    next_morning_high_pct REAL,
                    next_morning_low_pct REAL,

                    label_gap_up BOOLEAN,
                    label_morning_exit BOOLEAN,
                    label_stop_risk BOOLEAN,
                    label_net_ev_positive BOOLEAN,

                    labeled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
                        ON DELETE CASCADE
                )
                """
            )

            # flow_data_reliability: 추정값 vs KRX 확정값 (Layer 1 활성화 트리거)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_data_reliability (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    ticker TEXT NOT NULL,

                    inst_estimated REAL,
                    inst_confirmed REAL,
                    foreign_estimated REAL,
                    foreign_confirmed REAL,

                    inst_direction_match BOOLEAN,
                    foreign_direction_match BOOLEAN,

                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    UNIQUE(trade_date, ticker)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_flow_reliability_date "
                "ON flow_data_reliability(trade_date)"
            )


def init_db(db_path: Optional[str | Path] = None) -> ClosingBetDatabase:
    """모듈 외부 호출용 헬퍼: DB 생성/연결/마이그레이션 일괄 처리.

    반환된 DB는 ``connect()`` 상태이므로 호출 측에서 ``close()`` 를 책임진다.
    """
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()
    return db


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="종가베팅 DB 초기화 / 검증")
    parser.add_argument("--init", action="store_true", help="DB 초기화 + 마이그레이션 실행")
    parser.add_argument("--check", action="store_true", help="현재 스키마 버전 + 테이블 목록 출력")
    parser.add_argument("--db-path", type=str, default=None, help="DB 경로 오버라이드")
    args = parser.parse_args()

    db = ClosingBetDatabase(args.db_path)
    try:
        db.connect()
        if args.init:
            db.init_tables()
            print(f"[OK] 종가베팅 DB 초기화 완료: {db.db_path}")
            print(f"     스키마 버전: v{db._get_schema_version()}")
        elif args.check:
            version = db._get_schema_version()
            with db.get_cursor() as cur:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [row[0] for row in cur.fetchall()]
            print(f"[OK] 종가베팅 DB: {db.db_path}")
            print(f"     스키마 버전: v{version}")
            print(f"     테이블({len(tables)}): {', '.join(tables)}")
        else:
            parser.print_help()
    finally:
        db.close()


if __name__ == "__main__":
    main()
