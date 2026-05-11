# 단위 2-3 — flow_reliability_tracker 구현

## 작업 ID
`closing-bet-unit-2-3-flow-reliability` — 2026-05-11 PLAN 작성, 다음 세션에서 `/resume`

## 배경

### 왜 지금 필요한가
- 5/11 종가베팅 점검 중 발견: **Layer 1(수급) 점수가 모든 78건에서 0~1점에 한정**
- Layer 1 가중치는 의도적으로 `0.0` (settings.yaml:19) — KIS 추정치 정확도 미검증으로 보수 운영
- 100건 게이트 도달(5/14~15) 시점에 Layer 1 활성화 결정하려면 **이번 주에 검증 인프라 가동 시작 필요**

### 기존 인프라 (이미 구현됨)
| 항목 | 상태 | 위치 |
|---|---|---|
| `flow_data_reliability` 테이블 | ✅ | `closing_bet_system/storage/db.py:323` |
| `candidate_logger.log_flow_reliability()` | ✅ | `closing_bet_system/storage/candidate_logger.py:435` |
| `_direction_match()` 헬퍼 | ✅ | `candidate_logger.py:646` |
| `candidate_features.inst_net_buy_estimated` raw 저장 | ✅ | 118건 |
| KRX 확정값 수집 함수 (pykrx) | ✅ (universe_provider_v2.py:443에서 사용) | `pykrx.stock.get_market_net_purchases_of_equities_by_ticker` |

### 미구현 = 본 단위 작업
1. **매일 KRX 확정값 수집 + 매칭 잡** (APScheduler 통합)
2. **신뢰도 집계 함수** (방향 일치율 N일 윈도우 계산)
3. **자동/수동 활성화 트리거** (70%+ 일치율 검증 시 알림 또는 settings.yaml 변경)

## 추가 발견 사항 (별도 sub-step)

### 🚨 inst_net_buy_estimated 수집 버그
- 5/11 데이터에서 모든 candidate_features.inst_net_buy_estimated = **0.0**
- foreign_net_buy_3d는 정상 (-366억 ~ +817억 분포)
- 위치: `kis_intraday_flow_collector.py:232-240`
  ```python
  inst_net_buy_estimated = None
  ...
  inst_net_buy_estimated = latest_inst_qty * latest_close
  ```
- **추정 원인**: latest_inst_qty가 0 또는 None 반환되는 케이스, KIS API 응답 파싱 문제
- **영향**: Layer 1 활성화해도 기관 조건 항상 False → 점수 영향 미미. 단위 2-3 매칭도 inst=0 vs confirmed=실제값 매칭이라 일치율 산출 곤란

## 목표

1. **inst=0 버그 fix** (Step 1) — KIS 응답 디버깅 + 정상 추정치 수집 복원
2. **KRX 확정값 매칭 잡 구현** (Step 2~3) — 매일 19:30 자동 수집 + flow_data_reliability INSERT
3. **신뢰도 집계 + 알림** (Step 4) — `compute_direction_match_rate(window_days=7)` 함수 + 70%+ 시 알림
4. **5/4~5/10 백필** (Step 5) — 기존 candidate_features 데이터로 사후 매칭 (orderbook 누적 시작 이후 데이터)
5. **활성화 결정** (Step 6, 수동) — 7~10일 누적 후 일치율 70%+ 시 `settings.yaml layer1_weight: 0.0 → 1.0`

## 단위 분해

### Step 1 — inst_net_buy_estimated 수집 버그 fix (1~2시간)
**파일**: `closing_bet_system/collectors/kis_intraday_flow_collector.py`
**작업**:
1. KIS `get_investor_trading` API 응답 직접 호출하여 디버깅
2. `latest_inst_qty` 값이 0/None 반환되는 케이스 식별
3. 파싱 로직 수정 (예: `_safe_int()` 추가, 응답 키 이름 변경 대응)
4. 단위 테스트 작성 (정상 응답 / 빈 응답 / 0 응답 케이스)
5. py_compile + code-tester 에이전트 검증
6. systemd restart 또는 다음 15:10 잡 시점에 자연 검증

