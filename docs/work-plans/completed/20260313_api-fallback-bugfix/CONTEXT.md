# CONTEXT: API 폴백 및 None 방어 버그 수정

## 변경 이유
- KIS REST API 일시 장애 시 봇 전체 기능 마비되는 구조적 취약점
- DB에 NULL 데이터로 인한 TypeError (3/9~3/13 일일 리포트 미발송)

## 현재 코드 상태

### 버그 1: performance_calculator.py:305
```python
profit_rate = trade.get("profit_rate", 0)  # 키 존재, 값 None → None 반환
if profit_rate > 0:  # None > 0 → TypeError
```

### 버그 2: telegram_notifier.py:1064-1065
```python
price_info = await asyncio.to_thread(kis.get_current_price, stock_code)
current_price = price_info.get('price', buy_price) if price_info else buy_price
# API 실패 → None → buy_price → 수익률 0%
```

### 버그 3: dashboard_service.py:110-111
동일 패턴

### 버그 4: screener.py:186-195
```python
stock_info = kis_api.get_stock_full_info(code)
if not stock_info:
    # retry 없이 즉시 포기
    continue
```

### 버그 5: DB NULL 데이터
trades 테이블에 profit_rate=NULL 2건 (2026-02-11)

## 영향 범위
- 직접: 텔레그램 포트폴리오, 대시보드, 일일 리포트, 스크리닝
- 간접: 없음 (각 수정이 독립적)
