# PLAN — 종가베팅 텔레그램 메시지 형식 통일 (스윙 수준)

## 목표
종가베팅 텔레그램 메시지(`closing_bet_system/notification/`)를 스윙 봇(`modules/reporter/telegram_notifier.py`) 수준으로 통일. 5/25(월) 실발주 활성화 전 완료.

## 배경
- 현재 종가베팅 메시지는 집계 정보만(`발주 N건, 체결 N건`) — 종목/가격/손익 누락
- 실발주 활성화 후 종목 식별 및 손익 추적이 텔레그램만으로 불가능
- Phase1Result/Phase2Result/ExitResult dataclass에 `orders: list[CandidateOrder/Exit]` 이미 존재 → **dataclass 확장 없이 _format만 수정 가능**

## 사용자 결정 (2026-05-22)
1. **매수 알림**: 종목당 개별 알림 (스윙 풀텍스트 수준 — 코드/명/수량/가격/점수/L1·L2·L3)
2. **매도 알림**: 종목별 매수가→매도가/수익금/수익률 전부 표시 (DB 조회로 원가 확보)

## 변경 파일
| 파일 | 변경 |
|---|---|
| `closing_bet_system/notification/entry_notifier.py` | 종목당 개별 메시지 + 요약 메시지 분리 발송 |
| `closing_bet_system/notification/exit_notifier.py` | 종목당 손익 계산 + 개별 메시지 + 요약 분리 발송 |
| `closing_bet_system/notification/telegram_review_bot.py` | send_daily_summary에 entered 종목 PnL 추가 + send_system_start/stop 신규 |
| `closing_bet_system/storage/db.py` 또는 candidate_logger.py | 원가 조회 헬퍼 추가 (필요 시) |
| `scripts/test_closing_bet_notifier.py` (신규) | 단위 테스트 |

## 메시지 스타일 통일 규칙
- **이모지**: 🟢 매수 / 🔴 손실 / 🟢 수익 / 📈 종목 매수 / 📉 종목 매도 / 💰 금액 / ⏰ 시각 / 📅 거래일 / 🚨 경고 / ⚠️ 주의 / ✅ 정상 / 🚫 차단 / 🧪 dry_run
- **분리선**: `━` × 22 (스윙 표준)
- **시각 형식**: `HH:MM:SS KST` (메시지 본문) / `YYYY-MM-DD HH:MM:SS KST` (요약)
- **금액 표기**: `{:,}원` 천단위 콤마
- **수익률 표기**: `{:+.2f}%` 부호 명시
- **마크다운**: 파싱모드 Markdown(v1), `_` `*` `[` `\`` escape
- **dry_run 표기**: 제목 prefix `🧪 [DRY-RUN]` (실 발주 시 `✅ 실발주`)

## 메시지별 포맷 표준

### 매수 알림 (entry_notifier.py)

**phase1 — 종목별 개별 메시지 (체결 시)**
```
🟢 *종가베팅 1차 매수 체결* (✅ 실발주)
━━━━━━━━━━━━━━━━━━━━━━

📈 {name} ({ticker})
💰 {qty}주 × {price:,}원 = {amount:,}원
⭐ 점수: {raw}/11 (L1 {l1}/4 · L2 {l2}/4 · L3 {l3}/3) → {decision}
🏷️ 결정: {decision}
📋 주문 ID: {order_id}
⏰ {ts} KST  |  거래일: {trade_date}
```

**phase1 — 요약 메시지 (전체 종목 처리 후)**
```
📊 *종가베팅 1차 진입 종료* ({flag})
━━━━━━━━━━━━━━━━━━━━━━
⏰ {ts}  |  거래일: {trade_date}
후보 {total} → 발주 {submitted}건 / 체결 {filled}건
가격상한 위반 {price_cap} / fund_guard 거부 {fg}
MarketGuard: {status}
{에러 표시 (있을 때)}
```

**phase2** — 동일 패턴 (보류/취소 사유 추가)

**market_guard_skip** — 현재 형식 유지 (이미 ok)

### 매도 알림 (exit_notifier.py)

**종목별 개별 메시지 (체결 시)**
```
🔴 *종가베팅 매도 체결* — {cycle_label} ({flag})
━━━━━━━━━━━━━━━━━━━━━━

📉 {name} ({ticker})
💰 {qty}주 × {sell_price:,}원 = {sell_amount:,}원
📝 사유: {action_label} ({reason if any})

{pnl_emoji} *손익*
매수가: {buy_price:,}원
매도가: {sell_price:,}원
수익금: {profit:+,}원 ({profit_rate:+.2f}%)

⏰ {ts} KST  |  거래일: {trade_date}  |  주문ID: {order_id}
```

수익 ≥ 0: `🟢` 컬러 + `📈` 손익 / 손실 < 0: `🔴` + `📉`

