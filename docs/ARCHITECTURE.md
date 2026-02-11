# 한국 주식 AI 스윙 트레이딩 시스템 - 아키텍처 문서

## 1. 시스템 개요

| 항목 | 내용 |
|------|------|
| 목적 | AI 기반 한국 주식 스윙 트레이딩 자동화 |
| 인프라 | GCP VM (Ubuntu, UTC 타임존) |
| 언어 | Python 3.10+ (async/await) |
| 코드량 | ~22,000 lines (코어 5 + 모듈 19 + 스크립트 50+) |
| DB | SQLite (WAL 모드) |
| API | KIS (한국투자증권), Claude (Anthropic), DART (공시), Naver |
| 알림 | Telegram Bot |
| 스케줄링 | APScheduler (AsyncIOScheduler, timezone=Asia/Seoul) |

---

## 2. 디렉토리 구조

```
korean_stock_ai_trading/
├── main.py                 [992 lines]  메인 엔트리 & TradingSystem 클래스
├── config.py               [498 lines]  Pydantic 설정 관리
├── scheduler.py            [410 lines]  APScheduler 오케스트레이션 (8개 잡)
├── database.py             [653 lines]  SQLite 스키마 & CRUD
├── logger.py               [303 lines]  loguru 구조화 로깅
│
├── modules/
│   ├── theme_analyzer/     [3,085 lines] 테마 분석 & 7일 로테이션
│   ├── stock_screener/     [2,192 lines] 종목 스크리닝 & 필터
│   ├── ai_verifier/        [1,740 lines] Claude AI 종목 검증
│   ├── morning_filter/     [2,471 lines] 장 초반 실시간 필터
│   ├── portfolio_optimizer/ [1,876 lines] 포트폴리오 최적화
│   ├── trading_engine/     [3,917 lines] 주문 실행 & 모니터링
│   ├── reporter/           [1,802 lines] 텔레그램 & 리포트
│   ├── rebalancer/         [512 lines]   리밸런싱
│   └── backtester/         [1,480 lines] 백테스트 엔진
│
├── scripts/                백테스트 & 유틸리티 스크립트
├── data/trading.db         SQLite 데이터베이스
├── logs/                   로그 파일 (일별 로테이션)
└── .env                    API 키 (gitignored)
```

---

## 3. 일일 트레이딩 파이프라인

```
08:00  테마 로테이션 체크 (7일 주기, -20% 급락, +15% 서지)
08:30  테마 분석 (뉴스 크롤링 → 점수 산정 → 상위 4개 테마 선정)
09:05  종목 스크리닝 (실시간 데이터 → 필터 → AI 검증 → 후보풀 생성)
09:05  장 초반 관찰 시작 (20분간 갭/수급/거래량/체결강도 관찰)
09:25  매수 실행 (빈 슬롯에 신규 매수, 슬롯당 TOTAL_CAPITAL/MAX_POSITIONS)
09:26  실시간 모니터링 시작 (WebSocket 실시간 가격 수신)
       └─ 손절(-7%) / 분할익절(+10,+15,+20%) / 트레일링스탑(+8%→L1~L3)
       └─ 최대 보유기간 체크 (수익 14일, 손실 7일)
       └─ 30분마다 상태 로그 출력
15:30  모니터링 종료
15:35  장 마감 정리
16:00  일일 리포트 텔레그램 발송
```

---

## 4. 핵심 전략 파라미터

### 4.1 포지션 관리

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| TOTAL_CAPITAL | 1,500,000원 | 총 투자 자본 (실전 테스트) |
| MAX_POSITIONS | 5 | 최대 보유 종목 수 |
| 종목당 배분 | 300,000원 | TOTAL_CAPITAL / MAX_POSITIONS |
| MIN_POSITION_WEIGHT | 5% | 종목당 최소 비중 |
| MAX_POSITION_WEIGHT | 25% | 종목당 최대 비중 |
| MAX_THEME_WEIGHT | 40% | 테마당 최대 비중 |

### 4.2 매도 전략 (스윙)

보유 종목은 **손절/트레일링/타임아웃**으로만 매도. AI 추천 변경으로 매도하지 않음.

