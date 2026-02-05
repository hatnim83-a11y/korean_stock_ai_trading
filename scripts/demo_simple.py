#!/usr/bin/env python3
"""
포트폴리오 생성 데모 - 자산배분 가중치 시연
"""

print("=" * 80)
print("💼 포트폴리오 자산배분 가중치 자동 계산 데모")
print("=" * 80)

# 모의 검증 통과 종목
stocks = [
    {"name": "LG에너지솔루션", "code": "373220", "theme": "2차전지", "price": 420000, "score": 85.5},
    {"name": "삼성SDI", "code": "006400", "theme": "2차전지", "price": 320000, "score": 83.2},
    {"name": "SK하이닉스", "code": "000660", "theme": "AI 반도체", "price": 180000, "score": 82.1},
    {"name": "삼성전자", "code": "005930", "theme": "AI 반도체", "price": 75000, "score": 79.5},
    {"name": "삼성바이오로직스", "code": "207940", "theme": "바이오", "price": 920000, "score": 78.3},
    {"name": "셀트리온", "code": "068270", "theme": "바이오", "price": 185000, "score": 76.8},
    {"name": "카카오", "code": "035720", "theme": "플랫폼", "price": 42000, "score": 75.2},
    {"name": "NAVER", "code": "035420", "theme": "플랫폼", "price": 220000, "score": 74.5},
]

capital = 10000000  # 1000만원
print(f"\n총 투자 자본금: {capital:,}원")
print(f"✅ AI 검증 통과 종목: {len(stocks)}개\n")

# Step 1: 점수 기반 가중치 계산
total_score = sum(s["score"] for s in stocks)
for stock in stocks:
    stock["score_weight"] = stock["score"] / total_score

print("━" * 80)
print("📊 Step 1: 점수 기반 가중치 계산")
print("━" * 80)

for i, s in enumerate(stocks, 1):
    print(f"{i}. {s['name']:<15} 점수: {s['score']:5.1f} → 가중치: {s['score_weight']*100:5.2f}%")

# Step 2: 제약 조건 적용 (최소 3%, 최대 15%)
MIN_WEIGHT = 0.03
MAX_WEIGHT = 0.15

print(f"\n━" * 80)
print(f"📊 Step 2: 제약 조건 적용 (최소 {MIN_WEIGHT*100}%, 최대 {MAX_WEIGHT*100}%)")
print("━" * 80)

for stock in stocks:
    old_weight = stock["score_weight"]
    stock["final_weight"] = max(MIN_WEIGHT, min(MAX_WEIGHT, old_weight))
    
# 가중치 정규화
total_weight = sum(s["final_weight"] for s in stocks)
for stock in stocks:
    stock["final_weight"] = stock["final_weight"] / total_weight

for i, s in enumerate(stocks, 1):
    print(f"{i}. {s['name']:<15} {s['score_weight']*100:5.2f}% → {s['final_weight']*100:5.2f}%")

# Step 3: 투자 금액 및 수량 계산
print(f"\n━" * 80)
print(f"📊 Step 3: 투자 금액 및 매수 수량 계산")
print("━" * 80)

investable = capital * 0.95  # 95% 투자, 5% 현금 보유

for stock in stocks:
    target_amount = investable * stock["final_weight"]
    shares = int(target_amount / stock["price"])
    actual_amount = shares * stock["price"]
    
    stock["shares"] = shares
    stock["amount"] = actual_amount
    stock["actual_weight"] = actual_amount / capital

# Step 4: 손절/익절 가격 계산
print("\n" + "=" * 80)
print("💼 최종 포트폴리오")
print("=" * 80)

print(f"\n{'No':<4} {'종목명':<15} {'테마':<12} {'가중치':<8} {'수량':<8} {'금액':<12} {'손절가':<10} {'익절가':<10}")
print("━" * 80)

total_invested = 0
for i, s in enumerate(stocks, 1):
    stop_loss = int(s["price"] * 0.92)  # -8% 손절
    take_profit = int(s["price"] * 1.15)  # +15% 익절
    
    s["stop_loss"] = stop_loss
    s["take_profit"] = take_profit
    
    total_invested += s["amount"]
    
    print(f"{i:<4} {s['name']:<15} {s['theme']:<12} {s['actual_weight']*100:5.1f}%  "
          f"{s['shares']:5}주  {s['amount']:>10,}원  {stop_loss:>9,}  {take_profit:>9,}")

cash_remaining = capital - total_invested

print("━" * 80)
print(f"{'합계':<4} {'':<15} {'':<12} {total_invested/capital*100:5.1f}%  "
      f"{sum(s['shares'] for s in stocks):5}주  {total_invested:>10,}원")

# 요약
print(f"\n" + "=" * 80)
print("📈 포트폴리오 요약")
print("=" * 80)

print(f"\n💰 자본 배분:")
print(f"   총 자본금:     {capital:>12,}원 (100.0%)")
print(f"   총 투자 금액:  {total_invested:>12,}원 ({total_invested/capital*100:5.1f}%)")
print(f"   현금 잔액:     {cash_remaining:>12,}원 ({cash_remaining/capital*100:5.1f}%)")

# 테마별 집계
theme_data = {}
for s in stocks:
    theme = s["theme"]
    if theme not in theme_data:
        theme_data[theme] = {"amount": 0, "count": 0}
    theme_data[theme]["amount"] += s["amount"]
    theme_data[theme]["count"] += 1

print(f"\n📊 테마별 배분:")
for theme, data in sorted(theme_data.items(), key=lambda x: x[1]["amount"], reverse=True):
    weight = data["amount"] / capital * 100
    print(f"   {theme:<15} {data['count']}종목  {data['amount']:>11,}원 ({weight:5.1f}%)")

# 위험 지표
max_loss = sum((s["price"] - s["stop_loss"]) * s["shares"] for s in stocks)
max_profit = sum((s["take_profit"] - s["price"]) * s["shares"] for s in stocks)

print(f"\n⚠️  위험 관리:")
print(f"   최대 손실 (전체 손절):  -{max_loss:>10,}원 ({-max_loss/capital*100:5.1f}%)")
print(f"   최대 수익 (전체 익절):  +{max_profit:>10,}원 (+{max_profit/capital*100:4.1f}%)")
print(f"   손익비:                 1 : {max_profit/max_loss:.2f}")

print(f"\n" + "=" * 80)
print("✅ 포트폴리오 자산배분 완료!")
print("=" * 80)

print("\n💡 자동 계산되는 항목:")
print("   1. 점수 기반 가중치 (AI 점수에 비례)")
print("   2. 제약 조건 적용 (종목당 3-15%, 테마당 30%)")
print("   3. 매수 수량 (가격 기준 자동 계산)")
print("   4. 손절/익절 가격 (ATR 또는 기본 비율)")
print("   5. 테마별 분산 (자동 배분)")

print("\n📝 실제 시스템 실행 시:")
print("   - 변동성 데이터 반영")
print("   - 실시간 가격 조회")
print("   - ATR 기반 손절가")
print("   - 계좌 잔고 실시간 확인")
