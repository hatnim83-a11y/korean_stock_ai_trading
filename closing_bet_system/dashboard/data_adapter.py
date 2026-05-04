"""closing_bet_system.dashboard.data_adapter

종가베팅 대시보드용 데이터 어댑터 (Phase 2-6).

기존 ``web/`` 인프라(FastAPI + Jinja2 + 인증/SSE) 를 재사용하면서, 종가베팅 전용
``closing_bet.db`` 조회 헬퍼를 모아 web/api_routes.py 에서 호출한다.

**특징**:
- 모든 함수는 read-only (SELECT 전용)
- closing_bet.db 가 비어있어도 빈 dict / 0 카운트 정상 반환 (KeyError 금지)
- DB 미존재 / 스키마 미생성 등 예외 흡수 → 빈 결과 + 경고 로그

**호출 위치**:
- ``web/api_routes.py`` 신규 5개 엔드포인트 (/api/v1/closing-bet/...)
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== DB 경로 해석 =====


def _resolve_db_path() -> Path:
    """closing_bet.db 절대 경로 반환. settings.yaml 사용."""
    try:
        from closing_bet_system.storage.db import _load_settings, _resolve_db_path
        return _resolve_db_path(_load_settings())
    except Exception as e:
        logger.warning(f"[dashboard.data_adapter] DB 경로 해석 실패, 기본값 사용: {e}")
        return _PROJECT_ROOT / "data" / "closing_bet.db"


def _open_ro() -> Optional[sqlite3.Connection]:
    """closing_bet.db read-only 연결. DB 미존재 / 실패 시 None."""
    db_path = _resolve_db_path()
    if not db_path.exists():
        logger.debug(f"[dashboard.data_adapter] closing_bet.db 미존재: {db_path}")
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True,
            check_same_thread=False, timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning(f"[dashboard.data_adapter] DB 연결 실패: {e}")
        return None


# ===== 5 API 엔드포인트 데이터 =====


def get_today_candidates() -> dict:
    """오늘 후보 status별 카운트 + 리스트.

    Returns:
        ``{"date", "status_counts", "candidates": [...]}``
    """
    today = now_kst().date().isoformat()
    result = {
        "date": today,
        "status_counts": {
            "recommended": 0, "entered": 0,
            "rejected_filter": 0, "rejected_manual": 0,
        },
        "candidates": [],
    }
    conn = _open_ro()
    if conn is None:
        return result

    try:
        cur = conn.cursor()
        # status별 카운트
        cur.execute(
            """
            SELECT candidate_status, COUNT(*) as cnt
            FROM candidates
            WHERE trade_date = ?
            GROUP BY candidate_status
            """,
            (today,),
        )
        for row in cur.fetchall():
            status = row["candidate_status"]
            if status in result["status_counts"]:
                result["status_counts"][status] = int(row["cnt"])

        # 오늘 후보 리스트 (점수 순)
        cur.execute(
            """
            SELECT candidate_id, ticker, name, candidate_status,
                   layer1_score, layer2_score, layer3_score, total_score,
                   external_risk_score, rejection_reason
            FROM candidates
            WHERE trade_date = ?
            ORDER BY total_score DESC NULLS LAST, candidate_id ASC
            LIMIT 50
            """,
            (today,),
        )
        result["candidates"] = [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.warning(f"[dashboard.data_adapter] today 조회 예외: {e}")
    finally:
        conn.close()

    return result


def get_gate_progress() -> dict:
    """운영 점검 게이트 진척도 (30건 / 15영업일 / 20종목).

    Phase 1 carryover memory 의 1-9 게이트 기준.
    """
    today = now_kst().date()
    start_30d = today - timedelta(days=60)  # 30 영업일 ~ 60 캘린더일
    result = {
        "target": {
            "recommended": 30,
            "business_days": 15,
            "distinct_stocks": 20,
        },
        "actual": {
            "recommended": 0,
            "business_days": 0,
            "distinct_stocks": 0,
        },
        "passed": False,
        "since": start_30d.isoformat(),
    }
    conn = _open_ro()
    if conn is None:
        return result

    try:
        cur = conn.cursor()
        # 누적 recommended
        cur.execute(
            """
            SELECT COUNT(*) FROM candidates
            WHERE candidate_status = 'recommended'
              AND trade_date >= ?
            """,
            (start_30d.isoformat(),),
        )
        row = cur.fetchone()
        result["actual"]["recommended"] = int(row[0]) if row and row[0] else 0

        # 영업일 다양성 (서로 다른 trade_date)
        cur.execute(
            """
            SELECT COUNT(DISTINCT trade_date) FROM candidates
            WHERE candidate_status IN ('recommended', 'entered')
              AND trade_date >= ?
            """,
            (start_30d.isoformat(),),
        )
        row = cur.fetchone()
        result["actual"]["business_days"] = int(row[0]) if row and row[0] else 0

        # 종목 다양성
        cur.execute(
            """
            SELECT COUNT(DISTINCT ticker) FROM candidates
            WHERE candidate_status IN ('recommended', 'entered')
              AND trade_date >= ?
            """,
            (start_30d.isoformat(),),
        )
        row = cur.fetchone()
        result["actual"]["distinct_stocks"] = int(row[0]) if row and row[0] else 0

        # 게이트 통과 여부 (3 조건 AND)
        result["passed"] = (
            result["actual"]["recommended"] >= result["target"]["recommended"]
            and result["actual"]["business_days"] >= result["target"]["business_days"]
            and result["actual"]["distinct_stocks"] >= result["target"]["distinct_stocks"]
        )
    except sqlite3.Error as e:
        logger.warning(f"[dashboard.data_adapter] gate-progress 예외: {e}")
    finally:
        conn.close()

    return result


def get_orderbook_history(days: int = 1, limit: int = 200) -> dict:
    """최근 N일 호가 스냅샷 요약 (Phase 2-1 데이터).

    Returns:
        ``{"days", "snapshots": [{ticker, snapshot_time, ask1, bid1, spread_pct}, ...]}``
    """
    end = now_kst().date()
    start = end - timedelta(days=days)
    result = {"days": days, "snapshots": []}
    conn = _open_ro()
    if conn is None:
        return result

    try:
        cur = conn.cursor()
        # orderbook_snapshots 테이블이 v2 마이그레이션 후에만 존재
        cur.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='orderbook_snapshots'
            """
        )
        if cur.fetchone() is None:
            return result

        # snapshot_time 은 ISO8601 datetime str (예: "2026-05-04T15:10:05+09:00") 로 저장됨.
        # date.isoformat() 은 "YYYY-MM-DD" 라 사전순 비교가 우연히 맞지만, 의도 명확화 위해
        # T00:00:00 suffix 추가 (날짜 경계 정확).
        start_iso = start.isoformat() + "T00:00:00"
        cur.execute(
            """
            SELECT ticker, snapshot_time, ask1, bid1, current_price, spread_pct
            FROM orderbook_snapshots
            WHERE snapshot_time >= ?
              AND is_valid = 1
            ORDER BY snapshot_time DESC
            LIMIT ?
            """,
            (start_iso, limit),
        )
        result["snapshots"] = [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.warning(f"[dashboard.data_adapter] orderbook_history 예외: {e}")
    finally:
        conn.close()

    return result


def get_recent_rejections(days: int = 7, limit: int = 50) -> dict:
    """최근 N일 rejected_filter 후보 (외부 리스크/DART/KIND/atr_overheat 등 사유).

    Returns:
        ``{"days", "rejections": [{trade_date, ticker, name, rejection_reason}, ...]}``
    """
    end = now_kst().date()
    start = end - timedelta(days=days)
    result = {"days": days, "rejections": []}
    conn = _open_ro()
    if conn is None:
        return result

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT trade_date, ticker, name, candidate_status, rejection_reason
            FROM candidates
            WHERE candidate_status IN ('rejected_filter', 'rejected_manual')
              AND trade_date >= ?
            ORDER BY trade_date DESC, candidate_id DESC
            LIMIT ?
            """,
            (start.isoformat(), limit),
        )
        result["rejections"] = [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.warning(f"[dashboard.data_adapter] rejections 예외: {e}")
    finally:
        conn.close()

    return result


def get_fund_guard_status() -> dict:
    """fund_guard 현재 상태 — 자금 사용/한도, 활성 포지션, 주간 손실.

    실제 한도는 KIS 계좌 총자산이 필요한데, 본 어댑터는 read-only DB만 보므로
    한도는 settings.yaml 비율로 계산하지 않고 fund.capital_ratio 만 노출.
    KIS 호출은 web/dashboard_service.py 에서 별도 처리 가능 (본 단위 외).
    """
    result = {
        "active_amount": 0,
        "active_tickers": [],
        "today_entries": 0,
        "weekly_pnl_pct": None,
        "config": {
            "capital_ratio": 0.10,
            "max_concurrent_positions": 4,
            "max_daily_entries": 2,
            "weekly_loss_limit": -0.05,
        },
    }
    # config 로드
    try:
        from closing_bet_system.infra.fund_guard import GuardConfig
        cfg = GuardConfig.from_settings()
        result["config"] = {
            "capital_ratio": cfg.capital_ratio,
            "max_concurrent_positions": cfg.max_concurrent_positions,
            "max_daily_entries": cfg.max_daily_entries,
            "weekly_loss_limit": cfg.weekly_loss_limit,
        }
    except Exception as e:
        logger.debug(f"[dashboard.data_adapter] GuardConfig 로드 실패: {e}")

    conn = _open_ro()
    if conn is None:
        return result

    try:
        cur = conn.cursor()
        # 활성 포지션 (entered + 미청산)
        cur.execute(
            """
            SELECT ticker, entry_amount FROM candidates
            WHERE candidate_status = 'entered' AND exit_time IS NULL
            """
        )
        rows = cur.fetchall()
        result["active_amount"] = sum(int(r["entry_amount"] or 0) for r in rows)
        result["active_tickers"] = [r["ticker"] for r in rows if r["ticker"]]

        # 오늘 entered
        today = now_kst().date().isoformat()
        cur.execute(
            """
            SELECT COUNT(*) FROM candidates
            WHERE candidate_status = 'entered' AND trade_date = ?
            """,
            (today,),
        )
        row = cur.fetchone()
        result["today_entries"] = int(row[0]) if row and row[0] else 0

        # 주간 PnL (최근 7일 entered + exit_time 채워진)
        week_start = (now_kst().date() - timedelta(days=7)).isoformat()
        cur.execute(
            """
            SELECT SUM(net_pnl_pct), COUNT(net_pnl_pct) FROM candidates
            WHERE candidate_status = 'entered'
              AND exit_time IS NOT NULL
              AND trade_date >= ?
            """,
            (week_start,),
        )
        row = cur.fetchone()
        if row and row[1] and int(row[1]) > 0 and row[0] is not None:
            result["weekly_pnl_pct"] = float(row[0])
    except sqlite3.Error as e:
        logger.warning(f"[dashboard.data_adapter] fund-guard 예외: {e}")
    finally:
        conn.close()

    return result
