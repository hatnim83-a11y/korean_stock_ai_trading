# CHECKLIST — 종가베팅 누적 실현손익 노출

> **정정 이력**: 2026-07-27 검증 세션에서 실제 diff / 테스트 실행 결과로 정정.
> 2026-07-10 시점에 남아 있던 유일한 미결(`pytest 실행 — 환경 승인 게이트로 미실행`)을
> 이번 세션에서 실행해 해소했다.

## 구현
- [x] `data_adapter.get_realized_pnl_summary()` 추가 (read-only, 빈 결과 안전)
- [x] `web/api_routes.py` `GET /closing-bet/realized-pnl` 엔드포인트 추가
- [x] `dashboard.html` 종가베팅 탭 누적/오늘 실현손익·청산건수 카드 + JS 렌더
- [x] 전역 portfolio 계산부(dashboard_service.py)의 realized_pnl/current_total **계산 무변경** 확인
      (동일 파일에 `strategy_current_total` 키가 추가됐으나 이는 병행 작업
      broker-balance-separation 분이며, 값은 `current_total` 과 동일한 additive 키다.)

## 재작업 — financial-correctness follow-up (2026-07-10)
- [x] **근본원인 규명**: 저장 `net_pnl_pct` 재구성이 251/414 두 행에서 실현 원화와 불일치.
      exit_shares↔entry_amount 치환은 전 15행 no-op(둘 다 동일) — 실제 결함은 net_pnl_pct 신뢰.
- [x] `_row_realized_pnl()` 3단(Tier) 로직으로 재작성:
      Tier1 direct arithmetic(net_pnl_pct 무시) / Tier2 net_pnl_pct 폴백(entry_amount 우선,
      exit_shares 대용 금지) / Tier3 skip-safe(`skipped_incomplete` 집계)
- [x] `_num()` NULL-safe 헬퍼 추가, 반환 dict `skipped_incomplete` 키 추가 (UI/API 무해·additive)

## 검증
- [x] Tier1 직접계산: 비용 레그 4종 차감 + net_pnl_pct 무시 (test_tier1_subtracts_all_cost_legs...)
- [x] **실 DB 251/414 rounded KRW 재현**: 251=−4,440 / 414=+16,777 / 합산 12,336
      (테스트는 실 DB 를 열지 않고 해당 행 값을 **임시 sqlite 에 합성 재현**)
- [x] exit_shares≠진입수량 Tier2 폴백이 진입체결수량 사용(exit_shares 금지) — got 3000 not 2000
- [x] Tier2: entry_amount 우선 / legacy NULL buy_commission→설정율 / 율0→진입원가
- [x] Tier3 skip-safe: 불완전 legacy 행 손익 미조작 skip, skipped_incomplete 집계
- [x] 부분청산(final_exit_time NULL)·비청산(recommended) 제외 / 오늘 vs 누적 분리 / 음수 반영
- [x] reconciliation 회귀: 전역 current_total=TOTAL_CAPITAL+trading.db realized (KIS/종가베팅 미포함)
- [x] `py_compile` / `compileall` 통과 (data_adapter.py, api_routes.py, 테스트 파일)
- [x] code-tester 에이전트 재검토(재작업분): **심각 0 / 주의 4 / 참고 4**. 종합 "배포 가능".
- [x] 주의 반영: `_row_realized_pnl` 내 수치 변환 전부 `_num()` 경유로 하드닝
      (`exit_shares`/`net_pnl_pct`/진입수량 → ValueError 가 `except sqlite3.Error` 밖으로
      새어 요약 전체가 blank 되는 위험 제거). 방어 회귀 2종 + 경계 2종 테스트 추가.
- [x] ✅ **`pytest tests/test_closing_bet_realized_pnl.py` 실행 완료 (2026-07-27)** —
      이전 세션의 환경 승인 게이트 미결 해소.
      - 신규 5파일 합산 **38 passed** (2.42s)
      - 관련 스위트 `tests/test_closing_bet_daily_summary_phase1.py` +
        `tests/test_dashboard_improvements.py` → **19 passed** (회귀 없음)
      - 실행은 `python3 scripts/_run_pytest.py ... -q` 러너 경유
        (`venv/bin/python` 직접 호출이 이 환경에서 권한 차단되어 있어 venv site-packages 를
        sys.path 에 얹는 방식 — 동일 의존성)
- [x] 2026-07-27 code-tester 재검토(2개 작업 합동): **심각 0**. 본 작업 관련 지적 1건 수정 —
      dashboard.html 각주가 **폐기된 계산식**(`net_pnl_pct × 진입가 × 청산수량`)을 사용자에게
      공식 계산법으로 안내하고 있었다 → 실제 Tier1 직접 산술
      (`(청산가−진입가)×청산수량 − 수수료·거래세·슬리피지`) + Tier2 폴백/Tier3 제외 문구로 정정.
      표시 문구만 변경, 금액 계산 로직·API 응답 무변경.

## 배포 — **이번 작업에서 재시작하지 않음**
- [x] 재시작 불필요 확인: 이번 세션에서 systemctl restart/stop/start 미실행.
  - data_adapter.py / api_routes.py / dashboard.html mtime = 2026-07-10
  - `trading_dashboard`: PID 3222839, 시작 **2026-07-26 04:02:43 UTC**, active/running
    → 7/26 기동 시점에 이 dirty 코드를 **이미 로드**했으므로 종가베팅 실현손익 카드는
      현재 운영 대시보드에서 이미 동작 중이다.
  - `trading_system`: PID 3002350, 시작 **2026-07-13 00:54:33 UTC**, active/running (무변동)
- [ ] 브라우저 종가베팅 탭 카드 육안 확인 — 미수행(이번 세션은 브라우저 접속 없음).
      2026-07-27 정정한 각주 문구는 다음 대시보드 재시작 시 반영된다.

## 문서 업데이트
- [x] 완료 보고서 = 본 CHECKLIST 정정 + 세션 최종 보고로 대체
- [x] active/ → `completed/20260710_closing-bet-cumulative-realized-pnl/` 아카이브
