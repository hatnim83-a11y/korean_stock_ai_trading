# CONTEXT: 종가베팅 universe v2

## 변경 이유
- Phase 2 옵션 A (단위 2-1/2-2/2-6) 완료 후 사용자 검토에서 universe 한계 발견 (2026-05-04)
- 현재 universe v1 = 스윙 top_themes 5개 → 19종목/일 (PRD Layer 3 부분만)
- PRD 16-3 본래 의도 = Layer 1+3 다중 출처 (수급+모멘텀+테마 통합)
- 사용자 결정: "30건 게이트가 PRD 시그니처로 뽑힌 데이터로 채워져야 의미 있다"

## 영향 범위
- DB 변경 없음 (candidates 스키마 동일)
- KIS API 호출 추가 없음 (pykrx 활용)
- pykrx 호출 추가: 일 4 함수 (거래대금/등락률/외국인/시총) — 자체 throttle 있음
- universe 종목 수: 19 → 30~100 (예상 50내외)
- candidate 처리 시간 증가: 19 → 50종목 시 collect_for_universe 시간 ×2.5배

## 현재 코드 상태 (이미 활용 가능)

### universe_provider v1 (Phase 1 carryover 단위 B)
- 위치: `closing_bet_system/collectors/universe_provider.py`
- 핵심 함수:
  - `get_universe()` — 6자리 종목코드 list
  - `_build_universe()` — 스윙 테마 + 네이버 크롤링 + 검증
  - `_open_swing_db()` — Database 인스턴스 connect/close 보장
  - `_fetch_top_themes()` / `_fetch_swing_holdings()` — 스윙 DB 조회 헬퍼
- in-memory 캐시 (더블체크 lock 패턴)
- v2도 동일 패턴 차용

### pykrx 가용 함수 (사전 점검 완료)
```python
from pykrx import stock
# 모두 가용:
stock.get_market_trading_value_by_date  # 거래대금 (일자별)
stock.get_market_price_change_by_ticker # 등락률
stock.get_market_net_purchases_of_equities_by_ticker  # 외국인 순매수
stock.get_market_cap_by_ticker          # 시가총액
stock.get_market_ohlcv_by_ticker        # OHLCV (당일)
stock.get_market_fundamental_by_ticker  # 펀더멘털 (PER/PBR/ROE)
```

### Phase 2 옵션 A 단위 패턴 (참조)
- `kis_orderbook_collector.py` — collect_one + collect_for_universe + insert_snapshots 패턴
- `market_data_provider.py` — _safe_call 예외 격리 패턴
- 단위 테스트: 시나리오 정상/실패/캐시/예외/통합 (10~16건)
- code-tester 호출 패턴 + 보강

## 핵심 코드 스니펫

### v1 → v2 전환 위치 (scheduler.py:286-321)
```python
from closing_bet_system.collectors.universe_provider import (
    get_universe as _cb_get_universe,  # ← 단위 2-9b 완료 시 v2_filtered로 교체
)
self._closing_bet_orch = MainOrchestrator(
    universe_provider=_cb_get_universe,  # ← 한 줄 교체로 v2 적용
    ...
)
```

### v1 _build_universe 패턴 (v2도 유사 흐름)
```python
def _build_universe(today, theme_count, stocks_per_theme, hard_cap, exclude_swing_holdings):
    themes = _fetch_top_themes(today, theme_count)
    if not themes:
        return []
    excluded = _fetch_swing_holdings() if exclude_swing_holdings else set()
    seen, universe = set(), []
    for theme in themes:
        codes = _crawl_theme_codes(theme["url"], ..., stocks_per_theme)
        for code in codes:
            if code in seen or code in excluded:
                continue
            seen.add(code)
            universe.append(code)
            if len(universe) >= hard_cap:
                return universe
    return universe
```

### pykrx 호출 예시 (v2에서 활용)
```python
import pykrx.stock as krx
from datetime import datetime

today_str = datetime.now().strftime("%Y%m%d")

# 거래대금 (당일, KOSPI+KOSDAQ)
df_value = krx.get_market_trading_value_by_date(today_str, today_str, market="KOSPI")
top_value_kospi = df_value.nlargest(30, "거래대금").index.tolist()  # 종목코드 list

# 등락률
df_chg = krx.get_market_price_change_by_ticker(today_str, today_str, market="KOSPI")
top_change = df_chg.nlargest(30, "등락률").index.tolist()

# 외국인 순매수
df_for = krx.get_market_net_purchases_of_equities_by_ticker(today_str, today_str, market="KOSPI", investor="외국인")
top_foreign = df_for.nlargest(30, "거래대금").index.tolist()

# 시가총액 (필터)
df_cap = krx.get_market_cap_by_ticker(today_str, market="KOSPI")
small_cap = df_cap[df_cap["시가총액"] < 500_000_000_000].index  # 500억 미만 제외

# OHLCV (52주 고점/저점, 1일치)
df_ohlcv = krx.get_market_ohlcv_by_ticker(today_str, market="KOSPI")
upper_limit_today = df_ohlcv[df_ohlcv["등락률"] >= 29.5]  # 상한가 추정
```

