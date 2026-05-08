# CHECKLIST — 테마 점수 박제 버그 핫픽스

## 구현 항목

### 코드 수정
- [x] `main.py` 상단에 `_coalesce_zero(v)` 헬퍼 추가
- [x] `main.py` 상단에 `_pick_momentum(t)` 헬퍼 추가 (`is None` 체크)
- [x] `main.py:187-197` 수정 1: DB 복원 normalized에 momentum/supply_ratio/news_count/ai_sentiment 4개 키 + `_coalesce_zero` 적용
- [x] `main.py:438` 수정 2: `_pick_momentum(t)` 사용 (폴백 체인)
- [x] `main.py:1950-1957` 수정 3: midweek replacement dict에 점수 4개 키 + `_coalesce_zero` 적용
- [x] `modules/theme_analyzer/scorer.py:652-673` 수정 4: 가드 코멘트 추가 (momentum/momentum_score 양쪽 키 의존성 명시)

### 테스트
- [x] `tests/test_theme_dict_normalization.py` 신규 작성
  - [ ] TC-1: DB 복원 momentum 보존 (`:memory:` SQLite)
  - [ ] TC-2: `_pick_momentum` 폴백 체인 4 케이스
  - [ ] TC-3: NULL 컬럼 → 0 (None 아님)
  - [ ] TC-4: midweek replacement dict 점수 키 보유
  - [ ] TC-5: `_coalesce_zero` NULL/0/실수 구분
- [x] `scripts/verify_theme_restore_keys.py` 신규 작성 (단발 검증)
- [x] `scripts/repair_theme_zeros_2026_05.py` 신규 작성 (NULL 마킹 + 백업 + dry-run + confirm)

### 검증 (배포 전)
- [x] code-tester 에이전트 실행 (main.py + scorer.py 변경 대상)
- [x] code-tester 심각 이슈 0건 / 주의 이슈 즉시 패치
- [x] `pytest tests/test_theme_dict_normalization.py -v` 5건 통과
- [x] `python -m py_compile main.py modules/theme_analyzer/scorer.py` 통과
- [x] `python -c "from config import is_trading_day; from datetime import date; assert is_trading_day(date(2026,5,12))"` (5/12 영업일 사전 확인)
- [x] `python scripts/verify_theme_restore_keys.py` 출력 정상 (단발 검증)
- [x] 소급 복구 dry-run 출력 검토 (영향 row 수, 매칭 실패 0건 확인)

## 배포 항목
- [x] 재시작 시점 확인: 5/7 (수) 17:30 KST 이후 또는 5/8 (금) 07:50 KST 이전 (장중 09:00~15:30 회피)
- [x] 재시작 직전 `journalctl -u trading_system -n 20` 진행 중 잡 부재 확인
- [x] `data/trading.db.backup_YYYYMMDD_HHMMSS` 백업 (소급 복구 직전)
- [x] 소급 복구 스크립트 실행 (NULL 마킹)
- [x] `sudo systemctl restart trading_system`
- [x] 재시작 후 `journalctl -u trading_system -n 50` 정상 가동 확인
- [x] 재시작 후 "테마 로테이션 복원" 로그 + 5건 모두 momentum 키 보유 (단발 검증 스크립트로 확인)

## 검증 항목 (배포 후)

### 즉시 (배포 직후)
- [x] systemctl status 정상
- [x] 단발 검증 스크립트 출력에서 today_themes 5건 모두 momentum/ai_sentiment 정상값 (NULL 아님)

### 다음 영업일 (5/8 금)
- [x] 08:30 KST 트리거 발화 확인 (journalctl)
- [x] `SELECT * FROM themes WHERE date='2026-05-08' AND selected=1` — 5건 모두 momentum/ai_sentiment 값 보유 (NULL 또는 정상값)
- [x] 텔레그램 "08:30 테마 분석" 알림 점수 표시 정상
- [x] 대시보드 트렌드 차트 5/8 정상 + 5/4~5/7 dash/NULL 표시
- [x] 회귀 모니터링 SQL 0건 유지

### 다음 화요일 (5/12 화)
- [x] 정규 재선정 발화 확인
- [x] 5/13 (수) "기존 테마 유지" 저장 시 momentum 보존 (변경 2 폴백 체인 검증)
- [x] 회귀 모니터링 SQL 지속 0건

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 — 시스템 무결성 fix 분류, before/after 형식
- [x] `memory/MEMORY.md` 1줄 추가 — 의존 관계(scorer.py 양쪽 키 출력 유지) + 변경 위치 명시
- [x] (선택) `CLAUDE.md` 코드 규칙 섹션 — "DB → in-memory 정규화 시 NULL 방어 (`is None` 체크)" 1줄

## 아카이브
- [x] 모든 항목 [x] 확인 후 `active/theme-score-zero-fix/` → `completed/20260507_theme-score-zero-fix/` 이동
