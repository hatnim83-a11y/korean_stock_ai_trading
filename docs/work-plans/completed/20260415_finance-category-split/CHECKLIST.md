# CHECKLIST: 금융 카테고리 분리

## 구현 항목
- [x] `modules/theme_analyzer/crawlers.py:771-773` — 산업재에서 금융 키워드 제거
- [x] `modules/theme_analyzer/crawlers.py:754-778` — "금융" 신규 엔트리 추가 (산업재 뒤)
- [x] `자동화기기` → `금융자동화기기`로 키워드 좁힘
- [x] `database.py:176` migrations 리스트에 `(13, "themes 산업재→금융 재분류", self._migrate_v13)` 추가
- [x] `database.py` `_migrate_v12()` 뒤에 `_migrate_v13()` 메서드 정의 (화이트리스트 6개 theme_name)
- [x] `_migrate_v13`에서 schema_version 직접 INSERT 금지 확인

## 검증 항목
- [x] py_compile: 두 파일 통과
- [x] `classify_theme_category('금융')` == '금융'
- [x] `classify_theme_category('증권')` == '금융'
- [x] `classify_theme_category('은행')` == '금융'
- [x] `classify_theme_category('보험')` == '금융'
- [x] `classify_theme_category('리츠(REITs)')` == '금융'
- [x] `classify_theme_category('기업인수목적회사(SPAC)')` == '금융'
- [x] `classify_theme_category('생명보험')` == '금융'
- [x] `classify_theme_category('손해보험')` == '금융'
- [x] `classify_theme_category('조선')` == '산업재'
- [x] `classify_theme_category('철강')` == '산업재'
- [x] `classify_theme_category('해운')` == '산업재'
- [x] `classify_theme_category('건설')` == '산업재'
- [x] `classify_theme_category('핀테크(FinTech)')` == 'IT/SW' (순서 우선순위)
- [x] `classify_theme_category('스테이블코인')` == 'IT/SW'
- [x] `classify_theme_category('공장자동화')` == '기타' (키워드 좁힘 효과)
- [x] code-tester 에이전트 pass (심각 이슈 없음, 주의 1건은 의도된 설계)
- [x] MCP SQLite 재분류 후 조회: 6개 테마 모두 `category='금융'` 확인

## 배포 항목
- [x] 기존 프로세스 확인 (PID 2498325)
- [x] 서비스 재시작: `sudo systemctl restart trading_system` (PID 2801426)
- [x] 서비스 상태: active (running) since 2026-04-15 05:26:05 UTC
- [x] v13 마이그레이션 로그 확인: 6개 테마 24행 재분류 완료
- [x] 모니터링 정상 재개: 4종목 트레일링 상태 복원

## 문서 업데이트 항목
- [x] `memory/MEMORY.md` — 신규 메모 `project_finance_category_split.md` 등록
- [x] `active/finance-category-split/` → `completed/20260415_finance-category-split/` 이동
