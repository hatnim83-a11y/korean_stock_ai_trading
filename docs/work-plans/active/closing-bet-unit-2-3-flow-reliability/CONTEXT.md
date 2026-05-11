# CONTEXT — 단위 2-3 flow_reliability_tracker (옵션 H 확정 재설계)

## ✅ Step 0 조사 결과 종합 (2026-05-11)

### 0a — pykrx 종목별 함수 (❌ 전체 마비)
```python
from pykrx import stock as krx
# 모든 빈 응답 또는 KeyError
krx.get_market_trading_value_by_date('20260504', '20260509', '005930')  # shape=(0,0)
krx.get_market_trading_volume_by_date('20260504', '20260509', '005930')  # shape=(0,0)
krx.get_market_cap_by_date('20260504', '20260509', '005930')             # shape=(0,0)
krx.get_market_fundamental_by_date('20260504', '20260509', '005930')     # shape=(0,0)
krx.get_market_trading_value_by_investor('20260504', '20260509', '005930')  # KeyError '거래대금'
krx.get_market_trading_value_and_volume_by_ticker('20260509', '20260509', 'KOSPI', '기관합계')  # shape=(0,0) (deprecated)
# 작동: get_market_ohlcv_by_date(ticker) 만 정상 — universe_v2가 이미 사용 중
```
**결론**: pykrx 전 endpoint 마비 (단위 2-9c bulk 빈 응답과 동일). pykrx 의존 폐기.

### 0b — KRX 직접 크롤링 (⚠️ KRX 차단 + ✅ 네이버 대안)

**KRX endpoint LOGOUT 차단** (세션 인증 요구):
- pykrx 내부 코드에서 BLD `MDCSTAT02303` (개별종목 일별추이 상세) 등 후보 식별
- `https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd` 직접 호출 시 HTTP 400 + body `LOGOUT`
- 세션 쿠키 + Referer 추가해도 동일

**네이버 `frgn.naver` 정상 작동 ✅**:
```python
import requests
from bs4 import BeautifulSoup
r = requests.get("https://finance.naver.com/item/frgn.naver",
                 params={"code": "005930"},
                 headers={"User-Agent": "Mozilla/5.0 ..."}, timeout=15)
r.encoding = 'euc-kr'
soup = BeautifulSoup(r.text, 'html.parser')
tables = soup.find_all('table', class_='type2')
rows = tables[1].find_all('tr')[2:]  # 헤더 2행 제외
# row.find_all('td'):
#   [0]=날짜 '2026.05.08' / [1]=종가 / [5]=기관 '+1,074,644' / [6]=외국인 '-9,595,937'
```

**검증**:
| 종목 (시장) | 5/8 데이터 | 5/11 데이터 | 갱신 시점 |
|---|---|---|---|
| 005930 (KOSPI) | 기관 +1,074,644 / 외인 -9,595,937 | 미표시 (17:37 KST) | 18~19시 추정 |
| 086520 (KOSDAQ) | 기관 -80,579 / 외인 -181,872 | — | — |
| 293490 (KOSDAQ) | 기관 +95,580 / 외인 -62,495 | — | — |
| 068270 (KOSPI) | 기관 +35,047 / 외인 -157,490 | — | — |

- KOSPI/KOSDAQ 모두 단일 endpoint
- 단위: **수량(주)** — 추정치(원) 비교 시 부호만 비교 또는 종가 곱셈 환산
- 매일 18~19시 갱신 → 19:27 매칭 잡 시점에 사용 가능

### 0d — KIS 다른 TR (✅ HHPTJ04160200 결정적 해결)

**KIS Open API 공식 sample (`koreainvestment/open-trading-api`)**:
- `examples_llm/domestic_stock/investor_trend_estimate/`
- TR ID: **`HHPTJ04160200`**
- API URL: `/uapi/domestic-stock/v1/quotations/investor-trend-estimate`
- **공식 docstring**:
  > 증권사 직원이 장중에 집계/입력한 자료를 단순 누계한 수치로서,
  > 입력시간은 외국인 09:30, 11:20, 13:20, 14:30 / 기관종합 10:00, 11:20, 13:20, 14:30 이며, 사정에 따라 변동될 수 있습니다.

**실제 호출 (5/11 17:42 KST 검증)**:
```python
url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
headers = {
    "content-type": "application/json; charset=utf-8",
    "authorization": f"Bearer {token}",
    "appkey": settings.KIS_APP_KEY,
    "appsecret": settings.KIS_APP_SECRET,
    "tr_id": "HHPTJ04160200",
}
params = {"MKSC_SHRN_ISCD": "005930"}
r = requests.get(url, headers=headers, params=params, timeout=15)
# rt_cd='0' / output2 = 5 rows
```

