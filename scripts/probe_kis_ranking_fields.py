"""KIS Open API ranking 4종 응답 필드 매핑 단발 조사 (단위 2-9d 핫픽스).

각 ranking 응답 첫 1건의 필드를 출력해 종목코드 필드명을 확정한다.

- volume_rank (FHPST01710000)  — 거래대금 상위
- fluctuation (FHPST01700000)  — 등락률 상위
- foreign_total (FHPTJ04400000) — 외국인 순매수 상위
- market_cap (FHPST01740000)   — 시가총액 상위

KIS 토큰 1분 발급 제한 회피: 단일 프로세스에서 4종 일괄 호출 (rate_limit 내장 sleep 사용).

용도: 향후 회귀 재검증 시 보존.
"""
import sys

sys.path.insert(0, "/home/hatni/korean_stock_ai_trading")

from closing_bet_system.collectors.kis_market_provider import get_kis_market_provider
import closing_bet_system.collectors.kis_market_provider as km


def _print_first_item(label: str, items: list[dict]) -> None:
    print(f"\n=== {label} 응답 ({len(items)}건) ===")
    if not items:
        print("  (응답 없음)")
        return
    item = items[0]
    print(f"  --- item 0 (총 {len(item)}개 필드) ---")
    ticker_keys = []
    for k, v in sorted(item.items()):
        marker = ""
        if "iscd" in k.lower() or "shrn" in k.lower() or "tick" in k.lower():
            marker = "  <<< TICKER 후보"
            ticker_keys.append((k, v))
        print(f"  {k:30s} = {v!r}{marker}")
    if ticker_keys:
        print(f"  >>> ticker 후보 필드: {ticker_keys}")
    else:
        print(f"  >>> ticker 후보 필드 없음 — 전체 필드 검토 필요")


def main() -> None:
    prov = get_kis_market_provider()

    # 1. volume_rank
    params_v = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "3",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    items_v = prov._call_ranking(km._PATH_VOLUME_RANK, km._TR_VOLUME_RANK, params_v, source="probe_volume")
    _print_first_item("volume_rank (FHPST01710000)", items_v)

    # 2. fluctuation
    params_f = {
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": "0000",
        "fid_rank_sort_cls_code": "0",
        "fid_input_cnt_1": "5",
        "fid_prc_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
        "fid_rsfl_rate2": "",
    }
    items_f = prov._call_ranking(km._PATH_FLUCTUATION, km._TR_FLUCTUATION, params_f, source="probe_fluctuation")
    _print_first_item("fluctuation (FHPST01700000)", items_f)

    # 3. foreign_total
    params_fr = {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "1",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_ETC_CLS_CODE": "1",
    }
    items_fr = prov._call_ranking(km._PATH_FOREIGN_TOTAL, km._TR_FOREIGN_TOTAL, params_fr, source="probe_foreign")
    _print_first_item("foreign_total (FHPTJ04400000)", items_fr)

    # 4. market_cap
    params_m = {
        "fid_input_price_2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20174",
        "fid_div_cls_code": "0",
        "fid_input_iscd": "0000",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_input_price_1": "",
        "fid_vol_cnt": "",
    }
    items_m = prov._call_ranking(km._PATH_MARKET_CAP, km._TR_MARKET_CAP, params_m, source="probe_market_cap")
    _print_first_item("market_cap (FHPST01740000)", items_m)

    print("\n=== 결론 ===")
    print("각 ranking의 ticker 후보 필드를 위 결과에서 확인하세요.")
    print("핫픽스 _TICKER_KEYS_DEFAULT 우선순위 결정 근거.")


if __name__ == "__main__":
    main()
