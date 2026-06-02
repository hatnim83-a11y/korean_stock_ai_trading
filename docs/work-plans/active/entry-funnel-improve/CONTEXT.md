# CONTEXT — entry-funnel-improve

## 왜 이 작업인가
- 6/2 매수 0건. 보유 0/슬롯 5인데도 매수 실패. 깔때기: filter 21/41 → ai_verify 4/12 → **gap_filter 0/4 전멸**.
- 갭 탈락 4종목: 삼성전자(KOSPI)+3.30, 올릭스(KOSDAQ)+4.63, 코스텍시스(KOSDAQ)+4.05, 현대건설(KOSPI)-2.16.
- 시장 분열: KOSPI +1.44% / KOSDAQ -1.64% → avg -0.10% → neutral → 밴드 ±3/-2 그대로. 코스피 강세 혜택을 코스피 종목이 못 받음.

## 현재 코드 상태 (핵심 스니펫)
### dynamic_gap.py
- `get_market_condition()` line 134: `avg_change = (kospi_change + kosdaq_change)/2` → 단일 regime
- MarketCondition은 kospi_change, kosdaq_change를 **개별 보유** (이미 있음 → per-market 가능)
- `get_dynamic_gap()` line 167~213: status→rule→band. high vol +0.5
- rules: bullish gap_up +1.0/down +0.5, bearish -1.0/-0.5, neutral 0

### gap_filter.py
- `GapFilter(max_gap_up, max_gap_down)`, `.check()` per stock, `.check_multiple()` 일괄
- 현재 morning_screener는 단일 밴드 GapFilter 1개로 전체 체크

### morning_screener.py Step 1 (line 237~262)
- `gap_config = dynamic_gap_calculator.get_dynamic_gap()` → `GapFilter(...)` → `check_multiple`
- `_fetch_realtime_data` (line 150~): `get_current_price` 호출 → stock["open_price"]/["prev_close"] 세팅. **market 미주입**

### kis_api.py get_current_price (line ~484)
- result dict: code/name/price/change/prev_close/change_rate/volume/.../market_cap
- KIS inquire-price output에 `rprs_mrkt_kor_name`(대표시장한글명) 존재하나 **미파싱**

### verifier.py _save_ai_verify_logs (line 481~499)
- `score`: sentiment(AI점수) **이미 저장됨** (분석가 오류 — details_json만 봄)
- details_json: recommend/confidence/target_return만 → **ai_sentiment 누락**이 분석가 혼선 원인

## 실데이터 검증 (screening_log ai_verify)
- Yes 통과: score 7.0~8.5, confidence 0.65~0.82, target 8~15
- Hold 탈락: score 4.5~6.5, confidence 0.5~0.65, target 3~5
- 6.5 Hold 다수(DL이앤씨/대우건설/디아이티/DB하이텍/테스/삼성물산/SK/효성중공업/DB/아바텍...) → "score≥6.5 허용" 시 미온적 종목 대량 매수. Hold 종목 사후 대체로 하락 → **Hold 완화 보류가 정당**.

## 영향 범위
- morning_screener Step 1만 변경(매수 직전 게이트). 매도/모니터링 무관.
- GAP_REGIME_PER_MARKET=False 기본 → 미설정 운영기엔 동작 불변(회귀 0).
- market 분류 실패(빈 문자열) 시 kospi 밴드로 폴백 + debug 로그.

## 과거 관련 버그/교훈
- project_gap_filter_review.md: "일괄 +5% 완화" 기각(밴드 B 평균 -0.9%, 올릭스 -13.7%). 단계적 3.0→3.5만.
- 하드코딩 금지(modules/CLAUDE.md) → 밴드/토글 config 상수화.
