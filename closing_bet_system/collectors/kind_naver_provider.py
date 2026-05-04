"""KIND 시장경보 네이버 프로바이더 (단위 2-2b-1).

PRD 4-1 "관리/투자경고/거래정지 → 제외" 하드 룰을 위해 네이버 금융 시장경보 페이지에서
종목코드 + 경보 단계를 추출한다. KIND 공식 사이트는 SPA 라 직접 크롤링이 어렵고,
네이버 금융이 더 안정적이고 패턴이 일관되어 1차 출처로 채택.

**역할**:
- ``KindAlertCollector(provider=fetch_kind_alerts)`` 형태로 주입 (Phase 2-2 인터페이스 재사용)
- 5 페이지 fetch → 종목별 가장 강한 단계 dict 반환

**5 데이터 출처**:
- ``/sise/management.naver`` → 관리종목 (severity=3)
- ``/sise/trading_halt.naver`` → 매매거래정지 (severity=3)
- ``/sise/investment_alert.naver?type=caution`` → 투자주의 (severity=1)
- ``/sise/investment_alert.naver?type=warning`` → 투자경고 (severity=2)
- ``/sise/investment_alert.naver?type=risk`` → 투자위험 (severity=3)

**그래스풀 폴백**:
- 한 페이지 실패 → 나머지로 진행 (per-page try/except 격리)
- 모든 페이지 실패 → 빈 dict 반환 (KindAlertCollector 가 정상 동작 유지)
- timeout 5초 (각 페이지) → 1회/일 호출이라 차단 위험 낮음

**한 종목 다중 단계**:
- LEVEL_PRIORITY 로 가장 강한 단계 우선 선택 (관리 > 거래정지 > 위험 > 경고 > 주의)
- 단, severity 매핑은 ``ALERT_LEVEL_TO_SEVERITY`` (kind_alert_collector.py) 사용 — 본 모듈은 단계명만 결정.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


# ===== 모듈 상수 =====

NAVER_BASE = "https://finance.naver.com"
URLS: dict[str, str] = {
    "manage":  f"{NAVER_BASE}/sise/management.naver",
    "halt":    f"{NAVER_BASE}/sise/trading_halt.naver",
    "caution": f"{NAVER_BASE}/sise/investment_alert.naver?type=caution",
    "warning": f"{NAVER_BASE}/sise/investment_alert.naver?type=warning",
    "risk":    f"{NAVER_BASE}/sise/investment_alert.naver?type=risk",
}

# 페이지 키 → 한글 단계명 (ALERT_LEVEL_TO_SEVERITY 와 호환)
KEY_TO_LEVEL: dict[str, str] = {
    "manage":  "관리종목",
    "halt":    "매매거래정지",
    "caution": "투자주의",
    "warning": "투자경고",
    "risk":    "투자위험",
}

# 한 종목이 여러 페이지에 동시 등장 시 우선순위 (높을수록 강한 단계)
LEVEL_PRIORITY: dict[str, int] = {
    "관리종목":     5,
    "매매거래정지": 4,
    "투자위험":     3,
    "투자경고":     2,
    "투자주의":     1,
}

USER_AGENT = "Mozilla/5.0 (Linux x86_64) AppleWebKit/537.36"
TIMEOUT_SEC = 5

# 네이버 종목 추출 패턴 — `code=NNNNNN" class="tltle">종목명`
_CODE_PATTERN = re.compile(r'code=(\d{6})"\s+class="tltle"')


# ===== 헬퍼 =====


def _fetch_html(url: str, timeout: int = TIMEOUT_SEC) -> Optional[str]:
    """네이버 페이지 fetch + EUC-KR → UTF-8 디코딩.

    실패 시 ``None`` 반환 (logger.warning).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        logger.warning(f"[kind_naver] fetch 실패 ({url}): {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[kind_naver] fetch 예외 ({url}): {type(e).__name__}: {e}")
        return None

    # 네이버 finance 는 EUC-KR 인코딩
    try:
        return raw.decode("euc-kr", errors="replace")
    except Exception as e:
        logger.warning(f"[kind_naver] EUC-KR 디코딩 실패 ({url}): {e}")
        return None


def _extract_codes(html: str) -> list[str]:
    """페이지에서 6자리 종목코드 list 추출 (중복 제거).

    ``re.match(r'^\d{6}$')`` 정규식 일관 — universe v2 패턴.
    """
    if not html:
        return []
    raw_codes = _CODE_PATTERN.findall(html)
    seen: set[str] = set()
    out: list[str] = []
    for code in raw_codes:
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _pick_strongest_level(levels: list[str]) -> str:
    """동일 종목 다중 단계 시 LEVEL_PRIORITY 우선 단계 반환.

    빈 리스트면 빈 문자열 반환 (호출자에서 무시).
    """
    if not levels:
        return ""
    return max(levels, key=lambda lv: LEVEL_PRIORITY.get(lv, 0))


# ===== 메인 진입점 =====


def fetch_kind_alerts() -> dict[str, str]:
    """5 페이지 fetch → ``{ticker: 가장 강한 단계명}`` 반환.

    Returns:
        예: ``{"017040": "관리종목", "290120": "투자위험", "024840": "투자경고"}``

        모든 fetch 실패 시 빈 dict (graceful 폴백).
    """
    # 종목별 발견된 단계명 누적
    levels_by_ticker: dict[str, list[str]] = {}

    for key, url in URLS.items():
        level_name = KEY_TO_LEVEL.get(key)
        if not level_name:
            continue

        html = _fetch_html(url)
        if html is None:
            # 한 페이지 실패는 graceful — 나머지 페이지로 진행
            continue

        codes = _extract_codes(html)
        for code in codes:
            levels_by_ticker.setdefault(code, []).append(level_name)

        logger.info(f"[kind_naver] {key} 페이지 → {len(codes)}건 ({level_name})")

    # 종목별 가장 강한 단계 선택
    result: dict[str, str] = {}
    for ticker, levels in levels_by_ticker.items():
        chosen = _pick_strongest_level(levels)
        if chosen:
            result[ticker] = chosen

    logger.info(
        f"[kind_naver] 통합 결과 — 총 {len(result)}종목 "
        f"({sum(1 for lv in result.values() if lv == '관리종목')} 관리 / "
        f"{sum(1 for lv in result.values() if lv == '매매거래정지')} 정지 / "
        f"{sum(1 for lv in result.values() if lv == '투자위험')} 위험 / "
        f"{sum(1 for lv in result.values() if lv == '투자경고')} 경고 / "
        f"{sum(1 for lv in result.values() if lv == '투자주의')} 주의)"
    )
    return result


# ===== 디버그/단발 호출 =====

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    alerts = fetch_kind_alerts()
    print(f"\n총 {len(alerts)}종목")
    for level in ("관리종목", "매매거래정지", "투자위험", "투자경고", "투자주의"):
        matched = [t for t, lv in alerts.items() if lv == level]
        print(f"  {level}: {len(matched)}건 — 샘플 {matched[:5]}")
