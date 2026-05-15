# PLAN: 종가베팅 단위 2-4 entry_executor (Phase 2 자동매매 진입)

## 목표
종가베팅 100건 게이트 PASS 후 PRD 9-1/9-2/9-3 정합 자동매매 진입 모듈 구현. score≥2 / 포지션 PRD 70% / 옵션 C 운영. 단위 2-4a~e 완료 후 사용자 승인 시 단위 2-4f 실전 활성화.

## 배경
- 5/14 walkforward (n=103, realistic EV +1.04% / Sharpe +1.57)
- score≥2 (n=66): EV +1.60% / Sharpe +2.08 / W-L ∞
- strategy-planner + strategy-coder 병렬 리뷰 P0 5건 + P1 7건 반영 완료
- 메인 마스터 플랜: `/home/hatni/.claude/plans/recursive-questing-zephyr.md`

## 단위 분할 (6단위)
- **2-4a** Step 0 KIS 사전 조사 (4건: ord_dvsn / 예상체결가 / 분봉 / TR_ORDER_STATUS)
- **2-4b** collectors (vwap / estimated_price / orderbook polling) + fill_checker + price_utils
- **2-4c** EntryExecutor 클래스 + DB v2 마이그레이션 (+6 컬럼)
- **2-4d** APScheduler 단일 잡 통합 (run_entry_pipeline) + settings.yaml
- **2-4e** 단위 119건 테스트 + dry_run 통합 단발 + code-tester
- **2-4f** 실전 활성화 (사용자 승인 후 별도 세션)

## 변경 파일 요약 (신규 + 수정)
신규 8개 + 수정 4개. 마스터 플랜 "신규 컴포넌트 요약" / "수정 파일" 참조.

## 롤백
- 워크트리 격리 작업, 단위별 commit
- 실전 활성화 시 `settings.yaml entry_executor.enabled=false` + systemctl restart

## 완료 기준 (단위 2-4e 종료 시점)
- 단위 테스트 119건 PASS (회귀 60 + 신규 59)
- code-tester 심각 0건
- dry_run 통합 단발 성공
- 단위 2-4f 활성화 게이트 조건 충족 (단위 2-5 또는 수동 SOP 준비 + 사용자 승인)
