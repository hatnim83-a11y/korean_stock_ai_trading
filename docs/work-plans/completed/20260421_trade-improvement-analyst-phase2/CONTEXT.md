# CONTEXT: Phase 2 월간 모드 검증

## 변경 이유
Phase 1에서 `/improve weekly`, `/improve focus:<topic>` 경로는 리허설로 검증됐지만 `/improve monthly` 경로는 **정의만 있고 한 번도 실행된 적이 없다**. 자동 월간 스케줄(매월 1일) 도입 전에 수동 실행으로 동작을 확인하고 엣지 케이스(저표본 유보, 미결 검토 통합)를 미리 검증한다.

## 현재 코드 상태 (파일:라인)

### 에이전트 정의
- `.claude/agents/trade-improvement-analyst.md:3` — description에 "월간(매월 1일)" 명시
- `.claude/agents/trade-improvement-analyst.md:24` — "월간 모드 N<15일 때는 판단 유보"
- `.claude/agents/trade-improvement-analyst.md:32` — monthly 모드 = "지난 30일 데이터"
- `.claude/agents/trade-improvement-analyst.md:43` — 월간 파일명 규칙 `YYYY-MM-monthly.md`

### 슬래시 명령
- `.claude/commands/improve.md:6` — `monthly → 지난 30일`
- `.claude/commands/improve.md:10` — "반드시 `trade-improvement-analyst` 에이전트를 Task 도구로 호출"

### 쿼리 템플릿
- `docs/improvements/queries.md` — 월간/주간 공통 SQL 템플릿 (expand 필요 시 여기서 보강)

## DB 현재 상태 (2026-04-21 KST)

```
trade_reviews:       전체 34건, 최근 30일 11건, 최근 7일 5건
  - 30일 ai_review NOT NULL: 11건
  - 30일 JSON 파싱 성공:     5건
  - 30일 ai_review NULL:     6건  ← 조사 필요
  - 30일 파싱 실패:          0건

post_trade_prices:   최근 30일 40건 (D+5 추적)
```

월간 임계값 N≥15 미달 → **에이전트는 "판단 유보" 모드로 작동해야 한다**.

## 핵심 스니펫

### 에이전트의 표본 임계값 로직
```markdown
# .claude/agents/trade-improvement-analyst.md:24
3. **표본이 부족하면 판단을 유보하라.** 주간 모드 N<5, 월간 모드 N<15일 때는 "판단 유보" 섹션으로 결과를 제출한다.
```

### 제안서 섹션 5 (유보 시 생략)
```markdown
# .claude/agents/trade-improvement-analyst.md:189
| 표본 < 임계값 | "판단 유보" 섹션으로 제출 (섹션 5 파라미터 제안 생략) |
```

### 섹션 6 미결 검토 템플릿
```markdown
# .claude/agents/trade-improvement-analyst.md:100
## 6. 미결 검토 항목 결론
- project_stop_loss_review.md: 진행/미결/결론 중 택일 + 한 줄 이유
- project_gap_filter_review.md: ...
- project_hold_days_review.md: ...
```

## 미결 검토 메모 현재 상태

**경로**: `/home/hatni/.claude/projects/-home-hatni-korean-stock-ai-trading/memory/`

| 파일 | 현재 결론 | 재평가 시점 |
|-----|----------|------------|
| `project_stop_loss_review.md` | V자 회복 전수조사 완료, 축적 대기 | 2026-05-01 (4월 데이터) |
| `project_gap_filter_review.md` | 데이터 축적 후 기준 조정 검토 | 2026-04 초 (이미 지남) |
| `project_hold_days_review.md` | 매도후 평가 데이터 축적 후 재검토 | 미정 |

Phase 2 제안서는 이 3건 각각에 대해 **"현재 사이클에서 추가 근거 확보 여부"** 를 기록해야 한다.

## 영향 범위

### 직접 영향
- 신규 제안서 파일 1건 (`docs/improvements/2026-04-monthly.md`)
- 선택: 에이전트 정의 1~2줄 보완

### 간접 영향
- `modules/post_trade_analyzer/`: ai_review NULL 6건 원인 조사가 제안되면 추후 디버깅 대상이 될 수 있음 (이번 작업에서는 조사 권고만)
- `memory/project_*_review.md`: 섹션 6 결론이 기존 메모와 모순되면 메모 업데이트 필요

### 비영향
- `config.py` 파라미터 — 수정 금지 (Phase 2는 코드 변경 없음)
- 운영 서비스 — 제안서는 stdout/파일 출력만, 서비스 재시작 불필요

## 과거 버그/주의점
1. **Phase 1 리허설에서 에이전트가 세션 캐시에 로드되지 않아 Task 호출 실패** — 이번에도 동일 증상 시 정의를 수동 준용하여 리허설 수행 (Phase 1 완료 CHECKLIST 36행 기록)
2. `trade_reviews.profit_rate`는 **이미 퍼센트 단위**로 저장됨 — 쿼리에서 `*100` 중복 금지 (Phase 1 학습)
3. `Bash date +%V`는 시간대 차이로 주차 오인 가능 — 반드시 `now_kst().isocalendar()` 사용 (에이전트 정의서 46행)
4. Phase 1 제안서(`2026-04-21-focus-stop_loss.md`)가 이미 `docs/improvements/`에 있음 → 월간 제안서와 파일명 충돌 없음(형식 다름)
