# CONTEXT: 대시보드 테마 영역 분리 + 수급비율 0% 버그 수정

## 변경 이유
- 사용자가 금주 선정 테마와 일별 수집(후보) 테마를 구분할 수 없음
- supply_ratio가 항상 0이어서 수급 판단 정보가 무의미

## 현재 코드 상태

### 테마 API (dashboard_service.py:296-327)
- `get_themes_data()`: selected/candidate 독립 날짜 처리, 3개 필드 반환
- `current_themes` 하위 호환 유지 (selected 우선, 없으면 candidate 폴백)

### DB 저장 지점 (수정 완료)
- `main.py:504-539` (08:30 선정): 전일 DB에서 supply_ratio 조회 후 저장
- `main.py:1711-1730` (17:05 수집): KIS API로 실시간 수급비율 계산 후 저장
- `selector.py:397-407`: `t.get("supply_ratio", 0)` 로 변경

### 수급 계산 (scorer.py:648-717)
- `calculate_theme_supply_ratio(theme_url, kis)`: 종목 8개 크롤링 → KIS API 5일 순매수 조회
- 외국인+기관 순매수 양수인 종목 비율(%) 반환
- circuit breaker: 연속 5회 API 실패 시 중단

## 작업 중 발견 사항

### code-tester 발견 (2026-03-15)
- **심각**: `self.kis` 미정의 → 로컬 `KISApi()` 인스턴스로 수정 완료
- **주의 (기능 영향 없음)**:
  - DB `selected` 컬럼 NULL인 마이그레이션 전 레코드: `None != 1`은 True이므로 candidate로 분류됨 (의도 일치)
  - SQLite에서 `NULL = 0`은 false → 마이그레이션 전 데이터 08:30 매칭 불가 (배포 후 1일 경과하면 해결)
  - 장외시간 KIS API 동작: 17:05 호출이므로 결산 데이터 정상 제공 (확인 필요)
  - `supply_score: 0` (scorer.py:592) 유지 — 총점에 수급 반영은 별도 phase

## 영향 범위
- **직접**: 대시보드 테마 표시, DB supply_ratio 값
- **간접 없음**: 총점(65점) 배점 변경 없음, 매매 로직 영향 없음

## 이번 범위 제외 (후속 Phase)
- 총점 배점 변경 (수급 25점 추가 시 65→90점, 등급 기준 전면 재조정)
- weekly_aggregator.py 수급비율 집계
- scorer.py:592의 supply_score: 0 → 총점 반영
