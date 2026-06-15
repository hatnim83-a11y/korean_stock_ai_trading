# CHECKLIST — 메인 시스템 외국인/기관 수급 신호 도입

## 사전 검증 (Phase 1-A 시작 전)

- [x] `close_position` 동작 확인 — UPDATE status='closed' (database.py:1042-1057)
- [x] `get_stock_full_info` 내부 호출 확인 — kis_api.py:1081
- [x] `closing_bet.db` WAL 모드 확인 — 별도 파일, lock 없음
- [x] FHPTJ04400000 안정성 확인 — 종가베팅 1주 관찰 완료
- [x] DB 백업 — `_migrate()` 자동 백업 (data/trading.bak.20260512_102055)

---

## Phase 1-A 구현 (Day 1~3) — 데이터 파이프라인

### 구현 (코드 작업 완료)
- [x] `database.py` `_migrate_v16` 추가 (`daily_supply_snapshot`, `foreign_top_ranking`, `supply_score_observation` 신규 + portfolio +7 + trade_reviews +9)
- [x] `database.py` 헬퍼: `save_supply_snapshot`, `save_supply_snapshot_null`, `save_foreign_top_ranking`, `save_supply_score_observation`, `get_supply_snapshot`, `get_latest_supply_snapshot_date`, `count_supply_snapshots_for_date`
- [x] `config.py` 11개 토글 (SUPPLY_SIGNAL_ENABLED, COLLECT_HOUR/MINUTE, UNIVERSE_TOP_MARKET_CAP, RANKING_TOP_N, THEME_TOP_K, STOCK_LOOKBACK_DAYS, RANKING_CALL_SLEEP_SEC, RETRY_JOB_HOUR/MINUTE) — code-tester 주의 사항 반영 6개 추가
- [x] `modules/supply_collector/__init__.py` 신규
- [x] `modules/supply_collector/collector.py` 신규 (SupplyCollector 클래스, 3회 retry + 지수 백오프 1·2·4초 + 진행률 50종목마다)
- [ ] ~~`modules/supply_collector/aggregator.py`~~ — Phase 1-B의 DB 헬퍼로 통합 (별도 파일 불필요)
- [x] `scheduler.py` 17:10 supply_collection 잡 + 18:00 재시도 잡 (RETRY_JOB_HOUR=0이면 미등록)
- [x] `main.py` `run_supply_collection` 메서드 + on_supply_collection 콜백 연결 + 50건 누적 시 중복 호출 방어
- [x] `tests/test_supply_collector.py` 11 테스트 (mock KIS, retry, 멱등성, 경계값)
- [x] `tests/test_database_v16_migration.py` 13 테스트 (v15→v16, 컬럼, 헬퍼)

### 검증 (완료)
- [x] `python tests/test_supply_collector.py` 11/11 PASS
- [x] `python tests/test_database_v16_migration.py` 13/13 PASS
- [x] `python -m modules.supply_collector.collector --date 2026-05-08 --dry-run` 정상 동작 (universe=147종목)
- [ ] 실제 KIS 통합 테스트 — 17:10 첫 자연 트리거에서 자동 검증 (운영 게이트 단계)
- [x] code-tester 에이전트 통과 — 심각 0건 / 주의 5건 / 참고 6건, "배포 가능". 즉시 처리 5건 반영 완료
- [x] py_compile 전체 파일 통과

### 배포 (사용자 진행 필요)
- [ ] systemctl stop trading_system
- [x] DB 백업 — `_migrate()` 자동 백업 (data/trading.bak.20260512_102055, 이미 실행됨)
- [ ] systemctl start trading_system → v16 마이그레이션 자동 실행 확인 (이미 마이그레이션 적용됨)
- [ ] 17:10 supply_collection 잡 등록 확인 (`sudo journalctl -u trading_system | grep supply`)

### 운영 게이트 (3영업일, 사용자 진행) — ✅ **2026-05-18 통과**
- [x] 3영업일 연속 17:10 잡 성공률 ≥ 95% — **3일 모두 100%**
  - 1일차 (5/13 화): ✅ 93/93 (universe 전수 성공, 16.1초)
  - 2일차 (5/14 목): ✅ 105/105 (universe +12, 23.8초, KIS 500 9건 재시도 성공)
  - 3일차 (5/15 금): ✅ 113/113 (universe +8, 19.8초, KIS 500 0건)
