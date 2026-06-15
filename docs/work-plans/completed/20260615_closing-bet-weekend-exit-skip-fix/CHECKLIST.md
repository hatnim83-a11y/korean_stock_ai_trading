# CHECKLIST — 종가베팅 금요일/휴장 청산누락 버그 fix

## 구현
- [x] `config.py`에 `previous_trading_day(ref_date=None)` 헬퍼 추가 (is_trading_day 재사용, now_kst 기본, datetime 정규화 방어)
- [x] `main_orchestrator.run_emergency_stop_check` trade_date → `previous_trading_day()`
- [x] `main_orchestrator.run_morning_exit` trade_date → `previous_trading_day()`
- [x] `main_orchestrator.run_morning_force_close` trade_date → `previous_trading_day()`
- [x] `main_orchestrator.run_morning_trailing` trade_date → `previous_trading_day(now.date())` (now 박제 기준)
- [x] import 라인에 `previous_trading_day` 추가 (L56, NameError 방지)
- [x] 진입/라벨/요약 잡은 **미변경** 확인 (run_daily_pipeline=오늘, run_label_yesterday=이미 walk-back)

## 검증
- [x] `previous_trading_day` 단위 테스트: 월→금, 화→월, 금→목, 토/일 입력, 대체공휴일, 추석 다중 walk-back, datetime 정규화 (scripts/test_previous_trading_day.py 14/14 PASS)
- [x] 회귀: 연속 거래일에서 직전거래일 == 달력어제 동일성 (화→월 케이스로 검증, 기존 흐름 불변)
- [x] 4개 래퍼가 헬퍼 경유 확인 (code-tester 전수 확인 + datetime(월 09:05)→금 산출 검증)
- [x] py_compile + 기존 종가베팅 테스트 회귀 (exit_executor 47/47 / candidate_logger 24/24 / score 28/28 PASS)
- [x] **code-tester 에이전트** 심각 0 / 주의 0 (배포 가능)

## 배포
- [x] change_log.md 1줄 (청산 trade_date 달력어제→직전거래일)
- [x] 머지 (main 머지 완료)
- [ ] **서비스 restart** (다음 거래일 09:01 청산 전 필수, 다음 월요일 6/22 전까지). 005935 정리 + restart 묶기 권장 ← 사용자 실행 필요
- [ ] restart 후 로그로 trade_date 산출 정상 확인 ← restart 후

## 연계 후속 (별건)
- [ ] **005935 수동 정리** (장 마감 후 KIS API 회복 시): 실보유 확인 → KIS 앱 매도 또는 서비스 내 `execute_force_close("2026-06-12")` 1회 (실매도 주의) ← 사용자 판단 필요
- [ ] **KIS inquire-balance 500 종일 지속** 진단 (자금계산/fund_guard 영향 점검) ← 별도 작업
- [ ] (선택 하드닝) 청산 "최근 N거래일 미청산 sweep"로 과거 누락분 자동회수 검토 — 자동매도라 신중, 별도 결정

## 문서 업데이트
- [x] change_log.md 기록
- [ ] 메모리(project_closing_bet_weekend_exit_skip_bug.md) "수정 완료"로 갱신
- [ ] active → completed 아카이브 (restart 확인 후 권장, 또는 코드 머지 완료로 지금)

## 진행 메모
- 2026-06-15: 핸드오프 3문서 생성.
- 2026-06-15: fix 구현·테스트·머지 완료. 정답패턴(run_label_yesterday walk-back)을 config.previous_trading_day로 공용화. 사전 리뷰(planner+tester) → 구현 → 단위14/14 + 회귀(47/24/28) → 사후 code-tester(심각0) → change_log → 머지. **restart + 005935 정리는 사용자 실행 대기**(다음 월 6/22 전까지 여유).
