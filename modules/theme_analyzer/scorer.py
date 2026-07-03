"""
scorer.py - 테마 점수 계산 모듈

이 파일은 수집된 테마 데이터를 바탕으로 투자 매력도 점수를 계산합니다.

점수 계산 로직 (0~65점):
- 모멘텀 점수 (25점): 테마 내 평균 5일 수익률 (KIS API 우선, 크롤링 폴백)
- 과열 감점 (0~-15점): 5일 수익률 +8% 이상 시 감점 (급등 테마 고점매수 방지)
- 뉴스 화제성 (15점): 최근 3일 뉴스 언급 횟수
- AI 감성 (10점): Claude AI 테마 전망 분석
- 종목수 보너스 (5점): 테마 규모 반영
- 기본 점수 (10점): 고정

사용법:
    from modules.theme_analyzer.scorer import (
        calculate_momentum_score,
        calculate_theme_total_score
    )

    momentum = calculate_momentum_score(avg_return_5d=5.2)
"""

from datetime import time
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import now_kst, settings
from logger import logger


# ===== 점수 배점 상수 =====
MAX_MOMENTUM_SCORE = 25.0    # 모멘텀 최대 25점
MAX_NEWS_SCORE = 15.0        # 뉴스 화제성 최대 15점
MAX_AI_SCORE = 10.0          # AI 감성 최대 10점
MAX_SIZE_BONUS = 5.0         # 종목수 보너스 최대 5점
BASE_SCORE = 10.0            # 기본 점수 10점
MAX_OVERHEAT_PENALTY = -15.0 # 과열 감점 최대 -15점

# 하위 호환용 (기존 함수 참조)
MAX_SUPPLY_SCORE = 25.0

TOTAL_MAX_SCORE = 65.0  # 모멘텀(25) + 뉴스(15) + AI(10) + 종목수(5) + 기본(10)


# ===== 모멘텀 점수 계산 =====

def calculate_momentum_score(
    avg_return_5d: float,
    avg_return_20d: Optional[float] = None,
    weight_5d: float = 0.7,
    weight_20d: float = 0.3
) -> float:
    """
    모멘텀 점수 계산 (최대 25점)

    테마의 평균 수익률을 바탕으로 모멘텀 점수를 계산합니다.
    선형 매핑 공식 사용.

    계산 로직:
    - 5일 수익률 +10% 이상: 25점 (만점)
    - 5일 수익률 0%: 12.5점 (중간)
    - 5일 수익률 -10% 이하: 0점 (최저)

    Args:
        avg_return_5d: 5일 평균 수익률 (%, 예: 5.2)
        avg_return_20d: 20일 평균 수익률 (%, 선택)
        weight_5d: 5일 수익률 가중치 (기본 70%)
        weight_20d: 20일 수익률 가중치 (기본 30%)

    Returns:
        모멘텀 점수 (0 ~ 25)
    """
    # 범위 제한 (-10% ~ +10%)
    clamped_5d = max(-10.0, min(10.0, avg_return_5d))

    # 점수 계산 (선형 매핑)
    # -10 → 0, 0 → 12.5, +10 → 25
    score_5d = ((clamped_5d + 10) / 20) * MAX_MOMENTUM_SCORE

    # 20일 수익률이 있으면 가중 평균
    if avg_return_20d is not None:
        clamped_20d = max(-10.0, min(10.0, avg_return_20d))
        score_20d = ((clamped_20d + 10) / 20) * MAX_MOMENTUM_SCORE

        final_score = (score_5d * weight_5d) + (score_20d * weight_20d)
    else:
        final_score = score_5d

    # 범위 보정
    final_score = max(0.0, min(MAX_MOMENTUM_SCORE, final_score))

    logger.debug(f"모멘텀 점수: {final_score:.1f}/{MAX_MOMENTUM_SCORE:.0f} (5일 수익률: {avg_return_5d:+.2f}%)")

    return round(final_score, 2)


# ===== 과열 감점 계산 =====

def calculate_overheat_penalty(
    avg_return_5d: float,
    avg_return_3d: Optional[float] = None
) -> float:
    """
    과열 감점 계산 (0 ~ -15점)

    5일 수익률이 +8% 이상이면 감점 시작, +15%에서 최대 -15점.
    3일 수익률이 5일의 80% 이상(급가속)이면 추가 -3점.

    Args:
        avg_return_5d: 5일 평균 수익률 (%)
        avg_return_3d: 3일 평균 수익률 (%, 선택)

    Returns:
        과열 감점 (-15 ~ 0)
    """
    OVERHEAT_THRESHOLD = 8.0   # 감점 시작 기준
    OVERHEAT_MAX = 15.0        # 최대 감점 기준 수익률
    PENALTY_MAX = 15.0         # 최대 감점 점수
    ACCEL_BONUS_PENALTY = 3.0  # 급가속 추가 감점

    if avg_return_5d < OVERHEAT_THRESHOLD:
        return 0.0

    # 선형 감점: 8% → 0점, 15% → -15점
    ratio = min(1.0, (avg_return_5d - OVERHEAT_THRESHOLD) / (OVERHEAT_MAX - OVERHEAT_THRESHOLD))
    penalty = -ratio * PENALTY_MAX

    # 급가속 감점: 3일 수익률이 5일의 80% 이상이면 추가 -3점
    if avg_return_3d is not None and avg_return_5d > 0:
        accel_ratio = avg_return_3d / avg_return_5d
        if accel_ratio >= 0.8 - 1e-9:
            penalty -= ACCEL_BONUS_PENALTY

    # 최대 감점 제한
    penalty = max(MAX_OVERHEAT_PENALTY, penalty)

    logger.debug(f"과열 감점: {penalty:.1f}점 (5일: {avg_return_5d:+.1f}%, 3일: {avg_return_3d})")

    return round(penalty, 2)


# ===== 수급 점수 계산 =====

def calculate_supply_score(
    foreign_buy_ratio: float,
    institution_buy_ratio: float,
    foreign_weight: float = 0.6,
    institution_weight: float = 0.4
) -> float:
    """
    수급 점수 계산 (최대 25점)
    
    테마 내 종목 중 외국인/기관이 순매수하는 종목 비율로 점수 계산
    
    Args:
        foreign_buy_ratio: 외국인 순매수 종목 비율 (0~100%)
        institution_buy_ratio: 기관 순매수 종목 비율 (0~100%)
        foreign_weight: 외국인 가중치 (기본 60%)
        institution_weight: 기관 가중치 (기본 40%)
    
    Returns:
        수급 점수 (0 ~ 25)
        
    Example:
        >>> calculate_supply_score(70.0, 50.0)
        15.5  # (70*0.6 + 50*0.4) / 100 * 25 = 15.5
    """
    # 비율 범위 제한 (0 ~ 100)
    foreign = max(0.0, min(100.0, foreign_buy_ratio))
    institution = max(0.0, min(100.0, institution_buy_ratio))
    
    # 가중 평균
    weighted_ratio = (foreign * foreign_weight) + (institution * institution_weight)
    
    # 점수 변환 (0~100% → 0~25점)
    score = (weighted_ratio / 100) * MAX_SUPPLY_SCORE
    
    logger.debug(
        f"수급 점수: {score:.1f}/25 "
        f"(외국인: {foreign:.0f}%, 기관: {institution:.0f}%)"
    )
    
    return round(score, 2)


