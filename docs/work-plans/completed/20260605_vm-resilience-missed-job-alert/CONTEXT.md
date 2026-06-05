# CONTEXT — 누락 잡 탐지 + 장중 비정상 재시작 경보 (P0-B)

## 변경 이유
incident(2026-06-05): freeze로 08:00~09:06 잡 11개가 misfire 폐기됐으나 운영자 무감지.
APScheduler가 폐기 시 남긴 "Run time of job X was missed by Y" 경고 = `EVENT_JOB_MISSED`.
이걸 리스너로 잡아 텔레그램 경보 → DB 없이 누락 가시성 확보.

## 현재 코드 상태 (file:line)
- **scheduler.py:38** `from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED` → `EVENT_JOB_MISSED` 추가
- **scheduler.py:122-123** `add_listener(_on_job_executed, EVENT_JOB_EXECUTED)` / `_on_job_error` — 동일 패턴으로 `_on_job_missed` 추가
- **scheduler.py:104-120** 콜백 선언부 `self.on_X: Optional[Callable]=None` → `on_job_missed_alert` 추가
- **scheduler.py:136 setup_schedules / :361 끝** — 끝에서 `self.core_job_ids = CANDIDATE & {등록 잡 id}` 확정
- **scheduler.py 등록 잡 id**: theme_check, theme_analysis, stock_screening, execute_buy, monitoring_start_early(조건부 PARTIAL_PROFIT_EARLY_MONITORING_ENABLED), monitoring_start, monitoring_stop, hold_period_sell, midweek_sell_profit, midweek_sell_loss, market_close, daily_report, daily_health_check, post_trade_analysis, daily_theme_collection, supply_collection, ...
- **main.py:282-287** "✅ 시스템 시작 완료" → (B2 삽입) → `await self._resume_monitoring_if_needed()`
- **main.py:289-** `_resume_monitoring_if_needed()`: `market_open=dt_time(9,26)`, `market_close=dt_time(15,30)` (B2 장중 판정은 09:00 하한 — freeze가 08~09시였으므로)
- **main.py:54** `from config import settings, now_kst, is_trading_day, ...`
- **main.py:36** `import asyncio` 존재
- **telegram_notifier.py:99-127** `send_message(text, parse_mode='Markdown', ...)` 동기 httpx → `asyncio.to_thread` 래핑
- **config.py** pydantic Field 패턴, `class Config:`(L946~) 직전 삽입

## 2차 리뷰 핵심 (반드시 준수)
- 🔴 **이벤트루프 스레딩**: APScheduler 리스너는 (async 잡이면) 이벤트루프 스레드에서 호출되나, 동기 잡 추가 시 스레드풀에서 호출 → `get_running_loop()` RuntimeError. → `loop=asyncio.get_event_loop(); if loop.is_running(): loop.create_task(...)` 방어. 리스너 내 예외는 APScheduler가 BaseException 격리하므로 스케줄러는 안 죽지만 경보가 무음 실패 → try/except + logger.error 필수
- 🔴 **alert_job_missed 예외 소거**: create_task로 띄운 코루틴 예외는 미회수 경고 → 내부 try/except Exception
- **event 속성**: EVENT_JOB_MISSED = JobExecutionEvent → `event.job_id`, `event.scheduled_run_time`(None 불가, tz-aware) 확인됨
- **coalesce=True**(scheduler.py:98) → 동일 잡 중복 MISSED 없음
- **CORE 동적화**: monitoring_start_early는 토글 off 시 미등록 → static 포함해도 무해하나 `& 등록잡`으로 정리
- **휴장일 가드**: MISSED는 잡 실행 전 발화 → `_skip_on_holiday` 우회 → 리스너에서 `is_trading_day()` 직접 체크
- **B2 09:00~09:26**: 모니터 재개는 09:26부터라 09:00~09:26 경보 시 "모니터 재개 대기" 안내 포함
- **now_kst()** 사용 강제(UTC 서버)

## 핵심 스니펫 (목표 형태)
```python
# scheduler.py
CANDIDATE_CORE_JOB_IDS = frozenset({
    'theme_check','theme_analysis','stock_screening','execute_buy',
    'monitoring_start_early','monitoring_start','monitoring_stop',
    'hold_period_sell','midweek_sell_profit','midweek_sell_loss',
})  # monitoring_start_early는 PARTIAL_PROFIT_EARLY_MONITORING_ENABLED off 시 미등록

def _on_job_missed(self, event):
    try:
        if not is_trading_day(): return
        if event.job_id not in self.core_job_ids: return
        key = (event.job_id, event.scheduled_run_time)
        if key in self._missed_dedup: return
        if len(self._missed_dedup) > 200: self._missed_dedup.clear()
        self._missed_dedup.add(key)
        logger.error(f"⚠️ 핵심 잡 누락(misfire): {event.job_id} 예정={event.scheduled_run_time}")
        if self.on_job_missed_alert:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.on_job_missed_alert(event.job_id, event.scheduled_run_time))
            else:
                logger.error("이벤트루프 미실행 — 누락 경보 스케줄 불가")
    except Exception as e:
        logger.error(f"_on_job_missed 예외: {e}")
```

## 영향 범위
- 리스너/콜백은 신규, 기존 잡 로직 무변경. JOB_RECOVERY_ALERT_ENABLED 기본 True지만 순수 경보(트레이딩 무개입)
- 텔레그램 의존 → 장애 중엔 못 갈 수 있음(P0-A가 보완). 전송 실패는 logger.error로 잔존
- P0-A(healthcheck), SellLock, _resume_monitoring과 충돌 없음(B2는 resume 앞, send만)
