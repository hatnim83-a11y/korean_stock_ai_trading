# PLAN — 단위 2-9c · 종가베팅 universe v2 KRX bulk 우회 (작업 G)

## 목표
2026-05-06 15:10 KST 자연 트리거에서 종가베팅 universe v2 = 0건/파이프라인 스킵 발생. KRX 사이트 정책 변경으로 pykrx의 시장 단위 bulk 호출(`get_market_*_by_ticker`)이 빈 응답을 반환하는 것이 본질 원인. 본 단위는 `universe_filters.py` 만 수정해 시장 단위 bulk 빈 응답 시 종목별 `get_market_ohlcv_by_date`로 폴백시켜 출처 1(theme) 17~25건이 필터를 통과하도록 한다. 5/7(목) 15:10 자연 트리거에서 정상 가동 복귀가 완료 기준.

## 배경
- pykrx 1.2.3 `get_market_*_by_ticker` → 빈 DataFrame, KeyError(시가/고가/저가/종가 컬럼 없음) 발생.
- pykrx 1.2.8 → 동일 + `KRX_ID/KRX_PW` 인증 요구 (운영 부담 큼).
- 종목별 `get_market_ohlcv_by_date(ticker)` 는 정상 (5/4 005930 232,500원 검증).
- 1.2.3 → 1.2.8 → 1.2.3 롤백 완료, scipy 충돌 회복.
- KIS Open API ranking 본격 대체는 별도 단위 2-9d (새 대화).

## 사용자 결정 사항
- 시총 폴백: **옵션 A 보수적 처리** (종목별 시총 추가 호출 안 함, market_cap=None → `data_not_found`)
- 작업 범위: 작업 G만 이번 대화 (F-2는 새 대화)
- 토글 위치: `settings.yaml` `data_source` 섹션 신설

## 구현 단계

### Step 1 — 모듈 상수 + 설정 로드
- `MAX_FALLBACK_TICKERS = 100` (hard_cap 정합)
- `FALLBACK_RATE_LIMIT_SEC = 0.05` (KRX 차단 방어)
- `_load_filter_config()` 가 `data_source.fallback_per_ticker_enabled` 반환
- 폴백 default `True`

### Step 2 — `_fetch_per_ticker_today_data` 헬퍼 추가
- 시그니처: `(ticker, today_str, krx) -> dict`
- 반환: `{close, change_rate, trading_value}` (시총은 None 처리)
- try/except 격리, 실패 시 `{}` 반환

### Step 3 — `_fetch_market_data_bulk` 시그니처 확장
- `_fetch_market_data_bulk(today_str, tickers=None)`
- bulk 정상 → 기존 동작
- bulk 빈 dict + tickers 제공 + 토글 ON → 종목별 폴백
- 별도 캐시 dict (`_per_ticker_market_cache`)로 격리

### Step 4 — 호출부 갱신
- `apply_attribute_filters` (라인 360) → KIND 사전 제외 후 survivors 전달
- `apply_liquidity_filters` (라인 429) → 입력 tickers 그대로 전달

### Step 5 — settings.yaml 갱신
- `data_source.fallback_per_ticker_enabled: true` 신설

### Step 6 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9c.py`)
10 시나리오: UF-C-1 ~ UF-C-10 (CHECKLIST 참조)

### Step 7 — 회귀 + code-tester + 단발 통합 검증

### Step 8 — 배포 + 문서 갱신

## 변경 파일
1. `closing_bet_system/collectors/universe_filters.py` (수정)
2. `closing_bet_system/config/settings.yaml` (data_source 섹션 추가)
3. `scripts/test_closing_bet_unit_2_9c.py` (신규)
4. `docs/improvements/change_log.md` (1줄)
5. `memory/MEMORY.md` (인덱스 + 신규 메모 1건)
6. 본 디렉토리 3문서 (PLAN/CONTEXT/CHECKLIST)

## 롤백 계획
1. `settings.yaml.data_source.fallback_per_ticker_enabled: false`
2. `sudo systemctl restart trading_system`
3. 5/6 자연 트리거 동작 복원 (universe v2 = 0건)

## 완료 기준
- 단위 테스트 10건 PASS
- 회귀 33+건 PASS (2-9a 18 + 2-9b 15 + 2-2b-1 15 + 2-2b-2 10)
- code-tester 심각 0건
- 5/7 15:10 자연 트리거 → universe ≥ 5건 + candidates INSERT ≥ 1건 + 일일 요약 알림 발송
- 폴백 호출 시간 ≤ 10초

## 새 대화 권장
- **단위 2-9d (작업 F-2)**: KIS Open API ranking 대체. 본 단위 완료 후 새 대화 진행.
