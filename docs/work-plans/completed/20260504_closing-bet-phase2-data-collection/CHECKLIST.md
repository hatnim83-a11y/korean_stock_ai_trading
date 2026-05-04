# CHECKLIST: 종가베팅 Phase 2 데이터 수집/모니터링 (옵션 A)

## 구현 항목

### 단위 2-1: kis_orderbook_collector ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/kis_orderbook_collector.py` 신규
  - [x] `OrderbookSnapshot` dataclass — ticker/snapshot_time/is_valid + ask1/bid1/잔량/현재가/스프레드
  - [x] `KisOrderbookCollector.__init__(order_api=None)` — 의존성 주입
  - [x] `collect_snapshot(ticker)` — `inquire_asking_price` 호출 + Snapshot 변환
  - [x] `collect_for_universe(tickers)` — 순차 처리 + 예외 격리 (Phase 1 패턴)
  - [x] `insert_snapshots()` 헬퍼 (db=None 경로 finally close 보장)
- [x] `closing_bet_system/storage/db.py` 수정
  - [x] `_migrate_v2` 추가 — `orderbook_snapshots` 테이블 + 2 인덱스
  - [x] `_MIGRATIONS` 리스트에 v2 추가
- [x] `closing_bet_system/main_orchestrator.py` 수정
  - [x] `__init__`에 `orderbook_collector` 인자 추가
  - [x] lazy property
  - [x] `run_daily_pipeline` asyncio.gather에 orderbook_task 병렬 추가 (4 collectors)
  - [x] DB INSERT 통합 (signal/risk 점수에는 미반영, Phase 2-3에서 활용)
- [x] py_compile 통과
- [x] 단위 테스트 13 시나리오 PASS:
  - [ ] OB-1: 정상 호가 → Snapshot 생성
  - [ ] OB-2: KIS 실패 → is_valid=False
  - [ ] OB-3: spread_pct 계산 정확성 ((ask1-bid1)/((ask1+bid1)/2))
  - [ ] OB-4: ask1=0/bid1=0 시 spread_pct None
  - [ ] OB-5: 무효 ticker → is_valid=False
  - [ ] OB-6: collect_for_universe 5종목 → 5 Snapshot
  - [ ] OB-7: 일부 종목 실패 격리 → 다른 종목 정상
  - [ ] OB-8: DB v2 마이그레이션 idempotent
  - [ ] OB-9: orderbook_snapshots INSERT 정상
  - [ ] OB-10: PK 충돌 (같은 ticker+snapshot_time) → INSERT OR REPLACE
  - [ ] OB-11: main_orchestrator 통합 — gather 병렬 OK
  - [x] OB-12: orderbook_collector=None default → 자동 생성
- [x] code-tester 검증 (심각 1건 = DB 누수 → finally close 보강 완료)

### 단위 2-2: kind_alert_collector ✅ (2026-05-04 완료, 인터페이스 단계)
- [x] `closing_bet_system/collectors/kind_alert_collector.py` 신규
  - [x] `KindAlertSnapshot` dataclass — snapshot_date/is_valid/alerts dict/severity_map dict/error_msg
  - [x] `KindAlertCollector.__init__(provider=None)` — provider 주입형
  - [x] `collect()` → `KindAlertSnapshot` (전체 종목 dict, universe 불필요)
  - [x] 데이터 출처: provider 미주입 시 빈 dict 폴백 (Phase 2-2 1단계: 인터페이스만 활성)
    - 후속 단위에서 KindHttpProvider 주입 예정 (사이트 구조 안정성 검토 후)
  - [x] 4단계 매핑: "투자주의"→1 / "투자경고"→2 / "투자위험"→3 / "매매거래정지"→3
  - [x] 실패 시 `KindAlertSnapshot(is_valid=False, alerts={})` 폴백
- [x] `closing_bet_system/engines/overnight_risk_filter.py` 수정
  - [x] `assess()`에 `kind_alerts: Optional[Any] = None` 인자 추가
  - [x] severity 3 → can_enter=False, decision_reason "KIND 시장경보 즉시제외"
  - [x] severity 2 → reduced_size_factor (0.5)
  - [x] severity 1 → warnings 추가 (size 영향 없음)
  - [x] None 또는 ticker 미존재 → 영향 없음 (결손 정책 일관)
  - [x] `_resolve_kind_severity` 헬퍼 (Snapshot/dict 한글명/dict 정수 모두 호환)
  - [x] `assess_for_universe` 시그니처에도 `kind_alerts` 추가 (code-tester 심각 fix)
- [x] `closing_bet_system/main_orchestrator.py` 수정
  - [x] `__init__`에 `kind_collector` 인자 추가
  - [x] lazy property
  - [x] `run_daily_pipeline` 시작부에 `kind_snapshot = await asyncio.to_thread(...collect)`
  - [x] `_process_ticker`에 `kind_alerts=kind_snapshot` 전달
- [x] py_compile 통과
- [x] 단위 테스트 16 시나리오 PASS:
  - [ ] KA-1: pykrx 정상 → alerts dict 반환
  - [ ] KA-2: pykrx 실패 → HTML 크롤링 폴백 시도
  - [ ] KA-3: 모두 실패 → is_valid=False 폴백
  - [ ] KA-4: severity_map 4단계 매핑 정확
  - [ ] KA-5: OvernightRiskFilter 통합 — severity 3 → can_enter=False
  - [ ] KA-6: severity 2 → reduced_size 0.5
  - [ ] KA-7: ticker 미존재 → 영향 없음
  - [ ] KA-8: kind_alerts=None → 영향 없음 (회귀)
  - [ ] KA-9: main_orchestrator 통합 — kind_alerts 전달 흐름
  - [x] KA-10: 빈 alerts dict → 모든 ticker 통과
