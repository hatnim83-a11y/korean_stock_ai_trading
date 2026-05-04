# CONTEXT: 종가베팅 Phase 1 이월 항목

## 변경 이유
- 종가베팅 Phase 1 9단위는 완료(2026-05-04)되었으나 main.py 통합이 placeholder providers (universe=빈 리스트, market_data=빈 dict, name_lookup="(미상)") 라 잡은 등록되지만 **실제 데이터 처리 무동작**
- Phase 2 (반자동) 진입 전 Phase 1을 실제로 동작하게 만드는 것이 우선

## 영향 범위
- DB 변경 없음 (closing_bet.db v1 그대로)
- KIS API 호출 추가: universe (1회/일) + market_data (1회/일) + label_provider (≤5건/일) → 일 < 50건
- scheduler.py:294-301 의 lambda 4개를 실 함수로 교체
- main 시스템 영향 없음 (`_setup_closing_bet_jobs()` 자체가 try/except 격리)

## 현재 코드 상태

### scheduler.py:286-310 — `_setup_closing_bet_jobs()`
- MainOrchestrator 생성 시 4개 placeholder lambda 주입 중
- universe_provider/market_data_provider/name_lookup만 주입, label_provider는 register_jobs(scheduler) 호출 시 None (run_label_yesterday는 self가 직접 부르지 않고 scheduler의 트리거에서 실행)
- → label_provider는 MainOrchestrator 생성자에 추가하거나 register_jobs 시점에 partial 적용 필요

### main_orchestrator.py 핵심 시그니처
- `MainOrchestrator.__init__(...)` 인자: universe_provider, market_data_provider, name_lookup만 받음 → label_provider 신규 인자 추가 필요 (line 96-110)
- `run_label_yesterday(label_provider=None)` 인자로 받음 → scheduler.add_job 호출 시 partial로 주입하는 게 깔끔
- `register_jobs(scheduler)` line 498-543 — label 잡 등록 시 callable 인자 전달 경로 마련 필요

### 핵심 코드 스니펫

**`scheduler.py:294-302` (수정 대상)**
```python
self._closing_bet_orch = MainOrchestrator(
    universe_provider=lambda: [],
    market_data_provider=lambda: {},
    name_lookup=lambda t: "(미상)",
)
self._closing_bet_orch.register_jobs(self.scheduler)
```

**`closing_bet_system/infra/telegram_client.py:75-78` (silent break 위험)**
```python
# 강제 비활성화: 스윙 봇 토큰 폴백 차단 (채널 격리)
_notifier_instance.bot_token = None
_notifier_instance.chat_id = None
_notifier_instance._enabled = False     # ← 부모 내부 속성 직접 변경
_notifier_instance.base_url = ""
```
- 부모(`TelegramNotifier`)가 리팩터해서 `_enabled` 제거되면 silent break
- 대신 NoOp 더미 객체 패턴으로 치환

**`closing_bet_system/infra/fund_guard.py:212-260` (`_fetch_db_state`)**
- TOCTOU 방지를 위해 single connection으로 조회 중
- weekly_loss 검사도 같은 connection에서 추가 쿼리 가능

### 재사용 자원 (확인됨)

**universe_provider 의존성**
- `database.get_top_themes(target_date, count=5)` (database.py:662) — selected=1 우선, 폴백 전체
- 테마 dict의 `url` 필드 (v12에 추가됨)
- `crawl_naver_theme_stocks(theme_url)` (crawlers.py:242) — theme URL → list[{code, name, ...}]
- `database.get_portfolio(status="holding")` (database.py:852) — 스윙 보유 종목 조회 (중복 제외용)

**name_lookup 의존성**
- `KISApi.get_stock_name(stock_code)` (kis_api.py:212) — 네이버 금융 + 내부 캐시
- 캐시 멤버: `self._stock_name_cache` (인스턴스 속성)
- 종가베팅 wrapper에 `lru_cache` 또는 dict 캐시로 추가 보호 권장

**market_data_provider 의존성**
- `KISApi.get_index_price("0001")` (kis_api.py:294) — KOSPI, return dict {change_rate, ...}
- `KISApi.get_index_price("1001")` — KOSDAQ
- V-KOSPI / 미선물 / USD-KRW: KIS 미지원 → yfinance(`^VKOSPI`, `ES=F`, `KRW=X`) 폴백 또는 None 유지
- `MarketGuard` (market_guard.py) — KOSPI/KOSDAQ 위기 판단 패턴 참고만