def calculate_supply_score_from_amount(
    foreign_net_buy: float,
    institution_net_buy: float,
    threshold_billion: float = 50.0
) -> float:
    """
    순매수 금액 기반 수급 점수 계산 (최대 25점)
    
    Args:
        foreign_net_buy: 외국인 순매수 금액 (억원)
        institution_net_buy: 기관 순매수 금액 (억원)
        threshold_billion: 최대 점수 기준 금액 (기본 50억원)
    
    Returns:
        수급 점수 (0 ~ 25)
        
    Example:
        >>> calculate_supply_score_from_amount(30, 20)
        25.0  # (30 + 20) >= 50 → 만점
        
        >>> calculate_supply_score_from_amount(10, 5)
        7.5  # (10 + 5) / 50 * 25 = 7.5
    """
    # 총 순매수 금액 (음수면 0으로)
    total_net_buy = max(0.0, foreign_net_buy + institution_net_buy)
    
    # 점수 계산 (50억 이상이면 만점)
    ratio = min(1.0, total_net_buy / threshold_billion)
    score = ratio * MAX_SUPPLY_SCORE
    
    logger.debug(
        f"수급 점수: {score:.1f}/25 "
        f"(외국인: {foreign_net_buy:+.0f}억, 기관: {institution_net_buy:+.0f}억)"
    )

    return round(score, 2)


# ===== Phase 1-B½/1-C 수급 점수 v2 (DB 기반 정규화) =====

def _compute_supply_score_from_ratio(
    ratio: float,
    ref_ratio: float,
    max_score: float,
    signed: bool = True,
) -> float:
    """ratio → score 변환 헬퍼 (2026-06-15 옵션 C — 거래대금 대비 비율 정규화).

    ratio = foreign_net_5d / (trade_value_5d_avg × 5)
          (5일 거래대금 총합 대비 외인 순매수 비율)

    Args:
        ratio: 거래대금 대비 외인 5일 net 비율 (양수=매수 / 음수=매도, 단위 없음)
        ref_ratio: 만점/0점 기준 비율 (예: 0.15 = 15%)
        max_score: 최대 점수
        signed: True면 양선형 매핑 (-ref→0, 0→max/2, +ref→max). False면 음수→0

    Returns:
        0.0 ~ max_score 사이 점수
    """
    if max_score <= 0 or ref_ratio <= 0:
        return 0.0

    if signed:
        clamped = max(-ref_ratio, min(ref_ratio, ratio))
        r = (clamped + ref_ratio) / (2 * ref_ratio)
        return r * max_score
    else:
        if ratio <= 0:
            return 0.0
        r = min(1.0, ratio / ref_ratio)
        return r * max_score


def _compute_supply_score_from_avg(
    avg_net_bil: float,
    ref_bil: float,
    max_score: float,
    signed: bool = False,
    outlier_cap_bil: float = 0.0,
) -> float:
    """avg_net_bil → score 공통 변환 헬퍼.

    Args:
        avg_net_bil: 평균 외인 5일 net (억원, 음수 가능)
        ref_bil: 정규화 기준액 (signed=True일 때 ±이 값 범위로 매핑)
        max_score: 최대 점수
        signed: True면 양선형 매핑 (-ref→0, 0→max/2, +ref→max),
                False면 기존 (음수→0, 양수만 비례)
        outlier_cap_bil: 절댓값이 이 값을 넘으면 ±cap으로 clamp (0 또는 음수면 미적용)

    Returns:
        0.0 ~ max_score 사이 점수
    """
    if max_score <= 0 or ref_bil <= 0:
        return 0.0

    # outlier cap 적용 (0 또는 음수면 미적용)
    if outlier_cap_bil > 0:
        avg_net_bil = max(-outlier_cap_bil, min(outlier_cap_bil, avg_net_bil))

    if signed:
        # 양선형: -ref→0, 0→max/2, +ref→max
        clamped = max(-ref_bil, min(ref_bil, avg_net_bil))
        ratio = (clamped + ref_bil) / (2 * ref_bil)  # 0~1
        return ratio * max_score
    else:
        # 기존: 음수→0, 양수만 비례
        if avg_net_bil <= 0:
            return 0.0
        ratio = min(1.0, avg_net_bil / ref_bil)
        return ratio * max_score


def _stock_supply_ratio(snap: dict, lookback_days: int = 5) -> float:
    """단일 종목 snapshot에서 거래대금 대비 외인 5일 net 비율 계산.

    Returns: ratio (양수=매수, 음수=매도, 0=데이터 없음/거래대금 0)
    """
    foreign_net = snap.get("foreign_net_5d") or 0
    trade_value_avg = snap.get("trade_value_5d_avg") or 0
    denominator = trade_value_avg * lookback_days
    if denominator <= 0:
        return 0.0
    return foreign_net / denominator


def _valid_stock_codes(codes) -> list:
    """6자리 숫자 종목코드만 필터링 (URL 보강 실패/무효코드 방어).

    0015G0 같은 문자 포함 코드, 자릿수 불일치, 비문자열 값을 모두 제거한다.
    """
    if not codes:
        return []
    out = []
    for c in codes:
        if isinstance(c, str) and len(c) == 6 and c.isdigit():
            out.append(c)
    return out


def _resolve_theme_stock_codes_for_supply(theme, theme_name, supply_db=None) -> tuple:
    """supply_score_v2 계산용 종목코드 해석 + 출처 메타데이터 반환.

    theme["stocks"]가 비어 supply_score_v2가 강제 0.0이 되는 false-zero 관측
    (Phase 1-B½ noise)를 줄이기 위해, DB fallback으로 최근 screening_log 종목코드를 복원한다.

    출처 우선순위:
      1. 유효한 theme["stocks"]           → source 'theme_stocks'
      2. screening_log 최근 관측 (fallback) → source 'screening_log_recent'
      3. 없음                              → source 'none'

    Args:
        theme: 테마 dict (stocks 키 포함 가능)
        theme_name: 테마명 (fallback 조회 키)
        supply_db: Database 인스턴스 (None이면 fallback 미사용, 네트워크/DB 무접근)

    Returns:
        (codes: list[str], source: str)
    """
    theme_stocks = theme.get("stocks", []) if isinstance(theme, dict) else []
    theme_codes = _valid_stock_codes(theme_stocks)
    if theme_codes:
        return theme_codes, "theme_stocks"

    if (
        supply_db is not None
        and getattr(settings, "SUPPLY_THEME_STOCK_FALLBACK_ENABLED", False)
        and theme_name
    ):
        try:
            fallback = supply_db.get_recent_theme_stock_codes(
                theme_name,
                days=settings.SUPPLY_THEME_STOCK_FALLBACK_DAYS,
                limit=settings.SUPPLY_THEME_STOCK_FALLBACK_LIMIT,
            )
        except Exception as e:
            logger.warning(f"supply fallback 종목코드 조회 실패 [{theme_name}]: {e}")
            fallback = []
        fallback = _valid_stock_codes(fallback)
        if fallback:
            return fallback, "screening_log_recent"

    return [], "none"


