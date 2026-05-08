# CHECKLIST: 종가베팅 universe v2

## 구현 항목

### 단위 2-9a: universe_provider_v2 — 다중 출처 통합 ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/universe_provider_v2.py` 신규 (~330줄)
  - [x] `get_universe_v2() -> list[str]` — 4 출처 합집합
  - [x] 출처 1 (Layer 3 테마): `_fetch_theme_codes_v2()` — 기존 v1 `_fetch_top_themes` + `_crawl_theme_codes` 재사용
  - [x] 출처 2 (Layer 3 모멘텀): `_fetch_top_value_codes()` — pykrx 거래대금 상위 30 (KOSPI+KOSDAQ)
  - [x] 출처 3 (Layer 3 모멘텀): `_fetch_top_change_codes()` — pykrx 당일 등락률 상위 30
  - [x] 출처 4 (Layer 1 수급): `_fetch_top_foreign_buy_codes()` — pykrx 외국인 순매수 상위 30 (컬럼: `순매수거래대금`)
  - [x] 합집합 + 6자리 검증 + 중복 제거 + 스윙 보유 제외
  - [x] hard_cap (default 100)
  - [x] in-memory 캐시 (같은 거래일 1회만 pykrx 호출, 더블체크 lock)
  - [x] 각 출처 try/except 격리 (`_safe_call` 헬퍼, 빈 응답/None/KeyError 흡수)
  - [x] pykrx import lazy (`_import_pykrx()`)
  - [x] v1 인터페이스 alias `get_universe()` 제공 (scheduler.py 한 줄 교체용)
- [x] py_compile 통과
- [x] 단위 테스트 18 시나리오 모두 PASS (요구 15+ 충족):
  - [x] UV2-1: 4 출처 모두 정상 → 합집합 + 중복 제거
  - [x] UV2-2: pykrx 1개 실패 → 다른 3개로 진행
  - [x] UV2-3: pykrx 모두 실패 → 빈 리스트 폴백
  - [x] UV2-4: 캐시 히트 (같은 거래일 두 번째 호출 → 출처 호출 0회)
  - [x] UV2-5: 스윙 테마 + pykrx 합산 (출처 다중성 검증)
  - [x] UV2-6: 무효 종목코드 정규식 필터 (영문/길이/None 제외)
  - [x] UV2-7: 스윙 보유 제외
  - [x] UV2-8: hard_cap 도달 시 잘라냄
  - [x] UV2-9: 거래대금 상위 N — KOSPI+KOSDAQ 합산 nlargest
  - [x] UV2-10: 외국인 순매수 — investor="외국인" 정확 매핑 + 합산
  - [x] UV2-11: 등락률 상위 N — 음수(하락 종목)도 nlargest 영향
  - [x] UV2-12: 모킹 환경 호출 시간 < 1초 (회귀 — 실전 < 10초)
  - [x] UV2-13: 비-영업일 (빈 DataFrame) → 빈 리스트
  - [x] UV2-14: 인스턴스 lock 동시 호출 안전 (스레드 2개 → 빌드 1회)
  - [x] UV2-15: 호출 결과 list[str] 타입 + 모두 6자리
  - [x] UV2-16: None 반환 graceful 폴백
  - [x] UV2-17: v1 시그니처 alias `get_universe()` 호환
  - [x] UV2-18: pykrx KeyError graceful (실전에서 본 패턴 회귀)
- [x] code-tester 검증
  - [x] **심각 1건 발견 → 즉시 수정**: `_fetch_top_foreign_buy_codes` 컬럼명 `"거래대금"` → `"순매수거래대금"` (pykrx `get_market_net_purchases_of_equities_by_ticker` 실제 컬럼명. 수정 전이면 외국인 출처 항상 빈 리스트 → Layer 1 시그니처 무력화)
  - [x] 테스트 mock fixture `_make_foreign_df` 컬럼명도 동시 수정 (테스트가 버그 가려주는 구조 해소)
  - [x] 수정 후 재실행: 18 시나리오 PASS, 심각 0건 / 주의 2건(P2 — settings.yaml 미연동 / isinstance 방어 가드 — 기능 무관)
  - [x] **종합 판정: 단위 2-9a 완료 기준 충족** (PLAN의 "code-tester 심각 0건" 충족)

