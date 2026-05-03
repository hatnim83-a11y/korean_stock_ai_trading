"""closing_bet_system.infra.kis_client

종가베팅 시스템 전용 KIS API wrapper.

기존 ``modules/stock_screener/kis_api.py`` 의 ``KISApi`` 와
``modules/trading_engine/kis_order_api.py`` 의 ``KISOrderApi`` 를 그대로 재사용한다.

이 모듈은 다음을 담당한다:
1. **싱글톤 인스턴스 관리** — 토큰 1분 발급 제한 회피 (`web/dashboard_service.py` 패턴)
2. **종가베팅 전용 헬퍼** — 총 자산, 주문 가능 현금, 보유 종목 코드 set 등
3. **모의/실전 분기** — settings.yaml 의 ``kis.use_mock`` 사용

향후 단위(0-C, 1-x)에서 추가될 헬퍼:
- 동시호가 예상체결가 폴링 (KIS inquire_asking_price 활용)
- 막판 30분 VWAP 계산
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional

# 프로젝트 루트 path 추가
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from modules.stock_screener.kis_api import KISApi
from modules.trading_engine.kis_order_api import KISOrderApi

from closing_bet_system.storage.db import _load_settings


# ===== 싱글톤 인스턴스 =====
# web/dashboard_service.py:43-58 패턴 동일 적용. 토큰 1분 발급 제한 회피.

_kis_instance: Optional[KISApi] = None
_order_instance: Optional[KISOrderApi] = None
_instance_lock = threading.Lock()


def _get_use_mock() -> bool:
    """settings.yaml 에서 모의투자 여부 로드. 미정의 시 True (안전 폴백)."""
    settings = _load_settings()
    return settings.get("kis", {}).get("use_mock", True)


def get_kis_api() -> KISApi:
    """KISApi (시세 조회) 싱글톤. 동시 생성 방지를 위한 lock 적용."""
    global _kis_instance
    if _kis_instance is None:
        with _instance_lock:
            if _kis_instance is None:
                _kis_instance = KISApi(is_mock=_get_use_mock())
                logger.info("[종가베팅] KISApi 싱글톤 인스턴스 생성")
    return _kis_instance


def get_order_api() -> KISOrderApi:
    """KISOrderApi (주문) 싱글톤. KISApi와 토큰 공유 (`_shared_token`)."""
    global _order_instance
    if _order_instance is None:
        with _instance_lock:
            if _order_instance is None:
                _order_instance = KISOrderApi(is_mock=_get_use_mock())
                logger.info("[종가베팅] KISOrderApi 싱글톤 인스턴스 생성")
    return _order_instance


# ===== 종가베팅 전용 헬퍼 =====


def get_total_account_value() -> int:
    """총 평가금액 (주식 평가액 + 예수금) 조회.

    fund_guard 가 종가베팅 자금 한도 (``total_value × capital_ratio``) 산정에 사용한다.
    실패 시 0 반환 (보수적: fund_guard 가 자동으로 차단).

    KIS ``get_balance()`` 반환 dict 의존 키 (``modules/trading_engine/kis_order_api.py:709`` 참조):
    - ``total_value``: int (총 평가금액, 원)
    - ``cash``: int (예수금)
    - ``positions``: list[dict] (보유 종목 — get_held_stock_codes 에서 사용)
    """
    order_api = get_order_api()
    try:
        balance = order_api.get_balance()
        total = balance.get("total_value", 0)
        if not total or total <= 0:
            logger.warning("[종가베팅] 총 평가금액 조회 실패 또는 0원")
            return 0
        return int(total)
    except Exception as e:
        logger.error(f"[종가베팅] 총 평가금액 조회 예외: {e}")
        return 0


def get_orderable_cash() -> int:
    """주문 가능 현금 조회. 실패 시 0."""
    order_api = get_order_api()
    try:
        return order_api.get_orderable_cash()
    except Exception as e:
        logger.error(f"[종가베팅] 주문 가능 현금 조회 예외: {e}")
        return 0


def get_held_stock_codes() -> set[str]:
    """현재 KIS 계좌에서 보유 중인 종목코드 집합. 실패 시 빈 set.

    스윙 + 종가베팅이 같은 계좌이므로, 이 set은 "전체 보유 종목" 이다.
    종가베팅에서 이미 보유 중인 종목 재진입 차단 / 평가액 합산 등에 사용.
    """
    order_api = get_order_api()
    try:
        balance = order_api.get_balance()
        positions = balance.get("positions", []) or []
        codes = {p.get("stock_code") for p in positions if p.get("stock_code")}
        return codes
    except Exception as e:
        logger.error(f"[종가베팅] 보유 종목 조회 예외: {e}")
        return set()


def reset_singletons() -> None:
    """테스트용: 싱글톤 인스턴스 초기화."""
    global _kis_instance, _order_instance
    with _instance_lock:
        _kis_instance = None
        _order_instance = None
