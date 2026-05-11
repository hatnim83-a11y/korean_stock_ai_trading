# 단위 2-3 — flow_reliability_tracker 구현 (옵션 H 확정 재설계)

## 작업 ID
`closing-bet-unit-2-3-flow-reliability`
- 2026-05-11 1차 PLAN 작성
- 2026-05-11 옵션 G (세션 종료 + 재설계) 결정 (1차 시도)
- **2026-05-11 옵션 H (Step 0 a/b/d 조사 완료, 데이터 소스 확정) — 본 PLAN**

## ✅ Step 0 조사 종합 결과 (2026-05-11)

| 단위 | 결과 | 핵심 |
|---|---|---|
| **0a** pykrx 종목별 함수 | ❌ 전체 마비 | `get_market_trading_value_by_date` 등 빈 응답. OHLCV by_date 1개만 작동 |
| **0b** KRX 직접 크롤링 | ⚠️ → ✅ 대안 | KRX `data.krx.co.kr` LOGOUT 차단. **네이버 `finance.naver.com/item/frgn.naver` 정상 작동** (사후 확정값) |
| **0d** KIS 다른 TR | ✅ **결정적 해결** | **`HHPTJ04160200` (종목별 외인기관 추정가집계)** 14:30 누계 정상 반환 (5/11 17:43 KST 호출 검증) |

### 최종 데이터 소스 구도

| 데이터 | 소스 | TR/URL | 시점 | 용도 |
|---|---|---|---|---|
| **inst/foreign 가집계 (장중)** | KIS | `HHPTJ04160200` | 14:30 누계 (15:10 daily_pipeline에서 사용) | Layer 1 점수 추정치 |
| **inst/foreign 확정값 (사후)** | 네이버 | `finance.naver.com/item/frgn.naver?code=` | T+1 18~19시 갱신 | flow_reliability 매칭 |

### Step 0 보조 검증 (5/12 화요일)
- HHPTJ가 14:30 입력 후 15:00/15:10에 추가 갱신되는지 확인 (5/12 daily_pipeline 자연 검증)
- 만약 15:00 추가 입력 있으면 daily_pipeline 시간 조정 옵션 추가 검토

---

## 배경

### 왜 지금 필요한가
- 5/11 종가베팅 점검 중 발견: **Layer 1(수급) 점수가 모든 78건에서 0~1점에 한정**
- Layer 1 가중치는 의도적으로 `0.0` (settings.yaml:19) — KIS 추정치 정확도 미검증으로 보수 운영
- 100건 게이트 도달(5/14~15) 시점에 Layer 1 활성화 결정하려면 **이번 주에 검증 인프라 가동 시작 필요**

### Step 0에서 확정된 1차 시도 발견 사항
- `FHKST01010900` 15:10 시점에 inst=0 (KIS 정책, 코드 버그 아님)
- pykrx KRX 확정값 수집 함수 모두 빈 응답
- **해결**: `HHPTJ04160200` 가집계 + 네이버 사후 확정값 = 양쪽 데이터 소스 확보

### 기존 인프라 (이미 구현됨)
| 항목 | 상태 | 위치 |
|---|---|---|
| `flow_data_reliability` 테이블 | ✅ | `closing_bet_system/storage/db.py:323` |
| `candidate_logger.log_flow_reliability()` | ✅ | `closing_bet_system/storage/candidate_logger.py:435` |
| `_direction_match()` 헬퍼 | ✅ | `candidate_logger.py:646` |
| `candidate_features.inst/foreign_net_buy_estimated` 컬럼 | ✅ | (현재 inst=0 박제 데이터, 5/12 봇 재시작 후 정상화 예정) |
| KIS `get_investor_trading()` (FHKST01010900) | ✅ (그러나 inst=0) | `kis_api.py:594` |
| KIS `HHPTJ04160200` 호출 | ❌ 미구현 | 신규 추가 필요 (Step 1) |

### 미구현 = 본 단위 작업
1. **HHPTJ04160200 신규 호출 메서드** + collector 통합 (Step 1)
2. **네이버 frgn.naver 사후 확정값 수집기** (Step 2)
3. **매일 19:30 매칭 잡** (Step 3)
4. **신뢰도 집계 함수** (Step 4)
5. **5/4~5/10 백필** (Step 5, 선택)
6. **활성화 결정** (Step 6, 별도 단위)

## 목표

1. **HHPTJ04160200 collector 통합** (Step 1) — KIS 신규 메서드 + `kis_intraday_flow_collector.py` 수정. inst가 0이 아닌 정상값 수집 복원
2. **네이버 사후 확정값 수집기 + 매칭 잡** (Step 2~3) — 매일 19:30 자동 수집 + flow_data_reliability INSERT
3. **신뢰도 집계** (Step 4) — `compute_direction_match_rate(window_days=7)` 함수
4. **5/4~5/10 백필** (Step 5, 선택) — 기존 candidate_features inst는 0이라 부분 무효, foreign만 매칭 가능
5. **활성화 결정** (Step 6, 수동) — 7~10일 누적 후 70%+ 시 settings.yaml 변경

## 단위 분해

### Step 1 — HHPTJ04160200 collector 통합 (1~2시간)
**파일 수정**:
- `modules/stock_screener/kis_api.py` — 신규 메서드 `get_investor_trend_estimate(stock_code)` 추가
- `closing_bet_system/collectors/kis_intraday_flow_collector.py` — 기존 `FHKST01010900` 호출 위에 `HHPTJ04160200` 호출 추가, 마지막 차수(`bsop_hour_gb='5'`) 가집계 × 종가 = `inst_net_buy_estimated`

