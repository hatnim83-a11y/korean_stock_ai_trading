"""closing_bet_system.collectors.kis_orderbook_collector

호가 1단계 스냅샷 수집기 (Phase 2-1).

**책임**:
- 종목별 호가 매도/매수 1단계 + 잔량 + 현재가 + 스프레드% 캡처
- ``KISOrderApi.inquire_asking_price`` 재사용 (이미 매도 슬리피지 측정에서도 사용 중)
- ``OrderbookSnapshot`` 변환 + DB INSERT 헬퍼 제공

**책임 외**:
- 동시호가 1초 폴링 (15:20~15:28) → Phase 2-4 ``entry_executor`` 시점에 도입
- 1~10단계 호가 → Phase 2-3 ``flow_reliability_tracker`` 시점에 확장 (KIS 응답에 이미 포함, 추출만 필요)
- 호가 기반 점수 산출 → Phase 2-3에서 활용

**호출 시점**: ``MainOrchestrator.run_daily_pipeline`` (15:10 KST) 에서 다른 collector와 병렬 (asyncio.gather).

**KIS rate_limit**: 종목당 1회 호출. universe 20 → 20호출. 다른 collector(flow/price_volume/dart)와
병렬이지만 같은 KIS 토큰 공유라 자연스러운 throttle.

**Phase 1 패턴 준수** (``kis_intraday_flow_collector`` 참조):
- frozen dataclass + is_valid 플래그
- 종목별 try/except 격리 (한 종목 실패 → is_valid=False, 다른 종목 계속)
- collect_for_universe 순차 호출
- ``infra.kis_client.get_order_api`` 지연 로딩
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== 결과 dataclass =====


@dataclass(frozen=True)
class OrderbookSnapshot:
    """1종목 1회 호가 스냅샷 (Phase 2-1: 1단계만).

    이후 Phase 2-3 시점에 ask2~ask10/bid2~bid10 확장 예정.
    """

    ticker: str
    snapshot_time: datetime              # KST timezone-aware
    is_valid: bool

    # 매도/매수 1단계 (현재 inquire_asking_price 응답에서 추출 가능한 필드)
    ask1: Optional[int] = None           # 매도 1호가 (가장 낮은 매도 희망가)
    ask_volume1: Optional[int] = None
    bid1: Optional[int] = None           # 매수 1호가
    bid_volume1: Optional[int] = None

    # 현재가 + 파생 지표
    current_price: Optional[int] = None
    spread_pct: Optional[float] = None   # (ask1-bid1)/((ask1+bid1)/2), 소수

    # 디버깅용
    raw_payload: Optional[dict] = field(default=None, hash=False, compare=False)
    error_msg: Optional[str] = None


# ===== 수집기 =====


class KisOrderbookCollector:
    """KIS API 기반 호가 스냅샷 수집기.

    Args:
        order_api: 의존성 주입 — 미지정 시 ``infra.kis_client.get_order_api()`` 사용.
            ``inquire_asking_price`` 메서드만 호출함.

    **비동기 호환** (1-8 main_orchestrator):
        ``asyncio.to_thread(collector.collect_for_universe, tickers)`` 로 감싸 사용.
    """

    def __init__(self, order_api: Optional[Any] = None):
        self._order_api = order_api

    @property
    def order_api(self) -> Any:
        """KISOrderApi 지연 로딩 (토큰 발급 회피)."""
        if self._order_api is None:
            from closing_bet_system.infra.kis_client import get_order_api

            self._order_api = get_order_api()
        return self._order_api

    # ===== 단일 종목 =====

    def collect_snapshot(
        self,
        ticker: str,
        snapshot_time: Optional[datetime] = None,
    ) -> OrderbookSnapshot:
        """1종목 1회 호가 스냅샷.

        Args:
            ticker: 6자리 종목코드.
            snapshot_time: 스냅샷 시각 (None 이면 ``now_kst()``).

        Returns:
            ``OrderbookSnapshot`` (실패 시 ``is_valid=False``).
        """
        ts = snapshot_time or now_kst()

        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            logger.warning(f"[orderbook_collector] 유효하지 않은 종목코드: {ticker!r}")
            return OrderbookSnapshot(
                ticker=str(ticker), snapshot_time=ts, is_valid=False,
                error_msg="invalid_ticker",
            )

        try:
            payload = self.order_api.inquire_asking_price(ticker)
        except Exception as e:
            logger.error(f"[orderbook_collector] {ticker} API 호출 예외: {e}")
            return OrderbookSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False,
                error_msg=f"api_exception: {e}",
            )

        if not isinstance(payload, dict) or not payload.get("success"):
            msg = (payload.get("message") if isinstance(payload, dict) else None) or "api_failed"
            logger.warning(f"[orderbook_collector] {ticker} API 실패: {msg}")
            return OrderbookSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False,
                raw_payload=payload if isinstance(payload, dict) else None,
                error_msg=msg,
            )

        ask1 = _safe_int(payload.get("ask1"))
        bid1 = _safe_int(payload.get("bid1"))
        ask_volume1 = _safe_int(payload.get("ask_volume1"))
        bid_volume1 = _safe_int(payload.get("bid_volume1"))
        current_price = _safe_int(payload.get("current_price"))

        spread_pct = _compute_spread_pct(ask1, bid1)

        return OrderbookSnapshot(
            ticker=ticker, snapshot_time=ts, is_valid=True,
            ask1=ask1, ask_volume1=ask_volume1,
            bid1=bid1, bid_volume1=bid_volume1,
            current_price=current_price, spread_pct=spread_pct,
            raw_payload=payload,
        )

    # ===== 다종목 =====

    def collect_for_universe(
        self,
        tickers: list[str],
        snapshot_time: Optional[datetime] = None,
    ) -> list[OrderbookSnapshot]:
        """여러 종목 순차 수집. 한 종목 실패 시 다른 종목 계속 진행."""
        ts = snapshot_time or now_kst()
        snapshots: list[OrderbookSnapshot] = []
        success = 0
        for ticker in tickers:
            snap = self.collect_snapshot(ticker, ts)
            snapshots.append(snap)
            if snap.is_valid:
                success += 1
        logger.info(
            f"[orderbook_collector] 호가 스냅샷 {success}/{len(tickers)}건 성공 "
            f"(시각: {ts.strftime('%H:%M:%S')})"
        )
        return snapshots


# ===== DB INSERT 헬퍼 =====


def insert_snapshots(snapshots: list[OrderbookSnapshot], db: Optional[Any] = None) -> int:
    """``orderbook_snapshots`` 테이블에 INSERT OR REPLACE.

    Args:
        snapshots: ``collect_for_universe`` 결과.
        db: ``ClosingBetDatabase`` (None 이면 default 인스턴스 사용).

    Returns:
        실제 INSERT/REPLACE 된 행 수.
    """
    if not snapshots:
        return 0

    # db=None 경로에서만 본 함수가 connection 생명주기 책임짐 (예외 시에도 close 보장)
    _owned_db = False
    if db is None:
        from closing_bet_system.storage.db import ClosingBetDatabase
        db = ClosingBetDatabase()
        db.connect()
        db.init_tables()
        _owned_db = True

    inserted = 0
    try:
        with db.get_cursor() as cursor:
            for snap in snapshots:
                try:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO orderbook_snapshots (
                            ticker, snapshot_time, is_valid,
                            ask1, ask_volume1, bid1, bid_volume1,
                            current_price, spread_pct, error_msg
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snap.ticker,
                            snap.snapshot_time.isoformat(),
                            1 if snap.is_valid else 0,
                            snap.ask1, snap.ask_volume1,
                            snap.bid1, snap.bid_volume1,
                            snap.current_price, snap.spread_pct,
                            snap.error_msg,
                        ),
                    )
                    inserted += 1
                except Exception as e:
                    logger.warning(
                        f"[orderbook_collector] DB INSERT 실패 ({snap.ticker}): {e}"
                    )
    finally:
        if _owned_db:
            try:
                db.close()
            except Exception as e:
                logger.warning(f"[orderbook_collector] DB close 예외: {e}")
    return inserted


# ===== 헬퍼 =====


def _safe_int(value: Any) -> Optional[int]:
    """int 변환. 실패 / 0 / None 시 None.

    호가가 0 이면 미체결/장 마감 → None 처리해 분석에서 제외.
    """
    if value is None:
        return None
    try:
        v = int(value)
        return v if v != 0 else None
    except (TypeError, ValueError):
        return None


def _compute_spread_pct(ask1: Optional[int], bid1: Optional[int]) -> Optional[float]:
    """호가 스프레드 계산 (소수, 양수). 둘 다 양수일 때만 계산."""
    if ask1 is None or bid1 is None or ask1 <= 0 or bid1 <= 0:
        return None
    mid = (ask1 + bid1) / 2.0
    if mid <= 0:
        return None
    return round((ask1 - bid1) / mid, 6)
