# CONTEXT — 단위 2-9d · KIS Open API ranking 대체

## 변경 이유
단위 2-9c (commit d3755e9) 배포 후 통합 단발 검증에서 universe = 0건 (옵션 A 보수 탈락). 본질 해결을 위해 KIS Open API의 ranking 4종으로 pykrx bulk 완전 대체. 시총 보강도 KIS market-cap 1회 호출로 옵션 B 자연 활성화.

## 사전 조사 결과 (KIS Open API 매뉴얼)

### 출처별 endpoint 확정
| 출처 | TR_ID | URL Path |
|------|-------|----------|
| 거래대금 상위 | `FHPST01710000` | `/uapi/domestic-stock/v1/quotations/volume-rank` |
| 등락률 상위 | `FHPST01700000` | `/uapi/domestic-stock/v1/ranking/fluctuation` |
| 외국인 순매수 상위 | `FHPTJ04400000` | `/uapi/domestic-stock/v1/quotations/foreign-institution-total` |
| 시가총액 상위 | `FHPST01740000` | `/uapi/domestic-stock/v1/ranking/market-cap` |

### 응답 컬럼 (volume_rank 검증, 다른 ranking 동일 추정)
- `mksc_shrn_iscd` — 단축종목코드 (6자리)
- `hts_kor_isnm` — 종목명 (HTS 한글)
- `stck_prpr` — 현재가
- `prdy_ctrt` — 등락률 (%)
- `acml_vol` — 누적거래량
- `acml_tr_pbmn` — 누적거래대금
- `stck_avls` — 시가총액 (market-cap ranking 추정)

