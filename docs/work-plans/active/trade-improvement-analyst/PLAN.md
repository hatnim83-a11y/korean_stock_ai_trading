# PLAN: 거래 개선 전문 에이전트 도입

## 목표
학습 데이터(`trade_reviews`, `post_trade_prices`, `strategy_stats`)가 축적만 되고 시스템에 환류되지 않는 단절을 해결한다. 전담 에이전트 `trade-improvement-analyst`가 데이터를 스캔해 근거 기반 개선 제안서를 생성 → 사용자 승인 → `strategy-coder` 구현 → `change_log.md`에 before/after 추적 → 다음 사이클에서 효과 측정.

## 배경
- `WEEKLY_SUMMARY_PROMPT`가 이미 `parameter_suggestions`를 생성하지만 텔레그램 출력으로 끝남 — 시스템 반영 경로 0
- `project_stop_loss_review.md`(2026-05-01 재평가), `project_gap_filter_review.md`, `project_hold_days_review.md` 등 미결 항목 방치
- 기존 에이전트(strategy-planner/coder/tester/health-checker) 어느 것도 "데이터 기반 개선안 능동 제안" 역할을 맡지 않음

## 구현 단계 (Phase 1 — 이 작업)
1. 사전 검증: MCP SQLite 접속 + `trade_reviews.ai_review` JSON 파싱 샘플 확인
2. `.claude/agents/trade-improvement-analyst.md` 작성 (model:opus, tools 화이트리스트 8개)
3. `.claude/commands/improve.md` 슬래시 명령 작성
4. `docs/improvements/` 4문서 생성: README, _TEMPLATE, queries, change_log
5. `docs/INDEX.md`, `CLAUDE.md`, `memory/MEMORY.md` 업데이트
6. 리허설: `/improve focus:stop_loss` 실행 → 제안서 1건 생성

## 변경 파일 목록
**신규**:
- `.claude/agents/trade-improvement-analyst.md`
- `.claude/commands/improve.md`
- `docs/improvements/README.md`
- `docs/improvements/_TEMPLATE.md`
- `docs/improvements/queries.md`
- `docs/improvements/change_log.md`

**수정**:
- `docs/INDEX.md` — `docs/improvements/` 섹션 추가
- `CLAUDE.md` (루트) — 전략/파라미터 변경 시 리뷰 + change_log 업데이트 항목 1줄
- `memory/MEMORY.md` — 새 에이전트/경로 안내 1줄

**Phase 1 Python 코드 미변경** (config.py, main.py, database.py 등 보존)

## 롤백 계획
- 신규 파일 삭제 + 수정 파일 `git revert`
- `memory/MEMORY.md`는 auto-memory라 Edit으로 수동 제거

## 완료 기준
CHECKLIST.md 참조 — 구현 7항목 + 검증 5항목 + 배포 3항목 + 문서 업데이트 3항목

## 후속 단계 (별도 대화)
- Phase 2: 월간 모드 + 미결 검토 항목 통합 플로우
- Phase 3: DB 테이블 `improvement_proposals` + 자동 스케줄