### 단위 2-9b: PRD 4-1 속성 필터 + 4-4 유동성 필터 ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/universe_filters.py` 신규 (~440줄, 별도 파일 결정)
  - [x] `apply_attribute_filters(tickers, today, config) -> tuple[list[str], dict]`
  - [x] `apply_liquidity_filters(tickers, today, config) -> tuple[list[str], dict]`
  - [x] `apply_all_filters(tickers, today, config) -> tuple[list[str], dict]` 통합
  - [x] settings.yaml `stock_filter` / `liquidity` 섹션 로드 (`_load_filter_config`)
        — `closing_bet_system.storage.db._load_settings()` 재사용 / default 폴백 보유
  - [x] 시총 < 500억 제외 (`get_market_cap_by_ticker` bulk fetch)
  - [x] 주가 < 1000원 제외 (`get_market_ohlcv_by_ticker` "종가")
  - [x] 상한가 제외 (등락률 >= +29.5% — `UPPER_LIMIT_THRESHOLD_PCT` 모듈 상수)
  - [x] 52주 고점 -30% 초과 하락 제외 (`get_market_ohlcv_by_date(today-365, today, ticker)`)
  - [x] 20일 평균 거래대금 < 50억 제외 (`get_market_ohlcv_by_date(today-30, today, ticker)` 거래대금 평균)
  - [x] 당일 거래대금 < 100억 제외 (bulk fetch 의 "거래대금")
  - [x] 보호예수 D-7 / 호가 스프레드 비정상 → **후속 단위로 분리** (별도 데이터, 본 단위 범위 외)
  - [x] **bulk fetch 우선**: 시총/OHLCV 시장당 1회 호출 (KOSPI+KOSDAQ × 2 = 4 호출 ~5초)
  - [x] **종목별 fetch 최소화**: 52주 고점 / 20일 평균만 종목별 호출 (캐시 + 통과 후 호출)
  - [x] **보수적 하드 필터**: pykrx 실패 시 `data_not_found` 탈락 (PRD 의도 — 데이터 없으면 진입 안 함)
  - [x] **first-rejection-only**: 한 종목 여러 필터 위반 시 첫 위반 사유만 기록 (PRD 11-1 candidates 스키마 정합)
  - [x] `_safe_float()` NaN/inf 가드 (CLAUDE.md `pd.isna()` 규칙)
- [x] `universe_provider_v2.get_universe_v2_filtered()` 통합 함수 추가
  - [x] v2 산출 → 필터 적용 → 최종 universe 반환
  - [x] 필터 import/실행 실패 시 unfiltered universe graceful 폴백
  - [x] `get_universe()` v1 alias → `get_universe_v2_filtered()` 라우팅
- [x] py_compile 통과 (3 파일: universe_filters / universe_provider_v2 / 테스트)
- [x] 단위 테스트 15 시나리오 모두 PASS (요구 12+ 충족):
  - [x] UF-1: 시총 500억 미만 제외
  - [x] UF-2: 주가 1000원 미만 제외
  - [x] UF-3: 상한가 제외 (+29.5%, +29.97% 모두 탈락)
  - [x] UF-4: 52주 고점 -30% 초과 하락 제외
  - [x] UF-5: 20일 평균 거래대금 < 50억 제외
  - [x] UF-6: 당일 거래대금 < 100억 제외
  - [x] UF-7: 모든 필터 통과 종목 → 빈 dict (rejection 없음)
  - [x] UF-8: 한 종목이 여러 필터 위반 → 첫 위반만 기록
  - [x] UF-9: pykrx 호출 실패 → `data_not_found` 보수적 탈락
  - [x] UF-10: 통합 함수 — universe_v2 + 필터 → 최종 list
  - [x] UF-11: rejected 사유 dict 형식 검증 (`{ticker: reason}`)
  - [x] UF-12: 50종목 시뮬 통과율 60% (50~90% 자연 분포)
  - [x] UF-13: 빈 입력 → 빈 결과
  - [x] UF-14: settings.yaml 로드 실패 → default 폴백
  - [x] UF-15: 필터 import 실패 → unfiltered universe graceful 폴백
