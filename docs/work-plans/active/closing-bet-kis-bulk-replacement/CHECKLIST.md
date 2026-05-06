# CHECKLIST — 단위 2-9d · KIS Open API ranking 대체

## 구현 항목

### Step 1 — `kis_market_provider.py` 신규 모듈
- [x] `closing_bet_system/collectors/kis_market_provider.py` 신규 (~280줄)
- [x] 모듈 상수: TR_ID 4종 / URL path 4종 / 응답 컬럼명 / DEFAULT_TOP_N=30 / DEFAULT_MARKET_CAP_TOP_N=200
- [x] `KISMarketProvider` 클래스 — `KISApi` 인스턴스 위임 (토큰 공유)
- [x] `get_kis_market_provider()` 싱글톤 헬퍼 (race 방지 lock)
- [x] `get_top_value_codes(top_n=30, market="ALL") -> list[str]` (volume-rank, BLNG_CLS=3)
- [x] `get_top_change_codes(top_n=30, direction="up") -> list[str]` (fluctuation, direction 인자 반영)
- [x] `get_top_foreign_buy_codes(top_n=30) -> list[str]` (foreign-institution-total, ETC_CLS=1)
- [x] `get_top_market_cap_data(top_n=200, market="ALL") -> dict[str, dict]` (market-cap)
- [x] 응답 파싱: `output[*]['mksc_shrn_iscd']` 6자리 정규식 검증
- [x] `_safe_int`/`_safe_float` NaN 가드
- [x] `rt_cd != "0"` warn + 빈 리스트/dict 폴백
- [x] 모든 메서드 try/except 격리

### Step 2 — `universe_provider_v2.py` 라우팅
- [x] `_is_kis_ranking_enabled()` 토글 헬퍼 추가
- [x] `_fetch_top_value_codes` 본문 토글 분기
- [x] `_fetch_top_change_codes` 본문 토글 분기
- [x] `_fetch_top_foreign_buy_codes` 본문 토글 분기
- [x] 토글 false 시 회귀 안전 (기존 pykrx 호출 그대로)

### Step 3 — `universe_filters.py` 시총 보강 (옵션 B)
- [x] `_load_filter_config()` 에 `fallback_include_market_cap`/`kis_market_cap_top_n` 키 추가
- [x] `_maybe_run_per_ticker_fallback`에 KIS market-cap 호출 분기 추가
- [x] cfg 명시적 default 폴백 (코더 검토 심각 #1 NameError 방어)
- [x] **⚠️ stck_avls 단위 미확정으로 default false 처리** (단위 2-9e 후속)

### Step 4 — `settings.yaml` 갱신
- [x] `data_source.use_kis_ranking: true` (default)
- [x] `data_source.fallback_include_market_cap: false` (단위 2-9e 후 활성화)
- [x] `data_source.kis_market_cap_top_n: 200`
- [x] `kis.use_mock: false` 전환 (메인 봇 실전 통합)
- [x] 주석에 단위 2-9d 도입 배경 + Sources 추가

### Step 5 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9d.py`)
- [x] KP-1 ~ KP-12 모두 PASS (12건)

### code-tester 보강 수정 (2026-05-06)
- [x] 심각 #1: `cfg` NameError 방어 (settings 로드 실패 시 default 폴백)
- [x] 심각 #2: `direction` 파라미터 실제 반영 (rank_sort 분기)
- [x] 심각 #3: `_FIELD_CHANGE_RATE` 상수 사용 (문자열 리터럴 제거)
- [x] 주의 #3: `stck_avls` 단위 진단 로그 추가
- [x] 회귀 재실행 — 단위 12건 + 회귀 68건 모두 PASS

## 검증 항목

### 단위 검증
- [x] py_compile 통과 (4개 파일)
- [x] `pytest scripts/test_closing_bet_unit_2_9d.py` 12건 PASS
- [x] 회귀: 2-9a 18 + 2-9b 15 + 2-9c 10 + 2-9d 12 + 2-2b-1 15 + 2-2b-2 10 = 80건 PASS
- [x] code-tester 검증 — 심각 3건 + 주의 6건 발견 즉시 수정 후 재검증 PASS

### 통합 검증 (단발)
- [x] `get_universe_v2_filtered()` 단발 호출
- [x] KIS API 호출 정상 (토큰 발급 + 4 ranking 호출)
- [x] **출처별 기여**: theme=17 / top_value=25 / top_change=0 / top_foreign=21 = **63종목** (17→63 3.7배 증가)
- [x] **호출 시간**: 14.79초 (KIS 4 ranking + 종목별 by_date 폴백 63건 포함)
- [x] universe = 0건 (시총 보강 OFF로 옵션 A 보수 그대로 — 의도)

### 실전 검증 (5/7 자연 트리거)
- [ ] 5/7 15:10 KST 자동 트리거 발화
- [ ] `[universe_v2] 출처별 기여` 모두 비-0 (top_value/top_change/top_foreign)
- [ ] KIS API 호출 시간 ≤ 5초 (ranking 4종 합계)
- [ ] 일일 요약 텔레그램 알림 발송 (15:35)
- [ ] universe ≥ 5건 — **단위 2-9e 시총 보강 활성 후에만 가능** (현 단계는 0건 정상)

## 배포 항목 (2026-05-06 KST 20:14 완료)
- [x] 단일 PID 확인 (3414631 → 3440709)
- [x] `sudo systemctl restart trading_system`
- [x] active(running) 확인
- [x] 종가베팅 잡 3건 등록 + Phase 1 알림형 로그 확인

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-9d 항목)
- [x] `memory/project_closing_bet_system.md` — 단위 2-9d 1단락 추가
- [x] `memory/MEMORY.md` 인덱스 description 갱신
- [x] git commit + push (commit `bcb633c` 포함 origin/main 동기화 확인)
- [ ] 3문서 active → completed/20260506_closing-bet-kis-bulk-replacement/ (5/7 자연 트리거 검증 후)

## 후속 단위 (별도 작업)
- **단위 2-9e — 시총 보강 정규화**: KIS `stck_avls` 단위 검증(인덱스 2~3 종목 비교) + 정규화 또는 종목별 inquire-price 호출. `fallback_include_market_cap=true` 활성화. universe ≥ 5건 달성 목표.

## 완료 게이트 (선언 전 체크)
- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 단위/통합 전부 `[x]` (실전 검증은 5/7 자연 트리거 후)
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 (아카이브 제외) 전부 `[x]`
- [x] git commit + push (`bcb633c` origin/main 동기화 완료)
- [ ] 5/7 자연 트리거 검증 후 → active → completed 아카이브
