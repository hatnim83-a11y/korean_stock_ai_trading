"""closing_bet_system.engines.signal_score_engine

PRD 6-1 — Phase 1~2 단순 조건 충족 점수 엔진.

**책임**:
1. Layer 1/2/3 피처 dict 입력 → 11점 만점 (Phase 1: weighted 7점) 스코어 계산
2. 하드 필터 (atr_overheat > 1.8) 적용
3. 진입 의사결정 (EXCLUDED / MAX_SIZE_ENTRY / ENTRY / ALERT / BELOW_THRESHOLD)

**책임 외** (다른 단위에서):
- 피처 수집 → 1-2 ``IntradayFlowSnapshot``, 1-3 ``PriceVolumeSnapshot``, 1-5a DART
- DB 저장 → 1-6 ``candidate_logger``
- 텔레그램 알림 → 1-7 ``telegram_review_bot``
- ML 모델 점수 → Phase 3 (본 엔진은 단순 카운트)

**점수 체계 (PRD 6-1)**:
=================  =====  ===========================================================
카테고리           만점   조건 (각 1점)
=================  =====  ===========================================================
Layer 1 (수급)     4점    기관 순매수 / 외국인 3일 양수 / 프로그램 매수 / 마감 집중
Layer 2 (가격)     4점    close_strength≥0.85 / vol≥2× / 20일선 위 / 막판체결≥1.2
Layer 3 (모멘텀)   3점    52주 신고가 근접 / 테마 주도주(rank≤3) / 긍정 공시
=================  =====  ===========================================================

**가중치 정책 (P2-6)**:
``layer1_weight=0.0`` 로 시작 → 1-9 검증 단계에서 KRX 확정값 대비 방향 일치율 70% 이상
검증되면 활성화 (Phase 2). 따라서 ``weighted_total`` 만점은 Phase 1 ``7점`` (L2+L3),
Phase 2+ ``11점`` (L1+L2+L3).

**Phase 1 의사결정 영향**:
``alert_min=7`` 임에도 Phase 1 weighted max 가 7 이라 ALERT 도 만점만 가능 (매우 보수적).
의도된 설계 — Phase 1 은 "데이터 수집 + 알림형" 이므로 ENTRY 미발생, 1-9 30건 누적 후 검토.

**입력 dict 키 규약**:
- ``layer1``: ``inst_net_buy_estimated``, ``foreign_net_buy_3d``,
  ``program_net_buy_change``, ``closing_flow_concentration``
- ``layer2``: ``close_strength``, ``volume_surprise``, ``above_ma20``,
  ``closing_buy_sell_ratio``, ``atr_overheat``
  *(``upper_shadow_atr`` / ``last_30min_vwap_position`` 는 본 엔진 미사용 — 1-7 알림 본문)*
- ``layer3``: ``near_52w_high`` (bool), ``theme_leadership_rank`` (int),
  ``has_positive_disclosure`` (bool)

값이 ``None`` 이면 해당 조건 = ``None`` (점수 0). 하드 필터 ``atr_overheat`` 가 ``None``
이면 **보수적으로 EXCLUDED** (측정 불가 시 진입 차단).
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


# ===== 모듈 상수 (기본 임계값 — settings.yaml 또는 __init__ 인자로 오버라이드) =====

# 가중치 (PRD 6-1 + P2-6)
DEFAULT_LAYER1_WEIGHT = 0.0
DEFAULT_LAYER2_WEIGHT = 1.0
DEFAULT_LAYER3_WEIGHT = 1.0

# 진입 기준 (PRD 6-1, 11점 만점 기준)
DEFAULT_ALERT_MIN = 7.0
DEFAULT_ENTRY_MIN = 8.0
DEFAULT_MAX_POSITION_SCORE = 9.0

# Layer 2 임계값 (PRD 5-Layer 2 + 6-1)
DEFAULT_CLOSE_STRENGTH_MIN = 0.85
DEFAULT_VOLUME_SURPRISE_MIN = 2.0
DEFAULT_CLOSING_BUY_SELL_RATIO_MIN = 1.2
DEFAULT_ATR_OVERHEAT_MAX = 1.8        # 하드 필터 (PRD 5-Layer 2)

# Layer 3 임계값 (PRD 5-Layer 3 + 6-1)
DEFAULT_THEME_LEADERSHIP_RANK_MAX = 3  # 테마 내 상승률 상위 3위 (PRD 5-Layer 3)


# ===== 결과 dataclass =====


@dataclass(frozen=True)
class ScoreBreakdown:
    """1종목 1회 점수 산출 결과.

    각 조건은 ``True`` (충족) / ``False`` (미충족) / ``None`` (입력 결손).
    ``layerN_subscore`` 는 ``True`` 개수만 카운트 (None/False 둘 다 0).
    """

    ticker: str
    scored_at: datetime                # KST timezone-aware

    # Layer 1 (수급) — 4 조건
    cond_inst_net_buy: Optional[bool] = None
    cond_foreign_3d: Optional[bool] = None
    cond_program_net_buy: Optional[bool] = None
    cond_closing_flow: Optional[bool] = None
    layer1_subscore: int = 0           # 0~4

    # Layer 2 (가격/거래량) — 4 조건
    cond_close_strength: Optional[bool] = None
    cond_volume_surprise: Optional[bool] = None
    cond_above_ma20: Optional[bool] = None
    cond_closing_buy_sell: Optional[bool] = None
    layer2_subscore: int = 0           # 0~4

    # Layer 3 (모멘텀) — 3 조건
    cond_near_52w_high: Optional[bool] = None
    cond_theme_leadership: Optional[bool] = None
    cond_positive_disclosure: Optional[bool] = None
    layer3_subscore: int = 0           # 0~3

    # 합계
    raw_total: int = 0                 # 0~11 (가중치 무시한 단순 합)
    weighted_total: float = 0.0        # layer1_weight*L1 + layer2_weight*L2 + layer3_weight*L3

    # 하드 필터 (atr_overheat > 1.8 → 제외)
    atr_overheat_value: Optional[float] = None
    excluded_by_filter: bool = False
    exclusion_reason: Optional[str] = None

    # 의사결정
    decision: str = "BELOW_THRESHOLD"
    # 가능한 값:
    #   "EXCLUDED"           — 하드 필터 (atr_overheat > 1.8 또는 None) 또는 missing 필수 입력
    #   "MAX_SIZE_ENTRY"     — weighted_total >= max_position_score AND market_ok
    #   "ENTRY"              — weighted_total >= entry_min
    #   "ALERT"              — weighted_total >= alert_min
    #   "BELOW_THRESHOLD"    — 알림 기준 미달
    decision_reason: str = ""

    # 디버깅 정보 — None 처리된 입력 키 목록 (1-7 알림 본문에 활용 가능)
    missing_inputs: tuple = field(default_factory=tuple, hash=False, compare=False)


# ===== 엔진 =====


class SignalScoreEngine:
    """PRD 6-1 단순 카운트 점수 엔진.

    Args:
        layer1_weight: Phase 1 = 0.0, Phase 2+ 활성화 후 1.0
        layer2_weight / layer3_weight: 기본 1.0
        alert_min / entry_min / max_position_score: 진입 임계값 (11점 만점 기준)
        close_strength_min / volume_surprise_min / closing_buy_sell_ratio_min:
            Layer 2 4조건 임계값
        atr_overheat_max: 하드 필터 임계값 (초과 시 제외)
        theme_leadership_rank_max: Layer 3 테마 주도주 조건 (rank <= 값)

    Examples:
        >>> from closing_bet_system.engines.signal_score_engine import SignalScoreEngine
        >>> eng = SignalScoreEngine()
        >>> bd = eng.score(
        ...     ticker="005930",
        ...     layer1={"inst_net_buy_estimated": 1e9, "foreign_net_buy_3d": 5e9,
        ...             "program_net_buy_change": None, "closing_flow_concentration": None},
        ...     layer2={"close_strength": 0.9, "volume_surprise": 2.5,
        ...             "above_ma20": True, "closing_buy_sell_ratio": None,
        ...             "atr_overheat": 1.0},
        ...     layer3={"near_52w_high": True, "theme_leadership_rank": 1,
        ...             "has_positive_disclosure": False},
        ...     market_ok=True,
        ... )
        >>> bd.weighted_total
        6.0
        >>> bd.decision
        'BELOW_THRESHOLD'
    """

    def __init__(
        self,
        *,
        layer1_weight: float = DEFAULT_LAYER1_WEIGHT,
        layer2_weight: float = DEFAULT_LAYER2_WEIGHT,
        layer3_weight: float = DEFAULT_LAYER3_WEIGHT,
        alert_min: float = DEFAULT_ALERT_MIN,
        entry_min: float = DEFAULT_ENTRY_MIN,
        max_position_score: float = DEFAULT_MAX_POSITION_SCORE,
        close_strength_min: float = DEFAULT_CLOSE_STRENGTH_MIN,
        volume_surprise_min: float = DEFAULT_VOLUME_SURPRISE_MIN,
        closing_buy_sell_ratio_min: float = DEFAULT_CLOSING_BUY_SELL_RATIO_MIN,
        atr_overheat_max: float = DEFAULT_ATR_OVERHEAT_MAX,
        theme_leadership_rank_max: int = DEFAULT_THEME_LEADERSHIP_RANK_MAX,
    ):
        # 가중치 검증 (음수 차단)
        for name, val in [
            ("layer1_weight", layer1_weight),
            ("layer2_weight", layer2_weight),
            ("layer3_weight", layer3_weight),
        ]:
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                raise ValueError(f"{name} 는 0 이상의 수: got {val!r}")
        # 임계값 순서 검증 (alert <= entry <= max_position)
        if not (alert_min <= entry_min <= max_position_score):
            raise ValueError(
                f"임계값 순서 오류: alert_min({alert_min}) <= entry_min({entry_min}) "
                f"<= max_position_score({max_position_score}) 위반"
            )

        self.layer1_weight = float(layer1_weight)
        self.layer2_weight = float(layer2_weight)
        self.layer3_weight = float(layer3_weight)
        self.alert_min = float(alert_min)
        self.entry_min = float(entry_min)
        self.max_position_score = float(max_position_score)
        self.close_strength_min = float(close_strength_min)
        self.volume_surprise_min = float(volume_surprise_min)
        self.closing_buy_sell_ratio_min = float(closing_buy_sell_ratio_min)
        self.atr_overheat_max = float(atr_overheat_max)
        self.theme_leadership_rank_max = int(theme_leadership_rank_max)

    # ===== settings.yaml 진입점 =====

    @classmethod
    def from_settings(cls, score_settings: dict) -> "SignalScoreEngine":
        """settings.yaml ``score`` 섹션 dict 로 엔진 생성.

        지원 키 (모두 선택, 미지정 시 모듈 default 사용):
        - ``layer1_weight`` / ``layer2_weight`` / ``layer3_weight``
        - ``min_alert`` / ``min_entry`` / ``max_position_score``
        - ``close_strength_min`` / ``volume_surprise_min``
        - ``closing_buy_sell_ratio_min`` / ``atr_overheat_max``
        - ``theme_leadership_rank_max``
        """
        if not isinstance(score_settings, dict):
            raise TypeError(f"score_settings 는 dict: got {type(score_settings)}")
        return cls(
            layer1_weight=score_settings.get("layer1_weight", DEFAULT_LAYER1_WEIGHT),
            layer2_weight=score_settings.get("layer2_weight", DEFAULT_LAYER2_WEIGHT),
            layer3_weight=score_settings.get("layer3_weight", DEFAULT_LAYER3_WEIGHT),
            alert_min=score_settings.get("min_alert", DEFAULT_ALERT_MIN),
            entry_min=score_settings.get("min_entry", DEFAULT_ENTRY_MIN),
            max_position_score=score_settings.get("max_position_score", DEFAULT_MAX_POSITION_SCORE),
            close_strength_min=score_settings.get("close_strength_min", DEFAULT_CLOSE_STRENGTH_MIN),
            volume_surprise_min=score_settings.get("volume_surprise_min", DEFAULT_VOLUME_SURPRISE_MIN),
            closing_buy_sell_ratio_min=score_settings.get(
                "closing_buy_sell_ratio_min", DEFAULT_CLOSING_BUY_SELL_RATIO_MIN
            ),
            atr_overheat_max=score_settings.get("atr_overheat_max", DEFAULT_ATR_OVERHEAT_MAX),
            theme_leadership_rank_max=score_settings.get(
                "theme_leadership_rank_max", DEFAULT_THEME_LEADERSHIP_RANK_MAX
            ),
        )

    # ===== 메인 점수 산출 =====

    def score(
        self,
        ticker: str,
        layer1: Optional[dict] = None,
        layer2: Optional[dict] = None,
        layer3: Optional[dict] = None,
        *,
        market_ok: bool = True,
        scored_at: Optional[datetime] = None,
    ) -> ScoreBreakdown:
        """피처 dict 3종 → ``ScoreBreakdown`` 반환.

        Args:
            ticker: 6자리 종목코드
            layer1: 수급 dict (None 이면 4조건 모두 None)
            layer2: 가격/거래량 dict — ``atr_overheat`` 키 필수 (하드 필터)
            layer3: 모멘텀 dict (None 이면 3조건 모두 None)
            market_ok: 시장 양호 여부 (Market Guard NORMAL/CAUTION → True, DANGER/CRISIS → False)
            scored_at: 점수 산출 시각 (None 이면 now_kst)

        Returns:
            ``ScoreBreakdown`` (frozen)
        """
        ts = scored_at or now_kst()
        l1 = layer1 or {}
        l2 = layer2 or {}
        l3 = layer3 or {}

        missing: list[str] = []

        # ===== Layer 1 (수급) — 4 조건 =====
        # 임계값 모두 ">0" (양수). PRD 정의가 모호하여 보수적 해석.
        cond_inst = _cond_gt(l1.get("inst_net_buy_estimated"), 0.0, "inst_net_buy_estimated", missing)
        cond_for3d = _cond_gt(l1.get("foreign_net_buy_3d"), 0.0, "foreign_net_buy_3d", missing)
        cond_prog = _cond_gt(l1.get("program_net_buy_change"), 0.0, "program_net_buy_change", missing)
        cond_close_flow = _cond_gt(
            l1.get("closing_flow_concentration"), 0.0, "closing_flow_concentration", missing
        )
        layer1_subscore = sum(1 for c in (cond_inst, cond_for3d, cond_prog, cond_close_flow) if c is True)

        # ===== Layer 2 (가격/거래량) — 4 조건 =====
        cond_cs = _cond_gte(
            l2.get("close_strength"), self.close_strength_min, "close_strength", missing
        )
        cond_vs = _cond_gte(
            l2.get("volume_surprise"), self.volume_surprise_min, "volume_surprise", missing
        )
        cond_ma = _cond_bool(l2.get("above_ma20"), "above_ma20", missing)
        cond_cbs = _cond_gte(
            l2.get("closing_buy_sell_ratio"),
            self.closing_buy_sell_ratio_min,
            "closing_buy_sell_ratio",
            missing,
        )
        layer2_subscore = sum(1 for c in (cond_cs, cond_vs, cond_ma, cond_cbs) if c is True)

        # ===== Layer 3 (모멘텀) — 3 조건 =====
        cond_52w = _cond_bool(l3.get("near_52w_high"), "near_52w_high", missing)
        # theme_leadership_rank: int (1=최상위) → rank <= max → True
        # **수집기 규약**: rank 는 반드시 int 양수. float (예: 1.0) / bool / 0 / 음수 → None 처리.
        # 1-5 (Layer 3 collector) 에서 rank 반환 시 int(rank) 보장 필요.
        rank = l3.get("theme_leadership_rank")
        if rank is None:
            cond_theme = None
            missing.append("theme_leadership_rank")
        elif isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
            # bool / 음수 / 0 / float / 비정상 타입 → None (의미 불명확)
            cond_theme = None
            missing.append("theme_leadership_rank")
        else:
            cond_theme = rank <= self.theme_leadership_rank_max
        cond_disc = _cond_bool(l3.get("has_positive_disclosure"), "has_positive_disclosure", missing)
        layer3_subscore = sum(1 for c in (cond_52w, cond_theme, cond_disc) if c is True)

        # ===== 합계 =====
        raw_total = layer1_subscore + layer2_subscore + layer3_subscore
        weighted_total = (
            layer1_subscore * self.layer1_weight
            + layer2_subscore * self.layer2_weight
            + layer3_subscore * self.layer3_weight
        )

        # ===== 하드 필터 (PRD 5-Layer 2: atr_overheat > 1.8) =====
        # 정규화된 float 값을 atr_overheat_value 에 저장 (비정상 입력 시 None)
        # → 1-6 candidate_logger 가 candidate_features.atr_overheat REAL 컬럼에 안전하게 저장 가능
        atr_oh_raw = l2.get("atr_overheat")
        atr_oh_f = _coerce_float(atr_oh_raw)
        excluded = False
        excl_reason: Optional[str] = None
        if atr_oh_raw is None:
            # **보수적**: 측정 불가 → 진입 차단
            excluded = True
            excl_reason = "atr_overheat 측정 불가 (입력 None)"
            if "atr_overheat" not in missing:
                missing.append("atr_overheat")
        elif atr_oh_f is None:
            # 변환 실패 (문자열/bool/NaN/inf) → EXCLUDED
            excluded = True
            excl_reason = f"atr_overheat 타입/값 오류: {atr_oh_raw!r}"
        elif atr_oh_f > self.atr_overheat_max:
            excluded = True
            excl_reason = (
                f"atr_overheat={atr_oh_f:.3f} > {self.atr_overheat_max} (PRD 하드 필터)"
            )

        # ===== 의사결정 =====
        if excluded:
            decision = "EXCLUDED"
            decision_reason = excl_reason or "하드 필터 제외"
        elif weighted_total >= self.max_position_score and market_ok:
            decision = "MAX_SIZE_ENTRY"
            decision_reason = (
                f"weighted={weighted_total:.1f} >= max_position({self.max_position_score}) "
                f"+ market_ok"
            )
        elif weighted_total >= self.entry_min:
            decision = "ENTRY"
            decision_reason = f"weighted={weighted_total:.1f} >= entry_min({self.entry_min})"
        elif weighted_total >= self.alert_min:
            decision = "ALERT"
            decision_reason = f"weighted={weighted_total:.1f} >= alert_min({self.alert_min})"
        else:
            decision = "BELOW_THRESHOLD"
            decision_reason = f"weighted={weighted_total:.1f} < alert_min({self.alert_min})"

        # 안전한 ticker 처리 (ticker 자체는 본 엔진에서 검증 X — collector 가 책임)
        return ScoreBreakdown(
            ticker=str(ticker),
            scored_at=ts,
            cond_inst_net_buy=cond_inst,
            cond_foreign_3d=cond_for3d,
            cond_program_net_buy=cond_prog,
            cond_closing_flow=cond_close_flow,
            layer1_subscore=layer1_subscore,
            cond_close_strength=cond_cs,
            cond_volume_surprise=cond_vs,
            cond_above_ma20=cond_ma,
            cond_closing_buy_sell=cond_cbs,
            layer2_subscore=layer2_subscore,
            cond_near_52w_high=cond_52w,
            cond_theme_leadership=cond_theme,
            cond_positive_disclosure=cond_disc,
            layer3_subscore=layer3_subscore,
            raw_total=raw_total,
            weighted_total=weighted_total,
            atr_overheat_value=atr_oh_f,
            excluded_by_filter=excluded,
            exclusion_reason=excl_reason,
            decision=decision,
            decision_reason=decision_reason,
            missing_inputs=tuple(missing),
        )


# ===== 헬퍼 =====


def _coerce_float(value: Any) -> Optional[float]:
    """안전한 float 변환. None / bool / 변환 실패 / NaN / inf → None.

    PRD 5-Layer 2 수치 입력 정규화 — atr_overheat_value DB 저장 시
    (REAL 컬럼) 타입 안전성 확보용.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


