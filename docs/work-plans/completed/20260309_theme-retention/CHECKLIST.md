# CHECKLIST: 테마 연장 기능

## 구현 항목
- [x] selector.py에 RETENTION_SCORE = 38.0 상수 추가
- [x] selector.py에 select_themes_with_retention() 함수 추가
- [x] main.py 화요일 선정 로직 수정 (retention 호출)
- [x] format_theme_report에 유지/신규 표시
- [x] __init__.py export 추가
- [x] docstring에 비대칭 기준 명시

## 검증 항목
- [x] py_compile selector.py, main.py, __init__.py
- [x] 시뮬레이션: 기존 5개 중 3개 유지 + 2개 교체
- [x] 시뮬레이션: 기존 전부 탈락 → 전체 교체
- [x] 시뮬레이션: 기존 없음 (초기 상태)
- [x] 시뮬레이션: 기존 전부 유지
- [x] code-tester 에이전트 검증 (심각 0건)

## 배포 항목
- [x] systemd 서비스 재시작

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트
- [x] Phase 2~4 로드맵 문서 생성 (docs/phase2-4-roadmap.md)
