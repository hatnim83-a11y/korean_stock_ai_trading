# PLAN — 단위 2-7a · Phase 2.5 백테스트 데이터 로더

## 목표
종가베팅 자체 후보 DB(`candidates` + `candidate_labels` + `candidate_features`)를 통합 로드하여 단위 2-7b 시뮬레이터/2-7c 분석 리포트가 사용할 단일 DataFrame을 반환하는 데이터 로더 모듈을 신설한다.

## 배경
- **30건 게이트 통과** (2026-05-08 기준 candidate_labels 누적 37건)
- Phase 2.5 백테스트 3 단위(2-7a → 2-7b → 2-7c) 진입 가능
- 본 단위는 **데이터 접근 계층만** — 시뮬/EV 계산은 후속 단위
- 자동매매 코드 0줄 (Phase 1 알림형 안전 유지)

## 모듈 신설
- `closing_bet_system/backtest/phase25_data_loader.py` (신규, ~180줄 예상)
  - Public: `load_phase25_dataset(start_date, end_date, **filters) -> pd.DataFrame`
  - 내부 헬퍼: SQL JOIN, 컬럼 정규화, dtype 변환

## 변경 파일
| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `closing_bet_system/backtest/phase25_data_loader.py` | 신규 | ~180줄 |
| `scripts/test_phase25_data_loader.py` | 신규 | ~300줄, 단위 테스트 12건+ |
| `closing_bet_system/backtest/__init__.py` | 수정 | export 추가 (선택) |

## 구현 단계

### Step 1. SQL JOIN 정의
```sql
SELECT
    c.candidate_id, c.trade_date, c.ticker, c.name,
    c.candidate_status, c.layer1_score, c.layer2_score, c.layer3_score, c.total_score,
    c.entry_price, c.rejection_reason,
    cf.<feature columns 18개>,
    cl.next_open_pct, cl.next_morning_high_pct, cl.next_morning_low_pct,
    cl.label_gap_up, cl.label_morning_exit, cl.label_stop_risk, cl.label_net_ev_positive,
    cl.labeled_at
FROM candidates c
LEFT JOIN candidate_features cf ON c.candidate_id = cf.candidate_id
LEFT JOIN candidate_labels cl ON c.candidate_id = cl.candidate_id
WHERE c.trade_date BETWEEN ? AND ?
  AND c.candidate_status IN ('recommended', 'entered')  -- default
ORDER BY c.trade_date ASC, c.candidate_id ASC;
```

### Step 2. 함수 시그니처
```python
def load_phase25_dataset(
    start_date: date | str,
    end_date: date | str,
    *,
    db_path: Optional[str] = None,        # default: data/closing_bet.db
    statuses: Optional[Iterable[str]] = None,  # default: ('recommended', 'entered')
    only_labeled: bool = False,           # True면 cl.candidate_id IS NOT NULL 필터
    only_features: bool = False,          # True면 cf.candidate_id IS NOT NULL 필터
) -> pd.DataFrame:
    ...
```

### Step 3. 컬럼 후처리
- `trade_date` → `pd.Timestamp` 변환
- bool 라벨 (label_gap_up 등) → `pd.BooleanDtype()` (None 보존)
- pct 컬럼 → float (`pd.to_numeric(errors='coerce')`)
- 누락 라벨 종목 마킹 컬럼 `is_labeled` 추가 (`cl.candidate_id IS NOT NULL`)

### Step 4. 메타 정보 dict 반환 옵션
- `return_meta=False` (기본): DataFrame만
- `return_meta=True`: `(df, meta)` — meta는 `{rows, labeled_rows, features_rows, date_range, statuses}`

### Step 5. 단위 테스트 12건+
- LD-1: 정상 범위(5/4~5/8) 로드 → rows 56건 (전체)
- LD-2: status='recommended' 필터 → rejected_filter 제외 검증
- LD-3: only_labeled=True → 37건 (5/8 시점)
- LD-4: only_features=True → 56건 (모든 candidate에 features)
- LD-5: 빈 결과 (먼 미래 날짜) → 빈 DataFrame, meta.rows=0
- LD-6: 잘못된 db_path → FileNotFoundError 명확
- LD-7: dtype 검증 (trade_date Timestamp / 라벨 BooleanDtype)
- LD-8: NULL 라벨 → pd.NA 보존 (False로 변환되지 않음)
- LD-9: candidate_id ASC 정렬 검증
- LD-10: return_meta=True 메타 dict 키 검증
- LD-11: features 컬럼 18개 모두 포함 검증
- LD-12: 회귀 — 5/4 19건 / 5/7 18건 / 5/8 19건 trade_date별 카운트 일치

## 완료 기준
1. `phase25_data_loader.py` py_compile 통과
2. 단위 테스트 12건+ PASS
3. code-tester 심각 이슈 0건
4. 통합 단발: 5/4~5/8 데이터 로드 → 56행 × 30+컬럼 DataFrame 정상 반환
5. 단위 2-7b 시뮬레이터가 호출할 수 있는 안정 인터페이스 확정

## 롤백
- 단위 모듈 단독이라 시스템 영향 없음 (Phase 1 알림형 회로 무관)
- `git revert <commit>` 또는 신규 파일 삭제로 즉시 롤백

## 위험
- **매우 낮음** — 읽기 전용 데이터 로더, 자동매매 X, 기존 모듈 의존성 없음
- 기존 backtester 패턴(`modules/backtester/data_loader.py`)과 분리되어 충돌 없음

## 다음 단위 (별도)
- **단위 2-7b** 시뮬레이터: PRD 12-1 라벨 4종 + 12-2 EV 계산식 → 가상 PnL
- **단위 2-7c** 분석 리포트: walk-forward EV / win rate / Sharpe / 점수 구간별 분포

## 비범위 (본 단위 X)
- 시뮬레이션/EV 계산 (단위 2-7b)
- walk-forward 분석 (단위 2-7c)
- 자동매매 진입 결정 (단위 2-8)
- ML 학습용 train/test split (Phase 3+)