| 전략 | 조건 | 동작 |
|------|------|------|
| **손절** | -7% | 시장가 전량 매도 |
| **분할 익절 1** | +10% | 보유량 30% 매도 |
| **분할 익절 2** | +15% | 보유량 30% 매도 |
| **분할 익절 3** | +20% | 잔여 전량 매도 |
| **트레일링 L1** | +8% 진입 | 최고가 대비 -5% 하락 시 매도, 손절가 → 매수가로 이동 |
| **트레일링 L2** | +15% 진입 | 최고가 대비 -3% 하락 시 매도 |
| **트레일링 L3** | +25% 진입 | 최고가 대비 -2% 하락 시 매도 |
| **최대 보유 (수익)** | 14일 (수익률 +5% 이상) | 시장가 전량 매도 |
| **최대 보유 (손실)** | 7일 (수익률 +5% 미만) | 시장가 전량 매도 |

### 4.3 테마 로테이션

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| TOP_THEME_COUNT | 4 | 선정할 상위 테마 수 |
| THEME_REVIEW_DAYS | 7일 | 로테이션 주기 |
| THEME_CHANGE_THRESHOLD | -20% | 점수 급락 시 즉시 교체 |
| THEME_SURGE_THRESHOLD | +15% | 다른 테마 급등 시 교체 |

### 4.4 스크리닝 필터

| 필터 | 조건 | Hard/Soft |
|------|------|-----------|
| 거래대금 | ≥ 20억원 | Hard (미달 시 제외) |
| RSI | 30~70 (>70 과열 제외) | Hard |
| 거래량비율 | ≥ 0.7x (20일 평균 대비) | Hard |
| 부채비율 | ≤ 200% | Soft (정보만) |
| 수급 | 외국인+기관 순매수 | Soft (정보만) |

### 4.5 장 초반 필터 (09:05~09:25)

| 필터 | 기본값 | 설명 |
|------|--------|------|
| 갭 필터 | ±3% | 시가 vs 전일종가 (동적 조정) |
| 수급 필터 | 순매수 ≥ 0 | 외국인+기관 |
| 체결강도 | ≥ 45% | 매수/매도 비율 |
| 거래량 필터 | ≥ 0.5x 예상거래량 | 시간비례 거래량 |
| 관찰주기 | 3분 × 6회 (20분) | 실시간 모니터링 |

---

## 5. 스코어링 알고리즘

### 5.1 최종 점수 (Final Score)

```
Final Score = Supply(40%) + Technical(30%) + Theme(20%) + AI(10%)
```

### 5.2 수급 점수 (Supply Score, 0~100)

```
가중합 = 외국인순매수(억원) × 0.6 + 기관순매수(억원) × 0.4
점수 = ((가중합 + 50) / 100) × 100
// -50억 → 0점, 0억 → 50점, +50억 → 100점
```

### 5.3 기술 점수 (Technical Score, 0~100)

```
RSI (40점)   : 40~60 → 40점 | 30~40/60~70 → 30점 | <30 → 20점 | >70 → 10점
거래량 (30점): ≥2.0x → 30점 | ≥1.5x → 25점 | ≥1.2x → 20점 | <1.0x → 5점
이평선 (30점): 정배열(5>20>60) → 30점 | 중립 → 15점 | 역배열 → 0점
```

### 5.4 AI 감성 점수 (0~10)

Claude API가 종목별 뉴스/공시/기술 지표를 분석하여 0~10 점수 부여.
- 0~3: 부정적 | 4~6: 중립 | 7~10: 긍정적
- Temperature: 0.3 (일관성 우선)
- 동시 호출: 5개 (Semaphore)

---

## 6. 데이터베이스 스키마

### 6.1 portfolio (보유/청산 포지션)

```sql
portfolio (
    id, date, stock_code, stock_name, theme,
    weight, shares, buy_price, current_price,
    stop_loss, take_profit, profit_rate, profit_amount,
    status VARCHAR(20),  -- 'holding' | 'closed' | 'replaced'
    created_at, updated_at
)
```

### 6.2 trades (매매 기록)

