# CONTEXT — 종가베팅 라벨링 버그 수정

## 변경 이유
종가베팅 시스템 Phase 1 (알림형) 가동 중 라벨링 정합성 점검에서 다음이 드러남:
- **월요일 라벨링 잡(`run_label_yesterday`)이 일요일을 조회 → 금요일 후보 영구 누락**
- 5/7 라벨에 이미 `manual_backfill` 흔적이 있었으나 근본 원인 미수정 (수동 보정만 반복)
- 사용자 인식 "5/4 84% EV+"는 단일 폭등장 + 테스트 row 1건 오염 결과

## 현재 코드 상태 (수정 전)
**파일**: `closing_bet_system/main_orchestrator.py:397-420`
```python
async def run_label_yesterday(
    self,
    label_provider: Optional[Callable[[str], dict]] = None,
) -> dict:
    ...
    today = now_kst().date()
    yesterday = today - timedelta(days=1)  # ← 버그: 월요일 → 일요일
```

**영업일 헬퍼는 이미 존재**: `config.py:41 is_trading_day()` + 이미 import 되어 있음(`from config import now_kst, is_trading_day`).

## 핵심 스니펫 (수정 후)
```python
async def run_label_yesterday(
    self,
    label_provider: Optional[Callable[[str], dict]] = None,
    for_date: Optional[date_cls] = None,
) -> dict:
    today = now_kst().date()
    if for_date is not None:
        yesterday = for_date
    else:
        yesterday = today - timedelta(days=1)
        while not is_trading_day(yesterday):
            yesterday = yesterday - timedelta(days=1)
```

## 과거 버그 / 운영 흔적
- 5/7 candidate_labels.labeled_at 일부 row가 `2026-05-08` 시각(즉 manual_backfill)으로 채워짐 — 동일 버그를 수동 보정한 이력
- 5/4 `(테스트)` row(cid=19, ticker=005930)가 score=4 / EV+=1로 누적 통계 오염

## 조사 결과 (추가 4건)

### 라벨 자릿수 — 정상
- `label_provider._pct`: `round((value - base) / base, 6)` → **소수 ratio 반환** (예: 0.127527 = 12.75%)
- 5/4 평균 high 0.116 = 11.6% (폭등장), 5/7 평균 high 0.004 = 0.4% (약세장) — 정합
- bot-health-checker 리포트의 "+0.113%" 표기가 ratio를 % 단위로 잘못 표시한 것

### 우선주 005935 차단 미발동 — 의도된 정책
- `universe_filters.py:95`: `_BLOCK_PREF_STOCK_DEFAULT = False  # 점진 활성화 — 1주 관찰 후 True`
- `settings.yaml:107`: `pref_stock_block_enabled: false  # 1주 관찰 후 true 전환 권장`
- 룰(`_is_pref_stock`)은 정상 구현, 토글이 OFF — ETF 차단 false positive 0건 확인 후 활성화 예정
- **별도 작업 단위로 활성화 결정 (단위 2-9g 대상)**

### 알림 0건 — 의도된 설계
- `signal_score_engine.py:31-32` 코멘트:
  > `alert_min=7` 임에도 Phase 1 weighted max 가 7 이라 ALERT 도 만점만 가능 (매우 보수적).
  > 의도된 설계 — Phase 1 은 "데이터 수집 + 알림형" 이므로 ENTRY 미발생, 1-9 30건 누적 후 검토.
- 5/11 recommended 20건 모두 BELOW_THRESHOLD 등급 (정상)
- 15:35 daily_summary는 정상 발송 1회

## 영향 범위
- **운영 영향**: 5/8 19건 라벨 누락 (해당 작업으로 해소). 매주 월요일 재발 예상 (수정 후 차단).
- **다음 영업일 동작**: 5/12(화) 10:00 `run_label_yesterday` 자동 잡 — 5/11(월) 후보 23건 라벨링 (이번엔 정상 동작 예상)
- **백필 영향 받는 종목**: 5/8 후보 19건 (현대무벡스 1건 KIS 500 에러로 None 상태 유지 — 후속 단위에서 재시도)

## 누적 EV+ 통계 (정정 후)
| 일자 | 후보 | 라벨 | EV+ | 비율 |
|---|---|---|---|---|
| 5/4 (테스트 제거) | 18 | 18 | 15 | 83.3% |
| 5/7 | 18 | 18 | 4 | 22.2% |
| 5/8 (백필 후) | 19 | 18 | 15 | 83.3% |
| 5/11 | 23 | — | — | (라벨링 미도래) |
| **누적** | 55 | 54 | 34 | **63.0%** |

## 참고
- 메모리: `memory/project_closing_bet_system.md`, `memory/project_closing_bet_followups.md`
- PRD: `종가베팅_트레이딩_시스템_PRD_v2.0.md` 9-2 라벨 정의
- 관련 파일: `closing_bet_system/main_orchestrator.py`, `closing_bet_system/collectors/label_provider.py`, `closing_bet_system/storage/candidate_logger.py`
