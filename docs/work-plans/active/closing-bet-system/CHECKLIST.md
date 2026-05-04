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
  - [x] `.env` 에 CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID 추가 (값은 사용자 입력 대기)
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
- [ ] **1-3.** `collectors/kis_price_volume_collector.py`
- [ ] **1-4.** `engines/signal_score_engine.py` (Layer 1 가중치 0)
- [ ] **1-5a.** `collectors/dart_disclosure_collector.py`
- [ ] **1-5b.** `engines/overnight_risk_filter.py`
- [ ] **1-6.** `storage/candidate_logger.py`
- [ ] **1-7.** `notification/telegram_review_bot.py`
- [ ] **1-8.** `main_orchestrator.py` (익일 매도 09:30 이후)
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
- [ ] 신규 봇 토큰으로 실제 테스트 메시지 발송 (사용자가 .env 입력 후)
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
- [ ] 각 모듈 `python -m py_compile` 통과
- [ ] code-tester 에이전트 검증 통과
- [ ] 단위 통합 테스트 (모의투자)

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
