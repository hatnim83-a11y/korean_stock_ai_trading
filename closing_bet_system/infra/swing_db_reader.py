"""closing_bet_system.infra.swing_db_reader

스윙 시스템 DB(``data/trading.db``) read-only 조회 wrapper.

종가베팅이 스윙 보유 종목과 중복 진입하지 않도록 ``portfolio`` 테이블을 조회한다.
``mode=ro`` 옵션으로 SQLite 연결 — 쓰기 시도 시 ``OperationalError`` 발생.

스윙 DB 스키마 의존 (database.py 참조):
- ``portfolio`` 테이블의 ``stock_code``, ``status='holding'`` 컬럼
- ``trades`` 테이블의 ``date`` (DATE), ``action='buy'`` (오늘 진입 카운트)
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

from closing_bet_system.storage.db import _load_settings


_DEFAULT_SWING_DB = "data/trading.db"


def _resolve_swing_db_path() -> Path:
    """settings.yaml 의 ``db.swing_db_path`` 를 절대 경로로 변환."""
    settings = _load_settings()
    rel = settings.get("db", {}).get("swing_db_path", _DEFAULT_SWING_DB)
    p = Path(rel)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def _open_readonly(swing_db_path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """스윙 DB read-only 연결. 파일 없으면 None.

    URI 형식 ``file:...?mode=ro`` 으로 강제 read-only.
    """
    path = swing_db_path or _resolve_swing_db_path()
    if not path.exists():
        logger.warning(f"[종가베팅] 스윙 DB 없음 (스윙 미운용 또는 미설치): {path}")
        return None
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"[종가베팅] 스윙 DB read-only 연결 실패: {e}")
        return None


def get_swing_holding_codes() -> set[str]:
    """스윙 시스템이 현재 보유 중인 종목코드 집합 (status='holding').

    종가베팅 후보 선정 시 이 집합과 교집합인 종목은 자동 제외 (이중 노출 방지).
    실패 시 빈 set 반환 (보수적이지 않음 — 스윙 보유 종목이라도 진입 가능해짐).
    """
    conn = _open_readonly()
    if conn is None:
        return set()
    try:
        cur = conn.cursor()
        cur.execute("SELECT stock_code FROM portfolio WHERE status = 'holding'")
        codes = {row[0] for row in cur.fetchall() if row[0]}
        return codes
    except sqlite3.Error as e:
        logger.error(f"[종가베팅] 스윙 보유 종목 조회 실패: {e}")
        return set()
    finally:
        conn.close()


def get_swing_today_buy_count() -> int:
    """스윙이 오늘 매수한 종목 수 (KST 기준).

    동일 KIS 계좌의 일일 주문 수 모니터링 용도.
    """
    conn = _open_readonly()
    if conn is None:
        return 0
    try:
        today = now_kst().date().isoformat()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM trades WHERE date = ? AND action = 'buy'",
            (today,),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] else 0
    except sqlite3.Error as e:
        logger.error(f"[종가베팅] 스윙 오늘 매수 수 조회 실패: {e}")
        return 0
    finally:
        conn.close()
