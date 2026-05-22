# CHECKLIST — 종가베팅 텔레그램 형식 통일

## 구현
- [ ] db.py 또는 candidate_logger.py에 `get_executed_buy_price(candidate_id)` 헬퍼 추가
- [ ] entry_notifier.py 재작성
  - [ ] `_format_phase1_per_ticker(order, dry_run)` 신규
  - [ ] `_format_phase1_summary(result, dry_run)` 신규
  - [ ] `_format_phase2_per_ticker` / `_format_phase2_summary`
  - [ ] `send_phase1_result` 흐름 변경 (orders 순회 발송 + 요약)
  - [ ] `send_phase2_result` 흐름 변경
- [ ] exit_notifier.py 재작성
  - [ ] `_format_exit_per_ticker(order, cycle_label, dry_run, buy_price)` 신규
  - [ ] `_format_exit_summary(result, cycle_label, dry_run, total_pnl)` 신규
  - [ ] `send_emergency_stop_result` 흐름 변경
  - [ ] `send_morning_exit_result` 흐름 변경
  - [ ] `send_force_close_result` 흐름 변경
- [ ] telegram_review_bot.py 확장
  - [ ] `send_daily_summary` entered 종목 PnL 리스트 추가 (DB 조회)
  - [ ] `send_system_start` 신규
  - [ ] `send_system_stop` 신규

## 검증 (단위)
- [ ] `scripts/test_closing_bet_notifier.py` 신규
  - [ ] EN-1: phase1 dry_run + 종목 2건 → 2 종목 메시지 + 1 요약 (총 3건 send_message)
  - [ ] EN-2: phase1 실발주 + 종목 1건 (실 체결) → 1 종목 + 1 요약
  - [ ] EN-3: phase1 빈 orders → 요약만 발송
  - [ ] EN-4: phase1 에러 1건 → 요약에 에러 라인 표시
  - [ ] EN-5: phase2 보류/취소 분류 표시
  - [ ] EX-1: emergency_stop + 1종목 손실 → 🔴 손익 표시
  - [ ] EX-2: morning_exit + 1종목 수익 → 🟢 손익 표시
  - [ ] EX-3: force_close + 미체결 1건 → 요약에 cancelled 표시
  - [ ] EX-4: 원가 조회 실패 → "(원가 조회 실패)" 폴백
  - [ ] EX-5: ExitAction 각 5가지 라벨 매핑
  - [ ] RB-1: send_daily_summary entered=0 → 기존 형식 유지
  - [ ] RB-2: send_daily_summary entered=2 → PnL 리스트 추가
  - [ ] RB-3: send_system_start / stop 발송 검증

## 검증 (정합성)
- [ ] py_compile 통과 (3개 파일)
- [ ] mock notifier로 send_message 호출 횟수 검증
- [ ] mock 본문 텍스트 PLAN 스타일 일치 (이모지/분리선/시각)
- [ ] Markdown escape: `_` `*` `[` `` ` `` 포함 종목명/사유 정상 처리

## 검증 (code-tester 에이전트 — 1% 규칙)
- [ ] 변경 3개 파일 + 헬퍼 1개 + 테스트 1개 대상 호출
- [ ] 심각 0건, 주의 즉시 반영

## 배포
- [ ] worktree → main checkout cp
- [ ] git status diff 확인
- [ ] py_compile (main 위치)
- [ ] systemctl restart trading_system (코드 반영 시점)
- [ ] 5/22 (금) 또는 5/23 (토) 자연 발화 시 시각 검증

## 문서 업데이트
- [ ] memory/project_closing_bet_followups.md 갱신 (텔레그램 형식 통일 1단락)
- [ ] docs/improvements/change_log.md 1줄
- [ ] (선택) CLAUDE.md 새 규칙 (메시지 형식 표준)

## 아카이브
- [ ] 모든 [x] 완료 후 active/ → completed/20260522_closing-bet-telegram-format/