def calculate_theme_supply_score_v2(
    stock_codes: list,
    db,
    top_n: int = 5,
    ref_bil: float = 10.0,
    max_score: float = 5.0,
    signed: bool = True,
    outlier_cap_bil: float = 100.0,
    use_ratio: bool = True,
    ref_ratio: float = 0.15,
) -> dict:
    """테마 종목 풀의 외국인 5일 누적 net 기반 supply_score_v2 계산.

    Phase 1-B½ Shadow Run에서는 관측만 (총점 미반영), Phase 1-C에서 활성화.

    2026-06-06: signed=True 양선형 매핑 + outlier_cap_bil 도입
    2026-06-15: use_ratio=True 옵션 C — 거래대금 대비 비율 정규화 (대형주/중소형주 동일 척도)

    Args:
        stock_codes: 테마의 종목코드 리스트 (6자리)
        db: Database 인스턴스 (close 안 함)
        top_n: 절댓값(use_ratio면 ratio, 아니면 net) 상위 N개 종목 선정
        ref_bil: absolute 모드 정규화 기준액 (억원)
        max_score: 최대 가산 점수
        signed: True면 양선형 매핑, False면 음수→0
        outlier_cap_bil: absolute 모드 outlier cap (0이면 미적용)
        use_ratio: True면 ratio 모드 (foreign_net_5d / (trade_value_5d_avg×5)),
                  False면 absolute 모드 (절대값 기준, deprecated 가능)
        ref_ratio: ratio 모드 기준 비율 (예: 0.15 = 15%)

    Returns:
        {
            'score': float (0~max_score),
            'foreign_pos_ratio': float (0~1, 양수 비율),
            'avg_net_bil': float (선정 종목 평균 외인 5일 net, 억원),
            'avg_ratio': float (선정 종목 평균 ratio, use_ratio=True일 때만 의미),
            'top_codes': list[str],
            'mode': str ('ratio' 또는 'absolute', 분석용)
        }
    """
    empty_result = {
        "score": 0.0,
        "foreign_pos_ratio": 0.0,
        "avg_net_bil": 0.0,
        "avg_ratio": 0.0,
        "top_codes": [],
        "mode": "ratio" if use_ratio else "absolute",
    }
    if not stock_codes:
        return empty_result

    try:
        snapshots = db.get_supply_snapshots_bulk(stock_codes)
    except Exception as e:
        logger.warning(f"supply_score_v2 — get_supply_snapshots_bulk 실패: {e}")
        return empty_result

    if not snapshots:
        return empty_result

    if use_ratio:
        # ratio 모드: foreign_net_5d / (trade_value_5d_avg × 5) 절댓값 기준 top_n
        # trade_value_avg=0/NULL인 종목은 ratio=0으로 자동 후순위
        sorted_codes = sorted(
            snapshots.keys(),
            key=lambda c: abs(_stock_supply_ratio(snapshots[c])),
            reverse=True,
        )[:top_n]
        ratios = [_stock_supply_ratio(snapshots[c]) for c in sorted_codes]
        if not ratios:
            return empty_result
        avg_ratio = sum(ratios) / len(ratios)
        pos_count = sum(1 for r in ratios if r > 0)
        pos_ratio = pos_count / len(ratios)
        score = _compute_supply_score_from_ratio(
            avg_ratio, ref_ratio, max_score, signed=signed
        )
        # avg_net_bil도 같이 산출 (디버깅용)
        nets = [(snapshots[c].get("foreign_net_5d") or 0) for c in sorted_codes]
        avg_net_bil = (sum(nets) / len(nets)) / 1e8 if nets else 0.0
    else:
        # absolute 모드 (기존): foreign_net_5d 절댓값 기준 top_n
        sorted_codes = sorted(
            snapshots.keys(),
            key=lambda c: abs(snapshots[c].get("foreign_net_5d") or 0),
            reverse=True,
        )[:top_n]
        nets = [(snapshots[c].get("foreign_net_5d") or 0) for c in sorted_codes]
        if not nets:
            return empty_result
        avg_net = sum(nets) / len(nets)
        avg_net_bil = avg_net / 1e8
        pos_count = sum(1 for n in nets if n > 0)
        pos_ratio = pos_count / len(nets)
        score = _compute_supply_score_from_avg(
            avg_net_bil, ref_bil, max_score, signed=signed, outlier_cap_bil=outlier_cap_bil
        )
        avg_ratio = 0.0

    return {
        "score": round(score, 3),
        "foreign_pos_ratio": round(pos_ratio, 3),
        "avg_net_bil": round(avg_net_bil, 2),
        "avg_ratio": round(avg_ratio, 4),
        "top_codes": sorted_codes,
        "mode": "ratio" if use_ratio else "absolute",
    }


