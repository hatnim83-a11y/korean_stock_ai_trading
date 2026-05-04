"""단위 2-2b-1 검증: KindNaverProvider.

시나리오:
    KN-1:  5 페이지 모두 정상 → 통합 dict 반환
    KN-2:  1개 페이지 실패 (URLError) → 나머지로 진행 (graceful)
    KN-3:  모두 실패 → 빈 dict 폴백
    KN-4:  EUC-KR 디코딩 정상 (한글 종목명 패턴)
    KN-5:  한 종목이 관리 + 투자위험 동시 → 관리종목 우선 (LEVEL_PRIORITY)
    KN-6:  6자리 정규식 검증 (영문/5자리/7자리 제외)
    KN-7:  5단계 매핑 정확성 (관리/거래정지/위험/경고/주의)
    KN-8:  중복 종목코드 자연 제거 (extract_codes seen 처리)
    KN-9:  빈 HTML → 빈 dict (extract_codes 안전성)
    KN-10: timeout 적용 (urllib.request.urlopen timeout 인자)
    KN-11: User-Agent 헤더 설정 (Mozilla/5.0)
    KN-12: 한 페이지 timeout/HTTPError → 다음 페이지 정상 진행
    KN-13: ALERT_LEVEL_TO_SEVERITY 에 "관리종목": 3 매핑 추가 검증 (PRD 4-1)
    KN-14: _pick_strongest_level — 빈 list → 빈 문자열
    KN-15: KindAlertCollector(provider=fetch_kind_alerts) 통합 호출

실행:
    venv/bin/python scripts/test_closing_bet_unit_2_2b_1.py
"""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors import kind_naver_provider as knp
from closing_bet_system.collectors.kind_alert_collector import (
    KindAlertCollector,
    ALERT_LEVEL_TO_SEVERITY,
)


# ===== 모킹 헬퍼 =====


def _make_html(codes: list[str]) -> bytes:
    """6자리 코드 list → 네이버 HTML 패턴 (EUC-KR 인코딩) bytes."""
    rows = "\n".join(
        f'<a href="/item/main.naver?code={c}" class="tltle">종목명{c}</a>'
        for c in codes
    )
    html = f'<html><body><table>{rows}</table></body></html>'
    return html.encode("euc-kr")


def _mock_urlopen_factory(page_data: dict[str, bytes], errors: dict[str, Exception] = None):
    """page_data: {key: bytes_html}, errors: {key: Exception} — 키별로 다르게 응답."""
    errors = errors or {}

    def _opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        # URL 에서 page key 추출
        key = None
        if "management.naver" in url:
            key = "manage"
        elif "trading_halt.naver" in url:
            key = "halt"
        elif "type=caution" in url:
            key = "caution"
        elif "type=warning" in url:
            key = "warning"
        elif "type=risk" in url:
            key = "risk"

        if key in errors:
            raise errors[key]
        if key in page_data:
            mock_resp = MagicMock()
            mock_resp.read.return_value = page_data[key]
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = False
            return mock_resp
        # default: 빈 HTML
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        return mock_resp

    return _opener


# ===== 테스트 케이스 =====


def test_KN_1_5_페이지_모두_정상():
    page_data = {
        "manage":  _make_html(["000001", "000002"]),
        "halt":    _make_html(["100001", "100002"]),
        "caution": _make_html(["200001"]),
        "warning": _make_html(["300001"]),
        "risk":    _make_html(["400001"]),
    }
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data)):
        result = knp.fetch_kind_alerts()
    assert len(result) == 7, f"기대 7건, 실제 {len(result)}"
    assert result["000001"] == "관리종목"
    assert result["100001"] == "매매거래정지"
    assert result["200001"] == "투자주의"
    assert result["300001"] == "투자경고"
    assert result["400001"] == "투자위험"
    print("[PASS] KN-1: 5 페이지 모두 정상 → 통합 7종목")


def test_KN_2_1_페이지_실패():
    page_data = {
        "manage":  _make_html(["000001"]),
        # halt 실패
        "caution": _make_html(["200001"]),
        "warning": _make_html(["300001"]),
        "risk":    _make_html(["400001"]),
    }
    errors = {"halt": urllib.error.URLError("연결 거부")}
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data, errors)):
        result = knp.fetch_kind_alerts()
    assert len(result) == 4, f"기대 4건 (halt 제외), 실제 {len(result)}"
    assert "000001" in result
    assert "100001" not in result  # halt 실패로 빠짐
    print("[PASS] KN-2: 1 페이지 실패 → 나머지 4 페이지로 진행 (graceful)")


