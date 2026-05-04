# CHECKLIST: 종가베팅 universe v2

## 구현 항목

### 단위 2-9a: universe_provider_v2 — 다중 출처 통합
- [ ] `closing_bet_system/collectors/universe_provider_v2.py` 신규
  - [ ] `get_universe_v2() -> list[str]` — 4 출처 합집합
  - [ ] 출처 1 (Layer 3 테마): `_fetch_theme_codes()` — 기존 v1 `_fetch_top_themes` + `_crawl_theme_codes` 재사용
  - [ ] 출처 2 (Layer 3 모멘텀): `_fetch_top_value_codes()` — pykrx 거래대금 상위 30 (KOSPI+KOSDAQ)
  - [ ] 출처 3 (Layer 3 모멘텀): `_fetch_top_change_codes()` — pykrx 당일 등락률 상위 30
  - [ ] 출처 4 (Layer 1 수급): `_fetch_top_foreign_buy_codes()` — pykrx 외국인 순매수 상위 30
  - [ ] 합집합 + 6자리 검증 + 중복 제거 + 스윙 보유 제외
  - [ ] hard_cap (default 100)
  - [ ] in-memory 캐시 (같은 거래일 1회만 pykrx 호출)
  - [ ] 각 출처 try/except 격리 (1개 실패가 다른 출처 영향 없음)
  - [ ] pykrx import lazy
- [ ] py_compile 통과
- [ ] 단위 테스트 15+ 시나리오:
  - [ ] UV2-1: 4 출처 모두 정상 → 합집합 + 중복 제거
  - [ ] UV2-2: pykrx 1개 실패 → 다른 3개로 진행
  - [ ] UV2-3: pykrx 모두 실패 → 빈 리스트 폴백
  - [ ] UV2-4: 캐시 히트 (같은 거래일 두 번째 호출)
  - [ ] UV2-5: 스윙 테마 + pykrx 합산 (출처 다중성 검증)
  - [ ] UV2-6: 무효 종목코드 정규식 필터
  - [ ] UV2-7: 스윙 보유 제외
  - [ ] UV2-8: hard_cap 100 도달 시 잘라냄
  - [ ] UV2-9: 거래대금 상위 30 — KOSPI+KOSDAQ 합산
  - [ ] UV2-10: 외국인 순매수 — investor="외국인" 정확 매핑
  - [ ] UV2-11: 등락률 상위 30 — 음수(하락 종목)도 nlargest 영향 확인
  - [ ] UV2-12: pykrx 호출 시간 < 10초
  - [ ] UV2-13: 비-영업일 조회 → 빈 리스트
  - [ ] UV2-14: 인스턴스 lock 동시 호출 안전
  - [ ] UV2-15: 호출 결과 list[str] 타입 + 모두 6자리
- [ ] code-tester 검증

### 단위 2-9b: PRD 4-1 속성 필터 + 4-4 유동성 필터
- [ ] `closing_bet_system/collectors/universe_filters.py` 신규 (또는 v2에 통합)
  - [ ] `apply_attribute_filters(tickers) -> tuple[list[str], dict]` — 통과 종목 + 탈락 사유 dict
  - [ ] `apply_liquidity_filters(tickers) -> tuple[list[str], dict]` — 동일
  - [ ] settings.yaml `stock_filter` / `liquidity` 섹션 로드
  - [ ] 시총 < 500억 제외 (`get_market_cap_by_ticker`)
  - [ ] 주가 < 1000원 제외 (`get_market_ohlcv_by_ticker` 종가)
  - [ ] 상한가 제외 (등락률 >= +29.5%)
  - [ ] 52주 고점 -30% 초과 하락 제외 (1년치 OHLCV 또는 `get_market_ohlcv_by_date(today-365, today, ticker)`)
  - [ ] 20일 평균 거래대금 < 50억 제외 (`get_market_trading_value_by_date(today-30, today)`)
  - [ ] 당일 거래대금 < 100억 제외
  - [ ] 보호예수 D-7 / 호가 스프레드 비정상 → **후속 단위로 분리** (별도 데이터)
- [ ] `universe_provider_v2.get_universe_v2_filtered()` 통합 함수 추가
- [ ] py_compile 통과
- [ ] 단위 테스트 12+ 시나리오:
  - [ ] UF-1: 시총 500억 미만 제외
  - [ ] UF-2: 주가 1000원 미만 제외
  - [ ] UF-3: 상한가 제외 (+29.5% 이상)
  - [ ] UF-4: 52주 고점 -30% 초과 하락 제외
  - [ ] UF-5: 20일 평균 거래대금 < 50억 제외
  - [ ] UF-6: 당일 거래대금 < 100억 제외
  - [ ] UF-7: 모든 필터 통과 종목 → 빈 dict (rejection 없음)
  - [ ] UF-8: 한 종목이 여러 필터 위반 → 첫 위반만 기록
  - [ ] UF-9: pykrx 호출 실패 → 빈 결과 (graceful)
  - [ ] UF-10: 통합 함수 — universe_v2 + 필터 → 최종 list
  - [ ] UF-11: rejected 사유 dict 형식 검증 (`{ticker: reason}`)
  - [ ] UF-12: 정상 시 통과율 50~90% 분포
- [ ] code-tester 검증

## 검증 항목

### 단위 검증
- [ ] py_compile 2~3 파일 통과
- [ ] 단위 테스트 27+ 시나리오 PASS
- [ ] code-tester 심각 0건

### 통합 검증
- [ ] scheduler.py 교체 후 import 성공
- [ ] 단발 트리거 (providers 주입) — universe 30~80 산출 확인
- [ ] 필터 통과율 60~80% 분포 확인 (너무 강하면 universe 0 위험)
- [ ] candidate_features INSERT 정상 (기존 흐름 영향 없음)
- [ ] orderbook_snapshots INSERT 정상

### 실전 검증 (배포 후 1일)
- [ ] 15:10 잡 트리거 시 universe v2 산출 + 4 출처 로그 확인
- [ ] pykrx 호출 시간 모니터링 (< 10초)
- [ ] candidates 테이블 일일 30~80건 누적 확인
- [ ] 운영 점검 게이트 진척도 가속 (1주 이내 30건 도달 가능)

## 배포 항목
- [ ] systemd 재시작 전 선행 체크
- [ ] 장 마감 후 또는 장 시작 전 권장
- [ ] `sudo systemctl restart trading_system`
- [ ] active(running) 확인
- [ ] 종가베팅 잡 3건 + universe v2 활성 로그 확인
- [ ] 첫 15:10 잡 트리거 결과 관찰 (universe 종목 수)

## 문서 업데이트 항목
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] `memory/project_closing_bet_system.md` 갱신 — universe v2 적용
- [ ] `memory/MEMORY.md` 인덱스 갱신
- [ ] 3문서 active → completed/YYYYMMDD_closing-bet-universe-v2/ 이동

## 완료 게이트 (선언 전 체크)
- [ ] 구현 항목 전부 `[x]` (단위 2-9a, 2-9b)
- [ ] 검증 항목 전부 `[x]`
- [ ] 배포 항목 전부 `[x]`
- [ ] 문서 업데이트 항목 전부 `[x]`

## 새 대화 시작 가이드 (CLAUDE.md 권장)
이번 대화는 이미 8단위 작업 + 2회 배포 처리 → 컨텍스트 큼.
**새 대화에서 `/resume` 호출** → 이 3문서 자동 로드 → 단위 2-9a 시작.
