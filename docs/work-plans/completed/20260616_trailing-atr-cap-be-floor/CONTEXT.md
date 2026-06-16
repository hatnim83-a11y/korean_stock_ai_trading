# CONTEXT — 트레일링 ATR cap + BE 바닥 보존

## 변경 이유
2026-06-16 피에스케이홀딩스(031980) 트레일링 L2 청산이 +16.9% 고점 → -9.4%로 26%p를 반납해 사실상 손절로 작동. 트레일링 exit의 수익 보존 목적이 무력화됨. 두 독립 결함(ATR 폭 무제한 / 트레일링 활성 시 BE 무조건 양보)의 합작.

## 현재 코드 상태 (파일:라인)

### 결함 A — `modules/trading_engine/portfolio_monitor_v2.py:144-161`
```python
def effective_trailing_pct(self, fixed_pct: float) -> float:
    if not settings.TRAILING_USE_ATR or self.atr_at_buy <= 0 or self.first_buy_price <= 0:
        return fixed_pct
    atr_pct = (settings.ATR_MULTIPLIER * self.atr_at_buy) / self.first_buy_price
    return max(fixed_pct, atr_pct)        # ← 상한 없음. 피에스케이: max(0.03, 0.225)=0.225
```
- 호출처: line 1877 `trail_pct = pos.effective_trailing_pct(fixed_pct)` → line 1879 `new_trailing_stop = pos.highest_price * (1 - trail_pct)`

### 결함 B — `modules/trading_engine/portfolio_monitor_v2.py:1307-1310`
```python
# 트레일링이 활성화되어 stop_loss_price를 올린 경우, _check_trailing_stop에서 처리하도록 양보
if pos.trailing_active and pos.trailing_stop is not None:
    return False                          # ← 무조건 양보. BE(stop_loss_price) 버려짐
```
- 이어지는 경로: line 1312 grace 블록(hold_days≤grace_period_days=1) / line 1333 `return pos.current_price <= pos.stop_loss_price`
- grace 블록 line 1317-1322: `be_active = enable_be_stop and max_profit_rate>=trail_be_activate_pct(+5%)`; `effective_stop = max(grace_stop_price, pos.stop_loss_price) if be_active else grace_stop_price`

### BE / stop_loss 상향 지점
- `_update_trailing_stop` line 1824-1833: `max_profit_rate>=+5%`면 `be_stop = pos.buy_price*(1+trail_be_stop_pct=-0.01)`; `if stop_loss_price < be_stop: stop_loss_price = be_stop`
- line 1857: L1(+8%) 활성화 시 `pos.stop_loss_price = pos.buy_price`(본전)
- line 1893-1894: `if trailing_stop > stop_loss_price: stop_loss_price = trailing_stop` (상향만, **하향 없음** → trailing_stop이 BE보다 낮으면 stop_loss는 BE에 머묾 = 결함 B 발현 조건)

### 설정 로드 / 검증 패턴 — `__init__` line 281-312
```python
self.enable_be_stop = getattr(settings, 'TRAIL_BE_ENABLED', True)
self.trail_be_activate_pct = getattr(settings, 'TRAIL_BE_ACTIVATE_PCT', 0.05)
self.trail_be_stop_pct = getattr(settings, 'TRAIL_BE_STOP_PCT', -0.01)
# ... 검증: trail_be_stop_pct>=0이면 비활성화 경고 등 (line 287-299)
self.grace_period_days = getattr(settings, 'GRACE_PERIOD_DAYS', 1)
```
- 신규 키도 이 패턴으로 로드: `self.trail_be_floor_enabled = getattr(settings, 'TRAIL_BE_FLOOR_ENABLED', True)`

### config.py
- ATR 섹션: line 448-460 (TRAILING_USE_ATR, ATR_PERIOD)
- 고정 레벨: TRAIL_LEVEL1_PCT=0.04(503), L2=0.03(511), L3=0.02(519)
- ATR_MULTIPLIER=2.0 (524)
- 신규 키 삽입 위치: ATR 섹션(460 근처)

### import / 동시성
- `from config import settings, now_kst, is_trading_day, count_trading_days` (line 31)
- `pos.buy_price`는 DEPRECATED alias = `first_buy_price` (line 71, 114). BE/손절 전부 first 기준 = v17 정책 정합.
- `_check_all_positions` line 1112 손절 체크 → True면 line 1115 `continue`(트레일링 미평가). 손절↔트레일링 상호배타 → 이중 매도 없음. sell_lock 2차 방어.
- cap은 신고가 갱신 시에만(`_on_price_update` line 1090-1092 `if current_price > highest_price:` → `_update_trailing_stop`) 재계산. 매 사이클 재계산 아님.

