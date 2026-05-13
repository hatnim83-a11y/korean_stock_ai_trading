# CONTEXT — 메인 시스템 외국인/기관 수급 신호 도입

> 이 문서는 작업 중 참조할 현재 코드 상태, 핵심 스니펫, 과거 결정, 검증된 가정을 담고 있다.

## 1. 변경 이유

### 1.1 메인 시스템의 잠자는 함수
`scorer.py:662`에서 `supply_score`가 **0으로 고정**되어 있다:
```python
"supply_score": 0,  # 항상 0 (계산 함수는 존재하지만 미호출)
```
- `calculate_supply_score()` (scorer.py:147-186) 함수는 구현되어 있으나 `score_themes()`에서 호출되지 않음
- 결과: 모멘텀(25) + 뉴스(15) + AI(10) + 종목수(5) + 기본(10) + 감점 + 유동성 = 65점 만점 체계에서 **외국인/기관 수급 신호가 통째로 매장**

### 1.2 종가베팅 시스템의 신규 발굴 자원 (이식 가능)
종가베팅 단위 2-3 옵션 H (2026-05-11 완료):
1. **HHPTJ04160200** — 외국인/기관 14:30 가집계 TR (`modules/stock_screener/kis_api.py:696-743`)
2. **네이버 frgn.naver** — KRX 확정값 크롤러 (`closing_bet_system/services/flow_reliability_tracker.py`)
3. **FHPTJ04400000** — 외국인 순매수 상위 TR (`closing_bet_system/collectors/kis_market_provider.py:312`)
4. **HHPTJ 우선·FHKST 폴백 패턴** — 15:10 시점 기관=0 박제 우회

### 1.3 09:25 매수 직전 데이터 시점 제약
- KIS FHKST01010900은 장중 업데이트 없음 — 09:25 호출해도 T-1 데이터만 반환
- HHPTJ04160200 첫 입력은 09:30 — 09:25 매수에 못 씀
- 결론: **종목 선정에는 T-1 마감 데이터만 사용 가능**. 아침 KIS 재호출은 의미 없는 오버워크

## 2. 현재 코드 상태 (검증 완료)

### 2.1 메인 시스템 점수 체계
**파일**: `modules/theme_analyzer/scorer.py`
- `score_themes()` 라인 542-689
- 현재: 모멘텀(25) + 뉴스(15) + AI(10) + 종목수(5) + 기본(10) + 과열감점(0~-15) + 유동성(0~-12) = 최대 65점
- 등급 컷오프: S(≥58), A(≥48), B(≥38), C(≥30), D(<30)
- `RETENTION_SCORE=48`, `MIN_SELECTION_SCORE=30` (절대값)

### 2.2 매도 hook 3경로 (분산)
1. `portfolio_monitor_v2._close_position_in_db` (라인 722-778) — 트레일링/손절/익절
2. `portfolio_monitor_v2._save_partial_sell_to_db` (라인 795+) — 부분 익절
3. `main._save_trade_review_for_main_sell` (라인 2136-2181) — 보유기간/midweek

**결정**: `save_trade_review()`를 단일 진실의 원천으로 만들어 portfolio 행에서 supply 컨텍스트 자동 복사 → 3개 hook 손대지 않음.

### 2.3 close_position 동작 (검증 완료)
**database.py:1042-1057** — `UPDATE portfolio SET status='closed' ...`
- DELETE 아님. portfolio row가 살아있음
- 따라서 `save_trade_review`에서 매도 후에도 portfolio에서 supply 컨텍스트 조회 가능
- fallback 설계 불필요

### 2.4 kis_api.get_stock_full_info (검증 완료)
**파일**: `modules/stock_screener/kis_api.py:1081`
- 내부에서 `get_investor_trading()` 호출 확인됨
- `skip_supply: bool = False` 옵션 추가 시 호환 유지 가능

### 2.5 종가베팅 19:27 잡
**파일**: `closing_bet_system/main_orchestrator.py:477-594`
- 19:27에 `fetch_naver_confirmed_flow` 호출
- 결과를 `closing_bet.db.flow_data_reliability` 테이블에 저장
- 메인 시스템은 이 DB를 read-only로 ATTACH하여 사후 라벨링 (Phase 2)

