# CHECKLIST: 종가베팅 Phase 1 이월 항목

## 구현 항목

### 단위 A: name_lookup + telegram_client 결합 약화 ✅ (2026-05-04 완료)
- [x] `closing_bet_system/infra/name_lookup.py` 신규 — KIS get_stock_name 래퍼 + thread-safe 캐시
- [x] `closing_bet_system/infra/telegram_client.py` 수정 — NoOp 더미 패턴 (`__getattr__` silent False), 부모 내부 속성 직접 변경 코드 제거
- [x] `scheduler.py` 수정 — `name_lookup=_cb_get_name`
- [x] py_compile 통과 (3파일)
- [x] 단위 테스트 12건 PASS (NL-1~5, TG-1~7)
- [x] code-tester 검증: 심각 0 / 주의 2 (P2)

### 단위 B: universe_provider ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/universe_provider.py` 신규
  - [x] `get_universe()` — 6자리 종목코드 list 반환
  - [x] `database.get_top_themes()` 호출 + url 추출
  - [x] `crawl_naver_theme_stocks()` per theme (max_stocks 제한)
  - [x] 6자리 정규식 검증 + 중복 제거 + hard_cap=20
  - [x] 같은 거래일 in-memory 캐시 (더블체크 lock 패턴)
  - [x] 스윙 보유 종목 제외 (옵션)
  - [x] 빈 리스트 폴백 (DB 조회 실패 시)
- [x] `scheduler.py` 수정 — `universe_provider=_cb_get_universe`
- [x] py_compile 통과
- [x] 단위 테스트 10건 PASS (UV-1~10)

### 단위 C: market_data_provider ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/market_data_provider.py` 신규
  - [x] `get_market_data()` — 6키 dict 반환 (4 시장 + 2 placeholder)
  - [x] KOSPI: `KISApi.get_index_price("0001")` → kospi_change_pct (% → 소수)
  - [x] V-KOSPI: yfinance "^VKOSPI" → 실패 시 None
  - [x] 미선물: yfinance "ES=F" → 실패 시 None
  - [x] USD-KRW: yfinance "KRW=X" → 실패 시 None
  - [x] kospi_above_200ma / foreign_5d_cumulative: Phase 2 placeholder (None)
  - [x] 모든 호출 try/except 격리 (`_safe_call`)
  - [x] in-memory 캐시 (같은 거래일)
- [x] `scheduler.py` 수정 — `market_data_provider=_cb_get_market_data`
- [x] py_compile 통과
- [x] 단위 테스트 11건 PASS (MD-1~11, OvernightRiskFilter 통합 포함)

### 단위 D: label_provider ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/label_provider.py` 신규
  - [x] `get_label(ticker)` — 7키 dict 반환 (3 pct + 4 라벨)
  - [x] `KISApi.get_daily_price(ticker, count=5)` → 오늘/어제 OHLC
  - [x] KIS 정렬 방향 sanity check (rows[0].date >= rows[1].date)
  - [x] 라벨 4개 계산 (PRD 9-2):
    - [x] `label_gap_up`: open >= 어제종가 × 1.005
    - [x] `label_morning_exit`: high >= cost_engine.minimum_target_return
    - [x] `label_stop_risk`: low <= 어제종가 × 0.985
    - [x] `label_net_ev_positive`: morning_exit 와 동일 (Phase 1 단순화 — 명시 주석)
  - [x] KIS 실패 / 데이터 부족 / 어제 종가 0 시 None 반환
- [x] `closing_bet_system/main_orchestrator.py` 수정
  - [x] `__init__`에 `label_provider` 인자 추가
  - [x] `run_label_yesterday()` 인스턴스 폴백 (`self._label_provider`)
- [x] `scheduler.py` 수정 — `label_provider=_cb_get_label`
- [x] py_compile 통과 (3파일)
- [x] 단위 테스트 11건 PASS (LP-1~10b, orchestrator 통합 포함)

### 단위 E: weekly_loss_limit ✅ (2026-05-04 완료)
- [x] `closing_bet_system/infra/fund_guard.py` 수정
  - [x] `GuardConfig.weekly_loss_limit` 필드 추가 (default -0.05)
  - [x] `from_settings()` weekly_loss_limit 로드
  - [x] `_fetch_db_state()` 단일 connection 4쿼리 (TOCTOU 유지) — weekly_pnl 추가
  - [x] `allow_order()` 8번째 검사 — `weekly_pnl <= weekly_loss_limit` 시 차단
  - [x] settings.yaml `fund.weekly_loss_limit` 활용 (이미 -0.05 정의됨)
  - [x] Phase 3 다일 보유 시 trade_date 기준 한계 명시 주석
