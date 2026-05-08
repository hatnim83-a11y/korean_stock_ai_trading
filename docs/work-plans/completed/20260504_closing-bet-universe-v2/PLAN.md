# PLAN: 종가베팅 universe v2 (PRD 본래 의도)

## 목표
현재 universe_provider 가 스윙 시스템 주간 선정 테마 5개에서만 종목을 추출 (19종목/일).
PRD 16-3 본래 의도(Layer 1+3 다중 출처)대로 **거래대금/등락률/외국인 순매수 상위 종목**을 함께 포함하여
**universe 50~100종목 수준**으로 확장 + PRD 4-1/4-4 속성/유동성 필터를 적용한다.

**핵심 의의**: 30건/100건 게이트가 단순 카운터가 아닌 **PRD 시그니처 신호로 추출된 후보 누적**이 되도록.

## 배경

### 현재 한계 (memory/project_closing_bet_system.md Phase 2 옵션 A 완료 시점)
- universe_provider v1 = 스윙 top_themes 5개 → 네이버 크롤링 → 19종목/일
- PRD 16-3 명시 흐름 (14:00 Layer 3 + 14:30 Layer 1 + 15:00 Layer 1+2) 중 **Layer 3 부분만 구현**
- 종가베팅 핵심 시그니처 (거래대금 폭발 / 외국인 매수 우위 / 모멘텀 강세) 종목 누락

### 사용자 결정 (2026-05-04)
"PRD대로 설계된 데이터가 중요하니 B 즉시 진입 + 30건 게이트도 v2 데이터로 채우자"

## 핵심 설계 결정

### 1. 단위 분할 (2단위)
- **단위 2-9a**: `universe_provider_v2.py` — 다중 출처 통합 (스윙 테마 + 거래대금 + 등락률 + 외국인 순매수)
- **단위 2-9b**: PRD 4-1 속성 필터 + 4-4 유동성 필터 — 별도 모듈 / universe_provider_v2 와 통합

### 2. 데이터 출처 (모두 pykrx 가용 — 가용성 사전 점검 완료)
| 시그니처 | pykrx 함수 | PRD 근거 |
|---------|-----------|---------|
| 거래대금 상위 N | `get_market_trading_value_by_date` (당일 종목별 누적) | 16-3 14:00 Layer 3 + 4-4 유동성 |
| 당일 등락률 상위 N | `get_market_price_change_by_ticker` | 5장 Layer 3 모멘텀 |
| 외국인 순매수 상위 N | `get_market_net_purchases_of_equities_by_ticker` | 5장 Layer 1 수급 |
| 시가총액 (필터) | `get_market_cap_by_ticker` | 4-1 시총 500억 미만 제외 |
| OHLCV (52주 고/저) | `get_market_ohlcv_by_ticker` (1일치) + 별도 누적 | 4-1 52주 고점 -30% |

**대안**: KIS API 자체 호출도 가능하지만 pykrx 가 일괄 dataframe 반환이라 효율적. KIS rate_limit 부담 회피.

### 3. universe v2 산출 흐름

```
universe_provider_v2.get_universe()
  ├─ Layer 3: 스윙 top_themes (기존 v1) → 테마 종목 (max 20)
  ├─ Layer 3+: pykrx 거래대금 상위 30 종목
  ├─ Layer 3+: pykrx 당일 등락률 상위 30 종목
  ├─ Layer 1: pykrx 외국인 순매수 상위 30 종목
  ├─ 합집합 + 중복 제거 (대략 60~100종목)
  ├─ PRD 4-1 속성 필터:
  │     - 시총 < 500억 제외
  │     - 주가 < 1000원 제외
  │     - 상한가 종목 제외
  │     - 52주 고점 -30% 초과 하락 제외
  ├─ PRD 4-4 유동성 필터:
  │     - 20일 평균 거래대금 < 50억 제외
  │     - 당일 거래대금 < 100억 제외
  ├─ 스윙 보유 제외 (기존 v1 동일)
  ├─ KIND 시장경보 제외 (severity >= 3, 향후 KindHttpProvider 활성 시)
  ├─ hard_cap (50~100) 적용
  └─ list[ticker] 반환
```

### 4. v1 → v2 전환 전략
- **scheduler.py**: `universe_provider=cb_get_universe_v2`로 교체
- **v1 코드 보존**: `universe_provider.py` 그대로 유지 (롤백 가능, 단위 테스트 PASS 상태 보존)
- **호출 시점**: 같은 잡 (15:10 KST), 같은 인터페이스 (`Callable[[], list[str]]`)

### 5. 백워드 호환
- universe_provider_v2 시그니처는 v1과 동일 (`get_universe() -> list[str]`)
- main_orchestrator / scheduler.py 변경 최소화 (import 만 교체)
- 단위 테스트는 신규 파일 (test_closing_bet_unit_2_9a.py / test_closing_bet_unit_2_9b.py)

