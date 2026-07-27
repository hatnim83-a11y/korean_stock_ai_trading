"""test_kis_balance_parse.py — KIS get_balance 응답 파싱(순수 함수) 검증 (Part B).

목표:
- KIS output2 의 **실제 계좌총자산 필드**를 우선순위대로 노출(`total_assets`).
- cash + 주식평가 **중복 합산 금지**(추정 금지) — 문서 필드값을 그대로 사용.
- raw output2 핵심 필드 보존(`account_summary`) + 성공/실패 메타(`ok`/`error`).
- 기존 소비자 후방호환: `total_value`(=tot_evlu_amt), `cash`(=dnca_tot_amt) 유지.

실 KIS API 미의존 — 순수 함수 `parse_balance_response(data)` 에 fixture dict 주입.

실행: pytest tests/test_kis_balance_parse.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.kis_order_api import parse_balance_response


def _ok_data(output2: dict, output1=None) -> dict:
    return {"rt_cd": "0", "output1": output1 or [], "output2": [output2]}


def test_total_assets_prefers_tot_asst_amt():
    """tot_asst_amt 가 존재하면 그 값을 계좌총자산으로 사용(중복 합산 금지)."""
    data = _ok_data({
        "tot_asst_amt": "12345678",
        "tot_evlu_amt": "9091759",
        "nass_amt": "9000000",
        "dnca_tot_amt": "500000",
        "scts_evlu_amt": "8591759",
    })
    r = parse_balance_response(data)
    assert r["ok"] is True
    assert r["total_assets"] == 12345678
    assert r["total_assets_field"] == "tot_asst_amt"
    # 후방호환 필드 유지
    assert r["total_value"] == 9091759
    assert r["cash"] == 500000
    # 중복 합산 금지: cash(50만)+scts(859만) != total_assets
    assert r["total_assets"] != r["cash"] + 8591759


def test_total_assets_falls_back_to_tot_evlu_amt():
    """tot_asst_amt 부재 → tot_evlu_amt 로 폴백(추정/합산 아님)."""
    data = _ok_data({
        "tot_evlu_amt": "9091759",
        "nass_amt": "9000000",
        "dnca_tot_amt": "500000",
    })
    r = parse_balance_response(data)
    assert r["total_assets"] == 9091759
    assert r["total_assets_field"] == "tot_evlu_amt"


def test_total_assets_falls_back_to_nass_amt():
    """tot_asst_amt/tot_evlu_amt 모두 부재 → nass_amt 폴백."""
    data = _ok_data({"nass_amt": "7000000", "dnca_tot_amt": "100000"})
    r = parse_balance_response(data)
    assert r["total_assets"] == 7000000
    assert r["total_assets_field"] == "nass_amt"


def test_account_summary_preserves_raw_fields():
    """account_summary 에 raw output2 핵심 필드가 보존된다."""
    data = _ok_data({
        "tot_asst_amt": "12345678",
        "tot_evlu_amt": "9091759",
        "nass_amt": "9000000",
        "dnca_tot_amt": "500000",
        "scts_evlu_amt": "8591759",
        "evlu_amt_smtl_amt": "8591759",
        "pchs_amt_smtl_amt": "8000000",
        "evlu_pfls_smtl_amt": "591759",
    })
    r = parse_balance_response(data)
    s = r["account_summary"]
    assert s["tot_asst_amt"] == 12345678
    assert s["tot_evlu_amt"] == 9091759
    assert s["nass_amt"] == 9000000
    assert s["dnca_tot_amt"] == 500000
    assert s["scts_evlu_amt"] == 8591759


def test_empty_string_fields_safe():
    """빈 문자열 필드(KIS 흔한 응답)도 방어 — 예외 없이 0 처리."""
    data = _ok_data({"tot_asst_amt": "", "tot_evlu_amt": "", "nass_amt": "", "dnca_tot_amt": ""})
    r = parse_balance_response(data)
    assert r["ok"] is True
    # 모든 총자산 후보가 0 → total_assets=0, 필드 표기는 None(유효값 없음)
    assert r["total_assets"] == 0
    assert r["total_assets_field"] is None


def test_positions_parsed():
    """output1 보유종목이 파싱된다(기존 동작 유지)."""
    data = _ok_data(
        {"tot_asst_amt": "12345678", "tot_evlu_amt": "9091759", "dnca_tot_amt": "500000"},
        output1=[{
            "pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
            "pchs_avg_pric": "70000", "prpr": "75000",
            "evlu_pfls_amt": "50000", "evlu_pfls_rt": "7.14",
        }],
    )
    r = parse_balance_response(data)
    assert r["position_count"] == 1
    assert r["positions"][0]["stock_code"] == "005930"
    assert r["positions"][0]["quantity"] == 10


def test_error_response_marks_not_ok():
    """rt_cd != 0 → ok=False + error 메시지. 총자산 0(위장 금지)."""
    data = {"rt_cd": "1", "msg1": "일시적 오류", "output2": []}
    r = parse_balance_response(data)
    assert r["ok"] is False
    assert r["total_assets"] == 0
    assert r["total_value"] == 0
    assert r["error"]


def test_empty_output2_safe():
    """output2 가 빈 리스트/None 이어도 예외 없이 안전 처리."""
    for bad in ([], None):
        data = {"rt_cd": "0", "output1": [], "output2": bad}
        r = parse_balance_response(data)
        assert r["ok"] is True
        assert r["total_assets"] == 0
        assert r["total_assets_field"] is None
