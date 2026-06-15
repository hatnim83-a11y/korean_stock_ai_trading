"""closing_bet_system.storage.candidate_logger

PRD 11-1 — 후보 라이프사이클 DB 저장 통합 모듈.

**책임**:
1. 후보 등록 (``status='recommended'``) → ``candidates`` INSERT
2. 라이프사이클 상태 전이 → ``candidates`` UPDATE
   - ``rejected_filter`` (1-5b OvernightRiskFilter, 4-1 하드 룰)
   - ``rejected_manual`` (1-7 telegram 사용자 거부)
   - ``entered`` (2-4 entry_executor 진입 체결)
   - ``exit`` (익일 매도) — status 가 별도 없고 entry_status 유지 + exit_price/exit_time 채움
3. 피처 스냅샷 → ``candidate_features`` INSERT
4. 사후 라벨링 → ``candidate_labels`` INSERT/REPLACE (T+1 09:30)
5. 추정값 vs 확정값 → ``flow_data_reliability`` INSERT/REPLACE (Phase 2)
6. 1-9 검증 헬퍼 (30건 누적 / 영업일 / 종목 다양성)

**의존성** (주입 가능):
- ``ClosingBetDatabase`` (storage/db.py) — 미지정 시 ``init_db()`` 싱글톤
- ``CostSlippageEngine`` (engines/cost_slippage_engine.py) — 미지정 시 ``get_engine()`` 싱글톤

**candidates UNIQUE 미정의** (CONTEXT.md 의도):
같은 (trade_date, ticker) 가 여러 status 로 기록 가능. 본 모듈은:
- ``log_recommended`` → 매번 새 ``candidate_id`` (INSERT)
- ``mark_*`` / ``log_entry`` / ``log_exit`` → 명시한 ``candidate_id`` UPDATE

호출처는 첫 등록 시 받은 ``candidate_id`` 를 보관해 후속 호출에 전달.

**설계 원칙**:
- DB 트랜잭션 단위 = 1 메서드 호출 (``ClosingBetDatabase.get_cursor`` 컨텍스트 매니저가 commit)
- ScoreBreakdown / IntradayFlowSnapshot / PriceVolumeSnapshot / DartDisclosureSnapshot 등은
  duck-typing 으로 받음 (dict 도 지원). 1-2~1-5 형제 모듈과 결합도 최소화.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine, get_engine
from closing_bet_system.storage.db import ClosingBetDatabase, init_db


# ===== 상수 — 허용 상태 (DB CHECK 제약과 일치) =====

CANDIDATE_STATUS_RECOMMENDED = "recommended"
CANDIDATE_STATUS_ENTERED = "entered"
CANDIDATE_STATUS_REJECTED_FILTER = "rejected_filter"
CANDIDATE_STATUS_REJECTED_MANUAL = "rejected_manual"

ALLOWED_STATUSES: frozenset = frozenset((
    CANDIDATE_STATUS_RECOMMENDED,
    CANDIDATE_STATUS_ENTERED,
    CANDIDATE_STATUS_REJECTED_FILTER,
    CANDIDATE_STATUS_REJECTED_MANUAL,
))

# Layer 키 — candidate_features 컬럼명 1:1 매핑
LAYER1_KEYS = (
    "inst_net_buy_estimated",
    "foreign_net_buy_3d",
    "program_net_buy_change",
    "closing_flow_concentration",
)
LAYER2_KEYS = (
    "close_strength",
    "upper_shadow_atr",
    "last_30min_vwap_position",
    "closing_buy_sell_ratio",
    "volume_surprise",
    "atr_overheat",
)
LAYER3_KEYS = (
    "days_from_52w_high",         # INTEGER
    "relative_strength_5d",        # REAL
    "theme_leadership_rank",       # INTEGER
)
MARKET_REGIME_KEYS = (
    "kospi_above_200ma",           # BOOLEAN
    "vkospi",                      # REAL
    "foreign_5d_cumulative",       # REAL
    "us_futures_change",           # REAL
    "usd_krw_change",              # REAL
)


# ===== 결과 dataclass =====


@dataclass(frozen=True)
class ExitResult:
    """``log_exit`` 결과 — DB 저장 직후 손익 분해."""
    candidate_id: int
    cost_breakdown: Any                # CostBreakdown (frozen)
    saved_columns: dict


# ===== 메인 클래스 =====


class CandidateLogger:
    """후보 라이프사이클 DB 저장 통합.

    Args:
        db: ``ClosingBetDatabase`` 인스턴스. None 이면 ``init_db()`` 싱글톤 자동 생성.
        cost_engine: ``CostSlippageEngine`` 인스턴스. None 이면 ``get_engine()`` 싱글톤 자동.

    **호출 패턴 (정상 케이스)**:
        1. ``cid = logger.log_recommended(trade_date, ticker, name, score_breakdown)``
        2. ``logger.log_features(cid, layer1, layer2, layer3, market_regime)``
        3. (필터 통과 시) ``logger.mark_entered(cid, entry_price, entry_amount, entry_time)``
        4. (익일 매도 후) ``result = logger.log_exit(cid, exit_price, exit_time, shares)``
        5. (T+1 09:30 라벨) ``logger.log_labels(cid, next_open_pct, ...)``
    """

    def __init__(
        self,
        db: Optional[ClosingBetDatabase] = None,
        cost_engine: Optional[CostSlippageEngine] = None,
    ):
        # 의존성 주입 — 미지정 시 지연 로딩 (DB 연결 비용 회피)
        self._db = db
        self._cost_engine = cost_engine

    @property
    def db(self) -> ClosingBetDatabase:
        if self._db is None:
            self._db = init_db()   # connect + init_tables
        return self._db

    @property
    def cost_engine(self) -> CostSlippageEngine:
        if self._cost_engine is None:
            self._cost_engine = get_engine()
        return self._cost_engine

    # ===== 1. log_recommended (INSERT) =====

    def log_recommended(
        self,
        trade_date: Any,
        ticker: str,
        name: str,
        score_breakdown: Any = None,
        external_risk_score: Optional[int] = None,
    ) -> int:
        """후보 등록 — ``status='recommended'`` 로 새 행 INSERT.

        Args:
            trade_date: 거래일 (date / datetime / "YYYY-MM-DD" str)
            ticker: 6자리 종목코드
            name: 종목명
            score_breakdown: 1-4 ``ScoreBreakdown`` (또는 dict). subscore 3개 + raw_total 사용.
            external_risk_score: 1-5b 외부 리스크 점수 (PRD 8-3) — Phase 1 미구현 None

        Returns:
            새로 생성된 ``candidate_id``

        Raises:
            ValueError: ticker 형식 오류 / name 비어있음
        """
        ticker_str, name_str = self._validate_ticker_name(ticker, name)
        date_str = _to_date_str(trade_date)

        # ScoreBreakdown 추출 (없으면 None)
        l1 = _gak(score_breakdown, "layer1_subscore", None)
        l2 = _gak(score_breakdown, "layer2_subscore", None)
        l3 = _gak(score_breakdown, "layer3_subscore", None)
        total = _gak(score_breakdown, "raw_total", None)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO candidates (
                    trade_date, ticker, name, candidate_status,
                    layer1_score, layer2_score, layer3_score,
                    external_risk_score, total_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_str, ticker_str, name_str, CANDIDATE_STATUS_RECOMMENDED,
                    l1, l2, l3, external_risk_score, total,
                ),
            )
            candidate_id = cursor.lastrowid
        logger.info(
            f"[candidate_logger] {ticker_str} ({name_str}) recommended 등록 — "
            f"candidate_id={candidate_id} L1/L2/L3={l1}/{l2}/{l3}"
        )
        return int(candidate_id)

    # ===== 2. log_features (INSERT, PK=candidate_id) =====

    def log_features(
        self,
        candidate_id: int,
        layer1: Optional[dict] = None,
        layer2: Optional[dict] = None,
        layer3: Optional[dict] = None,
        market_regime: Optional[dict] = None,
    ) -> None:
        """피처 스냅샷 INSERT — ``candidate_features`` 18컬럼.

        모든 dict 키가 None 이어도 가능. 키 누락 시 NULL 저장.
        candidate_id 기존 존재 시 IntegrityError (PK 충돌) — 호출 측에서 1회만.
        """
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")
        l1 = layer1 or {}
        l2 = layer2 or {}
        l3 = layer3 or {}
        mr = market_regime or {}

        # 컬럼 순서 (schema_v1 정의 순)
        columns = (
            "candidate_id",
            *LAYER1_KEYS, *LAYER2_KEYS, *LAYER3_KEYS, *MARKET_REGIME_KEYS,
        )
        # 값 추출 (각 layer dict 에서 키 lookup)
        values = [candidate_id]
        for k in LAYER1_KEYS:
            values.append(_safe_get(l1, k))
        for k in LAYER2_KEYS:
            values.append(_safe_get(l2, k))
        for k in LAYER3_KEYS:
            v = _safe_get(l3, k)
            # int 컬럼 (days_from_52w_high, theme_leadership_rank) — float 보정
            if k in ("days_from_52w_high", "theme_leadership_rank") and v is not None:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = None
            values.append(v)
        for k in MARKET_REGIME_KEYS:
            v = _safe_get(mr, k)
            if k == "kospi_above_200ma" and v is not None:
                # SQLite BOOLEAN = INTEGER 0/1
                v = bool(v) if isinstance(v, (bool, int, float)) else None
                if v is not None:
                    v = int(v)
            values.append(v)

        placeholders = ", ".join(["?"] * len(columns))
        col_list = ", ".join(columns)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO candidate_features ({col_list}) VALUES ({placeholders})",
                values,
            )
        logger.debug(f"[candidate_logger] features INSERT — candidate_id={candidate_id}")

    # ===== 3. mark_* / log_entry / log_exit (UPDATE) =====

    def mark_rejected_by_filter(self, candidate_id: int, reason: str) -> None:
        self._update_status(candidate_id, CANDIDATE_STATUS_REJECTED_FILTER, reason)

    def mark_rejected_manual(self, candidate_id: int, reason: str) -> None:
        self._update_status(candidate_id, CANDIDATE_STATUS_REJECTED_MANUAL, reason)

    def mark_entered(
        self,
        candidate_id: int,
        entry_price: float,
        entry_amount: float,
        entry_time: Optional[datetime] = None,
    ) -> None:
        """진입 체결 → status='entered' + entry_* 업데이트."""
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")
        if entry_price is None or float(entry_price) <= 0:
            raise ValueError(f"entry_price 는 양수: got {entry_price}")
        if entry_amount is None or float(entry_amount) <= 0:
            raise ValueError(f"entry_amount 는 양수: got {entry_amount}")
        ts = (entry_time or now_kst()).isoformat()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE candidates
                SET candidate_status=?, entry_price=?, entry_amount=?, entry_time=?
                WHERE candidate_id=?
                """,
                (CANDIDATE_STATUS_ENTERED, float(entry_price), float(entry_amount), ts, candidate_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"candidate_id={candidate_id} 미존재 (mark_entered)")
        logger.info(
            f"[candidate_logger] candidate_id={candidate_id} entered "
            f"(entry_price={entry_price}, amount={entry_amount})"
        )

    def mark_entered_phase1_only(self, candidate_id: int) -> None:
        """phase1 only 보유 상태를 'entered' 로 전환 — log_exit 호출 가능하게.

        단위 2-5 morning_exit_manager P0-2 회피용. 옵션 A 인터페이스 계약 상
        phase1 만 체결된 후보는 candidate_status='recommended' + entry_price=NULL
        상태로 유지된다 (단위 2-4c `_finalize_mark_entered` 가 phase2 완료 시만 호출).
        본 헬퍼는 매도 발주 직전에 phase1_executed_price/_shares 를 그대로
        entry_price/_amount/_time 에 박제 + status='entered' 로 전환해
        ``log_exit()`` 가 LookupError 없이 호출 가능하게 만든다.

        Raises:
            LookupError: candidate_id 미존재 또는 phase1_executed_shares <= 0
        """
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")

        row = self.get_candidate(candidate_id)
        if row is None:
            raise LookupError(f"candidate_id={candidate_id} 미존재 (mark_entered_phase1_only)")

        p1_price = row.get("entry_phase1_executed_price")
        p1_shares = row.get("entry_phase1_executed_shares")
        if not p1_price or not p1_shares or p1_shares <= 0:
            raise LookupError(
                f"candidate_id={candidate_id} phase1 체결 정보 부재 "
                f"(price={p1_price}, shares={p1_shares}). mark_entered_phase1_only 호출 불가."
            )

        entry_price = float(p1_price)
        entry_amount = entry_price * int(p1_shares)
        self.mark_entered(
            candidate_id=candidate_id,
            entry_price=entry_price,
            entry_amount=entry_amount,
        )
        logger.info(
            f"[candidate_logger] candidate_id={candidate_id} phase1 only → entered "
            f"(price={entry_price}, shares={p1_shares}, amount={entry_amount:.0f})"
        )

    def log_exit(
        self,
        candidate_id: int,
        exit_price: float,
        exit_time: Optional[datetime] = None,
        shares: int = 1,
        slippage_rate: Optional[float] = None,
    ) -> ExitResult:
        """매도 체결 → cost_engine 으로 비용 분해 + UPDATE.

        candidate_id 가 ``entered`` 상태가 아니면 LookupError (entry_price 없으면 PnL 계산 불가).

        Args:
            shares: 매수 주식 수 (cost 계산 정확도용)
            slippage_rate: 편도 슬리피지율 (None=settings 기본 / 0=실거래 / float=명시)

        Returns:
            ``ExitResult`` (CostBreakdown 포함)
        """
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")
        if exit_price is None or float(exit_price) <= 0:
            raise ValueError(f"exit_price 는 양수: got {exit_price}")
        if not isinstance(shares, int) or isinstance(shares, bool) or shares <= 0:
            raise ValueError(f"shares 는 양의 정수: got {shares!r}")

        # entry_price 조회
        row = self.get_candidate(candidate_id)
        if row is None:
            raise LookupError(f"candidate_id={candidate_id} 미존재 (log_exit)")
        entry_price = row.get("entry_price")
        if entry_price is None or entry_price <= 0:
            raise LookupError(
                f"candidate_id={candidate_id} entry_price 없음 (status={row.get('candidate_status')!r}). "
                f"먼저 mark_entered 호출 필요."
            )

        # cost_engine 비용 분해
        breakdown = self.cost_engine.compute_pnl(
            buy_price=float(entry_price),
            sell_price=float(exit_price),
            shares=shares,
            slippage_rate=slippage_rate,
        )
        cost_payload = self.cost_engine.to_db_payload(breakdown)

        ts = (exit_time or now_kst()).isoformat()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE candidates
                SET exit_price=?, exit_time=?, exit_shares=?, final_exit_time=?,
                    buy_commission=?, sell_commission=?, transaction_tax=?,
                    estimated_slippage=?, net_pnl_pct=?
                WHERE candidate_id=?
                """,
                (
                    float(exit_price), ts, int(shares), ts,
                    cost_payload["buy_commission"], cost_payload["sell_commission"],
                    cost_payload["transaction_tax"], cost_payload["estimated_slippage"],
                    cost_payload["net_pnl_pct"],
                    candidate_id,
                ),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"candidate_id={candidate_id} UPDATE 영향 없음 (log_exit)")
        logger.info(
            f"[candidate_logger] candidate_id={candidate_id} exit "
            f"(net_pnl_pct={breakdown.net_pnl_pct*100:+.3f}%)"
        )
        return ExitResult(
            candidate_id=candidate_id,
            cost_breakdown=breakdown,
            saved_columns={**cost_payload, "exit_price": float(exit_price), "exit_time": ts},
        )

    def log_partial_exit(
        self,
        candidate_id: int,
        exit_price: float,
        exit_shares: int,
        exit_time: Optional[datetime] = None,
        slippage_rate: Optional[float] = None,
    ) -> ExitResult:
        """부분청산 누적 기록 (단위 2-5g Phase 2A). 같은 candidate 다중 호출 가능.

        - ``exit_shares`` 누적(old+this), ``exit_price`` 가중평균 갱신.
        - 누적이 진입 총수량 도달 시 전량청산: ``exit_time`` + ``final_exit_time`` 박제.
          **미도달(부분)이면 exit_time 을 박제하지 않음** → select_exit_targets 의
          ``exit_time IS NULL`` 가 잔여를 계속 매도대상으로 인식(gap_up_high 잔여 미청산 버그 fix).
        - 비용/net_pnl_pct 는 누적 청산수량·가중평균가 기준 재계산(완결성).

        log_exit(전량 1회)과 달리 부분→전량 핸드오프(09:02 1차 + 트레일링/force_close 잔여)에 사용.
        """
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")
        if exit_price is None or float(exit_price) <= 0:
            raise ValueError(f"exit_price 는 양수: got {exit_price}")
        if not isinstance(exit_shares, int) or isinstance(exit_shares, bool) or exit_shares <= 0:
            raise ValueError(f"exit_shares 는 양의 정수: got {exit_shares!r}")

        row = self.get_candidate(candidate_id)
        if row is None:
            raise LookupError(f"candidate_id={candidate_id} 미존재 (log_partial_exit)")
        entry_price = row.get("entry_price")
        if entry_price is None or entry_price <= 0:
            raise LookupError(
                f"candidate_id={candidate_id} entry_price 없음 (status={row.get('candidate_status')!r}). "
                f"먼저 mark_entered 호출 필요."
            )

        total_entered = int(row.get("entry_phase1_executed_shares") or 0) + int(
            row.get("entry_phase2_executed_shares") or 0
        )
        old_exit_shares = int(row.get("exit_shares") or 0)
        old_exit_price = float(row.get("exit_price") or 0.0)

        # 잔여 초과분 클립 (총수량 모르면=0 클립 안 함)
        this_shares = exit_shares
        if total_entered > 0:
            remaining = max(total_entered - old_exit_shares, 0)
            if remaining <= 0:
                raise LookupError(
                    f"candidate_id={candidate_id} 잔여 0 (이미 전량청산: "
                    f"exit_shares={old_exit_shares}/{total_entered})"
                )
            this_shares = min(exit_shares, remaining)

        new_exit_shares = old_exit_shares + this_shares
        # 가중평균 청산가
        if old_exit_shares > 0 and old_exit_price > 0:
            avg_exit_price = (
                old_exit_shares * old_exit_price + this_shares * float(exit_price)
            ) / new_exit_shares
        else:
            avg_exit_price = float(exit_price)

        is_full = total_entered > 0 and new_exit_shares >= total_entered

        # 누적 청산수량·가중평균가 기준 비용/PnL 재계산
        breakdown = self.cost_engine.compute_pnl(
            buy_price=float(entry_price),
            sell_price=float(avg_exit_price),
            shares=int(new_exit_shares),
            slippage_rate=slippage_rate,
        )
        cost_payload = self.cost_engine.to_db_payload(breakdown)

        ts = (exit_time or now_kst()).isoformat()
        final_ts = ts if is_full else None  # exit_time/final_exit_time 은 전량청산 시에만

        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE candidates
                SET exit_shares=?, exit_price=?,
                    buy_commission=?, sell_commission=?, transaction_tax=?,
                    estimated_slippage=?, net_pnl_pct=?,
                    exit_time=COALESCE(?, exit_time),
                    final_exit_time=COALESCE(?, final_exit_time)
                WHERE candidate_id=?
                """,
                (
                    int(new_exit_shares), float(avg_exit_price),
                    cost_payload["buy_commission"], cost_payload["sell_commission"],
                    cost_payload["transaction_tax"], cost_payload["estimated_slippage"],
                    cost_payload["net_pnl_pct"],
                    final_ts, final_ts,
                    candidate_id,
                ),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"candidate_id={candidate_id} UPDATE 영향 없음 (log_partial_exit)")
        logger.info(
            f"[candidate_logger] candidate_id={candidate_id} partial_exit "
            f"{this_shares}주 (누적 {new_exit_shares}/{total_entered}, "
            f"avg={avg_exit_price:,.0f}, full={is_full})"
        )
        return ExitResult(
            candidate_id=candidate_id,
            cost_breakdown=breakdown,
            saved_columns={
                **cost_payload, "exit_price": float(avg_exit_price),
                "exit_shares": int(new_exit_shares),
                "exit_time": final_ts, "final_exit_time": final_ts,
            },
        )

    # ===== 4. log_labels (T+1 09:30 — INSERT OR REPLACE) =====

    def log_labels(
        self,
        candidate_id: int,
        next_open_pct: Optional[float] = None,
        next_morning_high_pct: Optional[float] = None,
        next_morning_low_pct: Optional[float] = None,
        label_gap_up: Optional[bool] = None,
        label_morning_exit: Optional[bool] = None,
        label_stop_risk: Optional[bool] = None,
        label_net_ev_positive: Optional[bool] = None,
    ) -> None:
        """T+1 09:30 라벨 — ``candidate_labels`` INSERT OR REPLACE (PK=candidate_id).

        호출 측에서 먼저 candidates(candidate_id) 가 존재함을 보장. 미존재 시 FK 위반.

        **⚠️ INSERT OR REPLACE 부분 덮어쓰기 주의**:
        SQLite 의 INSERT OR REPLACE 는 기존 행을 DELETE 후 INSERT 하므로, 두 번째 호출에서
        이전 호출의 일부 컬럼만 다시 채우면 **나머지 컬럼은 NULL 로 초기화**된다.
        예: 1차 ``log_labels(cid, next_open_pct=0.008, label_gap_up=True)`` 후
            2차 ``log_labels(cid, label_gap_up=False)`` → ``next_open_pct`` 가 NULL 로 소실.

        부분 업데이트가 필요하면 호출 측에서 기존 값을 조회 후 모든 키를 함께 전달할 것.
        Phase 1 정상 사용은 1회 호출 (T+1 09:30 라벨 저장) 이므로 보통 문제 없음.
        """
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")

        def _to_int_bool(v):
            if v is None:
                return None
            if isinstance(v, bool):
                return int(v)
            return None  # int/float bool 이 아니면 None (의미 모호)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO candidate_labels (
                    candidate_id,
                    next_open_pct, next_morning_high_pct, next_morning_low_pct,
                    label_gap_up, label_morning_exit, label_stop_risk, label_net_ev_positive,
                    labeled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    _safe_float(next_open_pct), _safe_float(next_morning_high_pct),
                    _safe_float(next_morning_low_pct),
                    _to_int_bool(label_gap_up), _to_int_bool(label_morning_exit),
                    _to_int_bool(label_stop_risk), _to_int_bool(label_net_ev_positive),
                    now_kst().isoformat(),
                ),
            )
        logger.debug(f"[candidate_logger] labels — candidate_id={candidate_id}")

    # ===== 5. log_flow_reliability (Phase 2) =====

    def log_flow_reliability(
        self,
        trade_date: Any,
        ticker: str,
        inst_estimated: Optional[float] = None,
        inst_confirmed: Optional[float] = None,
        foreign_estimated: Optional[float] = None,
        foreign_confirmed: Optional[float] = None,
    ) -> None:
        """추정값 vs KRX 확정값 — INSERT OR REPLACE (UNIQUE: trade_date+ticker).

        방향 일치율 (inst_direction_match / foreign_direction_match) 자동 계산:
        부호가 같으면 True, 한쪽 0 또는 None 이면 None.
        """
        ticker_str, _ = self._validate_ticker_name(ticker, name="X")
        date_str = _to_date_str(trade_date)

        inst_match = _direction_match(inst_estimated, inst_confirmed)
        for_match = _direction_match(foreign_estimated, foreign_confirmed)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO flow_data_reliability (
                    trade_date, ticker,
                    inst_estimated, inst_confirmed,
                    foreign_estimated, foreign_confirmed,
                    inst_direction_match, foreign_direction_match,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_str, ticker_str,
                    _safe_float(inst_estimated), _safe_float(inst_confirmed),
                    _safe_float(foreign_estimated), _safe_float(foreign_confirmed),
                    None if inst_match is None else int(inst_match),
                    None if for_match is None else int(for_match),
                    now_kst().isoformat(),
                ),
            )

    # ===== 6. 조회 헬퍼 =====

    def get_candidate(self, candidate_id: int) -> Optional[dict]:
        """candidate_id 단건 조회 → dict (또는 None)."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def get_candidates_in_period(
        self,
        start_date: Any,
        end_date: Any,
        status: Optional[str] = None,
    ) -> list[dict]:
        """기간/상태별 후보 조회."""
        s = _to_date_str(start_date)
        e = _to_date_str(end_date)
        if status is not None and status not in ALLOWED_STATUSES:
            raise ValueError(f"status 는 {ALLOWED_STATUSES} 중 하나: got {status!r}")
        sql = "SELECT * FROM candidates WHERE trade_date BETWEEN ? AND ?"
        params: list = [s, e]
        if status:
            sql += " AND candidate_status=?"
            params.append(status)
        sql += " ORDER BY trade_date, candidate_id"
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def get_distinct_stocks_count(
        self,
        start_date: Any,
        end_date: Any,
        status: Optional[str] = None,
    ) -> int:
        """1-9 검증: 30건 누적 + 종목 다양성 (서로 다른 종목 20개 이상)."""
        s = _to_date_str(start_date)
        e = _to_date_str(end_date)
        if status is not None and status not in ALLOWED_STATUSES:
            raise ValueError(f"status 는 {ALLOWED_STATUSES} 중 하나: got {status!r}")
        sql = "SELECT COUNT(DISTINCT ticker) AS c FROM candidates WHERE trade_date BETWEEN ? AND ?"
        params: list = [s, e]
        if status:
            sql += " AND candidate_status=?"
            params.append(status)
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return int(row["c"]) if row else 0

    def count_by_status(self, start_date: Any, end_date: Any) -> dict:
        """기간별 status 분포 dict."""
        s = _to_date_str(start_date)
        e = _to_date_str(end_date)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_status, COUNT(*) AS c
                FROM candidates
                WHERE trade_date BETWEEN ? AND ?
                GROUP BY candidate_status
                """,
                (s, e),
            )
            rows = cursor.fetchall()
        result = {st: 0 for st in ALLOWED_STATUSES}
        for r in rows:
            result[r["candidate_status"]] = int(r["c"])
        return result

    def get_recent_recommended(self, limit: int = 30) -> list[dict]:
        """최근 ``limit`` 건의 recommended 후보 (최신순)."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError(f"limit 양의 정수: got {limit!r}")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM candidates
                WHERE candidate_status=?
                ORDER BY candidate_id DESC
                LIMIT ?
                """,
                (CANDIDATE_STATUS_RECOMMENDED, limit),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def get_closed_today(self, exit_date: Any) -> list[dict]:
        """exit_date 일자에 청산된 후보 조회 + PnL 가공.

        T+1 매도 정책: trade_date=T 진입 → exit_time=T+1 청산.
        반환 dict 필드: ticker, name, entry_price_avg, exit_price, qty,
        profit_abs, profit_rate, exit_time.

        Args:
            exit_date: 청산 발생 일자 (date / str / datetime). 일반적으로 오늘.

        Returns:
            청산 dict 리스트 (체결+entry/exit 가격 모두 있는 건만, exit_time 오름차순).
        """
        date_str = _to_date_str(exit_date)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id, ticker, name,
                       entry_phase1_executed_price, entry_phase1_executed_shares,
                       entry_phase2_executed_price, entry_phase2_executed_shares,
                       entry_price, exit_price, exit_time
                FROM candidates
                WHERE DATE(exit_time) = ?
                  AND exit_price IS NOT NULL
                ORDER BY exit_time ASC
                """,
                (date_str,),
            )
            rows = cursor.fetchall()
        result: list[dict] = []
        for r in rows:
            d = dict(r)
            p1 = d.get("entry_phase1_executed_price")
            s1 = d.get("entry_phase1_executed_shares")
            p2 = d.get("entry_phase2_executed_price")
            s2 = d.get("entry_phase2_executed_shares")
            # 가중평균 매수가
            if p1 and s1 and p1 > 0 and s1 > 0:
                if p2 and s2 and p2 > 0 and s2 > 0:
                    entry_avg = (float(p1) * int(s1) + float(p2) * int(s2)) / (
                        int(s1) + int(s2)
                    )
                    qty = int(s1) + int(s2)
                else:
                    entry_avg = float(p1)
                    qty = int(s1)
            elif d.get("entry_price") and d["entry_price"] > 0:
                entry_avg = float(d["entry_price"])
                qty = (int(s1) if s1 else 0) + (int(s2) if s2 else 0)
            else:
                continue  # 원가 미상은 PnL 산출 불가

            exit_price = d.get("exit_price")
            if not exit_price or exit_price <= 0:
                continue
            profit_per_share = float(exit_price) - entry_avg
            profit_abs = profit_per_share * qty if qty else 0.0
            profit_rate = (float(exit_price) - entry_avg) / entry_avg * 100
            result.append(
                {
                    "candidate_id": d["candidate_id"],
                    "ticker": d["ticker"],
                    "name": d["name"],
                    "entry_price_avg": entry_avg,
                    "exit_price": float(exit_price),
                    "qty": qty,
                    "profit_abs": profit_abs,
                    "profit_rate": profit_rate,
                    "exit_time": d.get("exit_time"),
                }
            )
        return result

    # ===== 내부 헬퍼 =====

    def _update_status(self, candidate_id: int, new_status: str, reason: str) -> None:
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            raise ValueError(f"candidate_id 는 양의 정수: got {candidate_id!r}")
        if new_status not in ALLOWED_STATUSES:
            raise ValueError(f"new_status 는 {ALLOWED_STATUSES} 중 하나: got {new_status!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"reason 비어있음: {reason!r}")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE candidates SET candidate_status=?, rejection_reason=? WHERE candidate_id=?",
                (new_status, reason.strip(), candidate_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"candidate_id={candidate_id} 미존재 (_update_status)")
        logger.info(
            f"[candidate_logger] candidate_id={candidate_id} → {new_status} (reason: {reason[:50]})"
        )

    @staticmethod
    def _validate_ticker_name(ticker: Any, name: Any) -> tuple[str, str]:
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            raise ValueError(f"ticker 6자리 숫자: got {ticker!r}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"name 비어있음: got {name!r}")
        return ticker, name.strip()


# ===== 모듈 헬퍼 =====


def _safe_float(value: Any) -> Optional[float]:
    """None / bool / NaN / inf / 변환 실패 → None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _safe_get(d: dict, key: str) -> Any:
    """dict.get + 보호 (dict 가 아니면 None)."""
    if not isinstance(d, dict):
        return None
    return d.get(key)


def _gak(obj: Any, name: str, default: Any) -> Any:
    """getattr-or-key (1-5b 패턴 동일)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_date_str(value: Any) -> str:
    """date / datetime / "YYYY-MM-DD" str → "YYYY-MM-DD" 문자열."""
    if value is None:
        return now_kst().date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_cls):
        return value.isoformat()
    if isinstance(value, str):
        # 단순 형식 검증 (YYYY-MM-DD 또는 YYYYMMDD)
        s = value.strip()
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return s
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    raise ValueError(f"날짜 형식 오류: got {value!r}")


def _direction_match(estimated: Any, confirmed: Any) -> Optional[bool]:
    """추정값 vs 확정값 부호 일치 — 한쪽 None / 0 이면 None."""
    e = _safe_float(estimated)
    c = _safe_float(confirmed)
    if e is None or c is None or e == 0 or c == 0:
        return None
    return (e > 0) == (c > 0)
