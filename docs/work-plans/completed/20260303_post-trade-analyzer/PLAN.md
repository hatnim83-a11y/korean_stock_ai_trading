# PLAN: Post-Trade Analyzer (사후 트레이드 분석)

## 목표
매도 후 주가 추이를 추적하여 매도 판단의 적절성을 평가하고, AI 분석을 통해 교훈을 축적하는 자동화 모듈 구축.

## 배경
- `trade_reviews` 테이블에 매도 기록이 자동 저장되지만 `ai_review`, `lesson_learned` 컬럼이 비어있음
- `get_pending_trade_reviews()`, `update_trade_review_ai()` 인프라는 준비됨
- 매도 후 주가 추이 수집 + AI 분석 로직이 없음

## 구현 단계

### Step 1: DB 마이그레이션 v9
- [ ] `database.py`: SCHEMA_VERSION 8 → 9
- [ ] `post_trade_prices` 테이블 생성
- [ ] CRUD: `save_post_trade_prices()`, `get_post_trade_prices()`, `get_reviews_ready_for_analysis()`

### Step 2: 주가 추이 수집 (price_tracker.py)
- [ ] yfinance 우선 → KIS API 폴백
- [ ] 매도가 대비 변동률 계산
- [ ] 영업일 계산 (`is_trading_day()` + `holidays.KR`)

### Step 3: AI 프롬프트 (prompts.py)
- [ ] 개별 매매 분석 프롬프트 (타이밍 점수, 기회비용, 교훈)
- [ ] 주간 종합 리뷰 프롬프트
- [ ] JSON 응답 형식 강제

### Step 4: 핵심 분석 로직 (analyzer.py)
- [ ] `PostTradeAnalyzer` 클래스
- [ ] `run_daily_analysis()`: D+5 미분석 건 처리
- [ ] `generate_weekly_summary()`: 주간 교훈 종합

### Step 5: 스케줄러 + main.py 통합
- [ ] `scheduler.py`: 콜백 2개 (17:00, 금 17:30)
- [ ] `main.py`: PostTradeAnalyzer 인스턴스 + 콜백 연결

### Step 6: 텔레그램 알림
- [ ] `send_post_trade_report()` 메서드 추가

## 변경 파일 목록
| 파일 | 작업 |
|------|------|
| `database.py` | 수정 (v9 마이그레이션 + CRUD) |
| `scheduler.py` | 수정 (콜백 슬롯 2개) |
| `main.py` | 수정 (PostTradeAnalyzer 통합) |
| `modules/reporter/telegram_notifier.py` | 수정 (알림 메서드) |
| `modules/post_trade_analyzer/__init__.py` | 신규 |
| `modules/post_trade_analyzer/price_tracker.py` | 신규 |
| `modules/post_trade_analyzer/prompts.py` | 신규 |
| `modules/post_trade_analyzer/analyzer.py` | 신규 |

## 롤백 계획
1. DB: 마이그레이션 전 자동 백업 (.bak 파일)
2. 코드: git revert로 원복 가능
3. 스케줄러: 콜백 None이면 실행 안됨 (기존 동작 영향 없음)

## 완료 기준
- `from modules.post_trade_analyzer import PostTradeAnalyzer` 임포트 성공
- DB에 `post_trade_prices` 테이블 존재
- 17:00/17:30 스케줄 등록 확인
- code-tester 에이전트 검증 통과