## 3. 핵심 코드 스니펫 (작업 대상)

### 3.1 scorer.py:662 박제 해제 대상
```python
# 현재
"supply_score": 0,  # ← 이 라인을 해제

# 변경 후 (Phase 1-C)
"supply_score": round(supply_score, 2),
```

### 3.2 score_themes 통합 (Phase 1-C)
```python
# scorer.py:600 부근, 라인 616의 total 합산식
# 현재
total = momentum_score + overheat + news_score + ai_score + size_bonus + BASE_SCORE + liquidity_penalty

# 변경 후
supply_score = 0.0
if settings.SUPPLY_SIGNAL_ENABLED and not settings.SUPPLY_SCORE_OBSERVE_ONLY:
    supply_score, _ = calculate_theme_supply_score_v2(
        theme.get("stocks_supply", []),
        top_n=settings.SUPPLY_SCORE_TOP_N,
        max_score=settings.SUPPLY_SCORE_MAX,
    )
total = momentum_score + overheat + news_score + ai_score + size_bonus \
      + BASE_SCORE + liquidity_penalty + supply_score
```

### 3.3 filters.py 키 분리 (이중 카운팅 방지)
```python
# 기존 supply_score (억원) — 유지, 의미 동일
# 신규 supply_strength (0~2) — 별도 키, 절대 합산 금지

# filters.py apply_supply_filter() 끝부분에 추가
foreign_intensity = min(1.0, max(0.0, foreign_net / max(trade_value, 1) / 0.10))
top200_bonus = 1.0 if stock.get("foreign_top_rank") else 0.0
consec_bonus = min(1.0, (stock.get("institution_consec_buy_days") or 0) / 5.0)
result["supply_strength"] = round(foreign_intensity + top200_bonus * 0.5 + consec_bonus * 0.5, 2)
```

### 3.4 AI Verifier 프롬프트 강화 대상
**파일**: `modules/ai_verifier/claude_analyzer.py:47-103`
```diff
- - 수급: 외국인 {foreign:+.0f}억원, 기관 {institution:+.0f}억원 (5일)
+ - 수급 (5일 누적, {snapshot_date} 기준):
+   * 외국인 {foreign:+.0f}억원 / 기관 {institution:+.0f}억원
+   * T-1 당일: 외국인 {foreign_1d:+.0f}억, 기관 {institution_1d:+.0f}억
+   * 외국인 순매수 TOP200 진입: {foreign_top_str}
+   * 기관 연속 매수일수: {institution_consec_days}일
+   * 거래대금 대비 외국인 강도: {foreign_intensity_pct:.1f}%
```

### 3.5 save_trade_review 자동 보강 (옵션 A)
**파일**: `database.py:1297-1326`
```python
def save_trade_review(self, review: dict) -> None:
    with self.get_cursor() as cursor:
        # close_position이 UPDATE만 하므로 portfolio row 존재 보장
        cursor.execute("""
            SELECT foreign_net_at_buy, institution_net_at_buy, supply_strength_at_buy,
                   foreign_top_rank_at_buy, institution_consec_days_at_buy,
                   theme_supply_score_at_buy, ai_supply_signal_at_buy
            FROM portfolio WHERE stock_code = ? ORDER BY date DESC LIMIT 1
        """, (review['stock_code'],))
        supply_ctx = cursor.fetchone() or {}

        # 25컬럼 한 번에 INSERT (race-free)
        cursor.execute("""
            INSERT INTO trade_reviews (...23 컬럼...)
            VALUES (?, ?, ..., ?)
        """, (..., supply_ctx에서 7개 ...))
```

## 4. 과거 결정 / 교훈

### 4.1 종가베팅 단위 2-3 옵션 H (2026-05-11)
- HHPTJ04160200 추정값 + 네이버 frgn.naver 확정값 매칭 패턴 완성
- foreign는 단위/기간 불일치(원/3일 vs 주/당일)로 NULL 강제 → 후속 단위에서 재설계
- → 메인 시스템도 단위 통일에 주의