### PRD 4-1 / 4-4 필터 정의 (closing_bet_system/config/settings.yaml에서 참조)
```yaml
stock_filter:
  min_market_cap: 50000000000              # 시가총액 500억 미만 제외
  min_price: 1000                          # 주가 1,000원 미만 제외
  max_drop_from_52w_high: 0.30             # 52주 고점 -30% 초과 하락 제외
  exclude_upper_limit: true                # 당일 상한가 제외
  protection_period_days: 7                # 보호예수 D-7 (별도 데이터 필요)

liquidity:
  min_avg_value_20d: 5000000000            # 20일 평균 거래대금 50억 미만 제외
  min_today_value: 10000000000             # 당일 거래대금 100억 미만 제외
  spread_multiplier_limit: 1.5             # 호가 스프레드 비정상
  order_value_pct_limit: 0.03              # 주문금액 vs 유동성
```

## 과거 버그/주의사항

- **종목코드 검증**: pykrx 결과는 모두 6자리 숫자지만 안전을 위해 `re.match(r'^\d{6}$')` 확인
- **timezone**: pykrx `today_str` 은 KST 영업일 (영업일 체크 필요 — config.is_trading_day)
- **장 시작 전 호출 금지**: pykrx `get_market_price_change_by_ticker` 는 당일 데이터 — 장 마감 후 호출 안전 (15:10 트리거 시점 OK, 장 마감 15:30 임박)
- **20일 평균 거래대금**: pykrx에서 `get_market_trading_value_by_date(20일전, 오늘)` 호출 후 평균 계산 필요 — 비용 증가 (1초+ 소요)
- **외국인 순매수**: investor 파라미터 정확히 "외국인" (한글). "외국계", "외국법인" 등 다른 카테고리도 있음 — PRD 5장 Layer 1 확정값 = "외국인"
- **상한가**: 등락률 +30% 정확 매칭 어려움 (실제 +29.97% 등) → 임계값 +29.5% 사용 권장

## 호환성

- universe_provider_v2 시그니처는 v1과 동일 (`get_universe() -> list[str]`)
- main_orchestrator 변경 없음 (Callable 인터페이스 유지)
- scheduler.py 한 줄 교체 (rollback 즉시 가능)
- 단위 테스트 v1과 별도 파일 (`test_closing_bet_unit_2_9a.py`, `_2_9b.py`)
- pykrx 의존성: requirements.txt 명시 필요 여부 점검 (이미 modules/post_trade_analyzer 등에서 사용 중일 수 있음)

## 호출 그래프 (v2 적용 후)

```
APScheduler 15:10 KST
  → MainOrchestrator.run_daily_pipeline()
    → universe_provider() = get_universe_v2_filtered()  ← 신규 (v2)
        → 4 출처 합집합 (단위 2-9a):
            - 스윙 top_themes 종목 (기존 v1 로직)
            - pykrx 거래대금 상위 30
            - pykrx 등락률 상위 30
            - pykrx 외국인 순매수 상위 30
        → 중복 제거 + 스윙 보유 제외
        → PRD 4-1 속성 필터 (단위 2-9b):
            - 시총 500억 미만 제외
            - 주가 1000원 미만 제외
            - 상한가 종목 제외
            - 52주 고점 -30% 초과 하락 제외
        → PRD 4-4 유동성 필터 (단위 2-9b):
            - 20일 평균 거래대금 50억 미만 제외
            - 당일 거래대금 100억 미만 제외
        → hard_cap 100 적용
        → list[ticker] 반환 (예상 50~80종목)
    → 4 collectors (flow + price/volume + DART + orderbook) 병렬
    → 종목별 점수 산출 + DB 기록
    → 알림 (현재 임계값 7점 미달이라 0건 유지, Layer 1 가중치 0)
```

## 검증 시 주의

- pykrx 호출은 KRX 공식 사이트 → 요청 빈번 시 일시 차단 가능 → 캐시 + 1회/일만 호출
- 4 출처 모두 빈 결과 시 v1과 동일하게 빈 리스트 폴백 (graceful)
- 필터 너무 강하면 universe 0 → 테스트로 분포 확인 필요
- 단위 2-9b 가 universe 너무 많이 잘라내면 운영 점검 게이트 도달 지연
- 단발 트리거로 universe 종목 수 + 필터 통과율 사전 검증 권장

