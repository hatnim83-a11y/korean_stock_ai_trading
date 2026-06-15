"""closing_bet_system.execution.exit_target_query

단위 2-5b — 매도 대상 candidates 식별 쿼리 + ``ExitTarget`` dataclass.

옵션 A 인터페이스 계약 (단위 2-4c `_finalize_mark_entered`):
- phase2 완료 → ``candidate_status='entered'`` + ``entry_price`` 가중평균
- phase1 only 보유 → ``candidate_status='recommended'`` + ``entry_price=NULL``
  + ``entry_phase1_executed_shares > 0`` + ``entry_phase2_executed_shares IS NULL``

본 쿼리는 두 보유 상태를 모두 매도 대상으로 식별한다.

SQL 검증: 단위 2-5a STEP0 검증 2 (운영 DB 직접 query) PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from closing_bet_system.storage.candidate_logger import CandidateLogger
from logger import logger


@dataclass(frozen=True)
class ExitTarget:
    """단일 종목 매도 대상 (per-row)."""

    candidate_id: int
    ticker: str
    name: str
    candidate_status: str            # 'entered' or 'recommended' (phase1 only)
    entry_price: Optional[float]      # 'entered' 시 가중평균, phase1 only 시 None
    entry_amount: Optional[float]
    phase1_executed_price: Optional[float]
    phase1_executed_shares: int       # 0 가능 (entered 인데 phase1만 체결 안된 케이스는 옵션 A상 불가)
    phase2_executed_price: Optional[float]
    phase2_executed_shares: int       # 0 / None 가능 (phase1 only 케이스)
    exit_shares: int = 0              # 누적 청산수량 (부분청산, Phase 2A). 기본 0

    @property
    def is_phase1_only(self) -> bool:
        """phase1 only 보유 상태 — log_exit 호출 전 mark_entered_phase1_only 선행 필요."""
        return (
            self.candidate_status == "recommended"
            and self.phase1_executed_shares > 0
            and (self.phase2_executed_shares is None or self.phase2_executed_shares == 0)
        )

    @property
    def total_shares(self) -> int:
        """진입 총 수량 (phase1 + phase2 합, NULL 안전)."""
        p1 = self.phase1_executed_shares or 0
        p2 = self.phase2_executed_shares or 0
        return p1 + p2

    @property
    def remaining_shares(self) -> int:
        """미청산 잔여 수량 = 진입총량 − 누적청산(exit_shares). 부분청산 후 잔여(Phase 2A).

        force_close/트레일링은 total_shares 가 아닌 이 값으로 발주해야 이미 청산된 수량
        재발주(과매도)를 막는다. 최소 0.
        """
        return max(self.total_shares - (self.exit_shares or 0), 0)


def select_exit_targets(
    candidate_logger: CandidateLogger,
    trade_date: str,
) -> list[ExitTarget]:
    """매도 대상 candidates select.

    Args:
        candidate_logger: CandidateLogger 인스턴스 (DB 연결 보유).
        trade_date: 진입일 ISO 문자열 (T-1, 어제). 단위 2-5는 T-1 진입 후 T 매도.

    Returns:
        ExitTarget list (entered + phase1 only 합집합). 빈 결과 graceful.
    """
    with candidate_logger.db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT candidate_id, ticker, name, candidate_status,
                   entry_price, entry_amount,
                   entry_phase1_executed_price, entry_phase1_executed_shares,
                   entry_phase2_executed_price, entry_phase2_executed_shares,
                   COALESCE(exit_shares, 0) AS exit_shares
            FROM candidates
            WHERE trade_date = ?
              AND (
                candidate_status = 'entered'
                OR (
                  candidate_status = 'recommended'
                  AND COALESCE(entry_phase1_executed_shares, 0) > 0
                  AND entry_phase2_executed_shares IS NULL
                )
              )
              AND exit_time IS NULL
            ORDER BY candidate_id ASC
            """,
            (trade_date,),
        )
        rows = cursor.fetchall()

    targets: list[ExitTarget] = []
    for row in rows:
        p1_shares = int(row["entry_phase1_executed_shares"] or 0)
        p2_shares_raw = row["entry_phase2_executed_shares"]
        p2_shares = int(p2_shares_raw) if p2_shares_raw is not None else 0

        targets.append(
            ExitTarget(
                candidate_id=row["candidate_id"],
                ticker=row["ticker"],
                name=row["name"],
                candidate_status=row["candidate_status"],
                entry_price=row["entry_price"],
                entry_amount=row["entry_amount"],
                phase1_executed_price=row["entry_phase1_executed_price"],
                phase1_executed_shares=p1_shares,
                phase2_executed_price=row["entry_phase2_executed_price"],
                phase2_executed_shares=p2_shares,
                exit_shares=int(row["exit_shares"] or 0),
            )
        )

    logger.info(
        f"[exit_target_query] trade_date={trade_date} 매도 대상 {len(targets)}건 "
        f"(entered={sum(1 for t in targets if t.candidate_status=='entered')}, "
        f"phase1_only={sum(1 for t in targets if t.is_phase1_only)})"
    )
    return targets
