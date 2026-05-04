# PLAN: KIND 시장경보 네이버 프로바이더 + universe_filters 사전 제외

## 목표
PRD 4-1 "관리/투자경고/거래정지 → 제외" 하드 룰을 실데이터로 활성화한다.
- `KindNaverProvider` 신규 구현 — 네이버 금융 3페이지 (investment_alert/trading_halt/management)에서 종목코드 + 경보 단계 추출
- `main_orchestrator`에서 KIND collect → universe_provider 호출 순서로 변경
- `universe_filters.apply_attribute_filters`에 `severity_map` 인자 추가 + severity ≥ 3 사전 제외

## 배경
- 현재 `KindAlertCollector(provider=None)` (main_orchestrator.py:209) → 항상 빈 dict 반환 → KIND severity 항상 0
- 결과: PRD 4-1 KIND 통합이 코드만 있고 실데이터 부재 (정합도 차이)
- KIND 공식 사이트는 SPA라 크롤링 어려움 → 네이버 금융 시장경보 페이지가 안정적 대체 (스윙 시스템에서 이미 네이버 패턴 사용 중)

## 핵심 설계 결정

### 1. 데이터 출처 (네이버 금융 3페이지)
| 단계 | URL | 추출 패턴 | 종목 수 |
|---|---|---|---|
| 투자주의/경고/위험 | `https://finance.naver.com/sise/investment_alert.naver` | `code=NNNNNN` + 단계 컬럼 (테이블 셀) | ~18 |
| 매매거래정지 | `https://finance.naver.com/sise/trading_halt.naver` | `code=NNNNNN` (모두 정지) | ~200 (페이지네이션 가능성) |
| 관리종목 | `https://finance.naver.com/sise/management.naver` | `code=NNNNNN` (모두 관리) | ~110 |

총 ~328 종목. universe 50~100 중 매칭은 자연 5~10건 예상.

### 2. severity 매핑 (`ALERT_LEVEL_TO_SEVERITY` 재사용)
```python
"투자주의": 1, "주의": 1
"투자경고": 2, "경고": 2
"투자위험": 3, "위험": 3
"매매거래정지": 3, "거래정지": 3, "정지": 3
"관리종목": 3  # PRD 4-1 명시 → 신규 추가 필요
```

### 3. 호출 순서 변경 (`main_orchestrator.run_daily_pipeline`)
**현재 (line 227, 284)**:
```
universe_provider() → 4 collectors 병렬 → kind_collector
```
**변경 후**:
```
kind_collector → universe_provider(severity_map) → 4 collectors 병렬
```
이유: PRD 4-1은 "candidates 진입 전 차단" 의도. universe 산출 단계에서 severity ≥ 3 종목 사전 제외 → 4 collectors가 그 종목 호출 안 함 (KIS API 호출 절감).

### 4. KindNaverProvider 신규 (kind_alert_collector.py 또는 별도 파일)
- 함수형: `def fetch_kind_alerts() -> dict[str, str]` — `{ticker: "관리종목"|"투자경고"|"매매거래정지"|...}`
- 3 페이지 순차 fetch (httpx 또는 urllib + EUC-KR 디코딩)
- 정규식 추출: `code=(\d{6}).*?>(?:[^<]+)`  + 단계 컬럼 별도 매칭
- 중복 시 가장 강한 단계 우선 (관리종목 + 투자위험 = 더 높은 severity)
- 예외/차단 시 빈 dict 반환 (CONTEXT.md "graceful 폴백" 패턴)
- 1회/일 호출 → 차단 위험 낮음 (네이버 finance 일반 패턴)

### 5. universe_filters.apply_attribute_filters 확장
```python
def apply_attribute_filters(
    tickers: list[str],
    today: datetime.date,
    config: dict,
    severity_map: Optional[dict[str, int]] = None,  # 신규 인자
) -> tuple[list[str], dict]:
    ...
    # severity ≥ 3 사전 제외 (속성 필터 첫 단계)
    if severity_map:
        for t in tickers:
            sev = severity_map.get(t, 0)
            if sev >= SEVERITY_EXCLUDE_THRESHOLD:  # 3
                rejected[t] = f"kind_severity_{sev}"
    ...
```

### 6. 호환성
- `kind_collector.collect()` 시그니처 유지 (KindAlertSnapshot 반환)
- `apply_attribute_filters`에 인자 추가 (default=None) → 기존 호출처 영향 없음
- main_orchestrator만 호출 순서 변경 + universe_provider에 severity 전달 인자 추가