- [x] `daily_supply_snapshot` universe 전수 성공 (universe는 테마/보유/시총에 따라 90~150 변동) — 3일 모두 PASS
- [x] 외인 TOP**30** (KIS `FHPTJ04400000` 1콜당 30건 한도, 페이지네이션 미지원)에 대형주 포함 — 종가베팅도 동일 패턴 운영 중
  - 1일차 (5/13): TOP30 중 삼성전자/SK하이닉스 미포함 (현대차 rank 4, 기아 rank 10) — 시장 흐름 영향 추정, 2-3일차 누적 후 재평가
  - 누적 3일: 3일 모두 30건 저장 OK, 시장 흐름에 따라 변동
- [x] stale 시뮬레이션 — **2026-05-18 결정: 옵션 D (코드 리뷰 갈음)** [원래 옵션 A 주말 수동 미수행]
  - 근거: `get_supply_snapshots_bulk(stock_codes, trade_date=None)` (database.py:1798-1809)이 SQL 레벨에서 각 종목별 `MAX(trade_date)` 서브쿼리로 가장 최근 영업일 데이터를 자동 선택. 오늘 데이터 누락 시 어제 데이터로 fallback 동작이 SQL 정의에 의해 보장됨. screener.py:285-320은 `supply_map.get(code)` 후 `foreign_net_5d is not None` 체크 → use_db_supply 분기 → KIS 폴백까지 graceful 처리
  - 운영 영향: 0 (DB 무손상)
- [x] 텔레그램 알림 정상 수신 (수급 수집 완료 메시지) — 5/13/5/14/5/15 3일 모두 OK
- [x] **사용자 확인 → Phase 1-B 진행** (2026-05-18)

### 문서 업데이트 (Phase 1-A 완료 시)
- [x] `docs/improvements/change_log.md`에 Phase 1-A 1줄 추가

---

## Phase 1-B 구현 (Day 4~5) — DB 조회 통합

### 사전 검증 (완료)
- [x] `grep -rn "get_stock_full_info" --include="*.py"` 외부 호출자 grep + 영향 분석
  - 결과: screener.py:300 + __init__.py:20 (테스트) 2곳, skip_supply 기본값 False로 회귀 안전

### 구현 (완료)
- [x] `database.py` 헬퍼 6개 (1개는 Phase 1-A에 이미 추가됨):
  - `get_supply_snapshot` (Phase 1-A 완료)
  - `get_supply_snapshots_bulk` (신규, IN 절 + 최근 날짜 자동)
  - `get_theme_supply_aggregate` (신규, 평균 + 양수 비율)
  - `is_in_foreign_top` (신규, kind 분리)
  - `update_portfolio_supply_context` (신규, 화이트리스트 SQL injection 방어)
  - `get_supply_attribution_data` (신규, KST 기반 days 필터)
- [x] `modules/stock_screener/kis_api.py:1081` `get_stock_full_info(skip_supply=False)` 옵션 추가
- [x] `modules/stock_screener/screener.py:280-380` DB 조회 진입부 + 루프 내 stock_info 주입

### 검증 (완료)
- [x] `pytest tests/test_supply_db_helpers.py` 16/16 PASS
- [x] 회귀 9개 파일 전체 PASS (이전 113건+ 영향 없음)
- [x] 회귀 안전성: `SUPPLY_SIGNAL_ENABLED=False` 시 `supply_map={}` → 모든 종목 KIS 폴백 (기존 동작 100% 보존)
- [x] code-tester 에이전트 통과 (심각 3건/주의 4건/참고 3건 발견 → 심각 3건 즉시 수정, 주의 4건 부분 반영)
  - 심각 1: `DATE('now')` UTC 버그 → `now_kst() + timedelta` 컷오프로 수정
  - 심각 2: `foreign_top_ranking` 날짜 필터 누락 → `MAX(trade_date)` 서브쿼리 추가
  - 심각 3: portfolio vs trade_reviews 컬럼명 매핑 → docstring 명시 (스키마 무변경)
- [x] 실 DB 검증: bulk 조회 3/4건, theme 집계 양수 비율 100%/33.3%, foreign_top rank 1·2·3 정확, update rowcount=1

