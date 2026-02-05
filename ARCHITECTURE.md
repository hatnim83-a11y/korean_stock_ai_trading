# 시스템 아키텍처 문서
**Korean Stock AI Swing Trading System v2.0.0**

---

## 목차
1. [시스템 개요](#1-시스템-개요)
2. [디렉토리 구조](#2-디렉토리-구조)
3. [핵심 모듈](#3-핵심-모듈)
4. [데이터 흐름](#4-데이터-흐름)
5. [일일 스케줄](#5-일일-스케줄)
6. [하이브리드 전략 (v2.0.0)](#6-하이브리드-전략-v200)
7. [설정 관리](#7-설정-관리)
8. [데이터베이스 스키마](#8-데이터베이스-스키마)
9. [외부 API 연동](#9-외부-api-연동)
10. [배포 및 운영](#10-배포-및-운영)

---

## 1. 시스템 개요

### 1.1 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        GCP VM (Ubuntu 22.04)                            │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    main.py (TradingSystem)                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │   │
│  │  │  Scheduler  │  │   Notifier  │  │     Database (SQLite)   │  │   │
│  │  │ (APScheduler)│  │ (Telegram)  │  │                         │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └────────────┬────────────┘  │   │
│  └─────────┼────────────────┼──────────────────────┼────────────────┘   │
│            │                │                      │                    │
│  ┌─────────▼────────────────▼──────────────────────▼────────────────┐   │
│  │                         Modules Layer                             │   │
│  │                                                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │Theme Analyzer│  │Stock Screener│  │    AI Verifier       │   │   │
│  │  │  - Crawlers  │  │  - KIS API   │  │  - News Crawler      │   │   │
│  │  │  - Scorer    │  │  - Filters   │  │  - DART API          │   │   │
│  │  │  - Rotator   │  │  - Screener  │  │  - Claude Analyzer   │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘   │   │
│  │                                                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │  Portfolio   │  │   Trading    │  │   Morning Filter     │   │   │
│  │  │  Optimizer   │  │   Engine     │  │  - Gap Filter        │   │   │
│  │  │  - Calculators│ │  - KIS Order │  │  - Supply Filter     │   │   │
│  │  │  - Optimizer │  │  - WebSocket │  │  - Volume Filter     │   │   │
│  │  │  - Formatter │  │  - Monitor V2│  │  - Dynamic Gap       │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘   │   │
│  │                                                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │  Rebalancer  │  │   Reporter   │  │    Backtester        │   │   │
│  │  │              │  │  - Telegram  │  │  - Engine            │   │   │
│  │  │              │  │  - Performance│ │  - Strategy Simulator│   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘   │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                    │                │                │
                    ▼                ▼                ▼
           ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
           │  KIS API      │ │  Claude API   │ │  Telegram API │
           │  (한국투자증권)│ │  (Anthropic)  │ │               │
           │  - REST       │ │               │ │               │
           │  - WebSocket  │ │               │ │               │
           └───────────────┘ └───────────────┘ └───────────────┘
```

### 1.2 기술 스택

| 구분 | 기술 | 버전 | 용도 |
|------|------|------|------|
| Language | Python | 3.10+ | 메인 개발 언어 |
| Scheduler | APScheduler | 3.10+ | 일일 작업 스케줄링 |
| Database | SQLite | 3.x | 데이터 저장 |
| HTTP Client | httpx | 0.25+ | API 호출 |
| WebSocket | websockets | 12.0+ | 실시간 시세 |
| Data Processing | pandas, numpy | - | 데이터 분석 |
| Settings | pydantic-settings | - | 환경 변수 관리 |

---

## 2. 디렉토리 구조

```
korean_stock_ai_trading/
├── main.py                     # 메인 엔트리포인트 (TradingSystem 클래스)
├── scheduler.py                # APScheduler 스케줄링 모듈
├── config.py                   # 환경 변수 및 설정 관리
├── database.py                 # SQLite 데이터베이스 연결 및 ORM
├── logger.py                   # 로깅 설정
├── requirements.txt            # Python 패키지 의존성
├── .env                        # 환경 변수 (gitignore)
├── .gitignore                  # Git 제외 파일
├── README.md                   # 프로젝트 소개
├── ARCHITECTURE.md             # 아키텍처 문서 (본 문서)
├── Korean stock ai trading prd.md  # 제품 요구사항 문서
├── trading_system.service      # systemd 서비스 파일
│
├── modules/                    # 핵심 비즈니스 로직 모듈
│   ├── __init__.py
│   │
│   ├── theme_analyzer/         # 테마 분석 모듈
│   │   ├── __init__.py
│   │   ├── crawlers.py         # 네이버/한경 크롤러
│   │   ├── scorer.py           # 테마 점수 계산
│   │   ├── selector.py         # Top N 테마 선정
│   │   ├── ai_analyzer.py      # Claude AI 테마 분석
│   │   └── theme_rotator.py    # 테마 로테이션 관리 (v2.0)
│   │
│   ├── stock_screener/         # 종목 스크리닝 모듈
│   │   ├── __init__.py
│   │   ├── kis_api.py          # KIS API 래퍼
│   │   ├── filters.py          # 수급/기술적/재무 필터
│   │   └── screener.py         # 통합 스크리닝
│   │
│   ├── ai_verifier/            # AI 검증 모듈
│   │   ├── __init__.py
│   │   ├── news_crawler.py     # 네이버 금융 뉴스 크롤러
│   │   ├── dart_api.py         # DART 공시 API
│   │   ├── claude_analyzer.py  # Claude AI 종목 분석
│   │   └── verifier.py         # 통합 검증 파이프라인
│   │
│   ├── portfolio_optimizer/    # 포트폴리오 최적화 모듈
│   │   ├── __init__.py
│   │   ├── calculators.py      # 변동성/ATR/손익 계산
│   │   ├── optimizer.py        # 가중치 최적화
│   │   └── formatter.py        # 출력 포맷팅
│   │
│   ├── trading_engine/         # 매매 실행 모듈
│   │   ├── __init__.py
│   │   ├── kis_order_api.py    # KIS 주문 API
│   │   ├── kis_websocket.py    # 실시간 시세 WebSocket
│   │   ├── trading_engine.py   # 매매 엔진
│   │   ├── portfolio_monitor.py    # 기본 모니터 (레거시)
│   │   └── portfolio_monitor_v2.py # V2 모니터 (분할익절+트레일링)
│   │
│   ├── morning_filter/         # 장 초반 필터 모듈 (v2.0)
│   │   ├── __init__.py
│   │   ├── morning_screener.py # 장 초반 스크리너
│   │   ├── gap_filter.py       # 시초가 갭 필터
│   │   ├── dynamic_gap.py      # 동적 갭 기준
│   │   ├── supply_filter.py    # 당일 수급 필터
│   │   ├── volume_filter.py    # 거래량 필터
│   │   └── realtime_monitor.py # 실시간 모니터
│   │
│   ├── rebalancer/             # 리밸런싱 모듈
│   │   ├── __init__.py
│   │   └── rebalancer.py       # 일일 리밸런싱
│   │
│   ├── reporter/               # 리포팅 모듈
│   │   ├── __init__.py
│   │   ├── performance_calculator.py  # 성과 지표 계산
│   │   ├── report_generator.py        # 리포트 생성
│   │   └── telegram_notifier.py       # 텔레그램 알림
│   │
│   └── backtester/             # 백테스팅 모듈
│       ├── __init__.py
│       ├── backtest_engine.py  # 백테스트 엔진
│       ├── data_loader.py      # 과거 데이터 로더
│       └── strategy_simulator.py  # 전략 시뮬레이터
│
├── data/                       # 데이터 저장소
│   └── trading.db              # SQLite 데이터베이스
│
├── logs/                       # 로그 파일
│   └── *.log
│
├── scripts/                    # 유틸리티 스크립트
│
├── tests/                      # 테스트 코드
│
└── backtesting/                # 백테스팅 결과
```

---

## 3. 핵심 모듈

### 3.1 TradingSystem (main.py)

시스템의 메인 엔트리포인트로, 모든 모듈을 통합 관리합니다.

```python
class TradingSystem:
    """
    주요 컴포넌트:
    - scheduler: APScheduler 기반 스케줄러
    - trading_engine: 매매 실행 엔진
    - monitor: PortfolioMonitorV2 (분할 익절 + 트레일링)
    - morning_screener: 장 초반 스크리너
    - theme_rotator: 테마 로테이션 관리
    - notifier: 텔레그램 알림
    - db: SQLite 데이터베이스
    """
```

### 3.2 TradingScheduler (scheduler.py)

APScheduler를 사용한 일일 작업 스케줄링을 담당합니다.

**등록된 스케줄:**
| 시간 | Job ID | 설명 |
|------|--------|------|
| 08:00 | theme_check | 테마 로테이션 체크 |
| 08:30 | daily_analysis | 일일 분석 (테마→스크리닝→AI검증) |
| 09:00 | morning_observation | 장 초반 관찰 시작 |
| 09:25 | execute_buy | 필터링 후 자동 매수 |
| 09:26 | monitoring_start | 실시간 모니터링 시작 |
| 15:30 | monitoring_stop | 모니터링 종료 |
| 15:35 | market_close | 장 마감 정리 |
| 16:00 | daily_report | 일일 리포트 발송 |

### 3.3 Theme Analyzer

테마 데이터 수집, 점수화, 로테이션 관리를 담당합니다.

**주요 파일:**
- `crawlers.py`: 네이버/한경 테마 크롤링
- `scorer.py`: 모멘텀/수급/뉴스/AI 점수 계산
- `selector.py`: Top N 테마 선정
- `theme_rotator.py`: 1주 단위 테마 로테이션 (v2.0, 14일 대비 +75% 수익)

### 3.4 Stock Screener

선정된 테마의 종목들을 수급/기술적/재무 조건으로 필터링합니다.

**필터링 단계:**
1. 수급 필터: 외국인/기관 순매수
2. 기술적 필터: MA 정배열, 거래량, RSI
3. 재무 필터: 부채비율, 영업이익률

### 3.5 AI Verifier

Claude AI를 활용한 뉴스/공시 분석으로 최종 검증합니다.

**분석 항목:**
- 투자 매력도 (0-10)
- 매수 추천 (Yes/No/Hold)
- 리스크 요인
- 목표 수익률

### 3.6 Portfolio Optimizer

검증된 종목의 투자 비중과 손익 목표를 계산합니다.

**최적화 전략:**
- Equal Weight: 동일 가중
- Score Based: 점수 비례 (기본)
- Risk Parity: 리스크 동일 배분

### 3.7 Trading Engine

KIS API를 통한 실제 매매 실행을 담당합니다.

**주요 컴포넌트:**
- `kis_order_api.py`: 주문 실행
- `kis_websocket.py`: 실시간 시세
- `portfolio_monitor_v2.py`: 분할 익절 + 트레일링 스탑

### 3.8 Morning Filter (v2.0)

장 초반 25분간 관찰 후 필터링합니다.

**필터링 조건:**
- 시초가 갭: ±3% 이내
- 당일 수급: 외국인+기관 순매수
- 거래량: 정상 범위

---

## 4. 데이터 흐름

### 4.1 일일 분석 파이프라인 (08:30)

```
테마 크롤링 (crawlers.py)
    │
    ▼
테마 점수화 (scorer.py)
    │
    ▼
테마 로테이션 체크 (theme_rotator.py)
    │
    ▼
Top 5 테마 선정 (selector.py)
    │
    ▼
종목 스크리닝 (screener.py)
    │
    ├── 수급 필터
    ├── 기술적 필터
    └── 재무 필터
    │
    ▼
AI 검증 (verifier.py)
    │
    ├── 뉴스 수집 (news_crawler.py)
    ├── 공시 수집 (dart_api.py)
    └── Claude 분석 (claude_analyzer.py)
    │
    ▼
포트폴리오 최적화 (optimizer.py)
    │
    ▼
관찰 후보 10-15개 선정
```

### 4.2 장 초반 관찰 → 매수 (09:00-09:25)

```
09:00 - 관찰 시작
    │
    ▼
시초가/수급/거래량 모니터링 (morning_screener.py)
    │
    ▼
09:25 - 필터링 실행
    │
    ├── 갭 필터 (gap_filter.py)
    ├── 수급 필터 (supply_filter.py)
    └── 거래량 필터 (volume_filter.py)
    │
    ▼
최종 5-8개 선정
    │
    ▼
자동 매수 실행 (trading_engine.py)
```

### 4.3 실시간 모니터링 (09:26-15:30)

```
WebSocket 시세 수신 (kis_websocket.py)
    │
    ▼
PortfolioMonitorV2 (portfolio_monitor_v2.py)
    │
    ├── 손절 체크 (-7%)
    │     └── 발동 시 → 즉시 매도
    │
    ├── 이익 추종 전략 (Let Profits Run)
    │     ├── +8% → 트레일링 L1 (5%) + 본전 손절
    │     ├── +15% → 트레일링 L2 (3%)로 강화
    │     └── +25% → 트레일링 L3 (2%)로 더 강화
    │     ※ 고정 익절 없음 - 끝까지 추종
    │
    └── 보유 기간 체크
          ├── 수익 +5% 이상 → 최대 14일
          └── 손실 중 → 최대 7일
    │
    ▼
텔레그램 알림 발송
```

---

## 5. 일일 스케줄

```
┌────────┬───────────────────────────────────────────────────────────────┐
│  시간  │                          작업 내용                             │
├────────┼───────────────────────────────────────────────────────────────┤
│ 08:00  │ 테마 로테이션 체크 (1주 단위)                                  │
├────────┼───────────────────────────────────────────────────────────────┤
│ 08:30  │ 일일 분석 시작                                                 │
│        │  └─ 테마 분석 → 종목 스크리닝 → AI 검증 → 후보 선정            │
├────────┼───────────────────────────────────────────────────────────────┤
│ 09:00  │ 장 초반 관찰 시작                                              │
│        │  └─ 시초가 / 수급 / 거래량 모니터링                            │
├────────┼───────────────────────────────────────────────────────────────┤
│ 09:25  │ 자동 매수 실행                                                 │
│        │  └─ 장 초반 필터링 후 최종 5-8개 매수                          │
├────────┼───────────────────────────────────────────────────────────────┤
│ 09:26  │ 실시간 모니터링 시작 (V2)                                      │
│  ~     │  └─ 분할 익절 / 트레일링 스탑 / 손절                           │
│ 15:30  │                                                               │
├────────┼───────────────────────────────────────────────────────────────┤
│ 15:35  │ 장 마감 정리                                                   │
│        │  └─ 미체결 주문 취소 / 포트폴리오 현황 정리                    │
├────────┼───────────────────────────────────────────────────────────────┤
│ 16:00  │ 일일 리포트 발송                                               │
│        │  └─ 텔레그램으로 성과 리포트 전송                              │
└────────┴───────────────────────────────────────────────────────────────┘
```

---

## 6. 하이브리드 전략 (v2.0.0)

### 6.1 이익 추종 전략 (Let Profits Run) - 신규

**백테스트 결과: +261.66% (3년), CAGR 51.70%, MDD -9.88%**

수익이 커질수록 트레일링을 강화하여 큰 수익을 끝까지 추종합니다.

```
+8% 도달  → 트레일링 L1 활성화 (고점 -5%) + 본전 손절
+15% 도달 → 트레일링 L2 강화 (고점 -3%)
+25% 도달 → 트레일링 L3 강화 (고점 -2%)

고정 익절 없음 - 무제한 상승 추종
```

**트레일링 레벨별 성과:**
| 레벨 | 조건 | 평균 수익률 |
|------|------|------------|
| L1 | +8%~15% | +4.90% |
| L2 | +15%~25% | +15.25% |
| L3 | +25%↑ | **+36.58%** |

### 6.2 분할 익절 전략 (레거시)

```
수익률 도달 시 분할 매도:

+10% 도달 → 보유 수량의 30% 매도 (1차 익절)
+15% 도달 → 보유 수량의 30% 매도 (2차 익절)
+20% 도달 → 남은 수량 전량 매도 (3차 익절)
```

### 6.3 트레일링 스탑 (레거시)

```
수익 중일 때 최고가 추적:

트레일링 스탑가 = 최고가 × (1 - 5%)

예시:
- 매수가: 10,000원
- 최고가: 12,000원 (수익률 +20%)
- 트레일링 스탑가: 11,400원
- 현재가가 11,400원 이하로 하락 시 → 전량 매도
```

### 6.4 보유 기간 관리

```
수익 상태 (+5% 이상):
  └─ 최대 14일까지 보유 가능

손실 상태:
  └─ 최대 7일 후 청산 고려
```

### 6.5 테마 로테이션 (1주 단위)

```
메인 테마 재평가 주기: 7일 (14일 대비 +75% 수익)

즉시 변경 조건:
  └─ 테마 점수 -20% 이상 하락 시

급등 테마 진입:
  └─ 새 테마 점수 +15% 이상 급등 시
```

### 6.6 장 초반 필터링 (09:00-09:25)

```
08:30 분석 후보 10-15개
    │
    ▼
09:00-09:25 관찰
    │
    ├── 갭 필터: 시초가 갭 ±3% 이내
    ├── 수급 필터: 외국인+기관 순매수
    └── 거래량 필터: 정상 거래량
    │
    ▼
최종 5-8개 매수
```

---

## 7. 설정 관리

### 7.1 config.py 주요 설정

모든 설정은 `config.py`의 `Settings` 클래스에서 관리되며, `.env` 파일에서 오버라이드 가능합니다.

**트레이딩 설정:**
| 설정 | 기본값 | 설명 |
|------|--------|------|
| TOTAL_CAPITAL | 10,000,000 | 총 투자 자본금 |
| MAX_POSITIONS | 10 | 최대 보유 종목 수 |
| MIN_POSITIONS | 5 | 최소 보유 종목 수 |
| DAILY_MAX_LOSS | 0.03 | 일일 최대 손실률 |

**손익 설정:**
| 설정 | 기본값 | 설명 |
|------|--------|------|
| DEFAULT_STOP_LOSS | -0.05 | 기본 손절률 (-5%) |
| TAKE_PROFIT_1 | 0.10 | 1차 익절률 (+10%) - 레거시 |
| TAKE_PROFIT_2 | 0.15 | 2차 익절률 (+15%) - 레거시 |
| TAKE_PROFIT_3 | 0.20 | 3차 익절률 (+20%) - 레거시 |
| TRAILING_STOP_PERCENT | 0.05 | 트레일링 스탑 (최고가 -5%) - 레거시 |

**이익 추종 전략 설정 (신규):**
| 설정 | 기본값 | 설명 |
|------|--------|------|
| ENABLE_PROFIT_TRAILING | True | 이익 추종 전략 활성화 |
| TRAIL_ACTIVATION_PCT | 0.08 | 트레일링 시작 수익률 (+8%) |
| TRAIL_LEVEL1_PCT | 0.05 | L1 트레일링 비율 (고점 -5%) |
| TRAIL_LEVEL2_THRESHOLD | 0.15 | L2 진입 수익률 (+15%) |
| TRAIL_LEVEL2_PCT | 0.03 | L2 트레일링 비율 (고점 -3%) |
| TRAIL_LEVEL3_THRESHOLD | 0.25 | L3 진입 수익률 (+25%) |
| TRAIL_LEVEL3_PCT | 0.02 | L3 트레일링 비율 (고점 -2%) |

**보유 기간 설정:**
| 설정 | 기본값 | 설명 |
|------|--------|------|
| MAX_HOLD_DAYS_PROFIT | 14 | 수익 시 최대 보유 기간 |
| MAX_HOLD_DAYS_LOSS | 7 | 손실 시 최대 보유 기간 |

**테마 로테이션 설정:**
| 설정 | 기본값 | 설명 |
|------|--------|------|
| THEME_REVIEW_DAYS | 7 | 테마 재평가 주기 (14일 대비 +75% 수익) |
| THEME_CHANGE_THRESHOLD | -0.20 | 즉시 변경 임계값 |

### 7.2 환경 변수 (.env)

```bash
# KIS API
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=12345678-01
KIS_CANO=12345678
KIS_ACNT_PRDT_CD=01
IS_MOCK=True

# Claude API
ANTHROPIC_API_KEY=sk-ant-xxxxx
CLAUDE_MODEL=claude-sonnet-4-5-20250929

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=123456789

# DART
DART_API_KEY=your_dart_key

# Database
DATABASE_PATH=./data/trading.db

# Logging
LOG_LEVEL=INFO
LOG_PATH=./logs
```

---

## 8. 데이터베이스 스키마

### 8.1 주요 테이블

**themes** - 테마 점수 이력
```sql
CREATE TABLE themes (
    id INTEGER PRIMARY KEY,
    date DATE,
    theme_name VARCHAR(50),
    score REAL,
    momentum REAL,
    supply_ratio REAL,
    news_count INTEGER,
    ai_sentiment REAL
);
```

**stocks** - 종목 스크리닝 이력
```sql
CREATE TABLE stocks (
    id INTEGER PRIMARY KEY,
    date DATE,
    stock_code VARCHAR(10),
    stock_name VARCHAR(50),
    theme VARCHAR(50),
    supply_score REAL,
    technical_score REAL,
    ai_sentiment REAL,
    final_score REAL
);
```

**portfolio** - 포트폴리오 현황
```sql
CREATE TABLE portfolio (
    id INTEGER PRIMARY KEY,
    date DATE,
    stock_code VARCHAR(10),
    stock_name VARCHAR(50),
    shares INTEGER,
    buy_price REAL,
    stop_loss REAL,
    take_profit REAL,
    trailing_stop REAL,
    status VARCHAR(20)  -- pending, holding, closed
);
```

**trades** - 매매 기록
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    date DATE,
    stock_code VARCHAR(10),
    action VARCHAR(10),  -- buy, sell
    shares INTEGER,
    price REAL,
    reason VARCHAR(50),  -- 손절, 익절, 트레일링, 기간초과
    profit_rate REAL
);
```

---

## 9. 외부 API 연동

### 9.1 KIS API (한국투자증권)

| 기능 | 엔드포인트 | 용도 |
|------|------------|------|
| 시세 조회 | /uapi/domestic-stock/v1/quotations | 현재가/일봉 조회 |
| 수급 조회 | /uapi/domestic-stock/v1/quotations/inquire-investor | 외국인/기관 수급 |
| 주문 | /uapi/domestic-stock/v1/trading | 매수/매도 주문 |
| WebSocket | ops.koreainvestment.com | 실시간 시세 |

### 9.2 Claude API (Anthropic)

| 용도 | 모델 |
|------|------|
| 테마 감성 분석 | claude-sonnet-4-5-20250929 |
| 종목 뉴스 분석 | claude-sonnet-4-5-20250929 |

### 9.3 DART API

| 용도 | 설명 |
|------|------|
| 공시 조회 | 최근 30일 공시 데이터 수집 |

### 9.4 Telegram Bot API

| 용도 | 설명 |
|------|------|
| 알림 발송 | 매수/매도/리포트 알림 |

---

## 10. 배포 및 운영

### 10.1 systemd 서비스

```ini
[Unit]
Description=Korean Stock AI Trading System
After=network.target

[Service]
Type=simple
User=hatni
WorkingDirectory=/home/hatni/korean_stock_ai_trading
ExecStart=/home/hatni/korean_stock_ai_trading/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 10.2 운영 명령어

```bash
# 서비스 시작
sudo systemctl start trading_system

# 서비스 상태 확인
sudo systemctl status trading_system

# 로그 확인
journalctl -u trading_system -f

# 서비스 중지
sudo systemctl stop trading_system
```

### 10.3 로그 관리

로그 파일 위치: `./logs/`

로그 레벨:
- DEBUG: 상세 디버깅 정보
- INFO: 일반 운영 정보
- WARNING: 경고 메시지
- ERROR: 에러 발생

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2025-02-04 | 초기 버전 |
| 2.0.0 | 2025-02-05 | 하이브리드 전략 추가 (분할익절, 트레일링, 테마로테이션, 장초반필터) |
| 2.1.0 | 2026-02-05 | 이익 추종 전략 추가 (Let Profits Run, 단계별 트레일링) |

---

**문서 작성일**: 2026-02-05
**작성자**: Claude Code