---

## 작업 중 발견 사항 (2026-05-04 세션)

### 이번 세션에서 진행한 흐름 (요약)

이번 대화는 **8단위 작업 + 2회 배포 + universe v2 계획 수립**까지 처리한 매우 큰 세션이었다.
단계별 진행:

1. **Phase 1 이월 항목 5단위 (단위 A~E)** — `closed-bet-phase1-carryover` 작업
   - placeholder 4개 providers (universe/market_data/name_lookup/label_provider) 와 fund_guard.weekly_loss_limit 미구현 등 6개 이월 항목을 5단위로 처리
   - 단위 테스트 55건 PASS, code-tester 심각 0
   - 배포 (PID 1965133 → 2336951 → 2511049)
   - 아카이브: `docs/work-plans/completed/20260504_closing-bet-phase1-carryover/`

2. **Phase 2 옵션 A 3단위 (2-1, 2-2, 2-6)** — `closing-bet-phase2-data-collection` 작업
   - 자동매매 위험 0인 데이터 수집/모니터링 인프라 (orderbook + KIND 인터페이스 + 대시보드 종가베팅 탭)
   - 단위 테스트 42건 PASS, code-tester 심각 2건 발견 → 즉시 보강 (insert_snapshots db=None 누수 + assess_for_universe kind_alerts 누락)
   - 배포 (PID 2511049 → 2533276 → 2540234)
   - 아카이브: `docs/work-plans/completed/20260504_closing-bet-phase2-data-collection/`

3. **첫 자연 트리거 (15:10 KST) 결과 + 즉시 fix**
   - 첫 트리거에서 **`from database import db` ImportError** 발견 (database.py에 모듈 수준 db 인스턴스 없음)
   - universe_provider.py 의 `_open_swing_db()` 헬퍼로 수정 — `Database` 인스턴스 직접 생성 + connect/close 보장 (try/finally)
   - 재시작 후 단발 검증: universe 19, valid 18, rejected_filter 4 (atr_overheat>1.8 PRD 하드 필터), 알림 0건 (Layer 1 가중치 0이라 정상 — 임계값 7점 도달 어려움)
   - DB 검증: orderbook_snapshots 19건 + candidates 18건 INSERT 성공

4. **사용자 검토 → universe v2 진입 결정 (현재 작업)**
   - 사용자 질문: "종목 선정이 무조건 스윙 선정 테마 범위 내에서만?"
   - PRD 16-3 본래 의도 확인: 14:00 Layer 3 (테마) + 14:30 Layer 1 (수급) + 15:00 Layer 1+2 통합
   - 현재 v1 = Layer 3 부분(스윙 테마 5개)만 → 거래대금/외국인 순매수/모멘텀 종목 누락
   - 사용자 결정: "PRD대로 설계된 데이터가 중요하니 B 즉시 진입 + 30건 게이트도 v2 데이터로 채우자"
   - 본 3문서 작성 완료 (PLAN/CONTEXT/CHECKLIST)

### 무엇을 발견했는가 (이번 세션의 핵심)

1. **universe v1 한계**: PRD 16-3 의도 중 Layer 3 부분만 구현. 종가베팅 핵심 시그니처(거래대금 폭발/외국인 매수 우위) 종목이 universe에 안 들어옴. 30건 게이트가 PRD 본래 시그니처가 아닌 데이터로 채워지는 문제.
2. **pykrx 가용성 사전 점검 완료**: 4개 핵심 함수 (`get_market_trading_value_by_date`, `get_market_price_change_by_ticker`, `get_market_net_purchases_of_equities_by_ticker`, `get_market_cap_by_ticker`) 모두 가용 — KIS API 호출 추가 부담 없음.
3. **첫 자연 트리거에서 import 버그 발견**: `from database import db` 가 잘못된 가정 (database.py는 `Database` 클래스만 export, 모듈 수준 `db` 인스턴스 없음). 관련 패턴은 `Database()` 직접 인스턴스화 (main.py:53, scheduler.py:44 동일).

### 무엇을 수정/작성했는가

**수정**:
- `closing_bet_system/collectors/universe_provider.py:159` — `_open_swing_db()` 헬퍼 + `_fetch_top_themes`/`_fetch_swing_holdings`에서 connect/close 보장
- `change_log.md` — 2026-05-04 항목 2건 추가 (Phase 1 carryover + Phase 2 옵션 A)
- `memory/MEMORY.md` 인덱스 + `memory/project_closing_bet_system.md` 본문

