"""label_provider KIS 재시도 로직 단위 테스트.

5/8 셀트리온(068270) 라벨링 누락 사례 재발 방지 검증:
- KIS 호출 1회 실패 → 재시도 → 정상 복구
- KIS 호출 N회 모두 실패 → None 반환 (회귀)
- 빈 응답(rows=[])은 정상 응답으로 처리, 재시도 X
- 재시도 호출 횟수 정확성

실행:
    venv/bin/python scripts/test_label_provider_retry.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors import label_provider


# ===== 픽스처 =====

VALID_ROWS = [
    {"date": "20260508", "open": 200000, "high": 200500, "low": 195000, "close": 198000},
    {"date": "20260507", "open": 197000, "high": 198000, "low": 196000, "close": 198500},
]


def _patched_sleep():
    """time.sleep 패치 (테스트 속도 확보)."""
    return patch("closing_bet_system.collectors.label_provider.time.sleep", return_value=None)


def _patched_kis_factory(side_effect):
    """get_kis_api().get_daily_price를 mock하는 helper.

    side_effect: list (각 호출별 결과) 또는 단일 값/예외.
    """
    mock_kis = MagicMock()
    mock_kis.get_daily_price = MagicMock(side_effect=side_effect)
    return patch(
        "closing_bet_system.infra.kis_client.get_kis_api",
        return_value=mock_kis,
    ), mock_kis


# ===== 테스트 =====


def test_RT_1_success_first_attempt():
    """RT-1: 첫 시도 성공 → 1회 호출, 결과 정상 반환."""
    ctx, mock_kis = _patched_kis_factory(side_effect=[VALID_ROWS])
    with _patched_sleep(), ctx:
        rows = label_provider._fetch_daily_price_with_retry("068270")
    assert rows == VALID_ROWS, "첫 시도 성공 시 그대로 반환해야 함"
    assert mock_kis.get_daily_price.call_count == 1, "재시도 없이 1회만 호출"
    print("✅ RT-1 첫 시도 성공")


def test_RT_2_recover_on_second_attempt():
    """RT-2: 첫 시도 예외 → 두번째 시도 성공 (셀트리온 케이스 재현)."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("500 Internal Server Error"), VALID_ROWS]
    )
    with _patched_sleep(), ctx:
        rows = label_provider._fetch_daily_price_with_retry("068270")
    assert rows == VALID_ROWS, "두번째 시도 성공 시 결과 반환"
    assert mock_kis.get_daily_price.call_count == 2, "2회 호출"
    print("✅ RT-2 재시도 후 성공 (셀트리온 케이스)")


def test_RT_3_recover_on_third_attempt():
    """RT-3: 첫·두번째 시도 모두 예외 → 세번째 시도 성공."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e1"), Exception("e2"), VALID_ROWS]
    )
    with _patched_sleep(), ctx:
        rows = label_provider._fetch_daily_price_with_retry("068270")
    assert rows == VALID_ROWS
    assert mock_kis.get_daily_price.call_count == 3, "3회 호출"
    print("✅ RT-3 마지막 시도 성공")


def test_RT_4_all_attempts_fail():
    """RT-4: 모든 재시도 실패 → None 반환 (회귀, 기존 동작 유지)."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e1"), Exception("e2"), Exception("e3")]
    )
    with _patched_sleep(), ctx:
        rows = label_provider._fetch_daily_price_with_retry("068270")
    assert rows is None, "모든 재시도 실패 시 None"
    assert mock_kis.get_daily_price.call_count == label_provider._KIS_FETCH_MAX_ATTEMPTS
    print("✅ RT-4 전체 실패 → None")


def test_RT_5_empty_response_not_retried():
    """RT-5: 빈 응답(rows=[])은 정상 응답으로 처리, 재시도 X.

    rate limit / 비영업일 / 미상장 등은 빈 응답이 정상이라 재시도 의미 없음.
    """
    ctx, mock_kis = _patched_kis_factory(side_effect=[[]])
    with _patched_sleep(), ctx:
        rows = label_provider._fetch_daily_price_with_retry("068270")
    assert rows == [], "빈 응답 그대로 반환"
    assert mock_kis.get_daily_price.call_count == 1, "빈 응답은 재시도 X"
    print("✅ RT-5 빈 응답 재시도 안 함")


