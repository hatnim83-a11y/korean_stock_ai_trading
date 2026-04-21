# PLAN: 거래 개선 에이전트 Phase 3 — 운영 정비 + 리마인더 자동화

## 목표
Phase 1/2에서 구축된 `trade-improvement-analyst` 에이전트를 (1) 데이터 사후 추적이 실제로 흐르도록 과거 변경 3건을 `change_log.md`에 소급 기록하고, (2) 주간/월간 실행 타이밍을 놓치지 않도록 **서버 APScheduler 기반 텔레그램 리마인더**를 도입한다. 완전 자동화(에이전트가 직접 제안서 생성)는 의도적으로 제외 — 사용자 판단 단계를 유지한다.

## 배경
- Phase 2 리허설에서 `change_log.md`가 비어 있어 에이전트가 "최근 변경 없음"으로 섹션 2를 기록 → 3건의 실제 변경(TRAIL_BE, 공격적 지정가, 회전문 방지)이 before/after 비교에서 누락
- Phase 2 원안(B)은 Claude Code `CronCreate`를 썼지만, 이 도구는 **세션 활성 시에만 동작 + 7일 자동 만료**라 봇 24/7 운영에 부적합 → 서버 APScheduler + 텔레그램 리마인더로 전환
- 완전 자동화(headless CLI)는 검증 비용 대비 이익이 불명확 → 사용자 판단 게이트 유지 (에이전트의 "제안만, 구현은 사용자 승인" 원칙과 일관)

## 구현 단계 (Phase 3)

### A. change_log.md 소급 기록 (3건)
- 2026-04-14 BE 손절 프리-트레일링 도입 (TRAIL_BE_*)
- 2026-04-16 공격적 지정가 주문 경로 (ORDER_TYPE_DEFAULT = "limit_aggressive")
- 2026-04-21 테마 재선정 회전문 방지 (THEME_*_BOOST/COOLDOWN)
→ "에이전트 도입 이전 변경" 주석과 함께 1줄씩 기록. 제안서 경로는 해당 git 커밋 해시로 대체.

### B. 에이전트 정의 보완
- 섹션 "실패/엣지 케이스" 또는 "도구 사용 규칙"에 "**리마인더 트리거로 호출된 경우 표본 임계값 미달 시에도 반드시 제안서를 생성**(판단 유보 모드)"을 1줄 추가 → 자동 리마인더 사이클에서 "표본 부족 → 스킵"으로 생략되지 않도록 보장

### C. 텔레그램 리마인더 스케줄
- 스케줄 정의:
  - **주간 리마인더**: 매주 금 **17:45 KST** (기존 주간 매매 복기 17:30 종료 15분 후 — 중복 알림 방지)
  - **월간 리마인더**: 매월 1일 **09:00 KST** (시장 개장 전, 하지만 시장 개시 첫 잡 08:30 이후)
- 구현:
  - `scheduler.py`: `add_job()` 2건 추가. `CronTrigger(..., timezone=_KST_TZ)` 필수
  - `scheduler.py`: `_run_improvement_reminder_weekly()`, `_run_improvement_reminder_monthly()` 핸들러 2개
  - `modules/reporter/telegram_notifier.py`: `send_improvement_reminder(mode: str)` 공용 함수 1개
  - 공휴일 스킵 적용 대상이 아님(월간 1일이 주말/공휴일이어도 리마인더는 발송 — 사용자가 실행 판단)
- 메시지 예시:
  ```
  📊 월간 개선 제안서 생성 시점입니다
  Claude Code 세션에서 `/improve monthly` 를 실행하세요.
  - 기간: 최근 30일 매매 데이터
  - 예상 산출물: docs/improvements/YYYY-MM-monthly.md
  ```

## 변경 파일 목록
**신규**: 없음

**수정**:
- `docs/improvements/change_log.md` — 3건 소급 기록
- `.claude/agents/trade-improvement-analyst.md` — 리마인더 트리거 동작 규칙 1줄
- `scheduler.py` — `add_job` 2건 + 핸들러 2개 (약 30~40줄)
- `modules/reporter/telegram_notifier.py` — `send_improvement_reminder()` 1개 (약 10~15줄)

**Python 코드 변경**: 2파일 (scheduler.py, telegram_notifier.py)
**영향 범위**: 기존 스케줄 잡/알림 로직과 독립. 기존 잡 수정 없음.

## 롤백 계획
- `scheduler.py`: 추가된 2개 add_job 블록 + 핸들러 2개 제거 (`git revert`)
- `telegram_notifier.py`: 추가된 1개 함수 제거
- `change_log.md`: 추가된 3행 제거
- 에이전트 정의: 추가된 1줄 제거
- 롤백 영향: 서비스 재시작만 필요 (손실 없음)

## 완료 기준
- CHECKLIST.md 구현/검증/배포/문서 업데이트 모두 `[x]`
- code-tester 에이전트 검증 통과 (심각 0건, 주의 있으면 수정)
- `sudo systemctl restart trading_system` 성공 + 잡 2개 등록 확인 (로그에서 "개선 제안 리마인더" 로그 1줄 이상)
- 다음 금요일(4/24) 17:45 실제 수신 확인

## 후속 단계 (Phase 4, 데이터/운영 성숙 후)
- Phase 4-A: 진짜 완전 자동화 (Claude Code headless / API 호출 / 에이전트 자체 구동)
- Phase 4-B: `improvement_proposals` DB 테이블 + 승인/거절/구현 상태 추적
- Phase 4-C: 저표본 월간(N=10~14) 구간 "관찰 수준 제안" 허용 규칙 재논의 (5월 월간 재실행 결과 반영)
