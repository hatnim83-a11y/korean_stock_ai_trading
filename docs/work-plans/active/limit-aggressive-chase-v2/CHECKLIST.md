# CHECKLIST — limit_aggressive 추격 B안

## 구현
- [x] config.py: DYNAMIC_CHASE_PCT / DYNAMIC_TRIGGER_PCT / OVERHEAT_PCT 3개 추가 (기존 dirty tree에 존재 확인)
- [x] _compute_aggressive_limit_price: B안 cap 로직 교체 + 진단 필드
- [x] _place_aggressive_limit_with_retry: overheat fast-break + 사유 메시지
- [x] quote/result 진단 필드(ask1/base_cap/dyn_cap/gap_pct/source/note) 노출

## 테스트 (먼저 작성)
- [x] KT 유사: base cap 초과 → dynamic 허용, 주문가 > base cap (test_..._kt_dynamic_chase_lifts_above_base_cap)
- [x] SKT 유사: 작은 cap 초과 → dynamic 허용 (test_..._skt_small_overshoot_allowed)
- [x] 과열 ask1 → 즉시 포기(overheat source/message, 주문 미발주) (2개 테스트)
- [x] 기존 diagnostics 테스트 갱신/유지 (_set_b_defaults 격리)
- [x] base cap 고정(gap>trigger) 회귀 테스트 유지 (test_..._base_cap_holds_when_gap_exceeds_trigger)

## 검증
- [x] pytest tests/test_aggressive_limit_chasing.py -q → 7 passed
- [x] pytest tests/test_buy_summary_failed_orders.py -q → 3 passed
- [x] scripts/test_aggressive_limit_order.py → 전체 PASS (8/8, expected_price mock 정합 후)
- [x] py_compile config.py trading_engine.py main.py → OK
- [ ] code-tester 에이전트 검증 생략 — Hermes가 직접 관련 테스트/compileall 검증

## 배포 (이번 세션 범위 아님 — 사용자 승인 후)
- [ ] docs/improvements/change_log.md 1줄 추가
- [ ] .env 신규 키(선택) — 운영 반영은 사용자 결정
- [ ] systemctl restart (사용자가 직접) — **이번 작업에서 금지**

## 문서 업데이트
- [ ] CLAUDE.md — v17/주문 규칙 섹션에 동적 추격 요약(필요 시)
- [ ] memory/MEMORY.md — 결과 1줄
- [ ] active/ → completed/ 아카이브

## 제약 준수
- [x] .env/DB/runtime 미수정 — 테스트 실행으로 일반 로그 파일 기록 가능성은 있으나 설정/DB 변경 없음
- [x] 서비스 재시작 없음
- [x] 실주문 없음
- [x] unrelated dirty 파일 미변경(기존 dirty tree 유지)
