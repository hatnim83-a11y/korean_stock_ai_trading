# 파라미터 변경 이력 (before/after 추적용)

`trade-improvement-analyst` 에이전트의 제안 → 사용자 승인 → strategy-coder 구현으로 이어진 **파라미터 조정의 단일 진실 공급원(single source of truth)**이다. 다음 분석 사이클에서 에이전트가 이 파일을 읽어 변경 전/후 성과를 비교 보고한다.

## 기입 규칙

- **누가**: strategy-coder (CHECKLIST 배포 항목에서)
- **언제**: 제안 구현 후, 배포(재시작) 직전 또는 직후
- **어떻게**: 아래 표에 **1줄 추가만**. 기존 행 편집 금지. 롤백/재조정은 새 행으로 기록.
- **필수 항목**: 날짜, 파라미터명, 이전값, 변경값, 제안서 경로, 승인자

## 변경 이력

> 에이전트 도입(2026-04-21) 이전 변경 3건은 **소급 기록**(제안서 경로 대신 커밋 해시)이므로 "제안서 경로" 칼럼에 `commit:<hash>` 표기. 이후 변경은 반드시 에이전트 제안서 경로를 기록할 것.

| 날짜 (KST) | 파라미터명 | 이전값 | 변경값 | 제안서 경로 | 승인자 | 비고 |
|-----------|-----------|-------|-------|------------|--------|------|
| 2026-04-14 | TRAIL_BE_ACTIVATION / TRAIL_BE_STOP | (미구현) | +5% 도달 시 매수가 -1% 손절 | commit:562e1d5 | hatni | 소급 기록. +5% 도달 후 하락 케이스(오이솔루션형) 방어. Phase 1 focus:stop_loss 제안서가 효과 측정 대기 중 |
| 2026-04-16 | ORDER_TYPE_DEFAULT | "market" (시장가 01) | "limit_aggressive" (지정가 00 + 매도 1호가) | commit:7161210 | hatni | 소급 기록. 증거금 1.3→1.04배로 슬롯당 실제 투자금 73→91% 확대 목표. Phase 5 실전 관찰 중 |
| 2026-04-21 | THEME_MOMENTUM_BOOST_FACTOR / CLAMP / DROP_COOLDOWN | (기존 ×1.5 무제한, 쿨다운 없음) | factor 0.7 / clamp ±8 / top_k 30 / cooldown ON | commit:d820638 | hatni | 소급 기록. 화요일 테마 재선정 회전문 방지 Phase 1+2 |
| 2026-04-24 | RSI_DYNAMIC (RSI_UPPER_BULL/NORMAL/BEAR) + THEME_MIN_SLOT | RSI 70 고정 / 테마 슬롯 보장 없음 | 강세장 75 / 평시 70 / 약세장 65, 테마당 최소 1개(≥25점) AI 검증 보장 | docs/improvements/2026-04-23_buy_filter_proposal.md | hatni | Phase A 제안 #2+#3 동시 배포. DB v14 마이그레이션 (rsi_at_screen, theme_slot_protected 컬럼). 1주 관찰 후 롤백 트리거 체크 |
| 2026-05-01 | trade_reviews 적재 경로 (시스템 무결성 fix) | main.py 매도 3경로(보유기간/midweek 수익/midweek 손실)에서 save_trade_review 미호출 → 4월 SELL 18건 vs reviews 14건 (5건 누락) | main.py에 `_save_trade_review_for_main_sell` + `_compute_hold_days` 헬퍼 추가, 3경로에서 trade_id 캡처 + 헬퍼 호출. 누락 5건은 `scripts/recover_missing_trade_reviews.py`로 소급 복구 | docs/improvements/2026-05_monthly.md | hatni | 파라미터 변경이 아닌 데이터 무결성 버그 fix. monthly 분석에서 발견. 19건 review_count=1 검증 완료. systemd 재시작 (PID 1008604→1947127). 알려진 한계: midweek strategy_type="manual"로 분류, 소급 3건 max_profit_during_hold NULL |
| 2026-05-01 | RSI_DYNAMIC + THEME_MIN_SLOT (Phase A 유지 결정) | (4/24 배포 동일) | (변경 없음, 1주 관찰 후 유지) | docs/improvements/2026-W18-weekly.md | hatni | Phase A 1주 관찰 결과: 통과율 17.9%→20.6%, 매수 실패일 0/4, 슬롯 보장 19건 발동, 롤백 트리거 9.1절 5개 모두 미발동. Phase B(MIN_FINAL_SCORE 45→42)는 매도 표본 N=1로 보류, 5/15 재평가. 작업 디렉토리 active→completed 아카이브 |
| 2026-05-01 | screening_log 다단계 로그 (W19 분석 데이터 기반) | screening_log.stage 단일값 'filter'만 존재 (2,985건) — 갭 필터/AI 검증 단계 흔적 부재 | morning_screener.py에 `_save_gap_filter_logs` (stage='gap_filter'), verifier.py에 `_save_ai_verify_logs` (stage='ai_verify') 추가. 통과/탈락 + reject_reason + score(AI sentiment) + details_json 기록 | docs/improvements/2026-05_monthly.md | hatni | 파라미터 변경이 아닌 데이터 인프라 fix. UNIQUE(date, stock_code, stage) 이미 존재해 마이그레이션 불필요. 코드 추가만으로 구현. 매수 흐름과 try/except 격리. systemd 재시작 (PID 1947127→1965133). W19 (5/8) `focus:gap_filter` 트리거 전 데이터 누적 시작 |
| 2026-05-04 | 매도 슬리피지 측정 (sell-slippage-tracking, 데이터 인프라 fix) | trades.slippage 95% NULL (55건 중 52건) — 시장가 매도 order_price=0이라 분모 0 + 5경로 모두 _save_trades 우회 | trading_engine에 `_capture_sell_reference_price`(bid1→current_price→fallback) + `_compute_sell_slippage` 헬퍼 추가, execute_sell_orders/stop_loss/take_profit 3진입점에서 result에 reference_price/source/slippage 채움. monitor 4함수(_close_position/_save_partial/_execute_max_hold/_execute_trailing) + main.py 3경로(보유기간/midweek 수익/손실)에서 result.get("slippage") 추출 후 db.save_trade에 전달. _save_trades 매도 분기는 order["slippage"] 우선, reference_price 폴백 | docs/work-plans/active/sell-slippage-tracking/PLAN.md | hatni | 파라미터 변경이 아닌 데이터 인프라 fix. DB 스키마 변경 없음(v9 slippage 컬럼 활용). 단위 테스트 11건 PASS, code-tester 심각 0건. 공격적 지정가 매수(2026-04-16) 효과 평가의 매도 측 카운터파트 지표 확보 목적. 장 마감 후 systemd 재시작 예정 → 5/5(화) 매도부터 측정 시작 |
| 2026-05-04 | 종가베팅 시스템 Phase 1 (closing-bet-system, 신규 모듈 도입) | (신규 시스템 — 기존 파라미터 변경 아님) | closing_bet_system/ 9 모듈 신규 (cost_engine/2 collector/score_engine/2 risk/logger/notifier/orchestrator) + main.py 통합 (register_jobs로 APScheduler 잡 3건 추가, placeholder providers로 무동작 상태 유지) + 신규 텔레그램 봇 (chat_id=8509696011 스윙과 단일 채널) | docs/work-plans/active/closing-bet-system/PLAN.md | hatni | Phase 1 = 알림형 (자동매수 절대 금지). 9/9 단위 완료, 200+ 테스트 PASS, 모든 모듈 code-tester 통과. main.py 통합은 placeholder universe (빈 리스트) 라 잡 등록되지만 무동작 → 안전. Phase 2 진입 시 universe_provider/market_data_provider/name_lookup/label_provider 실 구현체 연결 필요. 1-9 운영 점검 게이트 (30건 누적) 데이터 수집 시작점 |
| 2026-05-04 | 종가베팅 Phase 2 데이터 수집 (closing-bet-phase2-data-collection, 옵션 A 3단위) | Phase 1 이월 후 자동매매 30건 게이트 누적 대기 기간에 수집/모니터링 인프라 추가 (호가 데이터 0건 / KIND 통합 인터페이스만 / 대시보드 종가베팅 탭 부재) | 단위 2-1: kis_orderbook_collector(KIS inquire_asking_price 호출 → ask1/bid1/잔량/스프레드 OrderbookSnapshot, DB v2 orderbook_snapshots 테이블 + 2 인덱스, main_orchestrator asyncio.gather 4 collector 병렬 + DB INSERT). 단위 2-2: kind_alert_collector(KindAlertSnapshot dataclass + provider 주입 + 4단계 한글명→severity 매핑, OvernightRiskFilter assess/assess_for_universe에 kind_alerts 인자 + _resolve_kind_severity 헬퍼, severity 3→can_enter=False / 2→size 0.5 / 1→warnings) — 1단계 인터페이스만 활성, KindHttpProvider는 후속 단위. 단위 2-6: dashboard data_adapter(5 read-only 헬퍼) + web/api_routes 5 엔드포인트(/api/v1/closing-bet/{today,gate-progress,orderbook-history,rejections,fund-guard-status}) + dashboard.html "종가베팅" 탭(loadClosingBet, 5 카드/테이블 섹션). | docs/work-plans/active/closing-bet-phase2-data-collection/PLAN.md | hatni | 자동매매 위험 0 (모두 데이터 수집/표시). 단위 테스트 42건 PASS (OB 13 + KA 16 + DA 13). code-tester 심각 2건(insert_snapshots db=None 누수, assess_for_universe kind_alerts 누락) + 주의 2건 모두 보강. 종가베팅 첫 잡 트리거(15:10 KST) 이후 KIS API 추가 ~80호출/일 예상. KIND 실 데이터는 사이트 구조 안정성 검토 후 별도 단위로 도입 예정. systemd + dashboard 동시 재시작 (PID 2511049→2533276 / 2295896→2533412). |
| 2026-05-04 | 종가베팅 Phase 1 이월 항목 (closing-bet-phase1-carryover, providers 활성화) | placeholder providers 4종 모두 무동작 (universe=[], market_data={}, name_lookup="(미상)", label_provider=None) + fund_guard.weekly_loss_limit 미구현 + telegram_client 부모 내부 속성 직접 변경 (silent break 위험) | 단위 A: name_lookup.py(신규, KIS get_stock_name+캐시) + telegram_client.py(NoOp 더미 패턴). 단위 B: universe_provider.py(신규, 스윙 top_themes→네이버 크롤링→list[ticker], 캐시+검증+중복제거+스윙보유제외+hard_cap=20). 단위 C: market_data_provider.py(신규, KOSPI=KIS+V-KOSPI/미선물/USD-KRW=yfinance, Phase 1 결손정책 None 폴백). 단위 D: label_provider.py(신규, KIS daily 첫 행 + 라벨 4종 + cost_engine 통합) + main_orchestrator.py(label_provider 인자 추가, run_label_yesterday 인스턴스 폴백). 단위 E: fund_guard.py(weekly_loss_limit 8번째 검사 추가, _fetch_db_state 단일 connection 4쿼리). scheduler.py 4 lambda → 4 실 함수. | docs/work-plans/active/closing-bet-phase1-carryover/PLAN.md | hatni | 5단위 단위 테스트 55건 PASS (NL/TG 12 + UV 10 + MD 11 + LP 11 + WL 11). code-tester 검증 완료 (단위 A 심각 0/주의 2 / 단위 B-E 종합 심각 0/주의 4 — false alarm 1건은 KIS 정렬 방향 명시 주석+sanity check로 보강, 나머지 P2 4건은 주석/로그 개선으로 보강). DB 변경 없음. 종가베팅 Phase 1 잡 3건 (15:10/15:35/10:00) 실제 동작 가능 — universe 비-empty 시 알림 발송. Phase 2 (반자동) 진입 준비 완료. |

<!--
예시 행 (실제 변경 시 이런 형식으로 추가):
| 2026-05-03 | STOP_LOSS_FAST | -0.07 | -0.06 | docs/improvements/2026-W17-weekly.md | hatni | 트레일링 사전 발동 빈도 감소 목적 |
-->

## 관련 링크
- 제안서 템플릿: `docs/improvements/_TEMPLATE.md`
- 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`
- 관리 규칙: `docs/improvements/README.md`
