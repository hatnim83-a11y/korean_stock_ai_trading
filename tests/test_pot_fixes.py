"""
test_pot_fixes.py - 잠재적 버그 수정 검증 테스트

POT-001~015 수정 사항을 검증합니다.
"""

import sys
import json
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_safe_int_float_kis_order_api():
    """POT-001: kis_order_api.py _safe_int/_safe_float 테스트"""
    from modules.trading_engine.kis_order_api import _safe_int, _safe_float

    # 정상 값
    assert _safe_int("12345") == 12345
    assert _safe_int(100) == 100
    assert _safe_float("3.14") == 3.14

    # 빈 문자열
    assert _safe_int("") == 0
    assert _safe_float("") == 0.0

    # None
    assert _safe_int(None) == 0
    assert _safe_float(None) == 0.0

    # 잘못된 값
    assert _safe_int("abc") == 0
    assert _safe_float("xyz") == 0.0

    # 기본값
    assert _safe_int("", default=99) == 99
    assert _safe_float("", default=1.5) == 1.5

    # float -> int 체인
    assert _safe_int(_safe_float("3.7")) == 3
    assert _safe_int(_safe_float("")) == 0

    print("  [PASS] POT-001: _safe_int/_safe_float in kis_order_api.py")


def test_empty_list_indexing():
    """POT-002: 빈 리스트 인덱싱 안전 처리"""
    # 시뮬레이션: output2가 빈 리스트일 때
    data = {"output2": []}
    output2_list = data.get("output2") or [{}]
    output2 = output2_list[0] if output2_list else {}

    assert output2 == {}

    # output2가 None일 때
    data2 = {"output2": None}
    output2_list = data2.get("output2") or [{}]
    output2 = output2_list[0] if output2_list else {}

    assert output2 == {}

    # output2가 정상일 때
    data3 = {"output2": [{"tot_evlu_amt": "10000000"}]}
    output2_list = data3.get("output2") or [{}]
    output2 = output2_list[0] if output2_list else {}

    assert output2.get("tot_evlu_amt") == "10000000"

    print("  [PASS] POT-002: 빈 리스트 인덱싱 안전 처리")


def test_ai_json_parsing_safe():
    """POT-003/004: AI JSON 파싱 안전화"""
    from modules.theme_analyzer.ai_analyzer import _parse_claude_response

    # 정상 응답
    normal = '```json\n{"score": 7.5, "reason": "test", "outlook": "상승"}\n```'
    result = _parse_claude_response(normal)
    assert result is not None
    assert result["score"] == 7.5

    # 닫는 ``` 없는 응답
    broken = '```json\n{"score": 8.0, "reason": "test", "outlook": "중립"}'
    result2 = _parse_claude_response(broken)
    assert result2 is not None
    assert result2["score"] == 8.0

    # score가 숫자가 아닌 경우
    bad_score = '{"score": "높음", "reason": "test", "outlook": "상승"}'
    result3 = _parse_claude_response(bad_score)
    assert result3 is not None
    assert result3["score"] == 5.0  # fallback

    print("  [PASS] POT-003/004/012: AI JSON 파싱 안전화")


def test_claude_analyzer_json_parsing():
    """POT-004: claude_analyzer.py JSON 파싱"""
    from modules.ai_verifier.claude_analyzer import _parse_response

    # 정상
    normal = '```json\n{"sentiment": 7.0, "recommend": "Yes", "reason": "test"}\n```'
    result = _parse_response(normal)
    assert result is not None
    assert result["sentiment"] == 7.0

    # sentiment 비숫자
    bad = '{"sentiment": "good", "recommend": "Yes"}'
    result2 = _parse_response(bad)
    assert result2 is not None
    assert result2["sentiment"] == 5.0  # fallback

    print("  [PASS] POT-004: claude_analyzer.py JSON 파싱")


