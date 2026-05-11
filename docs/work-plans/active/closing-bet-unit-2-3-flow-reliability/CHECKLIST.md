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

## Step 1 — HHPTJ04160200 collector 통합 ✅ (2026-05-11 완료)
### kis_api.py 신규 메서드
- [x] `modules/stock_screener/kis_api.py`에 `get_investor_trend_estimate(stock_code)` 메서드 추가
  - [x] TR `HHPTJ04160200`, URL `/uapi/domestic-stock/v1/quotations/investor-trend-estimate`
  - [x] params `{"MKSC_SHRN_ISCD": stock_code}`
  - [x] output2 5행 파싱 (bsop_hour_gb '1'~'5')
  - [x] 18자리 zero-padded 부호 문자열 → `_safe_int()` 캐스팅
  - [x] 반환: `{code, latest_inst_qty, latest_foreign_qty, latest_sum_qty, by_slot}` 또는 None
  - [x] **정수 비교**로 마지막 차수 추출 (code-tester 심각 fix — 사전순 '10' < '9' 역전 차단)
  - [x] try/except + logger.warning/error 구분
- [x] py_compile 통과
- [x] 인라인 단위 테스트 (정상/역순/빈문자열 슬롯 3 케이스 PASS)

### kis_intraday_flow_collector 수정
- [x] `closing_bet_system/collectors/kis_intraday_flow_collector.py` 수정
  - [x] `_parse_payload()` 시그니처에 `trend_payload: Optional[dict] = None` 추가
  - [x] `collect_snapshot()` 에 HHPTJ 호출 추가 (try/except FHKST 폴백)
  - [x] `inst_net_buy_estimated` 산출: HHPTJ `latest_inst_qty != 0` 우선 → FHKST `daily[0].institution` 폴백
  - [x] foreign 통일은 보류 — FHKST의 daily[0:3] 3일 누적 그대로 유지 (HHPTJ는 당일 가집계만이라 3일 누적 못 만듦)
  - [x] HHPTJ inst=0 미집계/실제 0주 구분 불가 주석 추가 (Phase 2 by_slot 활용 예정)
  - [x] 모듈 docstring 업데이트 (HHPTJ 우선/FHKST 폴백 명시)
- [x] py_compile 통과
- [x] code-tester 에이전트 검증 (심각 1건 fix + 주의 1건 주석 추가)
- [x] **실 호출 검증** (5/11 KST 17:43 / 18:42):
  - 005930 삼성전자: `inst_net_buy_estimated = +411,691,000,000원` (이전 0)
  - HHPTJ `latest_inst_qty = 1,442,000` × 종가 285,500 = 정확히 매칭
  - 086520 에코프로 (KOSDAQ): inst = -732,000,000원 정상
  - 5/11 candidates 상위 3건 (한온/삼성물산/대한광통신) 모두 inst != 0
- [ ] 5/12 15:10 daily_pipeline 자연 검증 (inst != 0 확인) — 봇 재시작 후 잡 실행 확인

## Step 2 — 네이버 frgn.naver 사후 확정값 수집기 ✅ (2026-05-11 완료)
- [x] `closing_bet_system/services/__init__.py` 신규
- [x] `closing_bet_system/services/flow_reliability_tracker.py` 신규 (~290줄)
- [x] `fetch_naver_confirmed_flow(trade_date, tickers, sleep=0.4, retry=3, backoff=5)` 함수 구현
  - [x] requests + BeautifulSoup 파싱 + `r.encoding = 'euc-kr'`
  - [x] tables[1] (외인/기관 매매현황) 추출, `len(tables) < 2` 가드
  - [x] target_date 일치 행만 매칭 (당일 데이터 갱신 전 호출 시 graceful 누락)
  - [x] `_parse_int()` 헬퍼: `'+1,074,644'` → 1074644
  - [x] 재시도 3회 + 백오프 5초 (셀트리온 패턴)
  - [x] ticker당 sleep (네이버 차단 회피, 마지막 ticker 후 sleep 생략)
  - [x] `_is_valid_ticker()` 헬퍼 (6자리 숫자 검증)
- [x] `compute_direction_match_rate(window_days, indicator, db_path, end_date)` 함수 구현
  - [x] indicator 'inst' | 'foreign' 분기 (`_INDICATOR_COL_MAP` dict 매핑 — SQL injection 방어)
  - [x] N영업일 역산 (end 포함 N영업일 정책)
  - [x] flow_data_reliability GROUP BY 집계
  - [x] passes_70_threshold 판정 (`MATCH_RATE_THRESHOLD = 0.7` 상수)
- [x] 모듈 상수 추출 (URL/UA/sleep/retry/backoff/timeout/db_path/threshold/col_map)
- [x] 단위 테스트
  - [x] `_parse_int` 7 케이스 (+/-/콤마/빈/abc/None/+0) PASS
  - [x] `_is_valid_ticker` 8 케이스 (6자리/5자리/7자리/영문/빈/None/int/유효) PASS
  - [x] 실 호출 5/8: 4/5건 정상 (005930/086520/293490/068270), 무효 ticker graceful 누락
  - [x] compute_direction_match_rate 0 rows: rate=None / passes=False 정상
  - [x] 잘못된 인자 ValueError: indicator='wrong' / window_days=0
  - [x] 윈도우 검증: end 영업일/비영업일 모두 정확히 N영업일 윈도우
