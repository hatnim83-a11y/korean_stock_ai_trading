"""closing_bet_system.collectors.kind_alert_collector

KIND 시장경보 종목 수집 (Phase 2-2).

**책임**:
- KIND (https://kind.krx.co.kr) 4단계 경보 종목 일일 dict 수집
- 4단계: 투자주의(1) / 투자경고(2) / 투자위험(3) / 매매거래정지(3)
- ``OvernightRiskFilter`` 에 종목별 severity 입력 → 외부 리스크 점수 보강 (PRD 8-3)

**Phase 2-2 단계적 도입**:
1. 본 단위: ``KindAlertCollector`` 인터페이스 + ``KindAlertSnapshot`` dataclass + main_orchestrator/risk_filter 통합
2. 후속 단위: ``KindHttpProvider`` 신규 — KIND HTML 크롤링/공식 데이터 통합 (사이트 구조 안정성 검토 후)

**기본 동작 (provider 미주입 시)**:
- ``collect()`` → ``is_valid=True``, ``alerts={}`` (빈 dict — 종목별 경보 없음 = 모두 정상)
- ``OvernightRiskFilter`` 결손 정책 동일: ticker 미존재 → 영향 없음
- 시스템 안전: 인터페이스만 활성화되고 실 데이터는 후속 단위에서 채움

**의존성 주입 (테스트/실 데이터 교체)**:
    from closing_bet_system.collectors.kind_alert_collector import KindAlertCollector

    collector = KindAlertCollector(provider=lambda: {"005930": "주의"})
    snap = collector.collect()
    snap.severity_map  # {"005930": 1}
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== 모듈 상수 =====

# KIND 경보 단계 → severity 정수 매핑
# severity 0 = 정상 (dict 미존재 시 default), 1 = 주의, 2 = 경고, 3 = 위험/정지
ALERT_LEVEL_TO_SEVERITY: dict[str, int] = {
    "투자주의": 1, "주의": 1,
    "투자경고": 2, "경고": 2,
    "투자위험": 3, "위험": 3,
    "매매거래정지": 3, "거래정지": 3, "정지": 3,
}

# OvernightRiskFilter 통합 시 사용하는 임계값
SEVERITY_EXCLUDE_THRESHOLD = 3       # severity >= 3 → can_enter=False
SEVERITY_REDUCE_THRESHOLD = 2        # severity == 2 → reduced_size_factor


# ===== 결과 dataclass =====


@dataclass(frozen=True)
class KindAlertSnapshot:
    """KIND 시장경보 일일 스냅샷.

    Attributes:
        snapshot_date: 수집 일자 (date).
        is_valid: 수집 성공 여부.
        alerts: ticker → 경보 단계 한글명 (예: ``{"005930": "주의"}``).
        severity_map: ticker → severity 정수 (1~3). ``alerts`` 와 동기화.
        error_msg: 실패 시 사유 (성공 시 None).
    """

    snapshot_date: date_cls
    is_valid: bool = True
    alerts: dict = field(default_factory=dict, hash=False, compare=False)
    severity_map: dict = field(default_factory=dict, hash=False, compare=False)
    error_msg: Optional[str] = None

    def severity_for(self, ticker: str) -> int:
        """ticker 의 severity 반환. 미존재 시 0 (정상)."""
        return int(self.severity_map.get(ticker, 0))


# ===== 수집기 =====


class KindAlertCollector:
    """KIND 시장경보 수집기.

    Args:
        provider: ``Callable[[], dict[str, str]]`` — ticker → 경보 단계 한글명 매핑.
            None 이면 빈 dict 반환 (Phase 2-2 단계 1: 인터페이스만 활성, 실 데이터 후속).

    예시:
        # 인터페이스만 (실 데이터 없음)
        collector = KindAlertCollector()
        snap = collector.collect()
        # snap.alerts == {} → OvernightRiskFilter 룰 비활성

        # 실 데이터 주입 (후속 단위에서 KindHttpProvider 도입)
        from somewhere import fetch_kind_alerts
        collector = KindAlertCollector(provider=fetch_kind_alerts)
    """

    def __init__(self, provider: Optional[Callable[[], dict]] = None):
        self._provider = provider

    def collect(self) -> KindAlertSnapshot:
        """오늘자 KIND 경보 dict 수집. 실패 시 빈 dict + ``is_valid=False``."""
        today = now_kst().date()

        if self._provider is None:
            logger.debug(
                "[kind_alert_collector] provider 미주입 — 빈 dict 반환 "
                "(Phase 2-2 1단계: 인터페이스만 활성)"
            )
            return KindAlertSnapshot(snapshot_date=today, is_valid=True, alerts={}, severity_map={})

        try:
            raw = self._provider()
        except Exception as e:
            logger.warning(f"[kind_alert_collector] provider 호출 예외: {e}")
            return KindAlertSnapshot(
                snapshot_date=today, is_valid=False,
                alerts={}, severity_map={},
                error_msg=f"provider_exception: {e}",
            )

        if not isinstance(raw, dict):
            logger.warning(
                f"[kind_alert_collector] provider 반환 dict 아님: {type(raw)}"
            )
            return KindAlertSnapshot(
                snapshot_date=today, is_valid=False,
                alerts={}, severity_map={},
                error_msg="provider_returned_non_dict",
            )

        # 6자리 ticker 검증 + severity 매핑
        alerts_clean: dict[str, str] = {}
        severity_map: dict[str, int] = {}
        for ticker, level in raw.items():
            if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
                continue
            if not isinstance(level, str):
                continue
            severity = ALERT_LEVEL_TO_SEVERITY.get(level.strip())
            if severity is None or severity <= 0:
                continue
            alerts_clean[ticker] = level.strip()
            severity_map[ticker] = severity

        logger.info(
            f"[kind_alert_collector] KIND 경보 {len(alerts_clean)}건 수집 ({today})"
        )
        return KindAlertSnapshot(
            snapshot_date=today, is_valid=True,
            alerts=alerts_clean, severity_map=severity_map,
        )
