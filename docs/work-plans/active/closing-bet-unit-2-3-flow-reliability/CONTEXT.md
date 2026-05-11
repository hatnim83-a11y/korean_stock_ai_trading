# CONTEXT — 단위 2-3 flow_reliability_tracker

## 🚨 2026-05-11 1차 시도 발견 사항 (재설계 결정 근거)

### 1. KIS API 시간대별 응답 정책 (가설 D 확정)
- 5/11 16:06 KIS `get_investor_trading('005930', days=5)` 호출 → daily[0] institution=+1,604,512 정상 반환
- 5/11 15:10 daily_pipeline 시점: candidate_features.inst_net_buy_estimated = **0.0** (DB 저장값)
- **결론**: KIS `FHKST01010900` TR이 장중 15:10 시점에는 inst 추정치 미집계 (0 반환), 마감 후 갱신
- **코드 버그 아님** — KIS API 정책

### 2. 사용자 핵심 통찰 (2026-05-11 KST)
> "동시호가 시점에 필요한 데이터가 아닌가??"

→ **종가베팅 매수 결정 시점은 15:20~15:28 동시호가**. 16:00 사후 재수집 데이터는 매수 결정에 못 씀.
→ 옵션 A "16:00 inst 재수집"은 본질적으로 잘못된 방향. 철회.

### 3. pykrx KRX 투자자별 매매 함수 마비
```python
from pykrx import stock as krx
# 모든 날짜 (5/4/7/30, 2024-01-31) + 모든 투자자 (기관합계/외국인합계/개인)
krx.get_market_net_purchases_of_equities_by_ticker('20260507', '20260507', 'KOSPI', '기관합계')
# → shape=(0, 0), cols=[]
krx.get_market_trading_value_by_date('20260504', '20260504', 'KOSPI')
# → shape=(0, 0)
krx.get_market_ohlcv_by_ticker('20260507', market='KOSPI')
# → KeyError: "시가/고가/저가/종가 컬럼 없음"
```
- **단위 2-9c bulk 빈 응답과 동일** — pykrx KRX API 정책 변경
- universe_v2 수집은 종목별 폴백(`_fetch_per_ticker_today_data`)으로 우회 작동 중

### 4. 옵션 G 결정 — 세션 종료 + 데이터 소스 재조사
원래 PLAN의 단위 2-3은 KIS 추정치 vs **KRX 확정값** 매칭이지만:
- pykrx로 KRX 확정값 수집 불가 (3 발견)
- inst는 15:10 시점에 0이라 매칭 의미 약함 (1 발견)

→ **데이터 소스 재조사 (Step 0) 후 재설계 필요**. 자세한 옵션은 PLAN.md "다음 세션 진입 시 추가 조사 Step (Step 0)" 참조.

### 5. 다음 세션 5/12 실시간 검증 스크립트 (참고)
```python
# 5/12(화) 15:00~15:35 동안 5분 간격 호출 → inst 갱신 시점 추적
from closing_bet_system.infra.kis_client import get_kis_api
from datetime import datetime, time
import time as t
kis = get_kis_api()
for slot in ('15:00', '15:10', '15:15', '15:20', '15:25', '15:28', '15:30', '15:35'):
    # 해당 시각까지 sleep 또는 즉시 호출
    payload = kis.get_investor_trading('005930', days=2)
    daily0 = payload['daily'][0]
    print(f"{slot}: date={daily0['date']} foreign={daily0['foreign']:+,} institution={daily0['institution']:+,}")
```

---

## 변경 이유

종가베팅 시스템 Phase 1 (알림형) 가동 중 점수 산식 분석:
- Layer 1 (수급, 4점) 가중치 **0.0** — KIS 추정치 정확도 미검증으로 의도적 보수
- 100건 게이트 도달 시점(5/14~15)에 Layer 1 활성화 결정 필요
- 활성화 결정 근거 = "추정치 vs KRX 확정값 방향 일치율 70%+ 검증"
- 검증 인프라(flow_reliability_tracker) 미구현 상태

본 단위로 검증 모듈 가동 → 7~10일 누적 후 데이터 기반 활성화 결정 가능.

## 현재 코드 상태 (수정 전)

