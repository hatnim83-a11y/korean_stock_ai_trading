# PLAN: 종가베팅 Phase 1 이월 항목 (closing-bet-phase1-carryover)

## 목표
종가베팅 Phase 1 (알림형) 9단위는 완료되었으나, **`scheduler.py:294-301` placeholder providers** 때문에 실제 동작은 무동작 상태다. 6개 이월 항목을 완료하여 종가베팅 시스템이 실제 후보를 수집·점수화·알림하도록 만든다.

## 배경

### 현재 상태 (`scheduler.py:294-301`)
```python
self._closing_bet_orch = MainOrchestrator(
    universe_provider=lambda: [],          # ❌ 빈 리스트 → 파이프라인 무동작
    market_data_provider=lambda: {},        # ❌ 빈 dict → 외부리스크 룰 비활성
    name_lookup=lambda t: "(미상)",         # ❌ 종목명 미상
)
# label_provider=None → run_label_yesterday 무동작
```

### 미구현 6개 (memory/project_closing_bet_system.md:64-70)
1. `universe_provider` 실제 구현 (PRD 5-Layer 3 테마 모멘텀 후보)
2. `market_data_provider` 실제 구현 (미국선물/V-KOSPI/USD-KRW/KOSPI HTTP 수집)
3. `name_lookup` 실제 구현 (KIS API 또는 종목 마스터)
4. `label_provider` 실제 구현 (T+1 09:30 KIS get_daily_price 첫 행)
5. `weekly_loss_limit` 검사 추가 (settings.yaml 정의됨, fund_guard 미구현)
6. 1-7 부모 클래스 결합 약화 (telegram_client.py:78 silent break 위험)

### 영향 범위
- **DB 변경 없음** (기존 closing_bet.db v1 스키마 그대로 활용)
- **신규 KIS API 호출 추가**: name_lookup (캐시 이용), market_data 4개 지수 (KOSPI/V-KOSPI는 KIS, 미선물/USD-KRW는 외부 또는 후속 이월)
- **scheduler.py 수정**: providers 4개 lambda → 실 함수 교체

## 핵심 설계 결정

### 1. 단위 분할 (5개 단위, 의존성 순)
- **단위 A** (가장 가벼움): `name_lookup` 실 구현 + `telegram_client.py:78` 결합 약화
- **단위 B** (가장 핵심): `universe_provider` 실 구현 — 후보가 흘러야 알림 발생
- **단위 C** (외부 리스크 활성화): `market_data_provider` 실 구현 — KIS 가용 지수 우선, 미선물/USD-KRW는 placeholder 유지 또는 yfinance 폴백
- **단위 D**: `label_provider` 실 구현 — KIS get_daily_price 첫 행
- **단위 E**: `weekly_loss_limit` 검사 추가 — fund_guard 보강

각 단위 완료 후 사용자 확인을 받고 다음 단위 진행한다.

### 2. 단위 A 설계
- **name_lookup**: `KISApi.get_stock_name()` (네이버 금융 + 내부 캐시) 활용. closing_bet_system 측 wrapper 함수로 노출.
- **telegram_client 결합 약화**: 현재 `_notifier_instance.bot_token = None / chat_id = None / _enabled = False / base_url = ""` 로 부모 클래스 내부 속성 4개를 직접 변경하고 있어, 부모 클래스 리팩터 시 silent break 위험. 대신 **스윙 봇 토큰을 chat_id 인자로 절대 전달하지 않는** 명시적 분기로 변경:
  - 토큰/chat_id 둘 중 하나라도 None이면 `TelegramNotifier` 생성을 안 하고 **NoOp 더미 객체**를 반환

### 3. 단위 B 설계
- **데이터 출처**: `database.get_top_themes(target_date, count=5)` — 스윙 시스템이 화요일에 선정하고 매일 갱신하는 주간 테마. 종가베팅이 이를 read-only 활용.
- **theme.stocks 컬럼**: `_enrich_theme_stocks()`로 화요일 채워진 theme["stocks"]는 list[str] (코드만). 단, DB에 저장된 형태 확인 필요 (themes.url 컬럼은 v12에 있지만 stocks 컬럼은 없음). 대안: theme["url"]에서 `crawl_naver_theme_stocks` 재호출 → 무겁다. **권장**: stocks가 DB에 없으면 theme["url"]을 사용해 매일 15:10 직전 1회 크롤링 후 캐시. 또는 더 간단히 **screening_log/portfolio 종목과 분리하기 위해 일단 top_themes의 url로 1회 크롤**.
- **결정**: `database.get_top_themes()`로 url 가져오고, 각 url에서 `crawl_naver_theme_stocks()` 호출 → 상위 N개 종목코드 추출 (max 20개 제한). 이미 스윙 보유 중인 종목은 fund_guard에서 차단되므로 universe 단계 필터는 선택적이다.
- **위치**: `closing_bet_system/collectors/universe_provider.py` 신규 — 함수 `get_universe() -> list[str]`
- **scheduler.py**: `universe_provider=universe_provider.get_universe`로 교체

