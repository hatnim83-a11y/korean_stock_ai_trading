# CHECKLIST: 테마 카테고리 자동 분류

## 구현 항목
- [x] THEME_CATEGORY_MAP 키워드 매핑 추가 (7개 카테고리, 80+ 키워드)
- [x] classify_theme_category() 함수 추가 (None/빈 문자열 방어 포함)
- [x] crawl_all_themes()에서 분류 적용 (기존 category='기타'인 경우만)
- [x] predefined 테마 category와 충돌 없음 확인
- [x] 누락 키워드 보강 (방산, SMR, 원전, 자율주행, 통신, 금융, SPAC, REITs)

## 검증 항목
- [x] py_compile crawlers.py
- [x] 실제 DB 테마 81개 분류: 기타 7.4% (목표 <30% 달성)
- [x] 보강 후 9개 경계값 테스트 ALL PASS
- [x] code-tester 에이전트 검증 (심각 0건)

## 배포 항목
- [x] systemd 서비스 재시작

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트
