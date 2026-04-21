# MCP 서버 활용 가이드

> 프로젝트 `.mcp.json`에 등록된 3개 MCP 서버의 상세 사용법.
> 작업 성격상 MCP가 도움될 것 같으면(1% 규칙) 적극 활용할 것.

## 1. SQLite (`mcp__sqlite__*`) — DB 조회/수정

- **DB 경로**: `data/trading.db` (래퍼: `scripts/mcp_sqlite.py`)
- **용도**: 포트폴리오, 거래내역, 테마, 성과 등 트레이딩 DB 직접 조회/수정
- **사용 시점**: 봇 상태 확인, 거래 분석, 데이터 검증, 스키마 확인 시 Python 코드 실행 대신 MCP로 직접 조회

### 도구 목록
| 도구 | 용도 |
|------|------|
| `list_tables` | 전체 테이블 목록 |
| `describe_table` | 테이블 스키마 확인 |
| `read_query` | SELECT 쿼리 (조회 전용) |
| `write_query` | INSERT/UPDATE/DELETE 쿼리 |
| `create_table` | 테이블 생성 |
| `append_insight` | 분석 인사이트 메모 저장 |

### 주의사항
- **write_query 실행 전 반드시 사용자 확인.** 실서비스 DB이므로 함부로 수정 금지
- 실제 DB 경로는 `data/trading.db` (루트의 `trading.db`는 빈 파일 — 주의)
- 스키마 버전(`schema_version` 테이블) 확인 후 쿼리 작성

### 자주 쓰는 쿼리 예시
```sql
-- 현재 보유 포지션 + 트레일링 상태
SELECT stock_code, stock_name, shares, buy_price, trailing_active, trailing_level, trailing_stop
FROM portfolio WHERE shares > 0;

-- 최근 거래 30건
SELECT datetime, action, stock_code, price, shares, reason FROM trades ORDER BY id DESC LIMIT 30;

-- 테마 최근 점수
SELECT theme_name, final_score, created_date FROM theme_scores
WHERE created_date = (SELECT MAX(created_date) FROM theme_scores);
```

## 2. Fetch (`mcp__fetch__fetch`) — 웹 페이지 조회
- **용도**: URL의 웹 페이지 내용을 마크다운으로 변환하여 조회
- **파라미터**: `url`(필수), `max_length`(기본 5000), `start_index`(이어읽기), `raw`(HTML 원본)
- **사용 시점**: API 문서 확인, 외부 참고자료 조회, 에러 관련 GitHub 이슈 확인 등
- **주의**: 사용자가 제공한 URL 또는 프로그래밍 관련 URL만 사용

## 3. Sequential Thinking (`mcp__sequential-thinking__sequentialthinking`) — 구조적 사고
- **용도**: 복잡한 문제를 단계별로 분석, 가설 생성 및 검증
- **사용 시점**: 복잡한 버그 원인 분석, 전략 설계, 아키텍처 결정 등 다단계 추론 필요 시
- **특징**: 중간에 이전 사고 수정, 분기, 추가 확장 가능

## 설정 주의사항
- **MCP SQLite 인자 형식**: `--db-path` 플래그는 무효. 위치 인자로 DB 경로 전달 필수
  (상세: `memory/feedback_mcp_sqlite_config.md`)
