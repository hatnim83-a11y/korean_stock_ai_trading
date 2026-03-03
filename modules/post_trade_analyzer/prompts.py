"""
prompts.py - Post-Trade 분석용 AI 프롬프트 템플릿
"""

INDIVIDUAL_ANALYSIS_PROMPT = """당신은 한국 주식 매매 복기 전문 분석가입니다.
아래 매매 기록과 매도 후 주가 추이를 분석하여 매도 판단의 적절성을 평가해주세요.

## 매매 정보
- 종목: {stock_name} ({stock_code})
- 테마: {theme}
- 매수일: {buy_date} | 매도일: {sell_date}
- 매수가: {buy_price:,}원 | 매도가: {sell_price:,}원
- 보유일: {hold_days}일
- 수익률: {profit_rate:+.2f}%
- 매도 사유: {sell_reason}
- 전략: {strategy_type}
- 트레일링 레벨: {trailing_level}
- 보유 중 최고 수익률: {max_profit:+.2f}%

## 매도 후 5영업일 주가 추이
{price_table}

## 분석 요청
1. **타이밍 점수** (0-10): 매도 타이밍이 얼마나 적절했는가?
   - 0: 최악 (매도 후 급등, 큰 기회비용 발생)
   - 5: 보통 (매도 후 큰 변동 없음)
   - 10: 완벽 (매도 후 하락, 정확한 판단)

2. **기회비용 분석**: 매도 후 5일간 추가 수익/손실 기회가 있었는가?

3. **패턴 인식**: 이 매매에서 발견되는 패턴이 있는가? (조기 매도, 늦은 손절 등)

4. **파라미터 제안**: 손절/익절/트레일링 파라미터 조정이 필요한가?

5. **핵심 교훈**: 이 매매에서 배울 수 있는 가장 중요한 교훈 1가지

## 응답 형식 (반드시 JSON)
```json
{{
  "timing_score": 7,
  "timing_reason": "매도 후 소폭 하락하여 적절한 타이밍",
  "opportunity_cost": "+2.3% 추가 수익 기회가 있었으나 리스크 대비 합리적",
  "pattern": "트레일링 스탑 L1에서 적절히 수익 확보",
  "parameter_suggestion": "현재 트레일링 파라미터 유지 적절",
  "lesson": "테마 모멘텀이 약해지기 전 수익 확보가 중요",
  "overall_assessment": "Good"
}}
```

overall_assessment 값: "Excellent", "Good", "Neutral", "Poor", "Bad"
주의: 반드시 위 JSON 형식으로만 응답하세요.
"""

WEEKLY_SUMMARY_PROMPT = """당신은 한국 주식 트레이딩 시스템의 전략 자문가입니다.
이번 주 매매 복기 결과를 종합 분석하여 시스템 개선 제안을 해주세요.

## 이번 주 분석 요약
총 {total_count}건 분석 완료

### 전략별 성과
{strategy_summary}

### 개별 분석 결과
{individual_summaries}

## 분석 요청
1. **주간 핵심 패턴**: 이번 주 매매에서 반복되는 패턴
2. **전략별 평가**: 각 전략(손절/익절/트레일링)의 적절성
3. **파라미터 종합 제안**: 이번 주 결과를 바탕으로 한 파라미터 조정 제안
4. **다음 주 주의사항**: 시스템 운영 시 주의할 점

## 응답 형식 (반드시 JSON)
```json
{{
  "weekly_pattern": "손절 타이밍은 적절하나, 트레일링 L1에서 조기 매도 경향",
  "strategy_evaluation": {{
    "stop_loss": "적절 — 평균 타이밍 점수 7.2",
    "take_profit": "개선 필요 — 일부 조기 매도",
    "trailing_stop": "양호 — L2 이상 도달 비율 40%"
  }},
  "parameter_suggestions": [
    "트레일링 L1 진입 기준을 +8%에서 +10%로 상향 검토",
    "손절 -7% 유지 적절"
  ],
  "next_week_caution": "테마 전환기에 진입 시 모멘텀 확인 강화 필요",
  "avg_timing_score": 6.8
}}
```

주의: 반드시 위 JSON 형식으로만 응답하세요.
"""
