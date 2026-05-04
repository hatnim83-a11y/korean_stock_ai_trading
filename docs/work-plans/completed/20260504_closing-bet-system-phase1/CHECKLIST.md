# 종가베팅 시스템 — CHECKLIST.md

> 한 단위 완료 시마다 `[x]` 체크. 모든 단위 완료 후 `completed/YYYYMMDD_closing-bet-system/` 으로 아카이브.

---

## 구현

### Phase 0. 사전 준비

- [x] **0-A.** 디렉토리 스켈레톤 + DB 스키마 + settings.yaml *(2026-05-03 완료)*
  - [x] `closing_bet_system/` 트리 생성
  - [x] 모든 서브디렉토리에 `__init__.py`
  - [x] `config/settings.yaml` (13개 섹션)
  - [x] `storage/db.py` (`_migrate_v1`, 4테이블 + 5인덱스)
  - [x] `data/closing_bet.db` 초기화 + v1 적용
  - [x] code-tester 검증 → 심각 2건 + 주의 일부 수정 완료
    - yaml.YAMLError 폴백 추가
    - PyYAML requirements.txt 등록
    - 신규 빈 DB 백업 스킵
    - score_max / transaction_tax 주석 보강
    - candidates UNIQUE 미정의 의도 CONTEXT.md 명시
- [x] **0-B.** wrapper 3종 + **fund_guard 미들웨어 (P0)** *(2026-05-03 완료)*
  - [x] `infra/kis_client.py` (KISApi/KISOrderApi 싱글톤 + `get_total_account_value`/`get_orderable_cash`/`get_held_stock_codes`)
  - [x] `infra/telegram_client.py` (신규 봇 토큰 인자 주입 + **스윙 봇 폴백 차단** 채널 격리)
  - [x] `infra/swing_db_reader.py` (`mode=ro` 강제, `get_swing_holding_codes`/`get_swing_today_buy_count`)
  - [x] `infra/fund_guard.py` (P0-1 핵심, 7단계 검사, 단일 connection 통합)
  - [x] `.env` 에 CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID 추가 *(2026-05-04 값 주입 완료, chat_id는 스윙과 동일 단일 채널)*
  - [x] `scripts/test_closing_bet_fund_guard.py` 단위 테스트 **10케이스** 통과
  - [x] code-tester 검증 → 심각 3건 + 주의 1건 수정 완료
    - 검사 순서: 스윙 중복을 자금/한도 검사 앞으로 이동
    - DB 3회 connection → `_fetch_db_state()` 단일 connection 통합 (TOCTOU 방지)
    - kis_client 에 `get_balance()` 반환 키 주석 명시
    - 매직 넘버 상수화 (`_CONSERVATIVE_LARGE_AMOUNT/COUNT`)
    - 추가 입력 검증: float / bool / 정수 ticker 차단
  - [x] ~~`modules/reporter/telegram_notifier.py:37` __init__ 인자 주입~~ — **이미 구현되어 있음** (별도 리팩터 불필요)
  - 잔여 항목 5건 → CONTEXT.md "0-B 잔여 개선 항목" 으로 이월 (Phase 1 진입 전 처리)
- [x] **0-C.** Pre-Phase 1 백테스트 데이터/시뮬 (Layer 2 sanity check) *(2026-05-03 완료)*
  - [x] `closing_bet_system/backtest/daily_proxy_backtest.py` (332줄)
    - `compute_layer2_features()`: Close Strength, ATR(14d SMA), 20일선, 거래량 서프라이즈, 52주 신고가 근접
    - `score_candidates()`: 4점 만점 + ATR 과열 1.8 필터 + **volume>0 필터** (거래정지 종목 후보 자격 박탈)
    - `label_outcomes()`: PRD 12-1 라벨 (gap_up ≥ +0.6%, morning_exit ≥ +1.2%, stop_risk ≤ -1.0%)
    - `run_single_symbol` / `run_universe` / `save_results` (CSV)
  - [x] 19종목 × 3년 백테스트 실행 → 13,889행 / 후보 7,440건
  - [x] 결과 저장: `data/backtest_cache/daily_proxy_results.csv` (5MB, 30컬럼)
  - [x] code-tester 검증 → 거래정지 필터(volume>0) 즉시 수정, ATR 역선택 발견 0-D에 반영
  - [x] 점수별 hit rate: 1점 56.5% / 2점 51.2% / 3점 56.1% / 4점 61.8%
