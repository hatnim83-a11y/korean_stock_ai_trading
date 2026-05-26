# CONTEXT: 아침 매수 09:25 → 09:05 조기화 (A3안)

## 변경 이유

### 사용자 직접 관찰
시초가 갭상승 종목 매수 기회를 자주 놓침. 09:25 매수 시점에 +5% 이상 추격 매수 → 손절로 직결되는 패턴.

### 분봉 시뮬레이션 (5월 매수 5건 + 갭 탈락 일부)

| 진입 시점 | 평균 5일 exit% |
|---|---|
| 09:05 (시초가) | **+1.91%** |
| 09:10 | -2.85% |
| 09:15 | +1.76% |
| 09:20 | -2.84% |
| **09:25 (현재)** | **-2.81%** |

**격차 +4.72%p** — 시초가 진입 우위.

결정적 케이스:
- **GS리테일 (5/8)**: 09:05 +18.75% vs 09:25 -5% 손절 (23%p 격차)
- 갭상승 +1% 이상 종목 평균 슬리피지 +3.84% → 5일 누적 +13.60%p 손실

### 트렌드 필터 검토 결과
- 5개 시점 중 3개에서 무효 판정 (좋은 종목 차단)
- DB 미저장 + 로그 부족으로 과거 정량 측정 불가 (5/26 단일 케이스만 확인)
- 매수 통과 종목 50% 손절 → 트렌드 필터 손절 방지 효과 미입증

## 현재 코드 상태 (Phase 1 조사 결과)

### scheduler.py — 7개 아침 잡

| 시간 | jobid | 콜백 | 라인 |
|---|---|---|---|
| 09:00 | monitoring_start_early | _run_monitoring_start | scheduler.py:161 |
| 09:00 | midweek_sell_profit | _run_midweek_sell_profit | scheduler.py:216 |
| 09:05 | stock_screening | _run_stock_screening | scheduler.py:141 |
| 09:10 | midweek_sell_loss | _run_midweek_sell_loss | scheduler.py:225 |
| 09:15 | hold_period_sell | _run_hold_period_sell | scheduler.py:234 |
| 09:25 | execute_buy | _run_execute_buy | scheduler.py:150 |
| 09:26 | monitoring_start | _run_monitoring_start | scheduler.py:171 |

### main.py — execute_buy_orders (1097~1277)

- **Phase 0 (1182~1187)**: `_observer_task` 대기 — `MORNING_OBSERVATION_MINUTES * 60` timeout
- **Phase 5 (1261~1267)**: `morning_screener.filter_candidates` 호출 (Step 1~5)
- **하드코딩 "09:25"**: 1168 (로그), 1193/1109 (텔레그램), 1457 (docstring)

### morning_screener.py — 5 Step 필터

| Step | 필터 | 09:05 적용성 |
|---|---|---|
| 0 | _fetch_realtime_data | OK |
| 1 | 시초가 갭 필터 | OK (시초가 09:00 즉시 확정) |
| 2 | 당일 수급 필터 | OK |
| 3 | **거래량 필터** | ⚠️ 5분 데이터 90%+ 탈락 → **OFF 결정** |
| 4 | **체결강도 필터** | ⚠️ 초기 변동성 → **보존 결정** (planner 권고) |
| 5 | 트렌드 필터 | observation_result=None 시 **자동 skip** (안전) |

### CRITICAL — Screening↔Buy 동기화 부재 (strategy-coder 발견)
- `main.py:1182`는 observer task만 wait_for, **스크리닝 잡 자체 완료는 대기 안 함**
- 09:02 screening → 09:05 buy 사이 race 가능
- 오늘(5/26) 실측 스크리닝 103.7초 소요 + KIS 500 에러 재시도 시 더 길어짐
- 해결: `asyncio.Event` 추가 (단위 E)

## 과거 버그 / 사례

- **5/8 GS리테일**: 시초가 24,100 → 09:25 매수가 25,200 (+4.56% 슬리피지) → -5% 손절
- **5/22 심텍**: 시초가 122,700 → 09:25 매수가 127,100 (+3.59% 슬리피지) → -5% 손절
- **5/26 LS / 지아이에스**: 갭 필터로 차단 (+4.96%, +7.89%) — 갭 필터 정당 작동 확인

## 영향 범위

### 변경 파일 (5개)
1. `config.py` — 5개 Field + 5개 @computed_field
2. `scheduler.py` — setup_jobs() 5개 변수 + 7개 add_job + _print_schedules
3. `main.py` — execute_buy_orders + run_stock_screening + 사용자 메시지 14곳
4. `modules/morning_filter/morning_screener.py` — __init__ 토글 연동
5. `modules/reporter/telegram_notifier.py` — /pause 응답

### 의존성 확인 (안전)
- `ENABLE_MORNING_FILTER=False` 경로 안전 (`main.py:1000`, `1261`)
- `_observer_task=None` 처리 안전 (`main.py:1182`)
- Step 5 트렌드 필터: `observation_result=None` 자동 skip (`morning_screener.py:294, 315`)
- SellLock/BuyLock 도입 완료 (v17) — 매도/매수 race 보호
- 갭 필터: 시초가 09:00 즉시 확정, 09:05 호출 안전

### 외부 의존성
- KIS API rate limit (계좌당 초당 20건) — 09:00~09:06 호출 추정 90회 + 0.11초 delay = 9.9초, 안전
- KIS `_shared_token` (만료 24h 전 갱신) — 정상 운영 시 충돌 없음
- TelegramNotifier rate limit — 매수 알림 수 증가 미미

## 결정 회피 항목

### "왜 09:05인가? (09:01 아닌가)"
- 09:00~09:03 호가 잔존 + 변동성 폭증
- 5분 안정화 후 진입이 단타 패턴 (시뮬에서도 09:05 기준 +4.72%p 검증)

### "왜 한방 변경인가? (단계적 X)"
- strategy-planner 권고는 3단계 (필터 OFF → 09:15 → 09:05)
- 사용자 결정: 공격적 진행, 1주 본가동 후 실측 판정 (분할진입 안전망 활용)

### "왜 체결강도 보존인가? (둘 다 OFF 아닌가)"
- planner 권고: 체결강도는 장 초반 매수세/매도세 직접 신호
- 거래량은 시간 가중치(`expected_volume = avg × trading_min/390`) 5분 시 비현실적 → OFF
- 체결강도는 5분치도 의미 있음 → 보존

### "왜 토글인가? (하드 변경 X)"
- 1주 실측 후 지표 미달 시 즉시 복귀 필요
- 모든 변경이 분기 형태 → False 시 100% legacy 보존

## 참조 자료

- 사용자 시뮬 결과: 분봉 시뮬 `gap_analysis.py` + `intraday_sim.py` (5월 매수 + 갭 탈락)
- v17 분할진입: `memory/project_tranche_entry.md`
- SellLock: `modules/trading_engine/sell_lock.py`
- Market Guard: `modules/market_guard.py`
- strategy-planner / strategy-coder 리뷰 결과 (대화 로그)
