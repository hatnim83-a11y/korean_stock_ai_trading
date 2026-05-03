"""closing_bet_system.infra.fund_guard

종가베팅 주문 직전 자금/비중/종목 한도 미들웨어 (P0-1).

같은 KIS 계좌에서 종가베팅과 스윙이 자금을 공유하므로, 종가베팅이 한도를 무시하고
주문하면 스윙 매수가 증거금 부족으로 실패할 수 있다. 이 모듈은 모든 종가베팅
주문의 첫 번째 게이트키퍼다.

검사 순서 (모두 AND, 하나라도 위반 시 차단):

1. **입력 검증** — ticker 6자리 숫자, amount 양의 정수
2. **총 자산 조회** — KIS API 실패 시 보수적 차단
3. **스윙 중복** — 절대 우선 안전 규칙 (DB 조회 전에 차단)
4. **1종목 비중** — ``신규 주문 ≤ total_value × capital_ratio × max_position_per_stock``
5. **자금 한도** — ``누적 사용액 + 신규 주문 ≤ total_value × capital_ratio``
6. **동시 보유 한도** — ``현재 entered 수 < max_concurrent_positions`` (새 종목인 경우)
7. **1일 진입 한도** — ``오늘 entered 수 < max_daily_entries`` (새 종목인 경우)

향후 Phase 1에서 추가될 검사 (settings.yaml ``fund.weekly_loss_limit`` 참조):
- 주간 손실 -5% 도달 시 매매 중지 (현재는 미구현, 운영자 오인 방지를 위해 명시)

사용:
    from closing_bet_system.infra.fund_guard import FundGuard

    guard = FundGuard()
    allowed, reason = guard.allow_order(ticker="005930", amount=300_000)
    if allowed:
        order_api.buy_limit_order(...)
    else:
        logger.warning(f"주문 차단: {reason}")
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

from closing_bet_system.storage.db import (
    ClosingBetDatabase,
    _load_settings,
    _resolve_db_path,
)


# DB 조회 실패 시 보수적 차단을 위한 sentinel.
# 실제 어떤 자금/종목/거래도 이 값을 초과할 수 없을 만큼 큼.
_CONSERVATIVE_LARGE_AMOUNT = 10**12   # 1조원 (자금 한도 트리거)
_CONSERVATIVE_LARGE_COUNT = 10**6     # 100만 (카운트 한도 트리거)


@dataclass(frozen=True)
class GuardConfig:
    """fund_guard 한도 파라미터. settings.yaml 의 ``fund:`` 섹션을 매핑."""

    capital_ratio: float = 0.10                 # 종가베팅 전용 자금 비중
    max_position_per_stock: float = 0.25        # 1종목 최대 비중 (종가베팅 자금 기준)
    max_concurrent_positions: int = 4
    max_daily_entries: int = 2

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None) -> "GuardConfig":
        s = settings if settings is not None else _load_settings()
        f = s.get("fund", {})
        return cls(
            capital_ratio=float(f.get("capital_ratio", cls.capital_ratio)),
            max_position_per_stock=float(
                f.get("max_position_per_stock", cls.max_position_per_stock)
            ),
            max_concurrent_positions=int(
                f.get("max_concurrent_positions", cls.max_concurrent_positions)
            ),
            max_daily_entries=int(f.get("max_daily_entries", cls.max_daily_entries)),
        )


class FundGuard:
    """종가베팅 주문 게이트키퍼.

    의존성 주입 가능:
    - ``total_value_provider``: 0-인자 callable, 총 평가금액(원) 반환
    - ``swing_holdings_provider``: 0-인자 callable, set[str] 반환

    기본값은 ``infra.kis_client`` / ``infra.swing_db_reader`` 사용.
    테스트 시 위 두 콜백을 주입해 KIS API / 스윙 DB 의존성을 mock 한다.
    """

    def __init__(
        self,
        config: Optional[GuardConfig] = None,
        db_path: Optional[Path] = None,
        total_value_provider: Optional[Callable[[], int]] = None,
        swing_holdings_provider: Optional[Callable[[], set[str]]] = None,
    ):
        self.config = config or GuardConfig.from_settings()
        self.db_path = db_path or _resolve_db_path(_load_settings())

        # 콜백 lazy import — 테스트 시 주입 가능, 운영 시 기본 구현 사용
        self._total_value_provider = total_value_provider
        self._swing_holdings_provider = swing_holdings_provider

    # ===== 외부 API =====

    def allow_order(self, ticker: str, amount: int) -> tuple[bool, str]:
        """주문 허용 여부 + 차단 사유.

        Args:
            ticker: 6자리 종목코드
            amount: 주문 금액(원). 호출 측이 (1주 가격 × 수량 + 수수료 추정치)
                를 ``int`` 로 올림 처리하여 전달해야 한다.
                ``float`` 또는 음수는 차단된다.

        Returns:
            ``(allowed: bool, reason: str)`` — 허용 시 ``reason`` 은 빈 문자열
        """
        # 1) 입력 검증
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            return False, f"유효하지 않은 종목코드: {ticker!r}"
        # bool 은 int 의 subclass 이므로 명시 거부
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return False, f"주문 금액 오류 (양의 정수 필요): {amount!r}"

        cfg = self.config

        # 2) 총 자산 조회 — 실패 시 보수적 차단
        total_value = self._get_total_value()
        if total_value <= 0:
            return False, "총 자산 조회 실패 또는 0원 (보수적 차단)"

        # 3) 스윙 중복 — 절대 우선. DB 조회 전에 차단해 자원 낭비 방지.
        swing_held = self._get_swing_holdings()
        if ticker in swing_held:
            return False, f"스윙 시스템이 이미 보유 중인 종목: {ticker}"

        capital_limit = int(total_value * cfg.capital_ratio)
        per_stock_limit = int(capital_limit * cfg.max_position_per_stock)

        # 4) 1종목 비중 한도 (DB 조회 불필요)
        if amount > per_stock_limit:
            return False, (
                f"1종목 비중 초과: 주문 {amount:,}원 > 한도 {per_stock_limit:,}원 "
                f"(총자산 {total_value:,}원 × {cfg.capital_ratio:.0%} × {cfg.max_position_per_stock:.0%})"
            )

        # 5~7) DB 조회 통합 — 단일 connection + 단일 트랜잭션 스냅샷 (TOCTOU 방지)
        try:
            db_state = self._fetch_db_state()
        except sqlite3.Error as e:
            logger.error(f"[fund_guard] DB 상태 조회 실패 (보수적 차단): {e}")
            return False, f"종가베팅 DB 조회 실패: {e}"

        # 5) 자금 한도 (누적 + 신규)
        if db_state["active_amount"] + amount > capital_limit:
            return False, (
                f"자금 한도 초과: 현재 사용 {db_state['active_amount']:,}원 + 주문 {amount:,}원 "
                f"= {db_state['active_amount'] + amount:,}원 > 한도 {capital_limit:,}원"
            )

        # 6~7) 동시 보유 / 일일 진입 — 이미 활성화된 ticker 면 추가 매수 허용
        is_new_ticker = ticker not in db_state["active_tickers"]
        if is_new_ticker:
            active_count = len(db_state["active_tickers"])
            if active_count >= cfg.max_concurrent_positions:
                return False, (
                    f"동시 보유 한도 초과: 활성 {active_count}종목 "
                    f">= 한도 {cfg.max_concurrent_positions}종목"
                )

            if db_state["today_entries"] >= cfg.max_daily_entries:
                return False, (
                    f"1일 진입 한도 초과: 오늘 {db_state['today_entries']}건 "
                    f">= 한도 {cfg.max_daily_entries}건"
                )

        return True, ""

    # ===== 내부 헬퍼 (override 가능) =====

    def _get_total_value(self) -> int:
        if self._total_value_provider is not None:
            return self._total_value_provider()
        # lazy import — 모듈 로드 시 KIS 토큰 발급 회피
        from closing_bet_system.infra.kis_client import get_total_account_value

        return get_total_account_value()

    def _get_swing_holdings(self) -> set[str]:
        if self._swing_holdings_provider is not None:
            return self._swing_holdings_provider()
        from closing_bet_system.infra.swing_db_reader import get_swing_holding_codes

        return get_swing_holding_codes()

    def _open_db(self) -> sqlite3.Connection:
        """closing_bet.db read-only 연결 (조회 전용)."""
        return sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=10.0,
        )

    def _fetch_db_state(self) -> dict:
        """allow_order() 한 번에 필요한 모든 DB 정보를 단일 connection 으로 조회.

        TOCTOU(Time-of-check-to-time-of-use) 위험 회피 — 3 쿼리가 같은 스냅샷을 본다.

        Returns:
            ``{"active_amount": int, "active_tickers": set[str], "today_entries": int}``

        Raises:
            ``sqlite3.Error`` — 호출 측이 catch 하여 보수적 차단 처리
        """
        conn = self._open_db()
        try:
            cur = conn.cursor()

            # 활성 포지션 (entered + 미청산) — 자금 한도 + 동시 보유 동시 활용
            cur.execute(
                """
                SELECT ticker, entry_amount
                FROM candidates
                WHERE candidate_status = 'entered'
                  AND exit_time IS NULL
                """
            )
            rows = cur.fetchall()
            active_amount = sum(int(r[1] or 0) for r in rows)
            active_tickers = {r[0] for r in rows if r[0]}

            # 오늘 (KST) entered 처리 수
            today = now_kst().date().isoformat()
            cur.execute(
                """
                SELECT COUNT(*)
                FROM candidates
                WHERE candidate_status = 'entered'
                  AND trade_date = ?
                """,
                (today,),
            )
            row = cur.fetchone()
            today_entries = int(row[0]) if row and row[0] else 0

            return {
                "active_amount": active_amount,
                "active_tickers": active_tickers,
                "today_entries": today_entries,
            }
        finally:
            conn.close()
