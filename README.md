# 🚀 한국 주식 AI 스윙 트레이딩 시스템

한국 주식시장에서 AI 기반 테마 분석과 수급 데이터를 결합한 **완전 자동화 스윙 트레이딩 시스템**입니다.

## ✨ 핵심 기능

- **📊 테마 분석**: 네이버/한경 인기 테마 크롤링 + Claude AI 감성 분석
- **💹 수급 스크리닝**: 외국인/기관 순매수 종목 자동 필터링
- **🤖 AI 검증**: 뉴스/공시 분석으로 리스크 사전 제거
- **⚖️ 포트폴리오 최적화**: 변동성 기반 가중치 자동 계산
- **🔄 자동 매매**: 09:00 장 시작과 동시에 매수 실행
- **📱 실시간 모니터링**: 손절/익절/수급이탈 자동 감지
- **📨 텔레그램 알림**: 매매 및 일일 리포트 자동 전송
- **🚀 이익 추종 전략**: 단계별 트레일링으로 대박 종목 끝까지 추종

## 📈 매매 전략: 이익 추종 (Let Profits Run)

수익이 커질수록 트레일링을 강화하여 큰 수익을 끝까지 추종합니다.

```
+8% 도달  → 트레일링 L1 (고점 -5%) + 본전 손절
+15% 도달 → 트레일링 L2 (고점 -3%) 강화
+25% 도달 → 트레일링 L3 (고점 -2%) 더 강화

고정 익절 없음 - 무제한 상승 추종!
```

**트레일링 레벨별 백테스트 성과:**
| 레벨 | 조건 | 평균 수익률 |
|------|------|------------|
| L3 | +25%↑ | **+36.58%** |
| L2 | +15%~25% | +15.25% |
| L1 | +8%~15% | +4.90% |

## 🎯 백테스트 성과 (2023-2026, 3년)

| 지표 | 기존 전략 | 이익 추종 전략 | 최적화 후 |
|------|----------|---------------|----------|
| **총 수익률** | +135% | +261% | **+337%** |
| **연평균 수익률 (CAGR)** | 32% | 51.7% | **~62%** |
| **최대 낙폭 (MDD)** | -11.3% | -9.9% | **-7.9%** |
| **샤프 비율** | 1.84 | 2.56 | **~2.8** |
| **승률** | 52.3% | 52.7% | 52.7% |

**최적화 파라미터**: 손절 -7%, 테마 로테이션 7일

자세한 백테스트 결과: [docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)

## 📁 프로젝트 구조

```
korean_stock_ai_trading/
├── main.py                    # 메인 실행 파일
├── config.py                  # 환경 변수 관리
├── database.py                # SQLite DB 관리
├── logger.py                  # 로깅 시스템
├── requirements.txt           # 의존성 패키지
├── .env                       # 환경 변수 (git 제외)
│
├── modules/                   # 핵심 모듈
│   ├── theme_analyzer/        # 테마 분석 모듈
│   ├── stock_screener/        # 수급 스크리닝 모듈
│   ├── ai_verifier/           # AI 검증 모듈
│   ├── portfolio_optimizer/   # 포트폴리오 최적화 모듈
│   ├── trading_engine/        # 자동 매매 엔진
│   ├── rebalancer/            # 리밸런싱 모듈
│   └── reporter/              # 성과 리포팅 모듈
│
├── backtesting/               # 백테스팅 도구
├── data/                      # 데이터베이스 파일
├── logs/                      # 로그 파일
└── tests/                     # 테스트 코드
```

## ⚙️ 설치 방법

### 1. 저장소 클론
```bash
cd ~
git clone <repository-url> korean_stock_ai_trading
cd korean_stock_ai_trading
```

### 2. Python 가상환경 설정
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 환경 변수 설정
```bash
cp env_example.txt .env
nano .env  # 실제 API 키 입력
```

### 4. 데이터베이스 초기화
```bash
python database.py
```

## 🔐 필요한 API 키

| 서비스 | 용도 | 발급처 |
|--------|------|--------|
| KIS Developers | 주식 시세/매매 | [한국투자증권 API 포털](https://apiportal.koreainvestment.com) |
| Claude API | AI 분석 | [Anthropic Console](https://console.anthropic.com) |
| DART API | 공시 데이터 | [DART 오픈API](https://opendart.fss.or.kr) |
| Telegram Bot | 알림 전송 | @BotFather |

## 📅 일일 스케줄

| 시간 | 작업 |
|------|------|
| 08:30 | 테마 분석 |
| 08:35 | 수급 스크리닝 |
| 08:40 | AI 검증 |
| 08:50 | 포트폴리오 최적화 |
| 09:00 | 자동 매수 실행 |
| 09:01-15:20 | 실시간 모니터링 |
| 15:30 | 일일 리포트 생성 |

## 🚀 실행 방법

### 개발/테스트 모드
```bash
source venv/bin/activate
python main.py
```

### 백그라운드 실행 (systemd)
```bash
sudo cp trading_system.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading_system
sudo systemctl start trading_system
```

### 서비스 상태 확인
```bash
sudo systemctl status trading_system
```

## ⚠️ 주의사항

1. **반드시 모의투자로 먼저 테스트하세요**
   - `.env` 파일에서 `IS_MOCK=true` 설정 확인
   
2. **실전투자 전 최소 2주간 모의 운영 권장**

3. **투자 손실에 대한 책임은 본인에게 있습니다**

## 📊 개발 현황

- [x] Phase 1: 인프라 구축
- [ ] Phase 2: 테마 분석 모듈
- [ ] Phase 3: 수급 스크리닝 모듈
- [ ] Phase 4: AI 검증 모듈
- [ ] Phase 5: 포트폴리오 최적화 모듈
- [ ] Phase 6: 자동 매매 엔진
- [ ] Phase 7: 리밸런싱 & 리포팅
- [ ] Phase 8: 통합 & 스케줄링
- [ ] Phase 9: 백테스팅 & 최적화
- [ ] Phase 10: 실전 운영

## 👤 개발자

- **이강희**
- JDB&NOB Entertainment 대표
- 비트코인 선물 시스템 트레이딩 경험 보유

---

**Made with ❤️ and AI assistance (Claude)**
