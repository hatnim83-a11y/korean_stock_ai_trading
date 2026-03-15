# PLAN: 대시보드 테마 영역 분리 + 수급비율 0% 버그 수정

## 목표
1. 대시보드 테마 영역을 "금주 선정"과 "검토/조사 중"으로 2단 분리
2. 수급비율(supply_ratio)이 항상 0%인 버그 수정 — 실제 KIS API 수급 데이터 연결

## 배경
- DB에 `selected` 플래그(1=금주 선정, 0=일별 수집)가 있지만 대시보드에서 구분 없이 표시
- `main.py` 두 DB 저장 지점에서 `supply_ratio: 0` 하드코딩, `scorer.py`에 계산 함수 존재하나 미호출

## 구현 단계

### 작업1: 테마 영역 2단 분리
- [ ] Step 1: `dashboard_service.py:get_themes_data()` — selected/candidate 분리 반환 + 날짜 독립 처리
- [ ] Step 2: `dashboard.html` — HTML 2단 구조 + CSS + JS 렌더링

### 작업2: 수급비율 계산 파이프라인
- [ ] Step 3: `scorer.py` — `calculate_theme_supply_ratio()` 함수 신설
- [ ] Step 4: `main.py:1687` — 17:05 일별 수집에 수급 계산 연결
- [ ] Step 5: `main.py:504` — 08:30 선정에 전일 수급비율 DB 조회
- [ ] Step 6: `selector.py:402` — 하드코딩 0 제거

## 변경 파일 목록
| 파일 | 변경 |
|------|------|
| `web/dashboard_service.py` | get_themes_data() 응답 분리 |
| `web/templates/dashboard.html` | HTML 2단 + CSS + JS |
| `modules/theme_analyzer/scorer.py` | calculate_theme_supply_ratio() 추가 |
| `main.py` (라인 1687, 504) | 수급비율 계산 연결 + 하드코딩 제거 |
| `modules/theme_analyzer/selector.py` | supply_ratio 하드코딩 제거 |

## 접근 방식
- 작업1과 작업2는 독립적 — 병렬 구현 가능
- 기존 `current_themes` 필드 유지 (하위 호환)
- 수급비율은 표시용으로만 저장 — 기존 점수 체계(65점) 변경 없음
- theme["stocks"] 수정하지 않음 — 별도 로컬 변수 사용

## 롤백 계획
- git revert로 전체 롤백 가능
- 각 Step은 독립적이므로 부분 롤백도 가능

## 완료 기준
1. 대시보드에서 선정/검토 테마가 분리 표시
2. 17:05 수집 시 수급비율이 0이 아닌 실제 값으로 저장
3. 08:30 선정 시 전일 수급비율 재사용
4. py_compile 통과 + code-tester 에이전트 검증
