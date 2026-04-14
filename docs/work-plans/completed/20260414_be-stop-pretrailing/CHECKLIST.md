# CHECKLIST — BE 손절 프리-트레일링

## 구현 항목
- [x] config.py: TRAIL_BE_ENABLED 추가
- [x] config.py: TRAIL_BE_ACTIVATE_PCT 추가
- [x] config.py: TRAIL_BE_STOP_PCT 추가
- [x] portfolio_monitor_v2.py `__init__`: enable_be_stop, trail_be_activate_pct, trail_be_stop_pct 읽기
- [x] portfolio_monitor_v2.py: 설정값 방어 (음수/양수 검증 + L1 순서 경고)
- [x] portfolio_monitor_v2.py `_update_trailing_stop()`: BE 블록 삽입
- [x] portfolio_monitor_v2.py `_check_stop_loss()`: Grace 블록에 BE 오버라이드 (핵심 — 오이솔루션 케이스 방어)
- [x] portfolio_monitor_v2.py `_restore_trailing_state()`: BE only 상태 재시작 복원

## 검증 항목
- [x] py_compile config.py
- [x] py_compile modules/trading_engine/portfolio_monitor_v2.py
- [x] code-tester 에이전트 검증 1차 (심각 1건 + 주의 3건 발견 → 모두 수정)
- [x] 단위 시나리오 T1~T6 통과
  - T1: Day1 +5.44%→-3.36% → BE 9900 발동 (오이솔루션 케이스)
  - T2: Day1 max3% current-5% → Grace 유지
  - T3: Day1 current-8.6% → Grace 발동
  - T4: Day3 일반 stop_loss_price 기준
  - T5: TRAIL_BE_STOP_PCT=+0.01 설정 실수 방어 (자동 비활성)
  - T6: 재시작 BE 복원

## 배포 항목
- [ ] 장마감(15:30 KST) 후 `sudo systemctl restart trading_system`
- [ ] 재시작 후 BE 관련 로그 확인 (`🛡️`, `BE 손절 복원`)
- [ ] 다음 거래일(4/15) 장중 +5% 이상 포지션 있으면 BE 활성화 로그 수집
- [ ] 1주 후(4/21) trade_reviews에서 BE 마감 사례 SQL 조회:
  ```sql
  SELECT * FROM trade_reviews
  WHERE profit_rate BETWEEN -2 AND 0
    AND max_profit_during_hold >= 5
    AND sell_date >= DATE('2026-04-14');
  ```

## 문서 업데이트 항목
- [ ] memory `project_stop_loss_review.md` 업데이트 (BE 도입 완료 기록)
- [ ] memory `project_strategy.md` 파라미터 섹션 갱신 (+5% BE 단계 추가)
- [ ] active → completed 아카이브 (`20260414_be-stop-pretrailing`)
