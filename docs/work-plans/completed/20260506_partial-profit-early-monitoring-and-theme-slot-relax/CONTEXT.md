# CONTEXT — 09:00 조기 모니터링 + 테마 슬롯 조건부 상향

## 변경 이유
- **사용자 관찰 (2026-05-06)**:
  - 삼성전자/네페스아크 09:00 직후 급등. 09:25까지 모니터 미가동이라 만약 25분간 올랐다 빠지면 분할익절을 놓칠 위험.
  - 신규 후보가 테마 한도(2) 때문에 매수 탈락. 빈 슬롯이 있는데도 5종목을 못 채움.

## 현재 코드 상태

### 모니터링 시작 시점
- `scheduler.py:157-164` — `monitoring_start` 잡이 `09:26`으로 등록
- `modules/trading_engine/portfolio_monitor_v2.py:463-499` — `start_monitoring()`
  - `if not self.positions: return` (포지션 없으면 즉시 종료)
  - `self.websocket.subscribe(stock_codes)` — 시작 시점 종목만 구독 (동적 추가 메서드 호출 없음)
- `modules/trading_engine/portfolio_monitor_v2.py:252` — `add_position()` 메서드 존재 (재사용 가능)
- `main.py:1492-1515` — 봇의 `start_monitoring()` 래퍼 (load_positions_from_db + create_task)
- `main.py:299` — `self.scheduler.on_monitoring_start = self.start_monitoring`

### 매도 잡 위치
- `main.py:2027~` — `run_hold_period_sells()` (09:15)
- `main.py:2222~` — `_execute_midweek_profit_sells()` (09:00)
- `main.py:2336~` — `_execute_midweek_loss_sells()` (09:10)
- `main.py:2191, 2323, 2434` — 매도 후 `self.monitor.remove_position(stock_code)` 호출

### 분할익절/손절/트레일링
- `modules/trading_engine/portfolio_monitor_v2.py:941~` — `_check_and_execute_partial_profit()`
- `modules/trading_engine/portfolio_monitor_v2.py:~904` — `_execute_stop_loss()` (손절)
- `modules/trading_engine/portfolio_monitor_v2.py:~1182` — `_execute_trailing_stop()`

### 매수 슬롯/테마 한도
- `config.py:185-187` — `MAX_POSITIONS = 5`
- `config.py:254-257` — `MAX_STOCKS_PER_THEME = 2`
- `config.py:258-261` — `MAX_STOCKS_PER_SECTOR = 3`
- `main.py:1117` — `available_slots = MAX_POSITIONS - held_count`
- `main.py:1213-1242` — Phase 5.5 테마/섹터 분산 필터 (현 단일 패스)
- `main.py:1226, 1233` — `self._diversity_excluded` 누적
- `main.py:1241` — `new_ai_stocks = diversified_candidates[:available_slots]`
- `main.py:1242` — `self._slot_excluded` 기록
- `main.py:~1324` — `_send_buy_summary` (탈락 사유 송출)

## 핵심 스니펫

### 현재 phase 5.5 (라인 1213-1242 근방, 단일 패스)
```python
diversified_candidates = []
theme_counts = {...}  # 보유 종목 시작값
sector_counts = {...}
for stock in filtered_candidates:
    if theme_counts[stock['theme']] >= MAX_STOCKS_PER_THEME:
        self._diversity_excluded.append({**stock, '_reason': '테마 분산 ...'})
        continue
    if sector_counts[sector] >= MAX_STOCKS_PER_SECTOR:
        self._diversity_excluded.append({...})
        continue
    diversified_candidates.append(stock)
    theme_counts[...] += 1
    sector_counts[...] += 1
```

### `start_monitoring()` 현재 (포지션 0이면 종료)
```python
async def start_monitoring(self) -> None:
    if not self.positions:
        logger.warning("모니터링할 포지션이 없습니다")
        return
    ...
    self._running = True
    stock_codes = list(self.positions.keys())
    self.websocket.subscribe(stock_codes)  # 시작 시점 종목만
    await asyncio.gather(self.websocket.start(), self._monitor_loop())
```

## 과거 버그/교훈
- DB 메모리 기록 "테마 3개×종목 3개" → 실제 코드는 **2개**. 메모리 정정 필요.
- 09:00 동시 발화 race: APScheduler는 동일 분 등록 잡의 실행 순서를 보장하지 않음. SellLock acquire 시점이 모니터보다 늦으면 race 가능 → 모니터도 `acquire() + 실패 skip` 패턴 사용.
- `setup_schedules()`는 시작 시 1회 호출 → 플래그 변경은 `sudo systemctl restart trading_system` 필요.

## 영향 범위
- **영향 받는 시스템**: 매수 (테마 분산), 매도 (모니터 + 매도 잡 race), 텔레그램 알림 (매수 요약, SellLock skip 메시지)
- **영향 없는 시스템**: 종가베팅, 대시보드, KIS API 토큰 공유, 분할익절 임계값/트레일링 비율, 손절률
- **DB 스키마 변경 없음**

## 외부 의존성
- KIS WebSocket: 동적 subscribe 동작 확인 필요 (구현 단계에서 `subscribe()` 추가 호출이 누적인지 덮어쓰기인지 확인)
- APScheduler: 동일 분 잡 순서 미보장 → SellLock으로 봉쇄

## 비범위 (Out of Scope)
- 분할익절 임계값(+8/+15/+25) 변경 없음
- 트레일링 레벨/손절률 변경 없음
- 섹터 한도(`MAX_STOCKS_PER_SECTOR=3`) 변경 없음
- 종가베팅 시스템 무관
- 09:30 별도 clear 잡 도입 안 함 (15:30 monitoring_stop에서 일괄 clear)