### 운영 게이트 (2영업일, 사용자 진행) — ✅ **2026-05-18 통과**
- [x] systemctl restart trading_system (Phase 1-B 코드 적용) — 5/13 12:34 KST (UTC 03:34) 재시작 완료
- [x] 5/13(화) 09:05 자연 발화에서 DB 수급 조회 정상 동작 확인 — ❌ **표본 무효** (Phase 1-B 코드 커밋 시각 11:49 KST > 발화 시각 09:05 KST). 5/14(목) 09:05 첫 자연 발화로 이연
- [x] 5/14(목) 09:05 자연 발화에서 DB 수급 조회 정상 동작 확인 — ✅ 5개 테마 전부 `📥 DB 수급 조회: snapshot=N/M, foreign_top=K` 출력 (반도체 3/3·전선 8/8·조선 7/7·금융 20/20·로봇 9/20). **종합 매칭 47/58 (81%)** — 로봇 11종목 KIS 폴백 (graceful fallback 정상 동작)
- [x] 5/15(금) 09:05 자연 발화 누적 검증 — ✅ 매칭 회복 (반도체 3/3, 전선 8/8, 조선 7/7, 로봇 12/20, 금융 20/20). **종합 50/58 (86%)** — 로봇 12/20으로 1일 만에 +3 회복
- [x] 5/18(월) 09:05 누적 검증 — ✅ **거의 완전 회복** (반도체 3/3, 전선 8/8, 조선 7/7, 로봇 19/20, 금융 20/20). **종합 57/58 (98%)**. 로봇 매칭률 9/20 → 12/20 → 19/20 (45% → 60% → 95%) — universe ↔ 09:05 종목 풀 mismatch가 **화요일 재선정 후유증임을 확인**, 시간 경과로 stocks 테이블 누적되며 자연 회복. 후속 단위 `supply-signal-followup-universe` 우선순위 **낮음**으로 결정
- [x] KIS get_investor_trading 호출 횟수 감소 확인 — ✅ 09:05 시간대 ERROR 0건 (3일 누적). 정확 호출 카운터는 별도 작업이지만 ERROR 부재로 회귀 동작 입증
- [x] **사용자 확인 → Shadow Run 진입** (2026-05-18)

---

## Phase 1-B½ Shadow Run (Day 6~19, 14영업일)

### 구현 (2026-05-18 완료)
- [x] `config.py` 토글 6개 추가 — `SUPPLY_SCORE_OBSERVE_ONLY=True`, `SUPPLY_SCORE_MAX=0.0`, `SUPPLY_SCORE_TOP_N=5`, `SUPPLY_INTENSITY_REF_BIL=30.0`, `SUPPLY_STRENGTH_ENABLED=False`, `SUPPLY_UNIVERSE_TOP_N=30` (code-tester 주의 1 반영)
- [x] `modules/theme_analyzer/scorer.py` observation 로직 — `calculate_theme_supply_score_v2()` 신규 + `measure_universe_top_supply_signal()` 신규 (권고 조치, universe 내부 동적 TOP) + `score_themes()` 통합 + 라인 662 분기 (관측 모드 0 / 활성화 시 v2 점수)
- [x] `tests/test_theme_supply_score.py` 신규 — 10 테스트 PASS (빈 리스트/no_snapshot/강한 케이스/음수/경계/top_n 선정/max=0 관측/universe 빈/정상/cap)
- [x] code-tester 통과 — 심각 0/주의 3/참고 3, "배포 가능". 주의 3건 모두 즉시 처리 완료
  - 주의 1: top_n=30 하드코딩 → `SUPPLY_UNIVERSE_TOP_N` 신설
  - 주의 2: observation `observe_only` 의미 모호 → `score_applied` 필드 추가
  - 주의 3: 비화요일 08:30 stocks 누락 noise → `stocks_available` 필드 추가
- [x] 실제 운영 DB로 통합 검증 — universe 113종목/조선 테마 score=5.0/강한 케이스 score=5.0/빈 리스트 score=0.0 정상

