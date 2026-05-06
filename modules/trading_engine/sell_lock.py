"""
sell_lock.py - 매도 동시성 잠금 레지스트리

09:00 조기 모니터링 도입 후 분할익절/손절/트레일링 모니터와
midweek/hold_period 매도 잡이 동일 종목을 동시 매도하려는 race를 봉쇄한다.

설계 원칙:
- acquire 실패 = 다른 매도 경로가 이미 처리 중 → 호출자는 해당 종목 skip
- release는 매도 함수 종료 시점에 호출하지 않음. 15:30 monitoring_stop에서 clear_all() 일괄 해제
  (release-after-sell 방식은 09:30 race window를 다시 만든다)
- 같은 거래일 내 동일 종목에 두 매도 잡이 발화하지 않으므로 누수 위험 없음
"""

from __future__ import annotations

import threading
from typing import Optional

from config import now_kst


class SellLockRegistry:
    """매도 종목 잠금 레지스트리 (스레드 안전)"""

    def __init__(self) -> None:
        self._locked: dict[str, dict] = {}
        self._lock = threading.Lock()

    def acquire(self, stock_code: str, owner: str) -> bool:
        """잠금 획득 시도. True=획득, False=이미 다른 owner가 보유 중."""
        with self._lock:
            if stock_code in self._locked:
                return False
            self._locked[stock_code] = {
                "owner": owner,
                "acquired_at": now_kst().isoformat(),
            }
            return True

    def release(self, stock_code: str) -> None:
        """잠금 해제 (개별)."""
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
        """모든 잠금 해제 (15:30 monitoring_stop에서 호출). 해제된 건수 반환."""
        with self._lock:
            count = len(self._locked)
            self._locked.clear()
            return count

    def snapshot(self) -> dict[str, dict]:
        """현재 잠금 상태 스냅샷 (디버그/로그). 외부 수정이 원본에 영향 주지 않도록 deep copy."""
        with self._lock:
            return {code: dict(entry) for code, entry in self._locked.items()}


# 모듈 싱글톤
sell_lock = SellLockRegistry()
