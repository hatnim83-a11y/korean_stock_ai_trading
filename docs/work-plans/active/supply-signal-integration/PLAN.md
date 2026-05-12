# PLAN — 메인 시스템 외국인/기관 수급 신호 도입

> **승인 일자**: 2026-05-11
> **원본 plan**: `/home/hatni/.claude/plans/kis-compiled-otter.md`
> **작업명**: supply-signal-integration
> **예상 기간**: Phase 1 = 5~10영업일 (단위별 게이트 포함, Shadow Run 14영업일 별도)

## 1. 목표

종가베팅 시스템(`closing_bet_system/`)이 단위 2-3 옵션 H를 통해 검증한 외국인 수급 데이터 소스(HHPTJ04160200, 네이버 frgn.naver, FHPTJ04400000)와 패턴을 메인(스윙) 시스템에 이식하여:

- (a) 잠자던 `supply_score`를 활성화 (scorer.py:662 `supply_score = 0` 박제 해제)
- (b) 전일 마감 후 한 번만 수집해 다음날 KIS 호출 부담 제거 (T-1 데이터로만 종목 선정)
- (c) trade_reviews에 매수 시점 수급을 박제해 사후 적중률 측정 A/B 인프라 갖춤

**보호 자산**: 현재 운영 중인 테마 모멘텀 전략(+337%, CAGR 61.3%, Sharpe 2.98, MDD -7.9%)을 **희석시키지 않고 보강**.

## 2. 배경 — 발견된 결함

- **`scorer.py:662` supply_score=0 박제**: `calculate_supply_score()` (scorer.py:147-186)가 구현되어 있으나 호출되지 않아 모멘텀(25)+뉴스(15)+AI(10)+종목수(5)+기본(10) 점수 체계에서 수급 신호가 0점 고정.
- 매수일마다 종목별 `get_investor_trading()` 호출하는 비효율 (어차피 09:25 전에는 T-1 데이터만 받음).
- AI Verifier는 5일 누적 한 줄(`외국인 +X억, 기관 +Y억 (5일)`)만 받아 일별 추세/순위/연속 매수일수 컨텍스트 부재.

## 3. 사용자 확정 결정

| 결정 | 내용 |
|---|---|
| 데이터 시점 | T-1 마감 데이터만 사용 (아침 KIS 재호출 금지) |
| 수집 시각 | 평일 17:10 KST (16:00 일일리포트 후, 17:05 일별테마수집 직후, 종가베팅 19:27과 간격 확보) |
| 활성화 정책 | 단위별 단계 배포 + Shadow Run 2주 + A/B 모니터링 (점진 0→2.5→5) |
| 데이터 소스 | HHPTJ04160200 + FHKST01010900 + FHPTJ04400000 + 네이버 frgn.naver(종가베팅 19:27 잡 재활용) |
| 개선 범위 | 테마 점수 + 종목 필터 + AI Verifier + trade_reviews 박제 + (Phase 3) 트레일링 |

## 4. 리뷰 반영 결정 사항 (코더 + 플래너)

- Phase 1을 **1-A/B/B½/C/D 5단위 분리** (각 단위 사용자 확인 게이트)
- **Shadow Run 2주** (`SUPPLY_SCORE_OBSERVE_ONLY=True`) — 모멘텀 상관계수 r<0.7 검증
- **trade_reviews INSERT 옵션 A** (25컬럼 한 번에 INSERT, race-free)
- **close_position UPDATE 확정** (database.py:1042-1057, DELETE 아님)
- **filters.py 키 분리**: `supply_score`(억원, 기존) + `supply_strength`(0~2, 신규) 별도 키, 이중 카운팅 방지
- **위치 정정**: 17:05 = `_run_daily_theme_collection` (KIS 수집 OK), 08:30 = `run_theme_analysis` (DB 조회), 09:05 = `screen_stocks_in_theme` (DB 조회)
- **라인 정정**: `kis_api.py` 1081 (300 아님)
- **점진 배포**: SUPPLY_SCORE_MAX 0 → 2.5 → 5.0 (1일 간격)
- **운영 게이트**: 3영업일 + stale 시뮬레이션