### 4.2 이중 카운팅 위험 인지 (플래너 리뷰)
- 한국 시장에서 외국인 수급은 모멘텀 chasing(후행)일 가능성
- 이미 모멘텀 25점 비중인 시스템에 supply_score 5점 추가 시 같은 신호 반복 카운트 위험
- → Shadow Run 14영업일로 모멘텀 × supply 상관계수 r<0.7 검증 후 활성화

### 4.3 LOC 추정 정확도 (코더 리뷰)
- 초기 추정 1,650 → 실제 2,000 LOC (코더 검증)
- 등급 컷오프 인플레이션 측정, save_trade_review 패턴, 회귀 테스트 등 추가 작업 반영

## 5. 영향 범위

### 5.1 직접 변경 모듈
- `modules/theme_analyzer/scorer.py` — 점수 활성화 진원
- `modules/stock_screener/{screener,filters,kis_api}.py` — 종목 선정
- `modules/ai_verifier/{claude_analyzer,verifier}.py` — AI 판정
- `database.py` — 스키마 + 헬퍼 + 자동 보강
- `main.py` — 스케줄러 wiring + 매수 후 hook
- `scheduler.py` — 17:10 잡 등록
- `config.py` — 토글

### 5.2 간접 영향 (회귀 테스트 필요)
- `web/dashboard_service.py` — portfolio SELECT * 사용처
- `modules/trading_engine/portfolio_monitor_v2.py` — 매도 hook
- 백테스트 모듈 — supply 데이터 없음으로 인한 미반영

### 5.3 신규 종속성
- `closing_bet.db` (Phase 2 사후 라벨링용, read-only ATTACH)

## 6. 검증된 가정 (사전 검증 완료)

| 가정 | 결과 | 근거 |
|---|---|---|
| close_position이 UPDATE인가 DELETE인가 | **UPDATE status='closed'** | database.py:1042-1057 |
| get_stock_full_info가 get_investor_trading 내부 호출 | **호출함** | kis_api.py:1081 |
| closing_bet.db WAL 모드 | **양쪽 WAL, 별도 파일** | lock 없음 |
| FHPTJ04400000 안정성 | **검증됨** | 종가베팅 1주 관찰 |

## 7. Phase별 진행 시 추가 검증 항목

- Phase 1-B 시작 전: `grep -rn "get_stock_full_info" --include="*.py"` 외부 호출자 확인
- Phase 1-C 시작 전: `grep -n ">= 58\|>= 48\|>= 38\|>= 30" modules/theme_analyzer/scorer.py main.py` 컷오프 8곳 확인
- Phase 2 시작 전: 종가베팅 universe 겹침 SQL (target ≥ 30%)
- portfolio 컬럼 추가 회귀: `web/dashboard_service.py` SELECT * 영향 확인

## 8. 관련 메모리 / 문서

- `memory/MEMORY.md` — 프로젝트 전체 개요
- `memory/project_strategy.md` — 현재 운용 전략
- `memory/project_buy_filter_phase_a.md` — Phase A RSI 동적 + 슬롯 보장
- `memory/project_closing_bet_followups.md` — 단위 2-3 최종 상태
- `memory/project_trade_reviews_coverage_fix.md` — 매도 hook 3경로 보강 이력
- `docs/improvements/change_log.md` — 변경 이력 추적

---

## 9. 작업 중 발견 사항 (2026-05-12 세션, Phase 1-A 구현)

### 9.1 세션 흐름 (계획 수립 → 리뷰 → 구현)

**① 사용자 요청 (Plan mode 진입)**
"종가베팅 시스템에서 네이버 크롤러·KIS TR을 재분석해 외국인 수급 등 개선한 내용을 메인 시스템 테마/종목 선정에도 이식할 수 있는 부분 찾아줘."

**② 1차 조사 — 3개 Explore 에이전트 병렬 실행**
- A: 종가베팅 단위 2-3 옵션 H에서 발굴한 자원 정리 (HHPTJ04160200, 네이버 frgn.naver, FHPTJ04400000)
- B: 메인 시스템 점수/필터/AI 코드 위치 + supply_score 박제 발견
- C: 27개 KIS TR 카탈로그 + 외국인/기관 신호 매핑

