# PLAN — 종가베팅 금요일/휴장 청산누락 버그 fix (핸드오프)

- **작성일**: 2026-06-15 (다음 세션 인계용 핸드오프)
- **유형**: 실거래 청산 로직 버그 fix (명확한 근본원인 + 동일 파일에 정답 패턴 존재)
- **발견 경위**: 2026-06-15 종가베팅 수익극대화 활성화 직후, 미청산 포지션 005935 발견 → 원인 추적

## 목표
종가베팅 청산 잡이 **직전 "달력상 어제"**를 trade_date로 써서, 금요일/휴장 직전 진입분이 영구 미청산(고아)되는 버그를 고친다. **직전 "거래일"**로 교체.

## 버그 (재현)
- 종가베팅: T일 종가 진입(entry_pipeline 15:18) → T+1 오전 청산(emergency 09:01 / morning_exit 09:02 / force_close 10:30 / trailing 09:05~10:25).
- 청산 잡들은 `trade_date = now.date() - timedelta(days=1)`(달력 어제)로 대상을 찾는다.
- **금요일 진입** → 청산 예정 토요일(휴장, 잡 스킵) → **월요일** 청산 잡은 `trade_date=일요일`(진입 없음) 조회 → **금요일 진입분 영영 미조회**.
- 휴장일(수요일 등) 직전 진입도 동일.
- **실제 피해**: candidate_id=437, **005935 삼성전자우 7주 @206,500 (6/12 금 진입, 미청산)**. ~144.6만원. (이 건은 청산창이 6/15 월에 구코드로 이미 지나가 fix로 자동회수 안 됨 → 별도 수동 정리, CHECKLIST 참조)

## 근본 원인 / 정답 패턴 (★ 핵심)
**같은 파일 `closing_bet_system/main_orchestrator.py`에 이미 올바른 "직전 영업일 walk-back" 패턴이 존재**한다 (`run_label_yesterday` 등, 약 L613·L704):
```python
# 직전 영업일 (월요일 → 금요일, 공휴일 건너뛰기)
yesterday = today - timedelta(days=1)
while not is_trading_day(yesterday):
    yesterday = yesterday - timedelta(days=1)
```
exit 래퍼 4개만 이 패턴을 안 쓰고 단순 `-1일`을 쓴다 → 이걸 동일 패턴으로 교체하면 끝.

## 변경 파일 / 위치 (라인은 이동 가능 — 함수명으로 찾을 것)
`closing_bet_system/main_orchestrator.py` 4개 exit 래퍼의 `trade_date = (now_kst().date() - timedelta(days=1)).isoformat()`:
- `run_emergency_stop_check` (≈L1016)
- `run_morning_exit` (≈L1034)
- `run_morning_force_close` (≈L1053)
- `run_morning_trailing` (≈L1081, 변수명 `now` 사용 주의)

## 접근 (권장: 공용 헬퍼)
1. `config.py`에 `previous_trading_day(ref_date=None)` 헬퍼 추가 (config에 `is_trading_day` 이미 있음, L41). ref(기본 now_kst().date())에서 하루씩 빼며 첫 거래일 반환:
   ```python
   def previous_trading_day(ref_date=None):
       d = (ref_date or now_kst().date()) - timedelta(days=1)
       while not is_trading_day(d):
           d -= timedelta(days=1)
       return d
   ```
2. 4개 exit 래퍼를 `trade_date = previous_trading_day().isoformat()`로 교체.
   - **건드리지 말 것**: `run_daily_pipeline`(L941, 진입=오늘 trade_date), `run_daily_summary`, `run_label_yesterday`(이미 올바른 walk-back).
3. (선택, 하드닝) 청산 잡이 **직전 거래일 1일만** 보면, 과거 누락분(005935류)은 여전히 미회수. 필요시 "최근 N거래일 미청산 sweep"를 별도 후속으로 검토(이번 범위 밖 — 자동매도라 신중).

## 롤백
헬퍼 + 4행 교체뿐. git revert. 토글 무관(항상 적용 로직).

## 완료 기준
- `config.previous_trading_day` 단위 테스트 (월→금, 화→월, 수요일 휴장 직전, 연속 휴장).
- 4개 래퍼가 헬퍼 사용 + 기존 정상흐름(연속 거래일) 동일성 회귀.
- code-tester 심각 0.
- **서비스 restart 필요** (다음날 09:01 청산 전). 장 마감 후 005935 정리 + restart 묶어서.

## 워크플로 (다음 세션)
`/resume` 또는 이 3문서 읽고: 설계 확정 → strategy-planner+code-tester 병렬 리뷰(feedback 규칙) → 구현 → 테스트 → change_log 1줄 → 머지 → restart.

## 연계 후속 (별건, 같은 인계)
1. **005935 수동 정리**: 장 마감 후 KIS API 회복 시 실보유 확인 → 보유면 (a) KIS 앱 수동매도 또는 (b) 종가베팅 `execute_force_close("2026-06-12")` 1회 수동 실행(서비스 토큰 경합 주의, dry_run=false라 실매도). CONTEXT 참조.
2. **KIS 잔고 inquire-balance 500 종일 지속**: 실행 봇도 잔고 못 읽음(자금계산/fund_guard 영향 가능) → 별도 진단.
