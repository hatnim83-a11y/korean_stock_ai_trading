# Code Tester Agent Memory

## Key Patterns Found in This Codebase

### 지속적 주의 사항
- `date.today()` → UTC 서버에서 KST 날짜 불일치 (UTC 15:00 이후)
- `update` SQL 후 `cursor.rowcount` 미확인 → silent failure
- DB 작업: `Database()` → `connect()` → 작업 → `close()` 패턴에 finally 필요
- emoji in logger messages: 기존 코드에서 광범위 사용 (스타일 문제, 기능 무관)

### 잔존 기존 이슈 (미수정)
- `database.py` line 524: `save_trade()` date 기본값 `date.today()` UTC 버그
- `formatter.py` line 510: `date.today()` → `__main__` 블록에만 있음, 운영 경로 영향 없음
- `trading_engine.py` `_execute_stop_loss/take_profit`: sync `time.sleep(1)` 이벤트루프 블로킹
- `_close_position_in_db`/`_save_partial_sell_to_db`: DB `finally` 없음 (커넥션 누수 가능성)
- **2026-03-02 검증 완료**: telegram_notifier.py → now_kst() 수정 완료 (date.today() 없음 확인)

### is_trading_day() (config.py, 2026-03-02 추가)
- `holidays.KR`: 법정 공휴일 + 대체 공휴일 포함 (2026-03-02 3.1절 대체공휴일 정확히 감지)
- **미포함**: KRX 임시 특별 휴장일 (연말, 재난 등) — 알려진 한계, docstring 미언급
- `ImportError` fallback: 라이브러리 미설치 시 평일이면 거래일로 간주 (안전)

### _skip_on_holiday 데코레이터 (scheduler.py, 2026-03-02 추가)
- `@_skip_on_holiday` on class method: `functools.wraps` 보존됨 → `__name__`, coroutinefunction 인식 OK
- APScheduler와 완전 호환됨 (asyncio.iscoroutinefunction = True 확인)
- 휴장일에 None 반환 → APScheduler job error 없음 (설계 의도)

### crawlers.py naive/aware 캐시 처리 (2026-03-02)
- naive 타임스탬프 → `replace(tzinfo=timezone.utc)` 처리: 최대 9시간 오차 발생
- 7일 캐시 기준 실용적 영향: 경계 근처 9시간 편차로 만료 캐시를 유효로 간주 가능 (허용 수준)
- `from config import now_kst` 중복 import (line 29, 40) — 기능 무관, 스타일 이슈
- theme_rotator.py `get_history()` line 416: `fromisoformat` + `now_kst()` 비교
  - _load_history가 DB 미활성화(TODO) 상태이므로 naive 데이터 유입 경로 없음 → 현재 위험 없음
  - DB 활성화 시 naive 타임스탬프 처리 로직 추가 필요

### formatter.py (portfolio_optimizer, 2026-03-02 수정 완료)
- 운영 코드 lines 54, 185, 259: `now_kst().date()` 또는 `now_kst()` 사용으로 수정 완료
- line 510: `date.today()` 는 `__main__` 블록 내에만 존재 → 운영 경로 영향 없음
- `format_orders_for_telegram()`: `now_kst().date()` 사용 (line 259 기준)

### 매수/매도 구조적 버그 (2026-02-26, 수정 완료)
- partial_X_executed 재시작 이중 매도: DB position_state 저장으로 해결됨
- 3차 익절 profit_amount=0: `_close_position_in_db` 4번째 파라미터 추가로 해결됨
- execute_sell `_get_kis()` NameError → `_get_kis_api()`로 수정됨

### 테마 파이프라인 (2026-02-27)
- url 키 누락 버그: DB 복원 후 비월요일 재시작 시 스크리닝 종목 0개 — 수정 확인 필요
- main.py ISO week same_week: `rotation_date.year == today.year` → 연말 edge case
  - 수정 권장: `rotation_date.isocalendar().year == today.isocalendar().year`
- crawlers.py line 351-352: now_kst() 수정 완료 (2026-03-02 배치에서)

### DB 스키마 v8 주요 구조
- WAL 백업 불완전: `_migrate()` `.db-wal` 미포함 — 서비스 중단 없이 마이그레이션 시 위험
- KRX 12/31 임시 휴장: is_trading_day()에서 감지 안됨 (holidays.KR 미포함)
- `_save_daily_snapshot` 가격 최대 5분 지연 (DB current_price 사용)

### 검증 방법
- `py_compile` 구문 검사 후 런타임 import 테스트
- DB 작업: tempfile sqlite3 격리 테스트
- WAL 모드 DB 복사: `.db`, `.db-wal`, `.db-shm` 3파일 항상 확인
- datetime.utcnow() in JWT create_token: jose exp은 UTC 기준으로 올바름 (now_kst 교체 금지)

### 상세 히스토리
- 파일: `.claude/agent-memory/code-tester/review-history.md`
