# CHECKLIST: 거래 개선 전문 에이전트 도입 (Phase 1)

## 구현
- [x] 사전 검증: `mcp__sqlite__read_query`로 `data/trading.db` 조회 성공 (34 trade_reviews)
- [x] 사전 검증: `trade_reviews.ai_review` 샘플 `json.loads()` 성공률 확인 (28/28 = 100%)
- [x] `.claude/agents/trade-improvement-analyst.md` 작성 (model:opus, tools 8개 화이트리스트)
- [x] `.claude/commands/improve.md` 작성 (weekly/monthly/focus 모드)
- [x] `docs/improvements/README.md` 작성
- [x] `docs/improvements/_TEMPLATE.md` 작성 (frontmatter `analysis_period` 포함)
- [x] `docs/improvements/queries.md` 작성 (11개 샘플 쿼리)
- [x] `docs/improvements/change_log.md` 초기 파일 생성

## 검증
- [x] 에이전트 YAML tools 필드에 Edit/write_query/create_table 부재 확인 (frontmatter 직접 확인)
- [x] 리허설: `focus:stop_loss` 제안서 생성 (`docs/improvements/2026-04-21-focus-stop_loss.md`)
  - ※ 새 에이전트가 세션 캐시에 아직 로드되지 않아 Task 호출 불가 → 에이전트 정의를 수동 준용하여 리허설 수행. 세션 재시작 후 `/improve focus:stop_loss`로 정식 재현 가능
- [x] 제안서에 표본 수 / 쿼리 원문 / 현재값→제안값 / 근거 / 신뢰도 포함 확인
- [x] JSON 파싱 실패 카운트 명시 확인 (0건)
- [x] `memory/project_stop_loss_review.md`와 결론 일관성 확인 — 반전 방향을 명시적으로 기록

## 배포
- [x] `systemctl status trading_system` 정상 확인 (53분 실행 중, 영향 없음 — Phase 1은 Python 코드 미변경)
- [x] 승인된 제안 구현 시 `change_log.md` 1줄 추가 프로세스를 규칙화 — 다음 위치 모두 명시됨:
  - `.claude/agents/trade-improvement-analyst.md` (에이전트 정의)
  - `docs/improvements/README.md` (관리 규칙)
  - `CLAUDE.md` 루트 ("전략/파라미터 변경 시 필수 프로세스" 섹션)
- [ ] `git status`로 모든 변경 파일 확인 후 커밋 (사용자 승인 필요)

## 문서 업데이트
- [x] `docs/INDEX.md` — `docs/improvements/` 섹션 추가 + 진행 중 작업 등록
- [x] `CLAUDE.md` (루트) — "전략/파라미터 변경 시 필수 프로세스" 항목 추가
- [x] `memory/MEMORY.md` — `project_trade_improvement_agent.md` 1줄 추가
- [x] `memory/project_trade_improvement_agent.md` — 프로젝트 메모리 신규 작성

## 리허설 발견 (본 작업에서 드러난 추가 과제)
1. 새 에이전트는 **세션 재시작 후** `/improve` 호출 시 정상 로드 (Claude Code 특성)
2. 손절 후 D+5 반등 패턴 강력 관찰 → `memory/project_stop_loss_review.md` 방향 재검토 필요 (5월 초)
3. `trade_reviews.profit_rate` 컬럼이 이미 퍼센트 단위 저장 → 후속 쿼리/분석에서 `*100` 중복 금지
4. Phase 2에서 월간 모드 구현 시 위 발견들 반영
