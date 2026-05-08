# CHECKLIST — 단위 2-7a · Phase 2.5 백테스트 데이터 로더

## 사전 확인
- [x] candidate_labels 누적 30+ 검증 (5/8 기준 37건)
- [x] candidate_features 누적 candidates와 1:1 일치 확인 (56/56)
- [x] 단위 2-9f 등 후속 단위 아카이브 완료 (작업 A 완료)
- [x] PLAN.md / CONTEXT.md 사용자 승인

## 구현 항목

### Step 1 — `closing_bet_system/backtest/phase25_data_loader.py` 신설
- [x] 모듈 docstring (PRD 11-1/12-1 참조 + Phase 2.5 위치 명시)
- [x] `_PROJECT_ROOT` sys.path 패턴 (기존 backtest 모듈과 동일)
- [x] `_DEFAULT_DB_PATH = "data/closing_bet.db"` 모듈 상수
- [x] `_DEFAULT_STATUSES = ("recommended", "entered")` 모듈 상수
- [x] `_FEATURE_COLUMNS = (...)` candidate_features 18컬럼 명시 (스키마 검증용)
- [x] `_LABEL_COLUMNS = (...)` candidate_labels 7컬럼 명시
- [x] py_compile 통과

### Step 2 — `load_phase25_dataset()` 메인 함수
- [x] 시그니처: `(start_date, end_date, *, db_path=None, statuses=None, only_labeled=False, only_features=False, return_meta=False)`
- [x] 입력 검증: 날짜 파싱 (date / str / pd.Timestamp 허용)
- [x] db_path 존재 검증 (`Path.exists()`) → FileNotFoundError 명확 메시지
- [x] SQL JOIN 쿼리 생성 (LEFT JOIN candidate_features + candidate_labels)
- [x] `pd.read_sql_query()` 호출 + parameterized binding (SQL injection 방지)
- [x] DB connection 항상 close (try/finally)

### Step 3 — 컬럼 후처리
- [x] `trade_date` → `pd.to_datetime()` 변환
- [x] 라벨 4종 → `pd.BooleanDtype()` (None 보존, False로 강제 변환 X)
- [x] pct 컬럼 → float (`pd.to_numeric(errors='coerce')`)
- [x] `is_labeled` 파생 컬럼 (`cl.candidate_id IS NOT NULL`)
- [x] `is_featured` 파생 컬럼 (`cf.candidate_id IS NOT NULL`)

### Step 4 — 메타 정보 (옵션)
- [x] `return_meta=True` 시 `(df, meta)` 튜플 반환
- [x] meta 키: rows, labeled_rows, features_rows, date_range, statuses, generated_at

### Step 5 — `closing_bet_system/backtest/__init__.py` export
- [x] `from .phase25_data_loader import load_phase25_dataset` (선택)

## 검증 항목

### 단위 테스트 — `scripts/test_phase25_data_loader.py` 12건+
- [x] **LD-1**: 5/4~5/8 정상 로드 → rows=56
- [x] **LD-2**: statuses=('recommended',) → rejected_filter 제외 (rows < 56)
- [x] **LD-3**: only_labeled=True → labeled_rows=37 (5/8 시점)
- [x] **LD-4**: only_features=True → 56건 (모든 candidate에 features)
- [x] **LD-5**: 빈 결과 (2030-01-01) → 빈 DataFrame, meta.rows=0
- [x] **LD-6**: 잘못된 db_path → FileNotFoundError 명확 메시지
- [x] **LD-7**: dtype 검증 (`trade_date` Timestamp / 라벨 `BooleanDtype`)
- [x] **LD-8**: NULL 라벨 → `pd.NA` 보존 (False로 변환 X)
- [x] **LD-9**: 정렬 검증 (trade_date ASC, candidate_id ASC)
- [x] **LD-10**: return_meta=True → 튜플 반환 + 키 6종
- [x] **LD-11**: features 컬럼 18개 모두 포함 (스키마 정합)
- [x] **LD-12**: trade_date별 카운트 일치 (5/4=19, 5/7=18, 5/8=19)

### 통합 검증 (단발)
- [x] `venv/bin/python -m closing_bet_system.backtest.phase25_data_loader` 실행 (선택)
- [x] 5/4~5/8 로드 → DataFrame.shape = (56, 30+) 확인
- [x] candidate_id 31 (셀트리온, 수동 백필) 정상 포함 + label_gap_up=True
- [x] meta dict 출력 정상

### code-tester 검증
- [x] code-tester 에이전트 호출 (신규 1개 + 테스트 1개 대상)
- [x] 심각 이슈 0건
- [x] 하드코딩 검사 (DB 경로 / 컬럼명 모듈 상수화)
- [x] py_compile + 기존 backtest 모듈 회귀 영향 없음

## 배포 항목
- [x] systemd 무관 (오프라인 분석 모듈) → 재시작 불필요
- [x] 변경 파일 git stage + commit
  - `closing_bet_system/backtest/phase25_data_loader.py` (신규)
  - `scripts/test_phase25_data_loader.py` (신규)
  - `closing_bet_system/backtest/__init__.py` (수정 시)
- [x] git push

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-7a)
- [x] `memory/project_closing_bet_system.md` 단위 2-7a 단락 추가
- [x] `memory/project_closing_bet_followups.md` 후속 단위 우선순위 갱신 (2-7a 완료 → 2-7b 진입)
- [x] `memory/MEMORY.md` 인덱스 description 갱신

## 완료 게이트 (선언 전 체크)
- [x] 사전 확인 항목 전부 `[x]`
- [x] Step 1~5 구현 항목 전부 `[x]`
- [x] 단위 테스트 12건+ PASS
- [x] 통합 검증 전부 `[x]`
- [x] code-tester 통과
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 전부 `[x]`
- [x] active → completed/20260508_closing-bet-unit-2-7a-data-loader/ 아카이브

## 비범위 (명시)
- 시뮬레이션 / EV 계산 → **단위 2-7b** (별도 단위)
- walk-forward 분석 → **단위 2-7c** (별도 단위)
- 자동매매 진입 결정 → **단위 2-4/2-5** (100건 게이트 후)