## 구현 단계

### 단위 2-2b-1: KindNaverProvider 함수 구현 (반나절)
- `closing_bet_system/collectors/kind_naver_provider.py` 신규 (~250줄)
  - `fetch_kind_alerts() -> dict[str, str]` 메인 함수
  - `_fetch_management()` / `_fetch_alerts()` / `_fetch_halt()` 3 페이지별 헬퍼
  - `_parse_alert_levels()` — investment_alert는 단계 컬럼 별도 매칭 필요
  - `_pick_strongest_level()` — 한 종목 다중 단계 시 가장 강한 단계 우선
  - urllib + EUC-KR → UTF-8 디코딩
  - 모든 fetch 실패 시 빈 dict 폴백 (graceful)
- `kind_alert_collector.py`에 `"관리종목": 3` 매핑 추가 (PRD 4-1 정합)
- 단위 테스트 12+ 시나리오 (3 페이지 정상/실패/중복/EUC-KR/빈 응답/차단/단계 매핑)

### 단위 2-2b-2: 통합 (반나절)
- `main_orchestrator.run_daily_pipeline`:
  - KIND collect → universe_provider 호출 순서 변경
  - universe_provider에 severity_map 전달 (Callable 시그니처 변경 필요)
  - universe_provider_v2.get_universe_v2_filtered에 severity_map 인자 추가
- `universe_filters.apply_attribute_filters` / `apply_all_filters`에 severity_map 인자 추가
- KindAlertCollector 초기화 시 provider 주입 (lazy 또는 명시적)
- 단위 테스트 8+ 시나리오 (severity 사전 제외 / None 호환 / 호출 순서 / 인자 전달)

### 종합: 단발 검증 + code-tester
- python -m py_compile
- 통합 단위 테스트 (in-memory mock)
- code-tester 4~5 파일 검증
- systemd 재시작 → 첫 자연 트리거 (5/6) 관찰

## 변경 파일 목록

| 파일 | 변경 규모 | 단위 |
|---|---|---|
| `closing_bet_system/collectors/kind_naver_provider.py` | 중 (신규, ~250줄) | 2-2b-1 |
| `closing_bet_system/collectors/kind_alert_collector.py` | 소 ("관리종목": 3 매핑 추가) | 2-2b-1 |
| `closing_bet_system/main_orchestrator.py` | 중 (호출 순서 + provider 주입) | 2-2b-2 |
| `closing_bet_system/collectors/universe_filters.py` | 소 (severity_map 인자) | 2-2b-2 |
| `closing_bet_system/collectors/universe_provider_v2.py` | 소 (severity_map 인자 통과) | 2-2b-2 |
| `scripts/test_closing_bet_unit_2_2b_1.py` | 중 (신규, 12+ 시나리오) | 2-2b-1 |
| `scripts/test_closing_bet_unit_2_2b_2.py` | 중 (신규, 8+ 시나리오) | 2-2b-2 |
| `docs/improvements/change_log.md` | 1줄 추가 | 종합 |

## 롤백 계획
- KindNaverProvider 미주입 시 KindAlertCollector(provider=None)으로 자동 폴백 (현재 상태와 동일)
- universe_filters severity_map 인자 default=None → 기존 호출 영향 없음
- main_orchestrator 호출 순서 변경 1줄 revert로 즉시 복원

## 완료 기준

| 지표 | 목표 |
|---|---|
| KindNaverProvider fetch 성공 | 3 페이지 모두 200 응답 + 종목 추출 |
| 추출 종목 수 | 시장경보 ~18 + 거래정지 ~200 + 관리 ~110 |
| KIS API 호출 절감 | universe 100 중 KIND severity 3 종목 제외 → 5~10건 절감 (소소) |
| 단위 테스트 | 20+ 시나리오 PASS (12 + 8) |
| code-tester | 심각 0 |
| 호출 시간 | < 5초 (3 페이지 합산) |
| 첫 자연 트리거 (5/6) | KIND severity ≥ 3 사전 제외 로그 확인 |

## 후속 작업 (별도 단위)
- 단위 2-2c: KindHttpProvider (KIND 공식 사이트 직접 크롤링) — 네이버 차단 시 백업
- 호가 스프레드 1.5배 (PRD 4-4) — orderbook 20일 누적 후 (~5/29)
- 보호예수 D-7 (PRD 4-1) — SEIBro 데이터 출처 별도 조사
