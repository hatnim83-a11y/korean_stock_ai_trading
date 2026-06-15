# CHECKLIST — 종가베팅 금요일/휴장 청산누락 버그 fix

## 구현
- [ ] `config.py`에 `previous_trading_day(ref_date=None)` 헬퍼 추가 (is_trading_day 재사용, now_kst 기본)
- [ ] `main_orchestrator.run_emergency_stop_check` trade_date → `previous_trading_day()`
- [ ] `main_orchestrator.run_morning_exit` trade_date → `previous_trading_day()`
- [ ] `main_orchestrator.run_morning_force_close` trade_date → `previous_trading_day()`
- [ ] `main_orchestrator.run_morning_trailing` trade_date → `previous_trading_day()` (now 변수 주의)
- [ ] 진입/라벨/요약 잡은 **미변경** 확인 (run_daily_pipeline=오늘, run_label_yesterday=이미 walk-back)

## 검증
- [ ] `previous_trading_day` 단위 테스트: 월→금, 화→월, 수(휴장 가정)→화, 연속휴장 walk-back, 토/일 입력
- [ ] 회귀: 연속 거래일에서 직전거래일 == 달력어제 동일성 (기존 흐름 불변)
- [ ] 4개 래퍼가 헬퍼 경유 + 정상 trade_date 산출 (mock now_kst로 월요일 시뮬 → trade_date=금요일 확인)
- [ ] py_compile + 기존 종가베팅 테스트 회귀 (test_exit_executor 47 / candidate_logger 24 / score 28)
- [ ] **code-tester 에이전트** 심각/주의 0

## 배포
- [ ] change_log.md 1줄 (청산 trade_date 달력어제→직전거래일)
- [ ] 머지 → **서비스 restart** (다음 거래일 09:01 청산 전 필수). 005935 정리 + restart 묶기 권장
- [ ] restart 후 로그로 trade_date 산출 정상 확인

## 연계 후속 (별건)
- [ ] **005935 수동 정리** (장 마감 후 KIS API 회복 시): 실보유 확인 → KIS 앱 매도 또는 서비스 내 `execute_force_close("2026-06-12")` 1회 (실매도 주의)
- [ ] **KIS inquire-balance 500 종일 지속** 진단 (자금계산/fund_guard 영향 점검)
- [ ] (선택 하드닝) 청산 "최근 N거래일 미청산 sweep"로 과거 누락분 자동회수 검토 — 자동매도라 신중, 별도 결정

## 문서 업데이트
- [ ] `memory/project_closing_bet_exit_profit_max.md` 또는 신규 메모리에 버그fix 기록
- [ ] active → completed 아카이브

## 진행 메모
- 2026-06-15: 핸드오프 3문서 생성. 다음 세션 `/resume`로 착수. 정답 패턴(L613/704 walk-back) 복사가 핵심.
