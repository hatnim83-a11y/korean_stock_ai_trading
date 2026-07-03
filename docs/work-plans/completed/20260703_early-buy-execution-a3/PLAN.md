# PLAN: 아침 매수 09:25 → 09:05 조기 진입 (A3안)

> 분봉 시뮬: 시초가 진입이 09:25 대비 평균 5일 수익률 **+4.72%p** 우위 (GS리테일 +18.75% vs -5% 손절)
> v17 분할진입(1차 50%)이 1차 진입 리스크 분산 → 공격적 진입 정당화
> 사용자 결정: 한방 09:05 변경 + 5개 기술적 안전책 + 거래량 OFF / 체결강도 보존

## 목표

1. **아침 갭상승 기회 포착**: 시초가 직후 진입으로 09:05~09:25 사이 +5%+ 갭상승 종목의 1차 50% 확보
2. **CRITICAL race 봉쇄**: Screening↔Buy 간 `asyncio.Event` 동기화 필수
3. **1줄 롤백 보장**: `EARLY_BUY_ENABLED=False` + systemctl restart 1회

## 토글 설계 (마스터 1 + 보조 4)

| 상수 | Default | 역할 |
|---|---|---|
| `EARLY_BUY_ENABLED` | False | 마스터 토글 |
| `EARLY_BUY_OBSERVATION_MINUTES` | 0 | 관찰 task timeout |
| `EARLY_BUY_DISABLE_VOLUME_FILTER` | True | 거래량 필터 OFF (사용자 결정) |
| `EARLY_BUY_DISABLE_STRENGTH_FILTER` | **False** | 체결강도 보존 (planner 권고) |
| `EARLY_BUY_MARKET_GUARD_DELAY_MINUTES` | 15 | DANGER 시 지연 단축 (기존 35분) |

## 새 스케줄 (EARLY_BUY_ENABLED=True 시)

| 시간 | 잡 | 변경 |
|---|---|---|
| 09:00 | monitoring_start_early + midweek_sell_profit | 유지 |
| 09:01 | midweek_sell_loss | -9분 |
| 09:02 | stock_screening + hold_period_sell | -3 / -13분 |
| 09:05 | execute_buy | **-20분 (핵심)** |
| 09:06 | monitoring_start (재시작) | -20분 |

## 구현 단계 (B → D → A → E → C 순서)

각 단위는 default=False라 중간 상태에서 동작 영향 없음.

### 단위 B: config.py — 5개 Field + 5개 @computed_field
- 위치: `config.py:686~745` (Morning Filter 섹션 끝)
- 5개 Field 추가 + 시간 문자열 5개 property (`buy_time_str`, `screening_time_str`, `monitoring_time_str`, `hold_period_time_str`, `midweek_loss_time_str`)
- 검증: `python -c "from config import settings; print(settings.EARLY_BUY_ENABLED, settings.buy_time_str)"`

### 단위 D: 메시지/로그 변수화 (24+3 위치)
- `main.py` 사용자 메시지 14곳: 727/830/859/898/918/987/992/1066/1082/1109/1193/1195/2149/2152/2163/2413/2419
- `main.py` 로그 메시지 8곳: 455/666/863/961/1005/1168/2141/2144/2235
- `scheduler.py` 잡 이름 3곳: `name=` 매개변수
- `telegram_notifier.py:835~836` `/pause` 응답
- 패턴: `settings.buy_time_str` 등 property 호출로 치환
- 검증: `grep -n "09:25\|09:05" main.py scheduler.py modules/` 결과 0건

### 단위 A: scheduler.py — 7개 add_job 시간 변경
- 위치: `scheduler.py:130~250`
- 패턴: `setup_jobs()` 내부 변수 5개 (헬퍼 함수 대신, strategy-coder 권고)
- `_print_schedules()`에 `EARLY_BUY_ENABLED={settings.EARLY_BUY_ENABLED}` 출력 추가

### 단위 E: asyncio.Event 동기화 + Market Guard 분기 (CRITICAL)
- `main.py.__init__`: `self._screening_done = asyncio.Event()` + `self._screening_done.set()` (초기 set, 첫 호출 무대기)
- `run_stock_screening`: 진입 시 clear(), finally에서 set()
- `execute_buy_orders` Phase 0: `await asyncio.wait_for(self._screening_done.wait(), timeout=180)` 추가
- Market Guard 지연: `delay_min = EARLY_BUY_MARKET_GUARD_DELAY_MINUTES if EARLY else MARKET_GUARD_DELAY_MINUTES`

### 단위 C: observer 미생성 + timeout + filter 토글 연동
- `main.py:1000`: observer 생성 조건에 `and not settings.EARLY_BUY_ENABLED` 추가
- `main.py:1185`: timeout_min 변수화
- `morning_screener.py:84~120`: `enable_volume_filter`/`enable_strength_filter` 기본값 None → settings 토글 연동

## 변경 파일

1. `config.py` (단위 B)
2. `scheduler.py` (단위 A)
3. `main.py` (단위 C, D, E)
4. `modules/morning_filter/morning_screener.py` (단위 C)
5. `modules/reporter/telegram_notifier.py` (단위 D)

신규 테스트:
- `tests/test_early_buy_schedule.py` — 7개 잡 시간 (legacy/early)
- `tests/test_early_buy_event_sync.py` — `_screening_done` 동기화
- `tests/test_morning_screener_early.py` — 거래량 OFF / 체결강도 ON

## 완료 기준

- [ ] 단위 B~C 5개 모두 구현 + py_compile 통과
- [ ] 단위 테스트 3개 PASS
- [ ] Dry-run (TEST_MODE) 09:02/05 흐름 정상
- [ ] code-tester 에이전트 검증 통과 (심각 0)
- [ ] systemctl restart 후 `_print_schedules` 로그 확인
- [ ] 1주 본가동 + 슬리피지/승률 지표 측정

## 롤백

```bash
# .env
EARLY_BUY_ENABLED=false
sudo systemctl restart trading_system
```

모든 변경 토글 분기 → False 시 100% legacy 동작. 부분 롤백 필요 시 git revert 단위별.
