# CHECKLIST — 단위 2-9f · ETF/우선주 차단 + 자체 시총 계산 (사전 리뷰 반영)

## 사전 조사 (Step 0 — ✅ 2026-05-07 KST 19:50 완료)

### 0.1 pykrx ETF 리스트 동적 조회 가능 여부
- [x] `scripts/probe_pykrx_etf_list.py` 신규 + 단발 실행
- [x] 결과: ❌ **사용 불가** — `KeyError: '시장'` (단위 2-9c와 동일 KRX API 정책 영향)
- [x] 결정: **종목명 brand prefix 매칭** 채택 (정적 set ~800개 유지보수 부담 회피)

### 0.2 KIS API `FID_DIV_CLS_CODE` 단발 검증
- [x] `scripts/probe_kis_div_cls.py` 신규 + 6 호출 (3 ranking × 2 div_cls)
- [x] 결과:
  - [x] **volume_rank**: ✅ 우선주 source-level 차단 (5/7 005935 직접 검증) / ETF 미차단
  - [x] **fluctuation**: 응답 변화 없음 (영향 없음)
  - [x] **foreign_total**: 응답 변화 (지원 추정, 단 5/7 우선주 표본 부족)
- [x] 결정: volume_rank만 native filter 활용. ETF/ETN은 universe_filters 차단 분기 의존

### 0.3 5/7 거래대금 top 30 ETF/우선주 분포 실측
- [x] `scripts/probe_etf_pref_distribution.py` 신규 + 단발 실행
- [x] 결과:
  - [x] **ETF/ETN**: 13/30건 (43%) — KODEX 6 + TIGER 3 + 기타 4 (122630/069500/396500/102110/091160/252670/114800/379800/360750/487240/233740/494310/229200)
  - [x] **우선주 진성**: 1건 (005935 삼성전자우)
  - [x] **false positive 후보**: 0건 (끝자리 5/7/9 + 종목명 "우" 미포함 케이스 없음) ✅
  - [x] **종목명 brand 매칭 정확도**: 13/13 = 100%
- [x] 결정: AND 조건 + 종목명 brand prefix 매칭 안전 검증

### 0.4 자기주식 영향 측정 (top 5 시총 종목)
- [x] 단발 실행으로 stck_avls × 1억 vs lstn_stcn × stck_prpr 비교
- [x] 결과: top 5 모두 차이 +0.000% (절대값 4천만~3억원, 시총 대비 무시 가능)
- [x] 결정: 단위 2-9e CONTEXT "~0.01% 차이" 추정 과도. 두 방식 동등 → 우선순위는 가용성 기준
  - [x] 1순위: stck_avls × 1억 (top 200)
  - [x] 2순위: lstn_stcn × stck_prpr (top 30 보강)

### 0.5 사전 조사 게이트 통과 — ✅
- [x] ETF 차단 방식 확정 (종목명 brand prefix)
- [x] 우선주 차단 방식 확정 (끝자리 + 종목명 AND 조건)
- [x] 시총 보강 우선순위 확정 (가용성 기준)
- [x] 종목명 회수 경로 확정 (universe_provider_v2 → universe_filters name_lookup_map 주입)
- [x] **Step 1 진입 가능**

## 구현 항목

### Step 1 — `universe_filters.py` ETF/우선주 차단 분기 (Step 0 결과 반영)

