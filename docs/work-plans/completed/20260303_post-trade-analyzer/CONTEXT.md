# CONTEXT: Post-Trade Analyzer

## 변경 이유
매도 판단의 사후 검증 시스템이 없어, 매매 전략 개선을 위한 피드백 루프가 부재.

## 현재 코드 상태

### trade_reviews 테이블 (database.py:289-316)
- 매도 시 자동 생성 (`_close_position_in_db`, `_save_partial_sell_to_db`)
- 컬럼: trade_id, stock_code, stock_name, buy/sell_date/price, shares, hold_days, profit_rate/amount, sell_reason, strategy_type, trailing_level, max_profit_during_hold, theme, ai_review(NULL), lesson_learned(NULL)

### 기존 CRUD (database.py:1046-1063)
- `get_pending_trade_reviews()`: ai_review IS NULL인 레코드 조회
- `update_trade_review_ai(review_id, ai_review, lesson)`: ai_review + lesson_learned 업데이트

### Claude 분석 패턴 (modules/ai_verifier/claude_analyzer.py)
- `_get_api_key()` → Anthropic API 키
- `_get_model()` → claude-sonnet-4-6 기본
- `_parse_response()` → JSON 블록 추출
- `analyze_stock_async()` → 비동기 분석
- MAX_TOKENS=1500, TEMPERATURE=0.3

### yfinance 패턴 (modules/backtester/data_loader.py:197-246)
- 종목코드.KS → 데이터 없으면 .KQ 시도
- yf.Ticker(ticker_symbol).history(start=..., end=...)
- 컬럼: open, high, low, close, volume

### KIS API 패턴 (modules/stock_screener/kis_api.py:423-505)
- `get_daily_price(stock_code, period="D", count=60)`
- `_rate_limit()` 호출 필수
- `_safe_int()`, `_safe_float()` 사용
- 반환: [{date, open, high, low, close, volume, trade_value}, ...]

### 스케줄러 패턴 (scheduler.py)
- 콜백 슬롯: `self.on_xxx: Optional[Callable] = None`
- `_run_xxx()` 메서드 + `@_skip_on_holiday` 데코레이터
- `CronTrigger(hour=N, minute=M, day_of_week='mon-fri', timezone=_KST_TZ)`

### main.py 패턴
- `_setup_scheduler_callbacks()` (라인 281)에서 콜백 연결
- TradingSystem 클래스 내부에 메서드 추가

## 영향 범위
- **직접**: database.py, scheduler.py, main.py, telegram_notifier.py
- **간접**: 없음 (새 모듈은 독립적, 기존 로직 변경 없음)

## 핵심 주의사항
- `now_kst()` 사용 (datetime.now() 금지)
- KIS API 호출 시 `_rate_limit()` 준수
- DB 파싱 시 `_safe_int()`/`_safe_float()` 사용
- 실제 DB 경로: `data/trading.db`
