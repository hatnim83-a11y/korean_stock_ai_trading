# CHECKLIST: 매수 필터 체인 개선 (Phase A)

## 구현 항목

### Phase A-0: ~~블로커 해제~~ (2026-04-24 해제)
- [x] ~~KIS 일봉 지수 조회 TR ID 실기 검증~~ → **Phase A-2에 통합**
  - 이유: TR_ID가 틀려도 NORMAL 폴백이 안전하게 작동 → 전체 설계 영향 0. 구현 중 1회 실기 호출로 검증하는 게 효율적

### Phase A-1: config.py + database.py (스키마 선작업) ✅ 완료 (2026-04-24)
- [x] `config.py` 라인 392 직후에 8개 Field 추가
  - [x] `RSI_DYNAMIC_ENABLED: bool = True`
  - [x] `RSI_BULL_THRESHOLD: float = 1.0`
  - [x] `RSI_BEAR_THRESHOLD: float = -1.0`
  - [x] `RSI_UPPER_BULL: float = 75.0`
  - [x] `RSI_UPPER_NORMAL: float = 70.0`
  - [x] `RSI_UPPER_BEAR: float = 65.0`
  - [x] `THEME_MIN_SLOT_ENABLED: bool = True`
  - [x] `THEME_SAFETY_FLOOR: float = 25.0`
- [x] `database.py` `_migrate()` v14 블록 추가 (v13 이미 사용 중이라 v14로 확정)
  - [x] `ALTER TABLE screening_log ADD COLUMN rsi_at_screen REAL DEFAULT NULL`
  - [x] `ALTER TABLE screening_log ADD COLUMN theme_slot_protected INTEGER DEFAULT 0`
  - [x] 재실행 시 duplicate column 예외 ignore 처리
- [x] `save_screening_log()` 시그니처 확장 (두 키 선택 파라미터)
- [x] `py_compile` config.py + database.py 통과

### Phase A-2: kis_api.py (전일 지수 등락률) ✅ 완료 (2026-04-24)
- [x] `get_prev_index_change_rate(index_code: str = "0001") -> Optional[float]` 구현
  - [x] TR_ID `FHKUP03500100` 확정
  - [x] 일봉 히스토리 파라미터 (최근 10일 범위, `FID_PERIOD_DIV_CODE=D`)
  - [x] 응답 `output2` 배열에서 당일 배제 후 상위 2개 영업일 종가로 계산
  - [x] `(T-1 - T-2) / T-2 × 100` 반환 (round 2자리)
  - [x] `_safe_float` 사용
  - [x] `_rate_limit()` 적용
  - [x] 실패 시 `None` + `logger.warning`
- [x] **실기 호출 검증 완료 (2026-04-24)**
  - [x] KOSPI("0001"): +0.90% (4/22 6417.93 → 4/23 6475.81)
  - [x] KOSDAQ("1001"): -0.58% (4/22 1181.12 → 4/23 1174.31)
  - [x] 제안서의 "4/23 +1.67%"는 장중 스냅샷 값, 종가 기준은 +0.90% → RSI 동적은 실제 종가 기반으로 더 보수적 발동

### Phase A-3: filters.py + screener.py (시그니처 전파) ✅ 완료 (2026-04-24)
- [x] `filters.py:40` 상수 일원화 — `RSI_UPPER_LIMIT = settings.RSI_UPPER_NORMAL`
- [x] `screen_stocks_in_theme()` 시그니처에 `rsi_upper: Optional[float] = None` 추가
- [x] 내부에서 `effective_rsi_upper = rsi_upper if rsi_upper is not None else settings.RSI_UPPER_NORMAL`
- [x] `apply_all_filters(stock_info, rsi_upper=effective_rsi_upper)` 전달 (라인 211)
- [x] `screen_all_themes()` 시그니처에 `rsi_upper: Optional[float] = None` + `max_total: Optional[int] = None` 추가
- [x] `run_daily_screening()` 시그니처에 `rsi_upper: Optional[float] = None` 추가
- [x] `main.py:954` 호출부 무수정 확인 (기본값 None → NORMAL 평시 기준)
- [x] `main.py:800` run_daily_screening 호출부 무수정 (내부 자동 regime 판정)

### Phase A-4: screener.py (헬퍼 + 컷 재설계) ✅ 완료 (2026-04-24)
- [x] `_get_market_regime_rsi(kis_api=None) -> float` 구현
  - [x] `RSI_DYNAMIC_ENABLED=False` 시 NORMAL 즉시 반환
  - [x] KIS 인스턴스 재사용 (없으면 생성/close)
  - [x] 전일 등락률 조회 실패 시 NORMAL 폴백 + WARN 로그
  - [x] `logger.info("[RSI Regime] KOSPI 전일 {rate:+.2f}% → {regime} (RSI 상한 {applied})")`
  - [x] 실기 검증: KOSPI +0.90% → NORMAL(70) 정상 판정
- [x] `_apply_theme_min_slot(candidates, min_score, safety_floor) -> tuple[list, set]` 구현
  - [x] 테마별 그룹핑 후 상위 1개 선정
  - [x] 각 테마 상위 1개가 `score >= safety_floor`이면 보장 set에 추가
  - [x] safety_floor 미달 테마는 WARN 로그
  - [x] 반환: (보장 후 필터링된 candidates, 보장된 종목 코드 set)
  - [x] 4/23 시뮬레이션 검증: 입력 9건(5테마) → 출력 6건 (테마 5개 모두 보장 + 정상 1건)
