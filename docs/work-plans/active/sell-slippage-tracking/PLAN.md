# PLAN: 매도 슬리피지 측정 시스템 (sell-slippage-tracking)

## 목표
매도 슬리피지(`trades.slippage`) 컬럼이 95% NULL인 현 상태를 해소하여, **5개 매도 경로 모두**에서 매도 직전 매수 1호가(reference price) 기준 슬리피지를 일관 기록한다. 공격적 지정가 매수(2026-04-16 배포) 효과 평가의 매도 측 카운터파트 지표를 확보한다.

## 배경

### 2026-05-01 monthly 분석 식별
- 매도 55건 중 52건(95%) `slippage` NULL
- 측정된 3건도 `0.0` (시장가 매도 → `order_price=0` → 분모 빔)
- 매수 슬리피지는 정상 기록 중 (공격적 지정가 → expected_price 대비)

### 근본 원인 (이중)
1. **시장가 매도 + `order_price=0`**: `trading_engine._save_trades` 매도 분기의 슬리피지 식 `(filled_price - order_price) / order_price`가 분모 0으로 무의미
2. **5경로 모두 `_save_trades` 우회**:
   - `portfolio_monitor_v2._close_position_in_db` / `_save_partial_sell_to_db`: 직접 `db.save_trade` (slippage 필드 미포함)
   - `main.py`의 보유기간/midweek profit/midweek loss: `execute_sell_orders(save_to_db=False)` 후 자체 `db.save_trade` (slippage 필드 미포함)

## 핵심 설계 결정 (사용자 승인됨)

### 1. Reference price = 매수 1호가(`bid1`)
- 시장가 매도는 매수 호가에서 체결되므로 가장 자연스러움
- 폴백 순서: `bid1` → 호가 조회 실패 시 `current_price` (KIS API 현재가) → 마지막 폴백 `buy_price`
- 호가 조회 실패는 `reference_source` 필드(또는 로그)로 식별 가능하게

### 2. 중앙화 캡처 위치 = `trading_engine` 진입점
- `execute_sell_orders` / `execute_stop_loss` / `execute_take_profit` 3개 메서드에서 `sell_market_order` **직전** `inquire_asking_price()` 호출
- 결과 dict에 `reference_price` + `slippage` 필드 채워 반환
- 외부 호출처(monitor 2곳, main.py 3곳)는 result에서 추출만 하면 됨

### 3. Slippage 계산 공식
```python
slippage = round((filled_price - reference_price) / reference_price * 100, 4)
```
- 단위: `%`
- **음수 = 불리한 체결** (매수 1호가 아래로 체결, 시장가 매도의 전형적 결과)
- **양수 = 유리한 체결** (호가 흔들림 시 드물게 발생)
- `reference_price <= 0`이면 None 저장 (계산 불가)

## 구현 단계

### Phase 1: 인프라 — `trading_engine` 진입점 캡처 (반나절)
- `trading_engine.execute_sell_orders`: orders 루프 진입 시 종목별 reference price 캡처 → result에 `reference_price` 추가
- `trading_engine.execute_stop_loss`: `sell_market_order` 직전 `inquire_asking_price` 호출, result에 `reference_price` 추가
- `trading_engine.execute_take_profit`: 동일
- `trading_engine._save_trades` 매도 분기: `order_price=0` 시 `reference_price`를 분모로 사용하도록 식 수정
- 호가 조회 실패 폴백 헬퍼: `_capture_sell_reference_price(stock_code, fallback_price) -> tuple[int, str]` (price, source)
- code-tester 에이전트 검증

**Gate**: `trading_engine` 단독으로 reference_price/slippage가 result에 포함됨을 확인 (Mock 시나리오)

### Phase 2: 5경로 통합 (반나절)
- **monitor 2경로**:
  - `_close_position_in_db(pos, reason, sell_price, sell_shares=0, slippage=None)` 시그니처 확장
  - `_save_partial_sell_to_db(pos, sell_shares, stage, sell_price, slippage=None)` 시그니처 확장
  - `_execute_stop_loss`, `_execute_partial_sell` 호출부에서 result["slippage"] 추출 후 전달
- **main.py 3경로**:
  - `run_hold_period_sells`, `_execute_midweek_profit_sells`, `_execute_midweek_loss_sells`
  - result["orders"] 순회 시 `order.get("slippage")` 추출 후 `db.save_trade({..., "slippage": slippage})` 전달
