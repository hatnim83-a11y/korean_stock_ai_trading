# CHECKLIST — 단위 2-3 flow_reliability_tracker

## 🚨 2026-05-11 발견 사항 — 옵션 G 결정 (세션 종료 + 재설계)
- [x] KIS API 응답 검증: 16:06 호출 시 institution 정상값 (005930 +1,604,512 등)
- [x] 가설 D 확정: KIS `FHKST01010900` 15:10 시점에 inst=0 반환 정책 (코드 버그 아님)
- [x] **사용자 핵심 통찰**: 16:00 재수집은 매수 결정 시점 지난 후라 무효 — 옵션 A 철회
- [x] pykrx 빈 응답 확인: `get_market_net_purchases_of_equities_by_ticker` 모든 날짜/투자자에서 shape=(0,0)
- [x] PLAN/CONTEXT/CHECKLIST 갱신: 재설계 결정 + Step 0 (데이터 소스 재조사) 추가

## Step 0 (재조사) — 다음 세션 진입 시
- [ ] **0a**: pykrx 종목별 함수 탐색 — `from pykrx import stock as krx; help(krx)` 로 종목별 투자자 매매 함수 존재 여부 확인 (30분)
- [ ] **0b**: KRX 정보데이터시스템 직접 크롤링 평가 (data.krx.co.kr — OTP 발급 후 매매대금 API)
- [ ] **0c**: 5/12(화) 실시간 KIS 호출로 inst 갱신 시점 추적 — 15:10/15:15/15:20/15:25/15:28/15:30/15:35 분 단위 호출 + 응답 비교
- [ ] **0d**: KIS 다른 TR 탐색 — `FHKST01010800` (장중 외인/기관 가집계) 또는 `FHPST01040000` 등 (1~2시간)
- [ ] **결정**: 0a~0d 결과 종합 → 단위 2-3 본체 진행 방식 선택 (원래 PLAN / 0c 기반 시간 변경 / 옵션 D 단순화 / 영구 비활성)

## (보류) Step 1: inst_net_buy_estimated 수집 버그 fix
- [ ] `kis_intraday_flow_collector.py:230-240` 코드 정독 + KIS API 응답 직접 디버깅
- [ ] KIS `get_investor_trading` 응답 raw dump 확보 (이번 주 후보 1~3종목)
- [ ] `latest_inst_qty` 추출 키 확인 (`acml_ntby_qty` vs 다른 키)
- [ ] 5/11 candidate_features 23건 inst=0 원인 정확히 식별
- [ ] 수정 (예: 키 변경 / _safe_int 가드 / 응답 구조 변경)
- [ ] py_compile 통과
- [ ] code-tester 에이전트 검증
- [ ] systemd restart 또는 5/12 15:10 잡으로 검증 (정상 추정치 수집 확인)

## Step 2: KRX 확정값 수집기 신규
- [ ] `closing_bet_system/services/` 디렉토리 생성 (없으면)
- [ ] `flow_reliability_tracker.py` 신규 (~200줄)
- [ ] `fetch_krx_confirmed_flow(trade_date, tickers)` 함수 구현
  - [ ] pykrx `get_market_net_purchases_of_equities_by_ticker` 호출
  - [ ] KOSPI + KOSDAQ 양 시장 처리
  - [ ] '기관합계' + '외국인합계' 두 투자자 처리
  - [ ] 예외 처리 (빈 응답 / ticker 누락 / pykrx API 변경)
  - [ ] 재시도 로직 (3회, 5초 백오프 — 셀트리온 사례 동일 패턴)
- [ ] 단위 테스트: 정상 응답 / 빈 응답 / 일부 ticker 누락

## Step 3: 매일 19:30 매칭 잡
- [ ] `main_orchestrator.py`에 `run_flow_reliability_check(for_date=None)` async 메서드 추가
  - [ ] 영업일 보정 (라벨링 fix 패턴 재사용 — yesterday = today-1 → while not is_trading_day: yesterday-=1)
  - [ ] candidates 조회 (해당 일자)
  - [ ] candidate_features에서 inst/foreign estimated 추출
  - [ ] fetch_krx_confirmed_flow 호출 (Step 2 함수)
  - [ ] candidate_logger.log_flow_reliability 호출 (기존 함수 재사용)
  - [ ] for_date 인자로 수동 백필 지원 (라벨링 fix 패턴 동일)
- [ ] APScheduler 잡 등록: `cron(hour=19, minute=27, day_of_week='mon-fri')`
- [ ] _skip_on_holiday 데코레이터 적용 검토
- [ ] py_compile 통과

## Step 4: 신뢰도 집계 함수
- [ ] `compute_direction_match_rate(window_days=7, indicator='inst')` 함수 구현
  - [ ] indicator: 'inst' | 'foreign' 분기
  - [ ] N영업일 역산 (start 계산)
  - [ ] flow_data_reliability GROUP BY 집계
  - [ ] passes_70_threshold 자동 판정
- [ ] (선택) 70%+ 시 텔레그램 알림 발송 헬퍼

## Step 5: 5/4~5/10 백필 (선택)
- [ ] 5/4, 5/7, 5/8 영업일 3개 대상 수동 백필 스크립트
- [ ] inst 버그 fix 전 데이터라 inst_estimated=0 vs 확정값 매칭 → 부분 무효 인지
- [ ] foreign만이라도 즉시 7일 윈도우 데이터 확보

## Step 6: 활성화 결정 (별도 작업 단위)
- [ ] 7~10일 누적 후 compute_direction_match_rate(window_days=7) 실행
- [ ] inst 70%+ AND foreign 70%+ 시 활성화 권고 보고
- [ ] 사용자 승인
- [ ] `settings.yaml score.layer1_weight: 0.0 → 1.0` 변경
- [ ] `docs/improvements/change_log.md` 1줄 추가 (파라미터 변경 추적)
- [ ] systemctl restart trading_system

## 검증 (Step 별)
- [ ] Step 1 검증: 5/12 candidate_features.inst_net_buy_estimated 0이 아닌 값 다수 확인
- [ ] Step 2~3 검증: 5/12 19:30 잡 첫 실행 → flow_data_reliability 새 row 23건 INSERT (5/11 후보 수)
- [ ] Step 4 검증: compute_direction_match_rate 수동 호출 → n_samples > 0 결과 반환
- [ ] Step 6 검증: layer1_weight 변경 후 candidates 점수 분포 변화 (layer1 평균 상승 예상)

## 배포
- [ ] systemd restart 후 다음 잡(19:27) 정상 실행 확인
- [ ] 첫 회 잡 결과 텔레그램 알림 또는 로그 확인
- [ ] 1주일 누적 후 데이터 검토 (5/19 KST)

## 문서 업데이트
- [ ] `memory/project_closing_bet_followups.md` 갱신 (단위 2-3 구현 완료 표시 + 활성화 결정 트리거 일정)
- [ ] (Step 6 시) `docs/improvements/change_log.md` 1줄 추가
- [ ] 작업 폴더 `active/closing-bet-unit-2-3-flow-reliability/` → `completed/YYYYMMDD_closing-bet-unit-2-3-flow-reliability/` 아카이브

## 다음 세션 진입 명령
```
다음 세션에서 /resume 또는:
"docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/ 의 PLAN/CONTEXT/CHECKLIST 읽고 Step 1부터 진행"
```