**label_provider 의존성**
- `KISApi.get_daily_price(ticker, period="D", count=2)` (kis_api.py:508) — list[dict] {date, open, high, low, close, volume}
- `CostSlippageEngine` (closing_bet_system/engines/cost_slippage_engine.py) — 비용 차감 후 breakeven 계산 (label_net_ev_positive에 활용)

**fund_guard 의존성**
- closing_bet.db `candidates.net_pnl_pct` (db.py:237) — 이미 컬럼 존재
- `candidate_status='entered'` AND `exit_time IS NOT NULL` AND `exit_time >= today-7일`

## 과거 버그/주의사항
- **종목코드 검증**: `re.match(r'^\d{6}$')` 적용 필수 (universe 결과 필터). naver 크롤러는 무효 코드(0015G0 등) 반환 가능성
- **KIS rate_limit**: KISApi는 내부적으로 _rate_limit() 호출하지만, universe 단계에서 N개 종목명 조회 시 throttle 누적 → 캐시 + lru_cache로 회피
- **테마 URL 갱신**: themes.url은 v12 컬럼이지만 매일 17:05 일별 수집에서 갱신됨. 화요일 주간 선정 시점에서 최신 url 사용 보장됨
- **timezone**: 모든 시간 계산은 `now_kst()` (config.py)
- **KIS API mock 모드**: settings.yaml `kis.use_mock=true` 시 mock 가격 반환 — universe 운영 시 실 시세 필요할 수 있음 (Phase 1은 mock OK, Phase 2 점진 전환)

## 검증 시 주의
- universe_provider는 **장 시작 전 14:30~15:10 시점에 동작** → KIS 시세는 아직 활발하지만 막판 변동 전. universe는 "후보 ticker 리스트"만 반환하면 되므로 실시간 가격 X.
- label_provider는 **T+1 10:00 호출** → 오늘 09:30 시초가 이미 확정. KIS daily_price 첫 행이 오늘 OHLC.
- weekly_loss_limit은 closing_bet.db에 `entered + 매도 완료` 후보가 0건이면 자연스럽게 통과 → Phase 1 알림형 단계에서는 entered가 0건이라 통과만 함. Phase 2 진입 후 의미 발생.

## 호환성
- 단위별로 독립 커밋 → 단위 A만 머지하고 단위 B 보류해도 정상 동작
- placeholder lambda 모두 default OK 패턴 → 구현체 미주입 시에도 무동작 안전 (이전과 동일)
- TelegramNotifier NoOp 패턴: send_* 호출이 silent return → 호출 측 변경 불필요

## 호출 그래프 (수정 후)

```
APScheduler (15:10 KST)
  → MainOrchestrator.run_daily_pipeline()
    → universe_provider()  ← 신규 universe_provider.get_universe()
      → database.get_top_themes()
      → crawlers.crawl_naver_theme_stocks() (per theme)
      → 6자리 종목코드 필터
      → 스윙 보유 제외 (선택)
    → market_data_provider()  ← 신규 market_data_provider.get_market_data()
      → KISApi.get_index_price("0001")  # KOSPI
      → KISApi.get_index_price("1001")  # KOSDAQ (참고용)
      → yfinance fallback (선택)
    → for ticker in universe:
        → name_lookup(ticker)  ← 신규 name_lookup.get_name()
          → KISApi.get_stock_name(ticker) (캐시)
        → 1-3 collector + 1-2 collector + 1-5a → 점수 → 알림

APScheduler (10:00 KST)
  → MainOrchestrator.run_label_yesterday(label_provider=label_provider.get_label)
    → for candidate in yesterday_recommended:
        → label_provider(ticker)  ← 신규
          → KISApi.get_daily_price(ticker, count=2)
          → 라벨 4개 계산 + CostSlippageEngine 호출

FundGuard.allow_order(ticker, amount)  ← 신규 weekly_loss 검사 추가
  → _fetch_db_state() — 단일 connection
    → active_amount + active_tickers + today_entries + weekly_pnl  ← 신규
  → if weekly_pnl <= -5%: 차단
```
