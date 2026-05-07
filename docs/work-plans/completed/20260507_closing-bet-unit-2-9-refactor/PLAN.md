# PLAN — 단위 2-9 리팩토링 (`_maybe_run_per_ticker_fallback` 분리)

## 목표
`closing_bet_system/collectors/universe_filters.py` 의 `_maybe_run_per_ticker_fallback` 함수(약 145줄)를 가드/오케스트레이션만 남기고 3개의 단일 책임 헬퍼로 분리한다.

- 메인: `_maybe_run_per_ticker_fallback` (~50줄, 가드 + 캐시 + 호출 순서)
- 헬퍼 1: `_enrich_market_cap_from_kis_top` (단위 2-9d/2-9e — KIS market-cap top N 매치)
- 헬퍼 2: `_enrich_market_cap_from_volume_rank` (단위 2-9f — volume_rank lstn_stcn × stck_prpr)
- 헬퍼 3: `_enrich_ohlcv_per_ticker` (단위 2-9c — pykrx by_date 폴백)

## 배경
- 단위 2-9c → 2-9d → 2-9e → 2-9f 누적되며 함수가 비대해짐 (`market_cap_normalization` 작업으로 정렬은 정리되었으나 흐름은 단일 함수에 잔류)
- Phase 2 자동매매(단위 2-4/2-5) 진입 전 정리 필요 — 자동매매 추가 시 fallback 코드가 더 비대해지면 회귀 위험
- 단위 테스트 fixture 단순화 + 추가 시총 출처 도입(2-9g/2-9h) 시 유지보수성 개선

## 구현 단계
1. 헬퍼 함수 시그니처 확정 (mutate-fallback-dict 방식 통일)
   - `_enrich_market_cap_from_kis_top(candidates, fallback, cfg, n_input) -> int` (matched 반환)
   - `_enrich_market_cap_from_volume_rank(candidates, fallback) -> int` (1순위 미매치만)
   - `_enrich_ohlcv_per_ticker(candidates, fallback, today_str, krx) -> tuple[int, float]` (success_count, elapsed)
2. 헬퍼 3개를 메인 함수 위에 추가 (단위 책임 명시 docstring)
3. 메인 함수 본문 교체 — 가드(bulk 비어있음/tickers/캐시/cfg/토글) + candidates 빌드 + 헬퍼 호출 + 캐시 저장
4. 회귀 검증: `python -m py_compile`, import, 기본 호출(가드 분기/정상 흐름)
5. code-tester 에이전트 검증

## 변경 파일 목록
- `closing_bet_system/collectors/universe_filters.py` (단일 파일 — 함수 분리만)

## 롤백 계획
- 변경은 단일 파일 단일 함수 분리이므로 git revert 1회로 즉시 복구
- 새 헬퍼 3개는 모두 모듈 private (`_` prefix), 외부 호출자 없음
- 동작 변화 없음(순수 추출 리팩토링) → 운영 영향 없음

## 완료 기준
- [ ] `_maybe_run_per_ticker_fallback` 본문 ≤ 60줄 (가드+오케스트레이션만)
- [ ] 3개 헬퍼 모두 단일 책임 + 명시 docstring
- [ ] py_compile 통과
- [ ] systemctl restart 후 정상 가동 (16:00 헬스체크 통과 또는 syntax/import 에러 없음)
- [ ] code-tester 에이전트 critical/major 이슈 0건
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] active → completed 아카이브
