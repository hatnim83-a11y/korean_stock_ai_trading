# PLAN — 단위 2-9f · ETF/우선주 차단 + 자체 시총 계산 + pykrx bulk 게이트 변경

> **사전 리뷰 반영 (2026-05-07)** — Plan + strategy-coder 에이전트 병렬 리뷰 결과 P0 블로커 2건 + 심각/주의 11건 사전 정정 후 수립.

## 목표
1. **ETF/우선주 universe 진입 차단** — 단위 2-9e 트리거 조건 충족(5/7 우선주 005935 삼성전자우 universe 진입 발견). 알림 노이즈 제거 + 자동매매 시 본주 가격 괴리 위험 차단
2. **stck_avls × 1억 + lstn_stcn × stck_prpr 자체 시총 계산** — 단위 2-9d/2-9e 한계(KIS market-cap top 200 한도) 보강. 5/7 매치율 18/68(26%) → 60+/68(85%+) 목표
3. **pykrx bulk 정상 복구 시에도 KIS 시총 보강 유지 분기** — 데이터 일관성(KIS 단일 출처) vs 데이터 정확성(pykrx KRX 공식) trade-off는 토글로 운영자 선택

## 배경 + 사전 리뷰 발견 사항
- **5/7 자연 트리거 (단위 2-9e 후)**: universe = 18건 / 우선주 005935 삼성전자우 진입. 단위 2-9e CHECKLIST 65줄 트리거 조건 충족
- **단위 2-9e CHECKLIST 후속 가이드 (105줄)**: ETF prefix + 우선주 끝자리 5/7/9 차단 + lstn_stcn × stck_prpr 자체 계산 + pykrx bulk 게이트 변경
- **5/7 단발 조사 추가 발견 (probe_kis_ranking_fields.py)**: `volume_rank` / `market_cap` 응답에 `lstn_stcn` 존재. `fluctuation` / `foreign_total` 응답에는 부재
- **사전 리뷰 P0 블로커 2건**:
  - **ETF prefix 룰 22% 커버리지**: 102/122/233/252/360/419/466 등 메이저 ETF 누락 + 069080 웹젠 / 091990 셀트리온헬스케어 / 117730 티로보틱스 / 292340 모코 (KOSDAQ 보통주) false positive
  - **우선주 끝자리 룰**: KOSDAQ 보통주 끝자리 5/7/9 케이스 + SPAC + 재상장 종목 false positive
- **사전 리뷰 핵심 권고**:
  - **ETF**: pykrx `get_etf_ticker_list()` 동적 조회 (정확) + 정적 화이트리스트 보강 (안전망)
  - **우선주**: 종목명 "우/우B/우C/우K" 접미사 검증 (정확) + 끝자리 5/7/9 룰 (보조) → **AND 조건**으로만 차단
  - **시총**: stck_avls × 1억 (정확, 자기주식 차감 반영) 우선 + lstn_stcn × stck_prpr (보조, top 200 한도 우회)
  - **KIS native filter**: `FID_DIV_CLS_CODE=1` (보통주만) 옵션 매뉴얼 검증 후 채택 검토 (가장 깔끔한 source-level 차단)

## 현재 코드 상태 (수정 대상)
- `closing_bet_system/collectors/universe_filters.py` (737줄) — 속성 필터 본문 + `_maybe_run_per_ticker_fallback` (108줄, 단위 2-9c+2-9d+2-9e 누적) + cfg fallback 3개 default
- `closing_bet_system/collectors/kis_market_provider.py` (353줄, 단위 2-9d-hotfix 직후) — 4 ranking 메서드 + lstn_stcn 회수 경로 부재
- `closing_bet_system/collectors/universe_provider_v2.py` — KIS ranking 호출부 (호환성 영향)
- `closing_bet_system/config/settings.yaml` — stock_filter / data_source 토글 추가

## 구현 단계

### Step 0 — 사전 조사 (코딩 진입 차단 게이트)
- [ ] **pykrx ETF 리스트 동적 조회 가능 여부 검증** — 단위 2-9c에서 KRX bulk API 차단됐지만 `get_etf_ticker_list()`는 별도 엔드포인트 사용 가능성. 단발 호출 + EUC-KR/UTF-8 인코딩 + timeout 검증
- [ ] **KIS API `FID_DIV_CLS_CODE` 매뉴얼 검증** — koreainvestment/open-trading-api 공식 GitHub WebFetch:
  - volume-rank (FHPST01710000) — 이미 코드에 `# 전체 (1=보통주, 2=우선주)` 주석 있음 → 검증 완료
  - fluctuation (FHPST01700000) — `fid_div_cls_code` 파라미터 존재? 의미 동일?
  - foreign-institution-total (FHPTJ04400000) — `FID_DIV_CLS_CODE` 파라미터 존재?
