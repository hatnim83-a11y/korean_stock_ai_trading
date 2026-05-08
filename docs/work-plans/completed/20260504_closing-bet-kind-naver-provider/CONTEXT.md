# CONTEXT: KIND 시장경보 네이버 프로바이더

## 변경 이유
- PRD 정합도 분석 (2026-05-04 세션) — "관리/투자경고/거래정지 → 제외" 하드 룰 ⚠️ 부분 구현 판정
- 원인: `KindAlertCollector(provider=None)` 상태 (`main_orchestrator.py:209`) → 항상 빈 dict 반환 → KIND severity 항상 0 → OvernightRiskFilter도 KIND 영향 없음
- 사용자 결정 (2026-05-04): "지금 바로 1번 진행하자"

## 영향 범위
- KIS API 호출: universe 산출 단계에서 severity ≥ 3 종목 사전 제외 → 4 collectors 호출 감소 (소소, 5~10건)
- DB 변경 없음 (candidates 스키마 동일)
- 외부 의존: 네이버 금융 3페이지 (1회/일 호출, 차단 위험 낮음)
- universe_filters / universe_provider_v2 인자 추가 (default=None → 기존 호출 영향 없음)

## 현재 코드 상태

### KindAlertCollector (이미 구현됨, Phase 2-2)
- 위치: `closing_bet_system/collectors/kind_alert_collector.py`
- 핵심 dataclass: `KindAlertSnapshot(alerts: dict, severity_map: dict)`
- 핵심 함수: `KindAlertCollector(provider=None).collect() -> KindAlertSnapshot`
- provider 시그니처: `Callable[[], dict[str, str]]` — `{ticker: 한글경보단계명}`
- `ALERT_LEVEL_TO_SEVERITY` 정의:
  ```python
  "투자주의": 1, "주의": 1
  "투자경고": 2, "경고": 2
  "투자위험": 3, "위험": 3
  "매매거래정지": 3, "거래정지": 3, "정지": 3
  # "관리종목": 3 ← PRD 4-1 정합 위해 신규 추가 필요
  ```

### main_orchestrator (Phase 2-2 통합 완료)
- 위치: `closing_bet_system/main_orchestrator.py`
- 현재 호출 순서 (line 227, 282-287):
  ```
  1. universe_provider() → tickers 산출
  2. flow/pv/dart/orderbook 4 collectors 병렬 실행
  3. kind_collector.collect() → severity_map (universe와 무관)
  4. _process_ticker(kind_snapshot=...) — 종목별 OvernightRiskFilter에서 severity 활용
  ```
- 변경 필요: KIND를 universe_provider 전에 호출 + severity_map을 universe에 전달

### universe_filters (Phase 2-9b 완료)
- 위치: `closing_bet_system/collectors/universe_filters.py`
- 핵심 함수:
  - `apply_attribute_filters(tickers, today, config) -> tuple[list, dict]`
  - `apply_liquidity_filters(tickers, today, config) -> tuple[list, dict]`
  - `apply_all_filters(tickers, today, config) -> tuple[list, dict]` 통합
- first-rejection-only: 한 종목 여러 위반 시 첫 위반만 기록
- 추가할 인자: `severity_map: Optional[dict[str, int]] = None`

### universe_provider_v2 (Phase 2-9a 완료)
- 위치: `closing_bet_system/collectors/universe_provider_v2.py`
- 메인 함수: `get_universe_v2_filtered() -> list[str]`
- 추가할 인자: `severity_map: Optional[dict[str, int]] = None`
- scheduler.py 호출 시그니처도 (선택) 검토

## 네이버 금융 페이지 구조 (사전 조사 완료)

### 1. 시장경보 페이지 (3단계 통합)
- URL: `https://finance.naver.com/sise/investment_alert.naver`
- 추출: 18 종목 (투자주의 7 + 투자경고 7 + 투자위험 4)
- 인코딩: EUC-KR → UTF-8 변환 필수
- 패턴: `code=NNNNNN" class="tltle">종목명` + 단계는 별도 컬럼

### 2. 거래정지 페이지
- URL: `https://finance.naver.com/sise/trading_halt.naver`
- 추출: 200 종목 (페이지네이션 가능)
- 단계: 모두 "매매거래정지" (severity=3)

