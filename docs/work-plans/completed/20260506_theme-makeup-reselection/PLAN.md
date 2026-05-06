# 화요일 공휴일 → 보정 재선정 (Makeup Reselection)

## 목표
화요일이 비영업일(공휴일/주말)인 경우 그 주의 정규 테마 재선정이 누락되지 않도록, 다음 첫 영업일에 1회 보정 재선정을 자동 실행한다.

## 배경
- 2026-05-05(화) 어린이날 공휴일 → `_skip_on_holiday` 데코레이터(`scheduler.py:50`)가 화요일 정규 테마 재선정 잡(`_run_theme_analysis` 08:30 KST)을 스킵.
- 결과: 정규 테마 재선정이 누락 → 다음 정규 재선정은 05/12까지 14일 미뤄짐.
- 현재 5개 테마(전력반도체/플랫폼/전기차/건설/AI반도체)는 04/28 정규 선정분 그대로 유지.

## 구현 단계
1. **`config.py` 헬퍼 추가**: 순수 함수 `is_makeup_reselection_day(today, target_weekday=1)` + `THEME_MAKEUP_RESELECTION_ENABLED = True` 토글
2. **`main.py:run_theme_analysis` 수정** (L339~347): `is_makeup` 판정 + 중복 발화 방어 + `same_week` 식 보강
3. **`main.py` 정규 재선정 분기에 알림 메시지 추가** (L445 근처): logger.info + 텔레그램 "🔁 화요일 보정 재선정"
4. **`main.py:_check_midweek_replacement` 보정** (L1784): 보정일이면 미드위크 교체 스킵
5. **`main.py:check_theme_rotation` 보정** (L1741 근처): `is_review_day` OR `is_makeup`
6. **단위 테스트 작성**: `tests/test_makeup_reselection.py` 5개 시나리오

## 변경 파일
- `config.py` — 헬퍼 함수 + 토글 (신규 ~15줄)
- `main.py` — 3개 분기 수정 (~30줄)
- `tests/test_makeup_reselection.py` — 신규 단위 테스트
- `docs/improvements/change_log.md` — 1줄 추가

## 롤백 계획
`config.py`에서 `THEME_MAKEUP_RESELECTION_ENABLED = False` 후 `sudo systemctl restart trading_system`. 헬퍼는 즉시 `(False, None)` 반환하여 기존 동작 복구.

## 완료 기준
1. `pytest tests/test_makeup_reselection.py -v` 5/5 PASS
2. `python -m py_compile config.py main.py` OK
3. code-tester 에이전트 검증 통과
4. `sudo systemctl restart trading_system` 정상
5. `change_log.md` 업데이트
6. active → completed 아카이브

## 작업 단위
- 단위 1: 3문서 생성 (현재)
- 단위 2: config.py + 단위 테스트
- 단위 3: main.py 3곳 + code-tester
- 단위 4: 배포 + change_log + 아카이브

## 별도 작업 권장 (이번 scope 외)
1. `same_week` 분기의 매일 `selected=True` 동기화 의미 정정
2. KRX 임시휴장일 + 근로자의 날 캘린더 보강
3. APScheduler `misfire_grace_time` 정책 검토