- [x] **0-D.** Pre-Phase 1 백테스트 리포트 *(2026-05-03 완료)*
  - [x] `closing_bet_system/backtest/sanity_check_report.py` (분석 + 자동 markdown 생성, 547줄)
  - [x] 0-C 결과 CSV 로드 + 비용 차감 후 EV 계산 (왕복 0.41%)
  - [x] ATR 필터 역선택 명시 (필터된 4점 83% vs 통과 4점 61.8%)
  - [x] gap_up AND morning_exit / morning_exit only 분리표 (P(morning|gap_up)=88%)
  - [x] 종목별 분포 (대형주 편향 점검)
  - [x] 한계 6항목 명시 (일봉/Layer1 측정불가/Layer2 일부/52주/대형주/EV모델)
  - [x] 의사결정 분기 표 (Phase 1 진입 / 보류 / 재설계)
  - [x] 사용자 검토용: `docs/work-plans/active/closing-bet-system/0d_sanity_check_report.md` (7,862자)
  - [x] code-tester 검증 → 심각 2건(섹션 2/7 모순 제거 + EV 하향 편향 명시) + 주의 4건 즉시 수정
  - **종합 판정**: ⚠️ "기각 근거 없음" 수준 → Phase 1 forward test 진행 권장

### Phase 1. 데이터 수집 + 알림형

- [x] **1-1.** `engines/cost_slippage_engine.py` *(2026-05-03 완료)*
  - [x] PRD 7-2 순수익률 공식 정확 구현 (break-even 검증 -0.0012% 오차)
  - [x] `CostBreakdown` dataclass (frozen, 14필드)
  - [x] `round_trip_cost()` / `minimum_target_return()` / `compute_pnl()` / `to_db_payload()`
  - [x] 슬리피지 3가지 모드 (None=추정/0=실거래/명시값=사후측정)
  - [x] 싱글톤 + Thread-safe (`threading.Lock` double-checked locking)
  - [x] 단위 테스트 12케이스 (PRD 공식 검증/break-even/슬리피지 모드/입력 검증/싱글톤 thread safety)
  - [x] code-tester 검증 → 심각 0건, 주의 2건 즉시 수정 (bool shares 차단 + Lock 추가)
- [x] **1-2.** `collectors/kis_intraday_flow_collector.py` *(2026-05-03 완료)*
  - [x] `IntradayFlowSnapshot` frozen dataclass (`is_today` + `foreign_days_collected` 추가)
  - [x] `KisIntradayFlowCollector` (의존성 주입 + 지연 로딩)
  - [x] PRD 5-Layer 1 4지표 중 가용 2개 + Phase 2 placeholder 2개
  - [x] `to_layer1_features()` candidate_features schema 일치
  - [x] **날짜 검증** (daily[0] vs today KST → is_today)
  - [x] **close_price=0 가드** (장 시작 직후 미체결 처리)
  - [x] **frozen + dict field hash 안전성** (`field(hash=False, compare=False)`)
  - [x] **asyncio.to_thread 권고** docstring 명시 (1-8 통합용)
  - [x] 단위 테스트 14케이스 통과 (정상/Phase2 placeholder/잘못된 ticker/빈응답/1일치/stale_date/close=0/손상필드/API예외/다종목격리/_to_float/snapshot_time/empty universe/hash 안전성)
  - [x] code-tester 검증 → 심각 2건 + 주의 3건 즉시 수정
- [x] **1-3.** `collectors/kis_price_volume_collector.py` *(2026-05-04 완료)*
  - [x] `PriceVolumeSnapshot` frozen dataclass (raw OHLCV + 윈도우통계 + Layer 2 6컬럼 + 보조 above_ma20)
  - [x] `KisPriceVolumeCollector` (의존성 주입 + 지연 로딩 + 윈도우 상수: ATR_PERIOD=14 / MA_PERIOD=20 / VOL_AVG_PERIOD=20 / DEFAULT_DAILY_COUNT=60)
  - [x] PRD 5-Layer 2 6지표 중 가용 4개 + Phase 2 placeholder 2개
    - close_strength = (close-low)/(high-low)
    - upper_shadow_atr = (high-close)/atr14
    - volume_surprise = volume/vol_avg20 (백테스트 일관성: volume=0 포함)
    - atr_overheat = (close-prev_close)/atr14
    - last_30min_vwap_position / closing_buy_sell_ratio → Phase 2 (분봉/체결강도 필요)
  - [x] `_compute_atr` True Range SMA (거래정지일 발견 시 보수적 None — 백테스트와 의도적 차이, docstring 명시)
  - [x] **날짜 검증** (daily[0] vs today KST → is_today)
  - [x] **close=0 / 윈도우 부족 가드** (raw OHLCV 보존, Layer 2 None)
  - [x] **frozen + list field hash 안전성** (`field(hash=False, compare=False)`)
  - [x] **`_to_float` 강화** (None/빈 문자열/NaN/inf/bool 6케이스 차단 — 1-2 대비 bool 추가)
  - [x] `to_layer2_features()` candidate_features schema 1:1 매핑 (DB 6컬럼)
  - [x] 단위 테스트 19케이스 통과 (정상/Phase2 placeholder/잘못된 ticker/빈응답/윈도우부족/close=0/high=low/손상필드/거래정지/API예외/다종목격리/_to_float/snapshot_time/empty universe/hash 안전성/ATR 정확성/ATR 윈도우 미달/above_ma20/no_today_first)
  - [x] code-tester 검증 → **심각 0건 / 주의 2건** (ATR 거래정지 docstring + vol_avg20 비대칭 주석 즉시 보강) → 배포 가능