- [x] code-tester 검증 (심각 0건, 주의 3건 모두 fix)
  - [x] window off-by-one (end 포함 N영업일 정책 확정 + 코드 + docstring 갱신)
  - [x] OperationalError 전파 가능성 docstring 명시
  - [x] 네이버 표 컬럼 인덱스 의존 방어 정책 주석 추가
- [x] py_compile 통과

## Step 3 — 매일 19:27 매칭 잡 ✅ (2026-05-11 완료)
- [x] `closing_bet_system/main_orchestrator.py`에 `run_flow_reliability_check(for_date=None)` async 메서드 추가
  - [x] 영업일 보정 (run_label_yesterday 패턴 재사용)
  - [x] `_fetch_candidates_with_features(target_date)` 헬퍼 신규 (candidates LEFT JOIN candidate_features, ticker는 candidates에 있음)
  - [x] estimated 보유 ticker 필터로 네이버 호출 (불필요 호출 회피)
  - [x] `fetch_naver_confirmed_flow(date, tickers)` 호출 (asyncio.to_thread)
  - [x] `candidate_logger.log_flow_reliability(...)` 호출 (asyncio.to_thread, 기존 함수 재사용)
  - [x] for_date 인자로 수동 백필 지원
  - [x] **foreign_est 강제 None** — `foreign_net_buy_3d` (3일 누적, 원) vs 네이버 (당일, 주) 기간 불일치라 부호 비교 무의미. 후속 단위에서 재설계
  - [x] estimated 없는 candidate도 NULL,NULL로 저장 (재처리 IS NULL 쿼리 용이)
- [x] APScheduler 잡 등록: `cron(hour=19, minute=27, day_of_week='mon-fri', timezone="Asia/Seoul")`
  - [x] `FLOW_RELIABILITY_SCHEDULE_HOUR=19 / _MINUTE=27` 모듈 상수
  - [x] `register_jobs()` 잡 4건 등록으로 갱신 + docstring + 로그 메시지
  - [x] `replace_existing=True` (재배포 안전)
- [x] py_compile 통과
- [x] **수동 백필 검증 (5/8)**: 후보 19 / 추정치 보유 17 / 확정값 수집 17 / 저장 19 / 예외 0
  - inst 19/19 NULL (5/8 시점 inst=0 박제 — 5/12 봇 재시작 후 정상화)
  - foreign 19/19 NULL (강제 NULL, confirmed 값은 보존됨)
- [x] code-tester 검증 (심각 0건, 주의 2건 모두 fix)
  - [x] foreign 단위 불일치 → NULL 강제 + 후속 단위 분리 주석
  - [x] 카운터 의미 docstring 명시 (n_estimated vs n_logged)
- [ ] 5/12 19:27 첫 자동 실행 — 봇 재시작 후 자연 검증

## Step 4 — 신뢰도 집계 함수 ✅ (2026-05-11 완료, Step 2와 함께 구현)
- [x] `compute_direction_match_rate(window_days=7, indicator='inst', db_path, end_date)` 함수 구현
  - [x] indicator: 'inst' | 'foreign' 분기 (`_INDICATOR_COL_MAP` dict 매핑 — SQL injection 방어)
  - [x] N영업일 역산 (end 포함 N영업일 정책)
  - [x] flow_data_reliability GROUP BY 집계 (BETWEEN start AND end)
  - [x] `passes_70_threshold` 자동 판정 (`MATCH_RATE_THRESHOLD = 0.7` 상수)
- [x] **다중 시나리오 테스트 (2026-05-11)**:
  - [x] 1일 / 7일 / 14일 윈도우 (end=영업일/비영업일/None) 정확히 N영업일
  - [x] inst/foreign 모두 NULL이라 n_samples=0 정상 (5/12 봇 재시작 후 inst 정상값 누적 시 의미 있는 결과)
  - [x] 잘못된 db_path → OperationalError 정상 전파 (docstring 명시)
  - [x] indicator='wrong' / window_days=-1 → ValueError 정상
- [ ] (선택) 70%+ 시 텔레그램 알림 발송 헬퍼 — 별도 후속 단위 (Step 6 활성화 결정 시)

## Step 5 — 5/4~5/10 백필 ✅ (2026-05-11 완료)
- [x] 5/4, 5/7, 5/8 영업일 3개 대상 수동 백필 (run_flow_reliability_check for_date)
- [x] **inst/foreign 모두 NULL** — inst는 박제(0) + foreign는 단위 불일치 NULL 강제. 부호 매칭 데이터 없음
- [x] **confirmed 값 보존됨** — foreign_confirmed 52/55건 + inst_confirmed 52/55건 (5/12 후속 단위에서 foreign fix 시 재처리 가능)
- [x] 백필 결과:
  - 5/4: 후보 18 / estimated 18 / confirmed 18 / logged 18
  - 5/7: 후보 18 / estimated 17 / confirmed 17 / logged 18
  - 5/8: 후보 19 / estimated 17 / confirmed 17 / logged 19 (재실행 멱등성 검증)
- [x] flow_data_reliability 누적 55행 (5/12 19:27 잡 + 5/11 후보 23건 추가 시 78행 예상)

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
