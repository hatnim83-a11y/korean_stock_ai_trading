# PLAN — 테마 점수 박제 버그 핫픽스 (theme-score-zero-fix)

## 목표
`themes` 테이블의 `selected=1` 5건이 매일 `momentum=0/ai_sentiment=0/news_count=0`으로 박제되는 회귀를 차단하고, 5/4~5/7 박제 데이터를 정직하게 표시(NULL 마킹)한다.

## 배경
- 2026-05-07 종목추천 개선 작업 사후 검증 중 발견
- 4/29까지 정상(momentum=22.08 등) → 5/4부터 0 박제
- 원인: (1) `main.py:187-197` DB 복원 정규화 시 점수 4개 키 누락, (2) `main.py:438` "기존 테마 유지" 분기 키명 불일치(`momentum` vs `momentum_score`), (3) `main.py:1950-1957` midweek 교체 dict 키 누락
- 자연 회복은 다음 화요일(5/12) 정규 재선정 시까지 5일간 미발생

## 옵션 결정 (사용자 승인)
- **수정 안**: 옵션 A (3-spot fix) — 변경 1·2·3 모두 적용 + scorer.py 가드 코멘트 (변경 4)
- **소급 복구**: 옵션 2 (NULL 마킹) — 박제된 컬럼 NULL UPDATE
- **단발 검증 스크립트**: 작성

## 구현 단계
1. `main.py` 상단에 헬퍼 추가:
   - `_coalesce_zero(v)` — SQL NULL → 0, 실제 0/0.0 보존
   - `_pick_momentum(t)` — momentum_score → momentum 폴백 체인 (is None 체크)
2. `main.py:187-197` (DB 복원 normalized): momentum/supply_ratio/news_count/ai_sentiment 4개 키 추가, `_coalesce_zero` 사용
3. `main.py:438` (기존 테마 유지 저장): `_pick_momentum(t)` 사용
4. `main.py:1950-1957` (midweek replacement append): 점수 4개 키 추가, `_coalesce_zero` 사용
5. `scorer.py:652-673` (scored_theme dict): 가드 코멘트 추가 (momentum/momentum_score 양쪽 키 출력 의존성 명시)
6. `tests/test_theme_dict_normalization.py` 신규: TC-1~TC-5 (`:memory:` SQLite + 함수 단위)
7. `scripts/verify_theme_restore_keys.py` 신규: 단발 검증 스크립트
8. `scripts/repair_theme_zeros_2026_05.py` 신규: NULL 마킹 (백업 → dry-run → confirm → 실행)
9. `code-tester` 에이전트로 변경 검증
10. `pytest` + `py_compile` 통과
11. systemctl restart (5/7 17:30 KST 이후 또는 5/8 07:50 이전)
12. `docs/improvements/change_log.md` 1줄 추가
13. `memory/MEMORY.md` 1줄 추가

## 변경 파일 목록
| 경로 | 변경 |
|------|------|
| `main.py` | 헬퍼 2개 + 수정 1·2·3 |
| `modules/theme_analyzer/scorer.py` | 가드 코멘트 |
| `tests/test_theme_dict_normalization.py` | 신규 |
| `scripts/verify_theme_restore_keys.py` | 신규 |
| `scripts/repair_theme_zeros_2026_05.py` | 신규 |
| `docs/improvements/change_log.md` | 1줄 |
| `memory/MEMORY.md` | 1줄 |

## 롤백 계획
- **코드**: `git revert <hotfix-commit>` 한 번 (단일 PR)
- **데이터**: `data/trading.db.backup_YYYYMMDD_HHMMSS`에서 themes 테이블 복원 (NULL 마킹 실행한 경우)
- **부분 중단**: 변경 1·3만 제거하고 변경 2(폴백 체인)는 유지 → 기본 회귀 차단 유지

## 완료 기준
1. 단위 테스트 5건 PASS
2. py_compile 통과
3. code-tester 심각 0건
4. systemd 재시작 후 "테마 로테이션 복원" 로그 정상
5. 단발 검증 스크립트 실행 시 `today_themes` 5건 momentum/ai_sentiment 키 보유 + 정상값
6. 5/8 (금) 08:30 KST 후 themes 테이블 selected=1 5건 모두 momentum > 0 또는 ai_sentiment > 0 (정상값)
7. 5/12 (화) 정규 재선정 + 5/13 (수) "기존 테마 유지" 저장 회귀 없음
8. 박제 4일분(5/4~5/7) selected=1 row 모두 NULL 마킹 완료 (소급 복구)

## 작업 후 갱신 대상 문서
- `docs/improvements/change_log.md` (필수)
- `memory/MEMORY.md` (필수)
- `CLAUDE.md` (선택 — DB→in-memory NULL 방어 패턴 1줄)
- `CHECKLIST.md` 모든 항목 [x] 후 active → completed/20260507_theme-score-zero-fix/
