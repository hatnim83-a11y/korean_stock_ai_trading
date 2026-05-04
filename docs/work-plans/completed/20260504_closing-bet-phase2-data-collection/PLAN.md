# PLAN: 종가베팅 Phase 2 — 데이터 수집/모니터링 (옵션 A)

## 목표
Phase 2 마스터 플랜의 10단위 중 **자동매매 위험 0인 3단위만 우선 처리**:
- 2-1 `kis_orderbook_collector` — 호가 1~10단계 스냅샷 수집
- 2-2 `kind_alert_collector` — KIND 시장경보 수집 (외부 리스크 보강)
- 2-6 `dashboard_fastapi` 기본 — 종가베팅 모니터링 페이지 (기존 web/ 통합)

## 배경

### 왜 이 3단위만?
1. PRD 17은 자동매수를 30건 운영 점검 게이트(operational_review=30) 통과 후 허용 → 2-4/2-5는 미리 만들어도 활성화 못함
2. 2-3 flow_reliability는 2-1 호가/체결 데이터 며칠 누적되어야 평가 가능 → 자연스러운 선후
3. 2-7 백테스트(Phase 2.5)는 자체 후보 DB 30건+ 누적 후 의미
4. 2-8 100건 게이트는 추후

### Phase 1 첫 데이터 수집은 오늘 15:10 KST 시작 (배포 완료, 2026-05-04 14:40)

## 단위별 핵심 설계

### 단위 2-1: kis_orderbook_collector

**책임**:
- 종목별 호가 1~10단계 스냅샷 수집 (`KISApi inquire_asking_price` 재사용)
- 본 단위는 **데이터 누적이 목표** — 동시호가 1초 폴링은 entry_executor (2-4) 도입 시점에 추가
- Phase 1 데이터 수집 시점 (15:00/15:10/15:20/15:28) 4회 스냅샷 캡처

**위치**: `closing_bet_system/collectors/kis_orderbook_collector.py`

**스냅샷 구조** (`OrderbookSnapshot` dataclass):
- `ticker`, `snapshot_time` (KST timezone-aware), `is_valid`
- 매도 1~10호가 + 잔량 (`ask1`~`ask10`, `ask_volume1`~`ask_volume10`)
- 매수 1~10호가 + 잔량 (`bid1`~`bid10`, `bid_volume1`~`bid_volume10`)
- 현재가, 호가 스프레드 (ask1-bid1)/((ask1+bid1)/2)
- raw_payload (디버깅용)

**KIS `inquire_asking_price` 응답**:
- 현재 dict는 1단계만 반환. 2-10단계도 응답에 있지만 추출 안 됨 → 필요 시 단순 확장
- **결정**: 본 단위에서는 1단계만 우선 수집. 2~10단계 확장은 entry_executor (2-4) 시점에 KIS 호출 1회로 데이터 풍부화 (별도 KIS 호출 추가 X)

**Phase 1 단순화**: 1~10단계 스키마는 미리 준비하되 첫 단위에서는 1단계만 채움. 다단계는 후속 단위에서 추가.

**DB**: `closing_bet.db` v2 마이그레이션 — `orderbook_snapshots` 테이블 신규
- PK: `(ticker, snapshot_time)` 복합
- 컬럼: ticker, snapshot_time, is_valid, ask1, ask_volume1, bid1, bid_volume1, current_price, spread_pct, error_msg
- 인덱스: (ticker, snapshot_time DESC), (snapshot_time)

**main_orchestrator 통합**:
- `__init__`에 `orderbook_collector` 인자 추가
- `run_daily_pipeline`의 데이터 수집 단계에 orderbook_task 추가 (asyncio.gather 병렬)
- `_process_ticker`에 orderbook 스냅샷 처리 (Phase 1은 score 계산엔 미반영, DB 저장만)

**검증**: 12+ 단위 시나리오 (정상/실패/캐시/병렬/DB 저장 검증)

---

### 단위 2-2: kind_alert_collector