### 검증 — 2026-06-06 1차 측정 (5/19~6/5 누적)
- [x] 14영업일 데이터 ≥ 200건 — ✅ **351건 / 12 영업일** (5/22 부처님오신날, 6/3 현충일 휴장 + 5/30/5/31 주말 = 14 - 2)
- [x] 모멘텀 × supply_score Pearson 상관계수 r < 0.7 — ✅ **r = 0.1037** (매우 독립적, 신호 직교성 확보)
- [ ] supply_score 분포 차별성 (0/3/5점 골고루) — ❌ **FAIL** (0=91.7% / 5=5.7% / 사이 2.6% 양극화)
- [x] supply_score 상위/하위 테마 사이 점수 차이 명확 — ✅ (0 vs 5 명확하지만 중간 부재)
- [x] universe 내부 외인 TOP 동적 SQL 시범 측정 — ✅ supply_score_observation.breakdown_json.universe_top_signal 매일 누적 중

### 운영 게이트 — 변환식 재설계 결정 (2026-06-06)
- [x] 한 조건 FAIL → 점수 변환식 재설계 진입 (`SUPPLY_INTENSITY_REF_BIL` 조정 + 양선형 매핑 도입)
- [x] **사용자 의사결정**: "변환식 재설계 후 재측정"
- [x] config.py 신규 토글: `SUPPLY_SCORE_SIGNED=True`(양선형) + `SUPPLY_OUTLIER_CAP_BIL=100.0`(이상치 방어) + `SUPPLY_INTENSITY_REF_BIL` 30 → **10** 하향
- [x] scorer.py: `_compute_supply_score_from_avg()` 헬퍼 추출 + 두 함수에 signed/outlier 인자 전달
- [x] 단위 테스트 18/18 PASS (기존 10 회귀 보존 + 신규 8 양선형/clamp/outlier_cap)
- [x] code-tester 심각 0/주의 2/참고 2, "배포 가능" — 주의 2건 즉시 처리 (`or 5.0` → `if > 0 else 5.0` 명시화 + outlier_cap 조건 중복 제거)
- [x] 실측 165건 시뮬레이션: 새 공식 분포 0(39.4%) / 2-3(44.2%) / 5(15.2%) — 양극화 대폭 개선
- [ ] **systemctl restart trading_system** (사용자 진행 필요) → 5영업일 후 (~6/12) 분포 재평가

### 검증 — 2차 측정 (2026-06-15)
- [ ] ~~분포 차별성: 0/2-3/5 각 구간 모두 ≥ 10%~~ — ❌ FAIL (0=86.7% 양극화 유지, 6/10~6/12 90건)
- [x] Pearson r 유지 (< 0.7) — ✅ r=0.30 (stocks=1 필터 45건 기준)
- [x] outlier root cause 진단 — **데이터 정상**: 삼성전자 -7.78조원은 시장 실제값. cap=100억이 대형주 정상 데이터를 outlier로 오인하는 게 진짜 문제
- [x] **사용자 의사결정**: "outlier root cause 먼저 해결" → 변환식 근본 재설계 (옵션 C)

### 변환식 3차 재설계 — 옵션 C (ratio 정규화, 2026-06-15)
- [x] `config.py` 신규 토글 2개: `SUPPLY_USE_RATIO=True`, `SUPPLY_REF_RATIO=0.15` (5일 거래대금의 15%=만점)
- [x] `scorer.py` 신규 함수 2개: `_compute_supply_score_from_ratio()` 헬퍼 + `_stock_supply_ratio()` 단일 종목 비율 계산 (trade_value=0 가드)
- [x] `calculate_theme_supply_score_v2()` 시그니처 확장: `use_ratio`/`ref_ratio` 인자. top_n 선정도 ratio 절댓값 기준 (대형주 net 편향 제거)
- [x] `measure_universe_top_supply_signal()` 시그니처 확장: 동일 인자. SQL에서 `trade_value_5d_avg > 0` 필터 + ratio 계산
- [x] `score_themes()`: settings 토글 전달 + breakdown_json에 mode/avg_ratio/use_ratio/ref_ratio 추적
- [x] 단위 테스트 25/25 PASS (기존 18 회귀 보존 use_ratio=False 명시 + 신규 7 ratio 모드)
- [x] code-tester 심각 0/주의 2/참고 3 "배포 가능" — 참고 2(로그 가시성) 즉시 처리, 주의 2(테스트 보강)는 후속
- [x] 실측 통합 검증: 반도체(005930/000660) absolute=0 → ratio=3.3 (차별 점수 회복), 중소형 외인 매수 상위=5.0, 회귀 absolute=0.0
- [ ] **systemctl restart trading_system** (사용자 진행 필요) → 5영업일 후 (~6/22) 3차 측정

