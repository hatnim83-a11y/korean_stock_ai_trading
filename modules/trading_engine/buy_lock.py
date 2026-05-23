"""
buy_lock.py - 매수 동시성 잠금 레지스트리 (v17 분할 진입 + 불타기)

2차 진입(불타기) 발주 시 동일 종목 race 방어. SellLock과 동일 패턴이지만
release 정책이 다르다:
- SellLock: release-after-sell 금지 → 15:30 clear_all() 일괄 해제 (race window 봉쇄)
- BuyLock: 발주 완료 후 try/finally로 즉시 release (1차 해제)
           + 15:30 clear_all() 2차 안전망 (누수 차단)

설계 이유:
- 2차 매수는 1회성 발주라 누수 위험 없음 (재시도 후 종료)
- 매수 직후 release하지 않으면 다음 사이클에서 +5% 재도달 시 평가 자체가 차단됨
- 단 발주 중 예외 발생 시 release 누수 가능성 → try/finally 필수
- 15:30 clear_all 2차 안전망으로 이중 보장

monitor 루프 우선순위 가드 (Coder 리뷰 P3):
- 같은 사이클에 SellLock 활성 종목은 BuyLock acquire 시도 자체 skip
- 즉 분할익절 발화 사이클에서는 불타기 미발화 (다음 사이클 재평가)
"""

from __future__ import annotations

import threading
from typing import Optional

from config import now_kst


class BuyLockRegistry:
    """매수 종목 잠금 레지스트리 (스레드 안전).

    v17 2차 진입(불타기)에서만 사용. 1차 매수(09:25)는 단일 진입이라 잠금 불필요.
    """

    def __init__(self) -> None:
        self._locked: dict[str, dict] = {}
        self._lock = threading.Lock()

    def acquire(self, stock_code: str, owner: str) -> bool:
        """잠금 획득 시도.

        Args:
            stock_code: 6자리 종목코드
            owner: 잠금 소유자 (예: "pyramid_in", "first_buy") - 디버깅용

        Returns:
            True=획득 성공, False=이미 다른 owner가 보유 중 (호출자는 skip)
        """
        with self._lock:
            if stock_code in self._locked:
                return False
            self._locked[stock_code] = {
                "owner": owner,
                "acquired_at": now_kst().isoformat(),
            }
            return True

    def release(self, stock_code: str) -> None:
        """잠금 해제 (개별).

        매수 발주 완료 후 try/finally에서 호출 (체결 성공/실패/취소 모두 해제).
        """
        with self._lock:
            self._locked.pop(stock_code, None)

    def is_locked(self, stock_code: str) -> bool:
        """잠금 여부 확인 (논블로킹 read)."""
        with self._lock:
            return stock_code in self._locked

    def owner(self, stock_code: str) -> Optional[str]:
        """잠금 소유자 (없으면 None)."""
        with self._lock:
            entry = self._locked.get(stock_code)
            return entry["owner"] if entry else None

    def clear_all(self) -> int:
        """모든 잠금 해제 (15:30 monitoring_stop에서 호출, 2차 안전망). 해제된 건수 반환."""
        with self._lock:
            count = len(self._locked)
            self._locked.clear()
            return count

    def snapshot(self) -> dict[str, dict]:
        """현재 잠금 상태 스냅샷 (디버그/로그). deep copy로 반환."""
        with self._lock:
            return {code: dict(entry) for code, entry in self._locked.items()}


# 모듈 싱글톤
buy_lock = BuyLockRegistry()