- [x] **1-4.** `engines/signal_score_engine.py` (Layer 1 가중치 0) *(2026-05-04 완료)*
  - [x] `ScoreBreakdown` frozen dataclass (cond_* 11개 + subscore 3개 + raw_total + weighted_total + decision + missing_inputs)
  - [x] `SignalScoreEngine` (PRD 6-1 단순 카운트 11점 만점)
    - Layer 1 (수급) 4 조건: inst/foreign_3d/program/closing_flow > 0
    - Layer 2 (가격/거래량) 4 조건: close_strength≥0.85 / vol_surprise≥2.0 / above_ma20 / closing_buy_sell≥1.2
    - Layer 3 (모멘텀) 3 조건: near_52w_high / theme_leadership_rank≤3 / has_positive_disclosure
  - [x] **하드 필터** (PRD 5-Layer 2): atr_overheat > 1.8 → EXCLUDED, **None/NaN/inf/문자열도 보수적 EXCLUDED**
  - [x] **가중치 정책 (P2-6)**: layer1_weight=0.0 (Phase 1) → Phase 2 활성화 시 1.0
    - Phase 1 weighted max = 7 (L2+L3) — ENTRY 사실상 미발생, ALERT 만 가능
  - [x] **decision 우선순위**: EXCLUDED > MAX_SIZE_ENTRY (≥9 + market_ok) > ENTRY (≥8) > ALERT (≥7) > BELOW_THRESHOLD
  - [x] **`from_settings(score_settings: dict)`**: settings.yaml `score:` 섹션 그대로 주입
  - [x] **`__init__` 검증**: 음수/bool 가중치, alert>entry>max_position 순서 위반 → ValueError
  - [x] **결손 입력 처리**: dict None / 키 누락 / bool/int/문자열/NaN/inf → cond=None + missing_inputs 누적
  - [x] **bool/int strict 분리**: above_ma20=1 (int) → None (의도된 strict bool, 의미 모호 차단)
  - [x] **atr_overheat_value 정규화** (P1 fix): 항상 float/None — 1-6 candidate_features.atr_overheat REAL 컬럼 안전성
  - [x] 단위 테스트 22케이스 통과 (만점 Phase 1/2 / market_ok 분기 / atr_overheat 5변형 / 임계값 경계 / Layer 1+2+3 / theme_rank 경계 / corrupt 입력 / __init__ 검증 / from_settings / decision 우선순위 / immutable / helpers / atr_overheat 정규화 / scored_at 등)
  - [x] code-tester 검증 → **심각 0건 / 주의 2건** 모두 즉시 수정 (atr_overheat_value 정규화 + theme_rank docstring) → 배포 가능
