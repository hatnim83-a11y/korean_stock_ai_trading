# 금융 카테고리 분리 (산업재 → 금융 독립)

## 목표
`_CATEGORY_KEYWORDS`에서 금융 키워드(보험/금융/증권/은행/리츠/REITs/SPAC)를 산업재에서 분리하여 "금융" 카테고리로 독립. 기존 DB 오분류 6개 테마를 재분류하여 섹터 분산 필터(`MAX_STOCKS_PER_SECTOR=3`)가 금융과 산업재를 독립적으로 제한하도록 함.

## 배경
2026-04-15 09:25 매수 리포트에서 삼성증권이 "섹터 분산 (산업재 3/3)"으로 탈락. 기존 보유(키움증권/HD한국조선해양/HJ중공업) 3종목이 모두 산업재로 분류된 결과. 근본 원인은 `modules/theme_analyzer/crawlers.py:771-773` `_CATEGORY_KEYWORDS`에서 조선/철강과 보험/증권/은행이 하나의 산업재 바구니에 묶여있기 때문.

## 구현 단계

### Step 1: `_CATEGORY_KEYWORDS` 개정
- [ ] `modules/theme_analyzer/crawlers.py:754-774` 수정
  - "산업재"에서 보험/금융/증권/은행/리츠/REITs/SPAC 제거
  - "금융" 신규 엔트리 추가 (산업재 뒤 배치, IT/SW 우선순위 유지)
  - `자동화기기` → `금융자동화기기`로 좁혀 산업 자동화 오분류 방지

### Step 2: DB 마이그레이션 v13 추가
- [ ] `database.py:175` `migrations` 리스트에 `(13, "themes 산업재→금융 재분류", self._migrate_v13)` 추가
- [ ] `_migrate_v12()` 뒤에 `_migrate_v13()` 메서드 정의
- [ ] 화이트리스트 방식(theme_name IN) 채택 — 광범위 LIKE 거짓 양성 방지
- [ ] 대상 6개 테마: 금융, 생명보험, 손해보험, 기업인수목적회사(SPAC), 리츠(REITs), 화폐/금융자동화기기(디지털화폐 등)
- [ ] `schema_version` 직접 INSERT 금지 (auto 처리)

### Step 3: 코드/DB 검증
- [ ] `classify_theme_category()` 긍정/부정 샘플 통과
- [ ] MCP SQLite로 카테고리 분포 재확인

### Step 4: code-tester 에이전트 검증
- [ ] 수정된 2개 파일 대상 실행

### Step 5: 서비스 재시작
- [ ] `sudo systemctl restart trading_system`
- [ ] 마이그레이션 로그 확인

## 변경 파일
| 파일 | 내용 |
|------|------|
| `modules/theme_analyzer/crawlers.py` | `_CATEGORY_KEYWORDS` 개정 (금융 카테고리 신설) |
| `database.py` | v13 마이그레이션 추가 |

## 변경 불필요
- `crawlers.py:720` predefined 금융 (이미 `category: "금융"`)
- `scripts/backtest_live_logic.py:76-80` (이미 정합)
- `main.py:1187-1239` (카테고리 문자열 비교만 수행)
- `config.py:216` `MAX_STOCKS_PER_SECTOR=3` 유지

## 접근 방식
- 키워드 매칭 순서 유지: IT/SW → ... → 산업재 → 금융
- 마이그레이션은 화이트리스트 기반 (현재 DB에 실제 존재하는 6개 테마명만)
- 과거 모든 날짜 row 일괄 업데이트 → `weekly_aggregator` 집계 일관성 유지

## 롤백 계획
1. 코드: `git revert <commit>`
2. DB 타겟: `UPDATE themes SET category='산업재' WHERE category='금융' AND theme_name IN (...); DELETE FROM schema_version WHERE version=13;`
3. DB 전체: 자동 백업(`data/trading.db.bak.YYYYMMDD_HHMMSS`)에서 복원

## 완료 기준
- `_CATEGORY_KEYWORDS` 수정 및 검증 통과
- DB v13 적용 후 6개 테마 `category='금융'` 확인
- 서비스 재시작 후 다음 거래일 09:25 매수 시 금융주가 산업재로 묶이지 않음