**응답 구조** (5/11 005930 삼성전자):
```
{'bsop_hour_gb': '5', 'frgn_fake_ntby_qty': '-00000000002906000', 'orgn_fake_ntby_qty': '000000000001442000', 'sum_fake_ntby_qty': '-00000000001464000'}
{'bsop_hour_gb': '4', 'frgn_fake_ntby_qty': '-00000000002407000', 'orgn_fake_ntby_qty': '000000000001381000', 'sum_fake_ntby_qty': '-00000000001026000'}
{'bsop_hour_gb': '3', 'frgn_fake_ntby_qty': '-00000000002157000', 'orgn_fake_ntby_qty': '000000000000691000', 'sum_fake_ntby_qty': '-00000000001466000'}
{'bsop_hour_gb': '2', 'frgn_fake_ntby_qty': '-00000000001481000', 'orgn_fake_ntby_qty': '000000000000566000', 'sum_fake_ntby_qty': '-00000000000915000'}
{'bsop_hour_gb': '1', 'frgn_fake_ntby_qty': '-00000000001135000', 'orgn_fake_ntby_qty': '000000000000000000', 'sum_fake_ntby_qty': '-00000000001135000'}
```

**핵심**:
- `bsop_hour_gb` 1~5 = 입력 차수 (1=09:30 외인 첫 입력, 2=10:00 기관 추가, 3=11:20, 4=13:20, **5=14:30 마지막**)
- 5 = 14:30까지 누계 (마지막 입력 차수)
- 모든 수량은 **18자리 zero-padded 부호 문자열** → `int()` 캐스팅 필요
- KOSPI(005930) / KOSDAQ(086520) 모두 정상 응답
- **15:10 daily_pipeline 시점에 14:30 가집계 사용 가능 ✅**

---

## 변경 이유

종가베팅 시스템 Phase 1 (알림형) 가동 중 점수 산식 분석:
- Layer 1 (수급, 4점) 가중치 **0.0** — KIS 추정치 정확도 미검증으로 의도적 보수
- 100건 게이트 도달 시점(5/14~15)에 Layer 1 활성화 결정 필요
- 활성화 결정 근거 = "추정치 vs 확정값 방향 일치율 70%+ 검증"
- 검증 인프라(flow_reliability_tracker) 미구현 + **추정치 수집 자체가 inst=0 박제** (FHKST01010900 정책 한계)

본 단위 (옵션 H):
1. inst 추정치 데이터 소스 교체 (FHKST01010900 → HHPTJ04160200) → inst 정상값 수집 복원
2. 사후 확정값 수집기(네이버) + 매칭 잡 → 7~10일 누적 후 활성화 결정 가능

## 현재 코드 상태 (수정 전)

### 1. Layer 1 점수 산정 (이미 구현)
**위치**: `closing_bet_system/engines/signal_score_engine.py:284-292`
```python
cond_inst = l1.get("inst_net_buy_estimated", 0) > 0
cond_for3d = l1.get("foreign_net_buy_3d", 0) > 0
cond_prog = l1.get("program_net_buy_change", 0) > 0
cond_close_flow = l1.get("closing_flow_concentration", 0) > 0
layer1_subscore = sum(1 for c in (cond_inst, cond_for3d, cond_prog, cond_close_flow) if c is True)
```
가중치는 `__init__(layer1_weight=0.0)` 기본값. settings.yaml에서 `score.layer1_weight` 키로 오버라이드.

### 2. candidate_logger.log_flow_reliability (이미 구현)
**위치**: `closing_bet_system/storage/candidate_logger.py:435-475`
- `_direction_match(estimated, confirmed) -> Optional[bool]` 헬퍼
- INSERT OR REPLACE UNIQUE(trade_date, ticker)
- 본 단위 Step 3에서 그대로 재사용

### 3. flow_data_reliability 스키마
**위치**: `closing_bet_system/storage/db.py:323-340`
- 컬럼: trade_date, ticker, inst_estimated, inst_confirmed, foreign_estimated, foreign_confirmed, inst/foreign_direction_match, recorded_at
- 현재 0 rows (5/11 기준)
- **수량 단위 비교 주의**: estimated는 원 (HHPTJ qty × close), confirmed는 주 (네이버 qty)
  → 부호만 비교하므로 단위 불일치 무관 (`_direction_match`는 sign 비교)
  → 또는 confirmed에도 종가 곱셈 적용해서 단위 통일 (선택)