def measure_universe_top_supply_signal(
    db,
    trade_date=None,
    top_n: int = 30,
    ref_bil: float = 10.0,
    max_score: float = 5.0,
    signed: bool = True,
    outlier_cap_bil: float = 100.0,
    use_ratio: bool = True,
    ref_ratio: float = 0.15,
) -> dict:
    """[권고 조치, 2026-05-13] universe 내부 외인 5일 net 상위 N개 신호 측정.

    2026-06-06: signed + outlier_cap_bil 도입.
    2026-06-15: use_ratio 옵션 C — 거래대금 대비 비율 정규화 (대형주 편향 제거).

    Args:
        db: Database 인스턴스
        trade_date: 측정 기준일 (None이면 daily_supply_snapshot MAX)
        top_n: universe 내부 상위 N개 종목
        ref_bil: absolute 모드 정규화 기준액 (억원)
        max_score: 최대 점수
        signed: True면 양선형 매핑
        outlier_cap_bil: absolute 모드 outlier cap
        use_ratio: True면 거래대금 대비 비율 모드 (대형주/중소형주 동일 척도)
        ref_ratio: ratio 모드 기준 비율 (예: 0.15 = 15%)

    Returns:
        {
            'score': float,
            'top_codes': list[str],
            'top_avg_net_bil': float,
            'top_avg_ratio': float (ratio 모드),
            'pos_ratio': float,
            'measured_date': str (ISO),
            'universe_size': int,
            'mode': str
        }
    """
    empty_result = {
        "score": 0.0,
        "top_codes": [],
        "top_avg_net_bil": 0.0,
        "top_avg_ratio": 0.0,
        "pos_ratio": 0.0,
        "measured_date": None,
        "universe_size": 0,
        "mode": "ratio" if use_ratio else "absolute",
    }

    try:
        with db.get_cursor() as cursor:
            # 측정 기준일 결정
            if trade_date is None:
                cursor.execute(
                    "SELECT MAX(trade_date) FROM daily_supply_snapshot"
                )
                row = cursor.fetchone()
                td = row[0] if row and row[0] else None
                if not td:
                    return empty_result
            else:
                td = trade_date.isoformat() if hasattr(trade_date, "isoformat") else trade_date

            if use_ratio:
                # ratio 모드: 절댓값 ratio 기준 top_n (trade_value_5d_avg>0 가드)
                cursor.execute(
                    """SELECT stock_code, foreign_net_5d, trade_value_5d_avg,
                              (foreign_net_5d * 1.0 / (trade_value_5d_avg * 5)) AS r
                       FROM daily_supply_snapshot
                       WHERE trade_date = ? AND foreign_net_5d IS NOT NULL
                         AND trade_value_5d_avg > 0
                       ORDER BY r DESC
                       LIMIT ?""",
                    (td, top_n),
                )
            else:
                # absolute 모드 (기존)
                cursor.execute(
                    """SELECT stock_code, foreign_net_5d, trade_value_5d_avg, 0.0 AS r
                       FROM daily_supply_snapshot
                       WHERE trade_date = ? AND foreign_net_5d IS NOT NULL
                       ORDER BY foreign_net_5d DESC
                       LIMIT ?""",
                    (td, top_n),
                )
            rows = cursor.fetchall()

            cursor.execute(
                "SELECT COUNT(*) FROM daily_supply_snapshot WHERE trade_date = ?",
                (td,),
            )
            universe_size = cursor.fetchone()[0]
    except Exception as e:
        logger.warning(f"universe_top_supply_signal 측정 실패: {e}")
        return empty_result

    if not rows:
        return {**empty_result, "measured_date": td, "universe_size": universe_size}

    nets = [r[1] for r in rows]
    top_codes = [r[0] for r in rows]
    avg_net_bil = (sum(nets) / len(nets)) / 1e8
    pos_count = sum(1 for n in nets if n > 0)
    pos_ratio = pos_count / len(nets)

    if use_ratio:
        ratios = [r[3] for r in rows]
        avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
        score = _compute_supply_score_from_ratio(
            avg_ratio, ref_ratio, max_score, signed=signed
        )
    else:
        avg_ratio = 0.0
        score = _compute_supply_score_from_avg(
            avg_net_bil, ref_bil, max_score, signed=signed, outlier_cap_bil=outlier_cap_bil
        )

    return {
        "score": round(score, 3),
        "top_codes": top_codes,
        "top_avg_net_bil": round(avg_net_bil, 2),
        "top_avg_ratio": round(avg_ratio, 4),
        "pos_ratio": round(pos_ratio, 3),
        "measured_date": td,
        "universe_size": universe_size,
        "mode": "ratio" if use_ratio else "absolute",
    }


# ===== 뉴스 화제성 점수 계산 =====

def calculate_news_score(
    news_count: int,
    threshold_high: int = 100,
    threshold_mid: int = 50
) -> float:
    """
    뉴스 화제성 점수 계산 (최대 15점)

    최근 3일 뉴스 언급 횟수를 바탕으로 화제성 점수 계산

    계산 로직:
    - 100건 이상: 15점 (만점)
    - 50건: 7.5점 (중간)
    - 10건 이하: 1.5점 (최저)

    Args:
        news_count: 뉴스 언급 횟수
        threshold_high: 만점 기준 (기본 100건)
        threshold_mid: 중간 기준 (기본 50건)

    Returns:
        뉴스 점수 (0 ~ 15)
        
    Example:
        >>> calculate_news_score(127)
        15.0  # 100건 이상 → 만점

        >>> calculate_news_score(50)
        7.5  # 중간
    """
    if news_count <= 0:
        return 0.0
    
    half_max = MAX_NEWS_SCORE / 2.0
    if news_count >= threshold_high:
        score = MAX_NEWS_SCORE
    elif news_count >= threshold_mid:
        # 50~100건: half~max점 (선형)
        ratio = (news_count - threshold_mid) / (threshold_high - threshold_mid)
        score = half_max + (ratio * half_max)
    else:
        # 0~50건: 0~half점 (선형)
        ratio = news_count / threshold_mid
        score = ratio * half_max

    # 최소 점수 보장 (뉴스가 있으면 최소 1점)
    if news_count > 0:
        score = max(1.0, score)

    logger.debug(f"뉴스 점수: {score:.1f}/{MAX_NEWS_SCORE:.0f} (뉴스 {news_count}건)")
    
    return round(score, 2)


# ===== AI 감성 점수 계산 =====

def calculate_ai_sentiment_score(ai_sentiment: float) -> float:
    """
    AI 감성 분석 점수 계산 (최대 10점)

    Claude AI가 분석한 감성 점수(0-10)를 10점 만점으로 변환

    Args:
        ai_sentiment: AI 감성 점수 (0 ~ 10)

    Returns:
        AI 점수 (0 ~ 10)

    Example:
        >>> calculate_ai_sentiment_score(8.5)
        8.5  # 8.5 / 10 * 10 = 8.5
    """
    # 범위 제한 (0 ~ 10)
    clamped = max(0.0, min(10.0, ai_sentiment))

    # 점수 변환 (0~10 → 0~10)
    score = (clamped / 10.0) * MAX_AI_SCORE

    logger.debug(f"AI 점수: {score:.1f}/{MAX_AI_SCORE:.0f} (감성: {ai_sentiment:.1f}/10)")

    return round(score, 2)


# ===== 총점 계산 =====

