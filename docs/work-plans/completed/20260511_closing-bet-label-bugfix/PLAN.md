# 종가베팅 라벨링 버그 수정 + 검증 6항목 일괄 처리

## 작업 ID
`closing-bet-label-bugfix` — 2026-05-11

## 배경
- 2026-05-11(월) 봇 헬스 체크 도중 종가베팅 시스템 점검 위임
- bot-health-checker 결과 **심각 1건 + 주의 4건** 발견
- 사용자 결정: 라벨링 버그 즉시 수정 + 추가 4개 항목 일괄 처리

## 발견된 이슈
| 심각도 | 항목 | 위치 |
|---|---|---|
| 심각 | T+1 라벨링 잡 영업일 미반영 (월요일 → 일요일 조회) | `main_orchestrator.py:419-420` |
| 주의 | 5/8 후보 19건 라벨링 누락 (영구 NULL) | `candidates_labels` 미생성 |
| 주의 | 5/4 "(테스트)" 라벨 1건 누적 통계 오염 | `candidates.candidate_id=19` |
| 주의 | 5/7 vs 5/4 라벨 자릿수 의심 | `label_provider._pct` |
| 주의 | 우선주 005935 차단 미발동 | `universe_filters.py` 토글 |
| 주의 | 15:10 recommended 20건임에도 알림 0건 | `signal_score_engine` decision 임계 |

## 목표
1. 라벨링 버그 수정 — 영업일 헬퍼 사용, 백필용 `for_date` 인자 추가
2. 5/8 19건 수동 백필
3. 5/4 "(테스트)" row 격리 + 통계 재집계
4. 라벨 자릿수 검증 (단위 혼선 vs 데이터 정상 판별)
5. 우선주 차단 미발동 원인 추적 (코드 vs 정책)
6. 알림 0건 정책 확인 (의도 vs 버그)
7. 작업 문서화 + change_log + 아카이브

## 구현 단계
### Step 1: 코드 수정 (1파일)
- `closing_bet_system/main_orchestrator.py`
  - `run_label_yesterday(...)`에 `for_date: Optional[date_cls] = None` 인자 추가
  - 영업일 보정: `while not is_trading_day(yesterday): yesterday -= timedelta(days=1)`
  - `for_date` 명시 시 자동 보정 우회 (수동 백필용)

### Step 2: 5/8 백필 (DB 작업)
- 일회성 인라인 python 스크립트
- `closing_bet_system.collectors.label_provider.get_label` 직접 호출
- `CandidateLogger.log_labels()` 로 candidate_labels INSERT OR REPLACE

### Step 3: 5/4 "(테스트)" row 격리 (DB 작업)
- 신규 테이블 `_archived_test_rows` 생성 (백업용)
- `candidates`, `candidate_labels`, `candidate_features` 에서 candidate_id=19 삭제
- 트랜잭션 + 롤백 가능 백업 보존

### Step 4: 조사 (코드 읽기 only)
- 라벨 자릿수: `_pct` 산식 검증 → ratio 반환 정상
- 우선주: `_BLOCK_PREF_STOCK_DEFAULT = False` 점진 활성 정책
- 알림: `alert_min=7` weighted_max=7 보수 설계

## 변경 파일 목록
| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `closing_bet_system/main_orchestrator.py` | 수정 | run_label_yesterday 시그니처 + 영업일 보정 |
| `data/closing_bet.db` | 데이터 | candidate_labels 18건 INSERT + cid=19 archive/delete |
| `docs/improvements/change_log.md` | 추가 | 1줄 |
| `docs/work-plans/active/closing-bet-label-bugfix/*` | 신규 | 3문서 |
| `memory/project_closing_bet_followups.md` | 갱신 | 라벨링 버그 fix 표시 |

## 롤백 계획
- 코드: `git revert` (단일 커밋이면 직접)
- DB cid=19: `_archived_test_rows` 테이블에서 row_json 복원
- 5/8 백필: `DELETE FROM candidate_labels WHERE candidate_id BETWEEN 38 AND 56` (현대무벡스 47 제외 — 원래 NULL)

## 완료 기준
- [x] main_orchestrator.py 수정 + py_compile 통과
- [x] 5/8 18/19건 백필 완료 (1건 KIS 500 에러로 None)
- [x] 5/4 cid=19 격리 + 누적 EV+ 통계 재집계
- [x] 조사 4건 코드 위치 + 결론 확정
- [x] change_log.md 추가
- [x] 메모리 갱신
- [x] active → completed 아카이브
