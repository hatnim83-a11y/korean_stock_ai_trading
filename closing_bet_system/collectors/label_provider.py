"""closing_bet_system.collectors.label_provider

종가베팅 T+1 사후 라벨링.

PRD 9-2 라벨 정의:
- ``label_gap_up``: T+1 시초가가 어제 종가 대비 +0.5% 이상 갭상승
- ``label_morning_exit``: T+1 09:30~10:30 고가가 비용 차감 후 익절선 도달
- ``label_stop_risk``: T+1 09:30~10:30 저가가 손절선(-1.5%) 이하 진입
- ``label_net_ev_positive``: 시뮬 매도가 비용·세금 차감 후 양수

호출 시점: APScheduler 매일 10:00 KST (``MainOrchestrator.run_label_yesterday``)
→ 어제 recommended/entered 후보별로 ``get_label(ticker)`` 호출.

데이터 출처: ``KISApi.get_daily_price(ticker, period="D", count=5)``
- ``rows[0]`` = 가장 최근 일자 (KIS API ``inquire-daily-price`` 는 **내림차순** 반환)
  → T+1 호출 시점에서는 오늘(T+1) OHLC
- ``rows[1]`` = 어제 (T) 종가
- 정렬 방향 검증: ``kis_price_volume_collector.py:241`` 에서도 같은 가정으로 운영 중.
  추가 sanity check 로 ``rows[0].date >= rows[1].date`` 확인 → 위반 시 None 반환.

실패 정책: KIS 호출/데이터 부족 시 ``None`` 반환 → orchestrator 가 자연 스킵.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger


# ===== 라벨 임계값 (PRD 9-2 + settings.yaml exit) =====

# settings.yaml `exit:` 섹션에서 동적 로드 (Phase 1 단순화: 모듈 상수, Phase 3에서 주입)
LABEL_GAP_UP_THRESHOLD_PCT = 0.005       # T+1 시초가 +0.5%↑ → gap_up=True
LABEL_STOP_RISK_THRESHOLD_PCT = -0.015   # T+1 저가 -1.5%↓ → stop_risk=True

# label_morning_exit / label_net_ev_positive 는 cost_engine.minimum_target_return() 사용


# ===== 메인 진입점 =====


def get_label(ticker: str) -> Optional[dict]:
    """1종목 T+1 라벨 dict 반환. 데이터 부족 시 ``None``.

    Returns:
        dict: {
            ``next_open_pct``: float | None,         # T+1 시초가 등락률 (소수)
            ``next_morning_high_pct``: float | None, # T+1 고가 등락률 (소수)
            ``next_morning_low_pct``: float | None,  # T+1 저가 등락률 (소수)
            ``label_gap_up``: bool | None,
            ``label_morning_exit``: bool | None,
            ``label_stop_risk``: bool | None,
            ``label_net_ev_positive``: bool | None,
        }
        None: KIS 조회 실패 / 데이터 < 2 행 / 종가 0
    """
    if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
        logger.warning(f"[label_provider] 잘못된 ticker: {ticker!r}")
        return None

    ohlc = _fetch_recent_ohlc(ticker)
    if ohlc is None:
        return None
    today_ohlc, prev_close = ohlc

    if not prev_close or prev_close <= 0:
        logger.debug(f"[label_provider] {ticker} 어제 종가 0/음수 — 스킵")
        return None

    today_open = today_ohlc.get("open")
    today_high = today_ohlc.get("high")
    today_low = today_ohlc.get("low")

    next_open_pct = _pct(today_open, prev_close)
    next_morning_high_pct = _pct(today_high, prev_close)
    next_morning_low_pct = _pct(today_low, prev_close)

    # 라벨 4개 (PRD 9-2)
    label_gap_up = (
        next_open_pct >= LABEL_GAP_UP_THRESHOLD_PCT
        if next_open_pct is not None else None
    )
    label_stop_risk = (
        next_morning_low_pct <= LABEL_STOP_RISK_THRESHOLD_PCT
        if next_morning_low_pct is not None else None
    )
    target_return = _safe_minimum_target_return()
    if target_return is None or next_morning_high_pct is None:
        label_morning_exit = None
        label_net_ev_positive = None
    else:
        # Phase 1 단순화: morning_exit 와 net_ev_positive 가 같은 임계값 사용.
        # PRD 9-2 의 두 라벨 정의가 모두 "비용 차감 후 익절선 도달" 로 수렴되므로
        # 같은 조건으로 처리. Phase 3 ML 도입 시 별도 임계값 (보유 시간/수익률 분포) 으로 분리 예정.
        label_morning_exit = next_morning_high_pct >= target_return
        label_net_ev_positive = label_morning_exit

    return {
        "next_open_pct": next_open_pct,
        "next_morning_high_pct": next_morning_high_pct,
        "next_morning_low_pct": next_morning_low_pct,
        "label_gap_up": label_gap_up,
        "label_morning_exit": label_morning_exit,
        "label_stop_risk": label_stop_risk,
        "label_net_ev_positive": label_net_ev_positive,
    }


# ===== 내부 헬퍼 =====


def _fetch_recent_ohlc(ticker: str) -> Optional[tuple[dict, float]]:
    """KIS get_daily_price → (today_ohlc dict, prev_close float). 실패 시 None.

    KIS 응답: 최신순 내림차순 (첫 행=오늘, 둘째 행=어제).
    """
    try:
        from closing_bet_system.infra.kis_client import get_kis_api
        kis = get_kis_api()
        rows = kis.get_daily_price(ticker, period="D", count=5)
    except Exception as e:
        logger.warning(f"[label_provider] {ticker} KIS get_daily_price 예외: {e}")
        return None

    if not rows or len(rows) < 2:
        logger.debug(f"[label_provider] {ticker} 일봉 데이터 부족 (< 2행)")
        return None

    today_row = rows[0]
    prev_row = rows[1]

    # 정렬 방향 sanity check (KIS API 가 내림차순이라는 가정 검증)
    today_date = str(today_row.get("date") or "").strip()
    prev_date = str(prev_row.get("date") or "").strip()
    if today_date and prev_date and today_date < prev_date:
        logger.error(
            f"[label_provider] {ticker} KIS 정렬 방향 비정상 — "
            f"rows[0]={today_date} < rows[1]={prev_date} (내림차순 가정 위반). 라벨링 스킵."
        )
        return None

    prev_close = prev_row.get("close")
    if prev_close is None:
        return None
    try:
        prev_close = float(prev_close)
    except (TypeError, ValueError):
        return None

    today_ohlc = {
        "open": _to_float(today_row.get("open")),
        "high": _to_float(today_row.get("high")),
        "low": _to_float(today_row.get("low")),
        "close": _to_float(today_row.get("close")),
    }
    return today_ohlc, prev_close


def _pct(value: Optional[float], base: float) -> Optional[float]:
    """등락률 계산 (소수). value/base 무효 시 None."""
    if value is None or base is None or base == 0:
        return None
    try:
        return round((float(value) - float(base)) / float(base), 6)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    """안전 float 변환. 실패 시 None."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_minimum_target_return() -> Optional[float]:
    """``CostSlippageEngine.minimum_target_return()`` 호출 + 예외 흡수.

    Phase 1 모듈 의존성: settings.yaml `cost.*` 기본값 기준 약 0.0091 (0.91%).
    """
    try:
        from closing_bet_system.engines.cost_slippage_engine import get_engine
        engine = get_engine()
        return float(engine.minimum_target_return())
    except Exception as e:
        logger.warning(f"[label_provider] minimum_target_return 호출 예외: {e}")
        return None