```sql
trades (
    id, date, time, stock_code, stock_name,
    action VARCHAR(10),  -- 'buy' | 'sell'
    shares, price, amount,
    reason VARCHAR(50),  -- 'buy signal' | 'stop_loss' | 'trailing_L1' | '리밸런싱' 등
    profit_rate, profit_amount, order_id,
    created_at
)
```

### 6.3 themes (테마 분석 결과)

```sql
themes (
    id, date, theme_name, score, momentum,
    supply_ratio, news_count, ai_sentiment,
    created_at, UNIQUE(date, theme_name)
)
```

### 6.4 stocks (종목 스크리닝 결과)

```sql
stocks (
    id, date, stock_code, stock_name, theme,
    supply_score, technical_score, ai_sentiment, ai_reason,
    final_score, selected, created_at,
    UNIQUE(date, stock_code)
)
```

### 6.5 performance (일별 성과)

```sql
performance (
    id, date UNIQUE, total_value, total_cost, cash,
    daily_return, cumulative_return,
    win_count, loss_count, win_rate,
    mdd, sharpe_ratio, num_positions, created_at
)
```

---

## 7. 모듈별 데이터 플로우

```
┌──────────────────────────────────────────────────────────────────┐
│ 08:30 THEME ANALYSIS                                             │
│  Naver 크롤링 → 테마 점수 산정 → Claude 감성분석 → 상위 4개 선정  │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 09:05 STOCK SCREENING                                            │
│  테마별 종목 수집 → KIS API 실시간 데이터 → 4단계 필터            │
│  (수급 → 기술 → 유동성 → 펀더멘탈) → 점수 계산 → 후보풀 생성     │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 09:05 AI VERIFICATION                                            │
│  뉴스 크롤링 (7일) → Claude 개별 종목 분석 → 감성 점수 (0-10)    │
│  → 최종점수 = 수급40% + 기술30% + 테마20% + AI10%                │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 09:05~09:25 MORNING FILTER                                       │
│  갭 필터(±3%) → 수급 필터 → 거래량 필터 → 체결강도 필터(>45%)    │
│  동적 갭: 강세장 +1% 완화, 약세장 -1% 강화                      │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 09:25 BUY EXECUTION                                              │
│  DB 보유종목 로드 → 빈 슬롯 계산 → 슬롯당 배분(CAPITAL/MAX_POS) │
│  → 포트폴리오 최적화 → 시장가 매수 → 체결 대기 → DB 저장         │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 09:26~15:30 MONITORING (V2)                                      │
│  WebSocket 실시간 가격 → 매초 손익 체크                          │
│  ├─ 손절 (-7%): 시장가 전량 매도                                │
│  ├─ 분할 익절 (+10/15/20%): 30%/30%/전량 매도                   │
│  ├─ 트레일링 스탑: L1(-5%) / L2(-3%) / L3(-2%)                 │
│  └─ 보유기간 초과 (수익14일/손실7일): 시장가 전량 매도           │
│  30분마다 상태 로그 출력                                         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 8. 주요 클래스 & 메서드 맵

### 8.1 main.py — TradingSystem

| 메서드 | 라인 | 스케줄 | 설명 |
|--------|------|--------|------|
| `check_theme_rotation()` | 745 | 08:00 | 테마 로테이션 체크 |
| `run_theme_analysis()` | 229 | 08:30 | 테마 분석 실행 |
| `run_stock_screening()` | 347 | 09:05 | 종목 스크리닝 + AI 검증 |
| `execute_buy_orders()` | 496 | 09:25 | 빈 슬롯 매수 실행 |
| `start_monitoring()` | 685 | 09:26 | 실시간 모니터링 시작 |
| `stop_monitoring()` | 707 | 15:30 | 모니터링 종료 |
| `send_daily_report()` | 801 | 16:00 | 일일 리포트 발송 |

### 8.2 trading_engine.py — TradingEngine

| 메서드 | 라인 | 설명 |
|--------|------|------|
| `execute_portfolio()` | 124 | 포트폴리오 매수 실행 + 체결 대기 |
| `execute_sell_orders()` | 278 | 매도 실행 + 체결 대기 + 손익 계산 |
| `_wait_for_fills()` | 233 | 주문 체결 대기 (60초 타임아웃) |
| `_save_trades()` | 445 | 매매 기록 DB 저장 |
| `_save_positions()` | 460 | 포지션 DB 저장 |
| `get_balance()` | 430 | KIS API 잔고 조회 |
| `get_orderable_cash()` | 434 | 주문가능현금 조회 |

### 8.3 portfolio_monitor_v2.py — PortfolioMonitorV2

| 메서드 | 라인 | 설명 |
|--------|------|------|
| `start_monitoring()` | 295 | WebSocket 구독 + 모니터링 루프 시작 |
| `_monitor_loop()` | 339 | 매초 체크 + 30분 상태 로그 |
| `_on_price_update()` | 360 | WebSocket 가격 콜백 |
| `_check_all_positions()` | 387 | 전 포지션 손익 체크 |
| `_check_stop_loss()` | 463 | 손절 조건 체크 |
| `_check_trailing_stop()` | 682 | 트레일링 스탑 체크 |
| `_check_max_hold_days()` | 731 | 보유기간 체크 |
| `_log_status()` | 361 | 30분 간격 상태 로그 |

### 8.4 kis_order_api.py — KISOrderApi

| 메서드 | 라인 | 설명 |
|--------|------|------|
| `buy_market_order()` | 266 | 시장가 매수 주문 |
| `sell_market_order()` | 327 | 시장가 매도 주문 |
| `get_balance()` | 676 | 잔고 조회 (종목별 평가) |
| `get_orderable_cash()` | 624 | 주문가능현금 (D+2 정산 반영) |
| `get_order_status()` | 541 | 주문 체결 상태 조회 |
| `get_access_token()` | 153 | OAuth2 토큰 (23시간 캐시) |

---

## 9. 타임존 주의사항

- **서버**: GCP VM (UTC)
- **시장**: 한국 (KST = UTC+9)
- **규칙**: `datetime.now()` 사용 금지 → `from config import now_kst` 사용
- **CronTrigger**: `timezone="Asia/Seoul"` 명시 필수
- **KIS API**: 응답 시간은 KST 기준

---

## 10. 백테스트 결과

| 전략 | 수익률 | CAGR | 상태 |
|------|--------|------|------|
| 테마 모멘텀 + 트레일링 | **+261%** | **51.7%** | 현재 채택 |
| 52주 신고가 + 수급 | 실패 | - | 프록시 데이터 불안정 |
| 수급 모멘텀 (MFI/OBV) | 실패 | - | yfinance 기관 데이터 부재 |
| 듀얼 모멘텀 | 미정 | - | 다음 테스트 예정 |

**최적화된 파라미터** (백테스트 기반):
- 손절: -7% (vs -5%, 더 나은 성과)
- 테마 로테이션: 7일 (vs 14일, +75% 개선)
- 트레일링: L1(+8%,-5%), L2(+15%,-3%), L3(+25%,-2%)

---

## 11. 에러 처리 & 복원력

| 영역 | 전략 |
|------|------|
| KIS API 토큰 | 23시간 캐시 + 403 시 3회 재시도 (지수 백오프) |
| 주문 실행 | 3회 재시도, 체결 대기 60초 타임아웃 |
| WebSocket | 자동 재연결 (최대 5회), 하트비트 30초 |
| 텔레그램 | Markdown 실패 시 plain text 폴백 |
| PID 잠금 | 다중 인스턴스 방지 (`trading_system.pid`) |
| DB | WAL 모드 + 컨텍스트 매니저 (자동 커밋/롤백) |
| 시그널 | SIGINT/SIGTERM → 우아한 종료 |

---

## 12. 현재 운영 상태 (2026-02-11 기준)

- **모드**: 실전투자 (IS_MOCK=false)
- **자본**: 1,500,000원
- **MAX_POSITIONS**: 5
- **보유**: LG화학 1주 (323,500원 매수, -0.3%)
- **가용현금**: ~1,025,000원 (빈 슬롯 4개)
- **매도 방식**: 손절/트레일링/타임아웃만 (리밸런싱 매도 제거됨)
