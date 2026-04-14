# 종목당 예산 상한 동적화 (총자산 기반)

## 목표
`max_per_stock = settings.TOTAL_CAPITAL // MAX_POSITIONS` (정적 800,000원)
→ `max_per_stock = total_capital // MAX_POSITIONS` (실시간 총평가액 기반)

## 배경
- 현재: TOTAL_CAPITAL=4,000,000원 고정 → 종목당 상한 800,000원
- 현재 총자산: ~9,183,874원 (수익 +130%)
- 문제: 수익이 재투자 예산에 반영 안 됨. 빈 슬롯 3개인데 상한 800k에 걸려 과도하게 보수적
- 오늘 4/14 키움증권 2주×479k=958k만 집행 (기대치 1,836k의 절반)

## 구현
main.py `execute_buy_orders()` Phase 4 현금 조회 블록에서 `total_capital`(KIS balance `total_value`) 추가 획득 → Line 1241 `max_per_stock` 계산에 사용.

### 변경 파일
- `main.py` (1곳: Phase 4 + Line 1241)

## 엣지 케이스
- test_mode: `settings.TOTAL_CAPITAL` 그대로 (초기값 테스트용 고정)
- KIS API total_value=0 반환 시 폴백: `max(TOTAL_CAPITAL, available_cash)`
- Market Guard 축소: `available_cash`에만 적용, `total_capital`은 객관적 총자산 유지

## 검증
1. py_compile main.py
2. 로그 시뮬: 총자산 9,183,874원 → 상한 1,836,774원 (기존 800,000원)
3. 장마감 후 재시작 → 내일 매수 로그 `슬롯 배분:` 줄 확인

## 롤백
1줄 변경이라 쉽게 원복 가능 (`total_capital` → `settings.TOTAL_CAPITAL`)

## 완료 기준
- py_compile 통과
- 다음 거래일(4/15) 화요일 매수 시 상한이 총자산/5 반영