## 핵심 데이터 (사건 + 현재 보유)
| 종목 | first | atr_at_buy | ATR폭(2×) | level | trailing_stop | 결과 |
|------|-------|-----------|-----------|-------|---------------|------|
| 피에스케이홀딩스 | 132,300 | 14,878 | 22.5% | L2 | 119,905 | -9.4% 청산(고점+16.9%) |
| 대덕전자 | 169,500 | 19,903 | 23.5% | 0 | — | 노출(미활성) |
| 대주전자재료 | 119,800 | 12,578 | 21.0% | 0 | — | 노출(미활성) |
| 롯데쇼핑 | 168,800 | 12,471 | 14.8% | 2 | 173,430 | stop>BE, 손실위험 없음 |
| 신한지주 | 104,900 | 5,578 | 10.6% | 0 | — | 노출(미활성) |

## 관련 과거 버그 / 정책
- BE 손절 2026-04-14(562e1d5) 도입 — "오이솔루션형(+5% 찍고 하락) 방어". 본 사건은 그 BE가 트레일링 양보로 무력화된 첫 실패 사례.
- v17 정책(CLAUDE.md): 손절/BE/트레일링/2차 = `first_buy_price` 기준 / 익절 = `avg_buy_price` 기준.
- monitor_state.json 3중 동기화 — 복원 경로(line 742-781)는 cap/BE 변경과 무관(effective_trailing_pct·_check_stop_loss 미호출).

## 영향 범위
- 변경 함수: `effective_trailing_pct`, `_check_stop_loss`, `__init__`. 호출 체인: `_update_trailing_stop`(간접), `_check_all_positions`.
- 회귀 안전: 정상 18건은 `trailing_stop >= stop_loss_price`라 변경 B 거동 불변(`return False` 유지). cap은 고ATR 종목에만 바인딩(정상 18건 ATR폭 ≤ 8%라 무영향).
- 기존 테스트 `test_monitor_state_residue.py`: 충돌 없음(restore/remove 경로만 검증).

## 작업 중 발견 사항 (리뷰 반영)
- **(code-tester 심각)** cap=0 시맨틱: `min()` 직접 적용 시 ATR이 0으로 억제되는 역동작 → `if cap > 0:` 가드 필수. cap=0=상한없음(롤백).
- **(code-tester 주의)** B fall-through × grace 휩쏘: `trailing_stop < stop_loss_price` 발생 경로는 "고ATR로 trailing_stop이 BE 아래 형성"(재시작 불완전 아님). hold≤1 grace에서 BE(buy×0.99) 손절 — 의도된 동작이나 케이스 12로 명시 검증.
- **(strategy-coder 필수)** 제안서 정정: ① BE 바닥 = first×0.99(코드/정책), avg×0.99 아님 ② cap 소급 = 신규+신고가 종목만. 롯데쇼핑 187,220 상향은 무개입 시 미발생.
- **(strategy-coder 권장)** B 독립 토글 필요(A 롤백으로 B 못 끔). config 검증 가드 + fall-through 로그.

## 배포 후 실데이터 검증 (2026-06-16 14:59 KST 재시작, PID 895479)
- 머지 main d3c34cb + restart 정상(active, 에러/traceback 없음). 트레일링/BE 복원 정상 동작.
- **cap 미소급 실발현 확인**: 대주전자재료(078600) L1 복원, trailing_stop=104,597(high 132,400 대비 **-21%**, cap 미적용 옛값). position_state가 source라 복원값엔 cap 미반영(문서화된 한계대로).
- **결함 B 안전망 작동 확인**: 대주전자재료 trailing_stop(104,597) < stop_loss(메모리 ~109,848 = DB portfolio.stop_loss) → B가 양보 해제 → 약 -8.4%(first 119,800 대비)에서 손절 보장. **cap 없이 -12.7% 방치되는 피에스케이 재현은 차단됨.** 신고가(132,400) 갱신 시 cap 적용되어 trailing_stop 121,808로 자동 정상화.
- 롯데쇼핑 L2: trailing_stop 175,560 > BE → 안전. 신한지주/대덕전자 L0: 미활성, +8% 트레일링 켜질 때 cap 자동 적용.

## 후속 개선 후보 (이번 범위 밖, 별도 작업)
- **복원 경로 BE 재적용 갭**: `_restore_trailing_state`의 **트레일링 활성(L1+) 복원 경로(770-783)는 stop_loss_price를 BE로 올리지 않는다**(BE only 경로 786-800에만 BE 재적용 존재). 그래서 cap 미소급 + L1 복원 종목(대주전자재료)의 실효 손절선이 BE(-1%)가 아니라 DB stop_loss(-8.4%). 결함 B가 이 갭을 부분 보완(악화 아닌 개선)하나, 완전하려면 트레일링 활성 복원 경로에도 `max_profit>=+5%면 BE 재적용` 추가 필요. 이번 변경이 유발한 게 아니라 기존 갭이며, B가 catastrophic 손실은 막으므로 별도 단위로 분리.
