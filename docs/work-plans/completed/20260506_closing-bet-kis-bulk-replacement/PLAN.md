# PLAN — 단위 2-9d · 종가베팅 universe v2 KIS Open API ranking 대체 (작업 F-2)

## 목표
단위 2-9c에서 임시로 적용한 종목별 by_date 폴백 + 시총 None 보수 탈락(옵션 A) 한계를 본격 해결한다. KIS Open API ranking 4종(거래대금/등락률/외국인 순매수/시가총액)으로 pykrx bulk 의존을 완전 제거하고, **시총 보강(옵션 B 자연 전환)**으로 단위 2-9c의 universe = 0건 한계를 풀어 candidates ≥ 5건/일 목표 달성.

## 배경
- 5/6 15:10 자연 트리거 — universe v2 = 0건/파이프라인 스킵 (KRX 사이트 정책 변경, pykrx bulk 빈 응답)
- 단위 2-9c 폴백 인프라 배포 완료 (commit d3755e9), 옵션 A 보수로 시총 None 탈락
- KIS Open API 사전 조사 완료 — 4개 ranking endpoint TR_ID/path/parameter 확정
- 본격 해결로 universe 출처 2~4 + 시총 보강을 KIS API로 통일

## 사용자 결정 사항
- 매뉴얼 확보 방식: **WebFetch로 공식 GitHub 자동** (확보 완료)
- 시총 보강: 단위 2-9d에서 **market-cap ranking 1회 호출**로 옵션 B 자연 활성화 (사용자 옵션 A 선택과 충돌 없음 — 옵션 A는 단위 2-9c 한정 임시 조치)
- 토글 위치: `settings.yaml` `data_source` 섹션 확장

## 사전 조사 결과 — 4개 ranking endpoint
| # | 기능 | TR_ID | URL Path | 핵심 파라미터 |
|---|------|-------|----------|--------------|
| 1 | 거래대금 상위 | `FHPST01710000` | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FID_BLNG_CLS_CODE=3`(거래금액), `FID_INPUT_ISCD`(시장) |
| 2 | 등락률 상위 | `FHPST01700000` | `/uapi/domestic-stock/v1/ranking/fluctuation` | `fid_rank_sort_cls_code`(정렬), `fid_input_cnt_1`(개수) |
| 3 | 외국인 순매수 상위 | `FHPTJ04400000` | `/uapi/domestic-stock/v1/quotations/foreign-institution-total` | `FID_RANK_SORT_CLS_CODE=0`(순매수), `FID_ETC_CLS_CODE=1`(외국인) |
| 4 | 시가총액 상위 | `FHPST01740000` | `/uapi/domestic-stock/v1/ranking/market-cap` | `fid_input_iscd`(시장) |

응답 컬럼 (volume_rank 검증, 다른 ranking 동일 추정):
- `mksc_shrn_iscd`(단축코드), `hts_kor_isnm`(종목명), `stck_prpr`(현재가)
- `prdy_ctrt`(등락률), `acml_vol`(거래량), `acml_tr_pbmn`(거래대금)

## 변경 파일
1. `closing_bet_system/collectors/kis_market_provider.py` (**신규**, ~350줄)
2. `closing_bet_system/collectors/universe_provider_v2.py` (수정, 출처 2~4 라우팅)
3. `closing_bet_system/collectors/universe_filters.py` (수정, 시총 보강 옵션 B 활성)
4. `closing_bet_system/config/settings.yaml` (수정, `data_source.use_kis_ranking`/`fallback_include_market_cap` 토글)
5. `scripts/test_closing_bet_unit_2_9d.py` (신규, 12+ 시나리오)
6. `docs/improvements/change_log.md` (1줄 추가)
7. `memory/project_closing_bet_system.md` (1단락 추가)

## 구현 단계

### Step 1 — `kis_market_provider.py` 신규 모듈
- 싱글톤 `KISMarketProvider` (`get_kis_market_provider()` 헬퍼)
- 기존 `KISApi._shared_token` 재사용 (토큰 추가 발급 X)
- `_rate_limit()` (`settings.KIS_API_DELAY=0.11초`) 재사용
- 모듈 상수: TR_ID 4종, URL path 4종, 응답 컬럼명, default `top_n=30`
- 메서드:
  - `get_top_value_codes(top_n=30, market="ALL") -> list[str]` (volume-rank, BLNG_CLS=3)
  - `get_top_change_codes(top_n=30, direction="up") -> list[str]` (fluctuation)
  - `get_top_foreign_buy_codes(top_n=30) -> list[str]` (foreign-institution-total, ETC_CLS=1)
  - `get_top_market_cap_data(top_n=200, market="ALL") -> dict[str, dict]` (market-cap, ticker→{market_cap, close})
- 응답 파싱: `output[*]['mksc_shrn_iscd']` 6자리 정규식 검증, `_safe_int/_safe_float` NaN 가드
- 에러 처리: `rt_cd != "0"` warn + 빈 리스트, 토큰 만료 자동 재발급

### Step 2 — `universe_provider_v2.py` 라우팅
- `_fetch_top_value_codes` / `_fetch_top_change_codes` / `_fetch_top_foreign_buy_codes` 본문에 토글 분기
  - `use_kis_ranking=True` → `kis_market_provider.get_top_*()` 호출
  - `False` → 기존 pykrx 경로 (회귀 안전)
- pykrx 경로 deprecated 주석 (1주 병렬 운영 후 제거 검토)

### Step 3 — `universe_filters.py` 시총 보강 (옵션 B 활성)
- `_fetch_per_ticker_today_data` 호출 직전 또는 별도 헬퍼:
  - `kis_market_provider.get_top_market_cap_data(top_n=200, market="ALL")` 1회 호출
  - 반환 dict 에서 시총/현재가 추출 → `data["market_cap"]` 채움
- `data_source.fallback_include_market_cap: true` 토글 (default true 시 자동 활성)
- 옵션 A 보수 탈락 분기는 **유지** (`market_cap=None` 종목은 여전히 `data_not_found`) — KIS top 200 외 종목은 시총 불명이므로 안전

### Step 4 — `settings.yaml` 갱신
```yaml
data_source:
  fallback_per_ticker_enabled: true       # 단위 2-9c (기존)
  use_kis_ranking: true                   # 단위 2-9d (신규, default true)
  fallback_include_market_cap: true       # 단위 2-9d (신규, KIS market-cap 보강)
  kis_top_n: 30                           # 출처 2~4 각 상위 N (기본 30)
  kis_market_cap_top_n: 200               # 시총 보강 상위 N (옵션 B)