- [x] py_compile 통과
- [x] 단위 테스트 11건 PASS (WL-1~11)

### 종합 검증 ✅
- [x] py_compile 8파일 통과
- [x] 5단위 단위 테스트 55건 PASS
- [x] scheduler.py 통합 후 import 성공 + MainOrchestrator 4 providers callable 확인
- [x] code-tester (단위 A) — 심각 0 / 주의 2 (P2)
- [x] code-tester (단위 B-E 종합) — 심각 0 / 주의 4 (보강 완료)
  - false alarm: KIS 정렬 방향 → 명시 주석 + sanity check 보강
  - P2 4건 (캐시 race / 로그 메시지 / 라벨 동일성 / weekly_pnl trade_date 기준) → 주석/로그 개선

## 검증 항목

### 단위 검증
- [x] py_compile 8파일 통과
- [x] 단위별 단위 테스트 모두 PASS (55건)
- [x] code-tester 심각 0건

### 통합 검증
- [x] scheduler.py import 성공
- [x] MainOrchestrator 생성 성공 (4 providers 모두 callable)
- [x] register_jobs 후 잡 3건 등록 확인 (기존 검증)
- [x] 단위 테스트로 갈음 — 실 universe 산출은 운영 시간(15:10)에서 검증 예정

### 실전 검증 (배포 후 1일)
- [ ] 15:10 잡 트리거 시 universe 수집 + 알림 발송 (테마/종목에 따라 0건도 가능, 에러 0건이 핵심)
- [ ] 15:35 잡 트리거 시 일일 요약 텔레그램 도착 (텔레그램 활성화 시)
- [ ] 다음 날 10:00 잡 트리거 시 어제 후보 라벨링 시도 (entered 0건이면 무동작)
- [ ] KIS API 호출량 모니터링 (일 < 50건 추가 예상)

## 배포 항목 ✅ (2026-05-04 KST 14:40 완료)
- [x] systemd 재시작 전 선행 체크 (단일 PID 2336951, 매수 종료 후, 보유 3종목 위험 0건)
- [x] 장중 재시작 점검: -5%↓ 0건, +8%↑ 0건, 트레일링 활성 1건(네패스아크 L1) 정상 복원
- [x] `sudo systemctl restart trading_system` (PID 2336951 → 2511049, 다운타임 ~6초)
- [x] `sudo systemctl status trading_system` active(running) 확인
- [x] 종가베팅 잡 3건 등록 로그 확인 ("종가베팅 잡 3건 등록 — pipeline 15:10 / summary 15:35 / label 10:00", "providers 4종 활성")
- [x] 텔레그램 활성화 확인 ("텔레그램 알림 초기화 완료", CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID 등록 완료)
- [x] 포지션 3건 정상 복원 (삼성전자/에이디테크놀로지/네패스아크), 트레일링 복원 1건 (네패스아크 L1 38,016원)
- [x] 재시작 후 1분간 에러/예외 0건
- [ ] 첫 15:10 잡 트리거 시점 로그 실시간 관찰 (~30분 후 자연 트리거 예정)

## 문서 업데이트 항목 ✅
- [x] `docs/improvements/change_log.md` 1줄 추가 (2026-05-04 항목, before/after 추적)
- [x] `memory/project_closing_bet_system.md` 갱신 — 이월 항목 5단위 처리 완료
- [x] `memory/MEMORY.md` 인덱스 갱신
- [x] 3문서 active → completed/20260504_closing-bet-phase1-carryover/ 이동 (2026-05-04 배포 직후)
- [x] CHECKLIST의 모든 항목 `[x]` 확인 (실시간 관찰만 자연 진행 대기)

## 완료 게이트 ✅
- [x] 구현 항목 전부 `[x]` (단위 A~E)
- [x] 검증 항목 전부 `[x]` (단위/통합)
- [x] 배포 항목 전부 `[x]` (실시간 관찰은 자연 진행)
- [x] 문서 업데이트 항목 전부 `[x]`