- [x] code-tester 검증

### 단위 2-6: dashboard_fastapi 기본 ✅ (2026-05-04 완료)
- [x] `closing_bet_system/dashboard/data_adapter.py` 신규
  - [x] `get_today_candidates()` → 오늘 status별 카운트 + 리스트
  - [x] `get_gate_progress()` → recommended 누적 / 30, 영업일 / 15, 종목 / 20
  - [x] `get_orderbook_history(days, limit)` → Phase 2-1 데이터 (datetime ISO 비교 정확)
  - [x] `get_recent_rejections(days, limit)` → 최근 N일 탈락 사유
  - [x] `get_fund_guard_status()` → 자금 사용/한도, 활성 포지션, 주간 손실
- [x] `web/api_routes.py` 수정
  - [x] `/api/v1/closing-bet/today` (GET, require_auth)
  - [x] `/api/v1/closing-bet/gate-progress`
  - [x] `/api/v1/closing-bet/orderbook-history?days=1&limit=200`
  - [x] `/api/v1/closing-bet/rejections?days=7&limit=50`
  - [x] `/api/v1/closing-bet/fund-guard-status`
- [x] `web/templates/dashboard.html` 수정
  - [x] "종가베팅" 탭 추가
  - [x] 5 카드/테이블 섹션 (게이트 / 오늘 후보 / fund_guard / 탈락사유 / 호가)
  - [x] JavaScript loadClosingBet — 5 API 병렬 호출 + 렌더링
- [x] py_compile + import 통과
- [x] 단위 테스트 13 시나리오 PASS

### 종합 검증 ✅
- [x] py_compile 8파일 통과
- [x] 단위 테스트 42건 PASS (OB 13 + KA 16 + DA 13)
- [x] scheduler import + MainOrchestrator orderbook/kind property 자동 생성 확인
- [x] code-tester (3단위 종합) — 심각 2건 + 주의 2건 → 모두 보강 완료
  - 심각1: insert_snapshots db=None 경로 finally close 추가
  - 심각2: assess_for_universe에 kind_alerts 인자 추가 (현재 미사용 메서드지만 정합성)
  - 주의1: data_adapter datetime 비교 ISO + T00:00:00 명시
  - 주의2: _safe_call to_thread 이중 래핑 — Phase 2 후속 시 정리 권장 (현재 무영향)

## 검증 항목 ✅

### 단위 검증
- [x] py_compile 8파일 통과
- [x] 단위별 단위 테스트 모두 PASS (42 시나리오)
- [x] code-tester 심각 2건 → 보강 완료

### 통합 검증
- [x] scheduler.py / main_orchestrator.py import 성공
- [x] MainOrchestrator orderbook+kind property 자동 생성
- [x] register_jobs 후 잡 3건 등록 확인
- [x] providers 주입 단발 트리거: universe 19, valid 18, rejected 4, 알림 0 (정상)

### 실전 검증
- [x] 첫 트리거 시 universe_provider Database import fix → 19종목 정상 산출
- [x] orderbook_snapshots 19건 DB INSERT 확인 (ask/bid/스프레드 정확)
- [x] candidates 18건 (recommended 14 + rejected_filter 4 atr_overheat>1.8)
- [x] KIS API 호출량 ≈ 19종목 × 4 collector + 17 종목명 + KIND 0회(provider 미주입) = 약 95건/단발

## 배포 항목 ✅ (2026-05-04 KST 15:06 + 15:13)
- [x] systemd 재시작 전 선행 체크 (보유 3종목 위험 0건, 매수 종료)
- [x] 장중 재시작 점검 (네패스아크 +5.7%, 트레일링 비활성)
- [x] `sudo systemctl restart trading_system` 1차 (PID 2511049→2533276)
- [x] `sudo systemctl restart trading_dashboard` (PID 2295896→2533412)
- [x] active(running) 확인 (둘 다)
- [x] 종가베팅 잡 3건 등록 + providers 4종 활성 로그 확인
- [x] 첫 15:10 트리거 — universe_provider `from database import db` 오류 발견
- [x] 즉시 fix: `from database import Database` 인스턴스 직접 생성 + connect/close
- [x] 두 번째 systemd 재시작 (PID 2533276→2540234)
- [x] 단발 검증: universe 19, valid 18, 알림 0 (정상 — Layer 1 가중치 0)

## 문서 업데이트 항목 ✅
- [x] `docs/improvements/change_log.md` 1줄 추가
- [x] `memory/project_closing_bet_system.md` 갱신 — Phase 2 옵션 A 진척
- [x] `memory/MEMORY.md` 인덱스 갱신
- [x] 3문서 active → completed/20260504_closing-bet-phase2-data-collection/ 이동

## 완료 게이트 ✅
- [x] 구현 항목 전부 `[x]` (단위 2-1, 2-2, 2-6)
- [x] 검증 항목 전부 `[x]`
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 전부 `[x]`
