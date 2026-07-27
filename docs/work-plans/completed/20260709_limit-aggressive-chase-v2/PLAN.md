# PLAN — limit_aggressive 매수 지정가 추격 B안 개선

## 목표
공격적 지정가(limit_aggressive) 매수의 추격 알고리즘을 B안으로 개선하여,
기본 cap(+0.5%)에 살짝 걸리는 정상 매수는 동적 cap(+1.2%)으로 체결시키되,
진짜 과열(ask1 +1.8%↑)은 반복 주문 없이 빠르게 포기한다.

## 배경 (2026-07-09 실패 사례)
- KT(030200): 13주, 재시도 7회, 주문가 54,500원, ask1 54,800~55,100원, 체결 0.
  cap에 막혀 주문가가 ask1보다 300~600원 낮았음.
- SK텔레콤(017670): 8주, 재시도 7회, 주문가 86,600원, ask1 86,800~87,000원, 체결 0.
  cap에 막혀 주문가가 ask1보다 200~400원 낮았음.
- 로그: `logs/system_2026-07-09.log` 503~644행.
- 근본원인: `_compute_aggressive_limit_price`가 `LIMIT_AGGRESSIVE_MAX_CHASE_PCT=0.005`
  cap을 넘으면 cap_price로 고정 → ask1보다 낮은 주문가를 30초간 반복 제출/취소 → 0체결.

## 구현 단계
1. **config.py**: LIMIT_AGGRESSIVE 블록에 3개 신규 설정 추가
   - `LIMIT_AGGRESSIVE_DYNAMIC_CHASE_PCT = 0.012` — 동적 추격 허용 상한(+1.2%)
   - `LIMIT_AGGRESSIVE_DYNAMIC_TRIGGER_PCT = 0.012` — base cap 초과 허용 폭(+1.2%)
   - `LIMIT_AGGRESSIVE_OVERHEAT_PCT = 0.018` — 과열 즉시 포기 임계(+1.8%)
2. **trading_engine.py `_compute_aggressive_limit_price`**: cap 산출 로직 B안으로 교체
   - gap_pct = (ask1 - expected)/expected
   - gap ≥ overheat_pct → overheat(주문가 0, source ":overheat") 반환 → 반복 억제
   - raw_price ≤ base_cap → 정상(기존 동작)
   - raw_price > base_cap AND cap_gap ≤ trigger → base cap 기준 동적 cap 적용(dynamic/dyn_capped)
   - raw_price > base_cap AND cap_gap > trigger → 기존 base cap 고정(:capped)
   - 진단 필드(base_cap_price/dynamic_cap_price/gap_pct/cap_gap_pct/overheat/note) quote에 기록
3. **trading_engine.py `_place_aggressive_limit_with_retry`**: overheat 시 빠른 중단 +
   inferred_reason/message에 과열 사유 표기
4. **tests**: KT/SKT/과열/기존 diagnostics 회귀 테스트 추가·갱신

## 변경 파일
- `config.py` (설정 3개 추가 — 기존 dirty(Claude bridge)와 무관 영역)
- `modules/trading_engine/trading_engine.py` (_compute + retry 루프)
- `tests/test_aggressive_limit_chasing.py` (테스트 추가·갱신)
- (필요 시) `tests/test_buy_summary_failed_orders.py`

## 롤백 계획
- `LIMIT_AGGRESSIVE_DYNAMIC_CHASE_PCT=0` → 동적 cap 미적용, 기존 base cap 동작 복귀
- `LIMIT_AGGRESSIVE_OVERHEAT_PCT=0` → 과열 포기 미발동
- 완전 롤백은 두 값 0 + restart(코드 경로는 안전 디그레이드)

## 완료 기준
- KT/SKT 시나리오에서 주문가 > base cap 확인
- 과열 ask1(+1.8%↑)은 주문 반복 없이 overheat source/message로 즉시 포기
- 기존 diagnostics 테스트 갱신 후 통과
- `test_aggressive_limit_chasing.py` / `test_buy_summary_failed_orders.py` 전부 PASS
- py_compile 통과
- **서비스 재시작·실주문·크론 변경 없음**
