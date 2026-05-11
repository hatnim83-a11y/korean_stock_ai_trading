# CHECKLIST — 단위 2-3 flow_reliability_tracker (옵션 H)

## ✅ Step 0 (재조사 완료, 2026-05-11)
- [x] **0a**: pykrx 종목별 함수 탐색 — 7개 후보 함수 호출 → 모두 빈 응답/KeyError 확인 (OHLCV by_date만 작동)
- [x] **0b**: KRX 직접 endpoint 평가 — `data.krx.co.kr/comm/bldAttendant/getJsonData.cmd` LOGOUT 차단 + **네이버 `frgn.naver` 정상 작동 확인** (KOSPI/KOSDAQ 단일 endpoint)
- [x] **0d**: KIS Open API 공식 sample 정독 — **`HHPTJ04160200` (종목별 외인기관 추정가집계)** 발견 + 5/11 17:42 KST 직접 호출로 5건 응답 정상 확인
- [x] **결정**: 옵션 H 확정 — HHPTJ04160200 (장중 가집계) + 네이버 frgn.naver (사후 확정값) 양쪽 데이터 소스 확보, Step 1~6 진행 가능
- [x] PLAN/CONTEXT/CHECKLIST 옵션 H 재설계 반영

## (보조) Step 0 후속 검증 (5/12 화요일 자연 검증)
- [ ] 5/12 daily_pipeline 15:10 자연 검증: candidate_features.inst_net_buy_estimated 0이 아닌 값 다수 확인
- [ ] HHPTJ04160200 14:30 후 추가 입력 차수(15:00 등) 발생 여부 확인 — 만약 있으면 daily_pipeline 시간 조정 옵션 검토

## Step 1 — HHPTJ04160200 collector 통합
### kis_api.py 신규 메서드
- [ ] `modules/stock_screener/kis_api.py`에 `get_investor_trend_estimate(stock_code)` 메서드 추가
  - [ ] TR `HHPTJ04160200`, URL `/uapi/domestic-stock/v1/quotations/investor-trend-estimate`
  - [ ] params `{"MKSC_SHRN_ISCD": stock_code}`
  - [ ] output2 5행 파싱 (bsop_hour_gb '1'~'5')
  - [ ] 18자리 zero-padded 부호 문자열 → `_safe_int()` 캐스팅
  - [ ] 반환: `{stock_code, latest_inst_qty, latest_foreign_qty, latest_sum_qty, by_slot}`
  - [ ] bsop_hour_gb='5' 우선, 없으면 max slot_gb 폴백
  - [ ] try/except + logger.warning/error
- [ ] py_compile 통과
- [ ] 단위 테스트 (kis_api_unit_test 또는 인라인): 정상/빈응답/rt_cd 실패 케이스

### kis_intraday_flow_collector 수정
- [ ] `closing_bet_system/collectors/kis_intraday_flow_collector.py:230-240` 수정
  - [ ] `kis.get_investor_trend_estimate(stock_code)` 호출 추가
  - [ ] 응답 None 시 폴백 (`FHKST01010900` 결과 또는 None)
  - [ ] `inst_net_buy_estimated = trend["latest_inst_qty"] * latest_close`
  - [ ] foreign도 통일 검토 (HHPTJ로 일원화 vs 기존 FHKST 유지) — 결정 필요
- [ ] py_compile 통과
- [ ] code-tester 에이전트 검증
- [ ] 5/12 15:10 daily_pipeline 자연 검증 (inst != 0 확인)

## Step 2 — 네이버 frgn.naver 사후 확정값 수집기
- [ ] `closing_bet_system/services/__init__.py` 신규 (디렉토리 없으면)
- [ ] `closing_bet_system/services/flow_reliability_tracker.py` 신규 (~250줄)
- [ ] `fetch_naver_confirmed_flow(trade_date, tickers, sleep=0.4, retry=3, backoff=5)` 함수 구현
  - [ ] requests + BeautifulSoup 파싱
  - [ ] euc-kr 인코딩
  - [ ] tables[1] (외인/기관 매매현황) 추출
  - [ ] target_date 일치 행만 매칭 (당일 데이터 갱신 전 호출 시 graceful 누락)
  - [ ] `_parse_int()` 헬퍼: `'+1,074,644'` → 1074644
  - [ ] 재시도 3회 + 백오프 5초 (셀트리온 패턴)
  - [ ] ticker당 sleep (네이버 차단 회피)
- [ ] 단위 테스트
  - [ ] 정상 응답 (실 호출 mock 또는 saved HTML fixture)
  - [ ] 일자 미일치 (당일 데이터 갱신 전) graceful
  - [ ] 종목코드 무효 graceful

