# PLAN — 단위 2-9e · 종가베팅 universe v2 시총 보강 정규화 (옵션 A)

## 목표
KIS Open API `ranking/market-cap` 응답의 `stck_avls` 컬럼을 **× 100,000,000 (억원→원)** 으로 정규화하여 시총 보강을 활성화한다. 단위 2-9d 후 OFF 상태인 `fallback_include_market_cap` 토글을 ON 으로 전환해 옵션 A 보수 탈락(`data_not_found`) 대상 종목 다수를 universe 에 진입시킨다. **universe ≥ 5건/일** 목표 달성.

## 배경
- 단위 2-9d 후 통합 단발 검증: theme=17 / top_value=25 / top_foreign=21 = **63종목** 확보, 그러나 시총 미보강으로 옵션 A 보수 탈락 (`universe = 0건`)
- CONTEXT.md `다음 세션 가이드` 2순위 — 단위 2-9e 시총 보강 정규화
- 단위 2-9e 사전 조사 (이번 세션 — `scripts/probe_kis_unit_2_9e.py`) 완료:
  - **`stck_avls` 단위 = 억원** 확정
  - 증거: `005930 stck_avls=15,551,101 × 100,000,000 = 1,555,110,100,000,000원` ↔ `stck_prpr=266,000원 × lstn_stcn=5,846,278,608주 = 1,554,909,948,728,000원` ↔ `inquire-price market_cap = 1,555,110,100,000,000원` (3값 정확 일치)

## 사전 조사 결과 요약

### KIS `ranking/market-cap` 응답 11개 키 확정
`mksc_shrn_iscd`, `data_rank`, `hts_kor_isnm`, `stck_prpr`, `prdy_vrss`, `prdy_vrss_sign`, `prdy_ctrt`, `acml_vol`, `lstn_stcn`, `stck_avls`(억원), `mrkt_whol_avls_rlim`(시장전체비중%)

### 단위 검증 (5종목)
| 종목 | stck_avls (응답) | × 1억 (원) | inquire-price (정답) | 일치 |
|------|------------------|------------|----------------------|------|
| 005930 삼성전자 | 15,551,101 | 1,555조원 | 1,555조원 | ✅ |
| 000660 SK하이닉스 | 11,410,365 | 1,141조원 | 1,141조원 | ✅ |
| 005935 삼성전자우 | 1,518,889 | 152조원 | — | (참고) |
| 005380 현대차 | 1,126,168 | 112.6조원 | 112.6조원 | ✅ |
| 035420 NAVER | (top10 외) | — | 32.6조원 | — |

→ stck_avls × 100,000,000 = 원 단위 시총 (정확)

### 사각지대 확인 — 옵션 A 한계
- `ranking/market-cap` 호출 1회 → 상위 200종목만 시총 dict 반환
- universe 출처 2~4 중 **상위 200 외 중소형주**(예: 등락률 상위 일부)는 시총 보강 불가 → 여전히 옵션 A 보수 탈락
- 1주 실전 관찰 후 universe 5건 미달 빈발 시 단위 2-9f (옵션 C: `lstn_stcn × stck_prpr` 자체 계산) 추가 검토

## 변경 파일
1. `closing_bet_system/collectors/kis_market_provider.py` (수정, ~6줄 + 주석/진단 로그 갱신)
2. `closing_bet_system/collectors/universe_filters.py` (수정, DEFAULT_FALLBACK_INCLUDE_MARKET_CAP True→False 안전망 정합)
3. `closing_bet_system/config/settings.yaml` (수정, 1줄: `fallback_include_market_cap: true`)
4. `scripts/test_closing_bet_unit_2_9e.py` (신규, 11건 시나리오)
5. `docs/improvements/change_log.md` (1줄 추가)
6. `memory/project_closing_bet_system.md` (1단락 추가)
7. `scripts/probe_kis_unit_2_9e.py` (**보존 — 사용자 확정 결정**)

## 구현 단계

### Step 1 — `kis_market_provider.py` 단위 정규화
- `get_top_market_cap_data` (라인 203-263) 수정:
  - 기존: `entry["market_cap"] = mcap` (raw stck_avls, 억원 단위)
  - 변경: `entry["market_cap"] = int(round(mcap * _STCK_AVLS_UNIT_TO_WON))` (원 단위, IEEE 754 정밀도 보호 위해 round 적용)
