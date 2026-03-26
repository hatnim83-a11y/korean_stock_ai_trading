# Context — Market Crisis Guard

## 변경 이유
이란전쟁 폭락일(2026-03-04) 시장 전체 급락에도 매수 진입 → -350,000원 손실. 시장 레벨 가드 부재.

## 현재 코드 상태

### main.py:936 — execute_buy_orders()
- trading_paused 체크 후 바로 관찰 대기 → 지수 체크 없음
- `self.kis_api` 인스턴스 없음, KISApi() 로컬 생성 패턴 사용
- 투자금 계산: main.py:1041 (`per_slot_capital = available_cash // available_slots`)
- `asyncio.to_thread()` 패턴: main.py:1047, 1069

### kis_api.py:294 — get_index_price()
- "0001"=KOSPI, "1001"=KOSDAQ
- 반환: `{'price': float, 'change': float, 'change_rate': float}` 또는 None
- `_rate_limit()`: 0.11초 sleep (동기)
- `_shared_token` 클래스 레벨 공유

### 텔레그램: self.notifier.send_message() — 동기 함수

## 2026-03-04 폭락일 데이터
- 09:25:07 티씨케이 매수 (217,000원×5주)
- 09:26:02 HD한국조선해양 손절 (-10.7%)
- 09:35:08 HPSP 손절 (-8.1%)
- 12:32:10 이오테크닉스 손절 (-10.7%)
- 15:18:49 티씨케이 손절 (-12.4%)
- 총 손실: -350,000원

## 리뷰에서 발견된 주의사항
- CRISIS 기준 OR→AND 변경 (KOSDAQ 변동성 높아 OR 과잉방어)
- asyncio.sleep(35분)은 이벤트 루프 논블로킹이므로 다른 job 영향 없음
- API 부분 실패 시 성공한 쪽만으로 판단 (실패쪽=0%)
- 지연 매수 후 모니터링은 DB 폴링으로 자동 감지 (30초 내)

## 영향 범위
- 직접: main.py execute_buy_orders(), 매수 실행 흐름
- 간접: 모니터링 (10:00 지연매수 시 모니터링 자동 포함 확인 필요)
