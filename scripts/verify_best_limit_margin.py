"""
최유리지정가(ORD_DVSN=03) 증거금 차감 방식 검증 스크립트 (READ-ONLY).

KIS inquire-psbl-order API를 ORD_DVSN=01(시장가) vs 03(최유리지정가)로
각각 호출하여 주문가능수량/금액을 비교한다. 실제 주문은 발생하지 않으므로
안전하게 실전 계정에서 실행 가능하다.

판정:
- 01과 03의 max_buy_qty가 동일 → KIS가 둘 다 상한가(+30%) 기준 증거금 적용 → 계획 무효
- 03의 max_buy_qty가 01보다 크다 (약 1.3배) → 03은 주문가 기준 1.0배 증거금 → 계획 유효

실행: source venv/bin/activate && python scripts/verify_best_limit_margin.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from config import settings
from modules.trading_engine.kis_order_api import KISOrderApi
from modules.stock_screener.kis_api import KISApi


TEST_STOCK_CODE = "005930"  # 삼성전자 (실전 계정에서 안전하게 조회 가능)


def fetch_current_price(api: KISApi, stock_code: str) -> int:
    price_info = api.get_current_price(stock_code)
    if not price_info or price_info.get("price", 0) <= 0:
        raise RuntimeError(f"{stock_code} 현재가 조회 실패")
    return int(price_info["price"])


def inquire_psbl_order(
    order_api: KISOrderApi,
    stock_code: str,
    ord_unpr: str,
    ord_dvsn: str,
) -> dict:
    """inquire-psbl-order API 직접 호출 (ORD_DVSN을 파라미터로)"""
    tr_id = "VTTC8908R" if order_api.is_mock else "TTTC8908R"
    url = f"{order_api.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"

    account_no = order_api.account_no
    if "-" in account_no:
        cano, acnt_prdt_cd = account_no.split("-")
    else:
        cano = account_no[:8]
        acnt_prdt_cd = account_no[8:] if len(account_no) > 8 else "01"

    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": stock_code,
        "ORD_UNPR": ord_unpr,
        "ORD_DVSN": ord_dvsn,
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }

    headers = order_api._get_headers(tr_id)
    order_api._rate_limit()
    response = httpx.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def extract_fields(data: dict) -> dict:
    """응답에서 핵심 필드 추출"""
    output = data.get("output", {})
    return {
        "rt_cd": data.get("rt_cd"),
        "msg1": data.get("msg1", ""),
        "ord_psbl_cash": output.get("ord_psbl_cash"),        # 주문가능현금
        "ord_psbl_sbst_amt": output.get("ord_psbl_sbst_amt"), # 주문가능대용금액
        "max_buy_amt": output.get("max_buy_amt"),            # 최대매수금액
        "max_buy_qty": output.get("max_buy_qty"),            # 최대매수수량
        "psbl_qty": output.get("psbl_qty"),                  # 가능수량
        "cma_evlu_amt": output.get("cma_evlu_amt"),          # CMA 평가금액
        "ovrs_re_use_amt_wcrc": output.get("ovrs_re_use_amt_wcrc"),
        "nrcvb_buy_amt": output.get("nrcvb_buy_amt"),        # 미수없는매수금액
        "nrcvb_buy_qty": output.get("nrcvb_buy_qty"),        # 미수없는매수수량
    }


def print_result(title: str, fields: dict) -> None:
    print(f"\n━━━━━ {title} ━━━━━")
    print(f"  rt_cd: {fields.get('rt_cd')}  msg: {fields.get('msg1')}")
    print(f"  주문가능현금:        {fields.get('ord_psbl_cash')}")
    print(f"  최대매수금액:        {fields.get('max_buy_amt')}")
    print(f"  최대매수수량:        {fields.get('max_buy_qty')}  ← 핵심")
    print(f"  미수없는매수금액:    {fields.get('nrcvb_buy_amt')}")
    print(f"  미수없는매수수량:    {fields.get('nrcvb_buy_qty')}  ← 핵심")
    print(f"  가능수량(psbl_qty):  {fields.get('psbl_qty')}")


def main() -> None:
    if settings.IS_MOCK:
        print("⚠️  IS_MOCK=True — 실전 계정으로 전환 후 실행하세요")
        sys.exit(1)

    print("=" * 70)
    print("최유리지정가(ORD_DVSN=03) 증거금 검증 (READ-ONLY)")
    print("=" * 70)

    kis = KISApi()
    order_api = KISOrderApi()

    current_price = fetch_current_price(kis, TEST_STOCK_CODE)
    print(f"\n종목: {TEST_STOCK_CODE} (삼성전자)")
    print(f"현재가: {current_price:,}원")

    # 테스트 조합: (주문가격, 주문구분)
    test_cases = [
        ("0", "01", "시장가 + UNPR=0 (현재 봇 기본값)"),
        ("0", "03", "최유리지정가 + UNPR=0"),
        (str(current_price), "00", "일반 지정가 + UNPR=현재가"),
        (str(current_price), "03", "최유리지정가 + UNPR=현재가 (Plan B 후보)"),
    ]

    results = []
    for ord_unpr, ord_dvsn, desc in test_cases:
        try:
            data = inquire_psbl_order(order_api, TEST_STOCK_CODE, ord_unpr, ord_dvsn)
            fields = extract_fields(data)
            print_result(f"{desc}", fields)
            results.append((desc, ord_dvsn, fields))
        except Exception as e:
            print(f"\n⚠️ {desc} 조회 실패: {e}")

    # 판정
    print("\n" + "=" * 70)
    print("판정")
    print("=" * 70)

    def buy_qty(fields: dict) -> int:
        qty = fields.get("max_buy_qty") or fields.get("nrcvb_buy_qty") or "0"
        try:
            return int(str(qty).replace(",", ""))
        except (ValueError, AttributeError):
            return 0

    results_by_dvsn = {ord_dvsn: (desc, fields) for desc, ord_dvsn, fields in results}

    qty_01 = buy_qty(results_by_dvsn.get("01", ("", {}))[1])
    qty_03_unpr0 = buy_qty([f for d, o, f in results if o == "03" and True][0]) if any(o == "03" for _, o, _ in results) else 0

    # 03 UNPR=0 결과 찾기
    qty_03_unpr0_fields = None
    qty_03_unpr_price_fields = None
    for desc, ord_dvsn, fields in results:
        if ord_dvsn == "03":
            if "UNPR=0" in desc:
                qty_03_unpr0_fields = fields
            elif "UNPR=현재가" in desc:
                qty_03_unpr_price_fields = fields

    qty_03_u0 = buy_qty(qty_03_unpr0_fields) if qty_03_unpr0_fields else 0
    qty_03_up = buy_qty(qty_03_unpr_price_fields) if qty_03_unpr_price_fields else 0

    print(f"\n  01 (시장가, UNPR=0)      최대매수수량: {qty_01:,}주")
    print(f"  03 (최유리, UNPR=0)      최대매수수량: {qty_03_u0:,}주")
    print(f"  03 (최유리, UNPR=현재가) 최대매수수량: {qty_03_up:,}주")

    if qty_01 == 0:
        print("\n⚠️ 01 결과 없음 — 판정 불가")
        return

    ratio_u0 = qty_03_u0 / qty_01 if qty_01 else 0
    ratio_up = qty_03_up / qty_01 if qty_01 else 0

    print(f"\n  03 UNPR=0 / 01 비율:      {ratio_u0:.3f}")
    print(f"  03 UNPR=현재가 / 01 비율: {ratio_up:.3f}")

    print("\n━━━━━ 결론 ━━━━━")
    if ratio_u0 >= 1.25:
        print("✅ ORD_DVSN=03 + UNPR=0  → 1.0배 증거금 확인 (약 1.3배 많은 수량)")
        print("   → 원 계획(Plan A) 진행 가능")
    elif ratio_up >= 1.25:
        print("⚠️  ORD_DVSN=03 + UNPR=0은 1.3배 증거금이지만,")
        print("    ORD_DVSN=03 + UNPR=현재가는 1.0배 증거금 → Plan B 채택 필요")
    else:
        print("❌ ORD_DVSN=03은 어떤 UNPR에서도 증거금 이득 없음")
        print("   → Plan C(일반 지정가 +1%) 검토 필요")


if __name__ == "__main__":
    main()