### 1. Layer 1 점수 산정 (이미 구현)
**위치**: `closing_bet_system/engines/signal_score_engine.py:284-292`
```python
# Layer 1 (수급) — 4 조건
cond_inst = l1.get("inst_net_buy_estimated", 0) > 0
cond_for3d = l1.get("foreign_net_buy_3d", 0) > 0
cond_prog = l1.get("program_net_buy_change", 0) > 0
cond_close_flow = l1.get("closing_flow_concentration", 0) > 0
layer1_subscore = sum(1 for c in (cond_inst, cond_for3d, cond_prog, cond_close_flow) if c is True)
```
가중치는 `__init__(layer1_weight=0.0)` 기본값. settings.yaml에서 `score.layer1_weight` 키로 오버라이드.

### 2. candidate_logger.log_flow_reliability (이미 구현)
**위치**: `closing_bet_system/storage/candidate_logger.py:435-475`
```python
def log_flow_reliability(
    self, trade_date, ticker,
    inst_estimated=None, inst_confirmed=None,
    foreign_estimated=None, foreign_confirmed=None,
):
    inst_match = _direction_match(inst_estimated, inst_confirmed)
    for_match = _direction_match(foreign_estimated, foreign_confirmed)
    # INSERT OR REPLACE INTO flow_data_reliability (...)
```
- `_direction_match(estimated, confirmed) -> Optional[bool]` 헬퍼: 양쪽 None 시 None, 부호 일치 시 True
- INSERT OR REPLACE UNIQUE(trade_date, ticker) — 재실행 안전

### 3. flow_data_reliability 스키마
**위치**: `closing_bet_system/storage/db.py:323-340`
```sql
CREATE TABLE flow_data_reliability (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    inst_estimated REAL,
    inst_confirmed REAL,
    foreign_estimated REAL,
    foreign_confirmed REAL,
    inst_direction_match BOOLEAN,
    foreign_direction_match BOOLEAN,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, ticker)
);
```
**현재 상태**: 0 rows.

### 4. KIS 추정치 수집 (작동 중, 단 inst 버그)
**위치**: `closing_bet_system/collectors/kis_intraday_flow_collector.py:230-240`
```python
if not inst_data:
    inst_net_buy_estimated = None
else:
    latest_inst_qty = inst_data.get("acml_ntby_qty")  # 또는 유사 키
    if latest_inst_qty is None:
        inst_net_buy_estimated = None
    else:
        inst_net_buy_estimated = latest_inst_qty * latest_close
```
**버그 증상**: 5/11 데이터 23건 모두 inst_net_buy_estimated = 0.0 (NULL 아님!)
**추정 원인**: 
- KIS `get_investor_trading` API 응답에서 기관 ntby_qty가 0으로 반환 (또는 키 변경)
- latest_inst_qty가 0이면 0 × close = 0 저장
- foreign은 정상 작동 (5/11 23건 모두 0이 아닌 값)

### 5. pykrx KRX 확정값 수집 (이미 사용 중)
**참조 위치**: `closing_bet_system/collectors/universe_provider_v2.py:443`
```python
df = krx.get_market_net_purchases_of_equities_by_ticker(
    fromdate=today_str, todate=today_str, market='KOSPI', investor='기관합계'
)
```
- 시장: KOSPI / KOSDAQ 각각 호출
- 투자자: '기관합계' / '외국인합계' (정확한 인자명 pykrx 문서 확인 필요)
- 발표 시각: 정확하게는 오후 19:30 KST 이후 (당일 확정값 반영)

## 5/11 현재 데이터 상태

### candidates 점수 분포 (78건, 5/4~5/11)
| 점수 | 건수 |
|---|---|
| layer1_score=0 | 44건 |
| layer1_score=1 | 34건 |
| layer1_score≥2 | 0건 |

### candidate_features 샘플 (5/11)
```
candidate_id=59: inst=0.0,  foreign=-366억
candidate_id=60: inst=0.0,  foreign=+36억
candidate_id=61: inst=0.0,  foreign=+817억
```
→ inst가 항상 0. Layer 1 cond_inst 조건이 `inst > 0` 이라 항상 False.

## 핵심 스니펫 (Step 2~4 신규 모듈 골격)