# ===== 조건 평가 =====


def _cond_gte(value: Any, threshold: float, key: str, missing: list[str]) -> Optional[bool]:
    """value >= threshold (float). value None / 변환 실패 → None + missing 추가."""
    if value is None:
        missing.append(key)
        return None
    if isinstance(value, bool):  # bool 차단
        missing.append(key)
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        missing.append(key)
        return None
    # NaN/inf
    if f != f or f in (float("inf"), float("-inf")):
        missing.append(key)
        return None
    return f >= threshold


def _cond_gt(value: Any, threshold: float, key: str, missing: list[str]) -> Optional[bool]:
    """value > threshold (float). value None / 변환 실패 → None + missing 추가."""
    if value is None:
        missing.append(key)
        return None
    if isinstance(value, bool):
        missing.append(key)
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        missing.append(key)
        return None
    if f != f or f in (float("inf"), float("-inf")):
        missing.append(key)
        return None
    return f > threshold


def _cond_bool(value: Any, key: str, missing: list[str]) -> Optional[bool]:
    """value 가 bool 인지 확인. None / 비-bool → None + missing 추가."""
    if value is None:
        missing.append(key)
        return None
    if isinstance(value, bool):
        return value
    # 0/1 같은 int 도 bool 로 강제 해석 가능하지만 의미가 모호 → None 처리
    missing.append(key)
    return None
