# CONTEXT — 단위 2-7a · Phase 2.5 백테스트 데이터 로더

## 변경 이유
30건 게이트 통과(2026-05-08 candidate_labels 누적 37건) → Phase 2.5 백테스트 진입 가능. 시뮬레이터/분석 리포트 신설 전 통합 데이터 접근 계층 분리 (단일 책임 원칙).

## 현재 DB 상태 (2026-05-08 KST 17:55 시점)

### data/closing_bet.db 테이블
| 테이블 | 행수 | 비고 |
|---|---|---|
| `candidates` | 56 | 5/4 19 + 5/7 18 + 5/8 19 |
| `candidate_features` | 56 | 1:1 매칭 (모든 후보에 features) |
| `candidate_labels` | 37 | 5/4 19 + 5/7 18 (+ 5/8 백필 1) |
| `flow_data_reliability` | 0 | 단위 2-3 진입 시 활용 |
| `orderbook_snapshots` | 59 | 5/4 시작, 7~10일 누적 후 단위 2-3 게이트 |

### candidates 컬럼 (22)
candidate_id (PK), trade_date, ticker, name, candidate_status, rejection_reason,
layer1_score, layer2_score, layer3_score, external_risk_score, total_score,
entry_price, entry_amount, entry_time, exit_price, exit_time,
buy_commission, sell_commission, transaction_tax, estimated_slippage, net_pnl_pct, created_at

### candidate_features 컬럼 (19, PK candidate_id)
- **Layer 1**: inst_net_buy_estimated, foreign_net_buy_3d, program_net_buy_change, closing_flow_concentration
- **Layer 2**: close_strength, upper_shadow_atr, last_30min_vwap_position, closing_buy_sell_ratio, volume_surprise, atr_overheat
- **Layer 3**: days_from_52w_high, relative_strength_5d, theme_leadership_rank
- **Market**: kospi_above_200ma, vkospi, foreign_5d_cumulative, us_futures_change, usd_krw_change

### candidate_labels 컬럼 (9, PK candidate_id)
next_open_pct, next_morning_high_pct, next_morning_low_pct,
label_gap_up, label_morning_exit, label_stop_risk, label_net_ev_positive, labeled_at

## 핵심 스니펫 (참조)

### `closing_bet_system/storage/candidate_logger.py:201` log_features 패턴
```python
def log_features(self, candidate_id, layer1, layer2, layer3, market_regime):
    """피처 스냅샷 INSERT — candidate_features 18컬럼."""
    columns = list(layer1.keys()) + list(layer2.keys()) + list(layer3.keys()) + list(market_regime.keys())
    values = list(layer1.values()) + ...
    cur.execute(f"INSERT INTO candidate_features ({col_list}) VALUES ({placeholders})", values)
```

### `closing_bet_system/backtest/daily_proxy_backtest.py` 기존 패턴
- argparse 진입점
- pandas DataFrame 작업
- `_PROJECT_ROOT` sys.path 패턴
- `from modules.backtester.data_loader import DataLoader, MarketData` (Pre-Phase 1 데이터 로더 참조)

### PRD 12-1 라벨 정의 (시뮬레이터에서 사용 예정, 본 단위는 컬럼 보존만)
| 라벨 | 계산 | 성공 기준 |
|---|---|---|
| Gap-up | 익일 시가 / 진입가 - 1 | ≥ +0.6% |
| Morning Exit | 09:00~09:30 고가 / 진입가 - 1 | ≥ +1.2% |
| Stop Risk | 09:00~09:30 저가 / 진입가 - 1 | ≤ -1.0% |

### PRD 12-2 EV 계산식 (단위 2-7b에서 구현)
```
EV = P(Morning Exit 도달) × 평균 익절 수익률
   - P(Stop Risk 도달) × 평균 손실률
   - 거래비용 (왕복 0.5%)
   - 슬리피지 (왕복 0.2%)
```

## 영향 범위
- **신규 모듈 단독** — 기존 시스템 영향 없음
- main_orchestrator / collectors / scoring 변경 없음
- Phase 1 알림형 회로 무관 (자동매매 0줄)
- `data/closing_bet.db` 읽기 전용 접근

## 과거 패턴 (재사용)
- `closing_bet_system/storage/db.py:90` connect() / close() — 종가베팅 DB 연결 패턴
- `pd.read_sql_query(query, conn, params=...)` — pandas 표준 패턴
- `pd.BooleanDtype()` — None/True/False 3-state 유지 (단순 bool은 None을 False로 강제 변환)

## 기존 인프라 의존
- `pandas` 1.x+ 설치됨
- `sqlite3` (Python 표준 라이브러리)
- 외부 API 호출 없음 (오프라인 분석 전용)

## 검증 데이터 (단위 테스트용)
- 5/4 trade_date: 19건 (recommended 15 + rejected_filter 4)
- 5/7 trade_date: 18건 (recommended 18, 모두 라벨링됨)
- 5/8 trade_date: 19건 (recommended 16 + rejected_filter 3, 라벨링은 5/11 예정)
- candidate_id 31 (셀트리온): 5/8 수동 백필 (`labeled_at` 끝에 `manual_backfill` 마커)

## 비범위 명시 (혼동 방지)
- **본 단위는 데이터 로더만**: 시뮬레이션/EV/walk-forward는 단위 2-7b/2-7c
- 자동매매 진입 결정은 단위 2-4/2-5 (별도 100건 게이트 후)
- ML 학습용 데이터셋 정규화는 Phase 3+ (LightGBM 단계)