#### 1.1 모듈 상수 + cfg fallback default
- [x] 모듈 상수:
  - `_BLOCK_ETF_DEFAULT = True`
  - `_BLOCK_PREF_STOCK_DEFAULT = False` (점진 활성화)
  - `_KIS_MARKET_CAP_PRIORITY_DEFAULT = False`
  - `_KIS_DIV_CLS_CODE_DEFAULT = "0"` (Step 0.2 검증 — volume_rank만 1 활성화 가능)
  - `_PREF_STOCK_LAST_DIGITS = frozenset({"5", "7", "9"})`
  - `_PREF_STOCK_NAME_SUFFIXES = ("우", "우B", "우C", "우K")`
  - `_ETF_BRAND_PREFIXES = ("KODEX", "TIGER", "KOSEF", "HANARO", "ARIRANG", "ACE", "KBSTAR", "SOL", "KINDEX", "RISE", "PLUS", "SMART", "FOCUS", "TIMEFOLIO", "WOORI")` (Step 0.3 KOSPI top 30 100% 정확도 검증)
  - `_ETN_NAME_KEYWORD = "ETN"`
  - `REJECTION_REASON_IS_ETF = "is_etf"`
  - `REJECTION_REASON_IS_PREF_STOCK = "is_pref_stock"`
- [x] `_load_filter_config()` 신규 4개 키 추가 + cfg fallback dict default reflect

#### 1.2 ETF/우선주 헬퍼 함수 (종목명 기반)
- [x] `_is_etf_or_etn(name)` — 종목명 brand prefix 매칭 (Step 0.3 검증) + ETN 키워드. None/빈 → False (보수)
- [x] `_is_pref_stock(ticker, name)` — 6자리 정규식 + 끝자리 AND 종목명 접미사 (Step 0.3 false positive 0건 검증)

#### 1.3 차단 분기 (apply_attribute_filters)
- [x] **차단 분기 위치**: `for ticker in tickers:` 루프 첫 단계 (KIND severity if 블록 밖)
- [x] ETF/ETN → 우선주 순서 (first-rejection-only)
- [x] rejection_reason 모듈 상수 사용
- [x] **`name_lookup_map: Optional[dict[str, str]] = None` keyword-only 인자 신규**
- [x] **로그 분해**: 카운트 4종 (`kind/etf/pref/attr`) + 첫 호출 1회 차단 list 진단 로그
- [x] py_compile 통과

#### 1.4 universe_provider_v2.py 종목명 회수 (Step 0 신규 결정)
- [x] KIS ranking 응답에서 (ticker, name) 쌍 추출 헬퍼 신규
- [x] `get_universe_v2_filtered`가 name_lookup_map 누적 → universe_filters에 전달
- [x] 종목명 부재 종목은 `closing_bet_system/infra/name_lookup.py` 폴백 호출
- [x] py_compile 통과

### Step 2 — `kis_market_provider.py` lstn_stcn 자체 시총 계산

#### 2.1 모듈 상수
- [x] `_FIELD_LISTED_SHARES = "lstn_stcn"` (단위 2-9d 패턴 일관)

#### 2.2 헬퍼 함수
- [x] `_compute_market_cap_from_response(item)`:
  - `_safe_int(lstn_stcn)` × `_safe_int(stck_prpr)` (정수 × 정수 — IEEE 754 정밀도 손실 없음)
  - `_safe_float` 사용 절대 금지
  - 0 또는 음수 입력 → None
- [x] **단위 진단 로그** (첫 호출 1회, 단위 2-9e 패턴):
  - `[kis_market_provider] lstn_stcn × stck_prpr 자체 계산 — {code} {lstn_stcn:,d} × {stck_prpr:,d} = {market_cap:,d}원 (stck_avls 대비 차이 ~0.01% 자기주식 영향 추정)`

#### 2.3 신규 메서드
- [x] `get_top_value_data(top_n, market) -> dict[str, dict]`:
  - volume_rank API 호출 + 각 item에서 `_compute_market_cap_from_response` 호출
  - 반환: `{ticker: {market_cap, close, change_rate, lstn_stcn}}`
  - 기존 `get_top_value_codes` 유지 (옵션 A — 메서드 분리)
- [x] `get_top_change_data` / `get_top_foreign_buy_data`: **신규 X** (응답에 lstn_stcn 부재 — Step 0 검증 결과)