- 모듈 상수 추가: `_STCK_AVLS_UNIT_TO_WON = 100_000_000` (억원 → 원)
- 인라인 주석 갱신 (라인 239-244): "단위 미확정" → "단위 = 억원 (단위 2-9e 확정), × 1억 정규화 적용"
- 진단 로그 갱신 (라인 247-253): "원 단위 가정. min_market_cap=5,000억과 비교" → "단위 2-9e 확정: 억원 × 1억 정규화. min_market_cap=500억과 비교"

### Step 2 — `universe_filters.py` DEFAULT 값 정합 (안전망 보강)
- 라인 86: `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = True` → `False`
- 이유: settings.yaml 의도(false→true)와 default 가 정반대였음. settings 로드 실패 시 cfg fallback 분기에서 의도와 반대로 활성화 위험 봉쇄
- 단위 2-9e 활성 후에는 settings ON 이라 무관, 향후 롤백 시 안전 보장

### Step 3 — `settings.yaml` 토글 활성화
```yaml
data_source:
  fallback_per_ticker_enabled: true       # 단위 2-9c
  use_kis_ranking: true                   # 단위 2-9d
  fallback_include_market_cap: true       # 단위 2-9e (false → true)
  kis_top_n: 30
  kis_market_cap_top_n: 200
```
- 주석에 단위 2-9e 활성 배경 + 사전 조사 결과 (5종목 단위 확정) 추가

### Step 4 — 단위 테스트 작성 (`scripts/test_closing_bet_unit_2_9e.py`) — 11건
- **MC-1**: stck_avls=15551101 → market_cap=1,555,110,100,000,000원 (× 1억 정규화)
- **MC-1b**: `isinstance(entry["market_cap"], int)` 타입 검증 (round 후 int 캐스팅 보장)
- **MC-2**: stck_avls=0 → entry["market_cap"] 키 미생성 (mcap > 0 가드)
- **MC-3**: 응답 전체에 비-6자리 코드 혼입 → 정규식 필터링 정상
- **MC-4**: `fallback_include_market_cap=true` → universe_filters 시총 보강 적용 (mock)
- **MC-5**: 시총 보강 후 옵션 A 보수 탈락 종목 ≥ 1건 (상위 200 외) — `data_not_found` 유지
- **MC-6a**: 정규화 적용 → market_cap=60,000,000,000원 (600억) → `min_market_cap=50_000_000_000` (500억) 임계 통과 PASS
- **MC-6b** (회귀): 정규화 누락 시뮬 → market_cap=600 → 500억 미달 FAIL (정규화 누락 회귀 검출)
- **MC-7** (회귀): `fallback_include_market_cap=false` → 옵션 A 보수 그대로 (단위 2-9d 동작)
- **MC-7b** (안전망): `_load_filter_config` 예외 → `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=False` 분기 (Step 2 변경 검증)
- **MC-8**: NaN/inf/빈 문자열 시총 응답 → `_safe_float` None 반환 → entry["market_cap"] 키 미생성

### Step 5 — code-tester 검증
- 수정 파일 3개 + 테스트 1개 대상으로 code-tester 에이전트 호출
- 단위 2-9d 발견 패턴 (cfg NameError, direction 인자, _FIELD_CHANGE_RATE 상수, 단위 진단 로그) 재발 차단 확인
- 심각 이슈 0건 확인 후 다음 단계

### Step 6 — 통합 검증 + 회귀
- 통합 단발: `get_universe_v2_filtered()` 호출 → universe ≥ 1건 (단발 검증) + 시총 보강 로그 + 일부 상위 200 외 종목 옵션 A 보수 탈락 로그
- 회귀: 2-9a 18 + 2-9b 15 + 2-9c 10 + 2-9d 12 + 2-9e 11 + 2-2b-1 15 + 2-2b-2 10 = **91건 PASS**

### Step 7 — 배포
- 단일 PID 확인 (`pgrep -f main.py | wc -l`)
- `sudo systemctl restart trading_system`
- active(running) + 종가베팅 잡 3건 등록 + Phase 1 알림형 로그 확인
- 배포 후 30분 `journalctl -u trading_system -f` 무이상 모니터링

### Step 8 — 5/7 (또는 다음 영업일) 자연 트리거 검증
**시점**: 5/7 (목) 15:10 KST 이후

**검증 항목**:
- `[universe_v2] 출처별 기여` — top_value/top_change/top_foreign 모두 비-0
- `[universe_filters] 시총 보강` 로그 — KIS market-cap 호출 + 매치 종목 수 ≥ 30 + 미매치 탈락 카운트
- `[kis_market_provider] stck_avls 단위 = 억원 (단위 2-9e 확정)` 로그 1회
- KIS API 호출 시간 ≤ 6초 (4 ranking + 1 market-cap)
- universe ≥ 5건 (목표 달성)
- candidates INSERT ≥ 1건 + 일일 요약 텔레그램 알림 (15:35)
- **신규**: pykrx bulk 정상 복구 여부 점검 (시총 보강 로그 미발생 시 의심 — 단위 2-9d 게이트 의존성)
- **신규**: universe 에 ETF(069500 등)/우선주(005935 등) 0건 확인 (단위 2-9f 트리거 신호)

