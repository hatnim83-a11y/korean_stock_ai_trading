# CONTEXT: 종가베팅 Phase 2 데이터 수집/모니터링

## 변경 이유
- Phase 1 (이월 항목 5단위 포함) 모든 단위 완료 (2026-05-04 KST 14:40 배포)
- 30건 운영 점검 게이트까지 데이터 누적 대기 중 (3~8주)
- 대기 기간 동안 자동 매매 위험 0인 데이터 수집/모니터링 인프라 보강

## 영향 범위
- closing_bet.db v1 → v2 (orderbook_snapshots 테이블 추가)
- KIS API 호출 추가: 종목당 4 스냅샷 × universe(~20) = 80건/일
- KIND 크롤링 추가: 1회/일 (전체 종목 알림 dict)
- 대시보드 라우트 5개 + 탭 1개 추가
- 메인 시스템 영향 없음 (수집 실패 시 폴백, try/except 격리)

## 현재 코드 상태 (이미 활용 가능)

### KIS API
- `inquire_asking_price(stock_code)` (kis_order_api.py:819) — 1단계 호가 dict 반환 (ask1/bid1/현재가/스프레드)
  - **확장 여지**: 응답 output1에 1~10단계 모두 있지만 현재 1단계만 추출. 본 단위에서 1단계만 사용 → 단순.

### Phase 1 collector 패턴
- `KisIntradayFlowCollector.collect_for_universe(tickers)` (kis_intraday_flow_collector.py:160) — 순차 처리, 예외 격리, list[Snapshot] 반환
- `IntradayFlowSnapshot` dataclass (frozen) — ticker/snapshot_time/is_valid + 지표 필드 + raw_payload
- 본 단위 2-1/2-2 동일 패턴 차용

### Phase 1 결손 정책
- `OvernightRiskFilter.assess()` (overnight_risk_filter.py:195) — 모든 시장 데이터 None 허용, 해당 룰 비활성
- 본 단위 2-2 KIND 통합 시 동일 정책 적용

### main_orchestrator
- `__init__` 인자 패턴: `flow_collector`, `price_volume_collector`, `dart_collector` 등 None default → 자동 instantiate
- `run_daily_pipeline` line 214-227: asyncio.gather(return_exceptions=True) 병렬 수집 패턴
- `_process_ticker` line 376: 종목별 처리 + 예외 격리

### 기존 web/ 인프라
- FastAPI router prefix `/api/v1`
- `require_auth` dependency 인증
- `dashboard_service.py` 데이터 어댑터 패턴
- `dashboard.html` Jinja2 템플릿

### closing_bet_system/storage/db.py
- v1 마이그레이션 패턴: `_migrate_v1` (db.py:206)
- `_migrate(name, version, fn)` 헬퍼 (idempotent + auto-backup)
- v2 추가: `_MIGRATIONS` 리스트에 `(2, "...", self._migrate_v2)` append

## 핵심 코드 스니펫

### KIS `inquire_asking_price` 응답 (현재 1단계만 추출)
```python
# kis_order_api.py:866-878
output1 = data.get("output1", {})
output2 = data.get("output2", {})
return {
    "success": True,
    "ask1": _safe_int(output1.get("askp1")),
    "bid1": _safe_int(output1.get("bidp1")),
    "ask_volume1": _safe_int(output1.get("askp_rsqn1")),
    "bid_volume1": _safe_int(output1.get("bidp_rsqn1")),
    "current_price": _safe_int(output2.get("stck_prpr")),
}
```
- 본 단위 2-1: 이 dict를 그대로 활용 + DB INSERT

### Phase 1 collector 시그니처 패턴 (kis_intraday_flow_collector.py)
```python
class IntradayFlowSnapshot:
    ticker: str
    snapshot_time: datetime
    is_valid: bool
    raw_payload: Optional[dict] = None
    # ... 지표 필드

class KisIntradayFlowCollector:
    def __init__(self, kis_api: Optional[KISApi] = None):
        self._kis_api = kis_api
    
    def collect_one(self, ticker: str, snapshot_time: Optional[datetime] = None) -> IntradayFlowSnapshot:
        ...
    
    def collect_for_universe(self, tickers: list[str]) -> list[IntradayFlowSnapshot]:
        snapshots = []
        for ticker in tickers:
            try:
                snap = self.collect_one(ticker)
                snapshots.append(snap)
            except Exception as e:
                logger.error(...)
                snapshots.append(IntradayFlowSnapshot(ticker=ticker, ..., is_valid=False))
        return snapshots
```