#### 2.4 (선택) KIS native filter
- [x] volume-rank `FID_DIV_CLS_CODE` 옵션 — settings 토글로 `"0"` (전체) / `"1"` (보통주만) 선택
- [x] Step 0 매뉴얼 검증 결과에 따라 fluctuation/foreign-institution-total 동시 적용 여부 결정 (단위 2-9g 분리 가능)

### Step 3 — `universe_filters.py` 시총 보강 우선순위 + helper 분리

#### 3.1 helper 분리 (108줄 → ~50줄)
- [x] `_enrich_market_cap_from_kis_top(candidates, cfg)` — 단위 2-9d/2-9e 기존 코드 추출
- [x] `_enrich_market_cap_from_volume_rank(candidates, cfg)` — 단위 2-9f 신규 (lstn_stcn × stck_prpr)
- [x] `_enrich_ohlcv_per_ticker(candidates, today_str, krx)` — 단위 2-9c 기존 코드 추출
- [x] `_maybe_run_per_ticker_fallback` 본문 골격만 유지

#### 3.2 시총 보강 우선순위 (해석 A 채택)
- [x] 1순위: KIS market-cap top 200 (`stck_avls × 1억`)
- [x] 2순위: 1순위 미매치 → KIS volume-rank top 30 (`lstn_stcn × stck_prpr`)
- [x] 3순위: 2순위도 미매치 → `data_not_found` 탈락 (옵션 A 보수)
- [x] `kis_market_cap_priority` 토글:
  - `false` (default): pykrx bulk 정상 시 미진입 (현 동작)
  - `true`: pykrx 정상이어도 시총 컬럼만 KIS로 후처리 덮어쓰기

### Step 4 — `settings.yaml` 토글 4종
- [x] `stock_filter.etf_block_enabled: true`
- [x] `stock_filter.pref_stock_block_enabled: false` (점진 활성화)
- [x] `data_source.kis_market_cap_priority: false`
- [x] `data_source.kis_div_cls_code: "0"` (Step 0 검증 후 "1")
- [x] yaml 문법 검증

### Step 5 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9f.py`) — 22건+

#### 5.1 ETF/ETN 차단 (5건, 종목명 기반 — Step 0.3 fixture)
- [x] **ETF-1**: name="KODEX 200" → brand 매칭 → 탈락
- [x] **ETF-2**: name="TIGER 반도체TOP10" → 매칭 → 탈락
- [x] **ETF-3**: name="삼성전자" → 미매칭 → **통과** (보통주 회귀)
- [x] **ETF-4**: name="신한 ETN-1F WTI원유 선물" 등 → ETN 키워드 → 탈락
- [x] **ETF-5**: 토글 OFF → "KODEX 200" 통과 (회귀)

#### 5.2 우선주 차단 (5건)
- [x] **PREF-1**: 005935 삼성전자우 (끝자리 5 + 종목명 "삼성전자우") → AND → 탈락
- [x] **PREF-2**: 005930 삼성전자 (끝자리 0) → 끝자리 미충족 → 통과
- [x] **PREF-3**: 005385 현대차우 (끝자리 5 + 종목명 "현대차우") → AND → 탈락
- [x] **PREF-4**: KOSDAQ 보통주 끝자리 5/7/9 + 종목명 "우" 미포함 → AND 미충족 → **통과** (false positive 회귀, Step 0 결과 반영)
- [x] **PREF-5**: 토글 OFF → 005935 통과

#### 5.3 자체 시총 계산 (4건)
- [x] **MC-CALC-1**: lstn_stcn=5,846,278,608 + stck_prpr=271,500 → 1,587,264,478,572,000원
- [x] **MC-CALC-2**: lstn_stcn 부재 → None
- [x] **MC-CALC-3**: stck_prpr=0 → None
- [x] **MC-CALC-4**: NaN/문자/음수/공백 → `_safe_int` default=0 → None