## 5. Phase 단계 구성

### Phase 1-A: 데이터 파이프라인 (Day 1~3)
- DB v16 마이그레이션 (`daily_supply_snapshot`, `foreign_top_ranking`, `supply_score_observation`, portfolio +7, trade_reviews +9)
- `modules/supply_collector/collector.py` 신규 (rate limit + 백오프 + 진행률)
- scheduler.py 17:10 잡 + 18:00 재시도
- main.py `run_supply_collection` + 텔레그램 알림
- config.py 5개 토글
- 게이트: 3영업일 95% 성공률 + stale 시뮬레이션

### Phase 1-B: DB 조회 통합 (Day 4~5)
- database.py 헬퍼 6개
- kis_api.py:1081 `get_stock_full_info(skip_supply=False)` 옵션
- screener.py:280-380 DB 조회 통합
- 게이트: 2영업일 회귀 동등성

### Phase 1-B½: Shadow Run 2주 (Day 6~19)
- scorer.py observation 로직 (총점 미반영)
- `SUPPLY_SCORE_OBSERVE_ONLY=True`로 관측만
- 게이트: 14영업일 데이터 ≥ 200건 + 모멘텀 상관계수 r<0.7 + 분포 차별성

### Phase 1-C: 점수 활성화 점진 배포 (Day 20~22)
- scorer.py `calculate_theme_supply_score_v2` + 라인 662 박제 해제
- filters.py `supply_strength` 별도 키 + `calculate_final_score` 가중치 토글
- 점진: Day 20 MAX=2.5 → Day 21 MAX=5.0 → Day 22 STRENGTH=True
- 게이트: 3영업일 매수 종목 ±20%, 인플레이션 ±5%p

### Phase 1-D: AI Verifier 강화 + 매수 박제 hook (Day 23~25)
- claude_analyzer.py:47-103 프롬프트 강화 + `_format_supply_for_prompt`
- verifier.py `ai_supply_signal` 매핑
- main.py 매수 후 portfolio supply 컨텍스트 hook
- database.py `save_trade_review` 25컬럼 INSERT (옵션 A)
- 게이트: 2영업일 AI 토큰 + hook 3경로 검증

### Phase 2: 신뢰도 추적 (Week 4~6, 표본 60건 후)
- supply_labeler.py (closing_bet.db JOIN + 백필)
- 일일 리포트 메트릭
- 이중 카운팅 검증 (Pearson r < 0.7)

### Phase 3: 자동 가중치 + 트레일링 (Week 7+)
- SUPPLY_SCORE_MAX 5 → 7~10점 상향
- 수급 급반전 조기 익절
- 시장 체제 분기

## 6. 변경 파일 목록

### 신규 파일
- `modules/supply_collector/__init__.py` (5 LOC)
- `modules/supply_collector/collector.py` (~320 LOC)
- `modules/supply_collector/aggregator.py` (~80 LOC)
- `modules/post_trade_analyzer/supply_labeler.py` (~180 LOC, Phase 2)
- `tests/test_supply_collector.py` (~280 LOC)
- `tests/test_database_v16_migration.py` (~120 LOC)
- `tests/test_theme_supply_score.py` (~180 LOC)
- `tests/test_save_trade_review_auto_enrich.py` (~150 LOC)

### 기존 파일 수정
- `database.py` (+280): `_migrate_v16` + 8개 헬퍼 + `save_trade_review` 25컬럼 INSERT
- `config.py` (+50): SUPPLY_* 토글 9개
- `scheduler.py` (+80): 17:10 supply_collection 잡
- `main.py` (+250): `run_supply_collection` + 매수 후 hook
- `modules/stock_screener/screener.py` (+60): DB 조회 보강
- `modules/stock_screener/kis_api.py` (+20): `get_stock_full_info(skip_supply)` 옵션 (**라인 1081**)
- `modules/theme_analyzer/scorer.py` (+100): observation + `calculate_theme_supply_score_v2` + 라인 662 해제
- `modules/stock_screener/filters.py` (+40): `supply_strength` 별도 키 + 가중치 토글
- `modules/ai_verifier/claude_analyzer.py` (+50): 프롬프트 강화
- `modules/ai_verifier/verifier.py` (+5): `ai_supply_signal` 매핑

