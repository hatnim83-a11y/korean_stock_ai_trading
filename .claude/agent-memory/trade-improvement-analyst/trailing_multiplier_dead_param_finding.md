---
name: trailing-multiplier-dead-param-finding
description: ATR_MULTIPLIER 축소(2.0→1.5/1.7) 무효 — cap 8%가 ATR항 지배, 박제 11건 중 10건이 MULT 무관하게 cap고정. 폭 줄이려면 cap을 건드려야
metadata:
  type: project
---

# ATR_MULTIPLIER 축소 무효성 분석 (focus:trailing, 2026-06-16)

[[trailing-width-cap-finding]] 의 후속. 같은 박제 11건 표본, 질문 = "MULT 2.0→1.5/1.7 줄이면 수익 도움?".

## 결정적 수치 (data/trading.db portfolio.atr_at_buy 박제 11건, 5/27~6/16 v17)
- atr/first 분포: 중앙값 10.1%, 평균 10.2%, 범위 5.32%(신한지주)~18.62%(코스텍시스)
- **MULT별 cap 8% 구속 종목 수**: MULT 2.0→11/11 cap, 1.7→11/11, 1.5→10/11, 1.3→10/11, 1.0→9/11
- 즉 **MULT 1.5로 줄여도 effective_pct가 바뀌는 종목 = 신한지주 1건뿐**(8.00→7.98%, 0.02%p=무의미). MULT 1.0 극단축소조차 2건(신한·롯데쇼핑)만 ATR항 부활
- **MULT는 사실상 데드 파라미터**: cap 8%가 ATR항(raw 8~37%)을 전부 눌러 cap 진입. 배수를 곱해도 결과는 min(.,0.08)=0.08 불변

## 같은 "폭 줄이기" 목표 — MULT vs cap 대비 (결정적)
- MULT 1.5(cap 8% 유지): 11건 중 **1건** 변화
- cap 6%(MULT 2.0 유지): 11건 중 **11건** 변화 (8.00→6.00%)
- → 폭을 실제로 줄이려면 cap이 유일한 레버. MULT는 무력. **단 [[trailing-width-cap-finding]]에서 cap 하향은 휩쏘 반반으로 비권고** → 결국 둘 다 손대지 말 것

## 반사실 (ATR 박제된 트레일링 청산 2건뿐: 삼현·피에스케이)
- 트레일링 청산 19건 중 17건은 ATR 박제 이전(5/23 전)이라 정밀 반사실 불가
- 삼현(atr/first 8.12%)·피에스케이(11.25%) 둘 다 MULT 1.0~2.0 전구간 cap 8% 고정 → 청산결과 1원도 안 변함
- 피에스케이 실제 -9.45% vs cap가상 +8.93% 괴리는 BE floor 미작동 문제(별건), MULT 아님

## ATR 단위 검증 (코스텍시스 pykrx 수기재현)
- atr_calculator.py = 표준 14일 TR SMA, 단위정상(원화절대→/price 비율화 정상). 버그 없음
- 코스텍시스 재현 ATR(14)/price=20.05%, 일일 고-저/종가 평균 16.10%, 한달 종가 38,250~58,900(+54%)
- → 8~18% ATR/price는 계산오류 아니라 **실제 극단변동성**(급등주). 부풀림=종목선정 문제지 MULT로 보정할 대상 아님
- settings.ATR_MULTIPLIER 실제 참조처는 portfolio_monitor_v2.py:162 단 1곳(트레일링 전용). config description의 "손절 공용"은 문구일 뿐(portfolio_optimizer는 별도 DEFAULT_ATR_MULTIPLIER 사용)

## 결론
- **MULT 2.0→1.5/1.7 축소는 수익에 무효** (cap이 지배, 표본 10/11 불변). 권고: MULT 2.0 유지
- 폭 줄이려면 cap이 유일 레버지만 cap도 휩쏘 반반(비권고) → 트레일폭 파라미터는 전부 현행 유지
- 진짜 레버는 종목선정(고ATR 급등주 진입 자체) + BE floor. 신뢰도 Low~Medium(박제 11건/트레일청산 박제 2건)
