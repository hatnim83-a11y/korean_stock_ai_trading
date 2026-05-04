# 종가베팅 시스템 — CONTEXT.md

> 구현자(코더 에이전트, 테스터, 차후 새 대화 재개 시)가 코드를 손대기 전에 반드시 읽어야 할 컨텍스트.

---

## 변경 이유

기존 스윙 시스템(테마 모멘텀 + 트레일링)에 더해 **단기 종가베팅 전략**을 별도 시스템으로 추가한다. 14:30~15:30 매수 → 익일 09:30~10:30 매도, 16~18시간 보유, 비용 차감 후 +1.0~+2.5% 목표.

**핵심 철학**: "다음날 오를 종목을 맞추는 추천기"가 아니라 **"비용 차감 후 EV 양수 상황만 허락하는 시스템"**.

---

## 현재 코드 상태 (기존 인프라)

### 재사용할 모듈 (위치 + 핵심 시그니처)

```python
# KIS 시세/수급/지수
from modules.stock_screener.kis_api import KISApi  # 라인 57
kis = KISApi(is_mock=True)  # 모의/실전 분기 지원
kis.get_current_price(stock_code)         # 라인 422
kis.get_daily_price(stock_code, period="D", count=60)  # 라인 508
kis.get_investor_trading(stock_code, days=5)  # 라인 594
kis.get_index_price(index_code)           # 라인 294 (KOSPI/KOSDAQ)

# KIS 주문
from modules.trading_engine.kis_order_api import KISOrderApi  # 라인 86
order = KISOrderApi(is_mock=True)
order.buy_limit_order(code, qty, price)
order.sell_market_order(code, qty)
order.get_orderable_cash()

# 토큰 공유: KISApi._shared_token (클래스 변수, KISOrderApi도 동일 인스턴스 공유)

# 텔레그램
from modules.reporter.telegram_notifier import TelegramNotifier  # 라인 37
notifier = TelegramNotifier()  # env에서 TELEGRAM_BOT_TOKEN/CHAT_ID 로드
notifier.send_message(text, parse_mode="Markdown")
# ⚠️ 0-B 단계에서 부모 클래스 __init__에 (bot_token, chat_id) 인자 주입 필요

# DB 마이그레이션 패턴 (database.py:150 _migrate())
# - schema_version 테이블
# - 마이그레이션 튜플 배열: [(N, 설명, _migrate_vN), ...]
# - 멱등 보장, _has_column() 활용
# 종가베팅 db.py에 동일 패턴 적용 (별도 DB 파일이지만 같은 구조)

# 시장 위기 방어
from modules.market_guard import MarketGuard, MarketStatus  # 라인 31
status, info = MarketGuard().check()  # CRISIS/DANGER/CAUTION/NORMAL

# DART
from modules.ai_verifier.dart_api import (
    POSITIVE_KEYWORDS, NEGATIVE_KEYWORDS, fetch_dart_disclosures
)  # 0-A에서는 사용 X, 1-5a에서 활용

# 시간 유틸 (config.py)
from config import KST, now_kst, is_trading_day  # 라인 25, 28, 41

# 백테스트 데이터 로더
from modules.backtester.data_loader import ...  # 0-C에서 활용
# ⚠️ 시뮬 엔진은 재사용 X (테마 모멘텀 전제) - 별도 경량 스크립트
```

### KIS 토큰 충돌 주의

- `KISApi._shared_token` 클래스 변수로 공유. **앱키당 1분 1회 발급 제한**.
- 종가베팅도 같은 KIS 계정 → **같은 _shared_token 사용 → 같은 프로세스 권장**.
- 분리 프로세스로 가면 토큰 동시발급 충돌 위험.

### 시간 충돌 회피 (P2-7)