- [x] **1-5a.** `collectors/dart_disclosure_collector.py` *(2026-05-04 완료)*
  - [x] `DartDisclosureSnapshot` frozen dataclass (positive_matches/exclusion_matches tuple, raw_disclosures list)
  - [x] `DartDisclosureCollector` (의존성 주입 — `dart_fetch_fn` 또는 `modules/ai_verifier/dart_api.fetch_dart_disclosures` 자동 로드)
  - [x] **PRD 8-2 키워드 매트릭스**:
    - 즉시제외 17종: 유상증자/CB/BW/EB/전환사채/신주인수권부사채/교환사채/무상감자/횡령/배임/영업정지/제재/검찰/감리/공정위·금감원 조사/고발/과징금/실적쇼크/분식회계
    - 호재 9종: 단일판매공급계약/공급계약/수주/자기주식취득/자사주매입/주식양수도 (띄어쓰기 변형 포함)
  - [x] **제외 우선 정책**: 한 공시에 호재+제외 동시 발견 시 exclusion_matches 만 채움 (positive 비어있음) — 보수적
  - [x] **casefold() 매칭**: CB/BW/EB 영문 약어 대소문자 무관
  - [x] **빈 list vs None 구분**: 빈 list → is_valid=True (정상 무공시), None → is_valid=False (API 키 누락 가능)
  - [x] **`to_layer3_input()`**: 1-4 SignalScoreEngine layer3 dict 의 `has_positive_disclosure` 키 호환
  - [x] **에러 격리**: 잘못된 ticker / lookback_days(0/음수/bool/float/문자열) / API 예외 / dict 응답 → is_valid=False, collect_for_universe 다른 종목 진행
  - [x] **__init__ 검증**: 빈 키워드 집합 → ValueError
  - [x] 단위 테스트 26케이스 통과 (정상/호재 4종/제외 7종/제외 우선/다중 공시/빈 list/None/dict/잘못된 ticker 6/lookback 6/API 예외/다종목 격리/손상 필드/to_layer3/init 검증/frozen/hash/모듈 상수/_find_match/snapshot_time/**FP 방지 5종**)
  - [x] code-tester 검증 → **심각 0건 / 주의 2건** 모두 즉시 수정:
    - docstring "API 키 미설정 시 is_valid=False" 오기 정정
    - "조사" 단독 키워드 → "공정위 조사 / 금감원 조사 / 감리 착수" 등 구체 패턴으로 교체 (FP 5건 방지: 기업실태조사/시장조사/IR 조사 등)
  - [x] 배포 가능
- [x] **1-5b.** `engines/overnight_risk_filter.py` *(2026-05-04 완료)*
  - [x] `OvernightRiskAssessment` frozen dataclass (skip_today / position_size_factor / final_size_factor / market_warnings tuple)
  - [x] `OvernightRiskFilter` (PRD 4-2 매매 중지 + 4-3 비중 축소 + 1-5a DART 통합)
    - PRD 4-2: us_futures ≤ -0.015 / vkospi ≥ 30 → skip_today
    - PRD 4-3: us_futures (-0.010 ~ -0.015) / usd_krw ≥ 0.015 / kospi ≤ -0.02 → 비중 50%
    - 1-5a DART duck-typing (dataclass + dict 양방향) → exclude_by_dart 통합
  - [x] **의사결정 우선순위**: skip_today > exclude_by_dart > 비중축소 > 통과
    - skip 시 final_size_factor=0.0 (DART 정보는 보존, decision_reason 은 skip 우선)
    - position_size_factor (시장 단계 중간값) vs final_size_factor (최종) 명시 — 호출처는 final 사용 권장
  - [x] **__init__ 검증**: us_futures_skip < reduce 순서 / reduced_size_factor (0,1) / vkospi 양수 / usd_krw 양수 / kospi 음수 → 6케이스 ValueError
  - [x] **`from_settings(external_risk_settings: dict)`**: settings.yaml `external_risk:` 섹션 5키 호환
  - [x] **Phase 1 데이터 결손 정책**: market_data 키 None → 해당 룰 비활성 (skip 트리거 X), warning 누적
  - [x] **`_safe_float` 강화**: None / bool / NaN / inf / 문자열 모두 None
  - [x] **assess_for_universe**: 시장 1회 평가 + 종목별 DART 통합 매핑 반환 + 시장 skip 시 한 줄 로그
  - [x] 단위 테스트 24케이스 통과 (정상/skip 4종/reduce 4종/다중신호/DART 단독/호재/우선순위/결손/비정상/__init__ 6/from_settings/duck-typing/universe 2/frozen/helpers/assessed_at)
  - [x] code-tester 검증 → **심각 0건 / 주의 1건** 즉시 수정 (position_size_factor docstring 보강) → 배포 가능
- [x] **1-6.** `storage/candidate_logger.py` *(2026-05-04 완료)*
  - [x] `CandidateLogger` (의존성 주입 — `ClosingBetDatabase` + `CostSlippageEngine`)
  - [x] **라이프사이클 6 메서드**:
    - `log_recommended` (INSERT, candidate_id 반환) — ScoreBreakdown duck-typing (객체 + dict)
    - `log_features` (INSERT, PK=candidate_id, 18컬럼) — 부분 입력 시 누락 키 NULL
    - `mark_rejected_by_filter` / `mark_rejected_manual` (UPDATE)
    - `mark_entered` (UPDATE entry_price/amount/time)
    - `log_exit` (UPDATE + cost_engine.compute_pnl + to_db_payload 5컬럼) → ExitResult 반환
    - `log_labels` (INSERT OR REPLACE) — **부분 덮어쓰기 docstring 명시**
    - `log_flow_reliability` (INSERT OR REPLACE + 방향 일치 자동 계산)
  - [x] **타입 강제 변환**:
    - days_from_52w_high / theme_leadership_rank: float → int
    - kospi_above_200ma / label_*: bool → 0/1, 비-bool → None
  - [x] **에러 격리**:
    - 잘못된 ticker (6자리 검증) / candidate_id 양의 정수 / status whitelist / reason 비어있음 → ValueError
    - candidate_id 미존재 (rowcount=0) → LookupError
    - log_features 두 번 호출 → IntegrityError (PK 충돌, 의도된 호출 측 1회 보장)
  - [x] **1-9 검증 헬퍼**: `get_candidates_in_period` / `get_distinct_stocks_count` / `count_by_status` / `get_recent_recommended`
  - [x] **모듈 상수**: ALLOWED_STATUSES (frozenset) + LAYER1_KEYS / LAYER2_KEYS / LAYER3_KEYS / MARKET_REGIME_KEYS
  - [x] 단위 테스트 20케이스 통과 (recommended/features full+partial+int 강제/rejected_filter+manual/lifecycle+exit cost/exit 없는 entry/unknown cid/labels REPLACE/flow_reliability/조회 4종/잘못된 입력 14/helpers 17/recent/PK 충돌)
  - [x] code-tester 검증 → **심각 0건 / 주의 2건** 즉시 수정:
    - db.py `get_cursor` rollback 범위 확장 (`sqlite3.Error` → `Exception`) — LookupError 시도 rollback 보장
    - log_labels INSERT OR REPLACE 부분 덮어쓰기 docstring 명시
  - [x] 회귀 검증: 0-B fund_guard 10/10 PASS, 1-6 20/20 PASS
  - [x] 배포 가능
- [x] **1-7.** `notification/telegram_review_bot.py` *(2026-05-04 완료)*
  - [x] `TelegramReviewBot` (의존성 주입 — 0-B `infra/telegram_client.get_telegram_notifier()` 싱글톤)
  - [x] **Phase 1 정책 명시**: `_PHASE1_FOOTER` "Phase 1: 알림만 — 자동매수 비활성" + "수동 매수 검토 후 진행" 모든 메시지 첨부
  - [x] **3 발송 메서드**:
    - `send_test_message()` — 헬스체크용 (실제 신규 봇 발송 1건 확인)
    - `send_alert(ticker, name, score_breakdown, dart_snapshot, risk_assessment, rank?, total?)` — 단일 종목
    - `send_batch_alert(candidates: list[dict])` — 헤더 1건 + 종목별 N건 (15:15 트리거)
    - `send_daily_summary(trade_date, status_counts, recommended_count)` — 일일 요약 + 1-9 게이트 진척도 (15:35)
  - [x] **format_alert_message** — 1-4 ScoreBreakdown + 1-5a DartSnapshot + 1-5b RiskAssessment 통합 마크다운 본문 (점수/L1L2L3/atr_overheat/DART/외부리스크)
  - [x] **fail-safe**: `.env` 미설정 시 `is_enabled=False` → 모든 send_* 메서드 False 반환 + notifier 호출 0회 (시스템 동작 영향 X)
  - [x] **duck-typing**: ScoreBreakdown/DartDisclosureSnapshot/OvernightRiskAssessment 모두 dataclass + dict 양방향
  - [x] **Markdown v1 escape**: `_escape_markdown()` — backslash 우선 + `_*[`\\` 5종 처리 (종목명 / 사유 안전)
  - [x] **하드코딩 0**: `_HEADER` / `_SEPARATOR` / `_PHASE1_FOOTER` / `_GATE_OPERATIONAL_REVIEW=30` (settings.yaml 동기화 주석) 모듈 상수
  - [x] **신규 봇 .env 주입**: `CLOSING_BET_TELEGRAM_BOT_TOKEN`/`CHAT_ID` (chat_id=8509696011, 스윙과 동일 단일 채널)
  - [x] **실 sendMessage 1건 발송 확인**: `--live` 옵션으로 신규 봇 정상 작동 검증 (Telegram 채팅에서 "✅ 종가베팅 시스템 테스트" 수신)
  - [x] 단위 테스트 22케이스 통과 (mock 21 + P0 회귀 방지 1: atr_overheat_value 비-float 안전)
  - [x] code-tester 검증 → 심각 1건 / 주의 1건 / 정보 1건 모두 즉시 수정:
    - P0 atr_overheat_value str 입력 ValueError 크래시 → `_fmt_num()` 사용
    - P1 게이트 30건 하드코딩 → `_GATE_OPERATIONAL_REVIEW` 상수 추출
    - P2 테스트 픽스처 `dt.now()` → `now_kst()` 교체
  - [x] 배포 가능
- [x] **1-8.** `main_orchestrator.py` (익일 매도 09:30 이후) *(2026-05-04 완료)*
  - [x] `MainOrchestrator` (1-2~1-7 통합 오케스트레이터, 의존성 주입)
  - [x] **3 메인 메서드**:
    - `run_daily_pipeline` (15:10): Layer 1+2+DART 병렬 수집 → 종목별 점수+위험+DB+알림
    - `run_daily_summary` (15:35): DB 집계 + 누적 recommended + 텔레그램 발송
    - `run_label_yesterday` (T+1 10:00): label_provider 의존성, 어제 후보 사후 라벨링
  - [x] **register_jobs(scheduler)**: APScheduler 3 잡 등록 (15:10/15:35/10:00, mon-fri, KST, replace_existing=True)
  - [x] **Phase 1 정책 준수**: 자동매수 코드 경로 0건, mark_entered/log_exit 미호출
  - [x] **단위 격리**: per-ticker try/except, Layer 2 invalid → 스킵, EXCLUDED → mark_rejected_by_filter
  - [x] **휴장일 가드**: `is_trading_day()` 체크로 주말/공휴일 자동 스킵
  - [x] **비동기 안전성**:
    - 모든 collector/logger/notifier 호출 `asyncio.to_thread` 격리
    - `asyncio.gather(return_exceptions=True)`: 한 collector 실패 시 빈 폴백 → 전체 중단 방지 (P1 fix)
    - `market_data_provider` 도 `asyncio.to_thread` 로 격리 (P1 fix, Phase 2 HTTP 수집기 대비)
  - [x] **provider 예외 격리**: `_safe_call` / `_safe_name_lookup` 으로 callable 예외 방어
  - [x] **부수 fix**: 1-5b `decision_reason` DART 중복 prefix 제거 (P2 fix)
  - [x] standalone CLI (`python -m closing_bet_system.main_orchestrator --pipeline-now`)
  - [x] 통합 테스트 16케이스 통과 (lazy/normal alert/DART 제외/atr_overheat/시장 skip/Layer2 invalid/empty/예외 격리/daily_summary/label provider 2종/register_jobs/_extract_market_regime/_safe_call/_index_dart_snaps/스케줄 상수)
  - [x] code-tester 검증 → **심각 0건 / 주의 2건 / 정보 4건** 모두 즉시 fix:
    - P1 asyncio.gather return_exceptions
    - P1 market_data_provider to_thread
    - P2 docstring (log_labels Phase 1 포함 명시)
    - P2 1-5b decision_reason 중복 prefix
    - P2 테스트 fixture datetime.now → now_kst
    - P2 SUMMARY_SCHEDULE_MINUTE 검증 추가
  - [x] 회귀 검증: 1-8 16/16 + 1-5b 24/24 + 1-5a 26/26 모두 PASS
  - [x] 배포 가능
- [ ] **1-9.** 검증 (모의 → 실전 → 추천 후보 30건 누적)

### Phase 2. 반자동 + 게이트

- [ ] **2-1.** `collectors/kis_orderbook_collector.py`
- [ ] **2-2.** `collectors/kind_alert_collector.py`
- [ ] **2-3.** `storage/flow_reliability_tracker.py` → Layer 1 활성화 검토
- [ ] **2-4.** `execution/entry_executor.py` (fund_guard + 사용자 승인)
- [ ] **2-5.** `execution/morning_exit_manager.py` (시가 6단계)
- [ ] **2-6.** `dashboard/dashboard_fastapi.py`
- [ ] **2-7a.** Phase 2.5 백테스트: 데이터 추출
- [ ] **2-7b.** Phase 2.5 백테스트: 시뮬 엔진
- [ ] **2-7c.** Phase 2.5 백테스트: 리포트
- [ ] **2-8.** **🚦 100건 자동화 의사결정 게이트** (EV/평균 비/샤프 충족 검증)

### Phase 3. 부분 자동화

- [ ] **3-1.** `engines/ev_calculator.py`
- [ ] **3-2.** `engines/signal_score_engine.py` ML 전환
- [ ] **3-3.** Walk-forward 자동화
- [ ] **3-4.** `engines/regime_detector.py`
- [ ] **3-5.** `modules/msci_rebalancing_module.py`
- [ ] **3-6.** `modules/swing_integration.py`
- [ ] **3-7.** `execution/kill_switch.py`

---

## 검증

### 0-A 단위 검증
- [x] `python -c "import closing_bet_system"` 성공
- [x] `python -m closing_bet_system.storage.db --init` 성공
- [x] sqlite3 직접 쿼리로 4테이블 + schema_version 확인 (MCP는 스윙 DB 바인딩이라 직접 검증)
- [x] settings.yaml YAML 파싱 성공 (yaml.YAMLError 폴백 동작 검증 포함)
- [x] code-tester 에이전트 검증 통과 (심각 2건 수정 후 재검증)

### 0-B 단위 검증
- [x] 4 wrapper 모듈 import 성공
- [x] **fund_guard 단위 테스트 10건 통과**: 정상/1종목비중/자금한도/동시보유/추가매수허용/1일진입/스윙중복/총자산0/잘못된입력7건/DB실패
- [x] swing_db_reader read-only 강제 검증 (CREATE TABLE 차단)
- [x] telegram_client 비활성 폴백 (스윙 봇 토큰으로 폴백 차단)
- [x] telegram_client 활성 모드 (env 토큰 주입 시 _enabled=True)
- [x] 검사 순서 검증: 스윙 중복이 자금 한도보다 먼저 차단
- [x] 단일 connection (`_fetch_db_state`) 검증
- [x] 신규 봇 토큰으로 실제 테스트 메시지 발송 *(2026-05-04 .env 주입 완료, 1-7 telegram_review_bot 구현 시점에 sendMessage 검증)*
- [x] 기존 TelegramNotifier 호출부 영향 없음 (인자 미지정 시 기존 동작 유지)

### 0-C 단위 검증
- [x] py_compile 통과
- [x] 19종목 × 731일 = 13,889행 정상
- [x] ATR 과열 필터 작동 (267건 필터)
- [x] NaN 처리 (초기 윈도우/마지막 날) 정상
- [x] symbol leading zero 보존 (CSV zfill)
- [x] hit rate 비정상값 없음 (0%/100% 케이스 없음)
- [x] 거래정지 종목 후보 오염 차단 (volume>0 필터)
- [x] code-tester 검증 통과 (심각 1건 즉시 수정, 1건 0-D 반영)

### 0-D 단위 검증
- [x] Layer 2 sanity check 리포트 markdown 생성 (7,862자)
- [x] 비용 차감 후 EV 계산 포함 (왕복 비용 0.41%)
- [x] ATR 역선택 명시 (필터/통과 비교 표)
- [x] 의사결정 분기 표 (Phase 1 진입/보류/재설계)
- [x] code-tester 본문 일관성 검증 통과 (심각 2건 수정 후)
- [ ] 사용자 검토 회의 → **Phase 1 진입 결정**

### 1-1~1-8 단위 검증
- [x] 각 모듈 `python -m py_compile` 통과 (1-1/1-2/1-3/1-4/1-5a/1-5b/1-6/1-7/1-8 모두 통과)
- [x] code-tester 에이전트 검증 통과 (1-1/1-2/1-3/1-4/1-5a/1-5b/1-6/1-7/1-8 모두 통과)
- [ ] 단위 통합 테스트 (모의투자) — Phase 1 운영 시작 후 실데이터 검증

### 1-8 단위 검증
- [x] py_compile 통과 (orchestrator + overnight_risk_filter + test)
- [x] 통합 테스트 16/16 PASS
- [x] 회귀: 1-5b 24/24 + 1-5a 26/26 PASS (decision_reason 변경 영향 없음)
- [x] PRD 16-3 시간표 (15:10 / 15:35 / 10:00) 정확
- [x] Phase 1 정책 (자동매수 0건) 코드 검증
- [x] 단위 격리 (Layer 2 invalid / DART 제외 / atr_overheat / V-KOSPI skip / per-ticker 예외) 모두 검증
- [x] APScheduler 3 잡 등록 + day_of_week=mon-fri + Asia/Seoul timezone
- [x] code-tester 검증 → 심각 0건 / 주의 2건 / 정보 4건 모두 즉시 fix → 배포 가능

### 1-7 단위 검증
- [x] py_compile 통과
- [x] mock 단위 테스트 22/22 PASS (P0 회귀 방지 포함)
- [x] **실제 신규 봇 sendMessage 1건 발송 성공** (.env 주입 + Telegram 채팅 수신 확인)
- [x] Phase 1 정책 (자동매수 X, 알림만) 메시지 본문 검증
- [x] fail-safe (.env 미설정) 호출 0회 검증
- [x] duck-typing (ScoreBreakdown/Dart/Risk 모두 dataclass+dict)
- [x] Markdown v1 escape 5종 (종목명 underscore 안전)
- [x] code-tester 검증 → 심각 1건 / 주의 1건 / 정보 1건 모두 즉시 fix → 배포 가능

### 1-6 단위 검증
- [x] py_compile 통과 (db.py + candidate_logger.py)
- [x] 단위 테스트 20/20 PASS
- [x] 회귀: 0-B fund_guard 10/10 PASS (db.py rollback 범위 변경 영향 없음)
- [x] 라이프사이클 6 메서드 정확성 (recommended → entered → exit + cost 분해)
- [x] 1-9 검증 헬퍼 (distinct_stocks_count / count_by_status)
- [x] DB CHECK 제약 vs ALLOWED_STATUSES 일치
- [x] code-tester 검증 → 심각 0건 / 주의 2건 (즉시 fix) → 배포 가능

### 1-5b 단위 검증
- [x] py_compile 통과
- [x] 단위 테스트 24/24 PASS
- [x] PRD 4-2 매매 중지 (us_futures ≤ -0.015 / vkospi ≥ 30) + 경계 (=) 검증
- [x] PRD 4-3 비중 축소 (us_futures -1.0~-1.5 / usd_krw ≥ 1.5 / kospi ≤ -2) + 다중 신호 결합
- [x] DART 통합 (dataclass + dict duck-typing)
- [x] 의사결정 우선순위 (skip > exclude_by_dart > reduce > 통과)
- [x] Phase 1 데이터 결손 정책 (None → 룰 비활성)
- [x] settings.yaml `external_risk:` 섹션 from_settings 호환
- [x] code-tester 검증 → 심각 0건 / 주의 1건 (즉시 fix) → 배포 가능

### 1-5a 단위 검증
- [x] py_compile 통과
- [x] 단위 테스트 26/26 PASS (FP 방지 5케이스 포함)
- [x] PRD 8-2 키워드 매트릭스 정확 반영 (즉시제외 17종 + 호재 9종)
- [x] casefold 매칭 (CB/BW/EB 영문 대소문자 무관)
- [x] 제외 우선 정책 (호재+제외 동시 발견 → 제외만 기록)
- [x] FP 방지: "조사" 단독 키워드 제거 → 구체 패턴으로 교체 (기업실태조사/시장조사/IR 조사 미차단)
- [x] code-tester 검증 → 심각 0건 / 주의 2건 (즉시 fix) → 배포 가능

### 1-4 단위 검증
- [x] py_compile 통과
- [x] 단위 테스트 22/22 PASS
- [x] PRD 6-1 점수 11점 만점 + Phase 1 weighted=7 (L1=0) 검증
- [x] 하드 필터 (atr_overheat > 1.8 / None / NaN / inf / 문자열) 모두 EXCLUDED
- [x] decision 5-state 우선순위 정확
- [x] settings.yaml `score:` 섹션 from_settings 호환
- [x] code-tester 검증 → 심각 0건 / 주의 2건 (즉시 fix) → 배포 가능

### 1-3 단위 검증
- [x] py_compile 통과
- [x] 단위 테스트 19/19 PASS
- [x] PRD 5-Layer 2 4지표 수식 == 0-C 백테스트 `compute_layer2_features` 일치
- [x] 분모 0 / NaN / inf / bool / 빈 문자열 모두 방어
- [x] frozen + raw_payload(list) hash 안전 (set 사용 가능)
- [x] DB candidate_features Layer 2 6컬럼명과 1:1 매핑 (`to_layer2_features()`)
- [x] code-tester 검증 → 심각 0건 / 주의 2건 (docstring/주석 보강) → 배포 가능

### 1-9 검증
- [ ] 추천 후보 30건 누적
- [ ] 15영업일 이상 경과
- [ ] 서로 다른 종목 20개 이상
- [ ] CRISIS/DANGER 일에 진입 0건 (필터 작동)
- [ ] **30건 운영 점검 게이트 자동 리포트 생성**

### 2-7c/2-8 검증
- [ ] Phase 2.5 백테스트 점수 임계값별 EV 곡선
- [ ] 100건 누적 + 비용 차감 후 EV ≥ 0.5%
- [ ] 평균 익절 / 평균 손실 ≥ 1.3
- [ ] 월간 샤프 ≥ 1.0

---

## 배포

- [ ] systemd 서비스 추가 검토 (또는 기존 `trading_system.service`에 통합)
  - **권장**: 기존 서비스에 통합 (KIS 토큰 공유, 단일 systemd)
- [ ] `.env`에 `CLOSING_BET_TELEGRAM_BOT_TOKEN`, `CLOSING_BET_TELEGRAM_CHAT_ID` 추가
- [ ] `requirements.txt`에 신규 라이브러리 추가 시 갱신
- [ ] `.mcp.json` 종가베팅 DB MCP 추가 검토 (선택)
- [ ] 기존 프로세스 종료 후 재시작 (`sudo systemctl restart trading_system`)
- [ ] 시작 후 로그 확인 (`journalctl -u trading_system -n 50`)
- [ ] 신규 텔레그램 봇 알림 수신 확인

---

## 문서 업데이트 (필수, 빠뜨리지 말 것)

- [ ] `CLAUDE.md` (프로젝트 루트) — 종가베팅 시스템 운영 규칙 추가
- [ ] `memory/MEMORY.md` — 종가베팅 시스템 도입 메모 추가
- [ ] `docs/INDEX.md` — 종가베팅 관련 문서 인덱스 추가
- [ ] `docs/improvements/change_log.md` — 파라미터/임계값 변경 시 1줄 추가
- [ ] 작업 완료 시 `active/closing-bet-system/` → `completed/YYYYMMDD_closing-bet-system/` 이동
- [ ] 새 메모리 파일 작성 (예: `memory/project_closing_bet_system.md`)

---

## 단위별 진행 메모

### 0-A (2026-05-03 시작)
- 현재 진행 중
- 디렉토리/DB/설정 파일 동시 생성

### 0-B 예정
- TelegramNotifier 부모 클래스 수정 — **역호환 검증 필수**
- fund_guard.allow_order() 단위 테스트 4 케이스
