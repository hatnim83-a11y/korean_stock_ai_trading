# CONTEXT — 종가베팅 텔레그램 형식 통일

## 현재 메시지 차이 매트릭스

| 종류 | 스윙 | 종가베팅 (변경 전) |
|---|---|---|
| 매수 알림 | 종목/수량/가격/테마/점수/시각 + 🟢 | "발주 N건, 체결 N건" 집계만 |
| 매도 알림 | 종목/수량/매수가→매도가/수익금/수익률/사유/🟢🔴 | "대상 N건, 체결 N건" 집계만 |
| 손절/익절 | 별도 메시지 | 매도 알림에 통합 (구분 없음) |
| 일일 요약 | PnL 종합 | status 카운트만 |
| 시스템 시작 | ✅ | ❌ |

## 데이터 구조 (변경 X)

### Phase1Result (entry_executor.py:106-119)
```python
@dataclass
class Phase1Result:
    trade_date: str
    total_candidates: int = 0
    submitted: int = 0
    filled: int = 0
    skipped_price_cap: int = 0
    fund_guard_rejected: int = 0
    market_guard_status: Optional[str] = None
    orders: list[CandidateOrder] = field(default_factory=list)   # ← 종목별 상세
    errors: list[str] = field(default_factory=list)
```

### CandidateOrder (entry_executor.py:91-103)
```python
@dataclass
class CandidateOrder:
    candidate_id: int
    ticker: str
    name: str
    target_price: int
    quantity: int
    submitted: bool = False
    order_id: Optional[str] = None
    fill_status: Optional[FillStatus] = None    # executed_price, executed_shares
    rejection_reason: Optional[str] = None
```

### FillStatus (단위 2-4b)
- executed_price: 체결가
- executed_shares: 체결 수량
- 미체결 시 None

### ExitResult (exit_executor.py:100-113)
- 동일 패턴, orders: list[CandidateExit]

### CandidateExit (exit_executor.py:83-97)
```python
@dataclass
class CandidateExit:
    candidate_id: int
    ticker: str
    name: str
    action: Optional[ExitAction] = None
    target_shares: int = 0
    submitted: bool = False
    order_id: Optional[str] = None
    fill_status: Optional[FillStatus] = None
    rejection_reason: Optional[str] = None
    is_partial: bool = False
    gap_rate: Optional[float] = None
```

### ExitAction enum (exit_executor.py)
- EMERGENCY_STOP (하드 손절 -1% 이하 갭다운)
- GAP_UP_HIGH (+2% 이상 갭업 → 즉시 청산)
- GAP_UP_LOW (+0.5%~+2% 갭업)
- FLAT (평균 보유)
- WEAK_GAP_DOWN (-0.5%~-1%)

## 손익 계산 로직 (exit_notifier 신규)

### 원가(buy_price) 조회 흐름
1. candidates 테이블에서 `entry_phase1_executed_price` 우선
2. phase2도 체결됐으면 가중평균: `(p1×s1 + p2×s2) / (s1+s2)`
3. 둘 다 NULL이면 fallback: `entry_price` 컬럼 (PRD `mark_entered` 시 박제)
4. 그것도 NULL이면 `(원가 조회 실패)` 표기

### SQL
```sql
SELECT entry_phase1_executed_price, entry_phase1_executed_shares,
       entry_phase2_executed_price, entry_phase2_executed_shares,
       entry_price
FROM candidates
WHERE candidate_id = ?
```

### 손익 계산
```python
profit_per_share = sell_price - buy_price
total_profit = profit_per_share * sell_qty
profit_rate = (sell_price - buy_price) / buy_price * 100
```

## Markdown escape 패턴

종가베팅 `telegram_review_bot._escape_markdown()`:
- 활성 특수문자: `_` `*` `[` `` ` ``
- 사용처: ticker, name, kw, reason 등 사용자 콘텐츠
- 숫자(가격/금액/%)는 escape 불필요

## 시각 형식 결정
- 종목별 메시지: `HH:MM:SS` (간결)
- 요약 메시지: `YYYY-MM-DD HH:MM:SS KST` (식별용)
- DB 저장은 KST timezone-aware (now_kst() 사용)

## 기존 단위 테스트
- `scripts/test_closing_bet_telegram_review.py`: TelegramReviewBot.format_alert_message 12 시나리오 PASS
- entry_notifier / exit_notifier 단위 테스트 X (신규 추가)

## 영향 범위
- **호출부 변경 X**: send_phase1_result/send_phase2_result/send_emergency_stop_result 등 시그니처 유지
- **내부 발송 동작 변경**: 한 호출당 1개 메시지 → 한 호출당 N+1개 메시지 (종목별 + 요약)
- **텔레그램 rate limit**: 1초당 30 메시지 한도 (개인 봇). 1진입 2~3종목이라 영향 없음
- **DB 추가 쿼리**: 매도 알림당 candidate_id별 1회 SELECT (성능 영향 미미)

## 과거 관련 작업
- 단위 1-7 telegram_review_bot (5/4 완료)
- 단위 2-4c entry_notifier (5/15 완료, 집계만)
- 단위 2-5c exit_notifier (5/16 완료, 집계만)

## 변경 후 검증
1. py_compile 통과
2. 단위 테스트 시나리오 PASS
3. mock 발송 결과 텍스트가 PLAN 스타일과 일치
4. code-tester 심각 이슈 0건

## 일요일 활성화 전 적용 권장
- 5/22 (금): 코드 수정 + 단위 테스트
- 5/23 (토): main 반영 + systemctl restart (옵션)
- 5/24 (일): 활성화 전 dry-run 메시지 시각 검증 (15:18 자연 발화)
- 5/25 (월): 실발주 시 새 형식 메시지 수신