| 스윙 잡 | 시간 | 종가베팅 잡 | 시간 |
|---|---|---|---|
| 보유기간 매도 | 09:15 | 익일 매도 | **09:30 이후** (변경) |
| 매수 | 09:25 | 매수 | 14:30~15:28 (충돌 X) |
| 일일 스냅샷 | 15:35 | 마감 처리 | 15:30~15:35 (사실상 동시) ⚠️ |
| | | 야간 DART | 22:00 (충돌 X) |

**조치**: 익일 매도 09:30 이후 시작. 단, 시가 -1% 이하 갭다운만 09:00 손절 예외.

---

## DB 스키마 핵심 스니펫 (0-A에서 구현)

```sql
-- candidates: 후보 마스터 (모든 후보 4 status 저장)
CREATE TABLE candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    candidate_status TEXT NOT NULL,  -- recommended/entered/rejected_filter/rejected_manual
    rejection_reason TEXT,
    layer1_score INTEGER, layer2_score INTEGER, layer3_score INTEGER,
    external_risk_score INTEGER, total_score INTEGER,
    entry_price REAL, entry_amount REAL, entry_time TIMESTAMP,
    exit_price REAL, exit_time TIMESTAMP,
    buy_commission REAL, sell_commission REAL, transaction_tax REAL,
    estimated_slippage REAL, net_pnl_pct REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- candidate_features: 진입 시점 피처 스냅샷
CREATE TABLE candidate_features (
    candidate_id INTEGER PRIMARY KEY,
    -- Layer 1 수급
    inst_net_buy_estimated REAL, foreign_net_buy_3d REAL,
    program_net_buy_change REAL, closing_flow_concentration REAL,
    -- Layer 2 가격/거래량
    close_strength REAL, upper_shadow_atr REAL,
    last_30min_vwap_position REAL, closing_buy_sell_ratio REAL,
    volume_surprise REAL, atr_overheat REAL,
    -- Layer 3 모멘텀
    days_from_52w_high INTEGER, relative_strength_5d REAL, theme_leadership_rank INTEGER,
    -- 시장 레짐
    kospi_above_200ma BOOLEAN, vkospi REAL,
    foreign_5d_cumulative REAL, us_futures_change REAL, usd_krw_change REAL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);

-- candidate_labels: T+1 09:30 데이터로 사후 채움
CREATE TABLE candidate_labels (
    candidate_id INTEGER PRIMARY KEY,
    next_open_pct REAL, next_morning_high_pct REAL, next_morning_low_pct REAL,
    label_gap_up BOOLEAN, label_morning_exit BOOLEAN,
    label_stop_risk BOOLEAN, label_net_ev_positive BOOLEAN,
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);

-- flow_data_reliability: 추정값 vs 확정값 추적 (Layer 1 활성화 트리거)
CREATE TABLE flow_data_reliability (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date DATE NOT NULL, ticker TEXT NOT NULL,
    inst_estimated REAL, inst_confirmed REAL,
    foreign_estimated REAL, foreign_confirmed REAL,
    inst_direction_match BOOLEAN, foreign_direction_match BOOLEAN,
    UNIQUE(trade_date, ticker)
);
```

---

## 영향 범위

- **신규 생성**: `closing_bet_system/` 전체 + `data/closing_bet.db`
- **수정 (역호환 유지)**: `modules/reporter/telegram_notifier.py` (0-B 단계)
- **read-only 참조만**: 위 KIS/MarketGuard/DART/scheduler/config/database 모듈

스윙 시스템 영향 최소화. 공유 모듈 수정은 `telegram_notifier.py` 한 곳뿐이며 인자 미지정 시 기존 env 폴백.

---

## 과거 버그/주의점

### 시간대 (반드시!)
- 서버는 **UTC**. `datetime.now()` ≠ KST.
- `from config import now_kst` 사용. CronTrigger에 `timezone="Asia/Seoul"` 명시.

### KIS API 응답 파싱
- `_safe_int()`/`_safe_float()` 패턴 사용 (빈 문자열 방어).
- pandas 값 → float 변환 전 `pd.isna()` 체크.

