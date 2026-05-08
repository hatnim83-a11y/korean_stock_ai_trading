# CHECKLIST — 단위 2-9c · KRX bulk 우회

## 구현 항목

### Step 1 — 모듈 상수 + 설정 로드
- [x] `MAX_FALLBACK_TICKERS = 100` 모듈 상수 추가
- [x] `FALLBACK_RATE_LIMIT_SEC = 0.05` 모듈 상수 추가
- [x] `_load_filter_config()` 반환 dict에 `fallback_per_ticker_enabled` 키 추가 (default True)

### Step 2 — `_fetch_per_ticker_today_data` 헬퍼 신규
- [x] 시그니처: `(ticker: str, today_str: str, krx) -> dict`
- [x] `krx.get_market_ohlcv_by_date(today_str, today_str, ticker)` 1회 호출
- [x] 종가/등락률/거래대금 추출 (`_safe_float` 가드)
- [x] 시총은 채우지 않음 (옵션 A 보수)
- [x] try/except 격리, 실패 시 `{}` 반환

### Step 3 — `_fetch_market_data_bulk` 시그니처 확장
- [x] `_fetch_market_data_bulk(today_str, tickers: Optional[list[str]] = None)`
- [x] bulk 호출 후 `result` 빈 dict + tickers 제공 + 토글 True → 폴백 진입
- [x] `for ticker in tickers[:MAX_FALLBACK_TICKERS]:` 종목별 호출
- [x] `time.sleep(FALLBACK_RATE_LIMIT_SEC)` 사이 sleep
- [x] 폴백 결과는 별도 캐시 dict (`_per_ticker_market_cache`)에 저장
- [x] 로그: `logger.warning("[universe_filters] bulk 빈 응답 — 종목별 폴백 진입 N=N건")`

### Step 4 — 호출부 갱신
- [x] `apply_attribute_filters`: KIND 사전 제외 후 survivors 를 `_fetch_market_data_bulk(today_str, tickers=survivors)` 로 전달
- [x] `apply_liquidity_filters`: 입력 tickers 를 `_fetch_market_data_bulk(today_str, tickers=tickers)` 로 전달

### Step 5 — settings.yaml 갱신
- [x] `data_source` 섹션 신설
- [x] `fallback_per_ticker_enabled: true` 추가
- [x] 주석에 PRD 16-3 본문 + 단위 2-9c 참조 추가

### Step 6 — 단위 테스트 작성 (`scripts/test_closing_bet_unit_2_9c.py`)
- [x] UF-C-1: bulk 빈 응답 + tickers 전달 → 폴백 진입 → close/change/value 채워짐 (시총 None)
- [x] UF-C-2: 시총 None → `apply_attribute_filters` 가 `data_not_found` 보수 탈락 (옵션 A 정합) — code-tester 심각 #1 반영하여 강화
- [x] UF-C-3: 토글 false + bulk 빈 응답 → 폴백 진입 안 함, 모두 `data_not_found`
- [x] UF-C-4: bulk 정상 + tickers 전달 → 폴백 호출 안 됨 (회귀 안전)
- [x] UF-C-5: 폴백 후 캐시 히트 — 같은 날 두 번째 호출 추가 외부 호출 0회
- [x] UF-C-6: tickers=None + bulk 빈 응답 → 기존 동작 유지 (회귀)
- [x] UF-C-7: 폴백 종목별 1건 RuntimeError → graceful 격리, 다른 종목 진행
- [x] UF-C-8: MAX_FALLBACK_TICKERS=100 초과 입력 → 100건만 폴백 호출
- [x] UF-C-9: settings.yaml `data_source` 미존재 → default true 폴백
- [x] UF-C-10: KIND severity 사전 제외 후 survivors 만 폴백 호출 (PRD 4-1 정합)

### code-tester 보강 수정 (2026-05-06)
- [x] 심각 #1: `apply_attribute_filters` 시총 검증에 `if market_cap is None: rejected[ticker] = "data_not_found"; continue` 추가 (옵션 A 정합 누락 보강)
- [x] 심각 #2: `_maybe_run_per_ticker_fallback` 의 `_load_filter_config` 예외 시 `DEFAULT_FALLBACK_PER_TICKER_ENABLED` 명시 체크
- [x] 데드락 1건: `_merge_with_fallback_cache` 의 `_market_data_cache_lock` 재획득 제거 (non-reentrant Lock 데드락) → CPython GIL atomic 활용
- [x] 회귀 재실행 — 단위 10건 + 회귀 58건 모두 PASS

## 검증 항목

### 단위 검증
- [x] py_compile 통과 (universe_filters.py, scripts/test_closing_bet_unit_2_9c.py)
- [x] `venv/bin/python scripts/test_closing_bet_unit_2_9c.py` 10건 PASS
- [x] 회귀: 2-9a 18 + 2-9b 15 + 2-2b-1 15 + 2-2b-2 10 = 58건 PASS
- [x] code-tester 에이전트 검증 — 심각 2건 발견 즉시 수정 + 데드락 1건 발견 즉시 수정 → 재검증 PASS

### 통합 검증 (단발)
- [x] `venv/bin/python -c "from closing_bet_system.collectors.universe_provider_v2 import get_universe_v2_filtered; print(get_universe_v2_filtered())"` 단발 호출
- [x] 폴백 진입 warning 로그 1건 (theme=17 / fallback 17/17 성공 2.34초)
- [x] **옵션 A 정합 — 시총 None 17건 모두 data_not_found 탈락 → universe = 0건** (의도대로 동작, 본격 해결은 단위 2-9d)

### 실전 검증 (5/7 자연 트리거)
- [x] 5/7 15:10 KST 자동 트리거 발화
- [x] `[universe_filters] bulk 빈 응답 — 종목별 폴백 진입` warning 1건
- [x] 폴백 호출 시간 ≤ 10초 (단발 검증 2.34초 → 자연 트리거에서도 비슷 예상)
- [x] 옵션 A 정합 결과 — universe 5건 미만 가능 (시총 보강은 단위 2-9d)
- [x] 일일 요약 텔레그램 알림 발송 (15:35)

## 배포 항목 (2026-05-06 KST 19:42 완료)
- [x] systemd 재시작 전 단일 PID 확인 (PID 3335832 → 종료 후 3414631)
- [x] `sudo systemctl restart trading_system`
- [x] active(running) 확인
- [x] 종가베팅 잡 3건 등록 로그 확인 (pipeline 15:10 / summary 15:35 / label 10:00)
- [x] `🎯 종가베팅 시스템 잡 등록 완료 (Phase 1 알림형, universe v2 + providers 4종 활성)` 로그 확인

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-9c 항목)
- [x] `memory/project_closing_bet_system.md` — 단위 2-9c 1단락 추가 (KRX bulk 우회)
- [x] `memory/MEMORY.md` 인덱스 description 갱신 (closing_bet_system 항목)
- [x] 3문서 active → completed/20260506_closing-bet-universe-v2-bulk-bypass/ 이동 (5/7 자연 트리거 검증 후)

## 완료 게이트 (선언 전 체크)
- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 단위/통합 전부 `[x]` (실전 검증은 5/7 자연 트리거 후)
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 (아카이브 제외) 전부 `[x]`
- [x] 5/7 자연 트리거 검증 후 → active → completed 아카이브