**작성 (이번 세션 신규 9 파일 + 단위 테스트 6 파일)**:
- Phase 1 carryover: `name_lookup.py`, `universe_provider.py`, `market_data_provider.py`, `label_provider.py` + 단위 테스트 5개 (`test_closing_bet_unit_a~e`)
- Phase 2 옵션 A: `kis_orderbook_collector.py`, `kind_alert_collector.py`, `dashboard/data_adapter.py` + 단위 테스트 3개 (`test_closing_bet_unit_2_1/2/6`)
- universe v2 (계획): 본 3문서 (PLAN/CONTEXT/CHECKLIST)

### 왜 그렇게 판단했는가

- **Phase 1 carryover 5단위 분할**: CLAUDE.md "한 대화에서 너무 많이 처리하지 말 것" — 단위별 사용자 컨펌 + code-tester 검증
- **Phase 2 옵션 A 3단위만 선별**: PRD 17 = "자동매수 절대 금지". 2-3/2-4/2-5/2-7/2-8은 30건 게이트 후 의미. 자동매매 위험 0인 단위만 즉시 진입
- **단위 2-2 KIND는 인터페이스만**: KIND 사이트 구조 안정성 검토 + 차단 위험 검토를 별도 단위로 분리. 우선 OvernightRiskFilter 통합 흐름만 검증
- **2-6 dashboard는 별도 모듈 아닌 web/ 통합**: web/ 인증/SSE 인프라 재사용. 마스터 플랜은 별도 모듈 명시했으나 운영 부담 회피
- **universe v2 즉시 진입 결정**: 사용자 판단 + 자동매매 위험 0이라 가능. 30건 게이트 데이터 품질 확보

### 다음 단계 (단위 2-9a) 시작 전 주의

1. **pykrx import lazy 필수**: 모듈 로드 시 부담 회피. `_fetch_top_value_codes()` 등 함수 내부에서 import
2. **종목코드 검증**: pykrx 결과는 모두 6자리 숫자지만 안전을 위해 `re.match(r'^\d{6}$')` (Phase 1 패턴 일관)
3. **timezone**: pykrx `today_str = now_kst().strftime("%Y%m%d")` — UTC 서버라 `now_kst()` 필수. `is_trading_day()` 가드 권장
4. **외국인 investor 카테고리**: 정확히 `"외국인"` (한글). "외국계", "외국법인" 등 다른 카테고리 있으니 주의
5. **상한가 임계값**: 등락률 정확히 +30% 매칭 어려움 (실제 +29.97% 등) → +29.5% 사용 권장
6. **20일 평균 거래대금**: pykrx에 직접 함수 없음 → `get_market_trading_value_by_date(today-30, today)` 호출 후 종목별 평균 계산 (1초+ 추가 비용)
7. **단위 2-9a / 2-9b 파일 분리 vs 통합**: 큰 단위 한 파일 vs 두 파일. 권고는 별도 파일 (`universe_provider_v2.py` + `universe_filters.py`) — 단위 테스트 분리 + 롤백 단위 명확
8. **scheduler.py 교체 시점**: 단위 2-9b 완료 + 단발 검증 통과 후 (scheduler 수정 한 줄로 v1 ↔ v2 즉시 전환)
9. **롤백 트리거**: pykrx 응답 30초+ 지연 / universe >200종목 폭주 / 필터 오작동으로 정상 종목 제외
10. **테스트 패턴 참조**: `scripts/test_closing_bet_unit_2_1.py` 의 `_mock_order_api()` 패턴이 적합. pykrx 함수는 DataFrame 반환이라 mock 시 `pandas.DataFrame` 직접 생성 권장

### 현재 시스템 상태 (KST 15:17 시점, 새 대화 시작 전)

- `trading_system` active (PID 2540234, providers 4종 + 4 collectors 활성)
- `trading_dashboard` active (PID 2533412, 종가베팅 탭 활성)
- 종가베팅 잡 3건 등록 (15:10/15:35/10:00)
- closing_bet.db: candidates 18건 + orderbook_snapshots 19건 (오늘 단발 트리거 결과)
- 첫 자연 일일 요약 (15:35) 트리거 대기 중 — ScheduleWakeup 등록 (KST 15:36 자동 확인 예정)

### 컨텍스트 사이즈 안내

이번 대화는 **매우 큰 세션** (8단위 + 2회 배포 + 3문서 작성).
**다음 대화에서 `/resume` 호출 → 본 3문서 자동 로드 → 단위 2-9a 시작 권장** (CLAUDE.md 원칙).

새 대화 첫 명령:
```
/resume
```

새 대화에서 첫 작업:
1. 본 PLAN.md 단위 2-9a 섹션 확인
2. `closing_bet_system/collectors/universe_provider_v2.py` 신규 작성
3. 패턴 참조: 기존 `universe_provider.py` v1 + Phase 2 옵션 A `kis_orderbook_collector.py`
