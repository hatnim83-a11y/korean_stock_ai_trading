# CONTEXT — 종목당 예산 상한 동적화

## 변경 이유
TOTAL_CAPITAL=4,000,000원 고정값이라 수익 누적(현재 총자산 ~918만원) 미반영. 오늘(4/14) 키움증권 매수 시 상한 800k에 걸려 2주×479k=958k만 집행. 사용자 기대치(총자산/5=1.8M)와 괴리.

## 현재 코드 상태 (수정 후)

### main.py:1130-1147 (Phase 4)
- `get_orderable_cash()` + `get_balance()` 항상 호출
- `total_capital = balance.get("total_value", 0)`
- 폴백: `<=0`이면 `max(TOTAL_CAPITAL, available_cash)`
- Market Guard 축소는 `available_cash`에만 (`total_capital`은 객관 기준)

### main.py:1241
- `max_per_stock = int(total_capital) // settings.MAX_POSITIONS`
- float 전파 방지 (`int()` 명시)

## 영향 범위
- 직접: main.py (1곳, Phase 4 + Line 1241)
- 간접: KIS API 호출 1회/일 추가 (무시 가능)
- 백테스트: test_mode에선 `settings.TOTAL_CAPITAL` 유지 → 기존 시뮬 동작 보존

## 작업 중 발견 사항
- `get_balance()`의 `total_value`가 int로 반환되지만 방어적 `int()` 감싸기로 안전성 확보
- Market Guard 시나리오에서 상한은 총자산 기준 유지, 실제 투입만 축소 현금으로 제한 — 의도 정합