- [x] `screen_all_themes` 내 `max_total=None` 케이스 지원 (컷 지연)
- [x] `run_daily_screening` 3단 컷 적용
  - [x] ① `_apply_theme_min_slot()` 호출
  - [x] ② 보장 미활성 시 평면 `min_score` 컷 (기존 동작 유지)
  - [x] ③ `max_total=MAX_TOTAL_CANDIDATES` 최종 컷 (보장 종목 우선 보존 + 재정렬)
- [x] 로깅: `logger.info(f"[Theme Slot] {n}개 테마 슬롯 보장 ({codes})")`
- [x] `screening_logs` 딕셔너리에 `rsi_at_screen`, `theme_slot_protected` 키 추가

## 검증 항목

### 단위 검증 ✅ 완료 (2026-04-24)
- [x] `python -m py_compile config.py database.py` 통과
- [x] `python -m py_compile modules/stock_screener/kis_api.py filters.py screener.py` 통과
- [x] `python -m py_compile main.py` 통과
- [x] 4/23 저널로그 샘플 데이터로 `_apply_theme_min_slot` 단독 테스트
  - [x] 9건 입력 (5개 테마) → 출력 6건 (5테마 모두 보장 + 정상 ≥45점 1건 = 46.2 인터플렉스)
- [x] `_get_market_regime_rsi` 실기 검증 (KOSPI +0.90% → NORMAL RSI 70)

### 통합 검증 ✅ 완료 (2026-04-24)
- [x] `code-tester` 에이전트로 수정 파일 6개 종합 리뷰 → **PASS with minor**
- [x] code-tester 주의 이슈 2건 즉시 수정
  - [x] config.py `RSI_UPPER_LIMIT` 중복 Field 제거 + .env 동기화
  - [x] kis_api.py `get_prev_index_change_rate` output2 정렬 방어 추가
- [x] DB v14 마이그레이션 적용 확인 (2026-04-24 13:09:14 적용됨)
- [x] `PRAGMA table_info(screening_log)` → rsi_at_screen(idx 11), theme_slot_protected(idx 12) 존재
- [x] `SELECT MAX(version) FROM schema_version` = 14

### 실전 관찰 검증 (1주, 2026-04-28 ~ 2026-05-02)
- [ ] 매일 `[RSI Regime]` INFO 로그 기록 확인
- [ ] 매일 `[Theme Slot]` INFO 로그 기록 확인 (발동 시)
- [ ] `screening_log` 새 컬럼 populated 확인:
  - [ ] `SELECT COUNT(*), AVG(rsi_at_screen) FROM screening_log WHERE date >= '2026-04-28'` > 0
  - [ ] `SELECT COUNT(*) FROM screening_log WHERE theme_slot_protected=1` > 0 (최소 1일)
- [ ] 매수 실패일 ≤ 1/5 (20%) 목표 달성
- [ ] RSI 70~75 통과 종목 5일 평균 수익률 ≥ -5% (롤백 트리거 미발동)
- [ ] 전체 매도 건 평균 수익률 직전 2주 대비 -5%p 이내 유지

## 배포 항목

### 배포 전 체크 ✅ 완료 (2026-04-24 22:14 KST)
- [x] `ps aux | grep main.py | grep -v grep` → 기존 PID 484162 확인 (systemd 재시작으로 교체)
- [x] `trading_system.pid` 잔여 파일 — systemd 관리, 재시작 시 자동 갱신
- [x] `.env` 활성 계정 확인

### 배포 ✅ 완료 (2026-04-24 22:14 KST)
- [x] 장 마감(15:30 KST) 이후 `sudo systemctl restart trading_system` 실행
- [x] `sudo systemctl status trading_system` active (PID 1008604)
- [x] 스케줄러 정상 기동 로그 확인 (모든 잡 등록)
- [x] DB 자동 백업 생성 (`data/trading.bak.20260424_220913*`)

### 이상 시 롤백 (필요 시)
- [ ] `.env` 또는 `config.py`: `RSI_DYNAMIC_ENABLED=False` + `THEME_MIN_SLOT_ENABLED=False` → 재시작
- [ ] 롤백 시 `docs/improvements/change_log.md`에 롤백 사유 1줄 기록

## 문서 업데이트 항목

- [x] `docs/improvements/change_log.md`에 1줄 추가 (2026-04-24 행)
- [x] `memory/project_buy_filter_phase_a.md` 신규 작성
- [x] `memory/MEMORY.md` 인덱스에 위 파일 추가
- [ ] `memory/project_strategy.md`에 "RSI 동적 + 테마 슬롯" 섹션 추가 (1주 관찰 결과 반영 후)
- [ ] 프로젝트 `CLAUDE.md`: 새 교훈/규칙 발견 시에만 추가 (1주 관찰 후)
- [ ] `modules/CLAUDE.md`: 필요 시 추가 (1주 관찰 후)
- [ ] 3문서 (PLAN/CONTEXT/CHECKLIST) `active/` → `completed/20260501_buy-filter-rsi-theme-slot/` 이동 (Phase A-6 1주 관찰 후)

## 완료 게이트 (선언 전 체크)

- [ ] 구현 항목 전부 `[x]`
- [ ] 검증 항목 전부 `[x]`
- [ ] 배포 항목 전부 `[x]`
- [ ] **문서 업데이트 항목 전부 `[x]`** ← 이 단계를 빠뜨리지 말 것
- [ ] `active/` → `completed/` 아카이브 완료
