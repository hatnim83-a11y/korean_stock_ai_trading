# PLAN: 거래 개선 에이전트 Phase 2 — 월간 모드 검증 + 미결 검토 통합

## 목표
Phase 1에서 정의만 해둔 `/improve monthly` 모드를 **실전에서 처음으로 실행**하여 (1) 저표본 월간 구간에서 에이전트가 에이전트 정의서의 "판단 유보" 규칙대로 작동하는지, (2) 미결 검토 항목(`project_stop_loss_review.md`, `project_gap_filter_review.md`, `project_hold_days_review.md`) 3건이 섹션 6 통합 플로우에 제대로 반영되는지 검증한다. 필요 시 에이전트 정의서를 미세 보완한다.

## 배경
- 2026-04-21 Phase 1 완료 커밋 `2ed8d5d` — `monthly` 모드 정의만 포함, 실행 이력 없음
- 현재 DB 상태 (2026-04-21 KST 기준):
  - `trade_reviews` 전체 34건, 최근 30일 **11건** (월간 임계값 N≥15 미달)
  - 최근 30일 11건 중 `ai_review` JSON 보유 5건 / NULL 6건 / 파싱 실패 0건
  - `post_trade_prices` 최근 30일 40건 (D+5 추적 데이터 풍부)
- 에이전트 정의(`.claude/agents/trade-improvement-analyst.md:24`): "월간 모드 N<15일 때는 판단 유보 섹션으로 결과 제출"
- 미결 검토 메모 3건 모두 `/home/hatni/.claude/projects/-home-hatni-korean-stock-ai-trading/memory/`에 존재

## 구현 단계 (Phase 2 세부)

### Phase 2-A: 리허설 (실행 검증)
- [ ] `/improve monthly` 실행 → 에이전트가 제안서 `docs/improvements/2026-04-monthly.md` 생성
- [ ] 기대 동작 확인:
  - frontmatter `sample_size: 11`, `mode: monthly`, `analysis_period: 2026-03-22 ~ 2026-04-21`
  - 섹션 5 파라미터 제안 **생략** (유보 선언)
  - 섹션 6에 미결 검토 3건 현재 상태 기록
  - 섹션 8 다음 사이클 관찰 항목에 "5월 초 재분석 (N≥15 기대)" 포함
- [ ] JSON 파싱 실패 0건, 민감 데이터 미포함 재확인

### Phase 2-B: 정의 보완 (리허설 결과 반영)
리허설 결과에 따라 선택적으로 진행:
- [ ] `ai_review` NULL 6건 원인 조사 — `post_trade_analyzer` 실행 실패/지연 여부 확인 (제안서 기록)
- [ ] 저표본 월간(N=10~14) 구간 처리 방침 명시 여부 결정 — 필요 시 `.claude/agents/trade-improvement-analyst.md`에 "관찰 수준 기록만 허용" 1~2줄 추가
- [ ] 파일명 충돌 시 `-v2` 접미사 규칙 재확인

### Phase 2-C: 미결 검토 통합 검증
- [ ] 제안서 섹션 6이 `memory/project_stop_loss_review.md`의 "5월 초 재평가" 일정과 일관
- [ ] 섹션 6이 `memory/project_gap_filter_review.md`의 "2026-04 초 재분석" 결과를 반영 (이미 지난 일정)
- [ ] 섹션 6이 `memory/project_hold_days_review.md`의 "매도후 평가 데이터 축적" 상태를 인용

## 변경 파일 목록
**신규 (에이전트 생성)**:
- `docs/improvements/2026-04-monthly.md` — 월간 리허설 제안서 1건

**선택 수정 (리허설 결과 따라)**:
- `.claude/agents/trade-improvement-analyst.md` — 저표본 월간 처리 방침 1~2줄 추가 여부
- `memory/project_stop_loss_review.md` 등 — 제안서가 재평가 일정을 앞당기거나 결론을 제시하면 업데이트

**Python 코드 미변경** — `config.py`, `main.py`, `database.py` 등 운영 코드는 건드리지 않는다. 에이전트는 도구 화이트리스트로 기술적 차단.

## 롤백 계획
- 제안서 `docs/improvements/2026-04-monthly.md` 삭제 (단순)
- 에이전트 정의 수정이 있었다면 `git revert` 또는 해당 섹션 수동 되돌리기
- 프로젝트 메모리 업데이트는 해당 파일에서 Edit으로 제거

## 완료 기준
- CHECKLIST.md 구현 3항목 + 검증 4항목 + 문서 업데이트 2항목 모두 `[x]`
- 제안서가 유보 모드로 올바르게 작동했음을 육안 검증 완료
- Phase 2 학습 내용 `memory/project_trade_improvement_agent.md`에 1줄 추가

## 후속 단계 (Phase 3, 별도)
- Phase 3 (데이터 충분 시): 월간 제안서 실전 운영 — N≥15 도달 후 5월 초 재실행
- Phase 3+: DB 테이블 `improvement_proposals` 신설 + 월간 자동 스케줄
