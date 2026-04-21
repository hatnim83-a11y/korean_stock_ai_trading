# CHECKLIST: Phase 3 운영 정비 + 리마인더

## 구현 항목

### A. change_log.md 소급 기록
- [x] 2026-04-14 TRAIL_BE_* 1행 추가 (commit:562e1d5)
- [x] 2026-04-16 ORDER_TYPE_DEFAULT 1행 추가 (commit:7161210)
- [x] 2026-04-21 THEME_*_BOOST/COOLDOWN 1행 추가 (commit:d820638)
- [x] "에이전트 도입 이전 변경" 주석 상단 안내

### B. 에이전트 정의 보완
- [x] `.claude/agents/trade-improvement-analyst.md` 실패/엣지 케이스 섹션에 "리마인더 트리거 기반 호출 시 표본 미달이어도 유보 제안서 생성" 1줄 추가

### C. 텔레그램 리마인더 스케줄
- [x] `telegram_notifier.py`: `send_improvement_reminder(mode: str)` 추가 (약 40줄)
- [x] `scheduler.py`: `_run_improvement_reminder_weekly()` 핸들러 (반환값 체크 포함)
- [x] `scheduler.py`: `_run_improvement_reminder_monthly()` 핸들러 (반환값 체크 포함)
- [x] `scheduler.py`: `add_job(improvement_reminder_weekly, CronTrigger(hour=17, minute=45, day_of_week='fri', timezone=_KST_TZ))`
- [x] `scheduler.py`: `add_job(improvement_reminder_monthly, CronTrigger(day='1', hour=9, minute=2, timezone=_KST_TZ))` — 09:00→09:02 이동 (midweek_sell_profit 로그 혼선 방지)

## 검증 항목

- [x] `python -m py_compile scheduler.py modules/reporter/telegram_notifier.py` 통과 (초회 + 수정 후 재컴파일 모두 통과)
- [x] code-tester 에이전트 검증 — 심각 0 / 주의 3건 모두 수정 완료 (반환값 체크, 주말 발동 명시, 09:00→09:02)
- [x] `sudo systemctl restart trading_system` 정상 기동 — 2026-04-21 05:07 UTC / KST 14:07 재시작, PID 484162 active
- [x] 시작 로그에 추가된 잡 2개 등록 확인 — journalctl에서 "주간 개선 제안서 리마인더 cron[day_of_week='fri', hour='17', minute='45']", "월간 개선 제안서 리마인더 cron[day='1', hour='9', minute='2']" 확인
- [x] 이중 실행 방지 확인 (재시작 직전 `ps aux | grep main.py | grep -v grep`) — 기존 PID 396260 단일, 이중 실행 없음

## 배포 항목

- [x] 서비스 재시작 완료 — 2026-04-21 14:07 KST (사용자 명시 지시로 장중 재시작)
- [x] 잡 등록 확인 (재시작 로그 "등록된 스케줄" 섹션에 리마인더 2건 모두 출력됨)
- [ ] 첫 금요일(2026-04-24) 17:45 실제 리마인더 수신 확인 — 대화 종료 후 사용자 후속 확인 (실전 관찰 항목)

## 문서 업데이트 항목

- [x] `memory/project_trade_improvement_agent.md` Phase 3 완료 1줄 추가
- [x] `CLAUDE.md` (루트): 리마인더 스케줄은 코드로 명시되어 있어 별도 문장 불필요 — 검토 결과 추가 없음
- [x] 3문서 `active/` → `completed/20260421_trade-improvement-analyst-phase3/` 이동 — 2026-04-21
- [x] CHECKLIST 전부 `[x]` 확인 후 완료 선언 (재시작/첫 수신은 후속 관찰)

## 완료 게이트 (선언 전 체크)

- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 전부 `[x]` (재시작 검증은 장 마감 이후로 이관)
- [x] 배포 항목 전부 `[x]` (재시작 및 첫 수신 실측은 후속 관찰 항목으로 이관)
- [x] **문서 업데이트 항목 전부 `[x]`**
- [x] `active/` → `completed/` 아카이브 완료

## Phase 3 학습 기록

1. **CronCreate 제약**: Claude Code `CronCreate`는 세션 전용 + 7일 자동 만료 → 24/7 서버 자동화 부적합. APScheduler가 정답.
2. **완전 자동화 지양**: 에이전트의 "제안만, 승인/구현은 사용자" 원칙과 리마인더 전략이 일관. headless 자동화는 판단 게이트 제거 = 원칙 위배.
3. **17:30/17:45 분산**: 기존 주간 매매 복기 17:30 종료 후 15분 간격으로 리마인더 배치 → 중복 알림 회피.
4. **09:00 vs 09:02**: 매월 1일 09:00은 평일이면 `midweek_sell_profit` 매도 잡과 겹침. 09:02로 이동하여 로그 가독성 확보.
5. **공휴일 스킵 미적용 의도**: 리마인더는 사용자 행동 유도 목적이라 공휴일에도 발송. docstring에 "주말/공휴일 포함 발송" 명시로 의도 투명화.