### 4. KIS `get_investor_trading()` (FHKST01010900) — 기존
**위치**: `modules/stock_screener/kis_api.py:594-696`
- 5/11 데이터 23건 모두 inst_net_buy_estimated = 0.0 (NULL 아님)
- **원인 (Step 0 확정)**: KIS API 정책. 15:10 시점에 daily[0].institution = 0 반환. 16:06 호출 시 정상값. 코드 버그 아님.
- 본 단위 Step 1에서 새 메서드 `get_investor_trend_estimate()` 추가 (HHPTJ04160200), kis_intraday_flow_collector에서 inst_net_buy_estimated만 새 메서드로 대체

### 5. KIS HHPTJ04160200 신규 메서드 골격 (Step 1)
```python
# modules/stock_screener/kis_api.py 신규 메서드
def get_investor_trend_estimate(self, stock_code: str) -> Optional[dict]:
    """종목별 외인기관 추정가집계 (HHPTJ04160200).
    
    KIS 직원이 장중에 집계/입력한 자료의 단순 누계.
    입력시간: 외인 09:30/11:20/13:20/14:30, 기관 10:00/11:20/13:20/14:30.
    
    반환: {
        "stock_code", 
        "latest_inst_qty": int,      # bsop_hour_gb=='5' (14:30 누계, 단위: 주)
        "latest_foreign_qty": int,
        "latest_sum_qty": int,
        "by_slot": [
            {"slot_gb": "1"|...|"5", "frgn": int, "orgn": int, "sum": int},
            ...
        ],
    }
    """
    self._rate_limit()
    url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
    tr_id = "HHPTJ04160200"
    headers = self._get_headers(tr_id)
    params = {"MKSC_SHRN_ISCD": stock_code}
    try:
        response = self.client.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("rt_cd") != "0":
            logger.warning(f"[{stock_code}] HHPTJ 조회 실패: {data.get('msg1')}")
            return None
        out2 = data.get("output2", []) or []
        by_slot = []
        latest = {"frgn": 0, "orgn": 0, "sum": 0}
        for row in out2:
            slot_gb = row.get("bsop_hour_gb", "")
            f = _safe_int(row.get("frgn_fake_ntby_qty"))
            o = _safe_int(row.get("orgn_fake_ntby_qty"))
            s = _safe_int(row.get("sum_fake_ntby_qty"))
            by_slot.append({"slot_gb": slot_gb, "frgn": f, "orgn": o, "sum": s})
            if slot_gb == "5":
                latest = {"frgn": f, "orgn": o, "sum": s}
        # 폴백: bsop_hour_gb=='5' 없으면 가장 큰 차수 사용
        if latest["frgn"] == 0 and latest["orgn"] == 0 and by_slot:
            top = max(by_slot, key=lambda x: x["slot_gb"])
            latest = {"frgn": top["frgn"], "orgn": top["orgn"], "sum": top["sum"]}
        return {
            "stock_code": stock_code,
            "latest_inst_qty": latest["orgn"],
            "latest_foreign_qty": latest["frgn"],
            "latest_sum_qty": latest["sum"],
            "by_slot": by_slot,
        }
    except Exception as e:
        logger.error(f"[{stock_code}] HHPTJ 조회 실패: {e}")
        return None
```

### 6. kis_intraday_flow_collector 수정 골격 (Step 1)
**파일**: `closing_bet_system/collectors/kis_intraday_flow_collector.py:230-240`
```python
# 기존 inst 수집 (FHKST01010900) 폐기 또는 폴백 (HHPTJ 실패 시)
# inst_net_buy_estimated = latest_inst_qty * latest_close  (latest_inst_qty=0 박제 문제)

# 신규: HHPTJ04160200 우선 호출
trend = kis.get_investor_trend_estimate(stock_code)
if trend is not None:
    inst_net_buy_estimated = trend["latest_inst_qty"] * latest_close
    # foreign도 가능하면 동일 소스로 통일 (선택)
else:
    # FHKST01010900 폴백 또는 None
    inst_net_buy_estimated = None
```