def calculate_theme_total_score(
    momentum_score: Optional[float] = None,
    supply_score: Optional[float] = None,
    news_score: Optional[float] = None,
    ai_score: Optional[float] = None,
    # 또는 원본 데이터로 계산
    avg_return_5d: Optional[float] = None,
    foreign_buy_ratio: Optional[float] = None,
    institution_buy_ratio: Optional[float] = None,
    news_count: Optional[int] = None,
    ai_sentiment: Optional[float] = None
) -> dict:
    """
    테마 종합 점수 계산 (최대 65점)
    
    4가지 요소의 점수를 합산하여 총점 계산
    
    Args:
        momentum_score: 이미 계산된 모멘텀 점수 (0~25)
        supply_score: 이미 계산된 수급 점수 (0~25)
        news_score: 이미 계산된 뉴스 점수 (0~15)
        ai_score: 이미 계산된 AI 점수 (0~10)
        
        또는 원본 데이터:
        avg_return_5d: 5일 평균 수익률 (%)
        foreign_buy_ratio: 외국인 순매수 비율 (%)
        institution_buy_ratio: 기관 순매수 비율 (%)
        news_count: 뉴스 언급 횟수
        ai_sentiment: AI 감성 점수 (0~10)
    
    Returns:
        점수 상세 정보 딕셔너리:
        {
            'total_score': 58.0,
            'momentum_score': 19.0,
            'supply_score': 15.5,
            'news_score': 15.0,
            'ai_score': 8.5,
            'grade': 'S'  # S(>=58), A(>=48), B(>=38), C(>=30), D
        }

    Example:
        >>> result = calculate_theme_total_score(
        >>>     avg_return_5d=5.2,
        >>>     foreign_buy_ratio=70.0,
        >>>     institution_buy_ratio=50.0,
        >>>     news_count=127,
        >>>     ai_sentiment=8.5
        >>> )
        >>> print(result['total_score'])
        58.0
    """
    # 점수 계산 (주어진 값 사용 또는 원본 데이터로 계산)
    
    # 모멘텀 점수
    if momentum_score is not None:
        m_score = max(0, min(MAX_MOMENTUM_SCORE, momentum_score))
    elif avg_return_5d is not None:
        m_score = calculate_momentum_score(avg_return_5d)
    else:
        m_score = 0.0
    
    # 수급 점수
    if supply_score is not None:
        s_score = max(0, min(MAX_SUPPLY_SCORE, supply_score))
    elif foreign_buy_ratio is not None and institution_buy_ratio is not None:
        s_score = calculate_supply_score(foreign_buy_ratio, institution_buy_ratio)
    else:
        s_score = 0.0
    
    # 뉴스 점수
    if news_score is not None:
        n_score = max(0, min(MAX_NEWS_SCORE, news_score))
    elif news_count is not None:
        n_score = calculate_news_score(news_count)
    else:
        n_score = 0.0
    
    # AI 점수
    if ai_score is not None:
        a_score = max(0, min(MAX_AI_SCORE, ai_score))
    elif ai_sentiment is not None:
        a_score = calculate_ai_sentiment_score(ai_sentiment)
    else:
        a_score = 0.0
    
    # 총점 계산
    total = m_score + s_score + n_score + a_score
    
    # 등급 산정 (최대 65점 기준)
    if total >= 58:
        grade = "S"
    elif total >= 48:
        grade = "A"
    elif total >= 38:
        grade = "B"
    elif total >= 30:
        grade = "C"
    else:
        grade = "D"
    
    result = {
        "total_score": round(total, 2),
        "momentum_score": round(m_score, 2),
        "supply_score": round(s_score, 2),
        "news_score": round(n_score, 2),
        "ai_score": round(a_score, 2),
        "grade": grade
    }
    
    logger.debug(
        f"총점: {total:.1f}/65 ({grade}) - "
        f"모멘텀:{m_score:.1f}, 수급:{s_score:.1f}, "
        f"뉴스:{n_score:.1f}, AI:{a_score:.1f}"
    )
    
    return result


def _get_kis_api():
    """KIS API 인스턴스 반환 (실패 시 None)"""
    try:
        from modules.stock_screener.kis_api import KISApi
        return KISApi()
    except Exception:
        return None


def _calculate_theme_momentum(theme: dict, kis) -> float:
    """
    KIS API로 테마 종목 5일 수익률 계산, 실패 시 크롤링 데이터 폴백

    Args:
        theme: 테마 정보 (stocks 리스트 포함)
        kis: KISApi 인스턴스 또는 None

    Returns:
        평균 5일 수익률 (%)
    """
    # 1차: KIS API (장중에만 가능)
    if kis and theme.get("stocks"):
        returns = []
        for code in theme["stocks"][:5]:  # 최대 5종목
            try:
                daily = kis.get_daily_price(code, count=10)
                if daily and len(daily) >= 6:
                    close_today = daily[0]["close"]
                    close_5d_ago = daily[5]["close"]
                    if close_5d_ago > 0:
                        ret_5d = (close_today - close_5d_ago) / close_5d_ago * 100
                        returns.append(ret_5d)
            except Exception as e:
                logger.debug(f"KIS API 종목 {code} 조회 실패: {e}")
                continue
        if returns:
            avg = sum(returns) / len(returns)
            logger.debug(f"KIS API 모멘텀: {avg:+.2f}% ({len(returns)}종목)")
            return avg

    # 2차: 크롤링 데이터 폴백
    avg_change = theme.get("avg_change_rate")
    three_day = theme.get("three_day_rate")

    # 장중(09:00~15:30)이면 당일 등락률 사용, 장전이면 3일 등락률 우선
    current_time = now_kst().time()
    is_market_open = time(9, 0) <= current_time <= time(15, 30)

    if is_market_open and avg_change is not None:
        return avg_change
    # 장전: 3일 등락률 우선 → 당일 등락률 폴백
    if three_day is not None and three_day != 0.0:
        return three_day
    if avg_change is not None:
        return avg_change
    return 0.0


def calculate_liquidity_penalty(
    theme_name: str,
    pass_rates: dict,
    min_pass_rate: float = 0.15,
    low_pass_rate: float = 0.25,
    penalty_max: float = 12.0,
    min_data_days: int = 3
) -> tuple[float, str]:
    """
    테마 통과율 기반 유동성 보정 점수 계산

    과거 screening_log 통과율이 낮은 테마에 감점을 적용합니다.

    Args:
        theme_name: 테마명
        pass_rates: {theme: {"pass_rate": float, "days_data": int, ...}}
        min_pass_rate: 최소 통과율 (이하면 최대 감점)
        low_pass_rate: 저유동성 기준 (이하면 비례 감점)
        penalty_max: 최대 감점 (절대값)
        min_data_days: 최소 데이터 일수 (미만이면 판단 보류)

    Returns:
        (penalty: float, note: str)
    """
    data = pass_rates.get(theme_name)

    # 히스토리 없음 또는 데이터 부족 → 비례 기본 감점
    if not data or data.get("days_data", 0) < min_data_days:
        default_penalty = -round(penalty_max * 0.25, 1)
        return default_penalty, "신규테마(데이터부족)"

    pass_rate = data.get("pass_rate", 0.0)

    # 통과율 충분 → 감점 없음
    if pass_rate >= low_pass_rate:
        return 0.0, ""

    # 통과율 < min_pass_rate → 최대 감점
    if pass_rate <= min_pass_rate:
        return -penalty_max, f"유동성탈락({pass_rate:.0%})"

    # 통과율 min~low 구간 → 비례 감점
    denom = low_pass_rate - min_pass_rate
    if denom <= 0:
        return -penalty_max, f"유동성주의({pass_rate:.0%})"

    ratio = (low_pass_rate - pass_rate) / denom
    penalty = -ratio * penalty_max
    return round(penalty, 1), f"유동성주의({pass_rate:.0%})"


