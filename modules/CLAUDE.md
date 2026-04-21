# modules/ — 전략/스코어링 모듈 규칙

## 구조
| 디렉토리 | 역할 |
|----------|------|
| `theme_analyzer/` | 테마 수집·점수화 (crawlers, scorer, selector, weekly_aggregator) |
| `stock_screener/` | 종목 스크리닝 (모멘텀·거래량·갭 필터) |
| `ai_verifier/` | Claude 기반 매수 검증 (Hold/No 필터) |
| `trading_engine/` | 매수·매도 실행, 주문 API 래퍼 |
| `portfolio_optimizer/` | 포트폴리오 관리·리밸런싱 |
| `post_trade_analyzer/` | 매도 후 D+5 추적·AI 분석 |
| `rebalancer/` | 로테이션·보유기간 매도 |
| `morning_filter/` | 장시작 전 필터링 |
| `reporter/` | 일일/주간 리포트 |
| `backtester/` | 백테스트 |
| `market_guard.py` | 시장 폭락일 매수 방어 |

## 필수 규칙

### 시간 처리
- `datetime.now()` / `date.today()` 대신 `from config import now_kst` 사용
  - 서버가 UTC라 그냥 쓰면 9시간 틀어짐
- CronTrigger 생성 시 `timezone="Asia/Seoul"` 명시
- 공휴일 체크는 `config.is_trading_day()` 사용

### 숫자 방어
- pandas 값 → float 변환 전 `pd.isna()` 체크
- KIS API 응답: `_safe_int()`, `_safe_float()` 사용 (빈 문자열 방어)
- AI/크롤링 데이터: NaN / None / 빈 문자열 모두 방어

### 점수 체계 참조
테마 점수 구성(모멘텀 + 과열감점 + 뉴스 + AI + 종목수 + 기본 + 유동성) 상세는
[`memory/project_strategy.md`](../memory/project_strategy.md) 및
`MEMORY.md` 의 "Theme Selection System" 섹션 참조.

- 점수 변경 시 `scorer.py` / `selector.py` 동시 확인
- AI 재계산(`main.py`) 시 `BASE_SCORE` import + `overheat_penalty` 반영 필수

### DB 작업
- `save_trade()` 는 `Optional[int]` (trade_id) 반환 — FK 연결용
- `save_screening_log()` 는 `INSERT OR IGNORE` (다중 테마 UNIQUE 충돌 방지)
- 스키마 변경은 `database.py` `_migrate()` 에 버전 추가 (idempotent, auto-backup)

### 종목코드 검증
- 크롤링 결과는 `re.match(r'^\d{6}$')` 로 6자리 숫자 확인 (0015G0 등 무효코드 차단)

## 전략 로직 변경 시
- **strategy-planner 에이전트** 호출 검토 (1% 규칙)
- 변경 후 **strategy-coder** 로 구현, **code-tester** 로 검증
- 파라미터는 `config.py` 상수화 — 하드코딩 금지
