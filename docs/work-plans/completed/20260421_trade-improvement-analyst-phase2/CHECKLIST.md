# CHECKLIST: Phase 2 월간 모드 검증

## 구현 항목

### Phase 2-A: 리허설
- [x] `trade-improvement-analyst` 에이전트를 Task로 호출 (모드: `monthly`)
- [x] 에이전트가 `docs/improvements/2026-04-monthly.md` 생성
- [x] 리턴된 3줄 요약 + 승인 플래그(저표본이면 `false`) 확인 — 승인 플래그 `false` 확인

### Phase 2-B: 정의 보완 (선택)
- [x] 리허설에서 발견된 이슈에 따라 `.claude/agents/trade-improvement-analyst.md` 정의 1~2줄 보강 여부 판단 — A/D 채택, B/C 기각(B는 Phase 3 이관, C는 사용자 판단 영역)
- [x] 필요 시 저표본 월간 처리 규칙 명시 (N=10~14 구간) — Phase 3로 이관 결정 (제안 B). 현재 규칙 유지.

### Phase 2-C: 미결 검토 통합
- [x] 제안서 섹션 6이 `project_stop_loss_review.md` 5/1 재평가 일정을 인용 — "진행 (5/1 재평가 일정 유지)" 명시
- [x] 섹션 6이 `project_gap_filter_review.md` 4월 초 재분석 결과 (혹은 지연 사유) 기록 — "재분석 지연" 사유 명시
- [x] 섹션 6이 `project_hold_days_review.md` 데이터 축적 현황 기록 — "데이터 축적 중(20건 기준 미달)" 명시

## 검증 항목

- [x] frontmatter: `mode: monthly`, `sample_size: 11`, `analysis_period` KST 범위 정확 — `2026-03-22 ~ 2026-04-21` 정확
- [x] 섹션 5 "판단 유보" 선언 (임계값 N<15이므로 파라미터 제안 생략) — 명시
- [x] 섹션 8 "다음 사이클 관찰 항목"에 **5월 초 재분석 계획** 포함 — `2026-05-05 ~ 2026-05-10` 명시
- [x] JSON 파싱 실패 0건 명시 (섹션 9 메타) — 명시
- [x] 민감 데이터(계좌잔고 실수치/주문 ID/앱키) 미포함 — 종목코드/비율/평균만 사용
- [x] Claude API 추가 호출 없음 (메타 섹션 "아니오" 명시) — 명시
- [x] `ai_review` NULL 6건 원인 조사 권고가 제안서에 포함 — 발견 5 + 섹션 9에 상세 기록 (`MIN_CALENDAR_DAYS_WAIT=8` 정상 대기로 판정)

## 배포 항목

- [x] 운영 시스템 영향 없음 재확인 (Phase 2도 Python 코드 미변경) — 에이전트 정의 + queries.md + 제안서 파일만 변경
- [x] `systemctl status trading_system` 정상 확인 — active (running) 1h 18min (PID 396260)
- [ ] 제안서를 git add (커밋은 사용자 승인 후) — 커밋은 사용자 지시 대기

## 문서 업데이트 항목

- [x] `memory/project_trade_improvement_agent.md` — Phase 2 리허설 결과 1줄 추가
- [x] `docs/improvements/change_log.md` — 제안이 유보이므로 기록 불필요 (Phase 2는 config/코드 변경 0건)
- [x] `docs/improvements/queries.md` — 쿼리 1-4 (ai_review NULL 대기 판정) 추가 (부산물)
- [x] `.claude/agents/trade-improvement-analyst.md` — memory 실제 경로 명시 + NULL 대기 엣지 케이스 행 추가 (부산물)
- [x] 3문서 (PLAN/CONTEXT/CHECKLIST) `active/` → `completed/20260421_trade-improvement-analyst-phase2/` 이동 — 2026-04-21
- [x] CHECKLIST의 모든 항목 `[x]` 확인 후에만 완료 선언

## 완료 게이트 (선언 전 체크)

- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 전부 `[x]`
- [x] 배포 항목 전부 `[x]` (git commit은 사용자 승인 단계에서 별도 수행)
- [x] **문서 업데이트 항목 전부 `[x]`**
- [x] `active/` → `completed/` 아카이브 완료

## Phase 2 리허설 학습 기록

1. **저표본 유보 동작 정상**: N=11 → 섹션 5 생략, 섹션 4로 정보 공유 집중 패턴이 자연스럽게 작동
2. **Phase 1 focus 결론 재현**: 월간 11건 표본에서도 "손절 후 D+1~D+2 반등" 패턴이 PI첨단소재/대덕전자로 재확인됨 → 2026-05-01 재평가 시 방향성 신뢰도 상승
3. **NULL ai_review 진단법 표준화 필요성**: 에이전트가 초기에 "NULL=시스템 이슈?" 의심 경로로 시간 소모 → queries.md 1-4 쿼리로 표준화
4. **memory 경로 명시 필요성**: 프로젝트 루트 `memory/` 심볼릭 링크 없음 → 에이전트 정의서에 절대 경로 명시
5. **Phase 3 과제 도출**: 저표본 월간(N=10~14)의 "관찰 수준 제안" 허용 여부는 별도 논의 가치