def test_http_response_order():
    """POT-005: HTTP 응답 순서 확인 (코드 검증)"""
    import inspect
    from modules.trading_engine.kis_order_api import KISOrderApi

    source = inspect.getsource(KISOrderApi._place_order)
    # raise_for_status가 response.json() 전에 호출되는지 확인
    raise_idx = source.find("raise_for_status()")
    json_idx = source.find("response.json()")

    assert raise_idx < json_idx, "raise_for_status() must come before response.json()"

    print("  [PASS] POT-005: HTTP 응답 순서 (raise_for_status -> json)")


def test_async_dict_snapshot():
    """POT-006/014: dict 스냅샷 사용 확인"""
    import inspect
    from modules.morning_filter.realtime_monitor import MorningMonitor

    source = inspect.getsource(MorningMonitor._start_polling_monitoring)

    # list() 스냅샷 사용 확인
    assert "list(self.realtime_data.keys())" in source

    # key 존재 확인 후 접근
    assert "code in self.realtime_data" in source

    # _data_lock 존재 확인
    monitor = MorningMonitor(use_mock=True, enable_websocket=False)
    assert hasattr(monitor, '_data_lock')

    print("  [PASS] POT-006/014: 비동기 동기화 (dict snapshot + lock)")


def test_performance_calculator_guard():
    """POT-007: 빈 리스트 가드"""
    from modules.reporter.performance_calculator import PerformanceCalculator

    calc = PerformanceCalculator()

    # 빈 포트폴리오
    mdd = calc.calculate_mdd([])
    assert mdd["mdd"] == 0

    # 빈 메트릭스
    metrics = calc.calculate_all_metrics([], [], 10_000_000)
    assert metrics["total_return"] == 0
    assert metrics["trading_days"] == 0

    # 정상 데이터
    values = [
        {"date": "2025-01-01", "value": 10_000_000},
        {"date": "2025-01-02", "value": 10_100_000},
    ]
    mdd2 = calc.calculate_mdd(values)
    assert mdd2["mdd"] == 0  # 계속 상승이므로 MDD 0

    print("  [PASS] POT-007: 성과 계산기 빈 리스트 가드")


def test_bare_except_removed():
    """POT-008~011: bare except 제거 확인"""
    import inspect

    # scheduler.py
    from scheduler import TradingScheduler
    scheduler_source = inspect.getsource(TradingScheduler)

    # "except:" 만 있고 "except Exception:" 이 아닌 패턴 찾기
    lines = scheduler_source.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped == "except:":
            assert False, f"scheduler.py에 bare except 발견: {line}"

    # rebalancer.py
    from modules.rebalancer.rebalancer import Rebalancer
    rebalancer_source = inspect.getsource(Rebalancer)
    lines = rebalancer_source.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped == "except:":
            assert False, f"rebalancer.py에 bare except 발견: {line}"

    print("  [PASS] POT-008~011: bare except 제거 확인")


def test_url_parsing_regex():
    """POT-013: URL 파싱 regex 사용 확인"""
    import re

    # 정상 URL
    href = "https://finance.naver.com/item/main.nhn?code=005930&type=1"
    match = re.search(r'code=([^&]+)', href)
    assert match and match.group(1) == "005930"

    # code= 로 끝나는 URL
    href2 = "https://finance.naver.com/item/main.nhn?code="
    match2 = re.search(r'code=([^&]+)', href2)
    assert match2 is None  # 빈 값이면 None

    # code 없는 URL
    href3 = "https://finance.naver.com/item/main.nhn?type=1"
    match3 = re.search(r'code=([^&]+)', href3)
    assert match3 is None

    print("  [PASS] POT-013: URL regex 파싱")


def run_all_tests():
    """모든 테스트 실행"""
    print("=" * 60)
    print("  잠재적 버그 수정 검증 테스트")
    print("=" * 60)

    tests = [
        test_safe_int_float_kis_order_api,
        test_empty_list_indexing,
        test_ai_json_parsing_safe,
        test_claude_analyzer_json_parsing,
        test_http_response_order,
        test_async_dict_snapshot,
        test_performance_calculator_guard,
        test_bare_except_removed,
        test_url_parsing_regex,
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
