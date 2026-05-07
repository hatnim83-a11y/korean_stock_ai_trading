"""pykrx ETF 동적 조회 가능 여부 검증 (단위 2-9f Step 0.1).

`pykrx.stock.get_etf_ticker_list(today_str)` 단발 호출. 단위 2-9c에서 KRX bulk
API가 차단됐지만 ETF 리스트는 별도 엔드포인트일 가능성. 확인 + 메이저 ETF
매칭 검증 + 정적 폴백 set 사전 합의 근거 확보.
"""
import sys
import time

sys.path.insert(0, "/home/hatni/korean_stock_ai_trading")


def main() -> None:
    today_str = "20260507"
    print(f"=== pykrx ETF 리스트 동적 조회 검증 ({today_str}) ===\n")

    try:
        from pykrx import stock as krx
    except Exception as e:
        print(f"❌ pykrx import 실패: {type(e).__name__}: {e}")
        sys.exit(1)

    # 1. ETF 리스트 호출
    print("1. get_etf_ticker_list 호출 중...")
    t0 = time.time()
    try:
        etf_list = krx.get_etf_ticker_list(today_str)
        elapsed = time.time() - t0
        print(f"   ✅ 성공 — {len(etf_list)}건 ({elapsed:.2f}초)")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"   ❌ 호출 실패 ({elapsed:.2f}초) — {type(e).__name__}: {e}")
        print("\n   → 정적 화이트리스트 폴백 set 사전 합의 필요")
        sys.exit(2)

    # 2. 6자리 종목코드 검증
    invalid = [t for t in etf_list if not (isinstance(t, str) and len(t) == 6 and t.isdigit())]
    print(f"\n2. 6자리 종목코드 검증")
    print(f"   - 유효: {len(etf_list) - len(invalid)}건")
    print(f"   - 무효: {len(invalid)}건")
    if invalid:
        print(f"   - 무효 sample: {invalid[:10]}")

    # 3. 메이저 ETF 매칭 (사전 리뷰 발견)
    major_etfs = {
        "069500": "KODEX 200",
        "102110": "TIGER 200",
        "122630": "KODEX 레버리지",
        "233740": "KODEX 코스닥150 레버리지",
        "252670": "KODEX 200선물인버스2X",
        "091160": "KODEX 코스닥150",
        "114800": "KODEX 인버스",
        "117460": "KODEX 에너지화학",
        "153130": "KODEX 단기채권",
        "292340": "마이다스 코스닥혁신성장 ETF (검증 필요)",
    }
    etf_set = set(etf_list)
    print(f"\n3. 메이저 ETF 매칭 검증 (사전 리뷰에서 누락 의심된 종목)")
    matched = 0
    for code, name in major_etfs.items():
        if code in etf_set:
            print(f"   ✅ {code} {name}")
            matched += 1
        else:
            print(f"   ❌ {code} {name} — pykrx 리스트에 부재")
    print(f"   매칭: {matched}/{len(major_etfs)}")

    # 4. KOSDAQ 보통주 false positive 검증 (사전 리뷰 발견)
    false_positive_candidates = {
        "069080": "웹젠 (KOSDAQ 보통주)",
        "091990": "셀트리온헬스케어 (구 KOSDAQ 시총 상위)",
        "117730": "티로보틱스 (KOSDAQ)",
        "292340": "모코 (검증 필요)",
    }
    print(f"\n4. false positive 후보 검증 (KOSDAQ 보통주가 ETF 리스트에 들어가는지)")
    fp_in_etf = 0
    for code, name in false_positive_candidates.items():
        if code in etf_set:
            print(f"   ⚠️  {code} {name} — ETF 리스트 매칭 (실제 ETF일 가능성)")
            fp_in_etf += 1
        else:
            print(f"   ✅ {code} {name} — ETF 리스트 미포함 (false positive 회피)")
    print(f"   ETF 매칭: {fp_in_etf}/{len(false_positive_candidates)}")

    # 5. ETF 리스트 첫 30건 + 마지막 5건 sample
    print(f"\n5. ETF 리스트 sample")
    print(f"   첫 10건: {sorted(etf_list)[:10]}")
    print(f"   마지막 5건: {sorted(etf_list)[-5:]}")

    # 6. prefix 분포 분석
    from collections import Counter
    prefix_3 = Counter(t[:3] for t in etf_list if len(t) == 6)
    print(f"\n6. ETF prefix(3자리) 분포 top 15")
    for prefix, cnt in prefix_3.most_common(15):
        print(f"   {prefix}: {cnt}건")
    print(f"   총 prefix 종류: {len(prefix_3)}")

    # 7. 결론
    print(f"\n=== 결론 ===")
    print(f"pykrx 동적 조회: {'사용 가능' if len(etf_list) > 0 else '사용 불가'}")
    print(f"메이저 ETF 매칭률: {matched}/{len(major_etfs)} ({100*matched/len(major_etfs):.0f}%)")
    print(f"false positive 회피율: {len(false_positive_candidates) - fp_in_etf}/{len(false_positive_candidates)}")
    print(f"단위 2-9f Step 0.1 게이트: {'PASS' if matched >= 8 else 'FAIL — 정적 폴백 사전 합의 필요'}")


if __name__ == "__main__":
    main()