**책임**:
- KIND (https://kind.krx.co.kr) 시장경보 종목 일일 수집
- 4단계: 투자주의 / 투자경고 / 투자위험 / 매매거래정지
- 일 1회 (15:10 외부 리스크 시점) 수집 → 종목별 최고 경보 단계 매핑
- `OvernightRiskFilter`에 KIND 입력 추가 → 외부 리스크 점수 보강 (PRD 8-3)

**위치**: `closing_bet_system/collectors/kind_alert_collector.py`

**데이터 출처**:
- KIND HTML 크롤링 또는 공식 데이터 페이지
- 유사 인프라: `modules/theme_analyzer/crawlers.py` 패턴 (httpx + BeautifulSoup)
- 폴백: 실패 시 빈 dict 반환 (외부 리스크 룰 비활성, Phase 1 결손 정책 동일)

**스냅샷 구조** (`KindAlertSnapshot` dataclass):
- `snapshot_date`, `is_valid`
- `alerts: dict[str, str]` — ticker → alert_level ("주의"/"경고"/"위험"/"정지")
- `severity_map: dict[str, int]` — ticker → 0~3 (0=정상, 3=정지)
- `error_msg`

**OvernightRiskFilter 통합**:
- `assess()`에 `kind_alerts` 인자 추가 (선택)
- 종목 ticker가 "위험" 이상 → can_enter=False (즉시 제외)
- "경고" → reduced_size_factor (0.5)
- "주의" → 가벼운 감점 (점수 -1)

**main_orchestrator 통합**:
- `__init__`에 `kind_collector` 인자 추가
- `run_daily_pipeline` 시작부에 KIND 1회 수집 (universe 무관, 전체 종목 dict)
- `_process_ticker`에 kind_alerts 인덱싱하여 risk_filter에 전달

**DB**: 별도 테이블 불필요. `candidate_features.market_regime` 또는 `candidates.rejection_reason`에 기록

**검증**: 10+ 단위 시나리오 (정상 dict / 빈 응답 / 크롤 실패 / OvernightRiskFilter 통합)

---

### 단위 2-6: dashboard_fastapi 기본

**책임**:
- 종가베팅 후보 추적 + 운영 점검 게이트 진척도 표시
- 기존 `web/` (FastAPI + Jinja2 + Chart.js + 인증) **재사용**
- 마스터 플랜은 별도 모듈 명시했으나, 인증/SSE 이중 운영 방지를 위해 기존 dashboard에 통합

**위치 결정**:
- 데이터 어댑터: `closing_bet_system/dashboard/data_adapter.py` (closing_bet.db 조회 헬퍼)
- 라우트 추가: `web/api_routes.py` 또는 신규 `web/closing_bet_routes.py`
- 페이지: `web/templates/closing_bet.html` 또는 기존 dashboard.html에 탭 추가

**핵심 화면 (Phase 2 기본)**:
1. **오늘 후보** (recommended/entered/rejected_filter 상태별 카운트 + 리스트)
2. **운영 점검 게이트 진척도** (recommended 누적 / 30건, 영업일 / 15일, 종목 다양성 / 20개)
3. **최근 7일 KIND 경보 히스토리** (단위 2-2 데이터 활용)
4. **최근 7일 외부 리스크 평가** (skip_today / reduced 발생 빈도)
5. **fund_guard 상태** (현재 자금 사용액 / 한도, 활성 포지션 수, 주간 손실)

**API 엔드포인트**:
- `GET /api/v1/closing-bet/today` — 오늘 후보 상태 dict
- `GET /api/v1/closing-bet/gate-progress` — 운영 점검 게이트 진척도
- `GET /api/v1/closing-bet/kind-history?days=7`
- `GET /api/v1/closing-bet/risk-history?days=7`
- `GET /api/v1/closing-bet/fund-guard-status`

**페이지 변경 결정 매트릭스**:
- (a) 기존 dashboard.html에 "종가베팅" 탭 추가 → 통합 UX
- (b) 신규 `closing_bet.html` 별도 페이지 → 분리 UX
- **결정**: (a) 탭 추가 — 사용자 시야 일원화, SSE/인증 무료 재사용

**스코프 제한 (Phase 2 기본)**:
- 실시간 SSE는 추후 (Phase 2 후속)
- 차트는 단순 카운터 + 테이블만 (Chart.js 라인은 추후)

**검증**: API 엔드포인트별 단위 테스트 + 페이지 렌더링 smoke test

---

## 단위 진행 순서

1. **2-1** orderbook_collector → 사용자 확인
2. **2-2** kind_alert_collector → 사용자 확인
3. **2-6** dashboard_fastapi 기본 → 사용자 확인

각 단위마다:
- 코드 작성 + py_compile
- 단위 테스트 작성 + 실행 (10+ 시나리오)
- code-tester 에이전트 검증
- scheduler.py / main_orchestrator.py 통합 (필요 시)
- CHECKLIST 갱신

## 변경 파일 목록

| 파일 | 변경 규모 | 단위 |
|---|---|---|
| `closing_bet_system/collectors/kis_orderbook_collector.py` | 중 (신규) | 2-1 |
| `closing_bet_system/storage/db.py` | 소 (`_migrate_v2` 추가) | 2-1 |
| `closing_bet_system/collectors/kind_alert_collector.py` | 중 (신규) | 2-2 |
| `closing_bet_system/engines/overnight_risk_filter.py` | 소 (`kind_alerts` 인자 추가) | 2-2 |
| `closing_bet_system/main_orchestrator.py` | 소 (2 collector 통합) | 2-1+2-2 |
| `closing_bet_system/dashboard/data_adapter.py` | 중 (신규) | 2-6 |
| `web/api_routes.py` | 소 (5 엔드포인트 추가) | 2-6 |
| `web/templates/dashboard.html` | 소 (탭 1개 추가) | 2-6 |
| `scheduler.py` | 소 (orchestrator 인자 추가) | 2-1+2-2 |
| `docs/improvements/change_log.md` | 1줄 | 종합 |

## 접근 방식
- **단위별 사용자 확인**: 옵션 A 승인했지만 단위 사이에 사용자 OK 받음 (CLAUDE.md 원칙)
- **자동 매매 위험 0**: 모든 단위가 데이터 수집/표시. 자동매수 코드 없음
- **Phase 1 결손 정책 일관성**: KIND 실패 시 None/빈 dict, 룰 비활성

## 롤백 계획
- 단위별 독립 커밋 → `git revert <hash>`
- scheduler.py 한 줄 롤백 시 collector 비활성화
- DB 변경: `orderbook_snapshots` 테이블 추가만 — 기존 데이터 영향 없음
- 대시보드 탭 제거: dashboard.html 한 섹션 제거

## 완료 기준 (3단위 종합)

| 지표 | 목표 |
|---|---|
| `orderbook_snapshots` 테이블에 일일 N건 기록 (universe × 4 스냅샷) | universe 5 → 20행/일 |
| KIND 경보 dict 일일 갱신 | 평균 100~200 종목 |
| KIND가 OvernightRiskFilter에 입력되어 동작 | rejected_by_kind 카운트 산출 가능 |
| 대시보드에서 종가베팅 탭 정상 렌더링 | 5 API 엔드포인트 모두 200 응답 |
| code-tester 검증 | 3단위 모두 심각 0건 |
| 단위 테스트 | 30+ 시나리오 PASS |

## 후속 작업 (Phase 2 잔여, 30/100건 게이트 후)

- 2-3 flow_reliability_tracker (호가 데이터 며칠 누적 후)
- 2-4 entry_executor (30건 운영 점검 통과 후)
- 2-5 morning_exit_manager (30건 운영 점검 통과 후)
- 2-7a/b/c Phase 2.5 백테스트 (30건+ 누적 후)
- 2-8 100건 자동화 게이트