def test_KN_3_모두_실패():
    errors = {key: urllib.error.URLError("차단") for key in knp.URLS}
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory({}, errors)):
        result = knp.fetch_kind_alerts()
    assert result == {}, f"기대 빈 dict, 실제 {result}"
    print("[PASS] KN-3: 모든 페이지 실패 → 빈 dict 폴백")


def test_KN_4_EUC_KR_디코딩():
    # 한글 종목명 포함 (EUC-KR 인코딩)
    html = '<a href="/item/main.naver?code=005930" class="tltle">삼성전자</a>'
    page_data = {"manage": html.encode("euc-kr")}
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data)):
        result = knp.fetch_kind_alerts()
    assert "005930" in result, f"한글 인코딩 정상 추출 실패: {result}"
    assert result["005930"] == "관리종목"
    print("[PASS] KN-4: EUC-KR 디코딩 정상 (한글 종목명)")


def test_KN_5_LEVEL_PRIORITY_관리_우선():
    # 동일 종목 005930 이 manage + caution + warning 동시 등장
    page_data = {
        "manage":  _make_html(["005930"]),
        "caution": _make_html(["005930"]),
        "warning": _make_html(["005930"]),
    }
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data)):
        result = knp.fetch_kind_alerts()
    assert result["005930"] == "관리종목", f"관리종목 우선 실패: {result['005930']}"
    print("[PASS] KN-5: 다중 단계 → 관리종목 우선 (LEVEL_PRIORITY)")


def test_KN_6_종목코드_정규식_검증():
    # 잘못된 패턴 (5자리/7자리/영문) — 정규식 ^\d{6}$ 가 추출 단계에서 차단
    # _CODE_PATTERN 은 `code=(\d{6})"` 형태라 5자리/7자리 자체가 매칭 안 됨
    html = """
    <a href="?code=12345" class="tltle">5자리</a>
    <a href="?code=1234567" class="tltle">7자리</a>
    <a href="?code=ABCDEF" class="tltle">영문</a>
    <a href="?code=000001" class="tltle">정상</a>
    """
    codes = knp._extract_codes(html)
    assert codes == ["000001"], f"6자리만 통과 실패: {codes}"
    print("[PASS] KN-6: 6자리 정규식 검증 (영문/5자리/7자리 제외)")


def test_KN_7_5단계_매핑_정확성():
    expected = {"manage": "관리종목", "halt": "매매거래정지",
                "caution": "투자주의", "warning": "투자경고", "risk": "투자위험"}
    for key, level in expected.items():
        assert knp.KEY_TO_LEVEL[key] == level, f"매핑 오류: {key} → {knp.KEY_TO_LEVEL[key]}"
    # LEVEL_PRIORITY 5단계 정렬: 관리(5) > 정지(4) > 위험(3) > 경고(2) > 주의(1)
    assert knp.LEVEL_PRIORITY["관리종목"] == 5
    assert knp.LEVEL_PRIORITY["매매거래정지"] == 4
    assert knp.LEVEL_PRIORITY["투자위험"] == 3
    assert knp.LEVEL_PRIORITY["투자경고"] == 2
    assert knp.LEVEL_PRIORITY["투자주의"] == 1
    print("[PASS] KN-7: 5단계 매핑 + LEVEL_PRIORITY 정확")


def test_KN_8_중복_종목코드_제거():
    # 같은 페이지에 동일 종목코드 2회 등장 (페이지네이션/중복 시뮬)
    html = """
    <a href="?code=005930" class="tltle">삼성전자</a>
    <a href="?code=005930" class="tltle">삼성전자</a>
    <a href="?code=000660" class="tltle">SK하이닉스</a>
    """
    codes = knp._extract_codes(html)
    assert codes == ["005930", "000660"], f"중복 제거 실패: {codes}"
    print("[PASS] KN-8: 동일 페이지 중복 종목코드 자연 제거")


def test_KN_9_빈_HTML():
    assert knp._extract_codes("") == []
    assert knp._extract_codes(None) == []
    print("[PASS] KN-9: 빈/None HTML → 빈 list")


def test_KN_10_timeout_적용():
    captured = {}
    def _capture(req, timeout=None):
        captured["timeout"] = timeout
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        return mock_resp

    with patch.object(knp.urllib.request, "urlopen", _capture):
        knp._fetch_html("https://example.com", timeout=knp.TIMEOUT_SEC)
    assert captured["timeout"] == knp.TIMEOUT_SEC
    print(f"[PASS] KN-10: timeout 적용 ({knp.TIMEOUT_SEC}초)")