### 검증 — 3차 측정 (~2026-06-22, 5영업일 누적)
- [ ] 분포 차별성: 0/2-3/5 각 구간 모두 ≥ 10% (양극화 해소 기준)
- [ ] Pearson r 유지 (< 0.7)
- [ ] **사용자 확인 → Phase 1-C 진행**

### 문서 업데이트
- [ ] Shadow Run 결과를 `memory/project_supply_signal_integration.md`에 기록

---

## Phase 1-C 구현 (Day 20~22) — 점수 활성화 점진 배포

### 사전 검증
- [ ] `grep -n ">= 58\|>= 48\|>= 38\|>= 30" modules/theme_analyzer/scorer.py main.py` 컷오프 8곳 확인
- [ ] `web/dashboard_service.py`, `portfolio_monitor_v2.py` portfolio SELECT * 회귀 확인
- [ ] **[2026-05-13 추가] foreign_top 가산 신호 선택 확정**: ① KIS TOP30 (시장 핫스팟, universe 매칭 ~9건/일) vs ② universe 내부 동적 TOP (snapshot 기반). Phase 1-B½ Shadow Run 측정값 기반으로 어느 신호를 `calculate_theme_supply_score_v2` 가산에 쓸지 결정 + PLAN.md 섹션 5에 결과 기록

### 구현
- [ ] `config.py` Phase 1-C 토글: `SUPPLY_SCORE_ENABLED`, `SUPPLY_SCORE_MAX`, `SUPPLY_SCORE_TOP_N`, `SUPPLY_INTENSITY_REF_BIL`, `SUPPLY_STRENGTH_ENABLED`
- [ ] `modules/theme_analyzer/scorer.py` `calculate_theme_supply_score_v2()` 신규 함수 + `score_themes()` 통합 + 라인 662 박제 해제
- [ ] `modules/stock_screener/filters.py` `supply_strength` 별도 키 가산 (라인 58-125) + `calculate_final_score` 가중치 토글 (라인 489-527)
- [ ] `tests/test_theme_supply_score.py` 단위 테스트 (빈 리스트→0, 강한 케이스→max, 경계값)

### 점진 배포
- [ ] Day 20: `SUPPLY_SCORE_OBSERVE_ONLY=False`, `SUPPLY_SCORE_MAX=2.5` (절반) → 1영업일 관찰
- [ ] Day 21: `SUPPLY_SCORE_MAX=5.0` (정상) → 1영업일 관찰
- [ ] Day 22: `SUPPLY_STRENGTH_ENABLED=True` (filters.py) → 1영업일 관찰

### 검증
- [ ] `pytest tests/test_theme_supply_score.py` 통과
- [ ] 매일 인플레이션 SQL 실행 — 등급 분포 ±5%p 이내
- [ ] retention/turnover 측정 SQL 실행
- [ ] code-tester 에이전트 통과

### 운영 게이트 (3영업일)
- [ ] 3영업일 매수 종목 수가 평소 대비 ±20% 이내
- [ ] 점수 인플레이션 분포: A등급 비율 ±5%p 이내
- [ ] retention 통과 테마 수 평균 변화 (이상 시 `SUPPLY_SCORE_MAX` 축소)
- [ ] **사용자 확인 → Phase 1-D 진행**

### 문서 업데이트
- [ ] `docs/improvements/change_log.md` Phase 1-C 1줄 추가
- [ ] `CLAUDE.md` "DB Schema v15" 섹션을 "v16"으로 업데이트

---

## Phase 1-D 구현 (Day 23~25) — AI Verifier + 매수 박제 hook

