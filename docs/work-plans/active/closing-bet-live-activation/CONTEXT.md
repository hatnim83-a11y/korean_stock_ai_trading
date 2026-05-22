# CONTEXT — 종가베팅 실발주 활성화 컨텍스트

## 변경 이유

### dry-run 검증 결과 (5/15~5/22 영업일 6일)
- **표본 4건, EV+ 100%, net realistic +4.02% 평균** — 세전 목표 +1.2% 대비 3.3배
- 비용 모형 (settings.yaml `cost`): 편도 0.115% × 2 + 거래세 0.18% + 슬리피지 0.1% = 왕복 약 **0.41%**
- 4개 시장 상태 모두 검증:
  - 5/18 CAUTION ×0.5 → +0.65% / +6.26% (2건)
  - 5/19 CRISIS → 전체 스킵 (큰 손실 회피 검증)
  - 5/20 DANGER ×0.5 → +5.46% (1건)
  - 5/21 NORMAL → +5.34% (1건)

### 활성화 게이트 (settings.yaml 154-157, 175-178 정의)
| 게이트 | 상태 | 확인 |
|---|---|---|
| 단위 2-4e dry_run 통합 단발 | ✅ | docs/work-plans/active/closing-bet-unit-2-4-entry-executor/CHECKLIST.md |
| 단위 2-5 morning_exit_manager | ✅ | docs/work-plans/active/closing-bet-unit-2-5-morning-exit/CHECKLIST.md |
| 1주 dry_run 자연 검증 | ✅ | journalctl 5/15~5/22 |
| 사용자 명시 승인 | ✅ | 2026-05-22 본 세션 |
| dry_run=false + restart | 🔄 | 5/24 일 작업 예정 |
| 1주 모니터링 | 🔄 | 5/25~5/29 |

## 현재 코드 상태

### settings.yaml (변경 대상)
```yaml
# 라인 158-169: entry_executor
entry_executor:
  enabled: true                            # 5/15 dry_run 활성화 시 이미 true
  dry_run: true                            # ← 5/24 false 전환
  score_threshold: 2                       # 5/14 사용자 결정
  position_ratio: 0.7                      # PRD ×0.7
  phase1_ratio: 0.5                        # 정규장 50% / 동시호가 50%
  phase2_enabled: true
  polling_interval_sec: 5
  fill_check_deadline_sec: 300             # 5분 cut
  fallback_to_next_candidate: true
  market_guard_enabled: true

# 라인 180-186: morning_exit
morning_exit:
  enabled: true                            # 5/16 dry_run 활성화 시 이미 true
  dry_run: true                            # ← 5/24 false 전환
  emergency_stop_enabled: true             # 09:01 분기 활성
  polling_interval_sec: 5
  fill_check_deadline_sec: 60              # 시장가 매도 1분 cut
```

### settings.yaml fund_guard (변경 X, 참고용)
```yaml
fund_guard:
  capital_ratio: 0.10              # 종가베팅 자금 = 전체의 10%
  max_position_per_stock: 0.25     # 1종목 최대 = 종가베팅의 25%
  weekly_loss_limit: -0.05         # 주간 -5% 도달 시 매매 중지
kill_switch:
  daily_loss_threshold: -0.03      # 일일 -3% 추가 진입 금지
```

### entry_executor.py:361-368 dry_run 분기
```python
if self.settings.dry_run:
    logger.info(f"DRY_RUN phase1: {ticker} {quantity}주 @ {target_price:,}원 ...")
    order.submitted = True
    return order   # ← 박제 안 함

# 5. 실 발주 (dry_run=false 시)
result = await asyncio.to_thread(
    self.kis_order_api.buy_limit_order, ticker, quantity, target_price
)
# ↓ ODNO 박제 + fill 폴링 + executed_shares 박제
```

dry_run=false 전환 시 위 코드 라인 370~393이 실행됨. 코드 변경 없이 토글만 전환하면 자동 분기.

## 핵심 스니펫

### 진입 사이즈 계산
```
총자산 × 0.10 (capital_ratio)
       × 0.25 (max_position_per_stock)
       × 0.7  (position_ratio)
       × MarketGuard 비율
       = 1종목 진입 금액
```
- NORMAL: × 1.0 → **1.75%**
- CAUTION/DANGER: × 0.5 → 0.875%
- CRISIS: × 0 → 스킵
- 다시 phase1 50% / phase2 50% 분할 → 1차 진입 0.875%

### entry_executor 8개 잡 (재시작 후 등록 확인)
- pipeline 15:10 / summary 15:35 / label 10:00 / flow_reliability 19:27
- entry_pipeline 15:18 / emergency_stop 09:01 / morning_exit 09:30 / morning_force_close 10:30

