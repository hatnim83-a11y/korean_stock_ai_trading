# CONTEXT: 매수 필터 체인 개선 (Phase A)

## 변경 이유

최근 3일 매수 실패율 66.7%의 전수조사 결과 → 제안서 `docs/improvements/2026-04-23_buy_filter_proposal.md` 작성
- 1차 원인: 강세장 RSI 70 고정컷 (4/23: 11건 RSI ≥80 탈락)
- 2차 원인: 테마당 슬롯 보장 부재 (4/23: 5개 테마 중 3개가 AI 검증 0건)

## 현재 코드 상태 (변경 대상 파일별 스니펫)

### `config.py:384-404`
```python
# ===== 스크리닝 조건 =====
MIN_TRADING_VALUE: int = Field(default=5_000_000_000, description="최소 거래대금 (50억원)")
RSI_UPPER_LIMIT: float = Field(default=70.0, description="RSI 상한선 (과열 방지)")
RSI_LOWER_LIMIT: float = Field(default=30.0, description="RSI 하한선 (과매도)")
VOLUME_RATIO_MIN: float = Field(default=1.2, description="거래량 비율 하한")
```
→ **라인 389 직후에 RSI 동적 6개 + 테마 슬롯 2개 Field 추가**

### `modules/stock_screener/filters.py:40`
```python
RSI_UPPER_LIMIT = 70.0  # RSI 상한 (과열 방지, 백테스트 정합)
```
→ **모듈 상수와 `settings.RSI_UPPER_NORMAL` 이원화 해소 필요 (Q7-3)**

### `modules/stock_screener/filters.py:160-166`
```python
def apply_technical_filter(
    stock: dict,
    rsi_upper: float = RSI_UPPER_LIMIT,  # ✅ 이미 파라미터화
    ...
```

### `modules/stock_screener/filters.py:393-406`
```python
def apply_all_filters(
    stock: dict,
    ...
    rsi_upper: float = RSI_UPPER_LIMIT,  # ✅ 이미 파라미터화
    ...
```

### `modules/stock_screener/screener.py:43`
```python
MIN_FINAL_SCORE = 45.0  # 최소 최종 점수
```

### `modules/stock_screener/screener.py:132-137`
```python
def screen_stocks_in_theme(
    theme: dict,
    stock_codes: list[str],
    max_stocks: int = MAX_STOCKS_PER_THEME,
    kis_api: Optional["KISApi"] = None
) -> tuple[list[dict], list[dict]]:
    # ...
    # 라인 211: filtered = apply_all_filters(stock_info)  ← rsi_upper 전달 안 됨
```

### `modules/stock_screener/screener.py:278-291`
```python
def screen_all_themes(
    themes: list[dict],
    theme_stocks: dict[str, list[str]],
    max_per_theme: int = MAX_STOCKS_PER_THEME,
    max_total: int = MAX_TOTAL_CANDIDATES
) -> tuple[list[dict], list[dict]]:
    # ...
    # 라인 339: unique_candidates = unique_candidates[:max_total]  ← 이 컷이 문제
```

### `modules/stock_screener/screener.py:594-598`
```python
candidates, screening_logs = screen_all_themes(
    themes=themes, theme_stocks=theme_stocks,
    max_per_theme=max_per_theme, max_total=max_total
)
# 최소 점수 필터
candidates = [c for c in candidates if c.get("final_score", 0) >= MIN_FINAL_SCORE]
```
→ **이 위치에서 테마 슬롯 보장 적용, 단 상위 `max_total=30` 컷이 이미 결과 손상**

### `modules/market_guard.py:37-92`
- `check()`: 당일 KOSPI/KOSDAQ `change_rate` 조회만 지원
- **전일 등락률 조회 메서드 없음** → `kis_api.py`에 신규 필요

### `modules/stock_screener/kis_api.py:294-330`
- `get_index_price(index_code)`: 현재 시점 지수만 반환
- 일봉 히스토리 API 미구현