def score_themes(themes: list[dict], include_news: bool = False, include_ai: bool = False,
                 pass_rates: dict = None) -> list[dict]:
    """
    여러 테마에 대해 점수 일괄 계산

    배점: 모멘텀(25) + 과열(0~-15) + 뉴스(15) + AI감성(10) + 종목수(5) + 기본(10) + 유동성(0~-12) = 최대 65점
    과열 감점: 5일 수익률 +8% 이상 시 감점 시작, 급등 테마 고점매수 방지.
    유동성 감점: screening_log 통과율이 낮은 테마에 감점 (소형주 테마 방지).
    뉴스/AI는 include_news/include_ai 플래그로 활성화 (17:00 일별 수집 시).

    Args:
        themes: 테마 정보 리스트
        include_news: 뉴스 점수 수집 및 반영 여부
        include_ai: AI 감성 점수 수집 및 반영 여부
        pass_rates: 테마별 스크리닝 통과율 (None이면 유동성 검증 스킵)

    Returns:
        점수가 추가된 테마 리스트 (점수 내림차순 정렬)
    """
    scored_themes = []

    # KIS API 인스턴스 (테마 종목 가격 조회용)
    kis = _get_kis_api()

    # 종목 매핑 (URL 있는 테마에 stocks 추가 → KIS API 모멘텀 활성화)
    if include_news:
        _enrich_theme_stocks(themes)

    # 뉴스 건수+텍스트 수집 (상위 테마 대상)
    news_data = {}  # {theme_name: {"count": int, "text": str}}
    if include_news:
        news_data = _collect_news_data(themes)

    # v16 Phase 1-B½ Shadow Run — supply_score_v2 계산용 DB 인스턴스 + universe 측정
    # SUPPLY_SIGNAL_ENABLED=False 시 전체 분기 스킵 (기존 동작 보존)
    _supply_db = None
    _universe_top_signal = None
    # SUPPLY_SCORE_MAX=0 (관측 모드) 또는 음수일 때 5.0 만점 기준으로 관측 스케일 유지
    # — Phase 1-C 활성화 후엔 SUPPLY_SCORE_MAX=2.5/5.0 등 양수로 설정
    _supply_max_score = settings.SUPPLY_SCORE_MAX if settings.SUPPLY_SCORE_MAX > 0 else 5.0
    if settings.SUPPLY_SIGNAL_ENABLED:
        try:
            from database import Database  # lazy import (circular 방지)
            _supply_db = Database()
            _supply_db.connect()
            # 권고 조치 (2026-05-13): universe 내부 동적 TOP 신호 1회 측정
            _universe_top_signal = measure_universe_top_supply_signal(
                _supply_db,
                top_n=settings.SUPPLY_UNIVERSE_TOP_N,
                ref_bil=settings.SUPPLY_INTENSITY_REF_BIL,
                max_score=_supply_max_score,
                signed=settings.SUPPLY_SCORE_SIGNED,
                outlier_cap_bil=settings.SUPPLY_OUTLIER_CAP_BIL,
                use_ratio=settings.SUPPLY_USE_RATIO,
                ref_ratio=settings.SUPPLY_REF_RATIO,
            )
            _mode = _universe_top_signal.get("mode", "absolute")
            _ratio_str = (
                f", top_avg_ratio={_universe_top_signal.get('top_avg_ratio', 0):.2%}"
                if _mode == "ratio" else ""
            )
            logger.info(
                f"📊 universe top signal [{_mode}] — date={_universe_top_signal.get('measured_date')}, "
                f"size={_universe_top_signal.get('universe_size')}, "
                f"top_avg={_universe_top_signal.get('top_avg_net_bil'):.2f}억"
                f"{_ratio_str}, "
                f"pos_ratio={_universe_top_signal.get('pos_ratio'):.0%}"
            )
        except Exception as e:
            logger.warning(f"Phase 1-B½ DB 초기화/universe 측정 실패 (계속): {e}")
            _supply_db = None
            _universe_top_signal = None

    for theme in themes:
        theme_name = theme.get("name", theme.get("theme", ""))

        # 1. 모멘텀: KIS API 5일 수익률 (우선) → 크롤링 폴백
        avg_return = _calculate_theme_momentum(theme, kis)

        # 모멘텀 점수: ((avg_return + 10) / 20) * 25
        clamped = max(-10.0, min(10.0, avg_return))
        momentum_score = ((clamped + 10) / 20) * MAX_MOMENTUM_SCORE

        # 2. 뉴스 점수 (최대 15점)
        theme_news = news_data.get(theme_name, {})
        news_count = theme_news.get("count", 0) if theme_news else (theme.get("news_count", 0) or 0)
        news_text = theme_news.get("text", "") if theme_news else ""
        news_score = calculate_news_score(news_count) if news_count > 0 else 0.0

        # 3. AI 감성 점수 (최대 10점) — 이미 theme에 있으면 재사용
        ai_sentiment = theme.get("ai_sentiment", 0) or 0
        ai_score = calculate_ai_sentiment_score(ai_sentiment) if ai_sentiment > 0 else 0.0

        # 4. 종목수 보너스 (최대 5점)
        stock_count = theme.get("stock_count", len(theme.get("stocks", [])))
        size_bonus = min(MAX_SIZE_BONUS, stock_count * 1)

        # 5. 과열 감점 (0 ~ -15점)
        three_day = theme.get("three_day_rate") or avg_return
        overheat = calculate_overheat_penalty(avg_return, three_day)

        # 6. 유동성 보정 (0 ~ -8점) — pass_rates 없으면 스킵
        liquidity_penalty = 0.0
        liquidity_note = ""
        if pass_rates:
            liquidity_penalty, liquidity_note = calculate_liquidity_penalty(
                theme_name, pass_rates,
                min_pass_rate=settings.THEME_MIN_PASS_RATE,
                low_pass_rate=settings.THEME_LOW_PASS_RATE,
                penalty_max=settings.THEME_PASS_RATE_PENALTY_MAX,
                min_data_days=settings.THEME_PASS_RATE_MIN_DATA_DAYS,
            )

        # 7. 기본점수 10점
        total = momentum_score + overheat + news_score + ai_score + size_bonus + BASE_SCORE + liquidity_penalty

        # 8. v16 Phase 1-B½ Shadow Run — supply_score_v2 계산 (관측 모드면 미반영)
        supply_v2_result = None
        supply_score_v2 = 0.0
        _stocks_source = "none"
        if _supply_db is not None:
            # 2026-07-03 — theme["stocks"] 비면 screening_log fallback으로 종목코드 복원
            #   (supply_score_v2 false-zero 관측 감소). 출처 메타데이터 breakdown_json 기록.
            theme_stock_codes, _stocks_source = _resolve_theme_stock_codes_for_supply(
                theme, theme_name, supply_db=_supply_db
            )
            supply_v2_result = calculate_theme_supply_score_v2(
                theme_stock_codes,
                _supply_db,
                top_n=settings.SUPPLY_SCORE_TOP_N,
                ref_bil=settings.SUPPLY_INTENSITY_REF_BIL,
                max_score=_supply_max_score,  # 0/음수 시 관측용 5.0 폴백
                signed=settings.SUPPLY_SCORE_SIGNED,
                outlier_cap_bil=settings.SUPPLY_OUTLIER_CAP_BIL,
                use_ratio=settings.SUPPLY_USE_RATIO,
                ref_ratio=settings.SUPPLY_REF_RATIO,
            )
            supply_score_v2 = supply_v2_result["score"]
            # 관측 모드 OFF + MAX > 0 일 때만 총점 가산 (Phase 1-C 활성화 시)
            if (not settings.SUPPLY_SCORE_OBSERVE_ONLY) and settings.SUPPLY_SCORE_MAX > 0:
                total += supply_score_v2

        # 등급 산정 (최대 65점 기준)
        if total >= 58:
            grade = "S"
        elif total >= 48:
            grade = "A"
        elif total >= 38:
            grade = "B"
        elif total >= 30:
            grade = "C"
        else:
            grade = "D"

        # 선정 이유 생성
        reasons = []
        if momentum_score >= 20:
            reasons.append(f"강한모멘텀({avg_return:+.1f}%)")
        elif momentum_score >= 15:
            reasons.append(f"양호한모멘텀({avg_return:+.1f}%)")
        elif avg_return != 0:
            reasons.append(f"모멘텀({avg_return:+.1f}%)")
        if overheat < 0:
            reasons.append(f"과열감점({overheat:+.1f})")
        if liquidity_penalty < 0:
            reasons.append(liquidity_note)
        if news_score >= 8:
            reasons.append(f"화제({news_count}건)")
        if ai_score >= 6:
            reasons.append(f"AI긍정({ai_sentiment:.0f})")
        if stock_count >= 5:
            reasons.append(f"{stock_count}종목")

        selection_reason = ", ".join(reasons) if reasons else "기본조건충족"

        # 원본 테마 정보에 점수 추가
        # 주의: main.py:_pick_momentum 폴백 체인이 momentum/momentum_score 양쪽 키 존재에 의존.
        #       한쪽 키만 남기는 변경은 main.py 수정과 동시 진행 필수 (회귀 방지).
        # 관측 모드: supply_score는 0 유지 (총점 미반영). 활성화 모드: supply_score_v2 노출
        _displayed_supply_score = (
            round(supply_score_v2, 2)
            if (not settings.SUPPLY_SCORE_OBSERVE_ONLY) and settings.SUPPLY_SCORE_MAX > 0
            else 0
        )
        scored_theme = {
            **theme,
            "theme": theme_name,
            "avg_change_rate": round(avg_return, 2),
            "total_score": round(total, 2),
            "score": round(total, 2),
            "momentum": round(momentum_score, 2),
            "momentum_score": round(momentum_score, 2),
            "supply_score": _displayed_supply_score,
            "news_score": round(news_score, 2),
            "news_count": news_count,
            "news": news_text,  # AI 감성분석용 뉴스 텍스트
            "ai_score": round(ai_score, 2),
            "ai_sentiment": round(ai_sentiment, 2) if ai_sentiment else 0,
            "overheat_penalty": round(overheat, 2),
            "liquidity_penalty": round(liquidity_penalty, 2),
            "liquidity_note": liquidity_note,
            "pass_rate": pass_rates.get(theme_name, {}).get("pass_rate") if pass_rates else None,
            "bonus_score": round(size_bonus, 2),
            "grade": grade,
            "selection_reason": selection_reason
        }
        scored_themes.append(scored_theme)

        # v16 Phase 1-B½ — supply_score_observation 기록 (관측 모드 포함, 항상 저장)
        if _supply_db is not None and supply_v2_result is not None:
            try:
                import json as _json
                # code-tester 주의 2/3 반영: score_applied + stocks_available 명시
                # — Phase 1-C 분석 시 noise 데이터 필터링용
                _score_applied = (
                    (not settings.SUPPLY_SCORE_OBSERVE_ONLY)
                    and settings.SUPPLY_SCORE_MAX > 0
                )
                breakdown = {
                    "theme_top_codes": supply_v2_result.get("top_codes", []),
                    "theme_avg_net_bil": supply_v2_result.get("avg_net_bil"),
                    "theme_avg_ratio": supply_v2_result.get("avg_ratio"),  # 2026-06-15 ratio 모드
                    "theme_pos_ratio": supply_v2_result.get("foreign_pos_ratio"),
                    "theme_mode": supply_v2_result.get("mode"),  # 'ratio' | 'absolute'
                    # 권고 조치: universe 내부 동적 TOP 신호 병기 저장
                    "universe_top_signal": _universe_top_signal,
                    "observe_only": settings.SUPPLY_SCORE_OBSERVE_ONLY,
                    "score_applied": _score_applied,          # 실제 총점 가산 여부 (분석용)
                    "stocks_available": len(theme_stock_codes) > 0,  # noise 필터링용
                    # 2026-07-03 — 종목코드 커버리지/출처 메타데이터 (Phase 1-C gating 판단용)
                    "stocks_count": len(theme_stock_codes),
                    "stocks_source": _stocks_source,  # theme_stocks | screening_log_recent | none
                    "stocks_missing_reason": (
                        None if theme_stock_codes
                        else "no_theme_stocks_or_recent_screening_log"
                    ),
                    "score_max": settings.SUPPLY_SCORE_MAX,
                    "ref_bil": settings.SUPPLY_INTENSITY_REF_BIL,
                    "top_n": settings.SUPPLY_SCORE_TOP_N,
                    # 2026-06-06 변환식 재설계 추적 (Phase 1-B½ 분포 차별성 개선용)
                    "signed": settings.SUPPLY_SCORE_SIGNED,
                    "outlier_cap_bil": settings.SUPPLY_OUTLIER_CAP_BIL,
                    # 2026-06-15 ratio 모드 추적 (옵션 C)
                    "use_ratio": settings.SUPPLY_USE_RATIO,
                    "ref_ratio": settings.SUPPLY_REF_RATIO,
                }
                _supply_db.save_supply_score_observation(
                    obs_date=now_kst().date(),
                    theme_name=theme_name,
                    supply_score_v2=supply_score_v2,
                    momentum_score=round(momentum_score, 2),
                    news_score=round(news_score, 2),
                    ai_score=round(ai_score, 2),
                    theme_total_actual=round(total, 2),
                    breakdown_json=_json.dumps(breakdown, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"supply_score_observation 저장 실패 [{theme_name}] (계속): {e}")

    # DB 인스턴스 cleanup (lazy init된 경우만)
    if _supply_db is not None:
        try:
            _supply_db.close()
        except Exception:
            pass

    # 총점 기준 내림차순 정렬
    scored_themes.sort(key=lambda x: x["total_score"], reverse=True)

    active = []
    if include_news:
        active.append("뉴스")
    if include_ai:
        active.append("AI")
    mode = f"모멘텀+{'+'.join(active)}" if active else "모멘텀"
    logger.info(f"📊 {len(scored_themes)}개 테마 점수 계산 완료 ({mode})")

    return scored_themes


def _collect_news_data(themes: list[dict]) -> dict[str, dict]:
    """
    상위 30개 테마의 뉴스 건수 + 텍스트 일괄 수집

    Returns:
        {theme_name: {"count": int, "text": str}}
    """
    try:
        from modules.theme_analyzer.crawlers import crawl_theme_news, _random_delay
    except ImportError:
        logger.warning("[scorer] crawl_theme_news 임포트 실패")
        return {}

    results = {}
    for theme in themes[:30]:
        theme_name = theme.get("name", theme.get("theme", ""))
        if not theme_name:
            continue
        try:
            data = crawl_theme_news(theme_name, days=3)
            if data and data.get("count", 0) > 0:
                results[theme_name] = data
            _random_delay()
        except Exception as e:
            logger.debug(f"[scorer] 뉴스 수집 실패 ({theme_name}): {e}")
    logger.info(f"📰 뉴스 수집: {len(results)}개 테마 (텍스트 포함)")
    return results


def calculate_theme_supply_ratio(theme_url: str, kis) -> float:
    """
    테마 URL로부터 종목 목록을 크롤링하고, KIS API로 수급 데이터를 조회하여
    외국인+기관 순매수인 종목의 비율(%)을 반환한다.

    Args:
        theme_url: 네이버 테마 상세 페이지 URL
        kis: KIS API 인스턴스 (get_investor_trading 메서드 필요)

    Returns:
        수급 양호 종목 비율 (0.0~100.0). 조회 실패 시 0.0
    """
    if not theme_url or not kis:
        return 0.0

    try:
        from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks
    except ImportError:
        logger.warning("[scorer] crawl_naver_theme_stocks 임포트 실패")
        return 0.0

    # 종목 크롤링 (별도 로컬 변수 — theme["stocks"] 수정하지 않음)
    try:
        stock_list = crawl_naver_theme_stocks(theme_url)
    except Exception as e:
        logger.debug(f"[supply] 종목 크롤링 실패: {e}")
        return 0.0

    codes = [s["code"] for s in stock_list[:8] if s.get("code")]
    if not codes:
        return 0.0

    valid_count = 0
    positive_count = 0
    consecutive_failures = 0

    for code in codes:
        # circuit breaker: 연속 5회 API 실패 시 중단
        if consecutive_failures >= 5:
            logger.warning(f"[supply] circuit breaker: 연속 {consecutive_failures}회 API 실패, 중단")
            break

        try:
            investor = kis.get_investor_trading(code, days=5)
        except Exception as e:
            logger.debug(f"[supply] {code} 수급 조회 예외: {e}")
            consecutive_failures += 1
            continue

        if investor is None:
            consecutive_failures += 1
            continue

        # 성공 시 연속 실패 카운터 리셋
        consecutive_failures = 0
        valid_count += 1

        # 외국인+기관 순매수 합계가 양수이면 수급 양호
        foreign_net = investor.get("foreign_net", 0)
        institution_net = investor.get("institution_net", 0)
        if (foreign_net + institution_net) > 0:
            positive_count += 1

    if valid_count == 0:
        return 0.0

    ratio = round((positive_count / valid_count) * 100, 1)
    logger.debug(f"[supply] 수급비율: {positive_count}/{valid_count} = {ratio}%")
    return ratio


def _enrich_theme_stocks(themes: list[dict], max_stocks: int = 3) -> None:
    """
    URL 있는 테마에 종목코드 리스트 추가 (in-place)

    상위 15개 테마의 URL에서 crawl_naver_theme_stocks()로 종목 코드를 추출하여
    theme["stocks"] = [code1, code2, ...] 세팅. 실패 시 빈 리스트.

    Args:
        themes: 점수화 대상 테마 리스트
        max_stocks: 테마당 최대 종목 수
    """
    try:
        from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks, _random_delay
    except ImportError:
        logger.warning("[scorer] crawl_naver_theme_stocks 임포트 실패")
        return

    enriched = 0
    for theme in themes[:15]:
        url = theme.get("url", "")
        if not url:
            theme.setdefault("stocks", [])
            continue
        try:
            stocks = crawl_naver_theme_stocks(url)
            theme["stocks"] = [s["code"] for s in stocks[:max_stocks]]
            if theme["stocks"]:
                enriched += 1
                logger.debug(f"[{theme.get('name', '')}] 종목 매핑: {theme['stocks']}")
            _random_delay()
        except Exception as e:
            theme["stocks"] = []
            logger.debug(f"[scorer] 종목 매핑 실패 ({theme.get('name', '')}): {e}")

    logger.info(f"🔗 종목 매핑: {enriched}/{min(len(themes), 15)}개 테마")


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 테마 점수 계산 테스트")
    print("=" * 60)
    
    # 개별 점수 테스트
    print("\n1️⃣ 모멘텀 점수 테스트:")
    test_returns = [10.0, 5.0, 0.0, -3.0, -10.0]
    for ret in test_returns:
        score = calculate_momentum_score(ret)
        print(f"   수익률 {ret:+.1f}% → {score:.1f}점")
    
    print("\n2️⃣ 수급 점수 테스트:")
    score = calculate_supply_score(70.0, 50.0)
    print(f"   외국인 70%, 기관 50% → {score:.1f}점")
    
    score = calculate_supply_score_from_amount(30, 20)
    print(f"   외국인 +30억, 기관 +20억 → {score:.1f}점")
    
    print("\n3️⃣ 뉴스 점수 테스트:")
    test_counts = [0, 20, 50, 100, 200]
    for count in test_counts:
        score = calculate_news_score(count)
        print(f"   뉴스 {count}건 → {score:.1f}점")
    
    print("\n4️⃣ AI 점수 테스트:")
    test_sentiments = [10.0, 8.5, 5.0, 2.0, 0.0]
    for sent in test_sentiments:
        score = calculate_ai_sentiment_score(sent)
        print(f"   감성 {sent:.1f}/10 → {score:.1f}점")
    
    print("\n5️⃣ 종합 점수 테스트:")
    result = calculate_theme_total_score(
        avg_return_5d=5.2,
        foreign_buy_ratio=70.0,
        institution_buy_ratio=50.0,
        news_count=127,
        ai_sentiment=8.5
    )
    print(f"   총점: {result['total_score']}/65 ({result['grade']})")
    print(f"   - 모멘텀: {result['momentum_score']}/25")
    print(f"   - 수급: {result['supply_score']}/25")
    print(f"   - 뉴스: {result['news_score']}/15")
    print(f"   - AI: {result['ai_score']}/10")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
