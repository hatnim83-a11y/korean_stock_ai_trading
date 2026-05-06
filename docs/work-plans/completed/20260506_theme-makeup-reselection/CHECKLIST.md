# CHECKLIST — 화요일 공휴일 보정 재선정

## 구현
- [x] `config.py`에 `THEME_MAKEUP_RESELECTION_ENABLED = True` 토글 추가 (Settings 클래스 Field)
- [x] `config.py`에 `is_makeup_reselection_day(today, target_weekday=1)` 순수 함수 추가
- [x] `main.py` 상단 import 보강 (`is_makeup_reselection_day`)
- [x] `main.py:run_theme_analysis` 시작부 today/is_makeup/missed_tue 1회 계산 + 중복 발화 방어 가드
- [x] `main.py:run_theme_analysis` `same_week` 식 보강 (`not is_makeup` 추가)
- [x] `main.py:run_theme_analysis` 정규 재선정 진입 직전 보정 알림 (logger.info + 텔레그램, 한글 요일)
- [x] `main.py:check_theme_rotation` `is_review_day` 식에 `is_makeup_day` OR + 중복 발화 방어
- [x] `main.py:_check_midweek_replacement` 화요일 가드에 보정일 OR + 중복 발화 방어 + None 가독성 방어
- [x] `tests/test_makeup_reselection.py` 신규 — 8개 시나리오 (기본 5 + 엣지 3)

## 검증
- [x] `python -m py_compile config.py main.py` 통과
- [x] `pytest tests/test_makeup_reselection.py -v` 8/8 PASS
- [x] code-tester 에이전트로 `config.py` + `main.py` 변경분 리뷰 — GO 판정 (심각 0 / 주의 2건 모두 즉시 패치)
- [x] 시나리오 ①: 어린이날 휴장 다음날 (2026-05-06 수) → True, missed=2026-05-05
- [x] 시나리오 ②: 정규 화요일 (2026-05-12) → False
- [x] 시나리오 ③: 보정 기회 놓침 (2026-05-07 목, 사이에 수요일 영업일) → False
- [x] 시나리오 ④: 평범한 수요일 (2026-04-29) → False
- [x] 시나리오 ⑤: 토글 OFF → 모든 케이스 False
- [x] 엣지 ⑥: 화+수 연속 휴장 → 목요일 보정 발화
- [x] 엣지 ⑦: 오늘이 비영업일 → False
- [x] 엣지 ⑧: 월요일 (직전 화요일 영업일) → False
- [x] 중복 발화 방어: `_last_theme_rotation_date >= missed_tue`이면 보정 우회 — main.py 3곳 모두 확인

## 배포
- [x] `ps aux | grep main.py | grep -v grep` 으로 외부 프로세스 부재 확인 (systemd 단일 프로세스만)
- [x] `sudo systemctl restart trading_system`
- [x] `sudo systemctl status trading_system` ACTIVE 확인 (PID 3292348)
- [x] 부팅 직후 systemd 로그에서 import/실행 오류 없음 확인 (포지션 2개 정상 로드, WebSocket 연결, 모니터링 시작)
- [x] `docs/improvements/change_log.md`에 1줄 추가

## 문서 업데이트
- [x] `memory/MEMORY.md`에 신규 메모리 항목 추가
- [x] 신규 메모리 파일 `memory/project_makeup_reselection.md` 생성
- [x] CLAUDE.md 갱신 불필요 (운영 규칙 변동 없음)

## 아카이브
- [x] `docs/work-plans/active/theme-makeup-reselection/` → `docs/work-plans/completed/20260506_theme-makeup-reselection/` 이동
