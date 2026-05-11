"""closing_bet_system.collectors.kis_intraday_flow_collector

PRD 5-Layer 1 — 장중 수급 지표 스냅샷 수집기.

**책임**:
1. settings.yaml 의 4회 스냅샷 시점(15:00 / 15:10 / 15:20 / 15:28)에 KIS API 호출
2. 종목별 수급 데이터 → ``IntradayFlowSnapshot`` 변환
3. ``candidate_features`` 의 Layer 1 컬럼 dict 변환

**책임 외** (다른 단위에서):
- DB 저장 → 1-6 ``candidate_logger``
- 스케줄러 등록 → 1-8 ``main_orchestrator``
- 신뢰도 추적 → Phase 2 ``flow_reliability_tracker``

**PRD 5-Layer 1 4지표 가용성** (Phase 1 단계):
=========================================  ===================================
지표                                       Phase 1 가용성
=========================================  ===================================
``inst_net_buy_estimated``                 ✅ KIS ``get_investor_trend_estimate`` (HHPTJ04160200) 14:30 가집계 누계
                                            폴백: ``get_investor_trading`` daily[0] (15:10 시점 inst=0 박제 가능)
``foreign_net_buy_3d``                     ✅ ``get_investor_trading`` 의 3일 외국인 누적
``program_net_buy_change``                 ⚠️ 종목별 프로그램 매매 TR 미확인 → ``None``
``closing_flow_concentration``             ⚠️ 분단위 수급 데이터 필요 → ``None``
=========================================  ===================================

**inst_net_buy_estimated 데이터 소스 우선순위** (단위 2-3 옵션 H, 2026-05-11 도입):
1. HHPTJ04160200 ``latest_inst_qty * latest_close`` (14:30 가집계, 15:10 시점 가용)
2. 폴백: FHKST01010900 ``daily[0].institution * close_price`` (장중 inst=0 박제 위험)
3. 둘 다 실패: ``None``

**Layer 1 가중치 정책 (P2-6)**: settings.yaml ``score.layer1_weight=0.0`` 으로 시작.
flow_reliability 누적 후 KRX 확정값과의 방향 일치율 70% 이상 검증되면 활성화.
즉 Phase 1 의 본 collector 출력은 **로깅용** (점수에는 미반영).
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


@dataclass(frozen=True)
class IntradayFlowSnapshot:
    """1종목 1회 수급 스냅샷.

    PRD 5-Layer 1 4지표 + 메타데이터.

    **단위 주의 (Phase 2 정규화 예정)**:
    - ``inst_net_buy_estimated`` / ``foreign_net_buy_3d`` 는 **원화 절대값**.
    - PRD 5-Layer 1 정의는 "거래대금 대비 비율 (Z-score)" / "유동시총 대비 비율"
      이므로 Phase 2 점수 계산 단계에서 정규화 필요. 본 collector 는 raw 값만 저장.
    """

    ticker: str
    snapshot_time: datetime               # KST timezone-aware
    is_valid: bool                         # API 호출 성공 여부
    is_today: bool = False                 # daily[0] 날짜가 오늘 KST 와 일치하는지

    # Layer 1 지표 (Phase 1 가용 2개 + 미가용 2개)
    inst_net_buy_estimated: Optional[float] = None       # 당일 기관 순매수 (원, 절대값)
    foreign_net_buy_3d: Optional[float] = None           # 3일 외국인 누적 순매수 (원, 절대값)
    foreign_days_collected: int = 0                       # foreign_net_buy_3d 가 실제로 합산한 일수 (1~3)
    program_net_buy_change: Optional[float] = None       # Phase 2: 프로그램 매수 변화율
    closing_flow_concentration: Optional[float] = None   # Phase 2: 마감 수급 집중도

    # 보조 정보
    foreign_ratio: Optional[float] = None                # 외국인 보유 비율 (%)
    # raw_payload 는 dict (unhashable) 이라 frozen + hash 충돌 방지: hash/eq 제외
    raw_payload: Optional[dict] = field(default=None, hash=False, compare=False)

    def to_layer1_features(self) -> dict[str, Optional[float]]:
        """``candidate_features`` 테이블 Layer 1 컬럼 dict.

        DB schema_v1 의 4 컬럼명과 1:1 매핑.
        """
        return {
            "inst_net_buy_estimated": self.inst_net_buy_estimated,
            "foreign_net_buy_3d": self.foreign_net_buy_3d,
            "program_net_buy_change": self.program_net_buy_change,
            "closing_flow_concentration": self.closing_flow_concentration,
        }


class KisIntradayFlowCollector:
    """KIS API 기반 장중 수급 스냅샷 수집기.

    Args:
        kis_api: 의존성 주입 — 미지정 시 ``infra.kis_client.get_kis_api()`` 사용.

    **비동기 호환 (1-8 main_orchestrator 시점)**:
    본 클래스는 동기 메서드만 제공. ``collect_for_universe(N)`` 는 KIS rate_limit
    (분당 호출 제한) 으로 인해 30종목 기준 수초 소요 가능. AsyncIOScheduler
    이벤트 루프 블로킹을 피하려면 1-8 에서 ``asyncio.to_thread(...)`` 로 감쌀 것::

        snapshots = await asyncio.to_thread(
            collector.collect_for_universe, tickers, snapshot_time
        )
    """

    def __init__(self, kis_api: Optional[Any] = None):
        self._kis_api = kis_api

    @property
    def kis(self) -> Any:
        """KISApi 지연 로딩 (토큰 발급 회피)."""
        if self._kis_api is None:
            from closing_bet_system.infra.kis_client import get_kis_api

            self._kis_api = get_kis_api()
        return self._kis_api

    # ===== 단일 종목 =====

    def collect_snapshot(
        self,
        ticker: str,
        snapshot_time: Optional[datetime] = None,
    ) -> IntradayFlowSnapshot:
        """1종목 1회 수급 스냅샷.

        Args:
            ticker: 6자리 종목코드
            snapshot_time: 스냅샷 시각 (None 이면 now_kst)

        Returns:
            ``IntradayFlowSnapshot`` (실패 시 ``is_valid=False``)
        """
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            logger.warning(f"[flow_collector] 유효하지 않은 종목코드: {ticker!r}")
            return IntradayFlowSnapshot(
                ticker=str(ticker), snapshot_time=snapshot_time or now_kst(), is_valid=False
            )

        ts = snapshot_time or now_kst()

        try:
            payload = self.kis.get_investor_trading(ticker, days=5)
        except Exception as e:
            logger.error(f"[flow_collector] {ticker} API 호출 예외: {e}")
            return IntradayFlowSnapshot(ticker=ticker, snapshot_time=ts, is_valid=False)

        if not payload or "daily" not in payload:
            logger.warning(f"[flow_collector] {ticker} 응답 비어있음 또는 daily 누락")
            return IntradayFlowSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, raw_payload=payload
            )

        # HHPTJ04160200 가집계 (inst 보강용, 단위 2-3 옵션 H). 실패 시 None → FHKST 폴백.
        try:
            trend_payload = self.kis.get_investor_trend_estimate(ticker)
        except Exception as e:
            logger.warning(f"[flow_collector] {ticker} HHPTJ 호출 예외 (FHKST 폴백): {e}")
            trend_payload = None

        return self._parse_payload(ticker, ts, payload, trend_payload)

    # ===== 다종목 =====

    def collect_for_universe(
        self,
        tickers: list[str],
        snapshot_time: Optional[datetime] = None,
    ) -> list[IntradayFlowSnapshot]:
        """여러 종목 순차 수집. 한 종목 실패 시 다른 종목 계속 진행."""
        ts = snapshot_time or now_kst()
        snapshots: list[IntradayFlowSnapshot] = []
        success = 0
        for ticker in tickers:
            snap = self.collect_snapshot(ticker, ts)
            snapshots.append(snap)
            if snap.is_valid:
                success += 1
        logger.info(
            f"[flow_collector] 스냅샷 {success}/{len(tickers)}건 성공 (시각: {ts.strftime('%H:%M:%S')})"
        )
        return snapshots

    # ===== 응답 파싱 =====

    def _parse_payload(
        self,
        ticker: str,
        snapshot_time: datetime,
        payload: dict,
        trend_payload: Optional[dict] = None,
    ) -> IntradayFlowSnapshot:
        """KIS API ``get_investor_trading`` (+ HHPTJ 가집계) 응답 → ``IntradayFlowSnapshot``.

        KIS 반환 형식 (``modules/stock_screener/kis_api.py:594``):
        ::

            {
                "code": "005930",
                "foreign_net": int (N일 합계, 원),
                "institution_net": int,
                "individual_net": int,
                "foreign_ratio": float,
                "daily": [
                    {"date": str, "foreign": qty, "institution": qty,
                     "individual": qty, "close_price": int},
                    ...
                ]
            }

        ``daily[0]`` = 가장 최근 일자 (장중에는 추정값일 가능성 높음 — Phase 2 신뢰도 추적).

        ``trend_payload`` (HHPTJ04160200, 단위 2-3 옵션 H):
        ::

            {
                "code", "latest_inst_qty", "latest_foreign_qty", "latest_sum_qty",
                "by_slot": [{"slot_gb", "frgn", "orgn", "sum"}, ...]
            }

        inst_net_buy_estimated 산출 시 HHPTJ ``latest_inst_qty`` 우선, 실패 시 FHKST daily[0] 폴백.
        """
        daily = payload.get("daily") or []
        if not isinstance(daily, list) or not daily:
            logger.warning(f"[flow_collector] {ticker} daily 비어있음 또는 list 아님")
            return IntradayFlowSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=False, raw_payload=payload
            )

        latest = daily[0]
        if not isinstance(latest, dict):
            logger.warning(f"[flow_collector] {ticker} daily[0] dict 아님: {type(latest)}")
            return IntradayFlowSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=False, raw_payload=payload
            )

        # 날짜 검증 — daily[0] 가 오늘 KST 와 일치하는지 (KIS TR 정렬 미보장 대응)
        today_str = snapshot_time.astimezone(now_kst().tzinfo).strftime("%Y%m%d")
        latest_date = str(latest.get("date") or "").strip()
        is_today = (latest_date == today_str)
        if not is_today:
            logger.warning(
                f"[flow_collector] {ticker} daily[0] date={latest_date!r} ≠ today={today_str} — "
                "장중 추정값 아닐 수 있음 (Phase 2 신뢰도 추적 시 주의)"
            )

        # 1) 당일 기관 순매수 (원) = institution qty × close_price
        # close_price 가 0/None 이면 NaN 방지 위해 None 반환 (장 시작 직후 미체결 상태 대응)
        # 단위 2-3 옵션 H: HHPTJ 가집계 우선 (FHKST daily[0].institution=0 박제 회피)
        latest_close = _to_float(latest.get("close_price"))
        if latest_close is None or latest_close <= 0:
            inst_net_buy_estimated = None
            if latest_close == 0:
                logger.warning(
                    f"[flow_collector] {ticker} close_price=0 — 미체결 상태 (inst_net=None 처리)"
                )
        else:
            inst_qty: Optional[float] = None
            if isinstance(trend_payload, dict):
                # HHPTJ 우선. 0이면 FHKST 폴백 (단, 실제 0주 거래와 미집계(빈 필드→_safe_int=0) 구분 불가).
                # Phase 2 점수 반영 시 by_slot[latest_slot] 유무로 미집계 상태 세분화 예정.
                trend_qty = _to_float(trend_payload.get("latest_inst_qty"))
                if trend_qty is not None and trend_qty != 0:
                    inst_qty = trend_qty
            # 폴백: FHKST daily[0].institution
            if inst_qty is None:
                fallback_qty = _to_float(latest.get("institution"))
                if fallback_qty is not None:
                    inst_qty = fallback_qty
                    if isinstance(trend_payload, dict):
                        # HHPTJ는 호출 성공했으나 inst=0 → FHKST 폴백 사용 명시
                        logger.debug(
                            f"[flow_collector] {ticker} HHPTJ inst=0 → FHKST 폴백 사용"
                        )

            if inst_qty is None:
                inst_net_buy_estimated = None
            else:
                inst_net_buy_estimated = inst_qty * latest_close

        # 2) 3일 외국인 누적 (원) = sum of (foreign qty × close_price) for daily[0:3]
        foreign_3d_total = 0.0
        foreign_3d_count = 0
        for entry in daily[:3]:
            if not isinstance(entry, dict):
                continue
            foreign_qty = _to_float(entry.get("foreign"))
            close = _to_float(entry.get("close_price"))
            if foreign_qty is not None and close is not None and close > 0:
                foreign_3d_total += foreign_qty * close
                foreign_3d_count += 1
        foreign_net_buy_3d = foreign_3d_total if foreign_3d_count > 0 else None
        if 0 < foreign_3d_count < 3:
            logger.info(
                f"[flow_collector] {ticker} foreign_net_buy_3d 가 {foreign_3d_count}일치만 합산"
            )

        # 3,4) program / closing_flow_concentration: Phase 2 정밀화 (현재 None)

        return IntradayFlowSnapshot(
            ticker=ticker,
            snapshot_time=snapshot_time,
            is_valid=True,
            is_today=is_today,
            inst_net_buy_estimated=inst_net_buy_estimated,
            foreign_net_buy_3d=foreign_net_buy_3d,
            foreign_days_collected=foreign_3d_count,
            program_net_buy_change=None,        # Phase 2
            closing_flow_concentration=None,     # Phase 2
            foreign_ratio=_to_float(payload.get("foreign_ratio")),
            raw_payload=payload,
        )


# ===== 헬퍼 =====


def _to_float(value: Any) -> Optional[float]:
    """KIS 응답 값을 float 로 안전 변환. None / 빈 문자열 / 변환 실패 시 None.

    프로젝트 표준: ``CLAUDE.md`` 의 KIS 응답 파싱 규칙 ("``_safe_float()`` 사용")을
    이 collector 가 받는 dict 가 이미 ``_safe_int`` 처리된 후이므로 후처리 보강.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/inf 차단
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
