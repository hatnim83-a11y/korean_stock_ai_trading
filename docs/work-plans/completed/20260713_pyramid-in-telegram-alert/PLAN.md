# PLAN — v17 불타기(2차 진입) 텔레그램 알림 누락 수정

## 목표
2차 진입(불타기)이 실제 체결·DB 기록에 성공하면 기존 포맷의 Telegram 알림이
**정확히 한 번** 발송되도록 notifier 의존성을 안전하게 연결한다.

## 배경 / 원인 (재검증 완료)
- `PortfolioMonitorV2.__init__`는 `use_mock`만 받으며 `self.notifier`를 초기화하지 않음.
- `main.py`는 `self.notifier = TelegramNotifier()`를 만들지만, `start_monitoring()`에서
  `PortfolioMonitorV2(use_mock=self.test_mode)`로 생성할 뿐 notifier를 전달하지 않음.
- `_check_and_execute_pyramid_in()` step 16은
  `if hasattr(self, "notifier") and self.notifier: self.notifier.send_message(...)`로 알림 발화.
- → `hasattr(self, "notifier")`가 항상 False → 무음 skip. 오늘 ISC 2차 진입 체결됐으나 알림 미발송.

## 구현 단계
1. `PortfolioMonitorV2.__init__`에 optional `notifier=None` 파라미터 추가 + `self.notifier` 저장.
   - `TYPE_CHECKING` 하 `TelegramNotifier` 타입 힌트만 import (런타임 순환 import 회피).
2. `main.py start_monitoring()`에서 `PortfolioMonitorV2(use_mock=self.test_mode, notifier=self.notifier)` 전달.
3. step 16 알림 블록: `hasattr` 가드 단순화(`if self.notifier is not None:`) + 전송 실패 시
   `logger.debug` → `logger.warning`(운영자 가시성). 비밀값(토큰/chat id) 미기록.
4. 회귀 테스트 추가 (`tests/test_pyramid_in_telegram_alert.py`).

## 변경 파일
- `modules/trading_engine/portfolio_monitor_v2.py`
- `main.py`
- `tests/test_pyramid_in_telegram_alert.py` (신규)
- 본 작업 폴더 PLAN/CONTEXT/CHECKLIST

## 롤백 계획
- 세 코드 변경은 순수 가법적(알림 배선). 이상 시 `git diff`로 3 hunk revert.
- 기능 토글 불필요(알림 전송 실패는 전부 예외 소거 → 매매/DB/모니터 무영향).

## 완료 기준
- notifier 주입 시 2차 진입 성공 → 정확히 1회 send_message 호출.
- notifier=None 또는 send_message 예외 → DB/메모리 상태(tranche_count=2 등) 무손상.
- 기존 테스트 그린 유지. `git diff --check` 클린.

## 명시적 비수행 (승인 범위 밖)
- systemctl restart / 서비스 재시작 / 실주문 / Telegram 실전 전송 / .env·토큰 변경.
