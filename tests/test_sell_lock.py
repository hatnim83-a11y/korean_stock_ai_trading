"""
test_sell_lock.py - SellLockRegistry 단위 테스트

파일 직접 실행: python tests/test_sell_lock.py
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.sell_lock import SellLockRegistry, sell_lock


def _fresh() -> SellLockRegistry:
    """각 테스트가 독립적인 인스턴스를 사용하도록 헬퍼."""
    return SellLockRegistry()


def test_acquire_then_second_fails():
    """단일 acquire 후 두 번째 acquire는 False."""
    reg = _fresh()
    assert reg.acquire("005930", "monitor_partial") is True
    assert reg.acquire("005930", "hold_period") is False
    assert reg.is_locked("005930") is True
    assert reg.owner("005930") == "monitor_partial"
    print("  [PASS] acquire_then_second_fails")


def test_release_allows_reacquire():
    """release 후 재acquire OK."""
    reg = _fresh()
    assert reg.acquire("005930", "monitor_partial") is True
    reg.release("005930")
    assert reg.is_locked("005930") is False
    assert reg.acquire("005930", "hold_period") is True
    assert reg.owner("005930") == "hold_period"
    print("  [PASS] release_allows_reacquire")


def test_concurrent_acquire_only_one_wins():
    """100개 스레드가 동시 acquire → 정확히 1개만 True."""
    reg = _fresh()
    results: list[bool] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(100)

    def worker():
        barrier.wait()
        ok = reg.acquire("005930", "thread")
        with results_lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, f"기대 1, 실제 {sum(results)}"
    assert reg.is_locked("005930") is True
    print("  [PASS] concurrent_acquire_only_one_wins")


def test_clear_all_releases_everything():
    """clear_all() 후 모든 lock 해제 + 반환값은 해제 건수."""
    reg = _fresh()
    reg.acquire("005930", "monitor_partial")
    reg.acquire("000660", "monitor_partial")
    reg.acquire("035720", "hold_period")
    cleared = reg.clear_all()
    assert cleared == 3
    assert reg.is_locked("005930") is False
    assert reg.is_locked("000660") is False
    assert reg.is_locked("035720") is False
    assert reg.snapshot() == {}
    print("  [PASS] clear_all_releases_everything")


def test_snapshot_returns_copy():
    """snapshot()은 내부 딕셔너리 사본 반환 (외부 수정이 영향 주지 않음)."""
    reg = _fresh()
    reg.acquire("005930", "monitor_partial")
    snap = reg.snapshot()
    assert "005930" in snap
    assert snap["005930"]["owner"] == "monitor_partial"
    assert "acquired_at" in snap["005930"]
    snap["005930"]["owner"] = "hacked"
    # 원본은 영향 없음
    assert reg.owner("005930") == "monitor_partial"
    print("  [PASS] snapshot_returns_copy")


def test_singleton_is_shared():
    """모듈 싱글톤은 하나의 인스턴스를 공유."""
    from modules.trading_engine.sell_lock import sell_lock as sl2
    assert sell_lock is sl2
    sell_lock.clear_all()  # 다른 테스트 영향 차단
    assert sell_lock.acquire("999999", "smoke") is True
    sell_lock.clear_all()
    print("  [PASS] singleton_is_shared")


def run_all_tests() -> bool:
    print("=" * 60)
    print("SellLockRegistry 단위 테스트")
    print("=" * 60)

    tests = [
        test_acquire_then_second_fails,
        test_release_allows_reacquire,
        test_concurrent_acquire_only_one_wins,
        test_clear_all_releases_everything,
        test_snapshot_returns_copy,
        test_singleton_is_shared,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test.__name__}: {e}")
            failed += 1

    print("=" * 60)
    print(f"  결과: {passed} passed, {failed} failed / {len(tests)} total")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