### 7. flow_reliability_tracker 신규 모듈 골격 (Step 2~4)
```python
# closing_bet_system/services/flow_reliability_tracker.py (신규, ~250줄)
from __future__ import annotations
import sqlite3, time
from datetime import date, timedelta
from typing import Optional, Iterable

import requests
from bs4 import BeautifulSoup

from logger import logger
from config import now_kst, is_trading_day


_NAVER_FRGN_URL = "https://finance.naver.com/item/frgn.naver"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch_naver_confirmed_flow(
    trade_date: date,
    tickers: Iterable[str],
    sleep_per_ticker: float = 0.4,
    retry: int = 3,
    backoff: float = 5.0,
) -> dict[str, dict]:
    """네이버 frgn.naver 크롤링으로 종목별 외인/기관 일별 순매매 수량 수집.
    
    KOSPI/KOSDAQ 무관 단일 endpoint.
    수량 단위(주). 부호 비교 목적.
    
    반환: {ticker: {"inst_confirmed_qty": int, "foreign_confirmed_qty": int, "close": int}}
    빈 응답/일자 미일치 시 ticker 항목 누락 (graceful).
    """
    target_date_str = trade_date.strftime("%Y.%m.%d")
    headers = {"User-Agent": _USER_AGENT}
    out: dict[str, dict] = {}
    for ticker in tickers:
        for attempt in range(retry):
            try:
                r = requests.get(_NAVER_FRGN_URL,
                                 params={"code": ticker}, headers=headers, timeout=15)
                r.encoding = "euc-kr"
                soup = BeautifulSoup(r.text, "html.parser")
                tables = soup.find_all("table", class_="type2")
                if len(tables) < 2:
                    raise RuntimeError("no type2 table")
                rows = tables[1].find_all("tr")[2:]
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 7:
                        continue
                    d = cells[0].get_text(strip=True)
                    if d != target_date_str:
                        continue
                    close = _parse_int(cells[1].get_text(strip=True))
                    inst = _parse_int(cells[5].get_text(strip=True))
                    foreign = _parse_int(cells[6].get_text(strip=True))
                    out[ticker] = {
                        "inst_confirmed_qty": inst,
                        "foreign_confirmed_qty": foreign,
                        "close": close,
                    }
                    break
                break  # 일자 미일치도 성공 (out에서 누락만)
            except Exception as e:
                if attempt < retry - 1:
                    logger.warning(f"[reliability] {ticker} 재시도 {attempt+1}/{retry}: {e}")
                    time.sleep(backoff)
                else:
                    logger.error(f"[reliability] {ticker} 최종 실패: {e}")
        time.sleep(sleep_per_ticker)
    return out


def _parse_int(s: str) -> int:
    """'+1,074,644' / '-9,595,937' / '232,500' → int"""
    if not s:
        return 0
    s = s.replace(",", "").replace("+", "").strip()
    try:
        return int(s)
    except ValueError:
        return 0


def compute_direction_match_rate(
    window_days: int = 7,
    indicator: str = "inst",  # "inst" | "foreign"
    db_path: str = "data/closing_bet.db",
) -> dict:
    """flow_data_reliability에서 N영업일 윈도우 일치율 집계."""
    col = "inst_direction_match" if indicator == "inst" else "foreign_direction_match"
    end = now_kst().date()
    # 영업일 N일 역산
    start = end
    n = window_days
    while n > 0:
        start = start - timedelta(days=1)
        if is_trading_day(start):
            n -= 1
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        row = cur.execute(f"""
            SELECT COUNT(*), SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END)
            FROM flow_data_reliability
            WHERE trade_date BETWEEN ? AND ?
              AND {col} IS NOT NULL
        """, (str(start), str(end))).fetchone()
        n_samples = row[0]
        n_match = row[1] or 0
        rate = n_match / n_samples if n_samples > 0 else None
        return {
            "window_days": window_days,
            "indicator": indicator,
            "n_samples": n_samples,
            "match_rate": rate,
            "passes_70_threshold": rate is not None and rate >= 0.7,
        }
    finally:
        conn.close()
```

## 5/11 현재 데이터 상태

### candidates 점수 분포 (78건, 5/4~5/11)
| 점수 | 건수 |
|---|---|
| layer1_score=0 | 44건 |
| layer1_score=1 | 34건 |
| layer1_score≥2 | 0건 |

### candidate_features 샘플 (5/11)
```
candidate_id=59: inst=0.0 (박제), foreign=-366억
candidate_id=60: inst=0.0 (박제), foreign=+36억
candidate_id=61: inst=0.0 (박제), foreign=+817억
```
→ inst가 항상 0. Step 1 배포 후 5/12부터 정상화.

### flow_data_reliability 테이블
- 0 rows (5/11 기준)
- Step 3 잡 첫 실행(5/12 19:27) 시 5/11 candidates 23건 INSERT 예상