def test_RT_6_get_label_integration_recover():
    """RT-6: get_label 진입점에서 재시도 → 정상 라벨 반환."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("500"), VALID_ROWS]
    )
    with _patched_sleep(), ctx:
        result = label_provider.get_label("068270")
    assert result is not None, "재시도 후 라벨 dict 반환"
    assert "label_gap_up" in result
    assert "next_open_pct" in result
    assert mock_kis.get_daily_price.call_count == 2
    print("✅ RT-6 get_label 통합 재시도 검증")


def test_RT_7_get_label_all_fail():
    """RT-7: get_label 진입점, 모든 재시도 실패 → None."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e")] * label_provider._KIS_FETCH_MAX_ATTEMPTS
    )
    with _patched_sleep(), ctx:
        result = label_provider.get_label("068270")
    assert result is None, "전체 실패 시 get_label도 None"
    print("✅ RT-7 get_label 전체 실패 → None")


def test_RT_8_invalid_ticker_no_kis_call():
    """RT-8: 잘못된 ticker → KIS 호출 없이 즉시 None (회귀)."""
    ctx, mock_kis = _patched_kis_factory(side_effect=[VALID_ROWS])
    with _patched_sleep(), ctx:
        result = label_provider.get_label("ABC")  # 6자리 숫자 아님
    assert result is None, "잘못된 ticker 즉시 None"
    assert mock_kis.get_daily_price.call_count == 0, "KIS 호출 발생 안 함"
    print("✅ RT-8 잘못된 ticker 회귀")


def test_RT_9_constants():
    """RT-9: 모듈 상수 값 검증 (변경 시 회귀 차단)."""
    assert label_provider._KIS_FETCH_MAX_ATTEMPTS == 3, "재시도 횟수 = 3"
    assert label_provider._KIS_FETCH_BACKOFF_SEC == 5.0, "백오프 = 5초"
    print("✅ RT-9 모듈 상수")


def test_RT_10_backoff_called_correct_times():
    """RT-10: time.sleep 호출 횟수 = (max_attempts - 1) (마지막 시도 후 sleep X)."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e1"), Exception("e2"), Exception("e3")]
    )
    with patch("closing_bet_system.collectors.label_provider.time.sleep") as mock_sleep, ctx:
        label_provider._fetch_daily_price_with_retry("068270")
    # 1차 실패→sleep, 2차 실패→sleep, 3차 실패→sleep X (즉시 종료)
    assert mock_sleep.call_count == label_provider._KIS_FETCH_MAX_ATTEMPTS - 1
    mock_sleep.assert_called_with(label_provider._KIS_FETCH_BACKOFF_SEC)
    print("✅ RT-10 backoff 호출 횟수 정확성")


def test_RT_11_partial_data_after_retry():
    """RT-11: 재시도 후 1행만 반환 (< 2행) → None (회귀)."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e"), [VALID_ROWS[0]]]  # 재시도 후 1행만
    )
    with _patched_sleep(), ctx:
        result = label_provider.get_label("068270")
    assert result is None, "데이터 부족 시 None"
    print("✅ RT-11 데이터 부족 회귀")


def test_RT_12_retry_logs_warning_each_attempt():
    """RT-12: 재시도 시 logger.warning 호출 (관찰 가능성 검증)."""
    ctx, mock_kis = _patched_kis_factory(
        side_effect=[Exception("e1"), Exception("e2"), VALID_ROWS]
    )
    with _patched_sleep(), patch.object(label_provider.logger, "warning") as mock_warn, ctx:
        label_provider._fetch_daily_price_with_retry("068270")
    # 1차/2차 실패 시 warning, 3차는 성공이라 warning 없음
    assert mock_warn.call_count >= 2, "실패 시 warning 호출 (최소 2건)"
    print("✅ RT-12 warning 로깅 검증")


# ===== Runner =====

def main():
    tests = [
        test_RT_1_success_first_attempt,
        test_RT_2_recover_on_second_attempt,
        test_RT_3_recover_on_third_attempt,
        test_RT_4_all_attempts_fail,
        test_RT_5_empty_response_not_retried,
        test_RT_6_get_label_integration_recover,
        test_RT_7_get_label_all_fail,
        test_RT_8_invalid_ticker_no_kis_call,
        test_RT_9_constants,
        test_RT_10_backoff_called_correct_times,
        test_RT_11_partial_data_after_retry,
        test_RT_12_retry_logs_warning_each_attempt,
    ]
    print(f"\n=== label_provider 재시도 테스트 — {len(tests)}건 실행 ===\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"❌ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"💥 {t.__name__} 예외: {type(e).__name__}: {e}")
    print(f"\n=== 결과: {len(tests) - failed}/{len(tests)} PASS ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
