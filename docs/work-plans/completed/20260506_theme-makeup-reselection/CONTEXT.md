# CONTEXT — 화요일 공휴일 보정 재선정

## 변경 이유
2026-05-05(화) 어린이날 휴장으로 화요일 정규 테마 재선정이 스킵되어 14일간 같은 테마가 유지되는 운영 리스크 발생. 사용자 요청으로 화요일 휴장 시 다음 영업일에 보정 재선정하도록 개선.

## 현재 코드 상태

### `scheduler.py:50-58` — `_skip_on_holiday` 데코레이터
```python
def _skip_on_holiday(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not is_trading_day():
            logger.info(f"휴장일 - {func.__name__} 스킵 ({now_kst().date()})")
            return
        return await func(*args, **kwargs)
    return wrapper
```
적용 대상: `_run_theme_check`(L349), `_run_theme_analysis`(L367) 등 9개 잡.

### `main.py:316-443` — `run_theme_analysis` 분기 핵심
```python
# L339~347
if self.today_themes and self._last_theme_rotation_date:
    today = now_kst().date()
    days_since_rotation = (today - self._last_theme_rotation_date).days
    is_tuesday = (today.weekday() == 1)
    same_week = (days_since_rotation < 7) and not is_tuesday
    if same_week:
        # "기존 테마 유지" 분기 — DB에 today 날짜로 selected=True 저장 (L439)
        return {"success": True, "themes": ..., "reused": True}
# 그 외: Step 1~3 정규 재선정 진행
```

### `main.py:1739, 1784` — 화요일 의존 분기
- L1739: `is_review_day = (self._last_theme_rotation_date is None or (today - self._last_theme_rotation_date).days >= 7)`
- L1784: 미드위크 교체 진입 시 화요일이면 스킵

### `config.py` — `is_trading_day(check_date=None)`
- 공휴일(`holidays.KR`) + 주말 통합 판정
- `check_date=None`이면 `now_kst().date()` 사용

## 핵심 스니펫

### 신규 헬퍼 (`config.py`)
```python
def is_makeup_reselection_day(today: date, target_weekday: int = 1) -> tuple[bool, Optional[date]]:
    if not THEME_MAKEUP_RESELECTION_ENABLED:
        return (False, None)
    if not is_trading_day(today):
        return (False, None)
    delta = (today.weekday() - target_weekday) % 7 or 7
    missed = today - timedelta(days=delta)
    if is_trading_day(missed):
        return (False, None)
    cursor = missed + timedelta(days=1)
    while cursor < today:
        if is_trading_day(cursor):
            return (False, None)
        cursor += timedelta(days=1)
    return (True, missed)
```

### `main.py:run_theme_analysis` 수정 분기
```python
is_tuesday = (today.weekday() == 1)
is_makeup, missed_tue = is_makeup_reselection_day(today)
if is_makeup and missed_tue and self._last_theme_rotation_date >= missed_tue:
    is_makeup, missed_tue = (False, None)  # 중복 발화 방어
same_week = (days_since_rotation < 7) and not is_tuesday and not is_makeup
```

## 과거 버그/사례
- **2026-05-05 어린이날**: 정규 재선정 누락 → 본 작업의 직접 원인
- **`same_week` 분기 매일 `selected=True` 갱신**: `database.py:708`이 매일 최신 날짜를 반환하여 `_last_theme_rotation_date`가 매일 갱신되는 표시상 혼란 (정상 동작이지만 본 작업과 무관, 별도 작업 권장)
- **`holidays.KR` 한계**: 근로자의 날(05/01), KRX 임시휴장일(12/31 등) 미포함 → 보정 트리거가 발화 안 될 수 있음 (별도 작업 후보, memory에 기록됨)

## 영향 범위

### 직접 영향
- 화요일 공휴일 발생 시 그 주의 보정 재선정 1회 발화
- 정규 화요일에는 기존 동작 100% 유지 (헬퍼가 즉시 False 반환)
- 토글 OFF 시 헬퍼 즉시 False 반환 → 기존 동작

### 간접 영향
- `_check_midweek_replacement`: 보정일에 미드위크 교체 잡과 정규 재선정 충돌 방지 가드 추가
- `check_theme_rotation`(08:00): `is_review_day` 보정 → 운영자 로그 정합성

### 비영향
- KIS 매수/매도 로직
- 분할익절/트레일링/손절 모니터
- 종가베팅 시스템 (`closing_bet_system/` 별도 모듈)
- 웹 대시보드 / 텔레그램 봇 입력

## 의존성
- `is_trading_day()` (`config.py`) — 이미 존재, 수정 불필요
- `now_kst()` (`config.py`) — 이미 존재, 수정 불필요
- `self.notifier.send_message()` — 텔레그램, 이미 존재
- pytest + monkeypatch — 단위 테스트용
