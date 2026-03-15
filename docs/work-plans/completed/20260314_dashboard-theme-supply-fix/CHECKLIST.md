# CHECKLIST: 대시보드 테마 영역 분리 + 수급비율 0% 버그 수정

## 구현 항목

### 작업1: 테마 영역 2단 분리
- [x] Step 1: dashboard_service.py — selected/candidate 분리 반환
- [x] Step 2: dashboard.html — HTML 2단 구조 + CSS + JS 렌더링

### 작업2: 수급비율 계산 파이프라인
- [x] Step 3: scorer.py — calculate_theme_supply_ratio() 함수 추가
- [x] Step 4: main.py — 17:05 수집에 수급 계산 연결 + 하드코딩 제거
- [x] Step 5: main.py — 08:30 선정에 전일 수급비율 DB 조회 + 하드코딩 제거
- [x] Step 6: selector.py — supply_ratio 하드코딩 제거

## 검증 항목
- [x] py_compile 4개 파일 전부 통과
- [x] code-tester 에이전트 실행 — 심각 1건(self.kis) 수정 완료, 주의 4건(엣지케이스, 기능 영향 없음)
- [ ] 서비스 재시작 후 대시보드 정상 표시

## 배포 항목
- [ ] systemctl restart trading_system

## 문서 업데이트 항목
- [x] CONTEXT.md — 작업 중 발견 사항 기록
- [x] 이번 범위 제외 사항 → 후속 작업 기록 (PLAN.md에 이미 포함)
