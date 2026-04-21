# CHECKLIST — 테마 재선정 회전문 방지

## 구현
- [x] `config.py` — 상수 4개 추가 (`THEME_MOMENTUM_BOOST_FACTOR=0.7`, `_CLAMP=8.0`, `THEME_ENRICH_TOP_K=30`, `THEME_DROP_COOLDOWN_ENABLED=True`)
- [x] `main.py:2519` — 증폭 계수 상수화
- [x] `main.py:2519` 다음 줄 — adjustment clamp 삽입
- [x] `main.py:2490` — `top_15` → `top_k` 치환 (및 참조 모두)
- [x] `main.py:_enrich_tuesday_themes` — 진입/종료 로그 보강 + docstring 업데이트
- [x] `modules/theme_analyzer/selector.py:select_themes_with_retention` — 쿨다운 로직 추가
- [x] `tests/test_theme_selector.py` — 신규 작성 (4 케이스)
- [x] `scripts/replay_20260421.py` — 신규 작성

## 검증
- [x] `python -m py_compile config.py main.py modules/theme_analyzer/selector.py` — 문법 오류 없음
- [x] `python -c "from config import settings; print(settings.THEME_MOMENTUM_BOOST_FACTOR, settings.THEME_DROP_COOLDOWN_ENABLED)"` — 값 정상 (0.7, True)
- [x] `pytest tests/test_theme_selector.py -v` — 4/4 통과
- [x] `python scripts/replay_20260421.py` — 쿨다운 ON → drop 4개 전원 재진입 차단 ✅
- [x] `code-tester` 에이전트 실행 — 심각 0건, 주의 3건(반영 완료)

## 배포
- [ ] `ps aux | grep main.py | grep -v grep` — 외부 프로세스 없는지 확인 (사용자 확인 필요, sudo 접근)
- [ ] `sudo systemctl restart trading_system` — systemd 재시작 (사용자 실행 필요)
- [ ] `sudo systemctl status trading_system` — 정상 동작 (active 상태)
- [ ] 다음 화요일(2026-04-28) 08:30 로그 모니터링 준비

## 문서 업데이트
- [x] `memory/MEMORY.md` — "Theme Selection System" 섹션에 Phase 1+2 변경 기록
- [x] `memory/project_strategy.md` — 파라미터 변경 + 화요일 보강 안전장치 섹션 추가
- [x] `memory/project_theme_reselection_rotation_fix.md` — 신규 파일 생성
- [x] `docs/work-plans/active/` → `completed/20260421_theme-reselection-rotation-fix/` 아카이브

## 라이브 검증 (2026-04-28 화요일)
- [ ] 08:30 `_enrich_tuesday_themes` 로그: top30 보강 소요 시간 3분 이하 확인
- [ ] 08:30 `select_themes_with_retention` 로그: 쿨다운 발동 시 스킵 로그 출력 확인
- [ ] 09:25 매수 실행: 스크리닝/AI 통과 테마 존재 확인
- [ ] 이상 시 `THEME_DROP_COOLDOWN_ENABLED=False` 즉시 롤백

## 후속 작업 (code-tester 리뷰 반영)
- [ ] AI 보정 adjustment 클램프 추가 검토 (`main.py:_enrich_tuesday_themes` AI 경로) — 이번 작업 범위 밖, 별도 작업
- [ ] 모멘텀/AI delta 임계치(3.0pp / 2.0점) 상수화 검토 — 별도 작업