### MCP SQLite 경로 인자
- 위치 인자로 DB 경로 전달 (--db-path 무효).
- 종가베팅 DB는 `data/closing_bet.db`.

### 토큰 1분 제한
- KISApi._shared_token 사용 — 같은 프로세스에서만 공유 가능.

### `database.py:150` 마이그레이션 패턴
- `_migrate()` 호출은 `init_tables()`에서 자동.
- 백업: 자동 (WAL 포함). **신규 빈 DB는 백업 스킵** (current==0 조건).
- 멱등 보장: `IF NOT EXISTS`, `_has_column()` 사용.

### candidates 테이블 UNIQUE 미정의 — 의도적
- `(trade_date, ticker)` 에 UNIQUE 없음. **같은 날 같은 종목이 여러 status로 기록 가능**:
  - 예: 14:30 'recommended' → 15:15 'rejected_filter' (점수 미달)
  - 예: 시장 변화로 재진입 후보가 됐을 때
- Phase 1 candidate_logger에서 status 변경은 INSERT 새 행, UPDATE 기존 행 **둘 다 지원** 필요. 정확한 정책은 1-6에서 결정.

---

## 현재 상태 (2026-05-04)

### Phase 1 (알림형) 9/9 단위 모두 완료 + main.py 통합 완료

**완료 모듈 (200+ 테스트 PASS, 모두 code-tester 통과)**:
- 0-A: 디렉토리 + DB 스키마 v1 + settings.yaml
- 0-B: wrapper 4종 + fund_guard 미들웨어
- 0-C/D: Pre-Phase 1 Layer 2 sanity check 백테스트 (19종목 × 3년)
- 1-1 cost_slippage_engine: PRD 7-2 순수익률
- 1-2 kis_intraday_flow_collector: Layer 1 4지표 (가용 2)
- 1-3 kis_price_volume_collector: Layer 2 6지표 (가용 4)
- 1-4 signal_score_engine: PRD 6-1 11점 + atr_overheat>1.8 하드 필터
- 1-5a dart_disclosure_collector: PRD 8-2 키워드 매트릭스
- 1-5b overnight_risk_filter: PRD 4-2/4-3 + DART 통합
- 1-6 candidate_logger: 라이프사이클 + 1-9 검증 헬퍼
- 1-7 telegram_review_bot: 알림형 + 신규 봇 분리
- 1-8 main_orchestrator: 통합 + APScheduler 3 잡

**main.py 통합** (`scheduler.py:_setup_closing_bet_jobs`):
- `MainOrchestrator(universe_provider=lambda:[], market_data_provider=lambda:{}, name_lookup=lambda t:"(미상)")`
- placeholder providers → 잡 등록되지만 무동작 (안전)
- systemd 재시작 시 자동 적용 (사용자 결정)

### Phase 2 진입 전 처리 권장 (이월 항목)

1. **universe_provider 실제 구현** — PRD 5-Layer 3 테마 모멘텀 후보 산출
2. **market_data_provider 실제 구현** — 미국선물/V-KOSPI/USD-KRW/KOSPI HTTP 수집
3. **name_lookup 실제 구현** — KIS API 또는 종목 마스터
4. **label_provider 실제 구현** — T+1 09:30 KIS get_daily_price 첫 행 → label_dict
5. **weekly_loss_limit 검사** — settings.yaml 정의됨, fund_guard 미구현
6. **1-7 부모 클래스 결합 약화** — telegram_client.py:78 silent break 위험

### Phase 1-9 검증 (운영 시작 후)

- 추천 후보 30건 누적 (운영 점검 게이트)
- 15영업일 이상 경과
- 서로 다른 종목 20개 이상
- CRISIS/DANGER 일에 진입 0건 (필터 작동)
- **30건 운영 점검 게이트 자동 리포트 생성** → 사용자 검토

