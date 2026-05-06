"""사전 조사 — 단위 2-9e: KIS market-cap ranking 응답 단위 검증.

실행: venv/bin/python scripts/probe_kis_unit_2_9e.py

검증 목표:
1. ranking/market-cap output[*] 의 모든 키 / 값 표 출력 (상위 10종목)
2. volume-rank output[*] 의 stck_avls / hts_avls 등 시총 키 존재 여부
3. inquire-price (005930) 의 hts_avls 값 — 정답 시총 (× 100,000,000 = 원)
4. 알려진 종목 시총 비교로 단위 결정
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from closing_bet_system.collectors.kis_market_provider import (
    _PATH_MARKET_CAP, _PATH_VOLUME_RANK, _TR_MARKET_CAP, _TR_VOLUME_RANK,
    get_kis_market_provider,
)
from closing_bet_system.infra.kis_client import get_kis_api


# 알려진 종목 실제 시총 (단위: 원, 2026-04 기준 추정치)
KNOWN_MARKET_CAP = {
    "005930": ("삼성전자", 530_000_000_000_000),    # 약 530조
    "000660": ("SK하이닉스", 200_000_000_000_000),  # 약 200조
    "035420": ("NAVER", 30_000_000_000_000),         # 약 30조
    "005380": ("현대차", 50_000_000_000_000),         # 약 50조
    "035720": ("카카오", 20_000_000_000_000),         # 약 20조
}


def probe_market_cap_ranking():
    print("=" * 80)
    print("[1] ranking/market-cap (FHPST01740000) 원시 응답 — 상위 10종목")
    print("=" * 80)
    provider = get_kis_market_provider()
    kis = provider._kis
    kis._rate_limit()
    url = f"{kis.base_url}{_PATH_MARKET_CAP}"
    headers = kis._get_headers(_TR_MARKET_CAP)
    params = {
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
    resp = kis.client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    print(f"rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
    output = data.get("output") or data.get("output1") or []
    print(f"output type={type(output).__name__} len={len(output) if isinstance(output, list) else 'N/A'}")
    if isinstance(output, list) and output:
        print(f"\n첫 항목 모든 키 ({len(output[0])}개):")
        for k, v in output[0].items():
            print(f"  {k!r:32s} = {v!r}")
        print(f"\n상위 10종목 핵심 컬럼 추정 (mksc_shrn_iscd / hts_kor_isnm / stck_prpr / stck_avls / hts_avls):")
        print(f"{'rank':>4} {'code':>7} {'name':<14} {'stck_prpr':>12} {'stck_avls':>16} {'hts_avls':>16} {'lstn_stcn':>16} {'mrkt_cap':>16}")
        for i, item in enumerate(output[:10], 1):
            print(
                f"{i:>4} "
                f"{str(item.get('mksc_shrn_iscd', '')):>7} "
                f"{str(item.get('hts_kor_isnm', ''))[:13]:<14} "
                f"{str(item.get('stck_prpr', '')):>12} "
                f"{str(item.get('stck_avls', '')):>16} "
                f"{str(item.get('hts_avls', '')):>16} "
                f"{str(item.get('lstn_stcn', '')):>16} "
                f"{str(item.get('mrkt_cap', '')):>16}"
            )
    return output


def probe_volume_rank():
    print("\n" + "=" * 80)
    print("[2] volume-rank (FHPST01710000) — 시총 컬럼 존재 여부")
    print("=" * 80)
    provider = get_kis_market_provider()
    kis = provider._kis
    kis._rate_limit()
    url = f"{kis.base_url}{_PATH_VOLUME_RANK}"
    headers = kis._get_headers(_TR_VOLUME_RANK)
    params = {
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
    resp = kis.client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    print(f"rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
    output = data.get("output") or []
    if isinstance(output, list) and output:
        first = output[0]
        print(f"\n첫 항목 키 목록 ({len(first)}개):")
        # 시총/주식수 후보 키만 추출
        candidates = [k for k in first.keys() if any(t in k.lower() for t in ["avls", "cap", "mrkt", "stcn", "lstn"])]
        print(f"  시총/주식수 후보 키: {candidates}")
        if candidates:
            print(f"\n  상위 5종목의 후보 키 값:")
            for i, item in enumerate(output[:5], 1):
                vals = " ".join([f"{k}={item.get(k, '')!r}" for k in candidates])
                print(f"    {i}. {item.get('mksc_shrn_iscd', '')} {item.get('hts_kor_isnm', ''):>10}: {vals}")
        else:
            print("  ⚠ 시총 관련 키 없음 — volume-rank 응답에 시총 보강 불가")


def probe_inquire_price():
    print("\n" + "=" * 80)
    print("[3] inquire-price (FHKST01010100) — 005930 hts_avls 정답값")
    print("=" * 80)
    kis = get_kis_api()
    for code in ["005930", "000660", "035420", "005380", "035720"]:
        result = kis.get_current_price(code)
        if result:
            mc = result.get("market_cap")
            name, expected = KNOWN_MARKET_CAP.get(code, (code, 0))
            ratio = (mc / expected) if (mc and expected) else 0
            print(f"  {code} {name:>10}: market_cap={mc:>20,d}원 (expected≈{expected:>20,d}, ratio={ratio:.3f})")
        else:
            print(f"  {code}: 조회 실패")


def analyze_units(mc_output):
    print("\n" + "=" * 80)
    print("[4] 단위 분석 — ranking/market-cap stck_avls 추정 단위")
    print("=" * 80)
    if not isinstance(mc_output, list):
        print("  ⚠ output 비정상 — 분석 불가")
        return
    candidates_keys = ["stck_avls", "hts_avls", "lstn_stcn", "mrkt_cap"]
    print(f"\n  비교 기준 (실제 시총 / 응답값) → 단위 추정:")
    print(f"  {'code':>7} {'name':>10} {'expected':>16}", end="")
    for k in candidates_keys:
        print(f" {k:>14}", end="")
    print()
    for item in mc_output[:10]:
        code = str(item.get("mksc_shrn_iscd", ""))
        name = str(item.get("hts_kor_isnm", ""))[:9]
        if code not in KNOWN_MARKET_CAP:
            continue
        expected = KNOWN_MARKET_CAP[code][1]
        print(f"  {code:>7} {name:>10} {expected:>16,d}", end="")
        for k in candidates_keys:
            v = item.get(k, "")
            try:
                v_num = float(str(v).replace(",", "")) if v != "" else 0
                if v_num > 0:
                    ratio = expected / v_num
                    # ratio 가 1, 100, 10000, 100000000 근사 → 원/만/억/원
                    print(f" {v_num:>14,.0f}", end="")
                else:
                    print(f" {'':>14}", end="")
            except (ValueError, TypeError):
                print(f" {'':>14}", end="")
        print()
    print(f"\n  → ratio(expected / 응답값) ≈ 100,000,000 이면 응답 단위 = 원 단위 그대로? 아님")
    print(f"  → ratio ≈ 1 이면 단위 = 원 단위. ratio ≈ 100M 이면 응답 = 억원 단위")
    print(f"  → ratio ≈ 1,000 이면 응답 단위 = 천원. 다른 값이면 컬럼 의미 재검토")


def main():
    try:
        mc_output = probe_market_cap_ranking()
    except Exception as e:
        print(f"market-cap 조회 실패: {type(e).__name__}: {e}")
        mc_output = []
    try:
        probe_volume_rank()
    except Exception as e:
        print(f"volume-rank 조회 실패: {type(e).__name__}: {e}")
    try:
        probe_inquire_price()
    except Exception as e:
        print(f"inquire-price 조회 실패: {type(e).__name__}: {e}")
    try:
        analyze_units(mc_output)
    except Exception as e:
        print(f"단위 분석 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
