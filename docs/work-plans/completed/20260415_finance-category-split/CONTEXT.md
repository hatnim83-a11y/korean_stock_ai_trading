# CONTEXT: 금융 카테고리 분리

## 변경 이유
2026-04-15 09:25 실제 매수 사이클에서 삼성증권이 "섹터 분산 (산업재 3/3)" 사유로 탈락. 사용자 요청: 금융주(삼성증권/키움증권)와 산업재(조선/철강)가 같은 섹터로 묶이면 안 됨.

## 현재 코드 상태

### 원인 위치
- **`modules/theme_analyzer/crawlers.py:771-773`** — `_CATEGORY_KEYWORDS` "산업재" 엔트리:
  ```python
  ("산업재", ["조선", "해운", "건설", "철강", "화학", "자동차부품", "골판지", "아스콘",
            "페인트", "제지", "윤활유", "사료", "수산", "육계", "콩", "대두",
            "보험", "금융", "증권", "은행", "리츠", "REITs", "SPAC"]),
  ```

### 관련 위치 (수정 대상)
- **`modules/theme_analyzer/crawlers.py:777-796`** — `classify_theme_category()` 함수 (순차 매칭, `.upper()` 부분 포함 매칭)
- **`modules/theme_analyzer/crawlers.py:720`** — predefined 금융 테마 (이미 `category: "금융"` 정확, 수정 불필요)
- **`database.py:150-207`** — `_migrate()` 마이그레이션 체인. 자동 백업(line 183-194) + schema_version 자동 INSERT(line 199-203)
- **`database.py:175`** — migrations 리스트 끝 (v12까지 정의됨)
- **`database.py:418`** — `_migrate_v12()` 끝 (v13 메서드 삽입 위치)

### 섹터 분산 로직 (변경 불필요)
- **`main.py:1187-1239`** — `execute_buy_orders()` Phase 5.5
  - line 1189-1194: `theme_to_category` 매번 리빌드 (캐시 없음)
  - line 1229-1234: 섹터 카운트 제한 적용
  - 카테고리 문자열 비교만 사용 → 코드 변경 불필요

### 다운스트림 (모니터링 필요)
- **`modules/theme_analyzer/selector.py:52`** — `MAX_THEMES_PER_CATEGORY=2`
  - 분리 전: 금융+산업재 공유 2슬롯
  - 분리 후: 각 2슬롯 = 최대 4슬롯 (의도된 다양성 확장)
- **`modules/theme_analyzer/weekly_aggregator.py:97-119`** — theme_name 기준 집계. 마이그레이션이 모든 date row 일괄 업데이트하므로 이중 카운트 없음.

## 핵심 코드 스니펫

### classify_theme_category 동작 (crawlers.py:777-796)
```python
def classify_theme_category(theme_name: str) -> str:
    if not theme_name:
        return "기타"
    name_upper = theme_name.upper()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw.upper() in name_upper:
                return category
    return "기타"
```

### _migrate 패턴 (database.py:196-207)
```python
for version, description, migrate_fn in pending:
    try:
        migrate_fn()
        with self.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description)
            )
        logger.info(f"마이그레이션 v{version} 적용: {description}")
    except Exception as e:
        logger.error(f"마이그레이션 v{version} 실패: {e}")
        raise
```
→ `_migrate_v13` 내부에서 schema_version INSERT 금지.

## 현재 DB 상태 (2026-04-15 조회)

```
카테고리별 테마 수 (distinct theme_name):
  기타:             186
  IT/SW:             27
  산업재:            24  ← 이 중 6개가 금융계
  소비재:            15
  2차전지/에너지:    15
  반도체:            12
  바이오:             9
  방위/우주:          5
  에너지:             2 (legacy)
  IT서비스:           2 (legacy)
  통신/신성장/소재/방위산업/금융: 각 1
```

### 재분류 대상 (산업재 → 금융) 6개
- 금융
- 생명보험
- 손해보험
- 기업인수목적회사(SPAC)
- 리츠(REITs)
- 화폐/금융자동화기기(디지털화폐 등)

## 과거 관련 변경
- 2026-03-09: `themes.category` 컬럼 추가 (v10), 7개 카테고리 체계 수립 + `_select_with_diversity` MAX_THEMES_PER_CATEGORY=2 활성화
- 2026-03-03: 테마명 정규화 + 정확 매칭 (`_match_theme_name`)
- 2026-03-26: 테마 유동성 사전 검증 도입

## 영향 범위

### 직접 영향
- 09:25 매수 시 섹터 분산 필터 (`main.py:1187-1239`) — 금융+산업재 독립 카운트
- 주간 테마 선정 `_select_with_diversity` — 금융/산업재 각 2개까지 허용

### 간접 영향
- 대시보드 API 응답의 `category` 필드 값 변경 (UI 표시 없음, 내부 필터링만)
- `weekly_aggregator` 집계 결과의 카테고리 분포 변화 (수치상 재분류)

### 영향 없음
- 백테스트 스크립트 (이미 금융=금융으로 정의됨)
- 포트폴리오 총 슬롯 수 (MAX_POSITIONS 불변)

## 작업 중 발견 사항
- predefined `_PREDEFINED_THEMES`의 "금융" 테마는 이미 `category: "금융"`로 올바르게 정의되어 있었으나, `_CATEGORY_KEYWORDS`가 누락 처리되어 산업재로 재분류되는 이중 저장 문제 존재
- 레거시 카테고리(에너지/IT서비스/소재/방위산업/신성장/통신)는 predefined에는 있으나 `_CATEGORY_KEYWORDS`에는 없음 → 추후 별도 정리 필요 (이번 작업 범위 외)
- `tests/` 디렉토리 없음 — code-tester 에이전트로 수동 검증 대체

## 에이전트 리뷰 반영
- **Plan 에이전트**: SQL 화이트리스트 방식, 타겟 롤백 SQL 추가, selector 사이드이펙트 문서화
- **Coder 에이전트**: `자동화기기` → `금융자동화기기` 좁힘, `_migrate_v13` 내부 schema_version 직접 INSERT 금지 명시