## Step 3 — 매일 19:27 매칭 잡
- [ ] `closing_bet_system/main_orchestrator.py`에 `run_flow_reliability_check(for_date=None)` async 메서드 추가
  - [ ] 영업일 보정 (라벨링 fix 패턴 재사용 — yesterday = today-1 → while not is_trading_day: yesterday-=1)
  - [ ] candidates 조회 (해당 일자) — candidate_features 정확한 컬럼명 확인 필수 (ticker vs stock_code)
  - [ ] candidate_features에서 inst/foreign estimated 추출
  - [ ] `fetch_naver_confirmed_flow(date, tickers)` 호출
  - [ ] `candidate_logger.log_flow_reliability(...)` 호출 (기존 함수 재사용)
  - [ ] for_date 인자로 수동 백필 지원
- [ ] APScheduler 잡 등록: `cron(hour=19, minute=27, day_of_week='mon-fri', timezone="Asia/Seoul")`
- [ ] `_skip_on_holiday` 데코레이터 적용
- [ ] py_compile 통과

## Step 4 — 신뢰도 집계 함수
- [ ] `compute_direction_match_rate(window_days=7, indicator='inst')` 함수 구현
  - [ ] indicator: 'inst' | 'foreign' 분기
  - [ ] N영업일 역산 (start 계산)
  - [ ] flow_data_reliability GROUP BY 집계
  - [ ] passes_70_threshold 자동 판정
- [ ] (선택) 70%+ 시 텔레그램 알림 발송 헬퍼

## Step 5 — 5/4~5/10 백필 (선택)
- [ ] 5/4, 5/7, 5/8 영업일 3개 대상 수동 백필 스크립트
- [ ] **inst는 박제(0) 데이터라 매칭 무효** — foreign만 매칭 유효
- [ ] foreign 약 50건 7일 윈도우 데이터 즉시 확보

## Step 6 — 활성화 결정 (별도 작업 단위)
- [ ] 5/12 봇 재시작 후 7~10일 누적 (5/19~5/26)
- [ ] `compute_direction_match_rate(window_days=7)` 실행
- [ ] inst 70%+ AND foreign 70%+ 시 활성화 권고 보고
- [ ] 사용자 승인
- [ ] `closing_bet_system/config/settings.yaml score.layer1_weight: 0.0 → 1.0` 변경
- [ ] `docs/improvements/change_log.md` 1줄 추가 (파라미터 변경 추적)
- [ ] systemctl restart trading_system

## 검증 (Step 별)
- [ ] Step 1 검증: 5/12 candidate_features.inst_net_buy_estimated 0이 아닌 값 다수 확인
- [ ] Step 2~3 검증: 5/12 19:27 잡 첫 실행 → flow_data_reliability 새 row 약 23건 INSERT
- [ ] Step 4 검증: compute_direction_match_rate 수동 호출 → n_samples > 0 결과 반환
- [ ] Step 6 검증: layer1_weight 변경 후 candidates 점수 분포 변화 (layer1 평균 상승 예상)

## 배포
- [ ] systemd restart 후 다음 잡(19:27) 정상 실행 확인
- [ ] 첫 회 잡 결과 텔레그램 알림 또는 로그 확인
- [ ] 1주일 누적 후 데이터 검토 (5/19 KST)

## 문서 업데이트
- [ ] `memory/project_closing_bet_followups.md` 갱신 (단위 2-3 구현 완료 표시 + Step 0 발견 사항 + 활성화 결정 트리거 일정)
- [ ] `memory/project_closing_bet_system.md` 갱신 (HHPTJ04160200 도입 + 단위 2-3 데이터 소스 변경)
- [ ] (Step 6 시) `docs/improvements/change_log.md` 1줄 추가
- [ ] 작업 폴더 `active/closing-bet-unit-2-3-flow-reliability/` → `completed/YYYYMMDD_closing-bet-unit-2-3-flow-reliability/` 아카이브

## 다음 세션 진입 명령
```
/resume
```
또는
```
docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/ PLAN/CONTEXT/CHECKLIST 읽고 Step 1부터 진행
```

## 다음 세션 첫 작업 (Step 1 진입 시 주의)
1. KIS 토큰 발급 1분 1회 제한 — 봇이 사용 중이면 60~70초 대기 필요
2. `candidate_features` 정확한 컬럼명 확인 (`ticker` vs `stock_code`) — 본 세션 sqlite3 호출 시 `no such column: ticker` 에러 발생, 다음 세션에서 `PRAGMA table_info(candidate_features);` 첫 실행
3. HHPTJ04160200 14:30 이후 추가 입력 차수(15:00 등) 발생 여부 — 5/12 daily_pipeline 결과로 자연 검증