- [ ] **5/7 universe 18건 + KOSPI/KOSDAQ 거래대금 top 100 분포 실측** — `scripts/probe_etf_pref_distribution.py` 신규:
  - 6자리 종목코드 → pykrx ETF 리스트 매칭 → ETF 비율
  - 끝자리 5/7/9 종목 → 종목명 "우/우B/우C/우K" 접미사 매칭 → 우선주 진성 / false positive 분포
  - false positive 후보 list-up (보통주가 끝자리 5/7/9 + 종목명 "우" 미포함)
- [ ] **자기주식 비중 큰 종목 sample 단발 검증** — SK하이닉스/셀트리온/네이버 등에서 stck_avls × 1억 vs lstn_stcn × stck_prpr 차이 측정 → 시총 보강 우선순위 결정 근거
- [ ] **pykrx ETF 리스트가 사용 불가하다고 판명되면**: 정적 화이트리스트 set (~800개 ETF 종목코드 하드코딩) 옵션 사전 합의
- [ ] **CONTEXT.md "사전 조사 결과" 섹션에 모두 기록**

### Step 1 — `universe_filters.py` ETF/우선주 차단 분기

#### 1.1 모듈 상수 + cfg fallback default
- [ ] 모듈 상수 추가:
  - `_BLOCK_ETF_DEFAULT = True`
  - `_BLOCK_PREF_STOCK_DEFAULT = False` (점진 활성화 — 1주 관찰 후 True)
  - `_KIS_MARKET_CAP_PRIORITY_DEFAULT = False` (default false — pykrx 정상 시 KRX 공식 사용. 토글 ON 시에만 KIS 우선)
  - `_PREF_STOCK_LAST_DIGITS = frozenset({"5", "7", "9"})`
  - `_PREF_STOCK_NAME_SUFFIXES = ("우", "우B", "우C", "우K")` (KOSPI 우선주 종목명 접미사)
  - `_ETF_TICKER_CACHE_TTL_SEC = 86400` (pykrx 일별 캐시)
- [ ] `_load_filter_config()` 에 신규 3개 키 추가 + cfg fallback dict에 default reflect

#### 1.2 ETF/우선주 헬퍼 함수
- [ ] `_get_etf_ticker_set(today_str: str) -> frozenset[str]`:
  - pykrx `get_etf_ticker_list(today_str)` 1회 호출 + `_etf_ticker_cache` (TTL 86400s, dict 더블체크 lock)
  - try/except 격리: pykrx 호출 실패 시 정적 화이트리스트 set 폴백 (Step 0에서 결정)
  - 호출 결과 INFO 로그 1회 (`[universe_filters] ETF 리스트 N건 캐시`)
- [ ] `_is_etf(ticker: str, today_str: str) -> bool`:
  - 6자리 종목코드 정규식 검증
  - `_get_etf_ticker_set(today_str)` 매칭
- [ ] `_is_pref_stock(ticker: str, name: str) -> bool`:
  - 6자리 종목코드 정규식 검증
  - **AND 조건**: 끝자리 ∈ `_PREF_STOCK_LAST_DIGITS` AND 종목명이 `_PREF_STOCK_NAME_SUFFIXES` 중 하나로 끝남
  - 종목명 부재(KIS API 응답에서 회수 실패) 시 False 반환 (보수적 — false positive 회피)

#### 1.3 차단 분기 (apply_attribute_filters 본문)
- [ ] **차단 분기 위치**: KIND severity `if severity_map:` 블록 **밖**, `for ticker in tickers:` 루프 첫 단계 (severity_map 유무 무관 적용)
- [ ] ETF 차단 → 우선주 차단 순서 (ETF 먼저 차단되면 `is_etf` 기록)
- [ ] rejection_reason: `"is_etf"` / `"is_pref_stock"` (모듈 상수 `REJECTION_REASON_IS_ETF`, `REJECTION_REASON_IS_PREF_STOCK` 추가)
- [ ] **로그 분해**: `[universe_filters] 속성 필터: {passed}/{original} 통과 (KIND {kind} + ETF {etf} + 우선주 {pref} + 속성 {attr} 탈락)`
- [ ] **첫 호출 1회 진단 로그**: `[universe_filters] ETF 차단 list: [...]` / `우선주 차단 list: [...]` (단위 2-9e 패턴 일관, INFO 레벨)
- [ ] py_compile 통과