**핵심 응답 구조** (HHPTJ04160200):
```python
# rt_cd='0' / output2 = list of 5 rows
# bsop_hour_gb: '1'(09:30) / '2'(10:00 기관 시작) / '3'(11:20) / '4'(13:20) / '5'(14:30 마지막)
# frgn_fake_ntby_qty / orgn_fake_ntby_qty / sum_fake_ntby_qty (모두 18자리 zero-padded 문자열)
```

**검증 방식**:
- py_compile + code-tester
- 5/12 15:10 daily_pipeline 자연 검증 (inst_net_buy_estimated 0 아닌 값 다수 확인)

### Step 2 — 네이버 사후 확정값 수집기 (1시간)
**파일 신규**: `closing_bet_system/services/flow_reliability_tracker.py`

```python
def fetch_naver_confirmed_flow(trade_date: date, tickers: list[str]) -> dict[str, dict]:
    """네이버 frgn.naver 크롤링으로 종목별 일별 외인/기관 순매매 수량 수집.
    KOSPI/KOSDAQ 무관, 단일 endpoint.
    반환: {ticker: {"inst_confirmed_qty": int, "foreign_confirmed_qty": int}}
    
    주의: 수량 단위(주). candidate_features의 추정치는 금액(원) — 비교 시 부호만 비교 또는 종가 곱셈 환산.
    """
```

- BeautifulSoup 파싱
- 인코딩: `r.encoding = 'euc-kr'`
- 표 인덱스: `tables = soup.find_all('table', class_='type2')` 두 번째 [1]에서 row[2:] 데이터 추출
- 컬럼 인덱스: [0]=날짜, [1]=종가, [5]=기관, [6]=외국인
- 재시도 3회 백오프 5초 (셀트리온 사례 패턴)
- rate limit 신중 (네이버 차단 회피, ticker당 0.3~0.5초 sleep)

### Step 3 — 매일 19:30 매칭 잡 (1시간)
**파일**: `closing_bet_system/main_orchestrator.py`

1. `run_flow_reliability_check(for_date=None)` async 메서드 신규
   - 영업일 보정 (라벨링 fix 패턴 재사용)
   - candidates 조회 → candidate_features에서 inst/foreign estimated 추출
   - `fetch_naver_confirmed_flow` 호출 (Step 2 함수)
   - 매칭 → `candidate_logger.log_flow_reliability()` 호출
   - for_date 인자로 수동 백필 지원
2. APScheduler 잡 추가: `cron(hour=19, minute=27, day_of_week='mon-fri')` (KST)
3. `_skip_on_holiday` 데코레이터 적용

### Step 4 — 신뢰도 집계 함수 (30분)
**파일**: `flow_reliability_tracker.py` (Step 2와 동일 파일)

```python
def compute_direction_match_rate(
    window_days: int = 7,
    indicator: str = "inst",  # "inst" | "foreign"
) -> dict:
    """flow_data_reliability에서 N영업일 윈도우 일치율 집계.
    반환: {window_days, n_samples, match_rate, passes_70_threshold}
    """
```

### Step 5 — 5/4~5/10 백필 (선택, 1시간)
- candidate_features 5/4~5/10 데이터 (inst=0 박제) → foreign만 매칭 유효
- 5/12 봇 재시작 이후부터 inst 정상 — Step 6 본격 평가는 5/12 이후 데이터부터

### Step 6 — 활성화 결정 (수동, 별도 단위)
- 7~10일 누적 후 `compute_direction_match_rate(window_days=7)` 호출
- inst 일치율 70%+ AND foreign 일치율 70%+ 시 활성화 권고
- 사용자 승인 후 `settings.yaml layer1_weight: 0.0 → 1.0` 변경 + systemd restart
- `docs/improvements/change_log.md` 1줄 추가

## 변경 파일 목록

| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `modules/stock_screener/kis_api.py` | 수정 | Step 1 — 신규 메서드 `get_investor_trend_estimate()` 추가 |
| `closing_bet_system/collectors/kis_intraday_flow_collector.py` | 수정 | Step 1 — HHPTJ04160200 호출로 inst_net_buy_estimated 정상 수집 |
| `closing_bet_system/services/flow_reliability_tracker.py` | **신규** | Step 2~4 핵심 모듈 (~250줄) |
| `closing_bet_system/services/__init__.py` | **신규 (없으면)** | 디렉토리 생성 |
| `closing_bet_system/main_orchestrator.py` | 수정 | Step 3 — 잡 추가 + run_flow_reliability_check 메서드 |
| `closing_bet_system/config/settings.yaml` | (Step 6 시 변경) | layer1_weight: 0.0 → 1.0 |

## 롤백 계획
- Step 1: kis_api.py 메서드 삭제 + collector 호출 라인 제거 (단일 커밋이면 git revert)
- Step 2~4: 파일 삭제 + APScheduler 잡 제거
- Step 5 백필: `DELETE FROM flow_data_reliability WHERE trade_date >= '2026-05-04'`
- Step 6 활성화: settings.yaml 단순 토글 원복

## 완료 기준 (Step 별)
- [ ] Step 1: 5/12 candidate_features.inst_net_buy_estimated 0이 아닌 값 다수 확인 + py_compile + code-tester
- [ ] Step 2~4: 모듈 + 잡 구현 + py_compile + 단위 테스트 통과
- [ ] Step 5: flow_data_reliability에 5/4~5/10 데이터 백필 (선택, foreign만 유효 ~50건 예상)
- [ ] 5/12 19:27 잡 첫 자동 실행 검증
- [ ] Step 6: 5/19~5/26 사이 70%+ 검증 시 활성화 권고 알림 작동
- [ ] change_log.md (Step 6 시점에 layer1_weight 변경 기록)

## 다음 세션 진입 명령
```
/resume
```
또는
```
docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/ PLAN/CONTEXT/CHECKLIST 읽고 Step 1부터 진행
```