### main_orchestrator 통합 패턴 (자연 적용)
```python
# 단위 2-1 통합 후
flow_snaps_task = asyncio.to_thread(self.flow_collector.collect_for_universe, tickers)
pv_snaps_task = asyncio.to_thread(self.price_volume_collector.collect_for_universe, tickers)
dart_snaps_task = asyncio.to_thread(self.dart_collector.collect_for_universe, tickers)
orderbook_snaps_task = asyncio.to_thread(self.orderbook_collector.collect_for_universe, tickers)  # 신규
gather_results = await asyncio.gather(
    flow_snaps_task, pv_snaps_task, dart_snaps_task, orderbook_snaps_task,
    return_exceptions=True,
)
```

## KIND 시장경보 데이터 출처

### 후보 1: KIND 공식 사이트
- URL 예시: `https://kind.krx.co.kr/disclosure/marketwarning.do`
- HTML 테이블 크롤링 (httpx + BeautifulSoup)
- 단점: HTML 구조 변경 시 깨짐

### 후보 2: pykrx 라이브러리
- `pykrx.stock.get_market_warning_list()` 또는 유사 API 확인 필요
- 없으면 후보 1로 fallback

### 후보 3: KRX 정보데이터시스템 API
- 공식 API 키 필요할 수 있음

**결정**: 후보 2 (pykrx) 우선 시도 → 실패 시 후보 1 (HTML 크롤링). 본 단위 작업 중 결정.

## 과거 버그/주의사항

- **KIS rate_limit**: 종목당 호가 1회 = universe 20 → 20 호출. 이미 1-2/1-3에서 universe별 호출 있음. 추가 20호출은 충분히 흡수 가능.
- **종목코드 검증**: 항상 `re.match(r'^\d{6}$')` (다른 단위와 일관)
- **timezone**: `now_kst()` 사용 (config.py)
- **TOCTOU**: 단일 connection 내 INSERT (db.get_cursor()) — 기존 패턴 유지
- **KIND HTML 크롤링**: User-Agent 헤더 필수, MIN_DELAY 1~2초 (네이버 패턴 참조)
- **OvernightRiskFilter 호환성**: `kind_alerts` 인자는 **kwargs default None → 기존 호출처 영향 없음

## 호환성

- 단위 2-1 단독 머지 가능 (2-2 보류해도 정상 동작)
- 단위 2-2 단독 머지 가능 (2-1 보류해도 OvernightRiskFilter는 None 폴백)
- 단위 2-6 단독 머지 가능 (2-1/2-2 데이터 없으면 빈 dict 표시)
- 모든 신규 collector는 None default → `MainOrchestrator(orderbook_collector=None)` OK

## 호출 그래프 (3단위 통합 후)

```
APScheduler 15:10 KST
  → MainOrchestrator.run_daily_pipeline()
    → universe_provider() → list[ticker]
    → market_data_provider() → market dict
    → KIND 1회 (전체 종목)  ← 신규 (단위 2-2)
    → asyncio.gather(병렬):
        - flow_collector.collect_for_universe()
        - price_volume_collector.collect_for_universe()
        - dart_collector.collect_for_universe()
        - orderbook_collector.collect_for_universe()  ← 신규 (단위 2-1)
    → for ticker:
        → score_engine.score()
        → risk_filter.assess(market_data, dart, kind_alerts)  ← kind_alerts 추가
        → candidate_logger.log_recommended/log_features
        → orderbook 별도 INSERT (orderbook_snapshots 테이블)  ← 신규
        → 알림 후보 수집

웹 대시보드 (사용자 접속)
  → /api/v1/closing-bet/today              ← 신규 (단위 2-6)
  → /api/v1/closing-bet/gate-progress      ← 신규
  → /api/v1/closing-bet/kind-history       ← 신규
  → /api/v1/closing-bet/risk-history       ← 신규
  → /api/v1/closing-bet/fund-guard-status  ← 신규
  → 종가베팅 탭 렌더링 (dashboard.html에 탭 추가)
```

## 검증 시 주의

- universe가 0이면 orderbook_collector도 자연 무동작 (Phase 1 carryover universe_provider 빈 폴백 시)
- KIND 크롤링 실패 시 빈 dict → OvernightRiskFilter에서 룰 비활성 (기존 동작)
- 대시보드는 closing_bet.db가 비어있어도 빈 카운트 정상 표시 (KeyError 금지)
- 단위 2-1 4 스냅샷은 동시호가 시간(15:20~15:28) 폴링과 다름 — 본 단위는 단순 캡처만, 폴링은 entry_executor (2-4)
