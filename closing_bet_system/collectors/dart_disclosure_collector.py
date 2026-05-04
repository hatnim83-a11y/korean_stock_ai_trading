"""closing_bet_system.collectors.dart_disclosure_collector

PRD 8-2 — 종목별 DART 공시 수집 + 호재/악재 키워드 매칭.

**책임**:
1. 진입 직전(15:10~15:15 / 또는 D-1 야간) 종목별 DART 공시 ``lookback_days`` 일치 수집
2. PRD 8-2 키워드 매트릭스로 호재/즉시제외 분류
3. ``DartDisclosureSnapshot`` 으로 1-4 SignalScoreEngine Layer 3 / 1-5b overnight_risk_filter 입력 제공

**책임 외** (다른 단위에서):
- DART API 호출 자체 → 기존 ``modules/ai_verifier/dart_api.fetch_dart_disclosures`` 재사용
- 컨센서스 비교 → Phase 3 (LLM 분류기 도입 시점)
- 점수 합산 / 진입 의사결정 → 1-4 ``SignalScoreEngine``
- 즉시 제외 통합 (DART + 미국선물) → 1-5b ``OvernightRiskFilter``

**PRD 8-2 키워드 분류** (Phase 1 단순 substring 매칭):
=================================  =====================================  =================
패턴                               액션                                   비고
=================================  =====================================  =================
유상증자 / CB / BW / 무상감자       즉시 제외 (``exclude_by_disclosure``)  익일 갭다운 빈번
횡령 / 배임                        즉시 제외                              경영진 리스크
영업정지 / 제재 / 검찰              즉시 제외                              규제 리스크
실적 쇼크                          즉시 제외 (Phase 1: 키워드만)          Phase 3: 컨센서스 비교
단일판매공급계약 / 수주              가점 ``has_positive_disclosure=True``  호재
자기주식 취득 / 자사주 취득          가점                                  주가 부양 시그널
=================================  =====================================  =================

**Phase 1 한계**:
- "실적 공시 (양호)" 가점은 컨센서스 비교가 필요해 미구현 (Phase 3+ LLM)
- DART OpenAPI 의 ``corp_code`` 매핑은 기존 ``dart_api._get_corp_code()`` 의 TODO 상태에 의존
- DART API 키 미설정 시 ``dart_api.fetch_dart_disclosures`` 가 빈 list 반환 →
  collector 는 ``is_valid=True`` + 호재/제외 모두 ``False`` 처리 (공시 없음 취급).
  **API 키 미설정과 정상 무공시를 구분하려면** 호출 측에서 ``DART_API_KEY`` env 존재 여부를 별도 확인 필요.

**Phase 2 확장**:
- 정규표현식 패턴 + 컨센서스 데이터 수집기 통합
- KIND 시장경보 (``kind_alert_collector``, 2-2) 와 결과 결합
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== 키워드 매트릭스 (PRD 8-2) =====

# 즉시 제외 — 발견 시 후보 status='rejected_filter' (1-5b overnight_risk_filter 결정)
# Phase 1 정책: 단순 substring 매칭. False positive 우려는 1-9 검증 단계에서 평가.
EXCLUSION_KEYWORDS: tuple[str, ...] = (
    "유상증자",
    "전환사채",
    "신주인수권부사채",
    "교환사채",
    "CB",
    "BW",
    "EB",
    "무상감자",
    "횡령",
    "배임",
    "영업정지",
    "제재",
    "검찰",                  # "검찰 수사", "검찰 송치" 등
    # "조사" 단독 키워드는 의도적으로 제외 — code-tester 검증 결과 FP 5건 (기업실태조사 / 시장조사 /
    # 신용조사 / 기업실사 조사 / IR 조사) 발생. 규제 맥락 조사만 잡도록 더 구체적인 패턴으로 대체:
    "감리 착수",             # 감리 조사 착수
    "감리 결과",
    "공정위 조사",
    "금감원 조사",
    "고발",
    "과징금",
    "실적쇼크",              # 키워드 자체 발생 빈도 낮음 (대부분 "잠정실적"/"매출액|영업이익" 형태)
    "분식회계",
)

# 호재 가점 — has_positive_disclosure=True 로 1-4 Layer 3 가점
POSITIVE_KEYWORDS: tuple[str, ...] = (
    "단일판매공급계약",
    "단일판매ㆍ공급계약",   # 띄어쓰기 변형
    "공급계약",
    "수주",
    "자기주식취득",
    "자사주매입",
    "자기주식 취득",        # 띄어쓰기 변형
    "자사주 매입",
    "주식양수도",            # M&A 관련 호재
)

# 기본 lookback: 당일 공시는 장중 진입 이전까지 / D-1 공시는 야간 갭다운 트리거 영향
DEFAULT_LOOKBACK_DAYS = 2


# ===== Snapshot dataclass =====


@dataclass(frozen=True)
class DartDisclosureSnapshot:
    """1종목 1회 DART 공시 스냅샷.

    1-4 SignalScoreEngine Layer 3 ``has_positive_disclosure`` 입력 +
    1-5b OvernightRiskFilter ``exclude_by_disclosure`` 입력으로 사용.
    """

    ticker: str
    snapshot_time: datetime               # KST timezone-aware
    is_valid: bool                         # API 호출 성공 (키 누락 / 네트워크 실패 시 False)
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    total_disclosures: int = 0

    # 매칭 결과 — (제목, 매칭 키워드) tuple 리스트
    positive_matches: tuple = field(default_factory=tuple, hash=False, compare=False)
    exclusion_matches: tuple = field(default_factory=tuple, hash=False, compare=False)

    # 1-4 / 1-5b 결정 입력
    has_positive_disclosure: bool = False
    exclude_by_disclosure: bool = False
    exclusion_reason: Optional[str] = None

    # raw 응답 (디버깅 / 알림 본문)
    raw_disclosures: Optional[list] = field(default=None, hash=False, compare=False)

    def to_layer3_input(self) -> dict[str, Any]:
        """1-4 SignalScoreEngine ``layer3`` dict 의 한 키 (``has_positive_disclosure``).

        Layer 3 다른 키 (``near_52w_high``, ``theme_leadership_rank``) 는
        별도 collector 책임이므로 본 메서드는 1키만 반환.
        """
        return {"has_positive_disclosure": self.has_positive_disclosure}


# ===== Collector =====


class DartDisclosureCollector:
    """DART 공시 수집 + PRD 8-2 키워드 분류기.

    Args:
        dart_fetch_fn: ``fetch_dart_disclosures`` 콜러블 — 의존성 주입.
            None 이면 ``modules.ai_verifier.dart_api.fetch_dart_disclosures`` 자동 로드.
        positive_keywords / exclusion_keywords: 키워드 오버라이드 (테스트/Phase 2 확장용).

    **비동기 호환 (1-8 main_orchestrator 시점)**:
    DART API 호출은 동기 httpx 기반이라 30종목 기준 수십 초 소요 가능.
    AsyncIOScheduler 이벤트 루프 블로킹을 피하려면 ``asyncio.to_thread(...)`` 로 감쌀 것.

    Examples:
        >>> from closing_bet_system.collectors.dart_disclosure_collector import (
        ...     DartDisclosureCollector,
        ... )
        >>> coll = DartDisclosureCollector()
        >>> snap = coll.collect("005930", lookback_days=2)
        >>> snap.has_positive_disclosure  # Layer 3 1점 입력
        False
    """

    def __init__(
        self,
        dart_fetch_fn: Optional[Callable[[str, int], list]] = None,
        *,
        positive_keywords: tuple[str, ...] = POSITIVE_KEYWORDS,
        exclusion_keywords: tuple[str, ...] = EXCLUSION_KEYWORDS,
    ):
        self._dart_fetch_fn = dart_fetch_fn
        # 빈 키워드 집합 차단
        if not positive_keywords:
            raise ValueError("positive_keywords 비어있음")
        if not exclusion_keywords:
            raise ValueError("exclusion_keywords 비어있음")
        self._positive_keywords = tuple(positive_keywords)
        self._exclusion_keywords = tuple(exclusion_keywords)

    # ===== 의존성 지연 로딩 =====

    @property
    def dart_fetch(self) -> Callable[[str, int], list]:
        """``fetch_dart_disclosures`` 함수 지연 로딩 (테스트 mock 우선)."""
        if self._dart_fetch_fn is None:
            from modules.ai_verifier.dart_api import fetch_dart_disclosures

            self._dart_fetch_fn = fetch_dart_disclosures
        return self._dart_fetch_fn

    # ===== 단일 종목 =====

    def collect(
        self,
        ticker: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        snapshot_time: Optional[datetime] = None,
    ) -> DartDisclosureSnapshot:
        """1종목 ``lookback_days`` 일치 DART 공시 수집 + 키워드 분류.

        Args:
            ticker: 6자리 종목코드
            lookback_days: 조회 기간 (당일 + N-1 일 전)
            snapshot_time: 스냅샷 시각 (None 이면 now_kst)

        Returns:
            ``DartDisclosureSnapshot`` (실패 시 ``is_valid=False``)
        """
        ts = snapshot_time or now_kst()

        # ticker 검증 (1-2 / 1-3 collector 와 동일 패턴)
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            logger.warning(f"[dart_collector] 유효하지 않은 종목코드: {ticker!r}")
            return DartDisclosureSnapshot(
                ticker=str(ticker), snapshot_time=ts, is_valid=False, lookback_days=lookback_days
            )

        # lookback_days 검증
        if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days <= 0:
            logger.warning(f"[dart_collector] 유효하지 않은 lookback_days: {lookback_days!r}")
            return DartDisclosureSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, lookback_days=DEFAULT_LOOKBACK_DAYS
            )

        try:
            disclosures = self.dart_fetch(ticker, lookback_days)
        except Exception as e:
            logger.error(f"[dart_collector] {ticker} DART 호출 예외: {e}")
            return DartDisclosureSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, lookback_days=lookback_days
            )

        # dart_api.fetch_dart_disclosures 는 키 미설정 / 실패 시 빈 list 반환
        # 빈 응답을 "is_valid=False" 로 강제하면 정상적으로 공시가 없는 종목까지 invalid 처리됨
        # → 빈 list 도 valid (호재/제외 모두 False) 로 처리. 단 None 반환은 invalid.
        if disclosures is None:
            logger.warning(f"[dart_collector] {ticker} DART 응답 None — API 키 누락 가능")
            return DartDisclosureSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, lookback_days=lookback_days
            )

        if not isinstance(disclosures, list):
            logger.warning(f"[dart_collector] {ticker} DART 응답 list 아님: {type(disclosures)}")
            return DartDisclosureSnapshot(
                ticker=ticker, snapshot_time=ts, is_valid=False, lookback_days=lookback_days,
                raw_disclosures=None,
            )

        return self._classify(ticker, ts, lookback_days, disclosures)

    # ===== 다종목 =====

    def collect_for_universe(
        self,
        tickers: list[str],
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        snapshot_time: Optional[datetime] = None,
    ) -> list[DartDisclosureSnapshot]:
        """여러 종목 순차 수집. 한 종목 실패 시 다른 종목 계속 진행."""
        ts = snapshot_time or now_kst()
        snapshots: list[DartDisclosureSnapshot] = []
        success = 0
        excluded = 0
        positives = 0
        for ticker in tickers:
            snap = self.collect(ticker, lookback_days, ts)
            snapshots.append(snap)
            if snap.is_valid:
                success += 1
                if snap.exclude_by_disclosure:
                    excluded += 1
                if snap.has_positive_disclosure:
                    positives += 1
        logger.info(
            f"[dart_collector] {success}/{len(tickers)}건 수집 성공, "
            f"호재 {positives}건 / 즉시제외 {excluded}건 (시각: {ts.strftime('%H:%M:%S')})"
        )
        return snapshots

    # ===== 키워드 분류 =====

    def _classify(
        self,
        ticker: str,
        snapshot_time: datetime,
        lookback_days: int,
        disclosures: list,
    ) -> DartDisclosureSnapshot:
        """공시 list → 호재/즉시제외 분류."""
        positive: list[tuple[str, str]] = []
        exclusion: list[tuple[str, str]] = []

        for disc in disclosures:
            if not isinstance(disc, dict):
                continue
            title = str(disc.get("title") or "").strip()
            if not title:
                continue
            # **즉시제외 우선**: 한 공시에 호재/악재 키워드가 모두 있어도 제외 우선
            # 예: "유상증자 결정 (수주 자금 조달)" → 유상증자 제외
            matched_excl = _find_match(title, self._exclusion_keywords)
            if matched_excl:
                exclusion.append((title, matched_excl))
                continue
            matched_pos = _find_match(title, self._positive_keywords)
            if matched_pos:
                positive.append((title, matched_pos))

        has_positive = bool(positive)
        excluded = bool(exclusion)
        excl_reason: Optional[str] = None
        if excluded:
            # 첫 매칭만 reason 으로 노출 (전체는 exclusion_matches 에 보존)
            first_title, first_kw = exclusion[0]
            excl_reason = f"DART 즉시제외: '{first_kw}' in '{first_title[:50]}'"

        return DartDisclosureSnapshot(
            ticker=ticker,
            snapshot_time=snapshot_time,
            is_valid=True,
            lookback_days=lookback_days,
            total_disclosures=len(disclosures),
            positive_matches=tuple(positive),
            exclusion_matches=tuple(exclusion),
            has_positive_disclosure=has_positive,
            exclude_by_disclosure=excluded,
            exclusion_reason=excl_reason,
            raw_disclosures=disclosures,
        )


# ===== 헬퍼 =====


def _find_match(title: str, keywords: tuple[str, ...]) -> Optional[str]:
    """제목에 키워드 발견 시 그 키워드 반환, 없으면 None.

    Phase 1 단순 substring 매칭 (대소문자 구분 X). 한국어는 대소문자 영향 X
    이지만 "CB"/"BW" 등 영문 약어 대비 ``casefold()`` 적용.
    """
    title_norm = title.casefold()
    for kw in keywords:
        if kw.casefold() in title_norm:
            return kw
    return None