## 핵심 구현 세부사항

### candidate_features 컬럼 확인 (Step 3 매칭 시)
실제 컬럼명: `candidate_id`, `ticker` (또는 `stock_code`?). Step 3 작업 전 정확히 확인 필요:
```sql
PRAGMA table_info(candidate_features);
```
(이 세션에서 확인 시도했으나 `ticker` 컬럼 없음 에러 발생 — 다음 세션에서 재확인)

### 호출 빈도 / rate limit
- HHPTJ04160200: KIS API 표준 rate limit 적용 (`self._rate_limit()`)
- 네이버 frgn.naver: ticker당 0.3~0.5초 sleep (차단 회피)
- 23건 candidates 기준 약 10~15초 소요

## 과거 버그 / 운영 흔적

- **5/8 셀트리온 라벨링 누락 fix (커밋 de2fdb2)** — KIS API 일시 500 에러 1회로 영구 누락 발생 → 본 단위에서도 KRX/네이버 일시 실패 대응 필요 (재시도 로직)
- **2026-05-11 라벨링 영업일 버그 fix (커밋 b4c38d2)** — `main_orchestrator.run_label_yesterday`의 yesterday를 영업일 기반으로 변경. **본 단위의 `run_flow_reliability_check`도 동일한 패턴 적용 필수** (월요일에 일요일 조회 방지)

## 영향 범위

### 직접 영향
- 새 KIS API 메서드 1개 (`get_investor_trend_estimate`)
- 기존 collector 1파일 수정 (kis_intraday_flow_collector.py)
- 신규 모듈 1개 (`closing_bet_system/services/flow_reliability_tracker.py`)
- 새 APScheduler 잡 1개 추가 (매일 19:27 KST)

### 간접 영향
- 5/12+ Layer 1 점수 분포 변화 (inst 정상값 수집 후) → walkforward 결과 비교 시 활성화 전/후 데이터 분리 주의
- 7~10일 후 layer1_weight 활성화 결정 시 settings.yaml 변경 + 봇 재시작 1회

## 참고

- **메모리**: `memory/project_closing_bet_followups.md` (단위 2-3 진입 권고 + 본 단위 동기)
- **PRD**: `종가베팅_트레이딩_시스템_PRD_v2.0.md` 6-1 점수 체계 + Phase 2-6 가중치 정책
- **KIS Open API 공식 sample**: `https://github.com/koreainvestment/open-trading-api`
  - 경로: `examples_llm/domestic_stock/investor_trend_estimate/`
  - TR `HHPTJ04160200` (이미 본 세션에서 직접 호출 검증 완료)
- **관련 코드**:
  - `modules/stock_screener/kis_api.py:594` (FHKST01010900 — Step 1에서 폴백 또는 폐기)
  - `closing_bet_system/engines/signal_score_engine.py:284` (Layer 1 점수)
  - `closing_bet_system/storage/candidate_logger.py:435,646` (log_flow_reliability + _direction_match)
  - `closing_bet_system/storage/db.py:323` (flow_data_reliability 스키마)
  - `closing_bet_system/collectors/kis_intraday_flow_collector.py:230` (inst 추정치)
- **이전 커밋**:
  - `b4c38d2` 라벨링 영업일 fix — 본 단위 yesterday 처리 동일 패턴
  - `de2fdb2` 셀트리온 KIS 재시도 fix — 본 단위 KIS/네이버 호출에도 재시도 권장

---

## 작업 중 발견 사항 — 2026-05-11 2차 세션 (Step 0 완료 + 옵션 H 확정)

### 세션 흐름
1. `/resume`으로 closing-bet-unit-2-3-flow-reliability 재개 (1차 세션은 5/11 오전, 옵션 G로 보류)
2. Step 0 a/b/d 3개 모두 직접 검증 수행 (0c는 5/12 화요일 자연 검증으로 분리)
3. PLAN/CONTEXT/CHECKLIST 옵션 H로 전면 재설계
4. 임시 클론(`/tmp/kis_api_sample`) 정리

### 핵심 검증 (직접 호출 결과)
1. **0a — pykrx**: 7개 후보 함수 모두 빈 응답/KeyError. 데이터 소스 폐기 결정.
2. **0b — KRX/네이버**:
   - KRX `data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`: HTTP 400 + body `LOGOUT`. 세션 쿠키 + Referer 추가해도 동일.
   - 네이버 `frgn.naver`: KOSPI/KOSDAQ 모두 정상. 단, 17:37 KST 시점에 5/11 데이터 미표시 → **18~19시 갱신 추정** → 19:27 잡 시점 사용 가능.