### `main.py:941-954` (midweek 재평가 경로)
- `_rescore_midweek_loss_stocks()`가 `screen_stocks_in_theme()` 호출
- **이 경로는 rsi_upper=None 기본값 사용 → 평시 기준 유지** (사용자 확정)

## 핵심 스니펫 (변경 후 예상)

### 전파 구조
```
run_daily_screening
  └── _get_market_regime_rsi(kis_api=None)  [1회 호출, INFO 로그]
       → rsi_upper (75 / 70 / 65)
  └── screen_all_themes(..., rsi_upper=rsi_upper)  [max_total 컷 제거]
       └── screen_stocks_in_theme(..., rsi_upper=rsi_upper)
            └── apply_all_filters(stock, rsi_upper=rsi_upper)
  └── _apply_theme_min_slot(candidates, MIN_FINAL_SCORE, SAFETY_FLOOR)
  └── min_score 컷
  └── max_total 최종 컷 (슬롯 보장분 우선 보존)
```

### DB 마이그레이션 (v14)
```python
# database.py _migrate() 내부
if current_version < 13:
    cursor.execute("ALTER TABLE screening_log ADD COLUMN rsi_at_screen REAL DEFAULT NULL")
    cursor.execute("ALTER TABLE screening_log ADD COLUMN theme_slot_protected INTEGER DEFAULT 0")
    cursor.execute("UPDATE schema_version SET version = 13")
```

## 영향 범위

### 호출 체인
- `main.py:run_daily_screening_only_job` (스케줄) → 일일 스크리닝
- `main.py:execute_buy_orders` 앞단 → 매수 전 최종 필터
- `main.py:_rescore_midweek_loss_stocks` (midweek) → 평시 기준 유지

### 미영향 (의도적)
- `modules/market_guard.py`: 당일 KOSPI 등락률은 그대로 (매수 시점 방어용)
- `modules/rebalancer/`: 보유기간/로테이션 로직 영향 없음
- 백테스터: 실전만 적용, 백테스트는 기존 로직 유지 (주석 명시 필요)

## 과거 버그/교훈

- **2026-04-16 (ORD_DVSN 실기 검증)**: KIS API 스펙은 문서와 실기가 다를 수 있음 → 본 작업 TR ID도 반드시 실기 검증
- **2026-03-13 (screening_log INSERT OR IGNORE)**: UNIQUE 충돌 방지 필수
- **2026-02-06 (KST 타임존)**: `now_kst()` 사용 필수
- **2026-04-21 (화요일 회전문)**: 새 로직 배포 시 쿨다운/클램프 설계 필수 — 본 작업은 테마 회전 없으므로 해당 없음

## MCP 활용 계획

- DB 마이그레이션 확인: MCP SQLite `list_tables` + `describe_table screening_log`
- 배포 후 결과 집계: MCP SQLite `read_query`
- 현재 스키마 버전 확인: `SELECT * FROM schema_version`

## 참고 문서

- `docs/improvements/2026-04-23_buy_filter_proposal.md` (제안서)
- `memory/project_strategy.md` (현재 전략)
- `memory/MEMORY.md` (프로젝트 메모리 인덱스)
- `modules/CLAUDE.md` (모듈 규칙)
- `CLAUDE.md` (프로젝트 규칙)

## 에이전트 리뷰 결과 (2026-04-24)

### strategy-planner
- **GO with 조건**: 배포 순서 / API fallback / screening_log 관찰 컬럼
- A1: RSI 75 꼬리 위험 → 롤백 트리거로 사후 제어 가능
- A2: 슬롯 보장 + AI 게이트 조합 → 최악 시나리오가 현재와 동일, 수용 가능
- A3: 당일 갭다운 시 market_guard가 독립 보완

### strategy-coder
- **GO with 조건**: max_total × 슬롯 보장 순서 / TR ID / RSI 상수 일원화 / midweek 경로
- Q3: `run_daily_screening`에서 3단 컷 적용으로 해결
- Q7-3: `filters.py:40` 상수 일원화 (본 플랜 반영)
- Q7-4: 백테스트 정합성 주석 명시 필요

---

## 작업 중 발견 사항 (2026-04-24 대화 세션)

