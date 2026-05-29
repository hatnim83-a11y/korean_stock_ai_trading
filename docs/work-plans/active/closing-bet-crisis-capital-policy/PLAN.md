# PLAN — 종가베팅 위기 시 부분 흡수 + 선별 강화

## 1. 목표
2026-05-27 종가베팅 phase1 후보 4건 전원 `price_cap` 자동 거부 사건을 구조적으로 해결.
MarketGuard DANGER 상태에서도 후보의 상위 점수 종목은 진입 가능하도록 자본 한도 일부 회복.
동시에 score 임계를 위기 시 상향(2→3)하여 선별성 강화.

## 2. 배경
- **트리거 사건**: 2026-05-27 phase1 후보 4개(한화오션 s=3, 삼성전기/SK하이닉스/현대모비스 s=2) 전원 price_cap
- **원인**: MarketGuard DANGER → `disable_absorb_on_crisis=true` → swing_idle 흡수 차단 → base_pool(933k) 만 사용 → per_stock 233k → ratio 0.175 적용 후 order 40,834원
- **결과**: 단가 134k+ 종목 1주조차 매수 불가
- **제안서**: `docs/improvements/20260529_closing_bet_capital_limit_crisis_policy.md` (사용자 승인 완료)
- **사용자 결정**: 권장 1안 (부분 흡수 + 선별 강화)

## 3. 변경 파일 (예상)
| 파일 | 변경 내용 |
|---|---|
| `closing_bet_system/config/settings.yaml` | `disable_absorb_on_crisis: true→false`, `crisis_absorb_ratio: 0.5` 신규, `score_threshold_crisis: 3` 신규 |
| `closing_bet_system/infra/fund_guard.py` | `GuardConfig.crisis_absorb_ratio` 필드 추가, `compute_capital_limit()` 위기 시 부분 흡수 분기 추가 |
| `closing_bet_system/execution/entry_executor.py` | `EntryExecutorSettings.score_threshold_crisis` 추가, `_select_phase1_candidates()` 시그니처에 `external_risk_active` 추가, `execute_phase1()` 호출 변경 |
| `tests/` 또는 `closing_bet_system/tests/` | 단위 테스트 신규 (capital_limit 위기 분기 / score_threshold 동적 적용) |

## 4. 구현 단계 (단위별)

### 단위 1: GuardConfig + compute_capital_limit 수정
1. `GuardConfig`에 `crisis_absorb_ratio: float = 0.0` 추가 (default 0.0 = 롤백 안전)
2. `from_settings()` 에서 settings.yaml 값 로드 + 범위 검증 (0.0~1.0)
3. `compute_capital_limit()` 분기 수정:
   - 기존: `external_risk_active and disable_absorb_on_crisis` → base만 반환
   - 신규: 흡수 분기 진입 후 `external_risk_active` 시 `swing_idle × crisis_absorb_ratio` 적용
4. debug_info에 `crisis_partial_absorb` 키 추가 (디버깅용)
5. 단위 테스트 추가: 정상 / DANGER+부분흡수 / DANGER+disable=true 폴백

### 단위 2: EntryExecutorSettings + score_threshold 동적 적용
1. `EntryExecutorSettings`에 `score_threshold_crisis: int = 2` 추가 (default 2 = 무변화)
2. `_load_settings_from_yaml()` 또는 dataclass 매핑에서 settings.yaml `entry_executor.score_threshold_crisis` 로드
3. `_select_phase1_candidates()` 시그니처에 `*, external_risk_active: bool = False` 추가
4. threshold 분기: `external_risk_active` 시 `score_threshold_crisis`, 아니면 `score_threshold`
5. `execute_phase1()` 호출 시 `external_risk_active` 전달
6. 단위 테스트 추가: NORMAL(threshold=2) / DANGER(threshold=3) / score=2 후보 차단 확인

### 단위 3: settings.yaml 변경
1. `fund.disable_absorb_on_crisis: true → false`
2. `fund.crisis_absorb_ratio: 0.5` 신규
3. `entry_executor.score_threshold_crisis: 3` 신규
4. 주석으로 본 변경의 근거(제안서 경로 + 활성화 일자) 명시

### 단위 4: code-tester 에이전트 검증 + 배포
1. code-tester 호출 → 심각/주의 이슈 확인
2. 이슈 발견 시 즉시 수정
3. py_compile + 기존 테스트 전수 PASS 확인
4. systemctl restart trading_system
5. 텔레그램 시작 메시지 확인

### 단위 5: 문서 갱신 + change_log
1. `docs/improvements/change_log.md` 1줄 추가 (before/after 명시)
2. `memory/project_closing_bet_followups.md` 갱신
3. `CLAUDE.md` 종가베팅 섹션 업데이트 (위기 시 부분 흡수 정책)
4. CHECKLIST.md 전 항목 체크

## 5. 완료 기준
- [ ] settings.yaml 3개 키 변경 확정
- [ ] fund_guard.py 단위 테스트 신규 3건 PASS
- [ ] entry_executor.py 단위 테스트 신규 3건 PASS
- [ ] code-tester 심각 이슈 0건
- [ ] systemctl restart 후 정상 기동 + 텔레그램 알림 수신
- [ ] change_log.md 1줄 추가
- [ ] 1주 관찰 지표(제안서 9장) 측정 시작

## 6. 롤백 계획
- **즉시 (긴급)**: settings.yaml 3줄 원복 → systemctl restart
- **부분**: capital_limit만 유지하고 score_threshold만 롤백 (또는 그 반대)
- **코드 롤백**: 단위별 git revert (단위 1/2/3 각각 별도 커밋 권장)

## 7. 위험 & 완화
| 위험 | 완화책 |
|---|---|
| 위기 시 진입 시 실제 손실 확장 (5/26 -4.13% 패턴) | crisis_absorb_ratio=0.5 (전체 흡수의 50%만) + score≥3 (약한 신호 차단) |
| crisis_absorb_ratio=1.0 으로 잘못 입력 시 위험 폭발 | from_settings 범위 검증 (0.0~1.0 clamp) |
| swing 시스템 자본 침해 | swing_used 산출 cost_basis 방식 그대로 유지 (변경 없음) |
| 표본 부족 (실거래 2건) | 1주 관찰 후 재평가, 필요 시 즉시 롤백 |

## 8. 일정 (예상)
- 단위 1 (fund_guard): 1세션
- 단위 2 (entry_executor): 1세션
- 단위 3+4 (settings.yaml + code-tester + 배포): 1세션
- 단위 5 (문서): 단위 4와 같은 세션
- **합계**: 3~4세션

## 9. 참고 문서
- 제안서: `docs/improvements/20260529_closing_bet_capital_limit_crisis_policy.md`
- CONTEXT: `docs/work-plans/active/closing-bet-crisis-capital-policy/CONTEXT.md`
- CHECKLIST: `docs/work-plans/active/closing-bet-crisis-capital-policy/CHECKLIST.md`
- 관련 메모리: `memory/project_closing_bet_followups.md`
