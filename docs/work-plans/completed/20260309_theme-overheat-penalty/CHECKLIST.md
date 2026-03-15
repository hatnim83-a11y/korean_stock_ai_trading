# CHECKLIST: 테마 점수 과열 감점

## 구현 항목
- [x] 배점 상수 5개 변경
- [x] calculate_overheat_penalty() 함수 추가
- [x] score_themes() 통합 수정 (overheat 반영)
- [x] 모멘텀 clamping -10~+10
- [x] 종목수 보너스 stock_count * 1
- [x] MIN_SELECTION_SCORE = 30.0
- [x] 독스트링/주석 업데이트
- [x] main.py:1487 BASE_SCORE 하드코딩 + overheat_penalty 누락 수정
- [x] selector.py format_theme_report 구 배점 하드코딩 수정

## 검증 항목
- [x] py_compile scorer.py, selector.py, main.py
- [x] calculate_overheat_penalty 단위 테스트 (7개 경계값 PASS)
- [x] calculate_momentum_score 단위 테스트 (4개 PASS)
- [x] code-tester 에이전트 검증 → 심각 1건 수정 완료

## 배포 항목
- [ ] systemd 서비스 재시작

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트 (배점 변경 기록)