### 3. 관리종목 페이지
- URL: `https://finance.naver.com/sise/management.naver`
- 추출: 110 종목
- 단계: 모두 "관리종목" (severity=3 — PRD 4-1 명시)

### 추출 패턴 (검증됨)
```bash
grep -oE 'code=[0-9]{6}[^>]*>[^<]+' page.html
# 결과: code=017040" class="tltle">광명전기
```

### 인코딩 (검증됨)
```bash
curl ... | iconv -f EUC-KR -t UTF-8
```

## 핵심 코드 스니펫

### KindNaverProvider 초안 (단위 2-2b-1)
```python
# closing_bet_system/collectors/kind_naver_provider.py
import re
import urllib.request
from typing import Optional

NAVER_BASE = "https://finance.naver.com"
URLS = {
    "alert": f"{NAVER_BASE}/sise/investment_alert.naver",
    "halt":  f"{NAVER_BASE}/sise/trading_halt.naver",
    "manage": f"{NAVER_BASE}/sise/management.naver",
}

USER_AGENT = "Mozilla/5.0 (Linux x86_64) AppleWebKit/537.36"
TIMEOUT = 5  # 초

# severity 강도 (높을수록 강한 단계 — 중복 시 우선 적용)
LEVEL_PRIORITY = {
    "관리종목": 5,
    "매매거래정지": 4,
    "투자위험": 3,
    "투자경고": 2,
    "투자주의": 1,
}

def fetch_kind_alerts() -> dict[str, str]:
    """3 페이지 fetch + 종목별 가장 강한 단계만 반환."""
    out: dict[str, str] = {}
    for level_name, key in (("관리종목", "manage"), ("매매거래정지", "halt"), ("alert", "alert")):
        # alert 페이지는 단계 컬럼 별도 매칭, 나머지는 단일 단계
        ...
    return out
```

### main_orchestrator 호출 순서 변경 (단위 2-2b-2)
```python
# 변경 후 (run_daily_pipeline)
# 1. KIND collect (universe와 무관)
try:
    kind_snapshot = await asyncio.to_thread(self.kind_collector.collect)
except Exception as e:
    logger.error(f"[orchestrator] kind_collector 예외 (빈 폴백 진행): {e}")
    kind_snapshot = None

severity_map = kind_snapshot.severity_map if kind_snapshot else {}

# 2. universe_provider (severity_map 전달)
tickers = list(self._safe_call(
    lambda: self._universe_provider(severity_map=severity_map), default=[]
))

# 3. 4 collectors 병렬 (severity ≥ 3 종목은 이미 universe에서 제외됨)
flow_snaps_task = asyncio.to_thread(...)
```

### universe_filters severity_map 통합
```python
def apply_attribute_filters(
    tickers: list[str],
    today,
    config: dict,
    severity_map: Optional[dict[str, int]] = None,
) -> tuple[list[str], dict]:
    rejected: dict = {}
    survivors: list = []
    SEVERITY_EXCLUDE_THRESHOLD = 3

    # KIND severity 사전 제외 (PRD 4-1 정합)
    if severity_map:
        filtered_tickers = []
        for t in tickers:
            sev = severity_map.get(t, 0)
            if sev >= SEVERITY_EXCLUDE_THRESHOLD:
                rejected[t] = f"kind_severity_{sev}"
            else:
                filtered_tickers.append(t)
        tickers = filtered_tickers

    # 기존 시총/주가/상한가/52주 고점 필터
    ...
```

## 과거 버그/주의사항

- **EUC-KR 인코딩**: 네이버 finance는 EUC-KR. requests/httpx 사용 시 `r.encoding = 'euc-kr'` 또는 `r.content.decode('euc-kr')` 명시 필수
- **종목코드 검증**: `re.match(r'^\d{6}$')` 패턴 일관 (CONTEXT.md universe v2와 동일)
- **단계 우선순위 매핑**: 한 종목이 여러 페이지에 나올 수 있음 (관리종목 + 투자위험 동시) → 가장 강한 단계 우선
- **차단 위험**: 1회/일 호출 → 낮음. 다만 User-Agent 설정 + 5초 timeout 필수
- **알림 시간 영향 없음**: 현재 알림 임계값 7점 미달이라 0건 유지가 정상