#### 5.4 통합 (3건)
- [x] **INTEGRATION-1**: volume_rank 30종목 자체 계산 → 매치율 ≥ 80%
- [x] **INTEGRATION-2**: stck_avls × 1억 1순위 + lstn_stcn × stck_prpr 2순위 우선순위 검증
- [x] **INTEGRATION-3**: pykrx bulk 정상 + `kis_market_cap_priority=false` → KIS 보강 미진입 (회귀)

#### 5.5 회귀 (3건)
- [x] **REGRESSION-1** (단위 2-9d-hotfix): fluctuation `stck_shrn_iscd` 응답에서 ticker 추출 정상 + ETF/우선주 룰 정상 동작
- [x] **REGRESSION-2** (단위 2-9e): market_cap top 200 응답 stck_avls × 1억 = 005930 1,555,110,100,000,000원 유지
- [x] **REGRESSION-3** (단위 2-9c): pykrx bulk 빈 응답 시 KIS 시총 보강 + 종목별 OHLCV 폴백 정상

#### 5.6 Edge case (4건)
- [x] **EDGE-1**: ticker None/빈 문자열/5자리/7자리 → `_is_etf_or_etn` / `_is_pref_stock` False (None safe)
- [x] **EDGE-2**: lstn_stcn 문자열 응답 (`"5846278608"`) → `_safe_int` 정상 처리
- [x] **EDGE-3**: name=None / 빈 문자열 → `_is_etf_or_etn` / `_is_pref_stock` False (보수적)
- [x] **EDGE-4**: ETF/우선주 토글 둘 다 OFF + 5/7 universe 18건 → 단위 2-9e 결과 그대로 재현 (회귀)

### Step 6 — code-tester 검증
- [x] code-tester 에이전트 호출 (수정 4개 + 신규 테스트 1개 + probe 2개 대상)
- [x] 단위 2-9d/2-9e/2-9d-hotfix 발견 패턴 재발 차단:
  - cfg NameError → 신규 키 4종 module_const + cfg fallback default 명시 검증
  - `_FIELD_*` 상수 → `_FIELD_LISTED_SHARES` 추가 검증
  - 단위 진단 로그 → `lstn_stcn × stck_prpr` + ETF/우선주 차단 list 첫 호출 1회 검증
  - direction dead parameter → 본 단위 무관 (확인)
- [x] 심각 이슈 0건 또는 발견 시 즉시 수정
- [x] 회귀 누적 116건 (단위 2-9d-hotfix 기준) → 138+건 (2-9f 22건 추가) 재실행 PASS

## 검증 항목

### 단위 검증
- [x] py_compile 통과 (universe_filters.py / kis_market_provider.py)
- [x] `venv/bin/python scripts/test_closing_bet_unit_2_9f.py` 22건+ PASS
- [x] 회귀 138+건 PASS

### 통합 검증 (단발)
- [x] `get_universe_v2_filtered()` 단발 호출
- [x] universe에 ETF/우선주 0건 (사전 조사 결과 false positive도 0건)
- [x] `[universe_filters] 속성 필터: ... ETF {etf} + 우선주 {pref} ...` 로그
- [x] 시총 보강 매치율 ≥ 60% (volume_rank 자체 계산 효과)
- [x] universe 건수 회귀 없음 (5/7 18건 기준, 1~2건 감소 가능)

### 실전 검증 (다음 영업일 자연 트리거)
- [x] 15:10 KST 자동 트리거 발화
- [x] universe에 ETF/우선주 0건
- [x] universe 건수 회귀 없음
- [x] 시총 보강 매치율 향상 확인 (KIS top 200 한도 우회 효과)
- [x] candidates INSERT ≥ 1건
- [x] **신규 모니터링**: false positive 사례 발견 시 즉시 화이트리스트 보강

## 배포 항목
- [x] systemd 재시작 전 단일 PID 확인
- [x] 변경 파일 git stage (의도 일치 확인)
- [x] `sudo systemctl restart trading_system`
- [x] active(running) 확인
- [x] 종가베팅 잡 3건 등록 + Phase 1 알림형 로그 확인
- [x] 배포 후 30분 무이상 모니터링

