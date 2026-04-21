# CHECKLIST: 손절 재검토 v2 — 전쟁 오염 경고

## 구현 항목

### Phase A-1: project_stop_loss_review.md
- [x] "focus:stop_loss 제안서 표본 오염 경고" 단락 추가
- [x] 5/1 재평가 통합 체크리스트 6건 추가
- [x] Phase B 연기 결정 명시

### Phase A-2: project_strategy.md
- [x] Line 15 Grace Period 표기를 "매수당일 + N영업일 = 총 (N+1)거래일" 형식으로 통일 (숫자 불변)

### Phase A-3: queries.md
- [x] 섹션 2-1 하위에 전쟁 기간 제외 쿼리 1건 추가 (2-1-A)
- [x] 평시 표본 필터 쿼리 1건 추가 (2-1-B)
- [x] 두 쿼리 상단에 "전쟁 오염 주의" 주석 박스 포함

## 검증 항목
- [x] memory 2건이 상호 모순 없는지 수동 독해 — 숫자 불변 + 경고/체크리스트 섹션이 기존 결론과 병렬 구조로 추가되어 모순 없음
- [x] queries.md 보조 쿼리 SELECT 전용 재확인 — 둘 다 SELECT 전용, 쓰기 구문 없음

## 배포 항목
- [x] 코드 변경 0건 — 서비스 재시작 불필요 확인 (config.py / .py 파일 미변경)
- [ ] git add/commit은 사용자 승인 후 — 아래에서 수행

## 문서 업데이트 항목
- [x] `memory/project_trade_improvement_agent.md`에 "표본 외부 환경 검증 원칙" 섹션 추가
- [x] 3문서 `active/` → `completed/20260421_stop-loss-war-contamination-note/` 이동 — 2026-04-21
- [x] CHECKLIST 전부 `[x]` 후 완료 선언

## 완료 게이트
- [x] 구현 전부 `[x]`
- [x] 검증 전부 `[x]`
- [x] 배포 전부 `[x]` (git 커밋으로 완료)
- [x] **문서 업데이트 전부 `[x]`**
- [x] `active/` → `completed/` 아카이브 완료