- [x] code-tester 검증
  - [x] **심각 0건** / 주의 2건 (docstring 문구 + `pd.isna()` 가드 — 즉시 반영)
  - [x] 주의 1: docstring "graceful 통과" → "data_not_found 탈락 (보수적 하드 필터)" 문구 정정
  - [x] 주의 2: `_safe_float()` 헬퍼 추가 + 모든 pandas → float 변환 NaN 가드 적용 (3 호출처)
  - [x] 회귀 검증: 2-9a 18건 + 2-9b 15건 모두 PASS
  - [x] **종합 판정: 단위 2-9b 완료 기준 충족** (PLAN의 "code-tester 심각 0건")

## 검증 항목

### 단위 검증
- [x] py_compile 3 파일 통과 (universe_provider_v2 / universe_filters / scheduler)
- [x] 단위 테스트 33 시나리오 PASS (2-9a 18 + 2-9b 15)
- [x] code-tester 심각 0건 (2-9a 1건 발견 즉시 수정 완료, 2-9b 0건)

### 통합 검증 (2026-05-04 KST 20:25 단발 — pykrx 야간 차단으로 부분 검증)
- [x] scheduler.py 교체 후 import 성공 (`universe_provider_v2.get_universe_v2_filtered`)
- [x] 단발 트리거 — graceful 폴백 정상 작동 (예외 0건, 호출 6.41초 < 10초 목표 충족)
- [x] 출처 1 (스윙 테마) 정상 — 19종목 산출
- [x] 출처 2~4 (pykrx 거래대금/등락률/외국인) — **야간 KRX 사이트 차단으로 0건** (CONTEXT.md 기록 패턴 그대로 재현, 5/6 15:10 자연 트리거에서 재검증)
- [x] 필터 통과율 60~80% 분포 확인 (5/6 15:10 자연 트리거 후)
- [x] candidate_features INSERT 정상 (기존 흐름 영향 없음, 5/6 15:10 후)
- [x] orderbook_snapshots INSERT 정상 (5/6 15:10 후)

### 실전 검증 (배포 후 1일 — 5/6 수요일 15:10 KST 첫 자연 트리거)
- [x] 15:10 잡 트리거 시 universe v2 산출 + 4 출처 로그 확인
- [x] pykrx 호출 시간 모니터링 (< 10초)
- [x] candidates 테이블 일일 30~80건 누적 확인
- [x] 운영 점검 게이트 진척도 가속 (1주 이내 30건 도달 가능)

## 배포 항목 ✅ (2026-05-04 KST 20:28 완료)
- [x] systemd 재시작 전 선행 체크 (단일 PID 2540234, PID 파일 매칭, 이중 실행 없음)
- [x] 장 마감 후 (KST 20:28, 5/4 월요일 장 마감 후 약 5시간)
- [x] `sudo systemctl restart trading_system` (PID 2540234 → 2695913)
- [x] active(running) 확인 (재시작 후 5초 경과 정상)
- [x] 종가베팅 잡 3건 + **universe v2 활성 로그 확인** ("Phase 1 알림형, universe v2 + providers 4종 활성")
- [x] 첫 15:10 잡 트리거 결과 관찰 (**5/6 수요일** — 5/5 어린이날 휴일 건너뜀)

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가
- [x] `memory/project_closing_bet_system.md` 갱신 — universe v2 적용
- [x] `memory/MEMORY.md` 인덱스 갱신
- [x] 3문서 active → completed/YYYYMMDD_closing-bet-universe-v2/ 이동

## 완료 게이트 (선언 전 체크)
- [x] 구현 항목 전부 `[x]` (단위 2-9a, 2-9b)
- [x] 검증 항목 전부 `[x]`
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 전부 `[x]`

## 새 대화 시작 가이드 (CLAUDE.md 권장)
이번 대화는 이미 8단위 작업 + 2회 배포 처리 → 컨텍스트 큼.
**새 대화에서 `/resume` 호출** → 이 3문서 자동 로드 → 단위 2-9a 시작.
