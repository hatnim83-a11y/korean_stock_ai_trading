"""closing_bet_system.engines.overnight_risk_filter

PRD 4-2 / 4-3 / 8-3 — 외부 리스크 통합 필터.

**책임**:
1. 시장 상황 기반 매매 중지 (PRD 4-2): 미국 선물 -1.5% / V-KOSPI 30+ → 당일 전체 매매 중단
2. 시장 상황 기반 비중 축소 (PRD 4-3): 미국 선물 -1.0~-1.5% / USD/KRW +1.5%+ / KOSPI -2%+ → 50%
3. 종목별 DART 즉시제외 통합 (1-5a 결과 사용): 유상증자/CB/BW/횡령/배임 등
4. 통합 의사결정 → ``OvernightRiskAssessment``

**책임 외**:
- 미국 선물 / V-KOSPI / USD-KRW / KOSPI 데이터 **수집** → Phase 2 전용 collector
  본 엔진은 ``market_data`` dict 입력 받음 (의존성 주입). Phase 1 은 일부 None 허용.
- 이벤트 캘린더 (CPI/FOMC/금통위) → Phase 2 ``event_calendar_collector``
- DART 공시 수집 → 1-5a ``DartDisclosureCollector``
- KIND 시장경보 → Phase 2 ``kind_alert_collector``

**Phase 1 데이터 결손 정책**:
시장 데이터 None 인 경우 **해당 룰 비활성** (skip/축소 트리거하지 않음). 본 엔진은 가용한 신호만
적용. Phase 2 이후 핵심 데이터 미수집 시 보수적 skip 으로 강화 검토 (config 토글 추가).

**의사결정 우선순위** (높은 → 낮은):
1. ``skip_today`` (시장 매매 중단) → can_enter=False, size=0.0
2. ``exclude_by_dart`` (DART 즉시제외) → can_enter=False, size=0.0
3. 비중 축소 (50% 또는 100%)
4. 통과 → can_enter=True, size=position_size_factor
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


# ===== 모듈 상수 (settings.yaml external_risk 섹션 default) =====

# PRD 4-2 매매 중지 임계값
DEFAULT_US_FUTURES_SKIP_THRESHOLD = -0.015     # 미국 선물 -1.5% 이하
DEFAULT_VKOSPI_SKIP_THRESHOLD = 30.0            # V-KOSPI 30 이상

# PRD 4-3 비중 축소 (50%) 임계값
DEFAULT_US_FUTURES_REDUCE_THRESHOLD = -0.010   # 미국 선물 -1.0% ~ -1.5%
DEFAULT_USD_KRW_ALERT_THRESHOLD = 0.015         # USD/KRW +1.5% 이상
DEFAULT_KOSPI_ALERT_THRESHOLD = -0.02           # KOSPI -2% 이상 하락

DEFAULT_REDUCED_SIZE_FACTOR = 0.5
DEFAULT_FULL_SIZE_FACTOR = 1.0
ZERO_SIZE_FACTOR = 0.0


# ===== 결과 dataclass =====


@dataclass(frozen=True)
class OvernightRiskAssessment:
    """1종목 1회 외부 리스크 통합 평가.

    1-7 telegram_review_bot / 2-4 entry_executor 의 진입 결정 입력.
    """

    ticker: str
    assessed_at: datetime                  # KST timezone-aware

    # 시장 평가 (종목 무관, 모든 종목 공통)
    skip_today: bool = False
    skip_reason: Optional[str] = None
    # **시장 평가 단계 비중** (skip / DART 제외 적용 전 중간값). 1.0 또는 0.5 만 가능.
    # 진입 결정은 반드시 ``final_size_factor`` 사용 — skip/DART 제외 시 0.0 으로 강제됨.
    # 예: skip=True + reduce 신호 동시 발생 → position_size_factor=0.5 / final_size_factor=0.0
    position_size_factor: float = DEFAULT_FULL_SIZE_FACTOR
    market_warnings: tuple = field(default_factory=tuple, hash=False, compare=False)

    # 종목 평가 (DART)
    exclude_by_dart: bool = False
    dart_exclusion_reason: Optional[str] = None
    has_positive_disclosure: bool = False

    # 통합 결정
    can_enter: bool = True
    final_size_factor: float = DEFAULT_FULL_SIZE_FACTOR
    decision_reason: str = ""


# ===== 엔진 =====


class OvernightRiskFilter:
    """PRD 4-2/4-3/8-3 외부 리스크 통합 필터.

    Args:
        us_futures_skip_threshold: 미국 선물 매매 중지 임계 (default -0.015)
        us_futures_reduce_threshold: 미국 선물 비중 축소 임계 (default -0.010)
        usd_krw_alert_threshold: USD/KRW 비중 축소 임계 (default 0.015)
        kospi_alert_threshold: KOSPI 비중 축소 임계 (default -0.02)
        vkospi_skip_threshold: V-KOSPI 매매 중지 임계 (default 30.0)
        reduced_size_factor: 비중 축소 시 size factor (default 0.5)

    **임계값 검증**: us_futures 의 경우 skip < reduce (skip 이 더 음수). 위반 시 ValueError.

    Examples:
        >>> from closing_bet_system.engines.overnight_risk_filter import OvernightRiskFilter
        >>> rf = OvernightRiskFilter()
        >>> assess = rf.assess(
        ...     ticker="005930",
        ...     market_data={"us_futures_change_pct": -0.005, "vkospi": 25.0,
        ...                  "kospi_change_pct": -0.01, "usd_krw_change_pct": 0.005},
        ...     dart_snapshot=None,
        ... )
        >>> assess.can_enter, assess.final_size_factor
        (True, 1.0)
    """

    def __init__(
        self,
        *,
        us_futures_skip_threshold: float = DEFAULT_US_FUTURES_SKIP_THRESHOLD,
        us_futures_reduce_threshold: float = DEFAULT_US_FUTURES_REDUCE_THRESHOLD,
        usd_krw_alert_threshold: float = DEFAULT_USD_KRW_ALERT_THRESHOLD,
        kospi_alert_threshold: float = DEFAULT_KOSPI_ALERT_THRESHOLD,
        vkospi_skip_threshold: float = DEFAULT_VKOSPI_SKIP_THRESHOLD,
        reduced_size_factor: float = DEFAULT_REDUCED_SIZE_FACTOR,
    ):
        # 임계값 순서 검증 (skip 이 reduce 보다 더 음수)
        if us_futures_skip_threshold >= us_futures_reduce_threshold:
            raise ValueError(
                f"us_futures_skip ({us_futures_skip_threshold}) 는 reduce "
                f"({us_futures_reduce_threshold}) 보다 작아야 함 (더 음수)"
            )
        # size factor 범위 (0~1)
        if not (0.0 < reduced_size_factor < 1.0):
            raise ValueError(
                f"reduced_size_factor 는 (0, 1) 범위: got {reduced_size_factor}"
            )
        # vkospi 양수
        if vkospi_skip_threshold <= 0:
            raise ValueError(f"vkospi_skip_threshold 는 양수: got {vkospi_skip_threshold}")
        # USD-KRW 양수, KOSPI 음수
        if usd_krw_alert_threshold <= 0:
            raise ValueError(
                f"usd_krw_alert_threshold 는 양수 (절상): got {usd_krw_alert_threshold}"
            )
        if kospi_alert_threshold >= 0:
            raise ValueError(f"kospi_alert_threshold 는 음수: got {kospi_alert_threshold}")

        self.us_futures_skip_threshold = float(us_futures_skip_threshold)
        self.us_futures_reduce_threshold = float(us_futures_reduce_threshold)
        self.usd_krw_alert_threshold = float(usd_krw_alert_threshold)
        self.kospi_alert_threshold = float(kospi_alert_threshold)
        self.vkospi_skip_threshold = float(vkospi_skip_threshold)
        self.reduced_size_factor = float(reduced_size_factor)

    # ===== settings.yaml 진입점 =====

    @classmethod
    def from_settings(cls, external_risk_settings: dict) -> "OvernightRiskFilter":
        """settings.yaml ``external_risk`` 섹션 dict 로 엔진 생성.

        지원 키 (모두 선택, 미지정 시 모듈 default):
        - ``us_futures_skip_threshold`` / ``us_futures_reduce_threshold``
        - ``usd_krw_alert_threshold`` / ``kospi_alert_threshold``
        - ``vkospi_skip_threshold``
        """
        if not isinstance(external_risk_settings, dict):
            raise TypeError(f"external_risk_settings 는 dict: got {type(external_risk_settings)}")
        return cls(
            us_futures_skip_threshold=external_risk_settings.get(
                "us_futures_skip_threshold", DEFAULT_US_FUTURES_SKIP_THRESHOLD
            ),
            us_futures_reduce_threshold=external_risk_settings.get(
                "us_futures_reduce_threshold", DEFAULT_US_FUTURES_REDUCE_THRESHOLD
            ),
            usd_krw_alert_threshold=external_risk_settings.get(
                "usd_krw_alert_threshold", DEFAULT_USD_KRW_ALERT_THRESHOLD
            ),
            kospi_alert_threshold=external_risk_settings.get(
                "kospi_alert_threshold", DEFAULT_KOSPI_ALERT_THRESHOLD
            ),
            vkospi_skip_threshold=external_risk_settings.get(
                "vkospi_skip_threshold", DEFAULT_VKOSPI_SKIP_THRESHOLD
            ),
        )

    # ===== 메인 평가 =====

    def assess(
        self,
        ticker: str,
        market_data: Optional[dict] = None,
        dart_snapshot: Optional[Any] = None,
        *,
        kind_alerts: Optional[Any] = None,
        assessed_at: Optional[datetime] = None,
    ) -> OvernightRiskAssessment:
        """단일 종목 외부 리스크 통합 평가.

        Args:
            ticker: 6자리 종목코드
            market_data: 시장 데이터 dict — 키: ``us_futures_change_pct``, ``vkospi``,
                ``kospi_change_pct``, ``usd_krw_change_pct`` (모두 None 허용 — Phase 1 결손 정책)
            dart_snapshot: 1-5a ``DartDisclosureSnapshot`` (None 허용 — DART 평가 스킵)
            kind_alerts: Phase 2-2 ``KindAlertSnapshot`` 또는 ``{ticker: severity}`` dict
                (None 허용 — KIND 평가 스킵, Phase 1 결손 정책)
            assessed_at: 평가 시각 (None 이면 now_kst)

        Returns:
            ``OvernightRiskAssessment`` (frozen)
        """
        ts = assessed_at or now_kst()
        md = market_data or {}

        # ===== 1. 시장 평가 (종목 무관) =====
        skip_today = False
        skip_reason: Optional[str] = None
        size_factor = DEFAULT_FULL_SIZE_FACTOR
        warnings: list[str] = []

        # PRD 4-2: 미국 선물 -1.5% 이하 → 당일 전체 매매 중단
        us_fut = _safe_float(md.get("us_futures_change_pct"))
        if us_fut is not None:
            if us_fut <= self.us_futures_skip_threshold:
                skip_today = True
                skip_reason = (
                    f"미국 선물 {us_fut * 100:+.2f}% ≤ {self.us_futures_skip_threshold * 100:+.1f}% "
                    f"(PRD 4-2 매매 중단)"
                )
            elif us_fut <= self.us_futures_reduce_threshold:
                # PRD 4-3: 미국 선물 -1.0% ~ -1.5% → 50% 비중
                size_factor = min(size_factor, self.reduced_size_factor)
                warnings.append(
                    f"미국 선물 {us_fut * 100:+.2f}% (비중 {self.reduced_size_factor*100:.0f}%)"
                )
        else:
            warnings.append("미국 선물 데이터 없음 (Phase 1: 룰 비활성)")

        # PRD 4-2: V-KOSPI 30 이상 → 매매 중단
        vkospi = _safe_float(md.get("vkospi"))
        if vkospi is not None:
            if vkospi >= self.vkospi_skip_threshold:
                if not skip_today:
                    skip_today = True
                    skip_reason = (
                        f"V-KOSPI {vkospi:.1f} ≥ {self.vkospi_skip_threshold:.0f} "
                        f"(PRD 4-2 매매 중단)"
                    )
                else:
                    # 이미 skip — 이유 누적
                    skip_reason = f"{skip_reason}; V-KOSPI {vkospi:.1f}"
        else:
            warnings.append("V-KOSPI 데이터 없음 (Phase 1: 룰 비활성)")

        # PRD 4-3: USD/KRW +1.5% 이상 → 50% 비중
        usd_krw = _safe_float(md.get("usd_krw_change_pct"))
        if usd_krw is not None:
            if usd_krw >= self.usd_krw_alert_threshold:
                size_factor = min(size_factor, self.reduced_size_factor)
                warnings.append(
                    f"USD/KRW {usd_krw * 100:+.2f}% ≥ {self.usd_krw_alert_threshold * 100:+.1f}% "
                    f"(비중 {self.reduced_size_factor*100:.0f}%)"
                )

        # PRD 4-3: KOSPI -2% 이상 하락 → 50% 비중
        kospi = _safe_float(md.get("kospi_change_pct"))
        if kospi is not None:
            if kospi <= self.kospi_alert_threshold:
                size_factor = min(size_factor, self.reduced_size_factor)
                warnings.append(
                    f"KOSPI {kospi * 100:+.2f}% ≤ {self.kospi_alert_threshold * 100:+.1f}% "
                    f"(비중 {self.reduced_size_factor*100:.0f}%)"
                )

        # ===== 2. 종목 평가 (DART) =====
        exclude_by_dart = False
        dart_excl_reason: Optional[str] = None
        has_positive = False
        if dart_snapshot is not None:
            # DartDisclosureSnapshot duck-typing (object/dict 모두 지원)
            exclude_by_dart = bool(_get_attr_or_key(dart_snapshot, "exclude_by_disclosure", False))
            dart_excl_reason = _get_attr_or_key(dart_snapshot, "exclusion_reason", None)
            has_positive = bool(_get_attr_or_key(dart_snapshot, "has_positive_disclosure", False))

        # ===== 2b. 종목 평가 (KIND 시장경보, Phase 2-2) =====
        # severity 0: 정상 (영향 없음), 1: 주의 (워닝만), 2: 경고 (size 축소), 3: 위험/정지 (제외)
        kind_severity = _resolve_kind_severity(kind_alerts, ticker)
        if kind_severity >= 3:
            # 매매거래정지 / 투자위험 → 즉시 제외
            warnings.append(f"KIND severity {kind_severity} (위험/정지) → 진입 제외")
        elif kind_severity == 2:
            # 투자경고 → 50% 비중
            size_factor = min(size_factor, self.reduced_size_factor)
            warnings.append(
                f"KIND 투자경고 → 비중 {self.reduced_size_factor*100:.0f}%"
            )
        elif kind_severity == 1:
            # 투자주의 → 워닝만 (size 영향 없음, 알림으로 사용자 인지)
            warnings.append("KIND 투자주의 (size 영향 없음, 사용자 인지용)")

        # ===== 3. 통합 결정 =====
        if skip_today:
            can_enter = False
            final_size = ZERO_SIZE_FACTOR
            decision_reason = f"매매 중단: {skip_reason}"
        elif exclude_by_dart:
            can_enter = False
            final_size = ZERO_SIZE_FACTOR
            # 1-5a `exclusion_reason` 이 이미 "DART 즉시제외:" prefix 를 포함 → 중복 prefix 제거
            decision_reason = dart_excl_reason or "DART 즉시제외: 키워드 매칭"
        elif kind_severity >= 3:
            # KIND 위험/정지: DART 즉시제외와 같은 우선순위 (매매 자체 차단)
            can_enter = False
            final_size = ZERO_SIZE_FACTOR
            decision_reason = f"KIND 시장경보 즉시제외 (severity {kind_severity}: 위험/정지)"
        else:
            can_enter = True
            final_size = size_factor
            if size_factor < DEFAULT_FULL_SIZE_FACTOR:
                decision_reason = (
                    f"진입 가능 (비중 축소 {size_factor*100:.0f}%): " + " / ".join(warnings)
                )
            else:
                decision_reason = "진입 가능 (정상 시장)"

        # ticker 형식 안정화 (잘못된 ticker 도 결과 반환 — 시장 평가는 종목 무관이라 결정 자체는 유효)
        ticker_str = str(ticker) if ticker is not None else "UNKNOWN"

        return OvernightRiskAssessment(
            ticker=ticker_str,
            assessed_at=ts,
            skip_today=skip_today,
            skip_reason=skip_reason,
            position_size_factor=size_factor,
            market_warnings=tuple(warnings),
            exclude_by_dart=exclude_by_dart,
            dart_exclusion_reason=dart_excl_reason,
            has_positive_disclosure=has_positive,
            can_enter=can_enter,
            final_size_factor=final_size,
            decision_reason=decision_reason,
        )

    # ===== 다종목 (시장 평가 1회 + 종목별 DART 평가) =====

    def assess_for_universe(
        self,
        market_data: dict,
        dart_snapshots: dict,  # ticker → DartDisclosureSnapshot
        tickers: Optional[list[str]] = None,
        *,
        kind_alerts: Optional[Any] = None,
        assessed_at: Optional[datetime] = None,
    ) -> dict:
        """여러 종목 평가. ticker → ``OvernightRiskAssessment`` 반환.

        Args:
            market_data: 시장 데이터 (1회 평가, 모든 종목 공통)
            dart_snapshots: ``{ticker: DartDisclosureSnapshot}`` 매핑
            tickers: 평가 대상 ticker 리스트 (None 이면 dart_snapshots 키 사용)
            kind_alerts: Phase 2-2 ``KindAlertSnapshot`` 또는 ``{ticker: severity}`` dict
                (None 허용 — KIND 평가 스킵, 결손 정책 일관)
        """
        ts = assessed_at or now_kst()
        if tickers is None:
            tickers = list(dart_snapshots.keys())

        results: dict[str, OvernightRiskAssessment] = {}
        skip_count = 0
        excl_count = 0
        for ticker in tickers:
            snap = dart_snapshots.get(ticker)
            assessment = self.assess(
                ticker, market_data, snap,
                kind_alerts=kind_alerts, assessed_at=ts,
            )
            results[ticker] = assessment
            if assessment.skip_today:
                skip_count += 1
            elif assessment.exclude_by_dart:
                excl_count += 1
        # 시장 skip 인 경우 모든 종목이 동일 skip → 카운트 의미 적음. 로그는 한 줄.
        if tickers:
            sample = results[tickers[0]]
            if sample.skip_today:
                logger.warning(
                    f"[overnight_risk] 시장 매매 중단 — 평가 종목 {len(tickers)}개 모두 진입 불가: "
                    f"{sample.skip_reason}"
                )
            else:
                logger.info(
                    f"[overnight_risk] {len(tickers)}종목 평가, DART 제외 {excl_count}건, "
                    f"size_factor={sample.position_size_factor}"
                )
        return results


# ===== 헬퍼 =====


def _safe_float(value: Any) -> Optional[float]:
    """안전한 float 변환. None / bool / 변환 실패 / NaN / inf → None.

    Phase 1 외부 리스크 데이터 결손 정책 — None 반환 시 해당 룰 비활성.
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