### Sources
- [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api)
- [KIS Developers 공식 포탈](https://apiportal.koreainvestment.com/apiservice-apiservice)

## 현재 코드 상태

### `closing_bet_system/collectors/universe_provider_v2.py`
- 라인 296-324: `_fetch_top_value_codes(today_str, top_n=30)` — pykrx `get_market_ohlcv_by_ticker` 사용 (KRX 차단)
- 라인 327-357: `_fetch_top_change_codes(today_str, top_n=30)` — pykrx `get_market_price_change_by_ticker` (KRX 차단)
- 라인 360-397: `_fetch_top_foreign_buy_codes(today_str, top_n=30)` — pykrx `get_market_net_purchases_of_equities_by_ticker` (KRX 차단)
- 라인 240-256: `_safe_call` 헬퍼 (try/except 격리)

### `closing_bet_system/collectors/universe_filters.py` (단위 2-9c 후)
- 라인 168-277: `_fetch_market_data_bulk(today_str, tickers=None)` — pykrx bulk + 폴백 분기
- 라인 280-322: `_fetch_per_ticker_today_data(ticker, today_str, krx)` — 폴백 헬퍼 (시총 안 채움 — 옵션 A)
- 라인 360-403: `_maybe_run_per_ticker_fallback` — 폴백 진입 가드
- 시총 보강 위치: 폴백 진입 직전 또는 헬퍼 내부에 KIS market-cap 호출 추가 가능

### `modules/stock_screener/kis_api.py` (재사용 패턴)
- 라인 76-78: `_shared_token` / `_shared_token_expired_at` 클래스 변수
- 라인 112-115: 도메인 분기 (모의 `openapivts:29443` / 실전 `openapi:9443`)
- 라인 129-131: `_rate_limit()` (`settings.KIS_API_DELAY=0.11초`)
- 라인 133-180: `get_access_token()` — 토큰 발급 + 1시간 전 갱신 + 1분 발급 제한
- 라인 422+: `get_current_price` — `output[0]` 단건 응답 파싱 (`stck_prpr` 등)
- 라인 320, 470: `rt_cd != "0"` 체크 + warning + None 반환
- 라인 37-54: `_safe_int(value, default=0)` / `_safe_float`

### `closing_bet_system/infra/kis_client.py`
- 라인 51-59: `get_kis_api()` 싱글톤 헬퍼 (race 방지 lock)
- → 신규 `kis_market_provider.py` 도 동일 패턴 적용

### `closing_bet_system/config/settings.yaml`
- 라인 122-123: `kis: use_mock: true` (Phase 1 모의투자)
- 단위 2-9c 추가: `data_source.fallback_per_ticker_enabled: true`
- 단위 2-9d 추가 예정: `data_source.use_kis_ranking`/`fallback_include_market_cap`/`kis_top_n`/`kis_market_cap_top_n`

## 영향 범위
- **신규**: `kis_market_provider.py` (1 파일)
- **수정**: `universe_provider_v2.py` (출처 2~4 본문), `universe_filters.py` (시총 보강), `settings.yaml`
- **호출자**: `main_orchestrator.run_daily_pipeline` (변경 없음 — 토글 OFF 시 회귀 안전)
- **간접**: KIS API 호출량 +51회/일 (현 1,200 → 1,251)

## 호출 흐름 (단위 2-9d 적용 후)

```
main_orchestrator.run_daily_pipeline (15:10 KST)
  └─> universe_provider_v2.get_universe_v2_filtered()
        ├─> get_universe_v2()
        │     ├─> _fetch_theme_codes_v2()              # 출처 1 (스윙 테마, 변경 없음)
        │     ├─> _fetch_top_value_codes()              # 출처 2 → KIS volume-rank
        │     ├─> _fetch_top_change_codes()             # 출처 3 → KIS fluctuation
        │     └─> _fetch_top_foreign_buy_codes()        # 출처 4 → KIS foreign-institution-total
        ├─> apply_attribute_filters(severity_map=...)
        │     └─> _fetch_market_data_bulk(today, tickers=...)
        │           ├─> pykrx bulk (빈 응답)
        │           ├─> [옵션 B 활성 시] kis_market_provider.get_top_market_cap_data()  # 시총 보강
        │           └─> _maybe_run_per_ticker_fallback (옵션 A 보수, 시총 누락 종목)
        └─> apply_liquidity_filters
```

## 과거 버그 / 교훈 (단위 2-9c)
- 데드락: `_merge_with_fallback_cache`에 lock 재획득 시 non-reentrant 데드락 → lock 제거 (CPython GIL atomic 활용)
- 옵션 A 정합 누락: `market_cap=None` 시 시총 검증 스킵 → `data_not_found` 명시 탈락 보강
- 토글 예외 시 default 명시 체크 (코더 검토 심각 #2)

## 호환성 / 회귀 위험
- 토글 false 시 pykrx 경로 그대로 → 단위 2-9c 동작 유지
- KIS API 응답 컬럼명 추정 — 실 호출 시 KeyError 가능 → try/except + 컬럼 fallback 필수
- KIS API rate_limit 0.11초 + 4회 호출 = 약 0.44초 추가 (현 호출량과 무관)

## 검증 환경
- venv: `/home/hatni/korean_stock_ai_trading/venv/bin/python`
- DB: `data/closing_bet.db` + `data/trading.db` (스윙 read-only)
- KIS 도메인: `is_mock=true` 모의 / 실전 prefix 분기 (kis_api.py 112-115)
- 봇 PID 확인: `ps aux | grep main.py | grep -v grep`

---

## 작업 중 발견 사항 (2026-05-06 세션, 다음 대화 이어가기 가이드)

### 이번 세션 완료 항목
1. **단위 2-9c 완료** (commit d3755e9)
   - `universe_filters.py` 폴백 인프라 + 옵션 A 보수 탈락
   - 단위 10건 + 회귀 58건 PASS, code-tester 심각 2 + 데드락 1 즉시 수정
2. **단위 2-9d 완료** (commit b7ae572)
   - `kis_market_provider.py` 신규 + 출처 2~4 KIS 라우팅
   - 단위 12건 + 회귀 누적 80건 PASS, code-tester 심각 3 + 주의 6 즉시 수정
   - 통합 단발 — theme=17 / top_value=25 / top_foreign=21 = **63종목** (3.7배 증가)
3. **봇 재시작** (PID 3414631 → 3440709, KST 11:14)
4. **KIS use_mock=false 전환** (메인 봇 실전 통합)

### 핵심 발견 사항

#### 1. KIS API 토큰 도메인 충돌 (해결됨)
- 메인 봇이 `--real` 모드라 `KISApi._shared_token`이 실전 토큰 보유
- 종가베팅이 `is_mock=true` 모의 도메인으로 호출 시 토큰 인증 불일치 → **403 Forbidden**
- 해결: `settings.yaml kis.use_mock: false` 전환 (Phase 1 알림형이라 자동매수 위험 0)

#### 2. KIS Open API ranking endpoint 4종 매뉴얼 검증 완료
- 출처: koreainvestment/open-trading-api 공식 GitHub WebFetch
- TR_ID:
  - `FHPST01710000` volume-rank `/uapi/domestic-stock/v1/quotations/volume-rank`
  - `FHPST01700000` fluctuation `/uapi/domestic-stock/v1/ranking/fluctuation`
  - `FHPTJ04400000` foreign-institution-total `/uapi/domestic-stock/v1/quotations/foreign-institution-total`
  - `FHPST01740000` market-cap `/uapi/domestic-stock/v1/ranking/market-cap`
- 응답 컬럼: `mksc_shrn_iscd`/`hts_kor_isnm`/`stck_prpr`/`prdy_ctrt`/`acml_vol`/`acml_tr_pbmn`/`stck_avls`

#### 3. ⚠️ 미해결 — KIS stck_avls 단위 불일치 (단위 2-9e 후속 필수)
**증상**: 005930(삼성전자) `stck_avls=15,551,101` `stck_prpr=266,000`
- "원" 가정 → 1,500만원 (시총 X)
- "백만원" 가정 → 15.5조원 (실제 530조와 불일치)
- "억원" 가정 → 1,555조원 (실제 530조와 3배 차이)
- 어떤 단위로도 정확히 일치하지 않음

**영향**: `fallback_include_market_cap=true` 시 모든 종목이 시총 검증에서 탈락 → universe = 0건

**현재 조치**: `settings.yaml fallback_include_market_cap=false` default → 옵션 A 보수 유지 (universe = 0건 정상)

**원인 추정 후보**:
- (a) KIS market-cap ranking이 실제로 다른 의미의 데이터(주식수 등) 반환
- (b) `stck_prpr=266,000`은 액면분할 전 가격 → 분할 비율 50:1 적용 후 5,320원 (대략)
- (c) KIS 응답이 부분 절단 또는 형식 오류

#### 4. 이번 세션 코드 변경 파일 (총 8개, 신규 3 + 수정 5)
- 신규: `kis_market_provider.py` / `test_closing_bet_unit_2_9d.py` / `test_closing_bet_unit_2_9c.py`
- 수정: `universe_filters.py` / `universe_provider_v2.py` / `settings.yaml` / `change_log.md` / `test_closing_bet_unit_2_9a.py`

---

## 다음 세션 가이드 (반드시 새 대화 시작 권장 — 컨텍스트 큼)

### 1순위: 5/7 자연 트리거 모니터링 (목요일 KST 15:10)
**시점**: 5/7 (목) 15:10 KST 이후 (UTC 06:10 이후)

**확인 명령**:
```bash
sudo journalctl -u trading_system --since "06:00" --until "06:20" --no-pager | grep -E "universe_v2|universe_filters|kis_market|closing_bet"
```

**검증 항목**:
- [ ] `[universe_v2] 출처별 기여` 로그 — `top_value/top_change/top_foreign` 모두 비-0 (단위 2-9d 효과)
- [ ] `[universe_filters] bulk 빈 응답 — 종목별 폴백 진입` warning 1건 (단위 2-9c 효과)
- [ ] `[kis_market_provider] stck_avls 단위 진단` 로그 — 첫 항목 ticker/mcap/close 값 (단위 2-9e 분석 데이터)
- [ ] KIS API 호출 시간 ≤ 5초
- [ ] `[orchestrator] 일일 요약 발송=True` (15:35 발송)
- [ ] universe = 0건 (시총 보강 OFF 상태 정상)

**아카이브** (정상 검증 후):
- `docs/work-plans/active/closing-bet-universe-v2-bulk-bypass/` → `completed/20260507_*/`
- `docs/work-plans/active/closing-bet-kis-bulk-replacement/` → `completed/20260507_*/`

### 2순위: 단위 2-9e 시총 보강 정규화 (별도 새 대화)
**목표**: KIS `stck_avls` 단위 검증 + 시총 보강 활성화 → universe ≥ 5건 달성

**조사 출발점**:
1. 5/7 자연 트리거 로그에서 `stck_avls 단위 진단` 다수 종목 데이터 수집
2. 알려진 종목 시총 비교:
   - 005930 (삼성전자): 약 530조원
   - 000660 (SK하이닉스): 약 200조원
   - 035420 (NAVER): 약 30조원
3. KIS volume-rank `chk_volume_rank.py` 출력 비교 (단위 2-9d 사전 조사 시 일부 확인)

**대안 구현**:
- (A) 단위 정규화: `stck_avls` × N (N 검증 후 결정) 적용
- (B) 종목별 KIS `inquire-price` 호출로 시총 명시 추출 (~63회 × 0.11초 = 7초 추가)
- (C) pykrx `get_market_cap_by_date(today, today, ticker)` 종목별 호출 (KRX 차단 영향 미확인)

**핵심 코드 위치**:
- `closing_bet_system/collectors/kis_market_provider.py:222-258` `get_top_market_cap_data` (단위 정규화 시 수정)
- `closing_bet_system/collectors/universe_filters.py:280-330` `_fetch_per_ticker_today_data` (옵션 B 추가 호출 시 수정)
- `settings.yaml`: `data_source.fallback_include_market_cap` 활성화

### 3순위: 단위 2-9c + 2-9d active → completed 아카이브
5/7 자연 트리거 검증 완료 후:
```bash
mv docs/work-plans/active/closing-bet-universe-v2-bulk-bypass/ docs/work-plans/completed/20260507_closing-bet-universe-v2-bulk-bypass/
mv docs/work-plans/active/closing-bet-kis-bulk-replacement/ docs/work-plans/completed/20260507_closing-bet-kis-bulk-replacement/
```

---

## 주의 사항 (다음 대화에서 반드시 확인)

### 운영 안전망
- ✅ Phase 1 알림형 (자동매수 X)
- ✅ `kis.use_mock=false` 전환에도 종가베팅 잡 3건은 시세 조회만
- ✅ `data_source.use_kis_ranking=true` (default) — 출처 2~4 KIS 사용 중
- ⚠️ Phase 2 자동매수 진입 시 `use_mock` 분리 재검토 필요 (실전/모의 도메인 토큰 격리)

### 회귀 안전 토글 (롤백 시)
```yaml
# settings.yaml
data_source:
  fallback_per_ticker_enabled: false       # 단위 2-9c 비활성
  use_kis_ranking: false                   # 단위 2-9d 비활성
  fallback_include_market_cap: false       # 단위 2-9e 미활성 (현 default)
kis:
  use_mock: true                           # 모의 도메인 복귀
```

### 새 대화 시작 가이드
이번 대화는 **두 단위 + 사전 조사 + code-tester 2회 + 통합 단발 4회** 처리로 컨텍스트 매우 큼.
**새 대화에서 `/resume`** 호출 → 본 CONTEXT.md + 두 PLAN.md + 두 CHECKLIST.md 자동 로드 → 단위 2-9e 시작.

추가 자료:
- 이번 세션 commit: d3755e9 (2-9c) / b7ae572 (2-9d) — `git show <hash>` 로 변경 내용 검토
- KIS Open API 매뉴얼: https://github.com/koreainvestment/open-trading-api (WebFetch 검증 완료)
