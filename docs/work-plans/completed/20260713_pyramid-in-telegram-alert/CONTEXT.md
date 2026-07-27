# CONTEXT — v17 불타기 텔레그램 알림 누락 수정

## 변경 이유
2026-07-13 ISC 종목이 v17 2차 진입(불타기) 체결됐으나 Telegram 알림 미발송.
근본 원인: 모니터 인스턴스에 notifier 미주입 → step 16 `hasattr` 가드가 무음 skip.

## 현재 코드 상태
- `modules/trading_engine/portfolio_monitor_v2.py:211-262` `__init__(self, use_mock=True)`
  — `self.notifier` 초기화 없음. 콜백(on_stop_loss 등)은 main.py가 세터로 주입하지만
  notifier 배선 경로는 없음.
- `modules/trading_engine/portfolio_monitor_v2.py:1886-1899` step 16 알림 블록:
  ```python
  try:
      if hasattr(self, "notifier") and self.notifier:
          self.notifier.send_message(... "🔥 2/2 불타기 진입" ...)
  except Exception as e:
      logger.debug(f"[v17] 텔레그램 알림 실패: {e}")
  ```
- `main.py:184` `self.notifier = TelegramNotifier()`
- `main.py:1890` `self.monitor = PortfolioMonitorV2(use_mock=self.test_mode)`  ← notifier 미전달
- `main.py:1896-1902` 콜백 세터 주입 (notifier는 여기 없음)

## 핵심 스니펫 (알림 포맷 — 유지 대상)
```
🔥 2/2 불타기 진입
종목: {stock_name} ({stock_code})
매수: {filled_qty}주 @ {filled_price:,.0f}원
first={pos.first_buy_price:,.0f}원, avg={new_avg:,.0f}원 (가중평균)
트리거: +{pos.profit_rate*100:.1f}% (first 기준)
누적 보유: {total_qty_after}주
```

## import 안전성
- `TelegramNotifier`는 `modules.reporter.telegram_notifier`에 정의.
- telegram_notifier는 portfolio_monitor를 import하지 않음(sell_lock/capital_utils만 lazy import)
  → 순환 import 위험 없음. 타입 힌트는 `TYPE_CHECKING` 하에서만 import.

## 과거 버그 / 주의
- 알림 전송은 절대 매매/DB/모니터 흐름을 중단시키면 안 됨(예외 소거 유지).
- send_message는 동기 함수(main.py 곳곳에서 sync 직접 호출 + 일부 to_thread 래핑). step 16은 sync 직접 호출.
- 비밀값(봇 토큰/chat id) 로그 금지.

## 영향 범위
- 알림 배선만 추가. 진입 조건/수량/가격/트레일링/주문 실행 로직 불변.
- 1차 매수/매도/트레일링 알림은 별도 콜백 경로라 무영향.
