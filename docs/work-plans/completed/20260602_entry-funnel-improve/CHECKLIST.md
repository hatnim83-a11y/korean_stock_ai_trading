# CHECKLIST — entry-funnel-improve

## 구현
- [x] config.py: MAX_GAP_UP_PERCENT 3.0 → 3.5 (description 갱신)
- [x] config.py: GAP_REGIME_PER_MARKET 토글 추가 (default False)
- [x] kis_api.py: get_current_price 결과에 `market` (rprs_mrkt_kor_name) 추가 + docstring 갱신
- [x] dynamic_gap.py: `classify_market(market_name)` 모듈 함수
- [x] dynamic_gap.py: `_classify_status()` / `_band_from_status()` 헬퍼 추출 (get_dynamic_gap/get_market_condition 재사용)
- [x] dynamic_gap.py: `get_dynamic_gap_for_market(market, condition)` 신규
- [x] morning_screener.py: _fetch_realtime_data에 stock["market"] 주입
- [x] morning_screener.py: Step 1 GAP_REGIME_PER_MARKET 분기 (per-market 2개 필터 버킷) + bool() 캐스팅
- [x] verifier.py: details_json에 `ai_sentiment` 추가

## 검증
- [x] py_compile 5파일 전체 통과 (수정 후 재컴파일 포함)
- [x] dynamic_gap.py __main__ 회귀: BULLISH 4.5/BEARISH 2.5/NEUTRAL 3.5 (새 base 3.5 반영, 로직 보존)
- [x] 6/2 시뮬: per-market ON → 삼성전자(+3.30<4.5)/현대건설(-2.16>-2.5) 통과, 올릭스(+4.63)/코스텍시스(+4.05) 차단
- [x] GAP_REGIME_PER_MARKET=False → check_multiple 기존 경로 그대로 (code-tester 회귀 확인)
- [x] classify_market 폴백: 빈/None/KONEX → kospi
- [x] code-tester 에이전트 실행 → 심각 0건, 주의 3건(2건 즉시 반영, 1건 범위외)

## 배포
- [x] git 커밋 (worktree-entry-funnel-improve)
- [ ] git merge → main (사용자 승인 후)
- [ ] .env GAP_REGIME_PER_MARKET=true 추가 여부 결정(사용자) — 미설정 시 갭상승 3.5만 적용
- [ ] sudo systemctl restart trading_system (사용자 승인 후)
- [x] **docs/improvements/change_log.md 1줄 추가** (before/after)

## 문서 업데이트
- [ ] memory/MEMORY.md 또는 project_gap_filter_review.md 갱신 (배포 확정 후)
- [ ] active/ → completed/20260602_entry-funnel-improve/ 아카이브 (배포 확정 후)