**③ 핵심 의사결정 (AskUserQuestion 4문항)**
- 개선 범위 = 전 범위 + Layer 통합
- 데이터 소스 = HHPTJ + FHKST + FHPTJ04400000 + 네이버 frgn.naver(사후 라벨링)
- 활성화 = 즉시 배포 + A/B 모니터링
- 추가 분석 = AI Verifier + trade_reviews 컬럼 + FHPTJ 활용 + **08:30~09:00 시점 제약**

**④ 추가 Explore (시점 제약 + AI 프롬프트 + FHPTJ 활용)**
- AI Verifier 라인 56에 이미 5일 누적 한 줄 포함 → 강화 대상
- FHKST01010900은 장중 업데이트 없음 → 09:25 전에 받을 수 있는 건 T-1뿐
- HHPTJ04160200 첫 입력은 09:30 → 09:25 매수에 못 씀

**⑤ 사용자 핵심 추가 결정**
"전일장 마감까지 집계된 수급 정보 써도 될 듯. 아침에 한 번 더 분석은 오버워크." → **T-1 데이터만 사용 확정**

**⑥ Plan 에이전트 2개 병렬 (데이터 파이프라인 + 신호 통합)**
- 데이터 파이프라인: 17:10 SupplyCollector + DB 스키마 + 종가베팅 DB JOIN 전략
- 신호 통합: scorer.py:662 박제 해제 + filters.py 가산 + AI 프롬프트 강화 + trade_reviews 자동 박제

**⑦ Plan 파일 작성 → 코더+플래너 리뷰**
- 코더: 치명 3건 + 주의 11건 + 참고 6건
- 플래너: P0 2건 + P1 3건 (Phase 1 4단위 분리, Shadow Run 2주, 후행 지표 위험, close_position 검증 이동, retention 영향)

**⑧ 리뷰 반영 16건 → Plan 보강 → ExitPlanMode 승인**
- Phase 1을 **1-A/B/B½/C/D 5단위 분리**
- **Shadow Run 2주** 추가 (`SUPPLY_SCORE_OBSERVE_ONLY=True`, 모멘텀 상관계수 r<0.7 게이트)
- **이중 카운팅 리스크** 명시
- **trade_reviews INSERT = 옵션 A** (25컬럼 한 번에)
- **close_position = UPDATE 확정** (DELETE 아님)
- **filters.py 키 분리**: `supply_score`(억원) + `supply_strength`(0~2)
- **위치 정정**: 17:05/08:30/09:05 역할 명확화
- 라인 정정: `kis_api.py:1081` (300 아님)
- 점진 배포: SUPPLY_SCORE_MAX 0→2.5→5.0
- 운영 게이트 1일→3영업일

**⑨ Phase 1-A 구현 시작 (Plan mode 종료 후)**
3문서 생성 → DB v16 마이그레이션 → SupplyCollector → scheduler/main 연결 → 단위 테스트 24건 → code-tester → 즉시 처리 5건 반영.

### 9.2 추가로 발견된 사실들 (구현 중)

| 발견 | 사실 |
|---|---|
| `KISMarketProvider` 인스턴스화 | `get_kis_market_provider()` 가 싱글톤 진입점 (`get_provider`가 아님) |
| `KISMarketProvider.get_top_foreign_buy_codes` | **외국인만** (`etc_cls=1` 고정). 기관용은 별도 메서드 없음 → Phase 1-A에서 스킵, 후속 확장 시 etc_cls='2' 호출 헬퍼 추가 |
| `Database` 생성자 | `db_path` 인자를 받음 — 테스트는 `Database(db_path=str(tmpdir/'trading.db'))` 패턴 사용 가능 (사이드이펙트 회피) |
| `Database.__init__` | 기본 경로에 대해 `db_path.parent.mkdir(parents=True, exist_ok=True)` 자동 실행. 사후 `db.db_path = ...` 패턴은 사이드이펙트 발생 |
| `portfolio` 컬럼명 | `take_profit`/`stop_loss` (테스트의 `target_return`/`max_holding_days`는 오류) |
| `_run_daily_theme_collection` (17:05) | KIS 장중 호출 OK (장 마감 후이므로 적절). 변경 없음 |
| `aggregator.py` | Phase 1-B의 DB 헬퍼(`get_theme_supply_aggregate` 등)로 통합 가능 → 별도 파일 불필요 |