**요약 메시지 (per-cycle 처리 후)**
```
📊 *종가베팅 {LABEL}* 종료 ({flag})
━━━━━━━━━━━━━━━━━━━━━━
⏰ {ts}  |  거래일: {trade_date}
대상 {total} → 체결 {filled}건 / 미체결 {unfilled}건
액션 분포: {EMERGENCY_STOP=1, GAP_UP_HIGH=2, ...}
취소: {cancelled}건
누적 손익: {total_profit:+,}원 ({avg_rate:+.2f}%)
{에러 표시 (있을 때)}
```

### 일일 요약 (telegram_review_bot.send_daily_summary)
```
📊 *종가베팅 일일 요약* ({date})
━━━━━━━━━━━━━━━━━━━━━━

📋 *후보 통계*
• Recommended: {rec}건
• Entered: {ent}건
• Rejected (필터): {rej_f}건

💼 *오늘 진입 종목 PnL* (entered > 0 시만)
• {name1} ({ticker1}): {pnl1:+,}원 ({rate1:+.2f}%)
• {name2} ({ticker2}): {pnl2:+,}원 ({rate2:+.2f}%)

💰 *오늘 총 손익*: {total_pnl:+,}원 ({total_rate:+.2f}%)
누적 recommended: {cum}건 / 게이트 30건
```

### 시스템 시작/중지 (신규)
- send_system_start: 봇 시작 시 1회
- send_system_stop: 봇 종료 시 1회

## 구현 단계

### Phase 1: 인프라 헬퍼 (10~15분)
- DB에서 매수가 조회 헬퍼 (storage/db.py 또는 candidate_logger.py)
- `get_executed_buy_price(candidate_id) -> Optional[float]` (entry_phase1_executed_price 우선, phase2 가중평균 폴백)
- 종목명 long-form 포매팅 헬퍼

### Phase 2: entry_notifier 재작성 (30~45분)
- `send_phase1_result`: 종목별 개별 메시지 N건 + 요약 메시지 1건 발송
- `send_phase2_result`: 동일 패턴
- `_format_phase1_per_ticker`: 종목 단위 포맷
- `_format_phase1_summary`: 요약 포맷
- 동일하게 phase2도

### Phase 3: exit_notifier 재작성 (30~45분)
- `send_*_result`: 종목별 손익 계산 + 개별 메시지 + 요약 분리 발송
- `_format_exit_per_ticker`: DB 조회로 buy_price 가져와 손익 표시
- 누적 손익은 요약 메시지에 표시

### Phase 4: telegram_review_bot 확장 (15~20분)
- `send_daily_summary`: entered 종목 PnL 리스트 추가 (DB 조회)
- `send_system_start` 신규
- `send_system_stop` 신규

### Phase 5: 단위 테스트 (20~30분)
- 시나리오: dry_run / 실발주 / 손익 +/- / 빈 orders / 다중 종목 / 에러
- mock notifier로 send_message 호출 횟수 + 본문 검증

### Phase 6: code-tester 에이전트 (1% 규칙)
- 변경 파일에 대한 심각/주의 이슈 검토

### Phase 7: main checkout 동기화 + commit (worktree)
- main으로 cp + git status 확인

## 롤백 계획
- git revert (worktree 격리라 main 보호됨)
- 또는 git checkout HEAD -- closing_bet_system/notification/

## 완료 기준
- [ ] 3개 notifier 파일 수정 완료
- [ ] 단위 테스트 PASS
- [ ] code-tester 심각 이슈 0건
- [ ] 시각 검증 (mock 발송 결과 텍스트 확인)
- [ ] 일요일 활성화 전 main checkout 반영

## 위험 평가
| 위험 | 영향 | 완화 |
|---|---|---|
| 한 진입에 N개 메시지 발송 | 텔레그램 rate limit | 종목당 1초 sleep 추가 검토 (5건/분 한도 여유) |
| DB 조회 실패 (buy_price 누락) | 매도 알림 손익 미표시 | "(원가 조회 실패)" 폴백 메시지 |
| Markdown escape 누락 | 메시지 파싱 실패 | `_escape_markdown()` 헬퍼 활용 (이미 존재) |
| dataclass 필드 변경 가능성 | 코드 깨짐 | dataclass field name 직접 참조 + 단위 테스트 |
| 메시지 길이 초과 (Telegram 4096자) | 대량 종목 시 짤림 | 종목당 개별 메시지로 분리 (요약은 N건만) |

## 참고
- 스윙 표준: `modules/reporter/telegram_notifier.py:281-410` (send_buy_alert/sell_alert/stop_loss/take_profit)
- 종가베팅 현재: `closing_bet_system/notification/entry_notifier.py:92-145` / `exit_notifier.py:63-89`
- dataclass: `entry_executor.py:91-127` / `exit_executor.py:83-114`