### 최근 이슈 fix (2026-05-04)

- `db.py` `get_cursor` rollback 범위 확장 (`sqlite3.Error` → `Exception`) — LookupError 시 트랜잭션 정리 보장
- `overnight_risk_filter.py` `decision_reason` DART 중복 prefix 제거
- "조사" 단독 키워드 제거 → 구체 패턴 (FP 방지: 기업실태조사/시장조사 등 5건)
- `atr_overheat_value` 정규화 (DB REAL 컬럼 안전성)
- 텔레그램 봇 토큰/chat_id .env 주입 + 실제 sendMessage 1건 발송 확인

---

## 다음 대화에서 이어가기

새 대화 시작 시 첫 발화로 다음 중 택 1:

### 옵션 1: Phase 2 진입 (반자동 시스템)
> "종가베팅 Phase 2 시작. CONTEXT.md 참고해서 universe_provider 구현부터 시작해줘"
- Phase 2 첫 단위: 2-1 kis_orderbook_collector (호가 데이터)
- 또는 universe_provider 우선 구현 (Phase 1 활성화 → 1-9 게이트 데이터 누적 시작)

### 옵션 2: Phase 1-9 운영 시작
> "현재 main.py 통합된 종가베팅 시스템 활성화. universe_provider 실 구현해서 1-9 게이트 데이터 수집 시작"
- placeholder universe → 실제 종목 리스트 connector
- systemd 재시작 → 15:10 첫 파이프라인 실행
- 30건 누적까지 매일 모니터링

### 옵션 3: 다른 작업
> 종가베팅 외 다른 우선순위 작업이 있으면 그걸 먼저

---

## 0-B 잔여 개선 항목 (Phase 1 진입 전 처리 권장)

code-tester 0-B 검토에서 발견된 항목 중, 0-B에서 즉시 수정하지 않고 다음 단위로 이월한 항목.

1. **`weekly_loss_limit` 미구현** — settings.yaml `fund.weekly_loss_limit: -0.05` 가 정의되어 있으나 fund_guard에서 미검사. **Phase 1 1-9 검증 단계 또는 Phase 2 entry_executor에서 추가** 필수. 운영자 오인 방지를 위해 fund_guard.py docstring에는 "Phase 1 추가 예정" 명시 완료.

2. **TelegramNotifier 부모 클래스 결합 약화** — `telegram_client.py:78` 가 `_enabled`/`bot_token`/`base_url` 속성을 직접 수정해 폴백 차단. 향후 부모 클래스 변경 시 silent break 위험. **Phase 1 1-7 (telegram_review_bot) 작업 시 부모 클래스에 `disable_settings_fallback: bool = False` 파라미터 추가하는 리팩터** 검토.

3. **swing_db_reader 실패 정책** — `get_swing_holding_codes()` 가 실패 시 빈 set 반환 (이중 노출 방어 약화). settings.yaml에 `fund.swing_duplicate_check_strict: bool` 옵션 추가하고 strict 모드 시 `None` 반환 → fund_guard 보수적 차단. **Phase 1 1-5b (overnight_risk_filter) 또는 1-6 (candidate_logger) 시점**.

4. **`_get_use_mock()` 매 싱글톤 생성 시 settings 재로드** — kis_client.py 모듈 레벨 캐시 추가. **Phase 1 1-2 (kis_intraday_flow_collector) 또는 1-3 시점에 마이너 fix**.

5. **`get_held_stock_codes()` 사용 시점 명시 부족** — kis_client.py에 "Phase 2 entry_executor에서 활용 예정" 주석 추가. **Phase 1 마무리 또는 Phase 2 진입 시**.

이 5개 항목은 모두 **종가베팅 시스템이 실거래로 전환되기 전(Phase 2-4 entry_executor)**까지 처리하면 안전하다. 0-C/0-D는 백테스트 경로라 영향 없음.