```

### Step 5 — 단위 테스트 (`test_closing_bet_unit_2_9d.py`)
12+ 시나리오:
- **KP-1**: KIS volume-rank 정상 응답 → 30 종목 list 반환
- **KP-2**: KIS fluctuation 정상 응답 → 30 종목 list 반환
- **KP-3**: KIS foreign-institution-total 정상 응답 → 30 종목 list 반환
- **KP-4**: KIS market-cap 정상 응답 → ticker→{market_cap, close} dict
- **KP-5**: `rt_cd != "0"` 응답 → 빈 리스트/dict + warning
- **KP-6**: 응답 output 비-6자리 코드 혼입 → 정규식 필터링
- **KP-7**: 토큰 공유 검증 — 신규 인스턴스가 `_shared_token` 재사용
- **KP-8**: rate_limit 적용 검증 (호출 간 0.11초)
- **KP-9**: `use_kis_ranking=false` → pykrx 경로 (회귀)
- **KP-10**: `use_kis_ranking=true` → KIS 라우팅 + universe_provider_v2 통합
- **KP-11**: `fallback_include_market_cap=true` → universe_filters 시총 보강
- **KP-12**: 회귀 — KIS 호출 RuntimeError → graceful 폴백 (pykrx 또는 빈 리스트)

### Step 6 — 통합 검증 + 회귀
- `get_universe_v2_filtered()` 단발 호출 → universe ≥ 5건 + KIS API 호출 로그
- 회귀: 2-9a 18 + 2-9b 15 + 2-9c 10 + 2-2b-1 15 + 2-2b-2 10 = 68건 PASS

### Step 7 — code-tester 검증

### Step 8 — 배포 + 5/7 자연 트리거 검증

### Step 9 — 문서 갱신 + 커밋

## 위험 / 롤백

### 위험
1. **응답 컬럼명 추정**: volume_rank 외 3개 ranking의 응답 컬럼은 GitHub 코드만으로 부분 확정. 실 호출 시 KeyError 가능 → try/except + 컬럼 fallback 패턴 필수
2. **모의/실전 도메인 차이**: ranking TR_ID가 모의에서 미지원 가능성 → `is_mock=True` 시 즉시 폴백 + warning
3. **KIS API 일일 한도**: 추가 ~50회/일 + 시총 보강 1회 = ~51회. 현 사용량(1,200회/일) + 50 = 1,250회/일. 한도(추정 10,000+회) 대비 여유

### 롤백
1. `settings.yaml.data_source.use_kis_ranking: false` + `fallback_include_market_cap: false` + systemctl restart
2. → pykrx 경로 + 옵션 A 보수 (단위 2-9c 동작) 복귀
3. 코드 자체는 graceful 폴백 (회귀 위험 없음)

## 토글 / 안전망
- `data_source.use_kis_ranking` (default true) — 출처 2~4 KIS 라우팅
- `data_source.fallback_include_market_cap` (default true) — universe_filters 시총 보강
- Phase 1 알림형 안전망 유지

## 완료 기준
- 단위 12+건 PASS
- 회귀 78+건 PASS (2-9a 18 + 2-9b 15 + 2-9c 10 + 2-2b-1 15 + 2-2b-2 10 + 신규 12+)
- code-tester 심각 0건
- 통합 단발 — universe ≥ 5건 + KIS API 호출 로그 + 시총 채워짐
- 5/7 15:10 자연 트리거 — universe ≥ 5건 + candidates INSERT ≥ 1건 + 일일 요약 알림
- KIS API 호출 시간 ≤ 5초 (4 ranking 호출 합계)

## change_log.md 1줄 (배포 후 추가)
형식: PLAN.md 본문에 정의된 항목 활용 — 단위 2-9d 본격 KIS API 대체.
