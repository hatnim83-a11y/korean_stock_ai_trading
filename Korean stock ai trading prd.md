# 한국 주식 AI 스윙 트레이딩 시스템 PRD
**Product Requirements Document**
**Version 2.0.0** | 최종 수정: 2025-02-05

---

## 📋 목차
1. [프로젝트 개요](#프로젝트-개요)
2. [핵심 기능](#핵심-기능)
3. [v2.0 신규 기능](#v20-신규-기능)
4. [시스템 아키텍처](#시스템-아키텍처)
5. [상세 기능 명세](#상세-기능-명세)
6. [기술 스택](#기술-스택)
7. [개발 단계](#개발-단계)
8. [리스크 관리](#리스크-관리)
9. [성과 지표](#성과-지표)

---

## 🎯 프로젝트 개요

### 목적
한국 주식시장에서 AI 기반 테마 분석과 수급 데이터를 결합한 자동화 스윙 트레이딩 시스템 구축

### 배경
- 기존 비트코인 선물 시스템 트레이딩 성공 경험 보유
- OKX 거래소에서 Python 기반 자동매매 운영 중
- 한국 주식시장으로 확장하여 포트폴리오 다변화

### 목표
- **수익률**: 연 28-35%
- **승률**: 65% 이상
- **최대 낙폭(MDD)**: -15% 이내
- **평균 보유 기간**: 5-7일 (스윙 트레이딩)
- **포트폴리오**: 15개 종목 분산 투자

### 차별화 요소
1. **테마 우선 접근**: 시장 주도주 파악 후 종목 선정
2. **수급 중심 전략**: 외국인/기관 매수 종목 집중 공략
3. **AI 최종 검증**: Claude API로 뉴스/공시 분석하여 리스크 제거
4. **완전 자동화**: 테마 선정부터 매매까지 사람 개입 없이 운영

---

## 🔑 핵심 기능

### 1. 테마 분석 & 선정 시스템
**목적**: 시장을 주도하는 상위 3-5개 테마 자동 발굴

**입력 데이터**:
- 네이버 증권 인기 테마
- 한국경제 테마 정보
- 키움증권 HTS 테마
- 자체 정의 20개 섹터

**점수 계산 로직** (0-100점):
- 모멘텀 점수 (30점): 테마 내 평균 5일 수익률
- 수급 점수 (25점): 외국인+기관 순매수 종목 비율
- 뉴스 화제성 (20점): 최근 3일 뉴스 언급 빈도
- AI 감성 분석 (25점): Claude가 평가한 테마 전망 (0-10점 × 2.5)

**출력**:
```
1. 2차전지 (점수: 87.5/100)
   모멘텀: +5.2% | 수급양호: 68% | 뉴스: 127건 | AI전망: 8.5/10
   
2. AI반도체 (점수: 82.3/100)
   모멘텀: +3.8% | 수급양호: 71% | 뉴스: 95건 | AI전망: 8.0/10
   
3. K-방산 (점수: 79.1/100)
   ...
```

**실행 시점**: 매일 08:30

---

## 🆕 v2.0 신규 기능

### 하이브리드 전략 (v2.0.0)

v2.0에서는 수익 극대화와 리스크 관리를 위한 하이브리드 전략이 도입되었습니다.

#### 1. 분할 익절 전략
기존 단일 익절 방식에서 3단계 분할 익절로 개선:

```
+10% 도달 → 보유 수량 30% 매도 (1차 익절)
+15% 도달 → 보유 수량 30% 매도 (2차 익절)
+20% 도달 → 남은 수량 전량 매도 (3차 익절)
```

**장점**: 일부 수익 확정 + 추가 상승 기회 확보

#### 2. 트레일링 스탑
수익 중일 때 최고가를 추적하여 하락 시 수익 보호:

```
트레일링 스탑가 = 최고가 × (1 - 5%)

예시:
- 매수가: 10,000원
- 최고가: 12,000원 (+20%)
- 트레일링 스탑가: 11,400원
- 11,400원 이하 하락 시 → 전량 매도
```

#### 3. 장 초반 관찰 (09:00-09:25)
08:30 분석 후 바로 매수하지 않고 장 초반 25분간 관찰 후 매수:

```
08:30 분석 → 후보 10-15개 선정
    │
    ▼
09:00 장 시작 → 관찰 시작
    │
    ├── 시초가 갭 모니터링 (±3% 이내만 통과)
    ├── 당일 수급 모니터링 (외국인+기관 순매수)
    └── 거래량 모니터링
    │
    ▼
09:25 필터링 후 → 최종 5-8개 매수
```

**장점**: 급등락 종목 필터링, 당일 수급 확인

#### 4. 테마 로테이션 (2주 단위)
메인 테마를 2주 단위로 재평가하고 필요 시 변경:

```
재평가 주기: 14일

즉시 변경 조건:
- 메인 테마 점수 -20% 이상 하락

급등 테마 진입 조건:
- 새 테마 점수 +15% 이상 급등
```

#### 5. 보유 기간 관리

| 상태 | 최대 보유 기간 | 설명 |
|------|---------------|------|
| 수익 (+5% 이상) | 14일 | 추가 상승 기회 부여 |
| 손실 | 7일 | 조기 손절로 자금 회수 |

### 일일 스케줄 (v2.0)

| 시간 | 작업 |
|------|------|
| 08:00 | 테마 로테이션 체크 |
| 08:30 | 일일 분석 (테마→스크리닝→AI검증→후보선정) |
| 09:00 | 장 초반 관찰 시작 |
| 09:25 | 필터링 후 자동 매수 |
| 09:26~15:30 | 실시간 모니터링 (분할익절/트레일링/손절) |
| 15:35 | 장 마감 정리 |
| 16:00 | 일일 리포트 발송 |

---

### 2. 수급 기반 종목 스크리닝
**목적**: 각 테마 내에서 외국인/기관이 매수하는 종목 필터링

**1차 필터 - 수급 조건**:
- 외국인 5일 순매수 OR 기관 5일 순매수 (최소 1개 충족)
- 수급 점수 = (외국인 순매수액 + 기관 순매수액) / 1억원

**2차 필터 - 기술적 조건**:
- 이동평균선 정배열 (MA20 > MA60) OR 골든크로스 임박
- 거래량 증가 (당일 거래량 > 20일 평균 × 1.2)
- 과열 아님 (RSI < 75)

**3차 필터 - 재무 조건**:
- 부채비율 < 200%
- 영업이익률 > 0%

**출력**: 각 테마당 상위 5-10개 종목 (총 15-50개 후보)

**실행 시점**: 매일 08:35 (테마 선정 직후)

---

### 3. AI 뉴스 검증 시스템
**목적**: 최종 후보 종목의 리스크 제거 및 투자 매력도 평가

**입력**:
- 종목별 최근 7일 뉴스 (네이버 금융)
- 종목별 최근 30일 공시 (DART API)
- 테마 정보 및 현재 주가

**Claude AI 분석 요청**:
```
분석 항목:
1. 투자 매력도 점수 (0-10)
2. 매수 추천 여부 (Yes/No/Hold)
3. 핵심 근거 (2줄 이내)
4. 리스크 요인
5. 목표 수익률 예상 (%)

제외 조건:
- 악재 발생 (실적 악화, 횡령, 분식회계) → No
- 재료 소진된 테마주 → Hold
- 명확한 호재 없음 → Hold
```

**AI 응답 예시**:
```json
{
  "sentiment": 7.5,
  "recommend": "Yes",
  "reason": "신규 수주 1000억원, 외국인 지속 매수 중",
  "risk": "환율 변동성, 중국 경쟁 심화",
  "target_return": 15
}
```

**최종 점수 계산**:
- 수급 점수 (40%) + 테마 점수 (30%) + AI 점수 (30%)

**출력**: 점수 순위 상위 15-20개 종목

**실행 시점**: 매일 08:40-08:50 (병렬 처리로 10분 내 완료)

---

### 4. 포트폴리오 최적화 엔진
**목적**: 종목별 투자 비중 자동 계산 및 손익 목표 설정

**가중치 계산 알고리즘**:

**Step 1: 기본 가중치** (점수 기반)
```python
기본 가중치 = 해당 종목 점수 / 전체 종목 점수 합계
```

**Step 2: 변동성 조정** (리스크 패리티)
```python
변동성 가중치 = (1/변동성) / Σ(1/변동성)
# 변동성 낮을수록 많이 투자
```

**Step 3: 최종 가중치**
```python
최종 가중치 = 기본 가중치 × 0.7 + 변동성 가중치 × 0.3
```

**제약 조건**:
- 최소 투자: 3% (너무 작은 포지션 방지)
- 최대 투자: 15% (집중 리스크 방지)
- 동일 테마 제한: 30% 이내
- 목표 종목 수: 15개

**손절/익절 설정**:

**손절가 계산** (ATR 기반):
```python
손절가 = 현재가 - (ATR_14일 × 2)
# 단, -7% ~ -12% 범위 내
```

**익절가 계산** (리스크/리워드 1:2):
```python
손절 거리 = 현재가 - 손절가
익절가 = 현재가 + (손절 거리 × 2)
# AI 목표가와 비교하여 낮은 값 선택
```

**출력 예시**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 최적화된 포트폴리오 (총 10,000,000원)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 2차전지 테마 (28.5%)
────────────────────────────────────────────────────
LG에너지솔루션 (373220)
  매수가:  420,000원  |  수량:     45주  |  투자액:  1,890,000원 ( 8.9%)
  손절:  389,000원 (-7.4%)  |  익절:  482,000원 (+14.8%)
  AI점수: 8.2/10

엘앤에프 (066970)
  매수가:  180,000원  |  수량:    110주  |  투자액:  1,980,000원 ( 9.3%)
  손절:  167,000원 (-7.2%)  |  익절:  206,000원 (+14.4%)
  AI점수: 7.8/10
...

🎯 AI반도체 테마 (25.3%)
────────────────────────────────────────────────────
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
총 15개 종목  |  현금: 150,000원
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**실행 시점**: 매일 08:50-08:58

---

### 5. 자동 매매 실행 엔진
**목적**: 09:00 시장 개장과 동시에 포트폴리오 매수 자동 실행

**매수 로직**:
1. 신규 포지션 확인 (전날 보유 종목 제외)
2. KIS API를 통해 시장가 매수 주문
3. 주문 간 0.5초 간격 (API 제한 준수)
4. 체결 확인 (1분 후)

**에러 처리**:
- 주문 실패 시 3회 재시도
- 3회 실패 시 텔레그램 알림 + 해당 종목 스킵
- 체결 가격이 목표가 ±3% 벗어나면 취소 후 재주문

**실행 시점**: 매일 09:00

---

### 6. 실시간 모니터링 시스템
**목적**: 장중 실시간 가격 추적 및 손익 조건 충족 시 자동 청산

**모니터링 대상**:
- 모든 보유 종목 실시간 시세 (WebSocket)
- 외국인/기관 실시간 수급 (매 1분)

**자동 매도 트리거**:

**1) 손절 매도**:
```python
if 현재가 <= 손절가:
    즉시 시장가 매도
```

**2) 익절 매도**:
```python
if 현재가 >= 익절가:
    즉시 시장가 매도
```

**3) 수급 이탈 매도** (중요!):
```python
# 조건 A: 외국인 당일 누적 -10억 이상 매도
if 외국인_당일누적 < -10억:
    선제 매도

# 조건 B: 외국인+기관 모두 매도 전환
if 외국인_당일누적 < 0 AND 기관_당일누적 < 0:
    선제 매도
```

**4) 트레일링 스탑** (선택):
```python
if 수익률 > 10%:
    손절가 = 현재가 × 0.95  # 5% 하락까지 허용
    # 손절가를 계속 상향 조정
```

**알림**:
- 매도 실행 시 텔레그램 즉시 알림
- 손익률, 수익금액, 매도 사유 포함

**실행 시점**: 09:01 - 15:20 (장중 상시)

---

### 7. 일일 리밸런싱 시스템
**목적**: 익절/손절로 청산된 종목의 빈자리를 다음날 새 종목으로 채우기

**리밸런싱 조건**:
```python
if 사용가능_현금 >= 총자산 × 0.05:  # 현금 5% 이상
    리밸런싱 실행
else:
    스킵
```

**리밸런싱 프로세스**:
1. 현재 보유 종목 확인
2. 사용 가능 현금 계산
3. **Phase 1-3 재실행** (테마 선정 → 수급 스크리닝 → AI 검증)
4. 이미 보유 중인 종목 제외
5. 새로운 종목 포트폴리오 구성
6. 09:00에 추가 매수 실행

**목표**:
- 항상 15개 종목 풀 포지션 유지
- 동적으로 베스트 종목으로 교체

**실행 시점**: 매일 08:30 (다음날 아침)

---

### 8. 성과 리포팅 시스템
**목적**: 일일/주간/월간 성과 자동 집계 및 알림

**일일 리포트** (15:30 생성):
```
📊 일일 성과 리포트 (2025-02-04)

💰 전체 수익
총 평가액: 10,450,000원
총 투자액: 10,000,000원
오늘 수익: +450,000원 (+4.5%)

🔥 Best 3
1. LG에너지솔루션: +8.2%
2. 한화에어로스페이스: +6.5%
3. SK하이닉스: +5.1%

😰 Worst 3
1. 에코프로비엠: -2.3%
2. 삼성SDI: -1.8%
3. 포스코퓨처엠: -0.5%

📈 오늘 매도 (2건)
✅ 엘앤에프: 익절 +12.3% (+220,000원)
🛑 한미반도체: 손절 -8.5% (-85,000원)

📊 현재 포트폴리오: 14개 종목 보유
```

**주간 리포트** (매주 금요일):
- 주간 수익률
- 승률 (익절 종목 / 전체 청산 종목)
- 최대 낙폭 (MDD)
- 테마별 성과 비교

**월간 리포트** (매월 말일):
- 월간 수익률 및 누적 수익률
- 샤프 비율
- 최적 테마 분석
- 개선 제안 사항

**전송 방식**: 텔레그램 봇

---

## 🏗️ 시스템 아키텍처

### 전체 구조
```
┌─────────────────────────────────────────────────────┐
│              GCP VM (Ubuntu 22.04)                  │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │        Python Main Application                │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Daily Scheduler (APScheduler)          │ │ │
│  │  │  - 08:30: 테마 분석                      │ │ │
│  │  │  - 08:35: 수급 스크리닝                  │ │ │
│  │  │  - 08:40: AI 검증                        │ │ │
│  │  │  - 08:50: 포트폴리오 최적화              │ │ │
│  │  │  - 09:00: 자동 매수                      │ │ │
│  │  │  - 09:01-15:20: 실시간 모니터링          │ │ │
│  │  │  - 15:30: 일일 리포트                    │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 1: Theme Analyzer               │ │ │
│  │  │  - Naver/Hankyung 크롤링                │ │ │
│  │  │  - Claude AI 감성 분석                  │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 2: Stock Screener               │ │ │
│  │  │  - 수급 데이터 분석                      │ │ │
│  │  │  - 기술적 지표 계산                      │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 3: AI Verifier                  │ │ │
│  │  │  - 뉴스/공시 수집                        │ │ │
│  │  │  - Claude API 호출                      │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 4: Portfolio Optimizer          │ │ │
│  │  │  - 가중치 계산                           │ │ │
│  │  │  - 손익 목표 설정                        │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 5: Trading Engine               │ │ │
│  │  │  - KIS API 주문 실행                    │ │ │
│  │  │  - WebSocket 실시간 시세                │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                               │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Module 6: Risk Manager                 │ │ │
│  │  │  - 손익 모니터링                         │ │ │
│  │  │  - 자동 청산                             │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │          SQLite Database                      │ │
│  │  - Themes (테마 점수 이력)                     │ │
│  │  - Stocks (종목 스크리닝 이력)                 │ │
│  │  - Portfolio (포트폴리오 현황)                 │ │
│  │  - Trades (매매 기록)                          │ │
│  │  - Performance (성과 지표)                     │ │
│  └───────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
              │                    │
              │                    │
              ▼                    ▼
    ┌──────────────────┐  ┌──────────────────┐
    │  KIS Developers  │  │   Claude API     │
    │  (한국투자증권)   │  │   (Anthropic)    │
    │  - REST API      │  │                  │
    │  - WebSocket     │  │                  │
    └──────────────────┘  └──────────────────┘
              │                    │
              └──────────┬─────────┘
                         │
                         ▼
              ┌──────────────────┐
              │  Telegram Bot    │
              │  (알림 전송)      │
              └──────────────────┘
```

### 데이터 플로우
```
[08:30] 크롤링 → 테마 데이터 수집
           ↓
        Claude API → 테마 감성 분석
           ↓
        DB 저장 → Top 5 테마 선정
           ↓
[08:35] KIS API → 테마별 종목 + 수급 데이터
           ↓
        계산 → 수급 점수 산출
           ↓
        필터링 → 후보 종목 15-50개
           ↓
[08:40] 뉴스/공시 크롤링
           ↓
        Claude API → 병렬 분석 (20개)
           ↓
        최종 점수 → 15-20개 선정
           ↓
[08:50] 포트폴리오 최적화
           ↓
        가중치 계산 → 15개 종목 확정
           ↓
        DB 저장 → 포트폴리오 업데이트
           ↓
[09:00] KIS API → 시장가 매수 주문
           ↓
        체결 확인 → DB 업데이트
           ↓
[09:01~] WebSocket → 실시간 시세 수신
           ↓
        조건 체크 → 손익/수급 모니터링
           ↓
        (트리거 발동 시)
           ↓
        KIS API → 시장가 매도
           ↓
        텔레그램 → 알림 전송
           ↓
        DB 저장 → 거래 기록
           ↓
[15:30] 성과 집계 → 일일 리포트 생성
           ↓
        텔레그램 → 리포트 전송
```

---

## 🛠️ 기술 스택

### Backend
| 구분 | 기술 | 버전 | 용도 |
|------|------|------|------|
| 언어 | Python | 3.11+ | 메인 개발 언어 |
| 웹 프레임워크 | FastAPI | 0.104+ | 대시보드/API 서버 (선택) |
| 스케줄러 | APScheduler | 3.10+ | 일일 작업 스케줄링 |
| 비동기 | asyncio | Built-in | Claude API 병렬 호출 |
| HTTP 클라이언트 | httpx | 0.25+ | API 호출 |
| WebSocket | websockets | 12.0+ | 실시간 시세 |

### 데이터 처리
| 구분 | 기술 | 용도 |
|------|------|------|
| 데이터 분석 | pandas | 2.1+ | 수급/가격 데이터 처리 |
| 수치 계산 | numpy | 1.26+ | 지표 계산 |
| 통계 | scipy | 1.11+ | 변동성 계산 |
| 차트 | plotly | 5.18+ | 백테스팅 시각화 (선택) |

### 데이터베이스
| 구분 | 기술 | 용도 |
|------|------|------|
| RDBMS | SQLite | 3.x | 테마/종목/거래 데이터 저장 |
| ORM | SQLAlchemy | 2.0+ | DB 추상화 (선택) |

### 외부 API
| 서비스 | API | 용도 |
|--------|-----|------|
| 한국투자증권 | KIS Developers | 주식 시세/매매/수급 |
| Anthropic | Claude API | AI 테마/종목 분석 |
| DART | 공시 API | 기업 공시 데이터 |
| Telegram | Bot API | 알림 전송 |

### 크롤링
| 구분 | 기술 | 용도 |
|------|------|------|
| HTML 파싱 | BeautifulSoup4 | 4.12+ | 네이버/한경 크롤링 |
| HTTP | requests | 2.31+ | 웹페이지 요청 |

### 인프라
| 구분 | 기술 | 용도 |
|------|------|------|
| 클라우드 | GCP Compute Engine | VM 호스팅 |
| OS | Ubuntu | 22.04 LTS | 서버 운영체제 |
| 프로세스 관리 | systemd | 자동 시작/재시작 |
| 로깅 | loguru | 0.7+ | 구조화된 로깅 |
| 환경 변수 | python-dotenv | 1.0+ | API 키 관리 |

### 개발 도구
| 구분 | 기술 | 용도 |
|------|------|------|
| 패키지 관리 | poetry | 1.7+ | 의존성 관리 |
| 코드 포맷 | black | 23.11+ | 코드 스타일 |
| 린터 | ruff | 0.1+ | 정적 분석 |
| 타입 체크 | mypy | 1.7+ | 타입 검증 (선택) |

---

## 📅 개발 단계

### Phase 1: 인프라 구축 (1주)
**목표**: 개발 환경 및 기본 구조 준비

**작업 내용**:
- [x] GCP VM 생성 (e2-medium, Ubuntu 22.04)
- [x] Python 3.11 설치 및 가상환경 구성
- [x] KIS Developers 계정 생성 및 API 키 발급
- [x] Claude API 키 발급 (Anthropic)
- [x] Telegram Bot 생성
- [ ] SQLite 데이터베이스 스키마 설계
- [ ] 프로젝트 구조 설정 (`/src`, `/tests`, `/logs`, `/data`)
- [ ] 기본 로깅 시스템 구축

**산출물**:
- `config.py`: 환경 변수 관리
- `database.py`: DB 연결 및 테이블 생성
- `logger.py`: 로깅 설정

**테스트**:
- KIS API 연결 테스트 (모의투자)
- Claude API 호출 테스트
- Telegram 알림 전송 테스트

---

### Phase 2: 테마 분석 모듈 (1주)
**목표**: 테마 데이터 수집 및 점수화 시스템 구현

**작업 내용**:
- [ ] 네이버 증권 테마 크롤러 (`naver_theme_crawler.py`)
- [ ] 한국경제 테마 크롤러 (`hankyung_theme_crawler.py`)
- [ ] 자체 정의 테마 DB 구축 (20개 섹터)
- [ ] Claude API 테마 감성 분석 함수 (`ai_theme_analyzer.py`)
- [ ] 테마 점수 계산 로직 (`theme_scorer.py`)
  - 모멘텀 점수
  - 수급 점수
  - 뉴스 화제성
  - AI 감성 점수
- [ ] Top 5 테마 선정 로직
- [ ] 테마 데이터 DB 저장

**산출물**:
- `modules/theme_analyzer/`
  - `crawlers.py`
  - `scorer.py`
  - `ai_analyzer.py`
  - `selector.py`

**테스트**:
- 크롤링 데이터 정확성 검증
- Claude 감성 분석 결과 검증 (수동)
- 점수 계산 로직 유닛 테스트

**예상 결과**:
```python
themes = select_top_themes(count=5)
# [
#   {'theme': '2차전지', 'score': 87.5, ...},
#   {'theme': 'AI반도체', 'score': 82.3, ...},
#   ...
# ]
```

---

### Phase 3: 수급 스크리닝 모듈 (1주)
**목표**: 테마별 종목 필터링 시스템 구현

**작업 내용**:
- [ ] KIS API 수급 데이터 수집 함수 (`kis_investor_api.py`)
  - 외국인/기관/개인 순매수 데이터
- [ ] KIS API 시세 데이터 수집 함수 (`kis_price_api.py`)
  - OHLCV + 이동평균선 + RSI + ATR
- [ ] KIS API 재무 데이터 수집 함수 (`kis_financial_api.py`)
  - 부채비율, 영업이익률
- [ ] 수급 필터링 로직 (`supply_filter.py`)
  - 외국인+기관 순매수 체크
  - 수급 점수 계산
- [ ] 기술적 필터링 로직 (`technical_filter.py`)
  - MA 정배열, 거래량, RSI
- [ ] 재무 필터링 로직 (`fundamental_filter.py`)
- [ ] 종합 스크리닝 파이프라인 (`stock_screener.py`)

**산출물**:
- `modules/stock_screener/`
  - `kis_api.py`
  - `filters.py`
  - `screener.py`

**테스트**:
- 수급 데이터 정확성 검증 (HTS와 비교)
- 필터 조건 유닛 테스트
- 엣지 케이스 처리 (데이터 누락, API 에러)

**예상 결과**:
```python
candidates = screen_all_themes(top_themes)
# [
#   {'stock': {...}, 'supply_score': 150, 'theme': '2차전지'},
#   ...
# ] (15-50개)
```

---

### Phase 4: AI 검증 모듈 (1주)
**목표**: Claude AI 기반 뉴스/공시 분석 시스템

**작업 내용**:
- [ ] 네이버 금융 뉴스 크롤러 (`news_crawler.py`)
- [ ] DART API 공시 수집 함수 (`dart_api.py`)
- [ ] Claude AI 종목 분석 프롬프트 설계
- [ ] 병렬 AI 분석 시스템 (`ai_verifier.py`)
  - `asyncio.gather()` 활용
  - 동시 처리 제한 (5-10개)
- [ ] 최종 점수 계산 로직
  - 수급(40%) + 테마(30%) + AI(30%)
- [ ] 상위 15-20개 종목 선정

**산출물**:
- `modules/ai_verifier/`
  - `news_crawler.py`
  - `dart_api.py`
  - `claude_analyzer.py`
  - `verifier.py`

**테스트**:
- Claude 응답 JSON 파싱 검증
- 병렬 처리 성능 테스트 (20개 종목 < 10분)
- 에러 핸들링 (API 제한, 타임아웃)

**예상 결과**:
```python
verified = await ai_verify_stocks(candidates)
# [
#   {
#     'stock': {...},
#     'ai_sentiment': 7.5,
#     'ai_reason': '...',
#     'final_score': 82.3
#   },
#   ...
# ] (15-20개)
```

---

### Phase 5: 포트폴리오 최적화 모듈 (1주)
**목표**: 가중치 계산 및 손익 목표 자동 설정

**작업 내용**:
- [ ] 변동성 계산 함수 (`volatility_calculator.py`)
  - 60일 표준편차 → 연율화
- [ ] 가중치 계산 로직 (`weight_calculator.py`)
  - 점수 기반 가중치
  - 변동성 조정
  - 제약 조건 적용 (최소 3%, 최대 15%, 테마 30%)
- [ ] ATR 계산 함수 (`atr_calculator.py`)
- [ ] 손절가 계산 로직 (`stop_loss_calculator.py`)
- [ ] 익절가 계산 로직 (`take_profit_calculator.py`)
- [ ] 포트폴리오 최적화 파이프라인 (`portfolio_optimizer.py`)
- [ ] 포트폴리오 출력 포맷팅 (`portfolio_formatter.py`)

**산출물**:
- `modules/portfolio_optimizer/`
  - `calculators.py`
  - `optimizer.py`
  - `formatter.py`

**테스트**:
- 가중치 합계 100% 검증
- 제약 조건 준수 검증
- 손익 목표 합리성 검증 (시뮬레이션)

**예상 결과**:
```python
portfolio = optimize_portfolio(verified, total_capital=10_000_000)
# [
#   {
#     'code': '373220',
#     'name': 'LG에너지솔루션',
#     'weight': 0.089,
#     'shares': 45,
#     'stop_loss': 389000,
#     'take_profit': 482000
#   },
#   ...
# ] (15개)
```

---

### Phase 6: 자동 매매 엔진 (1주)
**목표**: KIS API 주문 실행 및 실시간 모니터링

**작업 내용**:
- [ ] KIS API 매수 주문 함수 (`kis_order_api.py`)
  - 시장가 매수
  - 주문 ID 반환
- [ ] KIS API 매도 주문 함수
- [ ] KIS API 주문 체결 조회 함수
- [ ] 매매 실행 엔진 (`trading_engine.py`)
  - 포트폴리오 일괄 매수
  - 에러 핸들링 및 재시도
- [ ] KIS WebSocket 실시간 시세 구독 (`kis_websocket.py`)
- [ ] 실시간 모니터링 시스템 (`portfolio_monitor.py`)
  - 손절/익절 체크
  - 수급 이탈 감지
  - 트레일링 스탑 (선택)
- [ ] 자동 청산 로직

**산출물**:
- `modules/trading_engine/`
  - `kis_order_api.py`
  - `kis_websocket.py`
  - `trading_engine.py`
  - `portfolio_monitor.py`

**테스트**:
- 모의투자 계좌로 주문 테스트
- WebSocket 연결 안정성 테스트
- 손익 조건 트리거 시뮬레이션

**주의사항**:
- **반드시 모의투자로 먼저 테스트**
- 실전 투자 전 최소 1주일 모의 운영

---

### Phase 7: 리밸런싱 & 리포팅 (1주)
**목표**: 일일 재조정 및 성과 집계 시스템

**작업 내용**:
- [ ] 보유 포지션 확인 함수 (`position_manager.py`)
- [ ] 사용 가능 현금 계산
- [ ] 리밸런싱 로직 (`rebalancer.py`)
  - 조건 체크 (현금 5% 이상)
  - Phase 1-3 재실행
  - 중복 종목 제외
  - 새 포트폴리오 구성
- [ ] 성과 집계 함수 (`performance_calculator.py`)
  - 일일 수익률
  - 승률
  - MDD
- [ ] 리포트 생성 (`report_generator.py`)
  - 일일/주간/월간
- [ ] 텔레그램 전송 (`telegram_notifier.py`)

**산출물**:
- `modules/rebalancer/rebalancer.py`
- `modules/reporter/`
  - `performance_calculator.py`
  - `report_generator.py`
  - `telegram_notifier.py`

**테스트**:
- 리밸런싱 시나리오 테스트
- 리포트 포맷 검증

---

### Phase 8: 통합 & 스케줄링 (1주)
**목표**: 전체 시스템 통합 및 자동 실행

**작업 내용**:
- [ ] 메인 실행 파일 (`main.py`)
- [ ] APScheduler 스케줄 설정
  - 08:30: 테마 분석
  - 08:35: 수급 스크리닝
  - 08:40: AI 검증
  - 08:50: 포트폴리오 최적화
  - 09:00: 자동 매수
  - 09:01-15:20: 실시간 모니터링
  - 15:30: 일일 리포트
- [ ] systemd 서비스 파일 작성
- [ ] 자동 시작/재시작 설정
- [ ] 로그 로테이션 설정
- [ ] 에러 알림 시스템 (텔레그램)
- [ ] 전체 통합 테스트

**산출물**:
- `main.py`
- `trading_system.service` (systemd)
- `README.md` (운영 가이드)

**테스트**:
- 전체 플로우 E2E 테스트 (모의투자)
- 장애 시나리오 테스트
  - API 에러
  - 네트워크 끊김
  - 서버 재시작

---

### Phase 9: 백테스팅 & 최적화 (2주)
**목표**: 과거 데이터로 전략 검증 및 파라미터 튜닝

**작업 내용**:
- [ ] 과거 데이터 수집 (2022-2024)
  - 테마별 종목 구성
  - 수급 데이터
  - 뉴스/공시
- [ ] 백테스팅 엔진 (`backtester.py`)
  - 일일 시뮬레이션
  - 매수/매도 기록
- [ ] 성과 지표 계산
  - 연 수익률
  - 승률
  - MDD
  - 샤프 비율
- [ ] 파라미터 최적화
  - 손절/익절 비율
  - 포트폴리오 크기
  - 가중치 비율
- [ ] 시각화 대시보드 (Plotly)

**산출물**:
- `backtesting/backtester.py`
- `backtesting/optimizer.py`
- 백테스팅 리포트 (PDF)

**예상 결과**:
- 연 수익률: 28-35%
- 승률: 65-70%
- MDD: -12% ~ -15%
- 샤프 비율: 1.5-2.0

---

### Phase 10: 실전 운영 (지속)
**목표**: 모의투자 → 소액 실전 → 본격 운영

**1단계: 모의투자 (2주)**
- 전체 시스템 모의 운영
- 일일 성과 모니터링
- 버그 수정 및 안정화

**2단계: 소액 실전 (1개월)**
- 100-300만원으로 실전 투자
- 시스템 검증
- 파라미터 미세 조정

**3단계: 본격 운영**
- 1000만원 이상 투자
- 지속적인 모니터링 및 개선

**운영 체크리스트**:
- [ ] 매일 아침 시스템 로그 확인
- [ ] 주 1회 성과 리뷰
- [ ] 월 1회 전략 평가 및 개선
- [ ] 분기 1회 대규모 업데이트

---

## ⚠️ 리스크 관리

### 기술적 리스크

**1. API 장애**
- **리스크**: KIS API 서버 다운 또는 Rate Limit 초과
- **대응**:
  - 주문 실패 시 3회 재시도
  - API 호출 제한 준수 (0.5초 간격)
  - Fallback: 수동 주문 알림 (텔레그램)

**2. 네트워크 불안정**
- **리스크**: VM 네트워크 끊김으로 실시간 모니터링 중단
- **대응**:
  - WebSocket 재연결 로직
  - 하트비트 체크 (30초마다)
  - 연결 끊김 시 텔레그램 알림

**3. 서버 다운**
- **리스크**: VM 장애 또는 Python 프로세스 크래시
- **대응**:
  - systemd 자동 재시작
  - 상태 모니터링 (health check)
  - 중요 데이터 SQLite 실시간 저장

**4. 데이터 오류**
- **리스크**: 크롤링 실패 또는 API 데이터 오류
- **대응**:
  - 데이터 검증 로직 (Null 체크, 범위 체크)
  - 크롤링 실패 시 재시도 (3회)
  - 실패 시 해당 테마/종목 스킵

**5. Claude API 제한**
- **리스크**: API 사용량 초과 또는 응답 지연
- **대응**:
  - 병렬 처리 제한 (동시 5-10개)
  - 타임아웃 설정 (30초)
  - JSON 파싱 실패 시 해당 종목 제외

### 투자 리스크

**1. 집중 리스크**
- **리스크**: 특정 테마/종목에 과도한 투자
- **대응**:
  - 종목당 최대 15%
  - 테마당 최대 30%
  - 최소 15개 종목 분산

**2. 급락 리스크**
- **리스크**: 손절가 도달 전 급락 (서킷브레이커 등)
- **대응**:
  - 일일 최대 손실 2% 제한
  - 수급 이탈 감지 시 선제 매도
  - 악재 종목은 AI 단계에서 사전 제외

**3. 유동성 리스크**
- **리스크**: 거래량 부족으로 매도 불가
- **대응**:
  - 스크리닝 시 거래대금 > 50억원 조건
  - 시가총액 상위 종목 우선
  - 분할 매도 (급한 경우)

**4. 테마 소멸 리스크**
- **리스크**: 테마주 단기 급등 후 급락
- **대응**:
  - AI 검증 단계에서 "재료 소진" 종목 제외
  - 익절가 보수적 설정 (10-15%)
  - 트레일링 스탑으로 수익 보호

**5. 수급 오판 리스크**
- **리스크**: 외국인 매수 = 반드시 상승 X
- **대응**:
  - 수급 단독 아닌 복합 조건 (테마+AI)
  - 수급 이탈 즉시 매도
  - 역사적 승률 70% 미만 시 전략 재검토

### 운영 리스크

**1. 과최적화 (Overfitting)**
- **리스크**: 백테스팅에서만 잘 작동
- **대응**:
  - 다양한 시장 구간 테스트 (상승장/하락장/횡보장)
  - 파라미터 과도한 튜닝 지양
  - Walk-forward 테스트

**2. 감정 개입**
- **리스크**: 손실 시 시스템 수동 중단
- **대응**:
  - 일일 리포트로 객관적 평가
  - 최소 3개월 운영 후 판단
  - 원칙: 시스템 신뢰

**3. 자본 부족**
- **리스크**: 연속 손실로 자본 고갈
- **대응**:
  - 초기 자본 충분히 확보 (최소 1000만원)
  - 일일 손실 제한 (2%)
  - 월 손실 -10% 시 1주일 휴식

---

## 📊 성과 지표 (KPI)

### 수익성 지표
| 지표 | 목표 | 측정 방법 |
|------|------|----------|
| 월간 수익률 | +2-3% | (당월 말 - 전월 말) / 전월 말 |
| 연간 수익률 | +28-35% | (연말 - 연초) / 연초 |
| 승률 | 65% 이상 | 익절 건수 / 전체 청산 건수 |
| 손익비 | 1.5:1 이상 | 평균 익절액 / 평균 손절액 |

### 리스크 지표
| 지표 | 목표 | 측정 방법 |
|------|------|----------|
| MDD | -15% 이내 | 최고점 대비 최대 낙폭 |
| 샤프 비율 | 1.5 이상 | (수익률 - 무위험수익률) / 변동성 |
| 일일 최대 손실 | -2% 이내 | 당일 손실 / 전일 자산 |

### 운영 지표
| 지표 | 목표 | 측정 방법 |
|------|------|----------|
| 시스템 가동률 | 99% 이상 | 정상 작동일 / 영업일 |
| 주문 성공률 | 95% 이상 | 체결 건수 / 주문 건수 |
| 평균 보유 기간 | 5-7일 | Σ 보유일 / 청산 건수 |
| 포트폴리오 크기 | 12-15개 | 일일 평균 보유 종목 수 |

### 평가 주기
- **일일**: 수익률, 승률, 시스템 가동률
- **주간**: MDD, 샤프 비율, 포트폴리오 회전율
- **월간**: 누적 수익률, 테마별 성과, 전략 유효성
- **분기**: 종합 평가 및 개선 방향 수립

---

## 📝 부록

### A. 환경 변수 (.env 예시)
```bash
# KIS API
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT_NO=12345678-01
KIS_CANO=12345678  # 종합계좌번호
KIS_ACNT_PRDT_CD=01  # 계좌상품코드

# Claude API
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789

# DART API
DART_API_KEY=your_dart_key

# Database
DATABASE_PATH=/home/user/trading_system/data/trading.db

# Logging
LOG_LEVEL=INFO
LOG_PATH=/home/user/trading_system/logs

# Trading
TOTAL_CAPITAL=10000000
MAX_POSITIONS=15
DAILY_MAX_LOSS=0.02
```

### B. 디렉토리 구조
```
korean_stock_ai_trading/
├── main.py                    # 메인 실행 파일
├── config.py                  # 환경 변수 로드
├── database.py                # DB 연결 및 스키마
├── logger.py                  # 로깅 설정
├── requirements.txt           # 의존성 패키지
├── .env                       # 환경 변수 (gitignore)
├── README.md                  # 프로젝트 설명
│
├── modules/                   # 핵심 모듈
│   ├── theme_analyzer/        # Phase 2
│   │   ├── crawlers.py
│   │   ├── scorer.py
│   │   ├── ai_analyzer.py
│   │   └── selector.py
│   │
│   ├── stock_screener/        # Phase 3
│   │   ├── kis_api.py
│   │   ├── filters.py
│   │   └── screener.py
│   │
│   ├── ai_verifier/           # Phase 4
│   │   ├── news_crawler.py
│   │   ├── dart_api.py
│   │   ├── claude_analyzer.py
│   │   └── verifier.py
│   │
│   ├── portfolio_optimizer/   # Phase 5
│   │   ├── calculators.py
│   │   ├── optimizer.py
│   │   └── formatter.py
│   │
│   ├── trading_engine/        # Phase 6
│   │   ├── kis_order_api.py
│   │   ├── kis_websocket.py
│   │   ├── trading_engine.py
│   │   └── portfolio_monitor.py
│   │
│   ├── rebalancer/            # Phase 7
│   │   └── rebalancer.py
│   │
│   └── reporter/              # Phase 7
│       ├── performance_calculator.py
│       ├── report_generator.py
│       └── telegram_notifier.py
│
├── backtesting/               # Phase 9
│   ├── backtester.py
│   └── optimizer.py
│
├── data/                      # 데이터 저장
│   ├── trading.db             # SQLite DB
│   └── historical/            # 과거 데이터
│
├── logs/                      # 로그 파일
│   ├── system.log
│   ├── trading.log
│   └── error.log
│
└── tests/                     # 테스트 코드
    ├── test_theme_analyzer.py
    ├── test_stock_screener.py
    └── ...
```

### C. 데이터베이스 스키마
```sql
-- 테마 점수 이력
CREATE TABLE themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    theme_name VARCHAR(50) NOT NULL,
    score REAL NOT NULL,
    momentum REAL,
    supply_ratio REAL,
    news_count INTEGER,
    ai_sentiment REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 종목 스크리닝 이력
CREATE TABLE stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50) NOT NULL,
    theme VARCHAR(50),
    supply_score REAL,
    technical_score REAL,
    ai_sentiment REAL,
    final_score REAL,
    selected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 포트폴리오 현황
CREATE TABLE portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50) NOT NULL,
    theme VARCHAR(50),
    weight REAL,
    shares INTEGER,
    buy_price REAL,
    stop_loss REAL,
    take_profit REAL,
    status VARCHAR(20) DEFAULT 'holding',  -- holding, closed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 매매 기록
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50) NOT NULL,
    action VARCHAR(10) NOT NULL,  -- buy, sell
    shares INTEGER,
    price REAL,
    amount REAL,
    reason VARCHAR(50),  -- 손절, 익절, 수급이탈
    profit_rate REAL,
    profit_amount REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 성과 지표
CREATE TABLE performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL UNIQUE,
    total_value REAL,
    total_cost REAL,
    daily_return REAL,
    cumulative_return REAL,
    win_rate REAL,
    mdd REAL,
    sharpe_ratio REAL,
    num_positions INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### D. systemd 서비스 파일
```ini
[Unit]
Description=Korean Stock AI Trading System
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/home/your_username/korean_stock_ai_trading
ExecStart=/home/your_username/korean_stock_ai_trading/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=append:/home/your_username/korean_stock_ai_trading/logs/systemd.log
StandardError=append:/home/your_username/korean_stock_ai_trading/logs/systemd_error.log

[Install]
WantedBy=multi-user.target
```

---

## 📞 문의 및 지원

### 개발자
- 이름: [이강희]
- 회사: JDB&NOB Entertainment
- 역할: 대표

### 기술 문의
- KIS Developers: https://apiportal.koreainvestment.com
- Anthropic Claude: https://docs.anthropic.com
- DART API: https://opendart.fss.or.kr

### 문서 버전
- 버전: 2.0.0
- 최종 수정일: 2025-02-05
- 작성자: Claude (Anthropic AI)

### 변경 이력
| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2025-02-04 | 초기 버전 |
| 2.0.0 | 2025-02-05 | 하이브리드 전략, 장 초반 관찰, 테마 로테이션, 분할 익절 추가 |

---

**[문서 끝]**