## 호환성

- `KindAlertCollector(provider=None)` → `provider=fetch_kind_alerts` 주입만으로 활성
- `apply_attribute_filters` severity_map 인자 default=None → 기존 호출처 영향 없음
- `universe_provider_v2.get_universe_v2_filtered` severity_map 인자 default=None → scheduler.py 호출 영향 없음
- 단위 테스트 별도 파일 (`test_closing_bet_unit_2_2b_1.py`, `_2_2b_2.py`)

## 호출 그래프 (배포 후)

```
APScheduler 15:10 KST
  → MainOrchestrator.run_daily_pipeline()
    → kind_collector.collect()  ← 신규 위치 (universe 전)
        → fetch_kind_alerts() — 네이버 3 페이지 fetch
        → KindAlertSnapshot(severity_map={ticker: 1~3})
    → universe_provider(severity_map=severity_map)
        → get_universe_v2_filtered(severity_map=...)
            → 4 출처 합집합 (변경 없음)
            → universe_filters.apply_all_filters(severity_map=...)
                → severity ≥ 3 사전 제외 (신규)
                → 시총/주가/상한가/52주 고점 (기존)
                → 20일/당일 거래대금 (기존)
            → list[ticker] 반환 (severity 3 제외 + 4-1/4-4 통과)
    → 4 collectors 병렬 (universe ⊂ severity < 3)
    → 종목별 점수 + DB + 알림
```

## 검증 시 주의

- 네이버 fetch 실패 시 graceful 폴백 (빈 dict) → universe 산출에 영향 없음 (현재와 동일)
- severity_map 없는 경우 (provider=None) → 기존 동작 유지 (universe 변화 없음)
- 첫 자연 트리거 (5/6 15:10) 로그에서 KIND fetch 시간 + severity 매칭 종목 수 확인
- KIND severity 사전 제외로 universe 종목 수 5~10건 감소 정상 (단, universe 0이 되면 출처 다수 실패 의심)

## 단위 테스트 시나리오 초안

### 단위 2-2b-1 (KindNaverProvider, 12+ 시나리오)
- KN-1: 3 페이지 모두 정상 → 통합 dict 반환
- KN-2: 1개 페이지 실패 → 나머지 2개로 진행 (graceful)
- KN-3: 모두 실패 → 빈 dict
- KN-4: EUC-KR 디코딩 정상
- KN-5: 한 종목이 관리 + 투자위험 동시 → 관리종목 우선 (LEVEL_PRIORITY)
- KN-6: 6자리 검증 (영문/길이/None 제외)
- KN-7: investment_alert 단계 컬럼 매칭 (3단계 분리)
- KN-8: trading_halt 모두 "매매거래정지"
- KN-9: management 모두 "관리종목"
- KN-10: timeout 5초 회귀
- KN-11: User-Agent 설정 검증
- KN-12: 빈 응답 (HTML 0줄) → 빈 dict

### 단위 2-2b-2 (통합, 8+ 시나리오)
- KI-1: severity_map 주입 → severity ≥ 3 사전 제외
- KI-2: severity_map=None → 기존 동작 유지 (회귀)
- KI-3: severity_map={} → 사전 제외 0건
- KI-4: severity 1, 2 → 통과 (3 미만)
- KI-5: severity 3 + 시총 미달 → kind 사유로 first-rejection
- KI-6: main_orchestrator 호출 순서 (KIND → universe)
- KI-7: KIND collect 실패 → severity_map={} 폴백 → universe 정상 산출
- KI-8: scheduler.py import 경로 회귀

## 현재 시스템 상태 (KST 20:35 시점)
- `trading_system` PID 2695913 (universe v2 활성, KIND provider 미주입 상태)
- 본 작업 완료 후 systemd 재시작 → 5/6 15:10 자연 트리거 첫 검증
- 컨텍스트 사이즈 적정. 단위 2-2b-1 / 2-2b-2 / 종합 검증을 같은 세션에서 처리 가능 예상
