---
name: project-tranche-entry
description: v17 분할 진입(50:50) + 불타기(+5% trigger) + 익절 avg 기준(+12/+20/+30%) + ATR 트레일링 max(고정,2.0×ATR(14)) 도입 (2026-05-20 코드, 2026-05 활성화)
metadata:
  type: project
---

# v17 분할 진입 + 불타기 + ATR 트레일링 (2026-05-20 도입)

## 도입 배경

- W21 매도 4건 전부 손실 (-28.93%p), 평시 손절 80%가 -7%+ 갭다운
- 평시 손절 10건 중 max_profit ≥ +5% 도달 = 2건(20%) — 수익 종목 끌기 부족 + 갭다운 손실 큼
- 사용자 의도 ①(수익 끌기) + ②(리스크 축소) — sequential thinking 8단계 + strategy-planner + strategy-coder 리뷰 13건 보완 반영

**Why**: 분할 진입(50:50)으로 갭다운 손절 시 손실 절대액 -50% 즉시 축소 + 불타기 +5% 트리거로 trend 종목 추가 추격
**How to apply**: 신규 매수 시 종목당 자금 50%만 1차 진입 → 모니터에서 +5% 도달 시 50% 추가 진입 → 분할 익절(avg 기준 +12/+20/+30%) → ATR 트레일링

## 정책 분기 (핵심 — 절대 헷갈리지 말 것)

| 항목 | 기준 |
|------|------|
| **익절 트리거** (`_check_and_execute_partial_profit`) | **`avg_buy_price`** (수익 실현 직관) |
| **손절/BE/트레일링/2차 트리거** | **`first_buy_price`** (평균단가 함정 회피) |

- 익절 = "수익 실현" → 평균단가 대비 실제 수익률
- 손절·트레일링 = "리스크 관리" → 1차 진입가 보호 (2차 진입 후에도 손절가 불변)
- 코드 컨벤션: `pos.buy_price` 직접 참조는 deprecated. `pos.first_buy_price` 또는 `pos.avg_buy_price` 명시

## 운용 파라미터

| 파라미터 | 값 | 비고 |
|---------|-----|------|
| `TRANCHE_FIRST_RATIO` | 0.5 | 1차 진입 비율 50% |
| `TRANCHE_SECOND_RATIO` | 0.5 | 2차 진입(불타기) 비율 50% |
| `PYRAMID_TRIGGER_PCT` | 0.05 | 2차 진입 트리거 (+5% **first 기준**) |
| `PYRAMID_MAX_WAIT_DAYS` | 10 | 자연 만료 (보유기간과 동일) |
| `TRANCHE_SECOND_AMOUNT_MODE` | `'mirror_first'` | 2차 금액 = 1차 매수금 (균등) |
| `TAKE_PROFIT_1/2/3` | 0.12 / 0.20 / 0.30 | **avg 기준** (기존 +10/+15/+20에서 상향) |
| `PARTIAL_SELL_RATIO_1/2/3` | 0.25 / 0.20 / 0.20 | 잔여 35% (기존 30/20/20에서 1차만 축소) |
| `PARTIAL_PROFIT_BASE` | `'avg'` | 익절 기준 토글 (롤백 시 `'first'`) |
| `TRAILING_USE_ATR` | True | `max(L1=4%/L2=3%/L3=2%, 2.0×ATR(14)/first)` |
| `ATR_PERIOD` | 14 | ATR 기간 (전통 표준) |
| `ATR_MULTIPLIER` | 2.0 | Chandelier Exit 표준 |

## 토글 (default = 안전 default)

| 토글 | default | 실전 활성 |
|------|---------|----------|
| `TRANCHE_ENTRY_ENABLED` | **False** (안전) | `.env`에 `=true` 명시 |
| `DRY_RUN_PYRAMID` | **True** (안전) | `.env`에 `=false` 명시 |

머지 직후 default는 1차 100% 진입 그대로 유지. `.env` 2줄로 활성화 (config 기본값 변경 없음).

## 동시성

