# PLAN — 종가베팅 시스템 실발주 활성화 (단위 2-4f + 2-5f 동시)

## 목표
종가베팅 시스템(`closing_bet_system/`)의 entry_executor + morning_exit dry_run 토글을 false로 전환하여 **2026-05-25(월)부터 실 발주** 시작. 1주 모니터링 후 dry-run vs 실전 정합성 평가.

## 배경
- **dry-run 5/15~5/22(영업일 6일) 자연 검증 완료**
  - 발화 4건 (5/18 010170/027360, 5/20 010170, 5/21 047040), MarketGuard 4상태 모두 검증
  - 라벨 완료 n=4: EV+ **4/4 (100%)**, realistic gross 평균 **+4.43%**, 비용 차감 net **+4.02%**
  - 누적 PnL (가상): notional 137,880원 → realistic +6,607원(+4.79%) / net +4.38%
- **활성화 게이트 4/4 통과** (settings.yaml:154-157, 175-178 정의 기준)
  1. ✅ 단위 2-4e dry_run 통합 단발 검증 (5/15)
  2. ✅ 단위 2-5 morning_exit_manager 완료 (5/16)
  3. ✅ 1주 dry_run 자연 검증 (5/15~5/22)
  4. ✅ 사용자 명시 승인 (2026-05-22)
- **누적 라벨 게이트**: 213건 (operational_review 30, auto_decision 100 모두 초과 통과)

## 사용자 결정 사항 (2026-05-22)
- **사이즈**: PRD 기본 `capital_ratio=0.10` 유지 (settings.yaml 변경 X, dry_run 토글만 전환)
- **순서**: entry + exit 동시 활성화 (settings.yaml 주석 권장안)
- **시점**: 2026-05-24(일) 늦은밤 토글 전환 → 2026-05-25(월) 15:18 첫 실 발주

## 변경 파일
| 파일 | 변경 |
|---|---|
| `closing_bet_system/config/settings.yaml:161` | `entry_executor.dry_run: true → false` |
| `closing_bet_system/config/settings.yaml:182` | `morning_exit.dry_run: true → false` |
| 코드 변경 | 없음 |

## 진입 사이즈 정량
- 1종목 실 진입: 총자산 × **0.10**(capital_ratio) × **0.25**(max_position_per_stock) × **0.7**(position_ratio) = **약 1.75%**
- MarketGuard CAUTION/DANGER ×0.5 시: **0.875%**
- MarketGuard CRISIS 시: **0% (전체 스킵)**
- phase1(정규장 15:18) 50% + phase2(동시호가 15:25) 50% 분할

## 구현 단계 (시계열)

### Phase 1: 사전 점검 (2026-05-23 토 ~ 2026-05-24 일 저녁)
1. CHECKLIST 검증 항목 전수 확인
2. KIS 계좌 잔액 확보 여부 확인
3. 종가베팅 텔레그램 봇 (`CLOSING_BET_TELEGRAM_BOT_TOKEN`/`CHAT_ID`) 활성 확인
4. fund_guard 한도 동작 단위 검증
5. 비상 정지 SOP 숙지
6. 5/21 라벨 누락 2건(cid=214 삼성전자우, cid=221 SK) 백필 (별도 작업)

### Phase 2: 토글 전환 (2026-05-24 일 늦은밤 ~ 23:30 KST)
1. `settings.yaml` 두 줄 수정 (dry_run: true → false)
2. py_compile + yaml syntax 검증
3. `sudo systemctl restart trading_system`
4. 재시작 후 종가베팅 8개 잡 등록 로그 확인
5. 텔레그램 "실발주 활성화 시작" 알림 발송 (수동)

### Phase 3: 실전 운영 1주 (2026-05-25 월 ~ 2026-05-29 금)
- 매일 자연 발화:
  - 09:01 emergency_stop (T-1 entered 종목 중 -1% 갭다운 즉시 매도)
  - 09:30 morning_exit (시초가 50% 매도)
  - 10:00 label_yesterday (T-1 후보 사후 라벨링)
  - 10:30 morning_force_close (잔여 50% 강제 청산)
  - 15:10 daily_pipeline (오늘 후보 수집)
  - 15:18 entry_pipeline phase1 (실 발주)
  - 15:25 entry_pipeline phase2 (실 발주)
  - 15:35 daily_summary (텔레그램)
  - 19:27 flow_reliability
- 매일 모니터링:
  - 텔레그램 매수/매도 알림 수신
  - daily_loss -3% 발동 시 추가 진입 중단
  - weekly_loss -5% 발동 시 매매 전체 중지

### Phase 4: 1주 평가 (2026-05-29 금 마감 후)
1. 1주 누적 net realistic 계산 vs dry-run 평균
2. 시뮬 정합성 비교 (옵션 C 3점)
3. EV+ 승률 vs dry-run 표본 비교
4. 슬리피지 실측 (settings.yaml cost.estimated_slippage=0.001 대비)
5. 종목 편향 검증 (010170 외 다양성)
6. 결정 분기:
   - net realistic ≥ +1.2% 달성 시 → 계속 운영, 다음 주 사이즈 증액 검토
   - net realistic < 0 시 → dry_run=true 복귀 + 원인 조사
   - daily/weekly 손실 한도 발동 시 → 즉시 중지

## 롤백 계획
- **긴급 중지**: `settings.yaml`에서 `entry_executor.enabled: false` + `morning_exit.enabled: false` → restart (잡 발화 차단)
- **dry-run 복귀**: 두 토글 `dry_run: true` 복원 + restart
- **systemd 차원**: `sudo systemctl stop trading_system` (스윙 시스템도 함께 중단되므로 최후 수단)
- **포지션 강제 청산**: 텔레그램 `/sell <ticker> <qty>` 또는 `/sellall` 수동 매도

## 완료 기준
- [ ] 5/24 일 23:30 KST 토글 전환 + restart 완료
- [ ] 5/25 월 15:18 첫 실 발주 발화 (텔레그램 알림 수신)
- [ ] 5/29 금까지 daily/weekly 손실 한도 미발동
- [ ] 5/29 금 마감 후 1주 누적 평가 완료
- [ ] 평가 결과 따라 계속 운영 / 롤백 / 사이즈 조정 결정

## 위험 평가
| 위험 | 영향 | 완화 |
|---|---|---|
| dry-run 표본 작음(n=4) | 평균 +4.38%이 outlier일 가능성 | capital_ratio=0.10 시작, 1종목 1.75% 한도 |
| 027360 +17.28% outlier | 평균 왜곡 | 제외 시에도 평균 +3.82%, 충분 |
| 종목 편향(010170 2회) | 다양성 부족 | 5/25 첫 진입 종목 다양성 관찰 |
| 라벨 누락 5건 패턴 | 사후 평가 데이터 부족 | 별도 단발 백필, retry 헬퍼 보강 |
| KIS API 장애 | submit_fail / 미체결 | fallback_to_next_candidate=true, 5분 cut |
| 시장 폭락(CRISIS) | 큰 손실 | MarketGuard 자동 스킵, daily/weekly limit |

## 참고 문서
- PRD: `종가베팅_트레이딩_시스템_PRD_v2.0.md`
- 단위 2-4: `docs/work-plans/active/closing-bet-unit-2-4-entry-executor/`
- 단위 2-5: `docs/work-plans/active/closing-bet-unit-2-5-morning-exit/`
- dry-run 분석: 본 세션 (5/15~5/22)
- 메모리: `memory/project_closing_bet_system.md`, `memory/project_closing_bet_followups.md`