### Step 2 — `kis_market_provider.py` lstn_stcn 자체 시총 계산

#### 2.1 모듈 상수
- [ ] `_FIELD_LISTED_SHARES = "lstn_stcn"` (단위 2-9d 발견 패턴 일관 — 문자열 리터럴 산재 방지)

#### 2.2 헬퍼 함수
- [ ] `_compute_market_cap_from_response(item: dict) -> Optional[int]`:
  - `lstn_stcn = _safe_int(item.get(_FIELD_LISTED_SHARES), default=0)`
  - `stck_prpr = _safe_int(item.get(_FIELD_PRICE), default=0)`
  - 둘 다 > 0 검증 → `lstn_stcn * stck_prpr` (정수 × 정수 = 정수, IEEE 754 정밀도 손실 없음)
  - 둘 중 하나라도 ≤ 0 → None 반환
- [ ] **단위 진단 로그** (첫 호출 1회, 단위 2-9e 패턴 일관):
  - `[kis_market_provider] lstn_stcn × stck_prpr 자체 계산 — {code} {lstn_stcn:,d} × {stck_prpr:,d} = {market_cap:,d}원 (stck_avls 대비 차이 ~0.01% 자기주식 영향)`

#### 2.3 신규 메서드 (옵션 A — 메서드 분리, 단위 2-9d `get_top_market_cap_data` 명명 일관)
- [ ] `get_top_value_data(top_n: int = DEFAULT_TOP_N, market: str = "ALL") -> dict[str, dict]`:
  - volume_rank API 호출 (응답에 lstn_stcn 존재)
  - 각 item에서 `_compute_market_cap_from_response(item)` 호출
  - 반환: `{ticker: {market_cap, close, change_rate, lstn_stcn}}`
  - 기존 `get_top_value_codes` 유지 (universe_v2.py 호환)
- [ ] **검토만 (보류)**: `get_top_change_data` / `get_top_foreign_buy_data` — 응답에 lstn_stcn 부재 → 시총 자체 계산 불가, 신규 X

#### 2.4 KIS native filter 옵션 (Step 0 매뉴얼 검증 결과 반영)
- [ ] volume-rank `FID_DIV_CLS_CODE` 옵션 — 현재 `"0"` (전체) → settings 토글로 `"1"` (보통주만) 선택 가능하게:
  - `data_source.kis_div_cls_code: "0"` (default 전체, 점진 활성화)
  - 토글 ON 시 universe_provider_v2.py 출처 2 자체에서 ETF/우선주 source-level 제외
  - **fluctuation/foreign_total 동일 파라미터 지원 시에만 토글 적용** (Step 0 검증 결과 의존)

### Step 3 — `universe_filters.py` 시총 보강 우선순위 + helper 분리

#### 3.1 helper 분리 (108줄 함수 비대화 방지)
- [ ] `_enrich_market_cap_from_kis_top(candidates: list[str], cfg: dict) -> dict[str, dict]`:
  - 단위 2-9d/2-9e 기존 코드 추출 (kis_market_provider `get_top_market_cap_data` 호출 + stck_avls × 1억 정규화)
  - 1순위 (가장 정확, 자기주식 차감 반영, top 200 한도 있음)
- [ ] `_enrich_market_cap_from_volume_rank(candidates: list[str], cfg: dict) -> dict[str, dict]`:
  - **신규 (단위 2-9f)** — kis_market_provider `get_top_value_data` 호출 + lstn_stcn × stck_prpr
  - 2순위 (top 200 한도 우회, ~0.01~5% 차이)
- [ ] `_enrich_ohlcv_per_ticker(candidates: list[str], today_str: str, krx) -> dict[str, dict]`:
  - 단위 2-9c 기존 코드 추출 (pykrx 종목별 OHLCV 폴백 — close/change_rate/today_value 채움)
- [ ] `_maybe_run_per_ticker_fallback` 본문은 cfg 로드 + 우선순위 호출 + dict merge + 캐시 저장 골격만 유지 (~50줄)

#### 3.2 시총 보강 우선순위 (kis_market_cap_priority 해석 명확화)