### 9.3 추가된 토글 (Plan 9개 → 실제 11개)

코더 리뷰 반영으로 6개 추가:
- `SUPPLY_THEME_TOP_K` (10): `get_top_themes(count=10)` 하드코딩 제거
- `SUPPLY_STOCK_LOOKBACK_DAYS` (7): `'-7 days'` 하드코딩 제거
- `SUPPLY_RANKING_CALL_SLEEP_SEC` (0.3): `_RANKING_CALL_SLEEP` 모듈 상수 제거
- `SUPPLY_RETRY_JOB_HOUR` (18): 18:00 재시도 잡 시 (0이면 미등록)
- `SUPPLY_RETRY_JOB_MINUTE` (0): 18:00 재시도 잡 분

### 9.4 다음 단계(Phase 1-B 진입 시) 주의점

1. **사용자 systemctl restart 필요** — v16 마이그레이션은 이미 자동 백업+적용됨 (`data/trading.bak.20260512_102055`), 17:10 잡 등록은 restart 필요
2. **운영 게이트 (3영업일)** — 17:10 성공률 ≥ 95%, daily_supply_snapshot ≥ 100/일 (universe 147 기준), stale 시뮬레이션
3. **Phase 1-B 시작 전 `get_stock_full_info` 외부 호출자 grep** — `skip_supply` 옵션이 다른 호출처에 영향 없는지
4. **Phase 1-B `get_theme_supply_aggregate` 등 DB 조회 헬퍼 추가** — Phase 1-A에서는 미구현
5. **stock_name 컬럼 NULL** — KIS `get_investor_trading` 응답에 `stock_name` 없음. Phase 1-B에서 종목명 표시 필요 시 `KISApi.get_stock_name(code)` 추가 호출 또는 portfolio/stocks 테이블 JOIN
6. **기관 TOP 수집 활성화** — Phase 1-A는 외인용만. `KISMarketProvider`에 `get_top_institution_buy_codes(etc_cls='2')` 헬퍼 추가 시 활성화
7. **종가베팅 universe 겹침 SQL** (Phase 2 진입 전) — 메인 매수 종목 ∩ 종가베팅 universe ≥ 30% 확인 후 closing_bet.db JOIN 경로 선택

### 9.5 새 대화 필요성 평가

- 현재 컨텍스트: 약 350K tokens (1M 한도 내 충분)
- Phase 1-A는 구현·검증 완료. 운영 게이트(3영업일)는 사용자 시간 필요
- **Phase 1-B 진입은 3영업일 후 새 대화로 이어가도 무방** — CONTEXT.md + CHECKLIST.md + change_log.md만 읽으면 충분히 복원 가능
- 별도 PR 또는 같은 브랜치 연속 작업 모두 OK

---

## 10. 작업 중 발견 사항 (2026-05-13 세션, Phase 1-A 사후 검증 + Phase 1-B 구현)

### 10.1 세션 흐름 (Phase 1-A 사후 검증 → Phase 1-B 구현 → 배포)

**① Phase 1-A 사후 검증 (5/12 17:10 잡 발화 미확인)**
- 사용자 질문 "잡 등록된 건 어찌 확인하는거야" → 3가지 방법 안내
- 5/12 KST 22:44 시점 supply 로그 grep → **로그 없음** (17:10 발화 X)
- 원인: 봇 PID 2196773이 5/11 11:13에 시작, Phase 1-A 코드 변경(5/12 01:30) 후 systemctl restart 미진행
- 옵션 B 선택 (수동 수집 + restart 병행):
  - `python -m modules.supply_collector.collector --date 2026-05-12` 실행
  - **결과: universe 82종목 / success 82 / fail 0 / 13.4초**
  - 000990 KIS 500 에러 1건은 retry 자동 복구