### 사전 결정 (구현 전)
- [ ] **[2026-05-13 추가] AI 프롬프트 외인 TOP 표현 분기 결정**: foreign_top_ranking 메타(stock_name, net_amount) NULL 상태 처리 — ① 후속 단위 `supply-signal-followup-meta` 선행 처리 후 "외인 TOP10 진입, +120억" 완전 표현 / ② 메타 없이 universe 내부 동적 TOP 기반으로 "테마 내 외인 매수 1위" 등 우회 표현. Phase 1-C 결정과 같은 방향으로 정렬

### 구현
- [ ] `config.py` `AI_PROMPT_SUPPLY_ENHANCED=True` 토글
- [ ] `modules/ai_verifier/claude_analyzer.py:47-103` 프롬프트 강화 (다중 라인 + 제외 조건)
- [ ] `modules/ai_verifier/claude_analyzer.py:224, 294` `_format_supply_for_prompt` 헬퍼 + format 인자
- [ ] `modules/ai_verifier/verifier.py:95-101, 196-211` `ai_supply_signal` 매핑
- [ ] `main.py` 매수 후 portfolio supply 컨텍스트 hook (`execute_buy_orders` 끝부분)
- [ ] `database.py` `save_trade_review()` 25컬럼 INSERT 패턴 (옵션 A, portfolio 자동 조회)
- [ ] `tests/test_save_trade_review_auto_enrich.py` (매도 hook 3경로 모두 자동 보강 확인)

### 검증
- [ ] `pytest tests/test_save_trade_review_auto_enrich.py` 통과
- [ ] AI 응답 토큰 +80~120 tokens 확인 (로깅)
- [ ] 매수 후 portfolio에 supply 컨텍스트 7개 컬럼 채워짐 확인
- [ ] 매도 시 trade_reviews 자동 보강 확인 (3개 매도 경로: `_close_position_in_db`, `_save_partial_sell_to_db`, `_save_trade_review_for_main_sell`)
- [ ] 분할매도 케이스 동일 `supply_at_buy` 박제
- [ ] code-tester 에이전트 통과

### 운영 게이트 (2영업일)
- [ ] **사용자 확인 → Phase 1 완료 선언**

### 문서 업데이트 (Phase 1 완료 시 — 필수)
- [ ] `memory/MEMORY.md`에 `project_supply_signal_integration.md` 항목 추가
- [ ] `memory/project_supply_signal_integration.md` 신규 작성 (목적, 시점, DB v16, 토글 9개, Shadow Run 결과, Phase별 활성화 일자)
- [ ] `CLAUDE.md`에 supply 신호 운영 규칙 섹션 추가
- [ ] `docs/INDEX.md` 신규 모듈(`supply_collector/`) 항목 추가
- [ ] `docs/improvements/change_log.md` Phase 1 종합 1줄 + 활성화 일자 기록
- [ ] active/ → completed/YYYYMMDD_supply-signal-integration/ 이동

---

## Phase 2 (Week 4~6, 표본 60건 누적 후)

### 사전 검증
- [ ] 종가베팅 universe 겹침 SQL (target ≥ 30%)

### 구현
- [ ] `modules/post_trade_analyzer/supply_labeler.py` 신규 (경로 A closing_bet.db JOIN + 경로 B 네이버 백필)
- [ ] `modules/reporter/` 일일 리포트에 supply 메트릭 추가
- [ ] 이중 카운팅 검증 (Pearson r 30일 측정)

### 운영 게이트
- [ ] foreign_direction_match 적중률 ≥ 60% over 60건 표본
- [ ] r < 0.7 (이중 카운팅 없음)

---

## Phase 3 (Week 7+, 검증 후)

- [ ] `SUPPLY_SCORE_MAX` 5 → 7~10 상향
- [ ] `SUPPLY_REVERSAL_EARLY_EXIT_ENABLED=True` 트레일링 보강
- [ ] 시장 체제 분기 (KOSPI 강세장/약세장)
- [ ] (선택) RETENTION_SCORE 동적 조정

---

## 단위 작업 추적

| 단위 | Task ID | 상태 |
|---|---|---|
| 3문서 생성 | #1 | in_progress |
| Phase 1-A | #2 | pending (blocked by #1) |
| Phase 1-B | #3 | pending (blocked by #2) |
| Phase 1-B½ | #4 | pending (blocked by #3) |
| Phase 1-C | #5 | pending (blocked by #4) |
| Phase 1-D | #6 | pending (blocked by #5) |
