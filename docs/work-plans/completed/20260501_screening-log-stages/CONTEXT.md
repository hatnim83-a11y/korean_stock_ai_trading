# CONTEXT: screening_log 다단계 로그 추가 (Phase A)

## 변경 이유

monthly 분석(2026-05-01)에서 발견: `screening_log.stage` 컬럼이 항상 `'filter'`로만 채워져 있어 갭 필터·AI 검증 단계의 통과/탈락 흔적이 부재. 사후 분석(특히 W19 `focus:gap_filter`)이 데이터 부족으로 진행 불가.

## 현재 코드 상태

### `database.py` 스키마 (변경 없음, 참고용)
```sql
CREATE TABLE screening_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50) NOT NULL,
    theme VARCHAR(50),
    stage VARCHAR(30) NOT NULL,
    passed BOOLEAN DEFAULT 0,
    score REAL,
    reject_reason TEXT,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rsi_at_screen REAL DEFAULT NULL,
    theme_slot_protected INTEGER DEFAULT 0,
    UNIQUE(date, stock_code, stage)  -- ✅ stage 이미 포함, 마이그레이션 불필요
)
```

### `database.py:1366` `save_screening_log()`
- INSERT OR IGNORE 사용 → 중복 호출 시 안전
- 모든 컬럼 dict.get 폴백 → 일부 키 빠져도 동작

### `modules/morning_filter/morning_screener.py:191` `filter_candidates()`
- 5단계 필터: 갭(228) → 수급(252) → 거래량(264) → 체결강도(278) → 트렌드(290)
- 각 단계 `passed, excluded` 분리
- 갭 단계: `gap_filter.check_multiple()` → 결과의 `s.get('gap_result')`에 `reason`, `pct` 등 메타
- **현재**: screening_log 호출 0건 (전체 5단계 모두 미기록)

### `modules/ai_verifier/verifier.py:136` `verify_stocks_async()`
- 라인 191-217: 결과 병합 후 `result["ai_passed"]` 계산
- `EXCLUDE_RECOMMENDATIONS = ["No", "Hold"]` (라인 37) — Hold/No는 차단
- `MIN_AI_SCORE` import 필요 — verifier.py 상단 검증 필요
- **현재**: screening_log 호출 0건

### `modules/stock_screener/screener.py:780` (정상 — 변경 없음)
- 기존 `db.save_screening_log(log)` with `stage="filter"` (라인 345) 그대로 유지
- 새 stage들과 충돌 없음 (UNIQUE에 stage 포함)

## 핵심 스니펫 (변경 후 예상)

### morning_screener.py — Phase A-1
```python
# filter_candidates 종료 직전, return MorningScreenResult(...) 전에 추가
def _save_gap_filter_logs(
    candidates: list[dict],
    passed_codes: set,
    gap_results: dict,  # stock_code -> gap_result
) -> None:
    """갭 필터 단계 통과/탈락 로그를 screening_log에 일괄 저장."""
    try:
        from database import Database
        db = Database()
        db.connect()
        try:
            for stock in candidates:
                code = stock.get("stock_code") or stock.get("code")
                if not code:
                    continue
                gap_r = gap_results.get(code)
                passed = code in passed_codes
                db.save_screening_log({
                    "date": now_kst().date().isoformat(),
                    "stock_code": code,
                    "stock_name": stock.get("stock_name") or stock.get("name", ""),
                    "theme": stock.get("theme"),
                    "stage": "gap_filter",
                    "passed": passed,
                    "score": None,
                    "reject_reason": (gap_r.reason if gap_r and not passed else None),
                    "details_json": (json.dumps({
                        "gap_pct": getattr(gap_r, "gap_pct", None),
                    }, ensure_ascii=False) if gap_r else None),
                })
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"갭 필터 로그 저장 실패 (무시): {e}")
```

### verifier.py — Phase A-2
```python
# verify_stocks_async에서 verified 리스트 완성 후 추가
def _save_ai_verify_logs(verified: list[dict]) -> None:
    """AI 검증 단계 결과를 screening_log에 일괄 저장."""
    try:
        from database import Database
        from config import now_kst
        import json
        db = Database()
        db.connect()
        try:
            for v in verified:
                code = v.get("stock_code") or v.get("code")
                if not code:
                    continue
                passed = bool(v.get("ai_passed"))
                recommend = v.get("ai_recommend", "")
                sentiment = v.get("ai_sentiment", 0) or 0
                if passed:
                    reason = None
                elif recommend in EXCLUDE_RECOMMENDATIONS:
                    reason = f"recommend={recommend}"
                else:
                    reason = "low_score"
                db.save_screening_log({
                    "date": now_kst().date().isoformat(),
                    "stock_code": code,
                    "stock_name": v.get("stock_name") or v.get("name", ""),
                    "theme": v.get("theme"),
                    "stage": "ai_verify",
                    "passed": passed,
                    "score": sentiment,
                    "reject_reason": reason,
                    "details_json": json.dumps({
                        "recommend": recommend,
                        "confidence": v.get("ai_confidence"),
                        "target_return": v.get("ai_target_return"),
                    }, ensure_ascii=False),
                })
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"AI 검증 로그 저장 실패 (무시): {e}")
```

## 영향 범위

### 호출 체인
- `morning_screener.filter_candidates()` ← `main.py` 또는 morning observation 흐름
- `verifier.verify_stocks_async()` ← `verifier.run_daily_verification()` ← `main.py:run_stock_screening`

### 미영향
- 매수 흐름 (예외 격리)
- 기존 stage="filter" (별도 호출처 그대로)
- 다른 morning_filter 단계 (수급/거래량/체결강도/트렌드) — Phase B로 분리

## 과거 버그/교훈

- **2026-03-13 (screening_log INSERT OR IGNORE)**: 다중 테마 종목 UNIQUE 충돌 → INSERT OR IGNORE로 해결. 본 작업은 stage 추가로 충돌 가능성 낮음 (동일 stage 내 중복은 정상적 무시)
- **2026-02-06 (KST 타임존)**: `now_kst()` 사용 필수 — date.today() 금지
- **2026-04-24 (Phase A 매수 필터)**: 새 로직 배포 시 try/except 격리 — 본 작업도 동일 패턴

## MCP 활용 계획

- 배포 후 검증: `SELECT stage, COUNT(*), SUM(passed) FROM screening_log WHERE date >= '2026-05-04' GROUP BY stage`
- 갭 필터 분석: `SELECT date, COUNT(*), SUM(passed) FROM screening_log WHERE stage='gap_filter' GROUP BY date`

## 참고 문서

- `docs/improvements/2026-05_monthly.md` (분석 트리거)
- `memory/project_followup_investigations_2026_05_01.md` (조사 결과)
- `modules/CLAUDE.md` (모듈 규칙)
- `CLAUDE.md` (프로젝트 규칙)