**Phase 1 총합 ≈ 2,000 LOC**

## 7. config.py 신규 토글 (9개)

```python
# Phase 1-A
SUPPLY_SIGNAL_ENABLED: bool = True
SUPPLY_COLLECT_HOUR: int = 17
SUPPLY_COLLECT_MINUTE: int = 10
SUPPLY_UNIVERSE_TOP_MARKET_CAP: int = 200
SUPPLY_RANKING_TOP_N: int = 200

# Phase 1-B½
SUPPLY_SCORE_OBSERVE_ONLY: bool = True

# Phase 1-C
SUPPLY_SCORE_ENABLED: bool = True
SUPPLY_SCORE_MAX: float = 0.0
SUPPLY_SCORE_TOP_N: int = 5
SUPPLY_INTENSITY_REF_BIL: float = 30.0
SUPPLY_STRENGTH_ENABLED: bool = False

# Phase 1-D
AI_PROMPT_SUPPLY_ENHANCED: bool = True

# Phase 3
SUPPLY_REVERSAL_EARLY_EXIT_ENABLED: bool = False
SUPPLY_REVERSAL_THRESHOLD_BIL: float = -50.0
```

## 8. 롤백 계획

| 상황 | 조치 |
|---|---|
| 17:10 잡 KIS 폭주 | `SUPPLY_SIGNAL_ENABLED=False` |
| AI 프롬프트 노이즈 | `AI_PROMPT_SUPPLY_ENHANCED=False` |
| 가중치 역효과 | `SUPPLY_SCORE_MAX=0`, `SUPPLY_STRENGTH_ENABLED=False` |
| 점진 배포 중 이상 | `SUPPLY_SCORE_OBSERVE_ONLY=True`로 회귀 |
| 외국인 매도 국면 | `SUPPLY_SCORE_MAX=2.0` 축소 |
| DB 스키마 롤백 | v16 백업파일(`data/trading.db.bak.YYYYMMDD_HHMMSS`) 복원 |

멱등성: `_migrate_v16`은 `IF NOT EXISTS` + `_has_column` 체크. `INSERT OR REPLACE` UNIQUE 키. 재실행 안전.

## 9. 완료 기준

### Phase 1-A 완료
- 3영업일 연속 17:10 잡 성공률 ≥ 95%
- daily_supply_snapshot 행 ≥ 180/일
- stale 시뮬레이션 PASS
- 사용자 확인

### Phase 1-B 완료
- 회귀 동등성 (SUPPLY_SIGNAL_ENABLED=False 시 기존 동작)
- KIS 호출 횟수 감소 확인
- 사용자 확인

### Phase 1-B½ Shadow Run 완료
- 14영업일 데이터 ≥ 200건
- 모멘텀 × supply_score 상관계수 r < 0.7
- 분포 차별성 확인
- 사용자 의사결정 (PASS → 1-C / FAIL → 점수 변환식 재설계)

### Phase 1-C 완료
- 3영업일 매수 종목 ±20%
- 인플레이션 분포 ±5%p
- 사용자 확인

### Phase 1-D 완료
- AI 응답 토큰 +80~120 tokens
- 매도 hook 3경로 자동 보강
- 분할매도 동일 supply_at_buy 박제
- 사용자 확인 → Phase 1 완료 선언

## 10. 참고 자료

- 원본 plan: `/home/hatni/.claude/plans/kis-compiled-otter.md`
- 종가베팅 단위 2-3 (자원 발굴): `docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/`
- 종가베팅 메모리: `memory/project_closing_bet_system.md`, `memory/project_closing_bet_followups.md`
- 전략 메모리: `memory/project_strategy.md`, `memory/project_buy_filter_phase_a.md`