- systemctl restart (PID 2196773 → 2949086)
- 등록 잡 확인: `외국인/기관 수급 수집: cron[hour='17', minute='10']` + `재시도 (18:00)` 모두 정상
- DB 검증: daily_supply_snapshot 82건, foreign_top_ranking 30건, 의미있는 신호 캡처 (454910 +3168억 / 277810 +2288억 / 006400 +1574억 등)

**② Phase 1-B 구현 시작 (사용자 옵션 1 = 즉시 코드 작업 선택)**
- 사전 검증: `grep -rn "get_stock_full_info"` → 외부 호출자 2곳 (screener.py:300 + __init__.py:20) 영향 없음 확인
- kis_api.py:1081 `get_stock_full_info(skip_supply=False)` 옵션 추가 (기본 False 회귀 안전)
- database.py 헬퍼 5개 신규:
  - `get_supply_snapshots_bulk`: IN 절 + 최근 trade_date 자동
  - `get_theme_supply_aggregate`: 평균 + 양수 비율
  - `is_in_foreign_top`: kind 분리
  - `update_portfolio_supply_context`: 화이트리스트 SQL injection 방어
  - `get_supply_attribution_data`: KST 기반 days 필터
- screener.py:280-380 DB 조회 통합:
  - 진입부 bulk 조회 (supply_map + foreign_top_map)
  - 루프 안 stock_info에 DB 데이터 주입 (skip_supply 분기)
  - 외인 TOP 진입 시 `stock_info["foreign_top_rank"]` 채움 (Phase 1-C 점수 가산 대비)
- 단위 테스트 14건 작성 → 실 DB 검증 통과 (5/12 82건 데이터로)

**③ code-tester 검증 → 심각 3건 즉시 수정**
- **심각 1**: `get_supply_attribution_data`의 SQLite `DATE('now')`는 UTC 기준 → KST 15:00~24:00 호출 시 오늘 데이터 누락. `now_kst() + timedelta` 컷오프로 수정
- **심각 2**: screener.py foreign_top_ranking bulk 조회에 날짜 필터 없어 과거 누적 rank가 현재 신호로 오용. `MAX(trade_date)` 서브쿼리로 가장 최근 날짜만 필터
- **심각 3**: portfolio.`foreign_net_at_buy` vs trade_reviews.`foreign_net_5d_at_buy` 컬럼명 불일치 → Phase 1-D에서 ctx 키 매핑 시 누락 위험. docstring 매핑 가이드 명시 (스키마 무변경)
- 주의 2건 반영: screener.py 가독성 + 테스트 보강 2건 추가 (UTC 회귀 차단 + skip_supply mock 동작 검증)

**④ 회귀 검증 + 배포**
- 단위 테스트 16건 PASS (test_supply_db_helpers.py)
- 회귀 누적 10개 파일 113건+ PASS
- systemctl restart (PID 2949086 → 3305153)
- 커밋 + 푸쉬: `599493a..e014141`, 6 files / 717 insertions / 29 deletions

### 10.2 추가로 발견된 사실들

| 발견 | 사실 |
|---|---|
| KIS FHPTJ04400000 응답 크기 | **30건만 반환** (TOP200 요청해도). settings.SUPPLY_RANKING_TOP_N=200 의미 없음. Phase 1-C `foreign_top_rank ≤ 200` 조건은 사실상 `≤ 30` 으로 작동 |
| portfolio 컬럼 take_profit | `take_profit`/`stop_loss` (테스트 작성 시 잘못 사용한 `target_return`/`max_holding_days` 오류 발견) |
| KIS get_stock_full_info 내부 | 현재가 + `get_investor_trading` + 기술지표 + 재무 4종 호출. `skip_supply=True`만으로 4종 → 3종 줄어듦. 추가 최적화 여지: 기술지표도 DB 캐시 가능 (별도 작업) |
| screener.py 매 테마마다 DB connect/close | code-tester 주의 4번. Phase 2 리팩토링으로 미룸 (현재 성능 임계치 미달) |
| 5/13 날짜 변경 | 작업 중 자정 통과. 5/12 → 5/13. CONTEXT 섹션 9는 5/12, 섹션 10은 5/13 세션 |

### 10.3 의도된 잠재 위험 (Phase 1-D 진입 전 재확인 필요)

