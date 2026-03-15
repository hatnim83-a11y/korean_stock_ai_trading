# PLAN: 테마 카테고리 자동 분류 (Phase 2)

## 목표
크롤링된 테마에 자동 카테고리 분류 적용 → _select_with_diversity() 활성화

## 배경
- DB `themes.category` 컬럼, `_select_with_diversity()` 로직 존재
- 현실: 94%가 '기타' → 다양성 로직 무력화
- 원인: 네이버/KRX 크롤링 결과에 category 필드 없음, predefined 20개만 분류

## 구현 단계

### Step 1: crawlers.py에 THEME_CATEGORY_MAP 추가
- 키워드 → 카테고리 매핑 딕셔너리
- 카테고리: 반도체, 2차전지/에너지, 바이오, IT/SW, 방위/우주, 소비재, 산업재, 기타

### Step 2: classify_theme_category() 함수 추가
- 테마명에 키워드 포함 여부로 분류
- 매칭 실패 시 '기타' 반환

### Step 3: crawl_all_themes()에서 분류 적용
- 크롤링 결과 각 테마에 category 필드 추가

### Step 4: score_themes()에서 category 전달 확인
- scored_theme dict에 category가 포함되는지 확인

## 변경 파일
| 파일 | 변경 |
|------|------|
| `modules/theme_analyzer/crawlers.py` | THEME_CATEGORY_MAP, classify_theme_category(), crawl_all_themes 적용 |

## 완료 기준
- py_compile 통과
- 실제 크롤링 데이터로 분류 비율 확인 ('기타' < 30%)
- code-tester 검증
