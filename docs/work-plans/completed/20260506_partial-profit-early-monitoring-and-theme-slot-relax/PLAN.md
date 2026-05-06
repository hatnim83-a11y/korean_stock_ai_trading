# PLAN — 09:00 조기 모니터링 + 테마 슬롯 조건부 상향

작성일: 2026-05-06
승인: 사용자 (플랜 모드)

## 목표
1. 09:00~09:25 모니터링 공백 제거 → 분할익절(L1 +8%/L2 +15%/L3 +25%) 시그널 누락 방지
2. 테마 슬롯 한도(2)로 빈 슬롯 낭비 방지 → 후보 부족 시 한도 3까지 조건부 상향 (최대 5종목 충원)

## 배경
- 2026-05-06 운영 중 삼성전자/네페스아크가 09:00 직후 급등. 09:25까지 모니터 미가동이라 만약 25분간 올랐다 빠지면 분할익절을 놓칠 위험 확인.
- 같은 날 신규 종목이 동일 테마 한도(2)로 매수 탈락하면서 슬롯이 비는 사례 발생.

## 핵심 설계 (마스터 플랜 `/home/hatni/.claude/plans/kst09-00-zazzy-parasol.md` 참조)

### 변경 1: 09:00 모니터 시작 + SellLock
- `start_monitoring()` idempotent 화 — 이미 가동 중이면 신규 종목만 add_position + websocket 동적 subscribe
- 09:00 신규 잡 + 09:26 잡 유지(monitoring_register 역할로 변경)
- `modules/trading_engine/sell_lock.py` 신규 — `acquire/release/is_locked/clear_all/snapshot`
- 매도 잡 3종(09:00 midweek profit, 09:10 midweek loss, 09:15 hold_period) 각각 acquire 패턴 적용
- 모니터 측(분할익절/손절/트레일링) 진입부 `acquire() + 실패 skip` 패턴
- release 시점: 매 매도 종료 시점 X, 15:30 monitoring_stop에서 `clear_all()`

### 변경 2: 테마 슬롯 2-pass
- `_apply_diversity_filter()` 순수 함수 추출 (main.py 또는 별도 모듈)
- 1차 패스(한도 2) → 빈 슬롯 + 후보 부족 시 2차 패스(한도 3)
- 시나리오 D(반도체×5, 빈슬롯 5)는 3종목에서 멈춤 (분산 우선 — 사용자 확정)

## 변경 파일
| 파일 | 변경 |
|------|------|
| `config.py` | 신규 상수 3개 |
| `modules/trading_engine/sell_lock.py` | **신규** |
| `modules/trading_engine/portfolio_monitor_v2.py` | start_monitoring idempotent + 3곳 lock 가드 |
| `main.py` | 매도 3종 lock acquire + Phase 5.5 헬퍼 추출 + summary 표시 + scheduler 콜백 |
| `scheduler.py` | 09:00/09:26 두 잡 + monitoring_stop에서 clear_all |
| `tests/test_sell_lock.py` | **신규** (5개 케이스, 평문 assert) |
| `tests/test_diversity_filter.py` | **신규** (시나리오 A~I) |

## 구현 단계 (의존성 순)
1. **인프라**: config 상수 + sell_lock 모듈 + sell_lock 단위 테스트
2. **변경 1 모니터**: portfolio_monitor_v2 idempotent + 3곳 lock 가드
3. **변경 1 매도잡**: main.py 매도 3종 함수 acquire 패턴
4. **변경 1 스케줄러**: scheduler.py 09:00 잡 + monitoring_stop에서 clear_all
5. **변경 2 알고리즘**: _apply_diversity_filter 헬퍼 + 단위 테스트
6. **변경 2 호출부**: main.py Phase 5.5 헬퍼 호출 + summary 변경
7. **검증**: code-tester 에이전트 + py_compile + dry-run
8. **배포**: 문서 갱신 + systemctl restart + 다음 거래일 09:00~09:35 관찰

## 롤백
- `PARTIAL_PROFIT_EARLY_MONITORING_ENABLED=False` → systemctl restart → 09:26 legacy
- `THEME_SLOT_RELAXATION_ENABLED=False` → 헬퍼에 `relax_max=None` → 1-pass 동일

## 완료 기준
- CHECKLIST.md 모든 항목 [x]
- 단위 테스트 전부 통과 (sell_lock 5개, diversity_filter 9개)
- code-tester 심각/주의 0건
- 다음 거래일 09:00~09:35 로그에서 [SellLock] 메시지 race 0건 확인
- `docs/improvements/change_log.md` 1줄 추가
- `memory/MEMORY.md` 갱신 (테마 슬롯 정책, 스케줄 표, SellLock)