3. **0d — KIS HHPTJ04160200**:
   - 공식 GitHub `koreainvestment/open-trading-api` clone 후 `examples_llm/domestic_stock/investor_trend_estimate/` 발견.
   - TR `HHPTJ04160200` URL `/uapi/domestic-stock/v1/quotations/investor-trend-estimate`.
   - 5/11 17:42 KST 직접 호출: 005930 / 086520 모두 output2 5건 정상 반환.
   - bsop_hour_gb=5 (14:30 누계) 데이터: 005930 외인 -2,906,000주 / 기관 +1,442,000주 (수량 단위, 18자리 zero-padded 부호 문자열).

### 옵션 H 결정 근거
- HHPTJ04160200 (장중 가집계) + 네이버 frgn.naver (사후 확정값) 양쪽 데이터 소스 모두 확보됨
- 단위 2-3 본래 설계 (KIS 추정치 vs KRX 확정값 매칭 일치율 70%+ 검증)와 동일 use case 가능
- 1차 시도의 두 가지 한계 모두 해결:
  - inst=0 박제 문제 → HHPTJ로 14:30 누계 사용 (15:10 daily_pipeline 시점 가용)
  - pykrx KRX 빈 응답 → 네이버 frgn.naver 대체

### 부수 발견 / 메모 (다음 세션 주의)
1. **KIS 토큰 1분 1회 발급 제한**: 봇이 systemd로 가동 중일 때 직접 호출 시 첫 시도 403 → 60~70초 대기 후 재시도 필요
2. **`candidate_features` 정확한 컬럼명 미확인**: 본 세션 sqlite3 호출 시 `no such column: ticker` 에러. 다음 세션 Step 3 진입 전 `PRAGMA table_info(candidate_features);` 필수
3. **수량 vs 금액 단위 불일치**:
   - HHPTJ 가집계 = 주(qty) → `latest_inst_qty * latest_close`로 금액 환산해서 candidate_features.inst_net_buy_estimated에 저장 (기존 패턴 유지)
   - 네이버 확정값 = 주(qty) → 부호 비교만 사용하므로 단위 환산 불필요
   - `_direction_match()`는 sign 비교만 하므로 단위 불일치 무관
4. **HHPTJ 추가 입력 차수 가능성**: 공식 docstring은 14:30이 마지막이지만 "사정에 따라 변동될 수 있다"고 명시. 5/12 daily_pipeline 결과로 15:00/15:10에 추가 입력 발생 여부 확인 필요
5. **foreign 통일 결정 보류**: 기존 `FHKST01010900`의 foreign은 5/11 정상 작동. HHPTJ로 일원화 vs 기존 유지 결정은 Step 1 진입 시 검토 (HHPTJ 단일 소스 권장 — 일관성)

### 변경 파일 (이번 세션)
| 파일 | 변경 내용 |
|---|---|
| `docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/PLAN.md` | 옵션 H 확정 + Step 0 결과 + Step 1~6 재정의 |
| `docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/CONTEXT.md` | Step 0 a/b/d 직접 검증 결과 + HHPTJ 응답 샘플 + 신규 메서드/모듈 골격 + 본 섹션 |
| `docs/work-plans/active/closing-bet-unit-2-3-flow-reliability/CHECKLIST.md` | Step 0 체크 + 보조 검증(5/12) + Step 1~6 진행 항목 갱신 |

### 다음 세션 진입 (`/resume`) — 첫 작업 순서
1. `PRAGMA table_info(candidate_features);` 실행 → `ticker` vs `stock_code` 정확한 컬럼명 확정
2. `modules/stock_screener/kis_api.py`에 `get_investor_trend_estimate()` 메서드 추가 (Step 1)
3. `closing_bet_system/collectors/kis_intraday_flow_collector.py:230-240` 수정 (HHPTJ 호출 통합)
4. py_compile + code-tester
5. 5/12 15:10 daily_pipeline 자연 검증 (inst != 0 확인) — 봇 재시작 후 자연 검증
6. Step 2 (네이버 사후 확정값 수집기) 신규 모듈 작성

### 컨텍스트 상태
- 본 세션 컨텍스트는 충분히 정리됨 (1차+2차 시도 모두 PLAN/CONTEXT에 반영)
- Step 1 진입은 새 세션 권장 (다음 `/resume`)