### MarketGuard 4단계 (closing_bet_system 내부, 스윙과 다름)
```python
# config.py MARKET_GUARD_*  설정 (스윙과 공유)
CRISIS: KOSPI -2% AND KOSDAQ -2%   → 전체 스킵
DANGER: KOSPI -1% AND KOSDAQ -1%   → ×0.5 축소 (또는 35분 지연)
CAUTION: 한쪽 -1%                  → ×0.5 축소
NORMAL: 그 외                      → ×1.0
```

## 과거 버그 / 주의 사항

### 1. 라벨 누락 패턴 (누적 5건)
| 날짜 | cid | ticker | 종목 | 원인 |
|---|---|---|---|---|
| 5/8 | 47 | 319400 | 현대무벡스 | KIS 500 (1회 차) |
| 5/15 | 157 | 028260 | 삼성물산 | KIS 500 (재시도 후) |
| 5/18 | 174 | 034020 | 두산에너빌리티 | KIS 500 |
| 5/21 | 214 | 005935 | 삼성전자우 | KIS 500 |
| 5/21 | 221 | 034730 | SK | KIS 500 |

- `_fetch_daily_price_with_retry` 헬퍼 도입(5/8) 후에도 발생
- 대형주 / 우선주 / SK 그룹 패턴 → KIS Open API 종목 특이 가능성
- 5/24 일요일 백필 작업 + retry 횟수 증가 검토

### 2. dry_run에서 phase2 0건은 정상
- `entry_executor.py:362-368` dry_run 분기에서 `_save_phase_order_id` / `_save_phase_fill` 미호출
- `_select_phase2_candidates`는 `entry_phase1_executed_shares > 0` 조건 → dry_run에선 항상 0건
- **실 발주(dry_run=false)에선 phase1 체결 박제 → phase2 select 정상 동작**

### 3. 종목 편향
- dry-run 4건 중 **010170 대한광통신 2회** 진입 (5/18, 5/20)
- score_threshold=2 + position_ratio=0.7 + ratio_mult=0.5 조합으로 진입 가능 가격대 좁음
- 실 발주 1주차 종목 다양성 관찰 필요

### 4. fund_guard TOCTOU 보호
- `_fetch_db_state` 단일 connection 4쿼리 (단위 E 검증)
- 같은 KIS 계좌 + 스윙과 자금 풀 분리: capital_ratio=0.10 강제

### 5. KIS 토큰 공유
- 스윙 시스템과 `_shared_token` 공유 (`kis_api.py`/`kis_order_api.py`)
- 토큰 발급 1분 1회 제한 → 종가베팅 첫 호출 시 토큰 미발급 가능성 낮음 (스윙이 이미 발급)

## 영향 범위

### 직접 영향
- **자금**: 총자산의 약 1.75% × 종목수 (1~2종목/일)
- **종목**: score_threshold=2 + universe v2(100건) → 필터 후 19건 / recommended 17건 평균
- **시간**: 매수 15:18~15:25 (KST) / 매도 09:01~10:30 (KST)

### 간접 영향
- **스윙 시스템**: fund_guard로 자금 풀 분리 → 영향 없음
- **텔레그램**: 별도 봇(CLOSING_BET_TELEGRAM_BOT_TOKEN) → 알림 채널 분리
- **DB**: data/closing_bet.db (별도 파일) → 스윙 data/trading.db와 분리
- **모니터링**: portfolio_monitor_v2는 스윙 전용 → 종가베팅 미감시 (의도된 분리)

### 위험 시나리오
1. **KIS API submit_fail 연쇄** → fallback_to_next_candidate=true 동작 검증
2. **phase1 체결 + phase2 미체결** → 50% 보유 상태로 다음날 시장 진입
3. **시장 폭락 다음날 갭다운** → emergency_stop 09:01에 즉시 매도 (PRD 10-1)
4. **API 토큰 만료** → 매도 잡 실패 → 수동 매도 (텔레그램 /sell) SOP 필요

## 변경 영향 추적

### 변경 후 즉시 검증
- [ ] systemctl status trading_system → active
- [ ] journalctl --since "5/24 23:30" → 종가베팅 잡 8개 등록 로그
- [ ] settings.yaml diff 2줄 (entry+exit dry_run 토글)

### 5/25 월 첫 자연 발화 검증
- 텔레그램 phase1 알림 (실 발주 시 candidate_id, ticker, order_id 포함)
- DB candidates 테이블 entry_phase1_order_id NOT NULL (dry-run에선 NULL이었음)
- KIS HTS/계좌 화면에서 실 매수 체결 확인

## 참고 메모리
- `memory/project_closing_bet_system.md` — 종가베팅 전체 인프라
- `memory/project_closing_bet_followups.md` — 단위 2-3/2-4/2-5 진행 상황
- `memory/project_monitor_state_residue_fix.md` — 스윙 상태 잔재 (영향 없음)
