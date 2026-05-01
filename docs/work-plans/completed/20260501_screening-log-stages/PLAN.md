# PLAN: screening_log 다단계 로그 추가 (Phase A)

## 목표

`screening_log` 테이블에 **갭 필터** 단계와 **AI 검증** 단계의 통과/탈락을 기록하여, W19 (2026-05-08) `focus:gap_filter` 분석을 비롯한 사후 분석에 데이터 기반을 마련한다.

## 배경

`docs/improvements/2026-05_monthly.md` (라인 420, 431) "추가 발견(정보 공유)"에서 식별:
- screening_log.stage 컬럼이 `'filter'`만 존재 (2,985건 전체) → 모닝 갭 검사·AI 검증 단계는 별도 로그 부재
- W19 `focus:gap_filter` 트리거 전 데이터 소스 점검 선행 필요

정찰 결과:
- DB 마이그레이션 **불필요** — UNIQUE(date, stock_code, stage) 이미 존재 (database.py 스키마 검증)
- `save_screening_log` + `INSERT OR IGNORE` 중복 안전
- 호출 추가만으로 단계 분리 가능

## 구현 단계

### Phase A-1: morning_screener.py 갭 필터 로그
- 위치: `modules/morning_filter/morning_screener.py:filter_candidates()` 종료 직전
- 동작: 갭 필터 통과 종목 + 갭 탈락 종목을 `stage="gap_filter"`로 일괄 저장
- 컬럼:
  - `passed`: 통과 여부 bool
  - `score`: NULL (갭은 점수 없음)
  - `reject_reason`: gap_result.reason (갭 탈락 종목만)
  - `details_json`: `{"gap_pct": ..., "max_up": ..., "max_down": ...}` (선택)
- 저장 시점: filter_candidates 함수 끝, 결과 리턴 직전
- 예외 격리: try/except로 감싸 매수 흐름 영향 차단

### Phase A-2: verifier.py AI 검증 로그
- 위치: `modules/ai_verifier/verifier.py:verify_stocks_async()` 결과 병합 후 (라인 ~214)
- 동작: 검증된 모든 종목을 `stage="ai_verify"`로 일괄 저장
- 컬럼:
  - `passed`: ai_passed
  - `score`: ai_sentiment (0~10)
  - `reject_reason`: 탈락 시 사유 ("recommend=Hold/No" 또는 "low_score")
  - `details_json`: `{"recommend": ..., "confidence": ..., "target_return": ...}` (선택)
- 저장 시점: verified 리스트 완성 후, 리턴 직전
- 예외 격리: try/except로 감싸 매수 흐름 영향 차단

### Phase A-3: 검증 + 배포
- py_compile 통과
- code-tester 에이전트 검증
- systemd 재시작
- 5/4 (월) 자연 발생 시 새 stage 행 생성 확인

## 변경 파일

- `modules/morning_filter/morning_screener.py` (Phase A-1)
- `modules/ai_verifier/verifier.py` (Phase A-2)

## 미변경 (의도적)

- `database.py` — 스키마 그대로 (UNIQUE 이미 stage 포함)
- `modules/stock_screener/screener.py` — 기존 stage="filter" 유지
- 기타 morning_filter 단계 (수급/거래량/체결강도/트렌드) — Phase B로 분리

## 롤백 계획

- 추가한 `save_screening_log` 호출만 주석 처리 → systemd 재시작
- 스키마 변경 없으므로 DB 영향 없음
- 추가된 row는 INSERT OR IGNORE라 데이터 정합성 영향 없음

## 완료 기준

- [ ] morning_screener.py에 stage="gap_filter" 호출 추가, py_compile 통과
- [ ] verifier.py에 stage="ai_verify" 호출 추가, py_compile 통과
- [ ] code-tester PASS (심각 0건)
- [ ] systemd 재시작 후 정상 동작 확인 (PID 갱신, 스케줄 등록)
- [ ] 5/4 또는 첫 매매일 새 stage 행 생성 확인 (`SELECT stage, COUNT(*) FROM screening_log WHERE date >= '2026-05-04' GROUP BY stage`)

## 관련 문서

- 분석 트리거: `docs/improvements/2026-05_monthly.md`
- 후속 조사: `memory/project_followup_investigations_2026_05_01.md`
- W19 분석 사이클: 2026-05-08 (금) 17:45 KST 리마인더 알림
