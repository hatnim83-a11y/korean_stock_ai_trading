# PLAN — 트레일링 ATR 폭 상한(cap) + 트레일링 활성 시 BE 손절 바닥 보존

## 목표
트레일링 exit 시스템의 두 설계 결함을 수정한다.
- **결함 A**: `effective_trailing_pct()`가 `max(고정, 2.0×ATR/first)`로 상한이 없어 고ATR 종목에서 트레일링 폭이 22.5%까지 폭발 → 트레일링이 사실상 손절로 작동.
- **결함 B**: `_check_stop_loss()`가 `trailing_active`면 무조건 양보(`return False`)해 BE 손절이 버려짐. ATR 폭이 터져 `trailing_stop < stop_loss_price(BE)`가 되면 BE선을 뚫어도 손절 미발동.

## 배경
- 2026-06-16 13:41 피에스케이홀딩스(031980): L2 상태에서 **+16.9% 고점 → -9.4% 청산**(고점반납 26%p, 정상 18건 최대 8%p의 4.3배).
- 트레일링 폭 = `max(0.03, 2.0×14,878/132,300) = 22.5%` → 트레일링 스탑 119,905원(고점 154,700의 -22.5%).
- BE 손절(buy×0.99=130,977)이 설정됐으나 `trailing_active`로 손절 체크가 무조건 양보돼 무력화.
- 근거 제안서: `docs/improvements/2026-06-16-focus-trailing.md` (표본 19건 전수 분석, 분포 이봉성 확인).
- 현재 보유 4종목 전부 노출(트레일링폭 10.6~23.5%). 대덕전자/대주전자재료는 L0(미활성) → 트레일링 켜지기 전 배포가 핵심 타이밍.

## 구현 단계

### Step 1 — config.py 신규 키 2개 (ATR 트레일링 섹션 ~line 460)
```python
TRAILING_ATR_CAP_PCT: float = Field(
    default=0.08,
    description=(
        "ATR 트레일링 폭 상한. effective_pct = max(고정, min(ATR항, cap)). "
        "0=상한 없음(롤백). 고정 레벨 최대값(L1=0.04)보다 크게 설정해야 하한 무력화 방지."
    )
)
TRAIL_BE_FLOOR_ENABLED: bool = Field(
    default=True,
    description=(
        "트레일링 활성 시에도 BE/stop_loss를 바닥으로 보존. "
        "True=trailing_stop이 stop_loss_price보다 낮으면 양보하지 않고 BE 손절 체크. "
        "False=기존 무조건 양보(롤백)."
    )
)
```

### Step 2 — `effective_trailing_pct()` cap 적용 (portfolio_monitor_v2.py:144-161)
```python
atr_pct = (settings.ATR_MULTIPLIER * self.atr_at_buy) / self.first_buy_price
cap = getattr(settings, 'TRAILING_ATR_CAP_PCT', 0.08)
if cap > 0:                          # cap=0 = 상한 없음(롤백). min() 직접 적용 시 ATR 0 억제 역동작 방지
    atr_pct = min(atr_pct, cap)
return max(fixed_pct, atr_pct)       # 하한은 항상 고정값(cap < fixed여도 고정값 보장)
```

### Step 3 — `_check_stop_loss()` 조건부 양보 (portfolio_monitor_v2.py:1307-1310)
```python
# 트레일링 스탑이 손절가(BE 포함)보다 높을 때만 양보. ATR 폭 폭발로 trailing_stop이 BE보다
# 낮아지면 양보하지 않고 BE 손절 체크 진행 (피에스케이홀딩스 2026-06-16 사건 방어).
be_floor_on = self.trail_be_floor_enabled
if (pos.trailing_active and pos.trailing_stop is not None
        and (not be_floor_on or pos.trailing_stop >= pos.stop_loss_price)):
    return False
```
- 양보 해제 후 fall-through: hold>grace(1)면 line 1333 `current <= stop_loss_price`(BE) 직행 / hold≤grace면 grace 블록 `max(grace_stop, stop_loss_price)`. 두 경로 모두 BE 보존.

### Step 4 — `__init__` 검증부 보강 (~line 287, BE 검증 패턴 옆)
- `self.trail_be_floor_enabled = getattr(settings, 'TRAIL_BE_FLOOR_ENABLED', True)`
- cap 오설정 경고: `0 < TRAILING_ATR_CAP_PCT < max(L1,L2,L3 고정레벨)` 이면 logger.warning (고정값 무력화 위험 고지). cap≤0이면 "상한 없음" info.

### Step 5 — fall-through BE 청산 로그 (line 1333 경로)
- hold>grace에서 trailing 양보 해제로 BE 손절 발동 시 info 로그 1줄(무음 청산 방지, 제안서 관찰항목 "양보 안 함 분기 발동 케이스 수집"과 연동).

### Step 6 — 신규 단위 테스트 `tests/test_trailing_atr_cap_be_floor.py`
code-tester 권장 12+ 케이스 (아래 CHECKLIST 검증 항목 참조).

## 변경 파일 목록
| 파일 | 변경 |
|------|------|
| `config.py` | 신규 키 2개 (TRAILING_ATR_CAP_PCT, TRAIL_BE_FLOOR_ENABLED) |
| `modules/trading_engine/portfolio_monitor_v2.py` | effective_trailing_pct cap / _check_stop_loss 조건부 양보 / __init__ 로드·검증 / BE 로그 |
| `tests/test_trailing_atr_cap_be_floor.py` | 신규 (12+ 케이스) |
| `docs/improvements/2026-06-16-focus-trailing.md` | 정정 메모(BE=first 기준 / cap 소급 한계) |
| `docs/improvements/change_log.md` | 1줄 추가 (배포 시) |

## 접근 방식
- 두 결함은 독립 → 각각 독립 토글(cap=0 / BE_FLOOR=false)로 롤백 가능.
- A는 1차 방어(폭 자체 제한), B는 2차 안전망(폭이 터져도 BE 보존). A+B 동시 적용 시 활성화 시점 trailing_stop이 BE 근처/위라 B fall-through 빈도 자체가 감소.
- 기존 코드 스타일(`getattr` 폴백, max/min 합성) 유지.

## 롤백 계획
- A: `.env` `TRAILING_ATR_CAP_PCT=0` + `sudo systemctl restart trading_system`
- B: `.env` `TRAIL_BE_FLOOR_ENABLED=false` + restart
- 둘 다: 위 두 줄 동시 + restart → 기존 동작 완전 복귀

## 완료 기준
1. 신규 테스트 12+ 전부 PASS
2. 기존 `tests/test_monitor_state_residue.py` 회귀 PASS
3. code-tester 심각/주의 0
4. `python -m py_compile` 통과
5. `change_log.md` 1줄 추가
6. active/ → completed/YYYYMMDD_trailing-atr-cap-be-floor/ 아카이브

## 한계 / 주의
- cap은 **신규 매수 + 재시작 후 신고가 갱신** 종목에만 소급(`_update_trailing_stop`은 신고가 시에만 호출). 이미 활성·고점 박힌 롯데쇼핑은 재시작만으로 trailing_stop 상향 안 됨(단 현재 stop>BE라 손실 위험 없음).
- BE 바닥 기준은 `pos.stop_loss_price`(=first_buy_price 기반, v17 정책 정합). 제안서의 avg×0.99 표기는 정정 대상.
