# 종가베팅 시스템 — PLAN.md

> 본 문서는 사용자 PRD v2.0(`종가베팅_트레이딩_시스템_PRD_v2.0.md`)과 strategy-coder 리뷰를 거쳐 확정된 구현 플랜이다.
> 전체 마스터 플랜은 `/home/hatni/.claude/plans/1-2-snazzy-rabbit.md`에도 동기화되어 있다.

## 목표

- 단기(16~18시간) 종가베팅 전략을 별도 시스템(`closing_bet_system/`)으로 추가 운용
- 비용 차감 후 EV 양수 상황만 진입을 허락하는 시스템 (종목 추천기 X)
- 기존 스윙 시스템과 같은 KIS 계좌 + 자금 풀 분리(10~15%)

## 배경

PRD v2.0이 forward test 위주로 설계된 이유: 종가베팅 핵심 신호(장중 수급 추정값, 막판 30분 체결강도, 동시호가 예상체결가)는 실시간 스냅샷이라 과거 데이터로 재현 불가능하다.

따라서 백테스트는 **3종 단계별** 도입:
1. **Pre-Phase 1 일봉 백테스트**: Layer 2 OHLCV 지표만으로 sanity check (방향 검증)
2. **Phase 2.5 후보 DB 백테스트**: 자체 수집 candidate_features로 점수 임계값별 EV 곡선
3. **Phase 3 Walk-forward**: 모델/임계값 재학습

## 사용자 결정사항

| # | 항목 | 결정 |
|---|---|---|
| 1 | 코드 위치 | `korean_stock_ai_trading/closing_bet_system/` |
| 2 | DB | `data/closing_bet.db` 별도 파일 |
| 3 | 텔레그램 | 신규 봇 (`CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID`) |
| 4 | 자금 | 같은 KIS 계좌, 자금 풀 비중 분리 |
| 5 | 시작 | 즉시 |
| 6 | 게이트 | **30건 운영 점검 / 100건 자동화 결정** 이중 게이트 |

## 구현 단계 (13단위)

### Phase 0. 사전 준비 (4단위)
- 0-A. 디렉토리 스켈레톤 + DB 스키마 + settings.yaml ← **현재 진행**
- 0-B. wrapper 3종 + fund_guard 미들웨어
- 0-C. Pre-Phase 1 백테스트 데이터/시뮬
- 0-D. Pre-Phase 1 백테스트 리포트

### Phase 1. 데이터 수집 + 알림형 (9단위)
- 1-1. cost_slippage_engine
- 1-2. kis_intraday_flow_collector
- 1-3. kis_price_volume_collector
- 1-4. signal_score_engine (단순, **Layer 1 가중치 0**)
- 1-5a. dart_disclosure_collector
- 1-5b. overnight_risk_filter
- 1-6. candidate_logger
- 1-7. telegram_review_bot (신규 봇)
- 1-8. main_orchestrator (**익일 매도 09:30 이후**)
- 1-9. 검증 (모의→실전, 추천 후보 30건 누적까지)

### Phase 2. 반자동 + 게이트 (10단위)
- 2-1. kis_orderbook_collector
- 2-2. kind_alert_collector
- 2-3. flow_reliability_tracker → Layer 1 가중치 활성화
- 2-4. entry_executor (fund_guard 통과 + 사용자 승인)
- 2-5. morning_exit_manager (시가 6단계, 09:30 이후)
- 2-6. dashboard_fastapi
- 2-7a/b/c. Phase 2.5 백테스트 (데이터/시뮬/리포트)
- 2-8. 100건 자동화 의사결정 게이트

### Phase 3. 부분 자동화 + Walk-forward (7단위)
- 3-1. ev_calculator
- 3-2. signal_score_engine ML 전환
- 3-3. Walk-forward 자동화
- 3-4. regime_detector
- 3-5. msci_rebalancing_module
- 3-6. swing_integration
- 3-7. kill_switch

### Phase 4. 운영 최적화 (별도 PRD)

## 변경 파일 목록 (전체 작업 누적)

**신규 생성**:
- `closing_bet_system/` 전체 디렉토리 트리
- `data/closing_bet.db`

**기존 파일 수정**:
- `.env` (텔레그램 토큰 2개 추가)
- `modules/reporter/telegram_notifier.py:37` (토큰 인자 주입)
- `requirements.txt`, `.mcp.json`, `CLAUDE.md`, `docs/INDEX.md` (필요 시점)

## 롤백 계획

- 단위별 git commit → 단위 미통과 시 해당 commit revert
- DB는 별도 파일이므로 단순 삭제로 롤백 가능
- 기존 시스템(스윙) 영향 최소화 — 공유 모듈 수정은 `telegram_notifier.py` 한 곳만 (역호환 보장)

## 완료 기준

- 모든 단위 CHECKLIST 항목 `[x]` 체크
- 100건 자동화 게이트 통과 (EV ≥ 0.5%, 평균 익절/손절 ≥ 1.3, 샤프 ≥ 1.0) 또는 게이트 미통과 시 Phase 2 연장 사유 문서화
- `docs/improvements/change_log.md` 갱신 (전략/파라미터 변경 시)
- `CLAUDE.md`에 종가베팅 운영 규칙 추가

## 참조

- `종가베팅_트레이딩_시스템_PRD_v2.0.md` (원 PRD)
- `/home/hatni/.claude/plans/1-2-snazzy-rabbit.md` (마스터 플랜)
- `CONTEXT.md` (구현 컨텍스트)
- `CHECKLIST.md` (단위 체크리스트)
