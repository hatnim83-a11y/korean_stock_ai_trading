"""closing_bet_system.collectors.kis_price_volume_collector

PRD 5-Layer 2 — 가격/거래량 지표 스냅샷 수집기.

**책임**:
1. settings.yaml 의 4회 스냅샷 시점(15:00 / 15:10 / 15:20 / 15:28)에 KIS API 호출
2. 종목별 일봉 OHLCV → ``PriceVolumeSnapshot`` 변환
3. ``candidate_features`` 의 Layer 2 6컬럼 dict 변환

**책임 외** (다른 단위에서):
- DB 저장 → 1-6 ``candidate_logger``
- 스케줄러 등록 → 1-8 ``main_orchestrator``
- 점수 계산 → 1-4 ``signal_score_engine``

**PRD 5-Layer 2 6지표 가용성** (Phase 1 단계):
=========================================  ===================================
지표                                       Phase 1 가용성
=========================================  ===================================
``close_strength``                         ✅ (close − low) / (high − low)
``upper_shadow_atr``                       ✅ (high − close) / atr14
``volume_surprise``                        ✅ volume / vol_avg20
``atr_overheat``                           ✅ (close − prev_close) / atr14
``last_30min_vwap_position``               ⚠️ 분봉 데이터 필요 → ``None``
``closing_buy_sell_ratio``                 ⚠️ 체결강도 필요 → ``None``
=========================================  ===================================

**보조 정보** (Layer 2 점수 항목 중 "20일선 위/아래" 판단용):
- ``above_ma20``: close > ma20

**ATR 계산**: True Range (max of HL, |H-PC|, |L-PC|) 의 14일 SMA.
0-C 백테스트 (``backtest/daily_proxy_backtest.py:compute_layer2_features``) 와 SMA 방식은 동일.

**백테스트와의 차이 (보수적 설계)**:
``_compute_atr`` 는 14일 윈도우 안에 거래정지일(high/low/prev_close <= 0) 발견 시 즉시 ``None``
반환. 백테스트는 ``rolling(window=14)`` 이라 거래정지 TR(=0 근사) 을 평균에 포함시키는 차이.
실거래 collector 는 거래정지 종목이 후보로 도달하면 ATR 기반 지표(atr_overheat /
upper_shadow_atr)를 계산하지 않는 쪽이 안전 — backtest 와 미세 수치 차이 가능.
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


# 윈도우 상수 — PRD 5-Layer 2 정의 + 백테스트와 일관성 유지
ATR_PERIOD = 14
MA_PERIOD = 20
VOL_AVG_PERIOD = 20

# KIS get_daily_price 권장 count: 위 윈도우 모두 + 여유
DEFAULT_DAILY_COUNT = 60


@dataclass(frozen=True)
class PriceVolumeSnapshot:
    """1종목 1회 가격/거래량 스냅샷.

    PRD 5-Layer 2 6지표 + 보조 정보(OHLCV / MA20 / 일수).

    **단위 주의**:
    - ``close_strength`` : 0~1 비율
    - ``upper_shadow_atr`` / ``atr_overheat`` : ATR 배수 (단위 없음)
    - ``volume_surprise`` : 20일 평균 대비 배수
    - ``daily_change_pct`` : -1~+1 (소수 — % 환산은 호출처)
    """

    ticker: str
    snapshot_time: datetime               # KST timezone-aware
    is_valid: bool                         # API 호출 성공 + 최소 윈도우 충족
    is_today: bool = False                 # daily[0] 날짜가 오늘 KST 와 일치하는지

    # 당일 raw OHLCV (sanity check / 디버깅 / 점수 계산 보조)
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    prev_close: Optional[float] = None
    daily_change_pct: Optional[float] = None

    # 윈도우 통계
    atr14: Optional[float] = None
    ma20: Optional[float] = None
    vol_avg20: Optional[float] = None
    days_collected: int = 0                # 실제 사용한 일봉 수 (max == DEFAULT_DAILY_COUNT)

    # Layer 2 6지표 — DB candidate_features 1:1 매핑
    close_strength: Optional[float] = None           # (close - low) / (high - low)
    upper_shadow_atr: Optional[float] = None         # (high - close) / atr14
    last_30min_vwap_position: Optional[float] = None  # Phase 2: 분봉 필요
    closing_buy_sell_ratio: Optional[float] = None    # Phase 2: 체결강도 필요
    volume_surprise: Optional[float] = None           # volume / vol_avg20
    atr_overheat: Optional[float] = None              # (close - prev_close) / atr14

    # 보조 정보 (Layer 2 점수 4점 항목 중 "20일선 위" 판단용)
    above_ma20: Optional[bool] = None

    # raw_payload 는 list[dict] (unhashable) → frozen + hash=False 로 안전성 확보
    raw_payload: Optional[list] = field(default=None, hash=False, compare=False)

    def to_layer2_features(self) -> dict[str, Optional[float]]:
        """``candidate_features`` 테이블 Layer 2 6컬럼 dict.

        DB schema_v1 의 6 컬럼명과 1:1 매핑.
        """
        return {
            "close_strength": self.close_strength,
            "upper_shadow_atr": self.upper_shadow_atr,
            "last_30min_vwap_position": self.last_30min_vwap_position,
            "closing_buy_sell_ratio": self.closing_buy_sell_ratio,
            "volume_surprise": self.volume_surprise,
            "atr_overheat": self.atr_overheat,
        }


class KisPriceVolumeCollector:
    """KIS API 기반 가격/거래량 스냅샷 수집기.

    Args:
        kis_api: 의존성 주입 — 미지정 시 ``infra.kis_client.get_kis_api()`` 사용.
        daily_count: KIS ``get_daily_price`` 호출 시 일봉 수 (기본 60).

    **비동기 호환 (1-8 main_orchestrator 시점)**:
    본 클래스는 동기 메서드만 제공. ``collect_for_universe(N)`` 는 KIS rate_limit
    (분당 호출 제한) 으로 인해 30종목 기준 수초 소요 가능. AsyncIOScheduler
    이벤트 루프 블로킹을 피하려면 1-8 에서 ``asyncio.to_thread(...)`` 로 감쌀 것::

        snapshots = await asyncio.to_thread(
            collector.collect_for_universe, tickers, snapshot_time
        )
    """

    def __init__(
        self,
        kis_api: Optional[Any] = None,
        daily_count: int = DEFAULT_DAILY_COUNT,
    ):
        self._kis_api = kis_api
        # 최소 ATR + MA + vol_avg 윈도우 + 1일(prev_close) 확보
        self._daily_count = max(daily_count, MA_PERIOD + 1)

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
    ) -> PriceVolumeSnapshot:
        """1종목 1회 가격/거래량 스냅샷.

        Args:
            ticker: 6자리 종목코드
            snapshot_time: 스냅샷 시각 (None 이면 now_kst)

        Returns:
            ``PriceVolumeSnapshot`` (실패 시 ``is_valid=False``)
        """
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            logger.warning(f"[price_volume_collector] 유효하지 않은 종목코드: {ticker!r}")
            return PriceVolumeSnapshot(
                ticker=str(ticker), snapshot_time=snapshot_time or now_kst(), is_valid=False
            )

        ts = snapshot_time or now_kst()

        try:
            payload = self.kis.get_daily_price(ticker, period="D", count=self._daily_count)
        except Exception as e:
            logger.error(f"[price_volume_collector] {ticker} API 호출 예외: {e}")
            return PriceVolumeSnapshot(ticker=ticker, snapshot_time=ts, is_valid=False)

        if not payload or not isinstance(payload, list):
            logger.warning(f"[price_volume_collector] {ticker} 응답 비어있음 또는 list 아님")
            return PriceVolumeSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, raw_payload=None
            )

        return self._parse_payload(ticker, ts, payload)

    # ===== 다종목 =====

    def collect_for_universe(
        self,
        tickers: list[str],
        snapshot_time: Optional[datetime] = None,
    ) -> list[PriceVolumeSnapshot]:
        """여러 종목 순차 수집. 한 종목 실패 시 다른 종목 계속 진행."""
        ts = snapshot_time or now_kst()
        snapshots: list[PriceVolumeSnapshot] = []
        success = 0
        for ticker in tickers:
            snap = self.collect_snapshot(ticker, ts)
            snapshots.append(snap)
            if snap.is_valid:
                success += 1
        logger.info(
            f"[price_volume_collector] 스냅샷 {success}/{len(tickers)}건 성공 "
            f"(시각: {ts.strftime('%H:%M:%S')})"
        )
        return snapshots

    # ===== 응답 파싱 =====

    def _parse_payload(
        self,
        ticker: str,
        snapshot_time: datetime,
        payload: list[dict],
    ) -> PriceVolumeSnapshot:
        """KIS API ``get_daily_price`` 응답 → ``PriceVolumeSnapshot``.

        KIS 반환 형식 (``modules/stock_screener/kis_api.py:508``):
        ::

            [
                {"date": "20260102", "open": int, "high": int, "low": int,
                 "close": int, "volume": int, "trade_value": int},
                ...
            ]

        ``daily[0]`` = 가장 최근 일자 (KIS 표준 내림차순).
        """
        if not payload:
            return PriceVolumeSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=False, raw_payload=payload
            )

        latest = payload[0]
        if not isinstance(latest, dict):
            logger.warning(f"[price_volume_collector] {ticker} daily[0] dict 아님: {type(latest)}")
            return PriceVolumeSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=False, raw_payload=payload
            )

        # 날짜 검증 — daily[0] 가 오늘 KST 와 일치하는지 (장 마감 전 호출 시 당일 데이터)
        today_str = snapshot_time.astimezone(now_kst().tzinfo).strftime("%Y%m%d")
        latest_date = str(latest.get("date") or "").strip()
        is_today = (latest_date == today_str)
        if not is_today:
            logger.info(
                f"[price_volume_collector] {ticker} daily[0] date={latest_date!r} ≠ today={today_str} — "
                "비거래일/장 시작 전 호출 가능성 (sanity check)"
            )

        # 당일 OHLCV
        open_ = _to_float(latest.get("open"))
        high = _to_float(latest.get("high"))
        low = _to_float(latest.get("low"))
        close = _to_float(latest.get("close"))
        volume = _to_float(latest.get("volume"))

        # close <= 0 이면 미체결 (장 시작 직후) — Layer 2 지표 모두 None
        if close is None or close <= 0:
            logger.warning(
                f"[price_volume_collector] {ticker} close={close} — 미체결 (Layer 2 지표 None 처리)"
            )
            return PriceVolumeSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=True, is_today=is_today,
                open=open_, high=high, low=low, close=close, volume=volume,
                days_collected=len(payload), raw_payload=payload,
            )

        # 윈도우 미충족 시 — Layer 2 지표 미계산 (raw OHLCV 만 채움)
        # MA_PERIOD + 1 = prev_close 까지 확보 (atr_overheat 계산)
        if len(payload) < MA_PERIOD + 1:
            logger.warning(
                f"[price_volume_collector] {ticker} 일봉 {len(payload)}건 < {MA_PERIOD + 1}건 — "
                "윈도우 부족, 지표 None"
            )
            return PriceVolumeSnapshot(
                ticker=ticker, snapshot_time=snapshot_time, is_valid=True, is_today=is_today,
                open=open_, high=high, low=low, close=close, volume=volume,
                days_collected=len(payload), raw_payload=payload,
            )

        # 전일 종가
        prev = payload[1] if len(payload) > 1 else None
        prev_close = _to_float(prev.get("close")) if isinstance(prev, dict) else None

        # ATR(14) — payload[0..14] 의 True Range SMA
        # KIS 응답은 내림차순 → 슬라이싱 시 [최신, ..., 14일전]
        atr14 = self._compute_atr(payload, period=ATR_PERIOD)

        # MA20 — payload[0..20] close 평균
        ma20_values = [_to_float(d.get("close")) for d in payload[:MA_PERIOD] if isinstance(d, dict)]
        ma20_values = [v for v in ma20_values if v is not None and v > 0]
        ma20 = sum(ma20_values) / len(ma20_values) if len(ma20_values) == MA_PERIOD else None

        # vol_avg20 — payload[0..20] volume 평균
        # **MA20 과 비대칭 의도**: ma20 은 close>0 필터 (가격은 0 이면 무의미), vol_avg20 은 volume=0
        # (거래정지일) 도 포함. 0-C 백테스트의 ``rolling(window=20).mean()`` 과 동일 동작 — 거래정지일이
        # 평균을 낮추므로 volume_surprise 가 다소 과대평가될 수 있으나 백테스트와 수치 일관성 유지.
        vol20_values = [_to_float(d.get("volume")) for d in payload[:VOL_AVG_PERIOD] if isinstance(d, dict)]
        vol20_values = [v for v in vol20_values if v is not None]
        vol_avg20 = sum(vol20_values) / len(vol20_values) if len(vol20_values) == VOL_AVG_PERIOD else None

        # ===== Layer 2 6지표 계산 =====

        # 1) close_strength = (close - low) / (high - low)
        if high is not None and low is not None and high > low:
            close_strength = (close - low) / (high - low)
        else:
            close_strength = None  # 상하한가 / 데이터 결손

        # 2) upper_shadow_atr = (high - close) / atr14
        if high is not None and atr14 is not None and atr14 > 0:
            upper_shadow_atr = (high - close) / atr14
        else:
            upper_shadow_atr = None

        # 3) volume_surprise = volume / vol_avg20
        if volume is not None and vol_avg20 is not None and vol_avg20 > 0:
            volume_surprise = volume / vol_avg20
        else:
            volume_surprise = None

        # 4) atr_overheat = (close - prev_close) / atr14
        if prev_close is not None and prev_close > 0 and atr14 is not None and atr14 > 0:
            atr_overheat = (close - prev_close) / atr14
        else:
            atr_overheat = None

        # 5) daily_change_pct = (close - prev_close) / prev_close
        if prev_close is not None and prev_close > 0:
            daily_change_pct = (close - prev_close) / prev_close
        else:
            daily_change_pct = None

        # 6) above_ma20
        above_ma20 = (close > ma20) if ma20 is not None else None

        return PriceVolumeSnapshot(
            ticker=ticker,
            snapshot_time=snapshot_time,
            is_valid=True,
            is_today=is_today,
            open=open_, high=high, low=low, close=close, volume=volume,
            prev_close=prev_close,
            daily_change_pct=daily_change_pct,
            atr14=atr14, ma20=ma20, vol_avg20=vol_avg20,
            days_collected=len(payload),
            close_strength=close_strength,
            upper_shadow_atr=upper_shadow_atr,
            last_30min_vwap_position=None,        # Phase 2
            closing_buy_sell_ratio=None,           # Phase 2
            volume_surprise=volume_surprise,
            atr_overheat=atr_overheat,
            above_ma20=above_ma20,
            raw_payload=payload,
        )

    # ===== ATR 계산 =====

    @staticmethod
    def _compute_atr(payload: list[dict], period: int = ATR_PERIOD) -> Optional[float]:
        """True Range 의 ``period`` 일 단순 평균 (SMA).

        TR = max(H-L, |H-PC|, |L-PC|).
        payload 는 KIS 표준 내림차순 (payload[0] = 최신).

        반환:
            ATR 값. payload 에 ``period + 1`` 일 미만이면 ``None``.
            (TR 계산에 prev_close 필요 → period+1 일 필요)
        """
        if len(payload) < period + 1:
            return None

        tr_list: list[float] = []
        # payload[i] (최신 i=0) 의 TR 은 payload[i+1] 을 prev 로 사용
        for i in range(period):
            cur = payload[i]
            prev = payload[i + 1]
            if not (isinstance(cur, dict) and isinstance(prev, dict)):
                return None
            high = _to_float(cur.get("high"))
            low = _to_float(cur.get("low"))
            prev_close = _to_float(prev.get("close"))
            if high is None or low is None or prev_close is None:
                return None
            if high <= 0 or low <= 0 or prev_close <= 0:
                # 거래정지 / 미체결 일 — TR 계산 불가
                return None
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        if len(tr_list) != period:
            return None
        return sum(tr_list) / period


# ===== 헬퍼 =====


def _to_float(value: Any) -> Optional[float]:
    """KIS 응답 값을 float 로 안전 변환. None / 빈 문자열 / 변환 실패 시 None.

    프로젝트 표준: ``CLAUDE.md`` 의 KIS 응답 파싱 규칙 ("``_safe_float()`` 사용") 의
    후처리 보강 (이미 ``_safe_int`` 처리된 응답을 추가 방어).
    """
    if value is None:
        return None
    # bool 은 int 의 서브타입이지만 의미상 거리 — 차단
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/inf 차단
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
