# CHECKLIST — 종가베팅 라벨링 버그 수정

## 구현
- [x] `main_orchestrator.py:run_label_yesterday`에 `for_date: Optional[date_cls] = None` 인자 추가
- [x] 영업일 보정 분기 추가 (`while not is_trading_day(yesterday): yesterday -= timedelta(days=1)`)
- [x] `for_date` 명시 시 자동 보정 우회
- [x] py_compile 통과

## 검증
- [x] 5/8 19건 백필 실행 → 18/19 성공 (현대무벡스 KIS 500 에러로 1건 None)
- [x] 백필 결과 candidate_labels INSERT 확인 (labeled_at 채워짐)
- [x] 5/4 "(테스트)" cid=19 백업 + 삭제 (candidates / candidate_labels / candidate_features)
- [x] 누적 EV+ 통계 재집계 (54 라벨 / 34 EV+ = 63.0%)
- [x] `_archived_test_rows` 백업 테이블에 row_json JSON 보존 (롤백 가능)
- [x] 조사 4건 (자릿수 / 우선주 / 알림 / 5/8 누락 원인) 결론 확정

## 배포
- [x] systemd 재시작 불필요 (코드 변경은 다음 트리거 시 자동 반영, 본 잡은 매일 10:00)
- [x] 5/12(화) 10:00 `run_label_yesterday` 자동 잡 → 5/11(월) 23건 라벨링 예정 (모니터링 권장)
- [x] DB 변경은 트랜잭션 + 백업 보존 → 롤백 가능

## 문서 업데이트
- [skip] `docs/improvements/change_log.md` — 본 작업은 파라미터 변경이 아닌 버그픽스 + DB 정리. change_log 표 형식(파라미터/이전값/변경값)에 부적합 → 추가 생략 (CLAUDE.md "전략/파라미터 변경 시 필수 프로세스" 적용 대상 아님)
- [x] `memory/project_closing_bet_followups.md` 갱신 (description/현 위치/누적 라벨/페이퍼트레이딩 4개 섹션 갱신)
- [x] 작업 폴더 `active/closing-bet-label-bugfix/` → `completed/20260511_closing-bet-label-bugfix/` 아카이브

## 후속 작업 (별도 단위로 분리)
- [ ] **단위 2-9g**: 우선주 차단 토글 활성화 (`settings.yaml pref_stock_block_enabled: true`) — ETF 차단 false positive 0건 확인 후
- [ ] **현대무벡스 319400 라벨 재시도**: 5/8 백필에서 KIS 500 에러 → 별도 재시도 또는 yfinance 폴백
- [ ] **휴장일 가드 강화**: `run_label_yesterday` 시작 시 `today` 자체가 영업일인지 체크 (휴일이면 스킵)
- [ ] **알림 임계 재검토**: 누적 30건 게이트 통과(현재 69건) → ALERT/ENTRY decision 임계 재검토 (1-9)
