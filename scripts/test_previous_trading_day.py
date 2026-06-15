"""previous_trading_day() 단위 테스트 — 종가베팅 청산누락 버그 fix.

config.previous_trading_day 가 직전 "거래일"을 정확히 반환하는지 검증.
실행: venv python scripts/test_previous_trading_day.py
"""
import sys
from datetime import date, datetime, timezone, timedelta

import config

KST = timezone(timedelta(hours=9))
_passed = 0
_failed = 0


def check(name, got, expected):
    global _passed, _failed
    if got == expected:
        print(f"  [PASS] {name} → {got}")
        _passed += 1
    else:
        print(f"  [FAIL] {name} → got={got} expected={expected}")
        _failed += 1


def run():
    # holidays.KR 설치 여부 (없으면 공휴일 케이스는 평일 폴백)
    has_holidays = config._kr_holidays is not None
    print(f"holidays.KR 설치: {has_holidays}")

    # 1. 월요일(2026-06-15) → 금요일(2026-06-12). ★ 핵심 버그 케이스
    check("월→금 (주말 walk-back)",
          config.previous_trading_day(date(2026, 6, 15)), date(2026, 6, 12))

    # 2. 화요일(2026-06-16) → 월요일(2026-06-15). 정상 연속 거래일 = 달력 어제
    check("화→월 (연속 거래일, 회귀 불변)",
          config.previous_trading_day(date(2026, 6, 16)), date(2026, 6, 15))

    # 3. 금요일(2026-06-12) → 목요일(2026-06-11). 정상 연속
    check("금→목 (연속 거래일)",
          config.previous_trading_day(date(2026, 6, 12)), date(2026, 6, 11))

    # 4. 일요일 입력(2026-06-14) → 금요일(2026-06-12). 주말 입력 방어
    check("일→금 (주말 입력)",
          config.previous_trading_day(date(2026, 6, 14)), date(2026, 6, 12))

    # 5. 토요일 입력(2026-06-13) → 금요일(2026-06-12)
    check("토→금 (주말 입력)",
          config.previous_trading_day(date(2026, 6, 13)), date(2026, 6, 12))

    # 6. datetime 객체 전달 → date 정규화(.isoformat 오염 방지)
    dt = datetime(2026, 6, 15, 9, 5, tzinfo=KST)
    got = config.previous_trading_day(dt)
    check("datetime 입력 → date 반환 (정규화)", type(got).__name__, "date")
    check("datetime 입력 값 (월 09:05 → 금)", got, date(2026, 6, 12))

    # 7. 공휴일 walk-back (holidays 설치 시): 2026 신정 1/1(목) 휴장.
    #    금(1/2) 청산은 직전거래일=목(1/1 휴장)→수(2025-12-31)... 단 12/31은 임시휴장(미포함).
    #    법정공휴일 확정 케이스: 2026-03-02(월)은 삼일절(3/1 일) 대체공휴일.
    if has_holidays:
        import holidays as _h
        kr = _h.KR()
        # 2026-08-17(월)이 광복절(8/15 토) 대체공휴일인지 확인 후 케이스 구성
        d_holiday = date(2026, 8, 17)
        if d_holiday in kr:
            # 8/18(화) → 직전거래일: 8/17(월,대체공휴일)→8/14(금)
            check("화→금 (월 대체공휴일 walk-back)",
                  config.previous_trading_day(date(2026, 8, 18)), date(2026, 8, 14))
        else:
            print(f"  [SKIP] 8/17 대체공휴일 아님 (holidays 버전 차이) — 공휴일 케이스 스킵")

        # 추석 연휴 등 다중 연속 공휴일 walk-back: 9/24~26(목~토) 휴장이면
        # 9/28(월) 청산 → 직전거래일=9/23(수) 까지 5영업일 walk-back
        chuseok = [date(2026, 9, 24), date(2026, 9, 25)]
        if all(d in kr for d in chuseok):
            check("월→수 (추석 연휴 다중 walk-back)",
                  config.previous_trading_day(date(2026, 9, 28)), date(2026, 9, 23))
        else:
            print(f"  [SKIP] 9/24~25 추석 연휴 아님 (holidays 버전 차이) — 다중 공휴일 케이스 스킵")

    # 8. ref_date=None → now_kst 기반, date 타입 반환 + 거래일 보장
    got_default = config.previous_trading_day()
    check("기본값(None) → date 타입", type(got_default).__name__, "date")
    check("기본값(None) → 거래일 보장", config.is_trading_day(got_default), True)

    # 9. 무한루프 방어: 반환값은 항상 거래일
    for ref in [date(2026, 6, 15), date(2026, 6, 16), date(2026, 1, 5)]:
        prev = config.previous_trading_day(ref)
        check(f"{ref} 반환값은 거래일 + ref 이전", prev < ref and config.is_trading_day(prev), True)


if __name__ == "__main__":
    print("▶ test_previous_trading_day")
    run()
    print(f"\n=== 결과: {_passed} PASS / {_failed} FAIL ===")
    sys.exit(1 if _failed else 0)
