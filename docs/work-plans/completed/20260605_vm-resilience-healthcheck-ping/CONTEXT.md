# CONTEXT — Healthcheck Ping

## 변경 이유
incident(2026-06-05)에서 VM freeze + 네트워크 단절로 봇이 텔레그램 알림을 못 보내 운영자 무감지.
신호 송신 기반 알림(텔레그램)은 네트워크/프로세스가 죽으면 함께 죽는다. **무신호를 외부에서 감지**하는 dead-man's-switch가 유일한 커버.

## 현재 코드 상태 (file:line)
- **scheduler.py:35-37** import: `AsyncIOScheduler`, `CronTrigger`, `EVENT_*`. → `IntervalTrigger` 추가 필요
- **scheduler.py:104-119** 콜백 선언부 (`self.on_X: Optional[Callable] = None` 패턴). → `on_healthcheck_ping` 추가
- **scheduler.py:121-122** `add_listener(_on_job_executed/_on_job_error)`
- **scheduler.py:50-58** `_skip_on_holiday` 데코레이터 — **healthcheck엔 붙이면 안 됨**
- **scheduler.py:413~** `_run_*` 메서드 패턴: `async def`, try/except, `if self.on_X: await self.on_X() else: warning`
- **scheduler.py:136** `setup_schedules()` / **L360** 끝에서 `_setup_closing_bet_jobs()` + `_print_schedules()`. → 등록은 closing_bet 직전
- **scheduler.py:151-157** add_job 예시(CronTrigger). IntervalTrigger도 동일 add_job 형식
- **main.py:362-379** `_setup_scheduler_callbacks()` — `self.scheduler.on_X = self.method` wiring
- **main.py:281** "✅ 시스템 시작 완료" 로그 / **L286** `_resume_monitoring_if_needed()`
- **main.py:54** `from config import settings, now_kst, ...`
- **config.py:853-878** Field 스타일(pydantic `Field(default=..., description=...)`) / **L946** `class Config:` (삽입 지점 직전) / **L1000** `settings = Settings()`
- **telegram_notifier.py:129** 기존 `httpx.post`(동기) 사용 — healthcheck는 별도 async 클라이언트로

## 핵심 스니펫 (패턴 정합)
scheduler `_run_*` 표준형:
```python
@_skip_on_holiday            # ← healthcheck엔 제거
async def _run_theme_check(self) -> None:
    try:
        if self.on_theme_check:
            await self.on_theme_check()
        else:
            logger.warning("... 콜백 미등록")
    except Exception as e:
        logger.error(...)
        self._send_error_notification(...)   # ← healthcheck는 에러알림 호출 안 함(스팸 방지)
```

## 리뷰 지적 (반드시 준수)
- **async/sync 경계**: AsyncIOScheduler는 async 잡을 await 실행. 동기 httpx → 루프 블로킹 → 09:05 매수 잡과 동시 발화 시 매수 지연. → `httpx.AsyncClient` 필수
- **데코레이터**: `@_skip_on_holiday` 붙으면 휴장일/주말 ping 중단 → 외부서비스가 "다운"으로 오경보. → 미적용
- **트레이딩 격리**: ping은 트레이딩에 어떤 영향도 주면 안 됨 → 예외 전부 삼킴, 텔레그램 미발송
- **URL 검증**: ENABLED=True + URL="" → `httpx`가 InvalidURL throw → silent fail. → 등록 전 `url.strip()` 검사

## 영향 범위
- 신규 모듈/Field는 기존 코드가 참조하지 않음 → 독립적, 회귀 위험 낮음
- 유일 접점: scheduler.setup_schedules()에 잡 1개 추가(ENABLED 분기 내부), main 콜백 1줄
- 기본 비활성이라 미설정 시 기존 동작 100% 동일

## 과거 관련
- `_skip_on_holiday`는 모든 매매 잡에 적용 — healthcheck는 의도적 예외(첫 비매매 24h 잡)
- scheduler_misfire_fix(2026-06-01): job_defaults grace/coalesce — interval 잡엔 영향 미미하나 동일 defaults 적용됨