1. **컬럼명 매핑 불일치** (심각 3): Phase 1-D `save_trade_review` 자동 보강 시 portfolio.`foreign_net_at_buy` → trade_reviews.`foreign_net_5d_at_buy`로 명시적 매핑 필요. docstring에 가이드 추가했으나 실제 코드 작성 시 매핑 표 재확인
2. **KIS FHPTJ04400000 30건 한계**: Phase 1-C 점수 가산 시 `foreign_top_rank ≤ 200` 조건은 사실상 `≤ 30`. 이대로 둘지 vs 별도 TR로 확장할지 결정 필요 (Phase 1-C 활성화 직전)

### 10.4 다음 단계 (5/13 자연 발화 검증)

**5/13(화) 09:05 — Phase 1-B 검증**
```bash
sudo journalctl -u trading_system --since "09:00" | grep -E "DB 수급 조회|스크리닝 시작|스크리닝 완료"
```
기대 출력:
```
🔍 [반도체] 테마 스크리닝 시작 (10개 종목, RSI≤70)
   📥 [반도체] DB 수급 조회: snapshot=8/10, foreign_top=2
✅ [반도체] 스크리닝 완료: ...
```

**5/13(화) 17:10 — Phase 1-A 게이트 1일차**
- 텔레그램 알림 수신: `📊 수급 수집 완료 (2026-05-13)`
- DB: `daily_supply_snapshot WHERE trade_date='2026-05-13'` 행 수 ≥ 100

**모두 정상이면**: Phase 1-B½ Shadow Run 2주 진입 (Task #4)

### 10.5 새 대화에서 이어 받는 방법

다음 세션 시작 시 권장 프롬프트:

> "/resume supply-signal-integration. 5/13 09:05 + 17:10 결과 확인."

또는 게이트 통과 시:

> "/resume supply-signal-integration. Phase 1-B 게이트 통과 확인. Phase 1-B½ Shadow Run 진입해줘."

문서 우선순위:
1. CHECKLIST.md (현재 진행 상태)
2. CONTEXT.md 섹션 10 (이번 세션 + 다음 단계)
3. PLAN.md (전체 그림 + Phase 1-B½ Shadow Run 상세)
4. change_log.md 마지막 2줄 (Phase 1-A + Phase 1-B 배포 이력)

### 10.6 컨텍스트 사용량

- 현재 누적 사용량: 약 540K tokens (1M 한도 내, 다음 세션 위해 새로 시작 권장)
- **다음 세션 권장**: 새 대화에서 `/resume`으로 시작 (CONTEXT.md 자동 로드)
- 본 세션은 Phase 1-A 사후 검증 + Phase 1-B 완료에서 자연 종료점 도달

### 10.7 Task 상태

```
#1 [completed] 3문서 생성
#2 [completed] Phase 1-A 데이터 파이프라인
#3 [completed] Phase 1-B DB 조회 통합  ← 본 세션 완료
#4 [pending]   Phase 1-B½ Shadow Run 2주  ← 다음 단계
#5 [pending]   Phase 1-C 점수 활성화 점진 배포
#6 [pending]   Phase 1-D AI Verifier + 매수 박제 hook
```

### 10.8 운영 환경 상태

- 봇 PID: 3305153 (5/13 02:49:25 시작, Phase 1-A + 1-B 코드 모두 로드)
- DB 스키마: v16
- 잡 등록 상태:
  - 17:10 supply_collection: 등록 OK
  - 18:00 supply_collection_retry: 등록 OK (50건 이상 시 중복 호출 방어)
  - 09:05 stock_screening: Phase 1-B 적용 (DB 우선 + KIS 폴백)
- 토글 상태 (모두 기본값):
  - SUPPLY_SIGNAL_ENABLED=True
  - SUPPLY_SCORE_OBSERVE_ONLY=True (Shadow Run 대기 모드)
  - SUPPLY_SCORE_MAX=0.0 (총점 미반영)
  - SUPPLY_STRENGTH_ENABLED=False
  - AI_PROMPT_SUPPLY_ENHANCED=True (Phase 1-D에서 활용)