### Step 9 — 문서 갱신 + 커밋 + 아카이브
- `docs/improvements/change_log.md` 1줄 추가
- `memory/project_closing_bet_system.md` 단위 2-9e 1단락 추가
- `memory/MEMORY.md` 인덱스 description 갱신
- git commit + push
- 자연 트리거 검증 후 active → completed 아카이브 (단위 2-9c/2-9d/2-9e 일괄)

## 위험 / 롤백

### 위험
1. **상위 200 외 종목 보수 탈락 잔존**: 등락률 상위 30종 중 중소형주 다수 포함 시 universe 5건 미달 가능 → 단위 2-9f (옵션 C) 추가 검토 트리거
2. **단위 정규화 오류 도입**: 잘못된 곱셈 → 시총 잘못 비교 → universe 0건 또는 부적절 종목 진입. MC-1/MC-6a/MC-6b 단위 테스트로 양방향 차단 (정규화 PASS + 누락 회귀 FAIL)
3. **부동소수점 절단 위험**: `_safe_float` 가 float 반환 후 곱셈 → IEEE 754 정밀도 한계로 경계값(500억 근처) 절단 누락 → `int(round(...))` 적용으로 봉쇄
4. **DEFAULT 값 안전망 역동작**: settings 로드 실패 시 cfg fallback 분기에서 의도와 반대로 활성화 위험. Step 2 에서 `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=False` 변경으로 봉쇄 + MC-7b 검증
5. **pykrx bulk 게이트 의존성**: 시총 보강은 bulk 빈 응답 시에만 트리거 (단위 2-9c/2-9d 설계). KRX 정책 풀려 bulk 정상 복구되면 시총 보강 자동 OFF → universe 회귀. 단위 2-9f 영역에서 분기 자체 변경 검토. 자연 트리거 시 시총 보강 로그 미발생 시 의심
6. **ETF/우선주 universe 진입 가능성**: ranking/market-cap 응답에 ETF(069500)/우선주(005935) 포함, 현재 universe_filters 에 명시적 차단 없음. 단위 2-9f 별도 처리 (사용자 결정). Phase 1 알림형이라 자동매수 위험 0
7. **회귀 안전 토글**: `fallback_include_market_cap=false` 즉시 OFF

### 롤백
1. `settings.yaml.data_source.fallback_include_market_cap: false` + systemctl restart
2. → 단위 2-9d 동작 (옵션 A 보수 탈락) 복귀
3. 코드 자체는 토글 분기 (회귀 위험 없음)

## 토글 / 안전망
- `data_source.fallback_include_market_cap` (단위 2-9e default true 전환) — universe_filters 시총 보강
- 옵션 B 는 본격 활성: KIS market-cap 1회 호출로 상위 200종목 시총 dict 보강
- Phase 1 알림형 (자동매수 X) 안전망 유지

## 완료 기준
- 단위 11건 PASS (MC-1 ~ MC-8 + 보강 1b/6a/6b/7b)
- 회귀 91건 PASS (2-9a 18 + 2-9b 15 + 2-9c 10 + 2-9d 12 + 2-9e 11 + 2-2b-1 15 + 2-2b-2 10)
- code-tester 심각 0건
- 통합 단발: universe ≥ 1건 (단발 검증, 5건은 자연 트리거 의존)
- 5/7 자연 트리거: universe ≥ 5건 + 일일 요약 알림 + ETF/우선주 0건
- KIS API 호출 시간 ≤ 6초
- change_log.md 1줄 추가

## change_log.md 1줄 (배포 후 추가)
형식: `2026-05-XX | 단위 2-9e | 종가베팅 universe v2 시총 보강 정규화 (stck_avls × 1억) — fallback_include_market_cap=true 활성, universe 0→5+건 목표`

## 후속 단위 (옵션, 1주 관찰 후 결정)
**단위 2-9f — 옵션 C `lstn_stcn × stck_prpr` 자체 계산**:
- ranking 4종 모든 응답에서 발행주식수 × 현재가 = 시총 자체 산출
- 상위 200 한도 없음 — 모든 universe 종목 시총 보강 가능
- 우선주(005935) / ETF(069500) 분기 처리 필요
- universe 5건 미달 빈발 시에만 진입
