# CHECKLIST: 봇 점검 이슈 5건 수정

## 구현 항목
- [x] Fix 1: screener.py에 screening_log 저장 호출 추가
- [x] Fix 2: crawlers.py KRX 크롤러 에러 핸들링 강화
- [x] Fix 3: verifier.py EXCLUDE_RECOMMENDATIONS에 "Hold" 추가
- [x] Fix 4: dashboard_service.py KISApi 싱글톤 패턴 적용
- [x] Fix 5: crawlers.py 네이버 크롤링 종목코드 유효성 검증
- [x] 추가: screening_logs 저장 조건을 candidates 유무에서 분리
- [x] 추가: screen_all_themes 테마별 예외 격리 (try/except)
- [x] 추가: screening_log UNIQUE 충돌 방지 (INSERT OR IGNORE)
- [x] 추가: verifier.py datetime.now() → now_kst() 수정
- [x] 추가: 반환 타입 힌트 tuple로 업데이트

## 검증 항목
- [x] py_compile 전 수정 파일 통과 (5개 파일)
- [x] code-tester 에이전트 검증

## 배포 항목
- [ ] 서비스 재시작 (사용자가 수동으로 진행)

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트