### 4. 단위 C 설계
- **KOSPI 등락률**: `KISApi.get_index_price("0001")` — 이미 가용
- **V-KOSPI**: KIS API에 V-KOSPI(`1010` 또는 변동성 지수 코드) 지원 여부 확인 필요. 미지원 시 `yfinance` 또는 placeholder
- **미국선물/USD-KRW**: KIS 미지원 (해외선물). yfinance로 폴백 (`ES=F`, `KRW=X`) 또는 첫 단계는 placeholder None 유지
- **위치**: `closing_bet_system/collectors/market_data_provider.py` 신규
- **Phase 1 결손 정책**: overnight_risk_filter는 None 키를 허용 (해당 룰 비활성). 따라서 가용한 것부터 채우면 점진적 활성화 가능.

### 5. 단위 D 설계
- **요구사항**: T+1 10:00 잡(`run_label_yesterday`)이 어제 recommended/entered 후보 ticker별로 `label_provider(ticker) -> dict` 호출. 키: `next_open_pct`, `next_morning_high_pct`, `next_morning_low_pct`, `label_gap_up`, `label_morning_exit`, `label_stop_risk`, `label_net_ev_positive`
- **데이터 출처**: `KISApi.get_daily_price(ticker, period="D", count=2)` 첫 행 = 오늘 OHLC. 어제 종가 대비 % 계산.
- **어제 종가**: candidate.entry_price가 있으면 그것을, 없으면 daily_price[1] (어제 행).
- **라벨 정의** (PRD 9-2):
  - `label_gap_up`: open >= 어제 종가 × 1.005 (+0.5% 이상)
  - `label_morning_exit`: high >= 어제 종가 × 1.012 (+1.2%, 코스피 비용 차감 후 양수)
  - `label_stop_risk`: low <= 어제 종가 × 0.985 (-1.5% 손절 트리거)
  - `label_net_ev_positive`: morning_high가 cost_engine.gross_breakeven 초과
- **위치**: `closing_bet_system/collectors/label_provider.py` 신규
- **scheduler.py**: `register_jobs(scheduler)` 호출 시 `run_label_yesterday`에 callable 주입 — 현재 인자가 없으므로 **MainOrchestrator 생성자에 label_provider 추가** 또는 register_jobs 시점 partial 적용

### 6. 단위 E 설계
- **요구사항**: closing_bet.db candidates 테이블에서 최근 7일 (영업일 5일) net_pnl_pct 합계 ≤ -5% 시 매매 중지
- **계산**: `WHERE candidate_status='entered' AND exit_time >= today-7 AND exit_time IS NOT NULL`
- **위치**: `fund_guard.py` 신규 메서드 `_check_weekly_loss()` 추가, `allow_order()` 8번째 검사로 추가
- **TOCTOU**: `_fetch_db_state()`에 weekly_pnl 쿼리 통합

### 7. 검증 방식
각 단위마다:
1. py_compile 통과
2. 단위 테스트 (mock 의존성)
3. code-tester 에이전트 검증 (CLAUDE.md 필수)
4. scheduler.py 통합 후 import 성공 확인
5. **종합 검증**: 단위 E 완료 후 placeholder 1개 시점에 `python main.py --manual --test --real` 또는 단발 트리거로 alert 1건 발생 확인

## 구현 단계

### 단위 A (반나절): name_lookup + telegram_client 결합 약화
- `closing_bet_system/infra/name_lookup.py` 신규 — KIS get_stock_name 래핑 + 캐시
- `telegram_client.py` 수정 — 토큰/chat_id 미설정 시 NoOp 더미 객체 반환
- scheduler.py: `name_lookup=lambda t: "(미상)"` → `name_lookup=name_lookup.get_name`
- 단위 테스트 + code-tester
- **Gate**: import OK + name 조회 PASS + 미설정 시 텔레그램 send_* 호출 무동작 확인

### 단위 B (반나절): universe_provider
- `closing_bet_system/collectors/universe_provider.py` 신규 — get_universe() 구현
- 의존성: `database.get_top_themes()` + `crawlers.crawl_naver_theme_stocks()` + `database.get_portfolio()` (스윙 보유 제외)
- 캐시: 같은 거래일 1회만 크롤링 (in-memory cache)
- scheduler.py: `universe_provider=lambda: []` → `universe_provider=universe_provider.get_universe`
- 단위 테스트 + code-tester
- **Gate**: 단발 호출 시 `len(universe) > 0` 확인 (장중 시간이 아니어도 DB에 테마가 있으면 OK)

### 단위 C (반나절): market_data_provider
- `closing_bet_system/collectors/market_data_provider.py` 신규 — get_market_data() 구현
- KOSPI: `KISApi.get_index_price("0001")` → kospi_change_pct (소수)
- V-KOSPI: KIS 지원 여부 점검 후 가능하면 사용, 아니면 None
- 미선물/USD-KRW: yfinance 폴백 (별도 단위 권장 시 placeholder)
- KOSPI 200MA / 외국인 5일 누적: Phase 2 (placeholder None)
- scheduler.py: `market_data_provider=lambda: {}` → 실 함수
- 단위 테스트 + code-tester
- **Gate**: kospi_change_pct 비-None + overnight_risk_filter 결손 정책 확인