**해석 A 채택 (권장)**: pykrx bulk 빈 응답 시에만 시총 보강 진입. 시총만 KIS로 채우고 OHLCV는 pykrx 폴백 그대로.
- 1순위: `_enrich_market_cap_from_kis_top` (stck_avls × 1억, top 200 매치)
- 2순위: 1순위 미매치 종목에 대해 `_enrich_market_cap_from_volume_rank` (lstn_stcn × stck_prpr, top 30)
- 3순위: 2순위도 미매치 → `data_not_found` 탈락 (옵션 A 보수)
- pykrx 정상 복구 시 동작:
  - **`kis_market_cap_priority=False` (default)**: pykrx 정상 시 _maybe_run_per_ticker_fallback 미진입 (현 동작 유지) → KRX 공식 시총 사용
  - **`kis_market_cap_priority=True`**: pykrx 정상이어도 시총 컬럼만 KIS로 후처리 덮어쓰기 (단위 2-9f 활성화 시 일관성 유지용 옵션)

### Step 4 — `settings.yaml` 토글 추가
- [ ] `stock_filter.etf_block_enabled: true`
- [ ] `stock_filter.pref_stock_block_enabled: false` (점진 활성화)
- [ ] `data_source.kis_market_cap_priority: false` (default false)
- [ ] `data_source.kis_div_cls_code: "0"` (Step 0 검증 후 "1" 활성화)
- [ ] yaml 문법 검증

### Step 5 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9f.py`) — 22건+

#### 5.1 ETF 차단 (5건)
- [ ] **ETF-1**: 069500 (KODEX 200) → ETF 리스트 매칭 → `is_etf` 탈락
- [ ] **ETF-2**: 102110 (TIGER 200) → 매칭 → 탈락 (메이저 ETF 회귀 검증)
- [ ] **ETF-3**: 069080 (웹젠, KOSDAQ 보통주) → ETF 리스트 미매칭 → **통과** (false positive 회귀 검증)
- [ ] **ETF-4**: 091990 (셀트리온헬스케어, 합병 전 KOSDAQ 보통주) → ETF 리스트 미매칭 → **통과** (false positive 회귀)
- [ ] **ETF-5**: 토글 OFF → 069500 통과 (회귀)

#### 5.2 우선주 차단 (5건)
- [ ] **PREF-1**: 005935 (삼성전자우, 끝자리 5 + 종목명 "삼성전자우") → AND 조건 → 탈락
- [ ] **PREF-2**: 005930 (삼성전자, 끝자리 0 + 종목명 "삼성전자") → 끝자리 미충족 → 통과
- [ ] **PREF-3**: 005385 (현대차우, 끝자리 5 + 종목명 "현대차우") → AND 조건 → 탈락
- [ ] **PREF-4**: KOSDAQ 보통주 끝자리 5/7/9 (사전 조사 결과 반영) + 종목명 "우" 미포함 → AND 미충족 → **통과** (false positive 회귀)
- [ ] **PREF-5**: 토글 OFF → 005935 통과 (회귀)

#### 5.3 자체 시총 계산 (4건)
- [ ] **MC-CALC-1**: lstn_stcn=5,846,278,608 + stck_prpr=271,500 → market_cap = 1,587,264,478,572,000원 (실제값)
- [ ] **MC-CALC-2**: lstn_stcn 부재 → None
- [ ] **MC-CALC-3**: stck_prpr=0 → None
- [ ] **MC-CALC-4**: NaN/문자/음수/공백 입력 → `_safe_int` default=0 → None

#### 5.4 통합 (3건)
- [ ] **INTEGRATION-1**: volume_rank 30종목 자체 계산 → 매치율 ≥ 80%
- [ ] **INTEGRATION-2**: stck_avls × 1억 1순위 + lstn_stcn × stck_prpr 2순위 우선순위 검증 (1순위 매치 종목은 1순위 값 유지)
- [ ] **INTEGRATION-3**: pykrx bulk 정상 복구 시 `kis_market_cap_priority=false` → KIS 보강 미진입 (회귀)

#### 5.5 회귀 (3건 — 직전 단위 회귀 검증)
- [ ] **REGRESSION-1** (단위 2-9d-hotfix): `mksc_shrn_iscd` 부재 + `stck_shrn_iscd` 만 있는 fluctuation 응답에서 ticker 추출 정상 + ETF/우선주 룰 정상 동작
- [ ] **REGRESSION-2** (단위 2-9e): market_cap top 200 응답에서 stck_avls × 1억 시총 정확값 (005930=1,555,110,100,000,000원) 유지
- [ ] **REGRESSION-3** (단위 2-9c): pykrx bulk 빈 응답 시 KIS 시총 보강 + 종목별 OHLCV 폴백 정상 동작

