# PLAN — 종가베팅 청산/필터 수익 극대화 (A+B+과열필터)

- **작성일**: 2026-06-15
- **근거 제안서**: `docs/improvements/20260615_closing_bet_exit_logic_proposal.md` (A·B), `docs/improvements/20260615_closing_bet_overheat_filter_proposal.md` (필터), 상위 분석 `docs/improvements/20260615_closing_bet_candidate_full_analysis.md`
- **사용자 결정(2026-06-15)**: 범위 = **전부(A+B+과열필터)**, 수익 극대화 방향. 단 글로벌 규칙(단위별 진행)에 따라 **phased 구현 + 단위별 사용자 확인**.

## 목표
종가베팅 실거래 5건이 신호는 양호한데 청산 시장가 투매로 평균 −3.00% 손실. 청산 방식 개선 + 오전 반등 캡처 + 과열필터 완화로 수익을 극대화한다.

| 단계 | 내용 | 5건 반사실 | 리스크 |
|---|---|---|---|
| **Phase 1 (A)** | morning_exit 시장가 → **시가 지정가 + 미체결 시장가 폴백** | −3.00% → **+0.49%** | 낮음 (검증된 바닥) |
| **Phase 2 (B)** | 오전 **부분익절 + 트레일링**(단위 2-5g) 도입 | → **+1.55%** | 중 (폴링 루프 신규) |
| **Phase 3 (필터)** | atr_overheat 하드컷 → **밴드 차등**(1.8~2.2 차단 / 2.2+ 통과) | 아침고가 +9.85% 알파 회수 | 중 (진입 변동성↑) |

> 각 Phase는 **독립 토글 + dry_run 선검증 + 사용자 확인** 후 다음으로 진행. 한 번에 전부 배포하지 않는다.

## 범위 경계 (리뷰 반영 — 절대 건드리지 않음)
- **emergency_stop은 시장가 유지** — 갭하락 긴급손절에 지정가 60초 지연은 역효과 (strategy-planner + code-tester 공통 권고).
- **force_close(10:30)는 시장가 유지** — 최후 안전망.
- `hard_stop_loss(-0.01)`, `gap_up_*`, `flat_lower` 등 **시가 액션 임계값 전부 불변** (표본 부족).

## Phase 1 (A) 상세 — 시가 지정가 + 폴백
### 신규 함수 `_execute_limit_sell_with_fallback`
1. 토글 `open_limit_sell_enabled` ON + morning_exit 경로 → `sell_limit_order(ticker, qty, price=snap.open_price)`
2. `limit_fill_deadline_sec`(default **30초** — 09:02 발주→09:02:30 폴백→09:05 스윙매수 전 수렴) 폴링
3. **전량체결** → `log_exit` 1회 → 종료
4. **미체결/부분체결** → `cancel_order` → `_wait_cancel_confirm` → **최종 체결수량 재조회** → **잔량(total−executed)만** `sell_market_order` → `log_exit` **1회(가중평균가·합산수량)**
5. 불변식: `log_exit` 정확히 1회 / 잔량은 `remaining`로 계산(`total_shares` 금지) / `_pending_exit_orders` ODNO는 취소확정 후 교체

### 변경 파일 (Phase 1)
| 파일 | 변경 |
|---|---|
| `closing_bet_system/execution/exit_executor.py` | `_execute_limit_sell_with_fallback` 신규 + `_process_morning_exit` 분기 + `ExitExecutorSettings`에 `open_limit_sell_enabled:bool=False`/`limit_fill_deadline_sec:float=30.0` + dry_run 로그 포맷 |
| `closing_bet_system/config/settings.yaml` | `morning_exit:` 섹션에 `open_limit_sell_enabled: false` + `limit_fill_deadline_sec: 30.0` |
| `closing_bet_system/main_orchestrator.py` | `ee_settings`(324~347)에 신규 2키 매핑 |
| `modules/trading_engine/kis_order_api.py` | **MockOrderApi.sell_limit_order 추가**(buy_limit_order 패턴, scenario_fill_ratio 재사용) |
| `scripts/test_exit_executor.py` | 경계 테스트 추가(전량/부분→폴백집계/미체결/dry_run/토글OFF NO-OP/sell_lock/open_price 방어) |
| `docs/improvements/change_log.md` | 1줄 추가 |
| phase25_simulator 가정 주석/문서 | open_pct 정합 동기 |

## Phase 2 (B) — 별도 단위 2-5g (Phase 1 검증 후 착수)
- gap_up_high 외 구간도 1차 부분익절(예 50%) + 잔여 오전 트레일링(고점 −1.5%, 10:30 force_close).
- `exit.trailing_stop_pct=-0.015` dead config 활성화. 폴링 루프 신규 → sell_lock 동시성 재검증.
- **Phase 1 실발주 1주 관찰 후 별도 PLAN 분리.**

## Phase 3 (과열필터) — 별도 제안서 기반 (진입 모듈, 병행 가능)
- `signal_score_engine.py` atr_overheat 하드컷(1.8) → 밴드 차등(1.8~2.2 차단 / 2.2+ 예외 통과) 또는 점수 감점 전환.
- **청산 개선(Phase 1)과 세트로만 효과 실현** (제안서 권고). 별도 PLAN 분리.

## 롤백
- Phase 1: `open_limit_sell_enabled=false` + restart → 기존 시장가 경로 완전 NO-OP (default False).
- Phase 2/3: 각자 독립 토글.

## 완료 기준 (Phase 1)
1. py_compile + 신규/기존 테스트 PASS (부분체결 폴백 집계 포함)
2. code-tester 에이전트 심각/주의 이슈 0
3. dry_run 단발 검증: "지정가=open_price" 로그 + KIS 실발주 0
4. (실발주 전환은 별도 사용자 승인) 1주 관찰 — "청산가−시가" 갭 −3.44%p→0 수렴
5. change_log.md + 문서 갱신