def test_KN_11_User_Agent_헤더():
    captured = {}
    def _capture(req, timeout=None):
        captured["headers"] = dict(req.headers)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        return mock_resp

    with patch.object(knp.urllib.request, "urlopen", _capture):
        knp._fetch_html("https://example.com")
    # urllib 은 헤더 키를 capitalize 함 → "User-agent"
    ua = captured["headers"].get("User-agent") or captured["headers"].get("User-Agent")
    assert ua and "Mozilla" in ua, f"User-Agent 미설정: {captured['headers']}"
    print("[PASS] KN-11: User-Agent 헤더 설정 (Mozilla)")


def test_KN_12_HTTPError_graceful():
    # 한 페이지 HTTPError (403 등) → 나머지로 진행
    page_data = {
        "manage": _make_html(["000001"]),
        # halt: HTTPError
        "caution": _make_html(["200001"]),
    }
    errors = {"halt": urllib.error.HTTPError(
        url="x", code=403, msg="Forbidden", hdrs=None, fp=None
    )}
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data, errors)):
        result = knp.fetch_kind_alerts()
    assert "000001" in result, "manage 페이지 정상 추출 실패"
    assert len([t for t in result if t == "100001"]) == 0, "halt 페이지 결과 침투 의심"
    print("[PASS] KN-12: HTTPError 발생 → 나머지 페이지 graceful")


def test_KN_13_관리종목_severity_매핑():
    # PRD 4-1 정합 — 관리종목 severity = 3
    assert ALERT_LEVEL_TO_SEVERITY.get("관리종목") == 3, \
        f'"관리종목" severity 매핑 누락: {ALERT_LEVEL_TO_SEVERITY.get("관리종목")}'
    assert ALERT_LEVEL_TO_SEVERITY.get("관리") == 3, "관리 alias 누락"
    print("[PASS] KN-13: ALERT_LEVEL_TO_SEVERITY 에 관리종목=3 매핑 (PRD 4-1)")


def test_KN_14_pick_strongest_level_빈_list():
    assert knp._pick_strongest_level([]) == ""
    assert knp._pick_strongest_level(["관리종목"]) == "관리종목"
    assert knp._pick_strongest_level(["투자주의", "관리종목", "투자경고"]) == "관리종목"
    assert knp._pick_strongest_level(["투자주의", "투자경고"]) == "투자경고"
    print("[PASS] KN-14: _pick_strongest_level — 빈 list / 단일 / 다중 정상")


def test_KN_15_KindAlertCollector_통합():
    # KindNaverProvider 를 KindAlertCollector(provider=...) 에 주입했을 때 정상 동작
    page_data = {
        "manage":  _make_html(["005930"]),
        "halt":    _make_html(["000660"]),
        "caution": _make_html(["066570"]),
    }
    with patch.object(knp.urllib.request, "urlopen", _mock_urlopen_factory(page_data)):
        collector = KindAlertCollector(provider=knp.fetch_kind_alerts)
        snap = collector.collect()
    assert snap.is_valid is True
    assert snap.severity_map.get("005930") == 3, f'관리종목 severity 3 실패: {snap.severity_map}'
    assert snap.severity_map.get("000660") == 3, f'거래정지 severity 3 실패'
    assert snap.severity_map.get("066570") == 1, f'투자주의 severity 1 실패'
    print("[PASS] KN-15: KindAlertCollector(provider=fetch_kind_alerts) 통합 호출 정상")


# ===== 실행 =====


def main():
    tests = [
        test_KN_1_5_페이지_모두_정상,
        test_KN_2_1_페이지_실패,
        test_KN_3_모두_실패,
        test_KN_4_EUC_KR_디코딩,
        test_KN_5_LEVEL_PRIORITY_관리_우선,
        test_KN_6_종목코드_정규식_검증,
        test_KN_7_5단계_매핑_정확성,
        test_KN_8_중복_종목코드_제거,
        test_KN_9_빈_HTML,
        test_KN_10_timeout_적용,
        test_KN_11_User_Agent_헤더,
        test_KN_12_HTTPError_graceful,
        test_KN_13_관리종목_severity_매핑,
        test_KN_14_pick_strongest_level_빈_list,
        test_KN_15_KindAlertCollector_통합,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    total = len(tests)
    if failed == 0:
        print(f"\n✅ {total}/{total} 시나리오 PASS")
        return 0
    else:
        print(f"\n❌ {failed}/{total} 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