### 🔍 발견 1: v13은 이미 사용 중 → v14로 변경
- 초안은 "DB v13"으로 계획했으나 `database.py:175`에서 v13이 이미 "themes 산업재→금융 재분류"에 사용 중
- **조치**: 신규 마이그레이션을 v14로 확정 (`_migrate_v14`). PLAN/CONTEXT/CHECKLIST 3문서의 v13 표기 모두 v14로 일괄 수정

### 🔍 발견 2: KIS TR_ID `FHKUP03500100` 실기 검증 결과
- `scripts/` 대신 인라인 `python -c "..."`로 READ-ONLY 호출 1회
- **결과**: 정상 작동
  - KOSPI ("0001") 전일 등락률 **+0.90%** (2026-04-22 6417.93 → 2026-04-23 6475.81)
  - KOSDAQ ("1001") 전일 등락률 **-0.58%**
- URL: `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice`
- 파라미터: `FID_COND_MRKT_DIV_CODE=U`, `FID_INPUT_ISCD=0001`, `FID_INPUT_DATE_1~2`, `FID_PERIOD_DIV_CODE=D`
- 응답 `output2` 배열 → 당일 배제 후 최신 2건 종가 사용

### 🔍 발견 3: 제안서 "4/23 +1.67%" vs 실제 종가 +0.90% 불일치
- 제안서는 "강세장(KOSPI +1.67%)"로 표현했으나 이는 **장중 스냅샷**(저널로그 09:30 시점)
- **확정 종가 기준 등락률은 +0.90%** → `RSI_BULL_THRESHOLD=1.0` 미달 → NORMAL(RSI 70) 판정
- **시사점**: 제안서가 기대한 "4/23 같은 강세장에서 RSI 75 발동 → ≥45점 2→4~5건 복원" 효과는 실제 데이터상 더 **보수적**으로만 발동. 의도치 않게 안전한 방향
- 메모리 `project_buy_filter_phase_a.md`에 이 불일치 명시

### 🔍 발견 4: 블로커 과잉 신중 → 병행 진행으로 전환
- 초안은 "TR_ID 실기 검증"을 Phase A-0 블로커로 지정 (ORD_DVSN 전례 기반)
- **실제**: 이번 TR은 단순 READ-ONLY 조회이고, 실패해도 NORMAL 폴백이 안전하게 작동 → 블로커 불필요
- 사용자 의견 반영하여 **Phase A-2에 실기 검증 통합**으로 전환. 이후 흐름 원활

### 🔍 발견 5: `max_total` 컷 × 슬롯 보장 순서 구멍 (code-review로 사전 발견)
- strategy-coder 에이전트가 지적: `screen_all_themes` 내부 `unique_candidates[:max_total=30]` 컷이 슬롯 보장보다 먼저 실행되면 테마 후순위 종목 정보가 손실됨
- **해결**: `max_total: Optional[int] = None` 파라미터 추가 → `run_daily_screening`에서 호출 시 `None` 전달 → 슬롯 보장 후 최종 컷 수행

### 🔍 발견 6: code-tester 주의 이슈 2건 (배포 전 즉시 해소)
1. **`RSI_UPPER_LIMIT` 중복 상수 잔존**: `config.py`에 구 Field와 신규 `RSI_UPPER_NORMAL`이 공존 → **구 Field 제거 + `.env` 동기화**
2. **`output2` 정렬 순서 가정 미명시**: KIS 응답 순서 뒤바뀔 경우 대비 → **`closes.sort(key=lambda x: x[0], reverse=True)` 추가**

### 🔍 발견 7: `/schedule` 원격 에이전트 제약
- 원격 에이전트는 로컬 VM의 `data/trading.db`에 접근 불가, MCP SQLite 미연결
- **대안 전환**: 기존 `improvement_reminder_weekly` 잡 (금 17:45 KST 텔레그램 알림) 활용
- 메모리에 "5/1 수신 시 `/improve focus:buy_filter_phase_a_review` 실행" 리마인더 추가로 최소 개입 해결
- 5/1이 근로자의 날이지만 리마인더는 공휴일에도 발송되는 코드 확인 (`scheduler.py:510`)

