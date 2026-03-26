# Market Crisis Guard — 시장 폭락일 매수 방어 로직

## 목표
시장 전체 폭락일(이란전쟁 등)에 매수 진입을 자동 차단/지연/축소하여 손실 방지

## 배경
- 2026-03-04: 이란전쟁 폭락일 09:25 신규 매수 → 당일 -12.4% 손절(-134,500원)
- 기존: `execute_buy_orders()`가 코스피/코스닥 지수 무체크, `get_index_price()` 미사용

## 구현 단계

### Step 1: config.py — 상수 7개 추가
- MARKET_GUARD_ENABLED, CRISIS_DROP(-2%), DANGER_DROP(-1%), CAUTION_DROP(-1%), CAUTION_RATIO(0.7), DELAY_ENABLED, DELAY_MINUTES(35)

### Step 2: modules/market_guard.py — 신규 모듈
- MarketStatus enum (NORMAL/CAUTION/DANGER/CRISIS)
- MarketGuard.check() → (상태, 상세정보) 반환
- KISApi 로컬 인스턴스 생성 패턴

### Step 3: main.py — execute_buy_orders() 수정
- 가드 로직 삽입 (trading_paused 직후)
- cash_ratio 적용 (available_cash 축소)
- asyncio.to_thread() 래핑
- 텔레그램 알림

## 변경 파일
- `config.py` (~10줄)
- `modules/market_guard.py` (신규 ~60줄)
- `main.py` (~40줄)

## 판단 기준
| 상태 | 조건 | 동작 |
|------|------|------|
| CRISIS | KOSPI ≤ -2% AND KOSDAQ ≤ -2% | 즉시 스킵 |
| DANGER | 양쪽 -1% 이하 OR 한쪽 -2% 이하 | 35분 지연 → 재체크 |
| CAUTION | 한쪽만 -1% 이하 | 70% 축소 진입 |
| NORMAL | 둘 다 > -1% | 정상 매수 |

## 롤백 계획
- config.py: `MARKET_GUARD_ENABLED = False`로 즉시 비활성화 가능
- 코드 롤백: git revert

## 완료 기준
- [ ] 3개 파일 수정/생성 완료
- [ ] py_compile 통과
- [ ] code-tester 에이전트 통과
- [ ] 텔레그램 알림 메시지 확인