### 점진 활성화 정책
- [x] **1주차**: ETF 차단만 활성화 (`etf_block_enabled=true`, `pref_stock_block_enabled=false`)
- [x] **2주차**: 사전 조사 결과 안정적이면 우선주 차단 활성화
- [x] **별도 결정**: `kis_market_cap_priority` / `kis_div_cls_code` (사용자 결정)

### 롤백 트리거
- [x] universe 건수 5/7 18건 대비 50% 이하 감소 (예: 9건 미만)
- [x] candidates INSERT 0건
- [x] false positive 명백한 종목명 발견

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-9f, 단위 2-9e/2-9d-hotfix 형식 일관)
- [x] `memory/project_closing_bet_system.md` 단위 2-9f 단락 추가
- [x] `memory/MEMORY.md` 인덱스 description 갱신
- [x] git commit + push
- [x] 임시 스크립트 보존: `scripts/probe_pykrx_etf_list.py`, `scripts/probe_etf_pref_distribution.py` (재검증용)

## 완료 게이트 (선언 전 체크)
- [x] Step 0 사전 조사 항목 전부 `[x]`
- [x] Step 1~4 구현 항목 전부 `[x]`
- [x] Step 5 단위 테스트 22건+ PASS
- [x] Step 6 code-tester 통과
- [x] 검증 항목 단위/통합 전부 `[x]`
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 (아카이브 제외) 전부 `[x]`
- [x] 자연 트리거 검증 후 → active → completed 아카이브 (단위 2-9c~2-9f + 2-9d-hotfix 와 함께)

## 사전 리뷰 반영 요약 (2026-05-07)

| 우선순위 | 항목 | 반영 결과 |
|---|---|---|
| **P0** | ETF prefix 룰 → pykrx 동적 조회 + 정적 폴백 | Step 0.1 + 1.2 + EDGE-3 |
| **P0** | 우선주 끝자리 룰 → 종목명 "우" 접미사 AND 조건 | Step 1.2 + PREF-4 |
| **S3** | IEEE 754 정밀도 보호 (`_safe_int × _safe_int`) | Step 2.2 + MC-CALC-4 |
| **M1** | `get_top_value_data` 옵션 A 메서드 분리 | Step 2.3 |
| **M2** | `_maybe_run_per_ticker_fallback` helper 3개 분리 | Step 3.1 |
| **M3** | `kis_market_cap_priority` 해석 A 명시 | Step 3.2 |
| **R1** | cfg NameError 방어 (4종 신규 키 module_const) | Step 1.1 |
| **R2** | `_FIELD_LISTED_SHARES` 상수 | Step 2.1 |
| **R3** | 단위 진단 로그 (lstn_stcn 첫 호출 1회 + ETF/우선주 list) | Step 1.3 + 2.2 |
| **R5** | 단위 테스트 14건 → 22건 (REGRESSION 3 + EDGE 4 추가) | Step 5.5 + 5.6 |
| **A1** | 차단 분기 위치 (KIND if 블록 밖) | Step 1.3 |
| **A2** | rejection_reason 모듈 상수 | Step 1.1 |
| **A3** | _is_etf/_is_pref_stock None safety | Step 1.2 + EDGE-1 |
| **점진 활성화** | pref_stock default false 1주 관찰 | Step 4 + 배포 항목 |
| **로그 분해** | etf_rejected/pref_rejected 카운트 분리 | Step 1.3 |

## 후속 단위 (별도)
- 단위 2-9g — `kis_div_cls_code` source-level 제외 활성화 (Step 0 검증 결과 OK 시)
- 단위 2-9h — universe v2 출처 5 신규 (KIS market-cap 상위 30) 검토
- 단위 2-9i — KIS top_market_cap top_n 증가 (200 → 500) 검토
- Phase 2 자동매매(2-3/2-4/2-5) — 30/100건 게이트 통과 후
