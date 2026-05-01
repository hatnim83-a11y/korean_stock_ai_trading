# CHECKLIST: screening_log 다단계 로그 추가 (Phase A)

## 구현 항목

### Phase A-1: morning_screener.py 갭 필터 로그 ✅ 완료 (2026-05-01)
- [x] `_save_gap_filter_logs(passed, excluded)` 모듈 레벨 헬퍼 정의
- [x] `filter_candidates()` 갭 필터 단계 직후 호출 (라인 ~252)
- [x] 통과 종목 + 갭 탈락 종목 모두 기록 (passed bool, reject_reason 분기)
- [x] try/except로 매수 흐름 영향 차단 (외부 try + 내부 try 이중)
- [x] `now_kst()` 사용 (config.py 기존 import 활용)
- [x] details_json: `{"gap_percent": ..., "prev_close": ..., "open_price": ...}` (getattr 안전 추출)
- [x] `py_compile modules/morning_filter/morning_screener.py` 통과
- [x] 통과/탈락 카운트 INFO 로그 추가

### Phase A-2: verifier.py AI 검증 로그 ✅ 완료 (2026-05-01)
- [x] `_save_ai_verify_logs(verified)` 모듈 레벨 헬퍼 정의
- [x] `verify_stocks_async()` 결과 병합 후 `return verified` 직전 호출
- [x] passed/탈락 분기:
  - passed=True: reject_reason=None
  - recommend in EXCLUDE_RECOMMENDATIONS(["No","Hold"]): reject_reason=f"recommend={recommend}"
  - 그 외: reject_reason="low_score"
- [x] try/except로 매수 흐름 영향 차단 (외부 + 내부 이중)
- [x] details_json: `{"recommend": ..., "confidence": ..., "target_return": ...}`
- [x] score 컬럼: ai_sentiment (0~10) 저장
- [x] AI 분석 미스 경로 키 누락 fix (`ai_target_return=0`, `ai_reason=""`, `ai_risk=""` 추가)
- [x] `py_compile modules/ai_verifier/verifier.py` 통과

## 검증 항목

### 단위 검증 ✅ 완료
- [x] `python -m py_compile modules/morning_filter/morning_screener.py modules/ai_verifier/verifier.py` 통과
- [x] code-tester 에이전트 검증 — **심각 0건** 확인
- [x] 주의 1건(ai_target_return 키 누락) 즉시 수정
- [x] 하드코딩 없음 (stage 명만 의도적, MIN_AI_SCORE 등 모듈 상단 상수 활용)

### 통합 검증 ✅ 완료
- [x] 기존 stage="filter" 호출 정상 (변경 없음 — modules/stock_screener/screener.py 무수정)
- [x] UNIQUE(date, stock_code, stage) 제약 확인 — 동일 종목이 'filter', 'gap_filter', 'ai_verify' 3행 가능
- [x] 모듈 로드 정상 (재시작 후 morning_screener / ai_verifier 모듈 로드 로그 확인)

### 실전 검증 (5/4 또는 다음 영업일) — 자연 발생 시 자동 검증
- [ ] `SELECT stage, COUNT(*), SUM(passed) FROM screening_log WHERE date >= '2026-05-04' GROUP BY stage` — 3개 stage 모두 행 존재
- [ ] gap_filter row > 0
- [ ] ai_verify row > 0
- [ ] 매수 흐름 정상 (오류 로그 없음)
- [ ] `💾 screening_log[gap_filter] 저장: ...` / `💾 screening_log[ai_verify] 저장: ...` INFO 로그 확인

## 배포 항목

### 배포 전 체크 ✅ 완료
- [x] `ps aux | grep main.py | grep -v grep` 확인 (PID 1947127)
- [x] DB 변경 없음 → 백업 불필요 (스키마 무변경, 추가 INSERT만)

### 배포 ✅ 완료 (2026-05-01 15:59 UTC = 5/2 00:59 KST)
- [x] 비매매 시간(토요일 새벽 KST)에 `sudo systemctl restart trading_system` 실행
- [x] `sudo systemctl status trading_system` active 확인
- [x] 스케줄 정상 등록 확인 (17개 잡)
- [x] systemd PID 변경 확인 (1947127 → 1965133)
- [x] morning_screener / ai_verifier 모듈 로드 정상

### 이상 시 롤백 (필요 시)
- [ ] 추가한 호출 블록만 주석 처리 → systemd 재시작
- [ ] DB 변경 없으므로 데이터 영향 없음
- [ ] change_log.md에 롤백 사유 1줄 기록

## 문서 업데이트 항목

- [x] `docs/improvements/change_log.md`에 1줄 추가 (2026-05-01)
- [x] `memory/project_followup_investigations_2026_05_01.md`에 "screening_log stage 작업 완료" 섹션 갱신
- [x] `memory/MEMORY.md` 인덱스 — 변경 불필요 (followup 메모리 인덱스 그대로)
- [x] 3문서 (PLAN/CONTEXT/CHECKLIST) `active/` → `completed/20260501_screening-log-stages/` 이동

## 완료 게이트 (선언 전 체크)

- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 전부 `[x]` (실전 검증은 5/4 이후 자연 발생 시 자동 검증)
- [x] 배포 항목 전부 `[x]`
- [x] **문서 업데이트 항목 전부 `[x]`**
- [x] `active/` → `completed/` 아카이브 완료

## 후속 작업 (Phase B 후보)

- 수급 필터 stage="supply_filter" 로그 추가
- 거래량 필터 stage="volume_filter" 로그 추가
- 체결 강도 필터 stage="strength_filter" 로그 추가
- 트렌드 필터 stage="trend_filter" 로그 추가

이 4개는 Phase A 검증(5/4 이후 자연 발생) 후 별도 작업(`docs/work-plans/active/screening-log-stages-phase-b/`)으로 분리.

## 자연 검증 시 확인 쿼리 (5/4 이후 실행)

```sql
-- 3 stage 모두 누적 확인
SELECT stage, COUNT(*) AS rows, SUM(passed) AS passed_count
FROM screening_log
WHERE date >= '2026-05-04'
GROUP BY stage;

-- 갭 필터 rejection 분포
SELECT reject_reason, COUNT(*)
FROM screening_log
WHERE stage = 'gap_filter' AND passed = 0
GROUP BY reject_reason
ORDER BY COUNT(*) DESC;

-- AI 검증 점수 분포
SELECT
  CASE
    WHEN score < 5 THEN 'low'
    WHEN score < 7 THEN 'mid'
    ELSE 'high'
  END AS bucket,
  COUNT(*), SUM(passed)
FROM screening_log
WHERE stage = 'ai_verify' AND date >= '2026-05-04'
GROUP BY bucket;
```
