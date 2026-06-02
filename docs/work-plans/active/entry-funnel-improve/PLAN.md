# PLAN — 진입 깔때기 완화 (entry-funnel-improve)

## 목표
최근 매수 부진(6/2 매수 0건, 슬롯 5개 비었음)의 진입 깔때기 이중 병목(AI Hold 게이트 + 시초가 갭 필터)을 근거 기반으로 완화. 좋은 종목을 부당하게 버리는 케이스를 줄이되, 손실 밴드는 유지.

## 근거 문서
`docs/improvements/2026-06-02-focus-entry_funnel.md` (trade-improvement-analyst 제안서)

## 구현 단계 (확정 범위 — 사용자 승인 2026-06-02)
1. **② 종목 소속시장별 갭 regime** (신뢰도 Medium, 최우선)
   - 분열장(코스피 강세/코스닥 약세 등)에서 갭 밴드를 두 지수 평균 대신 **종목 소속시장 지수**로 판정
   - 토글 `GAP_REGIME_PER_MARKET` (default **False**, 안전)
2. **①a 갭상승 상한 3.0 → 3.5%** (밴드 A 과차단 회수, 3.5%+ 위험밴드 유지)
3. **① AI 점수 details_json 보강** — score는 이미 `score` 컬럼 저장됨. details_json에 `ai_sentiment` 명시 추가(분석가가 못 찾은 근본원인 보완). **Hold 완화 로직은 도입 안 함**(데이터가 반대).

## 범위 제외 (별도/보류)
- ③ 갭→진입가 하향/눌림목 대기 전환 → **별도 설계 플랜**
- ①b 갭하락 2.0→2.5 → 표본 1건, 관찰 우선
- AI Hold 완화 본안 → 보류(Hold 종목 사후 대체로 하락)

## 변경 파일
- `config.py` — MAX_GAP_UP_PERCENT 3.0→3.5, GAP_REGIME_PER_MARKET 신규
- `modules/stock_screener/kis_api.py` — get_current_price 결과에 `market`(rprs_mrkt_kor_name) 추가
- `modules/morning_filter/dynamic_gap.py` — `classify_market()`, `get_dynamic_gap_for_market()`, 헬퍼 추출
- `modules/morning_filter/morning_screener.py` — _fetch_realtime_data에 market 주입 + Step 1 per-market 분기
- `modules/ai_verifier/verifier.py` — details_json에 ai_sentiment 추가

## 롤백 계획
- ②: `GAP_REGIME_PER_MARKET=False` + restart (기본값이라 미설정 시 기존 동작)
- ①a: `MAX_GAP_UP_PERCENT=3.0` 원복 + restart
- ① 로깅: details_json 필드 추가뿐 — 부작용 없음, 원복 불필요

## 완료 기준
- py_compile + dynamic_gap/gap_filter __main__ 테스트 통과
- code-tester 심각/주의 이슈 없음
- GAP_REGIME_PER_MARKET=False일 때 기존 동작 100% 동일(회귀 없음) 검증
- 6/2 데이터 시뮬: per-market ON 시 삼성전자/현대건설 통과, 올릭스/코스텍시스 계속 차단 확인
- change_log.md 1줄 추가