### 6. KIS API rate_limit 영향
- pykrx는 KIS와 무관 (KRX 공식 사이트 크롤링)
- KIS 호출 추가 X
- pykrx rate_limit: 자체 throttle 있음, 일 1회 호출은 부담 없음

## 구현 단계

### 단위 2-9a (반나절): universe_provider_v2 다중 출처 통합
- `closing_bet_system/collectors/universe_provider_v2.py` 신규
  - `get_universe_v2()` — 4 출처 합집합 + 중복 제거 + 스윙 보유 제외 + hard_cap
  - 각 출처별 helper 함수 (예외 격리, 빈 결과 폴백)
  - in-memory 캐시 (같은 거래일 1회만 pykrx 호출)
- pykrx import lazy (모듈 로드 시 부담 X)
- 단위 테스트 15+ 시나리오 (4 출처 정상/실패/캐시/중복/스윙보유)

### 단위 2-9b (반나절): PRD 4-1 속성 필터 + 4-4 유동성 필터
- `closing_bet_system/collectors/universe_filters.py` 신규 (또는 v2에 통합)
  - `apply_attribute_filters(tickers)` — 시총/주가/상한가/52주 고점
  - `apply_liquidity_filters(tickers)` — 20일/당일 거래대금
  - 필터별 사유 dict 반환 (rejected_reason 기록용)
- universe_provider_v2 와 파이프라인으로 통합 (`get_universe_v2_filtered()`)
- 단위 테스트 12+ 시나리오 (필터별 정상/예외/경계값)

### 종합: scheduler.py 교체 + 단발 검증
- `scheduler.py`: `universe_provider=cb_get_universe_v2_filtered`
- 단발 트리거 — universe 30~80종목 산출 + 필터 통과 확인
- code-tester 검증 (2단위 종합)

## 변경 파일 목록

| 파일 | 변경 규모 | 단위 |
|---|---|---|
| `closing_bet_system/collectors/universe_provider_v2.py` | 중 (신규, ~300줄) | 2-9a |
| `closing_bet_system/collectors/universe_filters.py` | 중 (신규, ~250줄) | 2-9b |
| `closing_bet_system/main_orchestrator.py` | 미수정 (인터페이스 동일) | - |
| `scheduler.py` | 소 (universe_provider 교체 1줄) | 종합 |
| `scripts/test_closing_bet_unit_2_9a.py` | 중 (신규, 15+ 시나리오) | 2-9a |
| `scripts/test_closing_bet_unit_2_9b.py` | 중 (신규, 12+ 시나리오) | 2-9b |
| `docs/improvements/change_log.md` | 1줄 추가 | 종합 |
| `requirements.txt` | pykrx 명시 (이미 사용 중이라 검토만) | - |

## 접근 방식
- **단위별 사용자 확인**: 2-9a 완료 → 2-9b 진입
- **자동 매매 위험 0**: 데이터 수집/필터링만. universe 산출 → DB 기록 → 알림 (현재 알림 임계값 7점 미달이라 알림 0건 유지)
- **롤백 보장**: v1 코드 보존 + scheduler.py 한 줄 교체로 즉시 복원 가능

## 롤백 계획
- scheduler.py 한 줄 교체로 v1 즉시 복원
- DB 변경 없음 (universe 출처만 변경, candidates 스키마 동일)
- 롤백 트리거:
  - universe v2 폭주 (>200종목) → KIS API 부담 + 처리 시간 증가
  - pykrx 호출 30초+ 지연 → 15:10 잡 timeout
  - 필터 오작동으로 정상 종목 잘못 제외

## 완료 기준 (2단위 종합)

| 지표 | 목표 |
|---|---|
| universe 산출 종목 수 | 30~100 (기존 19에서 확장) |
| pykrx 호출 시간 | < 10초 (4 함수 합산) |
| 4-1 속성 필터 통과율 | 70~90% (시총 500억 미만 / 상한가 / 52주 고점 -30% 자연 분포) |
| 4-4 유동성 필터 통과율 | 60~80% (당일 거래대금 100억 미만 자연 분포) |
| 단위 테스트 | 27+ 시나리오 PASS (15+ + 12+) |
| code-tester | 심각 0 |
| 알림 발생 | Layer 1 가중치 0인 동안은 0 (정상) |

## 후속 작업 (별도 단위)
- 보호예수 D-7 필터 (SEIBro 데이터, PRD 4-1) — 데이터 출처 별도 확보 필요
- 호가 스프레드 비정상 필터 (PRD 4-4) — orderbook 데이터 며칠 누적 후
- 52주 고점 신고가 시그니처 (Layer 3) — 1년치 OHLCV 누적 / pykrx 별도 호출

## 새 대화 시작 가이드
- 새 대화에서: `/resume` 호출 → 이 PLAN/CONTEXT/CHECKLIST 자동 로드
- 첫 작업: 단위 2-9a 시작 (universe_provider_v2.py)
- 진행 패턴은 Phase 2 옵션 A 단위들과 동일 (코드 → py_compile → 단위 테스트 → code-tester)
