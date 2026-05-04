# CHECKLIST — 3월 분석 기반 수익률 개선

## 구현 항목
- [x] config.py: GRACE_PERIOD_DAYS, GRACE_PERIOD_STOP_LOSS 추가
- [x] config.py: TRAIL_LEVEL1_PCT 0.05 → 0.04
- [x] config.py: MAX_STOCKS_PER_THEME=2, MAX_STOCKS_PER_SECTOR=3 추가
- [x] config.py: MAX_GAP_DOWN_PERCENT 3.0 → 2.0
- [x] config.py: MIN_STRENGTH 45.0 → 50.0
- [x] portfolio_monitor_v2.py: _check_stop_loss() 보호기간 로직
- [x] main.py: Phase 5.5 테마/섹터 분산 필터

## 검증 항목
- [x] py_compile 3파일 통과
- [x] code-tester 에이전트 검증 (심각0, 주의3건 수정 완료)
- [x] 서비스 재시작 성공

## 배포 항목
- [x] 일요일 배포 (장 영향 없음)
- [x] 4/7(화) 동작 확인 — 배포 후 약 3주 운영, 이후 추가 개선(금융 카테고리 분리, Phase A 매수 필터 등)이 본 변경 위에 누적 적용되어 안정 동작 검증됨

## 문서 업데이트 항목
- [x] 프로젝트 메모리 업데이트 (전략 파라미터 변경)