```python
# closing_bet_system/services/flow_reliability_tracker.py (신규, ~200줄)
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from typing import Optional

from logger import logger
from config import now_kst, is_trading_day
from closing_bet_system.storage.candidate_logger import CandidateLogger


def fetch_krx_confirmed_flow(trade_date: date, tickers: list[str]) -> dict[str, dict]:
    """pykrx 확정값 수집. 반환: {ticker: {inst_confirmed, foreign_confirmed}}."""
    from pykrx import stock as krx
    today_str = trade_date.strftime("%Y%m%d")
    result: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        for investor_key, col in (("기관합계", "inst_confirmed"),
                                   ("외국인합계", "foreign_confirmed")):
            try:
                df = krx.get_market_net_purchases_of_equities_by_ticker(
                    fromdate=today_str, todate=today_str,
                    market=market, investor=investor_key,
                )
                # df.index = ticker (가정), 컬럼 중 "순매수거래대금" 추출
                for ticker in df.index:
                    if ticker in tickers:
                        net_value = float(df.loc[ticker, "순매수거래대금"])
                        result.setdefault(ticker, {})[col] = net_value
            except Exception as e:
                logger.warning(f"[reliability] {market} {investor_key} 수집 실패: {e}")
    return result


def compute_direction_match_rate(
    window_days: int = 7,
    indicator: str = "inst",
) -> dict:
    """flow_data_reliability에서 N일 윈도우 일치율 집계."""
    col = "inst_direction_match" if indicator == "inst" else "foreign_direction_match"
    end = now_kst().date()
    # 영업일 N일 역산
    start = end
    n = window_days
    while n > 0:
        start = start - timedelta(days=1)
        if is_trading_day(start):
            n -= 1
    conn = sqlite3.connect("data/closing_bet.db")
    cur = conn.cursor()
    row = cur.execute(f"""
        SELECT COUNT(*), SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END)
        FROM flow_data_reliability
        WHERE trade_date BETWEEN ? AND ?
          AND {col} IS NOT NULL
    """, (str(start), str(end))).fetchone()
    n_samples, n_match = row[0], row[1] or 0
    rate = n_match / n_samples if n_samples > 0 else None
    conn.close()
    return {
        "window_days": window_days,
        "indicator": indicator,
        "n_samples": n_samples,
        "match_rate": rate,
        "passes_70_threshold": rate is not None and rate >= 0.7,
    }
```

## 과거 버그 / 운영 흔적

- **5/8 셀트리온 라벨링 누락 fix (커밋 de2fdb2)** — KIS API 일시 500 에러 1회로 영구 누락 발생 → 본 단위에서도 KRX API 일시 실패 대응 필요 (재시도 로직)
- **2026-05-11 라벨링 영업일 버그 fix (커밋 b4c38d2)** — `main_orchestrator.run_label_yesterday`의 yesterday를 영업일 기반으로 변경. **본 단위의 `run_flow_reliability_check`도 동일한 패턴 적용 필수** (월요일에 일요일 조회 방지)

## 영향 범위

### 직접 영향
- 새 APScheduler 잡 1개 추가 (매일 19:30 KST)
- 신규 모듈 1개 (`closing_bet_system/services/flow_reliability_tracker.py`)
- inst 추정치 수집 버그 fix (기존 collector 1파일 수정)

### 간접 영향
- 5/12+ Layer 1 점수 분포 변화 (inst 버그 fix 후) → walkforward 결과 비교 시 활성화 전/후 데이터 분리 주의
- 7~10일 후 layer1_weight 활성화 결정 시 settings.yaml 변경 + 봇 재시작 1회

## 참고

- **메모리**: `memory/project_closing_bet_followups.md` (단위 2-3 진입 권고 + 본 단위 동기)
- **PRD**: `종가베팅_트레이딩_시스템_PRD_v2.0.md` 6-1 점수 체계 + Phase 2-6 가중치 정책
- **관련 코드**:
  - `closing_bet_system/engines/signal_score_engine.py` (점수 산식)
  - `closing_bet_system/storage/candidate_logger.py:435,646` (log_flow_reliability + _direction_match)
  - `closing_bet_system/storage/db.py:323` (flow_data_reliability 스키마)
  - `closing_bet_system/collectors/kis_intraday_flow_collector.py:230` (inst 추정치)
  - `closing_bet_system/collectors/universe_provider_v2.py:443` (pykrx 사용 패턴)
- **이전 커밋**:
  - `b4c38d2` 라벨링 영업일 fix — 본 단위 yesterday 처리 동일 패턴
  - `de2fdb2` 셀트리온 KIS 재시도 fix — 본 단위 KRX 호출에도 재시도 권장