---

## 배포 완료 요약 (2026-04-24 22:14 KST)

### 수정 파일 (6)
- `config.py` — RSI 동적 6개 + 테마 슬롯 2개 Field 추가, 구 `RSI_UPPER_LIMIT` Field **제거**
- `database.py` — `_migrate_v14` + `save_screening_log` 확장
- `modules/stock_screener/kis_api.py` — `get_prev_index_change_rate` 신규 (출력 정렬 방어 포함)
- `modules/stock_screener/filters.py` — `RSI_UPPER_LIMIT = settings.RSI_UPPER_NORMAL` 일원화
- `modules/stock_screener/screener.py` — 헬퍼 2개 (`_get_market_regime_rsi`, `_apply_theme_min_slot`) + 3단 컷
- `.env` — `RSI_UPPER_LIMIT` → `RSI_UPPER_NORMAL/BULL/BEAR` 3개로 교체

### 검증 실측치
- `_get_market_regime_rsi()` 실기 호출: KOSPI +0.90% → NORMAL(70) 판정
- `_apply_theme_min_slot()` 4/23 시뮬레이션: 9건 입력(5테마) → 6건 출력 (5테마 전부 보장 + 45점 이상 1건)
- schema_version: v14 적용 (2026-04-24 13:09:14)
- screening_log 신규 컬럼 적용 확인 (`rsi_at_screen` idx 11, `theme_slot_protected` idx 12)
- DB 백업: `data/trading.bak.20260424_220913*`

### systemd 재시작
- 이전 PID 484162 → 신규 PID 1008604 (2026-04-24 13:14:49 UTC = 22:14 KST)
- 스케줄 전체 정상 등록 확인
- Active: running

### 문서 업데이트 완료
- `docs/improvements/change_log.md` 2026-04-24 행 추가
- `memory/project_buy_filter_phase_a.md` 신규 작성
- `memory/MEMORY.md` 인덱스 추가
- 3문서 (PLAN/CONTEXT/CHECKLIST) 최신화

---

## 다음 대화 진입점 (2026-05-01 금 17:45 이후)

**컨텍스트가 충분히 큽니다 — 5/1 관찰 집계 작업 시 새 대화 시작 권장.**

새 대화에서 입력할 명령:
```
/improve focus:buy_filter_phase_a_review
```

### 새 대화가 참조해야 할 문서
1. `memory/project_buy_filter_phase_a.md` — Phase A 전체 맥락 + 5/1 액션 지시
2. `docs/improvements/2026-04-23_buy_filter_proposal.md` — 원본 제안서 (롤백 트리거 9.1절)
3. `docs/work-plans/active/buy-filter-rsi-theme-slot/CHECKLIST.md` — Phase A-6 관찰 항목
4. `docs/improvements/change_log.md` — before/after 비교 기준
5. `data/trading.db` screening_log 테이블 (v14 컬럼: `rsi_at_screen`, `theme_slot_protected`)

### 새 대화에서 확인할 지표
- 4/28~5/1 기간 `screening_log` 매일 `[RSI Regime]` / `[Theme Slot]` 로그
- `SELECT COUNT(*) FROM screening_log WHERE theme_slot_protected=1 AND date >= '2026-04-28'`
- `SELECT rsi_at_screen, passed, reject_reason FROM screening_log WHERE rsi_at_screen BETWEEN 70 AND 75 AND date >= '2026-04-28'`
- 매수 실패일(해당 영업일 매수 0건) 수 ≤ 1/5 달성 여부
- 트레일링/손절 기록으로 RSI 70~75 구간 진입 종목 5일 평균 수익률

### 판정 분기
- **롤백 미발동** → CHECKLIST Phase A-6 체크, 3문서 아카이브, Phase B(MIN_FINAL_SCORE 45→42) 검토 착수
- **롤백 발동** → `.env`에 스위치 OFF 2줄 추가 + 재시작 + change_log.md 롤백 행 추가