- code-tester 에이전트 검증

**Gate**: 5경로 모두 코드 path에 slippage 인자가 흐름 → DB 저장 직전까지 검증

### Phase 3: 시뮬레이터 + 단위 검증 (반나절)
- `scripts/test_sell_slippage.py` 신규 — 5경로 시뮬레이션
  - 시나리오 A: 정상 호가 → slippage 음수 (시장가 매도 정상)
  - 시나리오 B: 호가 조회 실패 → 현재가 폴백 → slippage 계산 OK
  - 시나리오 C: 호가/현재가 모두 0 → slippage None
  - 시나리오 D: 부분 매도 (분할 익절) → slippage 정상
- py_compile 4개 파일 통과
- `MockOrderApi`의 `inquire_asking_price` 시뮬레이터 활용

### Phase 4: 모의 검증 + 배포 (반나절)
- `python main.py --manual --test --real` 1회 매도 트리거 → DB `trades.slippage` 비-NULL 확인
- 장 마감(15:30) 이후 `sudo systemctl restart trading_system`
- 이중 실행 체크: `ps aux | grep main.py | grep -v grep`

### Phase 5: 1주 실전 관찰
- 5/4(월) ~ 5/8(금) 매도 발생 시마다 MCP SQLite 집계
- 경로별(stop_loss / take_profit / max_hold / midweek_*) slippage 분포 산출
- 이상치(|slippage| > 2%) 빈도 모니터링
- W19 weekly 보고서에 매도 slippage 섹션 신규 추가

## 변경 파일 목록

| 파일 | 변경 규모 | 종류 |
|---|---|---|
| `modules/trading_engine/trading_engine.py` | 중 (3 메서드 + `_save_trades` 보정 + 헬퍼) | 수정 |
| `modules/trading_engine/portfolio_monitor_v2.py` | 중 (2 함수 시그니처 + 호출 5곳) | 수정 |
| `main.py` | 소 (3경로 save_trade에 slippage 인자 추가) | 수정 |
| `modules/trading_engine/kis_order_api.py` | 미미 (`inquire_asking_price` 이미 존재, MockOrderApi 검토만) | 검토 |
| `scripts/test_sell_slippage.py` | 중 (5경로 시뮬) | 신규 |
| `docs/improvements/change_log.md` | 1줄 추가 | 수정 |

## 접근 방식
- **중앙화 캡처**: 5경로 호출처 변경 최소화 — trading_engine만 한 번 손대면 result에 슬리피지 포함됨
- **Phase별 사용자 확인**: 단위별로 끊어서 진행, 각 Gate 통과 후 다음 단계
- **호환성 보장**: 신규 인자 모두 default 값 — 호출처 누락 시에도 기존 동작 유지 (slippage=None)

## 롤백 계획
- **코드 롤백**: Phase별 독립 커밋 → `git revert <hash>`
- **데이터 영향 없음**: slippage NULL → 측정값으로 변하는 단방향 (DB 마이그레이션 불필요)
- **롤백 트리거**:
  - 매도 후 trades 테이블에 비정상 데이터 기록 (slippage 절댓값 > 5%)
  - inquire_asking_price 호출 추가로 매도 흐름이 5초 이상 지연
  - 모의 테스트에서 5경로 중 1개라도 슬리피지 측정 실패

## 완료 기준 (1주 관찰 후)

| 지표 | 목표 |
|---|---|
| 매도 5경로 slippage 비-NULL 비율 | ≥ 95% |
| reference_source = "bid1" 비율 | ≥ 90% (호가 조회 성공률) |
| 평균 매도 slippage (시장가) | -0.05% ~ -0.5% 분포 (음수가 정상) |
| 이상치 빈도 (|slippage| > 2%) | ≤ 1건/일 |
| `inquire_asking_price` 추가 호출로 인한 매도 지연 | ≤ 1초/건 |
| 공격적 지정가 매수 vs 시장가 매도 슬리피지 비교 보고서 | W19 보고서에 포함 |

## 후속 작업 후보 (별도 트래킹)
- **시장가 매도 → 공격적 지정가 매도 전환**: 매수와 동일 패턴으로 매도도 1.0배 증거금 효과는 없지만, 슬리피지 ↓ 효과 가능. 본 작업 완료 후 데이터 기반 결정.