def _get_attr_or_key(obj: Any, name: str, default: Any) -> Any:
    """dataclass attribute 또는 dict key 모두 지원."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _resolve_kind_severity(kind_alerts: Any, ticker: str) -> int:
    """KIND 입력에서 ticker 의 severity 추출. 미존재/None 시 0 (정상).

    지원 입력 형태:
    - ``KindAlertSnapshot`` (Phase 2-2 dataclass) — ``severity_for(ticker)`` 호출
    - ``{ticker: severity_int}`` dict
    - ``{ticker: severity_str}`` (예: ``{"005930": "주의"}``) — 한글명 매핑
    - None — 평가 스킵 (0 반환)
    """
    if kind_alerts is None:
        return 0

    # KindAlertSnapshot 또는 호환 객체 (severity_for 메서드 가짐)
    severity_fn = getattr(kind_alerts, "severity_for", None)
    if callable(severity_fn):
        try:
            return int(severity_fn(ticker))
        except (TypeError, ValueError):
            return 0

    if isinstance(kind_alerts, dict):
        raw = kind_alerts.get(ticker)
        if raw is None:
            return 0
        if isinstance(raw, int):
            return max(0, raw)
        if isinstance(raw, str):
            # 본 모듈에서 매핑 표 import 회피 위해 인라인
            from closing_bet_system.collectors.kind_alert_collector import (
                ALERT_LEVEL_TO_SEVERITY,
            )
            return int(ALERT_LEVEL_TO_SEVERITY.get(raw.strip(), 0))
        return 0
    return 0