#### 5.6 Edge case (4건)
- [ ] **EDGE-1**: ticker=None / 빈 문자열 / 5자리 / 7자리 → `_is_etf` / `_is_pref_stock` False 반환 (None safe)
- [ ] **EDGE-2**: lstn_stcn 문자열 응답 (`"5846278608"`) → `_safe_int` 정상 처리
- [ ] **EDGE-3**: pykrx ETF 리스트 호출 실패 → 정적 화이트리스트 폴백 + warning 로그
- [ ] **EDGE-4**: ETF/우선주 토글 둘 다 OFF + 5/7 universe 18건 회귀 (단위 2-9e 결과 그대로 재현)

### Step 6 — code-tester 검증
- [ ] code-tester 에이전트 호출 (수정 4개 + 신규 테스트 1개 + 신규 probe 1개 대상)
- [ ] 단위 2-9d/2-9e/2-9d-hotfix 발견 패턴 재발 차단:
  - cfg NameError → 신규 키 4종 module_const + cfg fallback default 명시
  - direction dead parameter → 본 단위 무관 (확인)
  - `_FIELD_*` 상수 → `_FIELD_LISTED_SHARES` 추가 확인
  - 단위 진단 로그 → `lstn_stcn × stck_prpr` 첫 호출 1회 + ETF/우선주 차단 list 추가 확인
- [ ] 심각 이슈 0건 또는 발견 시 즉시 수정
- [ ] 회귀 누적 116건 (단위 2-9d-hotfix 기준) → 138+건 (2-9f 22건 추가) 재실행 PASS

### Step 7 — systemd 재시작 + 자연 트리거 검증
- [ ] 단일 PID 확인 → restart → 종가베팅 잡 3건 등록 로그 확인
- [ ] 다음 영업일 15:10 자연 트리거에서:
  - `[universe_filters] 속성 필터: ... ETF {etf} + 우선주 {pref} ...` 로그
  - universe에 ETF/우선주 0건
  - 시총 보강 매치율 ≥ 60% (volume_rank 자체 계산 효과)
  - universe 건수 회귀 없음 (5/7 18건 기준, 1~2건 감소 가능)
  - candidates INSERT ≥ 1건

## 변경 파일
- `closing_bet_system/collectors/universe_filters.py` (수정 — 차단 분기 + helper 3개 분리 + 시총 보강 우선순위)
- `closing_bet_system/collectors/kis_market_provider.py` (수정 — `_FIELD_LISTED_SHARES` 상수 + `_compute_market_cap_from_response` 헬퍼 + `get_top_value_data` 신규 메서드)
- `closing_bet_system/config/settings.yaml` (수정 — 토글 4종)
- `scripts/test_closing_bet_unit_2_9f.py` (신규 — 22건+ 테스트)
- `scripts/probe_etf_pref_distribution.py` (신규 — 사전 조사용, 보존)
- `docs/improvements/change_log.md` (1줄 추가)
- `memory/project_closing_bet_system.md` (단위 2-9f 단락 추가)

## 롤백 계획
1. **즉시 롤백 (운영 안전망)**: `settings.yaml` 토글 4종 false 또는 default 복귀 + systemctl restart
2. **점진 활성화 정책**:
   - **1주차**: ETF 차단만 활성화 (`etf_block_enabled=true`, `pref_stock_block_enabled=false`)
   - **2주차**: 우선주 차단 활성화 (사전 조사 결과 안정적이면)
   - **`kis_market_cap_priority` / `kis_div_cls_code`**: 사용자 결정 후 활성화
3. **롤백 트리거**: universe 건수 5/7 18건 대비 50% 이하 감소 (예: 9건 미만) / candidates 0건 / false positive 명백한 종목명 발견
4. **위험도**: 중간 (rule-based + pykrx 의존 — 종목코드 체계 + ETF 리스트 정확도 의존)

## 완료 기준
- [ ] 사전 조사 항목 (Step 0) 전부 `[x]`
- [ ] 구현 항목 (Step 1~4) 전부 `[x]`
- [ ] 단위 테스트 22건+ PASS
- [ ] 회귀 누적 138+건 PASS
- [ ] code-tester 심각 0건
- [ ] systemd 재시작 + 30분 무이상
- [ ] 다음 영업일 자연 트리거에서 ETF/우선주 0건 + 시총 보강 매치율 ≥ 60% + universe 건수 회귀 없음

## 후속 단위 (별도)
- 단위 2-9g — `kis_div_cls_code` source-level 제외 활성화 (Step 0 검증 결과 OK 시)
- 단위 2-9h — universe v2 출처 5 신규 (KIS market-cap 상위 30) 검토
- 단위 2-9i — KIS top_market_cap top_n 증가 (200 → 500) 검토
- 또는 단위 2-9 시리즈 마무리 후 Phase 2 자동매매(2-3/2-4/2-5) 진입 — 30/100건 게이트 통과 후
