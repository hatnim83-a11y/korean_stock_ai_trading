# PLAN: 분할 진입 + 불타기 + 익절 변경 + ATR 트레일링

> 사용자 정의: "분할 진입 ≡ 불타기" — 1차 진입 후 일정 수익률 달성 시 2차 진입하는 단일 통합 개념
> 원본 플랜: `/home/hatni/.claude/plans/wild-frolicking-pony.md` (526줄)

## 목표

1. **사용자 의도 ① 수익 끌기**: +5% 도달 종목에 2차 50% 추가 진입 + 익절 임계 상향(+12/+20/+30%, avg 기준) + ATR 기반 트레일링으로 추세 종목 수익 극대화
2. **사용자 의도 ② 리스크 축소**: 1차 50% 진입 → 갭다운 손절 시 손실 절대액 -50% 축소

## 배경 (Why)

- W21 매도 4건 전부 손실 (-28.93%p), 평시 손절 80%가 -7%+ 갭다운
- 평시 손절 10건 중 max_profit ≥ +5% 도달은 2건(20%) — 수익 종목 충분히 끌고 가지 못함
- 현재 분할 익절만 있음 (분할 진입 없음) → 단일 진입의 갭다운 노출 큼
- 두 리뷰 에이전트 강력 권고: 단계적 도입이지만, 사용자는 일괄 도입 결정 → 리뷰 보완사항을 모두 반영하여 위험 완화

## 사용자 확정 사항

| 항목 | 결정값 | 기준 |
|------|--------|------|
| 분할 비율 | 50:50 | — |
| 진입 방식 | 추세 분할 (1차 → +5% 시 2차) | first_buy_price |
| 2차 트리거 | +5% 수익률 도달 | first_buy_price |
| 손절가 (DEFAULT/GRACE/BE) | 현재 로직 유지 | first_buy_price |
| 분할 익절 임계값 | +12% / +20% / +30% | **avg_buy_price ⚠️** |
| 분할 익절 매도 비율 | 25% / 20% / 20% (잔여 35%) | original_shares |
| 트레일링 폭 | `max(고정값, 2.0 × ATR(14))` | first_buy_price |
| 미진입 자금 | 보유 종료일까지 대기 | — |

## 핵심 설계 요약

### 정책 분기 (중요)
- **익절 트리거** → `avg_buy_price` 기준 (수익 실현 직관)
- **손절/BE/트레일링/2차 트리거** → `first_buy_price` 기준 (평균단가 함정 회피)

### 매수 흐름
```
09:25 1차 매수 (50%)
  ↓
모니터 감지 (09:00~15:20)
  ├─ +5% 도달 → 2차 매수 50% (BuyLock + 우선순위 가드)
  └─ 보유기간 종료 → 1차만 매도
```

### 트레일링
- L1/L2/L3 각각 `effective_pct = max(고정값, 2.0×ATR(14)/first_buy_price)`
- ATR 박제: 매수 시점 1회, pykrx 우선 KIS 폴백, prefetch 09:20

### 동시성
- BuyLock 신규 + 우선순위 가드 (Sell 우선)
- SellLock 우선 발화 시 그 사이클 2차 매수 skip → 다음 사이클 재평가

## 구현 단계 (12 Steps)

상세 내용은 원본 PLAN 참조: `/home/hatni/.claude/plans/wild-frolicking-pony.md`

1. **DB v17 마이그레이션** — 7개 컬럼 추가 + 기존 holding 백필 UPDATE 4문장
2. **Position 클래스 확장** — 필드 6개 + `profit_rate_avg` / `effective_trailing_pct(level)`
3. **config.py 신규 상수 ~12개** — TRANCHE_*, ATR_*, 익절 임계/비율 갱신, PARTIAL_PROFIT_BASE 토글
4. **ATR 모듈 신규** (`modules/atr_calculator.py`) — pykrx + KIS 폴백, prefetch 09:20
5. **BuyLock 신규 모듈** (`modules/trading_engine/buy_lock.py`) — try/finally + clear_all
6. **매수 50% 분할** (`main.py:execute_buy_orders`) — 1차 50% + ATR 박제
7. **2차 진입 모니터** (`_check_and_execute_pyramid_in`) — 우선순위 가드, MarketGuard 재호출
8. **분할 익절 avg 기준** — 트리거 변경, 임계 +12/+20/+30%, 비율 25/20/20%
9. **ATR 트레일링 + sanity 분기** — `_update_trailing_stop` ATR 통합, monitor_state.json 키 추가
10. **대시보드/텔레그램 표시** — avg/first 양쪽 표시, tranche 배지
11. **신규 테스트** — `test_tranche_entry.py` + `test_atr_trailing.py` + monitor_state_residue 보강
12. **code-tester + 검증 + 문서 업데이트** — change_log.md 1줄 추가, MEMORY 업데이트

## 변경 파일 목록

원본 PLAN 526줄의 "변경 파일 목록" 섹션 참조. 핵심 16개 파일:
- `database.py`, `modules/portfolio_monitor_v2.py`, `main.py`, `config.py`
- `modules/atr_calculator.py` (신규), `modules/trading_engine/buy_lock.py` (신규)
- `web/dashboard_service.py`, `web/templates/dashboard.html`
- `tests/test_tranche_entry.py` (신규), `tests/test_atr_trailing.py` (신규)
- `scripts/backtest_full_system.py`, `modules/trading_engine/portfolio_monitor.py` (v1)
- `scheduler.py`, `modules/market_guard.py`
- `docs/improvements/change_log.md`

## 롤백 계획

원본 PLAN "롤백 계획" 섹션 참조. 4종 토글:
1. `TRANCHE_ENTRY_ENABLED=False` (마스터)
2. `PARTIAL_PROFIT_BASE='first'` (익절 기준만)
3. `TRAILING_USE_ATR=False` (ATR만 비활성)
4. **익절 임계값 강제 원복** ⚠️ (TAKE_PROFIT_1/2/3 + PARTIAL_SELL_RATIO_1) — 패키지 변경이라 별도 점검 필수

## 완료 기준

`CHECKLIST.md` 참조.

## 다음 단계 (Phase 2, 별도 /plan)

- 3분할(40+30+30), 2차 트리거 추세 확정 조건, ATR 일일 갱신, 백테스트 시뮬레이션
