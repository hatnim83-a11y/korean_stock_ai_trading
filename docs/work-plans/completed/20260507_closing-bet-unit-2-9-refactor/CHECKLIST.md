# CHECKLIST — 단위 2-9 리팩토링

## 구현
- [x] `_enrich_market_cap_from_kis_top` 헬퍼 추가 (KIS market-cap top N 매치, matched 반환)
- [x] `_enrich_market_cap_from_volume_rank` 헬퍼 추가 (volume_rank 자체 계산, matched 반환)
- [x] `_enrich_ohlcv_per_ticker` 헬퍼 추가 (pykrx 종목별 OHLCV + sleep, success/elapsed 반환)
- [x] `_maybe_run_per_ticker_fallback` 본문을 헬퍼 호출 + 가드/캐시/오케스트레이션으로 단순화 (raw 89줄, 본문 ~55줄)
- [x] try/except 격리 의미 유지 (1순위 실패 → 2순위 시도, 2순위 실패 → 옵션 A 보수)
- [x] `fallback_include_market_cap=False` 시 두 시총 보강 모두 스킵 (중첩 의미 유지)

## 검증
- [x] `python -m py_compile closing_bet_system/collectors/universe_filters.py` 통과
- [x] `python -c "from closing_bet_system.collectors.universe_filters import _maybe_run_per_ticker_fallback"` 통과
- [x] 가드 분기 검증: `bulk_result` 비어있지 않을 때 즉시 return
- [x] 가드 분기 검증: `tickers=None/[]` 시 즉시 return
- [x] 가드 분기 검증: 같은 today_str 캐시 히트 시 즉시 return
- [x] 토글 OFF 시 함수 진입 차단 (가드 동일 보존)
- [x] code-tester 에이전트 critical/major 이슈 0건 (주의 1건은 리팩토링 이전과 동일값 → 후속 단위)

## 배포
- [x] systemctl restart trading_system (PID 4045208 → 4070866)
- [x] systemctl status trading_system → active(running) 확인
- [x] 22개 스케줄 잡 정상 등록 확인
- [x] `docs/improvements/change_log.md` 1줄 추가

## 문서 업데이트
- [x] `memory/project_closing_bet_followups.md` — 단위 2-9 리팩토링 완료 표시 + 다음 우선순위 갱신
- [x] active → completed 아카이브
- [ ] git commit (별도 사용자 승인 후)

## 후속 (별도 단위로 분리)
- [ ] `_enrich_market_cap_from_volume_rank` 의 `top_n=30` 하드코딩 → `DEFAULT_VOLUME_RANK_TOP_N` 모듈 상수화 또는 settings cfg 노출 (code-tester 주의 #1, 비긴급)