### 단위 D (반나절): label_provider
- `closing_bet_system/collectors/label_provider.py` 신규 — get_label(ticker) 구현
- KIS get_daily_price(ticker, count=2)로 오늘/어제 OHLC
- 비용 차감 후 라벨 4개 계산 (`CostSlippageEngine` 재사용)
- MainOrchestrator 생성자 또는 register_jobs에 label_provider 주입 경로 마련
- scheduler.py: orchestrator 생성 시 label_provider 인자 추가
- 단위 테스트 + code-tester
- **Gate**: candidate 1건 mock으로 라벨 dict 반환 확인

### 단위 E (반나절): weekly_loss_limit
- `fund_guard.py` 수정 — `_check_weekly_loss()` + `allow_order()` 통합
- settings.yaml `fund.weekly_loss_limit` 사용
- `_fetch_db_state()`에 weekly_pnl 쿼리 추가
- 단위 테스트 + code-tester
- **Gate**: 누적 손실 -5% 도달 시 차단, 미만 시 통과

### 종합 검증 + 문서 갱신
- code-tester 4파일(name_lookup, universe_provider, market_data_provider, label_provider, fund_guard) 일괄 검증
- `docs/improvements/change_log.md` 1줄 추가
- `memory/project_closing_bet_system.md` 갱신 (이월 항목 → 완료 표시)
- `memory/MEMORY.md` 인덱스 갱신
- 3문서 active → completed/20260504_closing-bet-phase1-carryover/ 이동

## 변경 파일 목록

| 파일 | 변경 규모 | 종류 |
|---|---|---|
| `closing_bet_system/infra/name_lookup.py` | 소 (단순 래퍼 + 캐시) | 신규 |
| `closing_bet_system/infra/telegram_client.py` | 소 (NoOp 더미 분기) | 수정 |
| `closing_bet_system/collectors/universe_provider.py` | 중 (테마→종목 매핑) | 신규 |
| `closing_bet_system/collectors/market_data_provider.py` | 중 (KIS+yfinance 통합) | 신규 |
| `closing_bet_system/collectors/label_provider.py` | 중 (KIS daily + 라벨 계산) | 신규 |
| `closing_bet_system/infra/fund_guard.py` | 소 (weekly_loss 메서드 + 통합) | 수정 |
| `closing_bet_system/main_orchestrator.py` | 소 (label_provider 주입 인자) | 수정 |
| `scheduler.py` | 소 (4 providers 교체) | 수정 |
| `docs/improvements/change_log.md` | 1줄 | 수정 |
| `memory/project_closing_bet_system.md` | 갱신 | 수정 |

## 접근 방식
- **단위별 사용자 확인**: 각 단위 완료 후 `[OK]` 신호 받고 다음 진행
- **호환성 보장**: 모든 신규 함수는 default 인자 + 예외 시 안전한 폴백 (빈 리스트/dict)
- **CLAUDE.md 규칙**: 코드 변경 후 code-tester 에이전트 검증 필수
- **rate limit 고려**: KIS API 호출 추가는 collector 내부에서 throttle (universe 매일 15:10 1회)

## 롤백 계획
- **각 단위 독립 커밋**: `git revert <hash>` 가능
- **scheduler.py 한 줄 롤백**: lambda placeholder로 즉시 복원 (`universe_provider=lambda: []` 등)
- **롤백 트리거**:
  - universe 크롤링 실패로 매일 에러 폭증
  - market_data 호출이 외부 리스크 필터를 잘못 트리거 (skip_today=True 비정상)
  - label_provider 폭주로 KIS rate_limit 초과
  - weekly_loss_limit 오작동으로 정상 거래 차단

## 완료 기준 (단위 E 완료 시점)

| 지표 | 목표 |
|---|---|
| `scheduler.py:294-301` placeholder lambda | 0개 (모두 실 함수로 교체) |
| 종가베팅 잡 3건 (15:10/15:35/10:00) 실제 동작 | universe 비-empty 시 alert 발송 |
| KIS API 추가 호출량 | 일일 평균 < 50건 (universe 1회 + market_data 1회 + label 5건 미만) |
| code-tester 검증 | 5파일 모두 심각 0건 |
| `docs/improvements/change_log.md` | 1줄 추가 |
| 3문서 archive | active/ → completed/20260504_*/ |

## 후속 작업 후보 (별도 트래킹)
- **yfinance 의존성 추가**: 미선물/USD-KRW 수집 정식화 (단위 C에서 placeholder 유지 시)
- **테마-종목 매핑 DB 컬럼화**: themes.stocks JSON 컬럼 추가로 매일 크롤링 회피
- **Phase 2 본격 진입**: 2-1 orderbook_collector ~ 2-8 100건 게이트