### Step 2 — KRX 확정값 수집기 신규 (1시간)
**파일 신규**: `closing_bet_system/services/flow_reliability_tracker.py`
**핵심 함수**:
```python
def fetch_krx_confirmed_flow(trade_date: date, tickers: list[str]) -> dict[str, dict]:
    """pykrx로 KRX 확정 투자자별 순매수 수집.
    반환: {ticker: {"inst_confirmed": float, "foreign_confirmed": float}}
    """
```
- pykrx `get_market_net_purchases_of_equities_by_ticker` 사용
- 시장 구분(KOSPI/KOSDAQ) 자동 매핑
- 예외 처리 (pykrx 빈 응답, ticker 누락)

### Step 3 — 매일 19:30 매칭 잡 (1시간)
**파일**: `closing_bet_system/main_orchestrator.py`
**작업**:
1. `run_flow_reliability_check(for_date=None)` async 메서드 신규
   - for_date 미지정 시 직전 영업일 (라벨링 버그 fix 패턴 재사용)
   - 해당 일자 candidates 조회 → candidate_features의 inst/foreign estimated 추출
   - KRX 확정값 수집 (Step 2 함수 호출)
   - 매칭 → `candidate_logger.log_flow_reliability()` 호출
2. APScheduler 잡 추가: `cron(hour=19, minute=27, day_of_week='mon-fri')` (KST)
   - 매수도 동시호가 마감 후 + KRX 발표 시점 고려
3. systemd 재시작 후 자동 가동

### Step 4 — 신뢰도 집계 함수 (30분)
**파일**: `flow_reliability_tracker.py` (Step 2와 동일 파일)
**핵심 함수**:
```python
def compute_direction_match_rate(
    window_days: int = 7,
    indicator: str = "inst",   # "inst" | "foreign"
) -> dict:
    """window_days 영업일 윈도우의 방향 일치율 계산.
    반환: {"window_days", "n_samples", "match_rate", "passes_70_threshold"}
    """
```
- flow_data_reliability 테이블 GROUP BY trade_date 집계
- 70%+ 시 텔레그램 알림 또는 INFO 로그 (자동 활성화는 별도 단위)

### Step 5 — 5/4~5/10 백필 (선택, 1시간)
- candidate_features 데이터로 사후 매칭
- 5/4~5/10 데이터: pykrx KRX 확정값 일괄 수집 → flow_data_reliability INSERT
- 즉시 데이터셋 확보로 5/15+ 활성화 결정 가속

### Step 6 — 활성화 결정 (수동, 다음 작업 단위)
- 7~10일 누적 후 `compute_direction_match_rate(window_days=7)` 호출
- inst 일치율 70%+ AND foreign 일치율 70%+ 시 활성화 권고
- 사용자 승인 후 `settings.yaml layer1_weight: 0.0 → 1.0` 변경 + systemd restart

## 변경 파일 목록

| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `closing_bet_system/collectors/kis_intraday_flow_collector.py` | 수정 | Step 1 inst 수집 버그 fix |
| `closing_bet_system/services/flow_reliability_tracker.py` | **신규** | Step 2~4 핵심 모듈 (~200줄) |
| `closing_bet_system/main_orchestrator.py` | 수정 | Step 3 잡 추가 + run_flow_reliability_check 메서드 |
| `scripts/test_closing_bet_unit_2_3.py` (선택) | 신규 | 단위 테스트 |
| `closing_bet_system/config/settings.yaml` | (Step 6 시 변경) | layer1_weight: 0.0 → 1.0 |

## 롤백 계획
- Step 1 fix: git revert (단일 커밋이면 직접)
- Step 2~4 신규 모듈: 파일 삭제 + APScheduler 잡 제거
- Step 5 백필: `DELETE FROM flow_data_reliability WHERE trade_date >= '2026-05-04'`
- Step 6 활성화: settings.yaml 단순 토글 원복

## 완료 기준 (Step 별)
- [ ] Step 1: inst_net_buy_estimated 0이 아닌 값 정상 저장 확인 (5/12 candidates)
- [ ] Step 2~4: 모듈 + 잡 구현 + py_compile + 단위 테스트 통과
- [ ] Step 5: flow_data_reliability에 5/4~5/10 데이터 백필 (선택, ~80건 예상)
- [ ] 5/12 19:30 잡 첫 자동 실행 검증
- [ ] Step 6: 5/15~5/22 사이 70%+ 검증 시 활성화 권고 알림 작동
- [ ] change_log.md (Step 6 시점에 layer1_weight 변경 기록)

## 다음 세션 진입 명령
```
/resume
```
또는
```
docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/PLAN.md CONTEXT.md CHECKLIST.md 를 읽고 Step 1부터 진행해주세요.
```