- **BuyLock** (`modules/trading_engine/buy_lock.py`): 2차 진입 race 차단, try/finally + 15:30 clear_all
- **OrderLock 우선순위**: monitor 사이클에서 `Sell > Buy` — 분할익절 발화 사이클에서 2차 진입 skip → 다음 사이클 재평가
- `_check_and_execute_pyramid_in` 호출 위치: `_check_and_execute_partial_profit` 직후 (우선순위 가드 자동)

## 잔재 회귀 차단 (Coder P2 반영)

- monitor_state.json sanity 임계 `tranche_count` 분기:
  - count==1: `first × 1.02` (기존)
  - count==2: `max(first×1.02, avg×1.02)` (2차 진입 후 정상 데이터 보호)
- `avg_buy_price <= 0` 복원 시 `avg = first` 강제 (catastrophic bug 방어)
- 매수 직후 자동 dump_monitor_state → 3중 동기화 즉시 보장

## 핵심 함수 / 파일

| 위치 | 역할 |
|------|------|
| `modules/trading_engine/portfolio_monitor_v2.py:_check_and_execute_pyramid_in` | 2차 진입 모니터 (200줄, MarketGuard/SellLock/BuyLock/DRY_RUN/avg 가중평균) |
| `modules/trading_engine/portfolio_monitor_v2.py:Position` | first/avg/atr/tranche/pending/executed 필드 + profit_rate_avg/effective_trailing_pct 프로퍼티 |
| `modules/atr_calculator.py` | pykrx 우선 KIS 폴백, asyncio.gather 0.5초 타임아웃, 일 단위 캐시 |
| `modules/trading_engine/buy_lock.py` | BuyLock 싱글톤 |
| `database.py:_migrate_v17` | portfolio +7 컬럼 + 백필 4문장 |
| `database.py:update_portfolio_second_tranche` | 2차 진입 DB UPDATE 헬퍼 (avg 가중평균, shares 누적) |

## 데이터 기반 기대 효과

- ② 리스크 축소: 평시 갭다운 손절 시 손실 절대액 **-50% 즉시 축소** (수학적 입증)
- ① 수익 끌기: +5% 도달 종목(평시 20% 빈도)에서 2차 진입으로 trend 추격 + 익절 +12/+20/+30% avg + ATR 트레일링

## 후속 검증 항목

- [ ] Dry-run 1주(또는 실전 즉시): 2차 트리거 발화 빈도, 거짓 신호 비율, ATR 채움 비율 ≥ 90%
- [ ] 4주 실전 후 `/improve weekly`: 1차 손절 손실 절대액 감소 확인, 익절 +12% avg 발화 빈도
- [ ] 회귀: monitor_state.json 잔재 0건, BuyLock 누수 0건

## 알려진 한계 (후속 작업)

- `_close_position_in_db` profit_amount는 first 기준 (avg 기준 대비 2차 진입 후 ~5만원 bias) — 거래 안전 무관, 통계 정확성 P2 (code-tester 주의 2)
- `PYRAMID_MAX_WAIT_DAYS` 자연 만료 구조라 코드 미참조 (config 키만 존재)
- 보유 중 ATR 미갱신 (매수 시점 박제만, Phase 2에서 일일 재계산 검토)

## 롤백 (이상 발생 시)

`.env` 두 줄 변경 + restart:
```
TRANCHE_ENTRY_ENABLED=false
DRY_RUN_PYRAMID=true
```

익절 임계도 같이 원복하려면 `config.py`에서 `TAKE_PROFIT_1/2/3 → 0.10/0.15/0.20`, `PARTIAL_SELL_RATIO_1 → 0.30` 수정 후 restart.

## 관련 메모리

- [[project-stop-loss-review]] — 평시 손절 Phase B 정식 평가 완료(2026-05-20), -7% 손절선 유지 권고
- [[project-monitor-state-residue-fix]] — Phase 1+2 잔재 차단 (sanity ×1.02 → v17에서 tranche 분기로 확장)
- [[project-partial-profit-early-monitoring]] — 09:00 조기 모니터링 + SellLock (v17 OrderLock 우선순위 가드와 결합)
