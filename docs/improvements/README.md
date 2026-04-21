# docs/improvements/ — 거래 개선 제안서

이 디렉토리는 `trade-improvement-analyst` 에이전트가 생성한 거래 개선 제안서와 관련 메타 파일을 담는다.

## 구성

| 파일 | 역할 |
|-----|------|
| `README.md` | (이 파일) 관리 규칙 |
| `_TEMPLATE.md` | 제안서 템플릿 |
| `queries.md` | 표준 SQL 쿼리 세트 (에이전트가 재사용) |
| `change_log.md` | 파라미터 변경 이력 (before/after 추적용) |
| `YYYY-Www-weekly.md` | 주간 제안서 |
| `YYYY-MM-monthly.md` | 월간 제안서 |
| `YYYY-MM-DD-focus-<topic>.md` | 집중 분석 제안서 |

## 관리 규칙

### 1. 민감 데이터 금지
- **포함 가능**: 비율, 통계, 종목코드, 종목명, 파라미터값, 쿼리 원문
- **포함 금지**: 계좌 잔고 실수치(원화 금액), 주문 ID, 앱키/토큰, 개인 식별 정보
- `docs/improvements/*.md`는 git으로 추적되므로 누출 리스크 관리가 필요하다.

### 2. 제안서 수정 원칙
- 에이전트는 기존 제안서를 **덮어쓰지 않는다**. 같은 파일명이 이미 존재하면 접미사 `-v2`, `-v3`을 붙여 저장.
- 사용자는 제안서를 자유롭게 편집 가능하나, **승인 이력/신뢰도/근거 쿼리는 보존**하라.

### 3. change_log.md 갱신 책임
- 에이전트 제안 → 사용자 승인 → `/plan`으로 이관되어 strategy-coder가 구현 후, **strategy-coder가 CHECKLIST 배포 항목에서 `change_log.md`에 1줄 추가**한다.
- 형식: `| YYYY-MM-DD | 파라미터명 | 이전값 | 변경값 | 제안서 경로 | 승인자 |`
- 이것이 다음 사이클에서 before/after 비교의 기준이 된다.

### 4. 아카이브 정책
- Phase 1~2 동안은 평면 구조 유지 (`docs/improvements/` 바로 아래)
- Phase 3에서 제안 수가 월 5건 초과 시 `YYYY/` 연도 디렉토리 분리 검토

### 5. 에이전트 ↔ 제안서 연결
- 제안서 frontmatter에 `generated_at`, `mode`, `sample_size` 필수
- `analysis_period`를 ISO 날짜 범위로 명시해 재현 가능성 보장

## 승인 → 구현 플로우

```
에이전트 제안서 생성
  ↓
사용자 검토 (docs/improvements/*.md)
  ↓
승인 시 사용자가 /plan [제안서 경로] 호출
  ↓
strategy-planner가 3문서 (PLAN/CONTEXT/CHECKLIST) 생성
  ↓
strategy-coder가 구현
  ↓
code-tester가 검증
  ↓
배포 후 strategy-coder가 change_log.md에 1줄 추가 ← 중요
  ↓
다음 사이클에 에이전트가 change_log.md 읽고 before/after 보고
```

## 연관 자산

- 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`
- 슬래시 명령: `.claude/commands/improve.md`
- 기존 사후 분석: `modules/post_trade_analyzer/`
- DB 스키마: `database.py` (trade_reviews, post_trade_prices, strategy_stats, screening_log)
- 프로젝트 메모리: `memory/project_*_review.md`
