# CHECKLIST — v17 불타기 텔레그램 알림 누락 수정

## 구현
- [x] `PortfolioMonitorV2.__init__`에 `notifier=None` 파라미터 + `self.notifier` 저장
- [x] `TelegramNotifier` 타입 힌트/의존성 연결 추가 (기존 import 경로 재사용, 순환 import 없음)
- [x] `main.py start_monitoring()`에서 `notifier=self.notifier` 전달 (main.py:1892)
- [x] step 16 알림 블록 `hasattr` 단순화 + 실패 로그 debug→warning (비밀값 미기록)

## 검증
- [x] RED: 신규 테스트가 미수정 코드에서 실패(생성자 notifier kwarg 미지원, 4 failed)
- [x] GREEN: notifier 주입 시 2차 진입 성공 → send_message 정확히 1회
- [x] notifier=None → DB/메모리 상태(tranche_count=2) 무손상, 예외 없음
- [x] notifier.send_message 예외 → 상태 무손상, 예외 전파 안 됨 (warning 로그 확인)
- [x] 기존 회귀 테스트 그린 유지 (tranche/monitor_state/atr_cap 46 passed)
- [x] `python3 -m py_compile` 통과
- [x] `git diff --check` 클린

### 커밋 전 재검증 (2026-07-27)
- [x] `tests/test_pyramid_in_telegram_alert.py` → **4 passed** (외부 주문/DB write 없음: FakeDB·FakeNotifier·execute_portfolio 스텁)
- [x] 회귀 `test_tranche_entry.py` + `test_monitor_state_residue.py` + `test_trailing_atr_cap_be_floor.py` → **46 passed**
- [x] `py_compile main.py portfolio_monitor_v2.py test_pyramid_in_telegram_alert.py` → OK
- [x] 관련 diff에 무관한 hunk 혼입 없음 확인 (main.py 1 hunk / portfolio_monitor_v2.py 3 hunk 전부 notifier 배선)

## 배포 및 운영 검증
- [x] 사용자 승인 후 `systemctl restart trading_system` 실행 (2026-07-13 00:54:33 UTC)
- [x] 새 MainPID `3002350`, service `active` 확인
- [x] 장중 재시작 자동복구: DB holding 5개 → monitor 5개 로드/모니터링 재개 확인
- [x] ISC 상태 보존 확인: 7주, tranche=2, avg=160,071.43원, 2차 완료
- [x] 재시작 이후 service error/alert 없음
- [x] 새 프로세스가 수정된 notifier 주입 경로를 로드하도록 source wiring + 새 PID 기동 교차검증
- [ ] 다음 실제 2차 진입 시 Telegram 수신 여부 운영 관찰 (실주문/강제 트리거 금지)
      → **미완료 · 운영 관찰 대기 항목**. 실전 2차 진입이 발생해야 확인 가능하므로
        코드/테스트 완료와 무관하게 열어 둔다 (인위적 트리거 금지).

## 문서 업데이트
- [x] active → completed 아카이브
