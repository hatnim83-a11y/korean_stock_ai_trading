# CHECKLIST — 트레일링 ATR cap + BE 바닥 보존

## 구현
- [x] config.py: `TRAILING_ATR_CAP_PCT=0.08` 신규 키 (ATR 섹션)
- [x] config.py: `TRAIL_BE_FLOOR_ENABLED=True` 신규 키
- [x] portfolio_monitor_v2.py `effective_trailing_pct()`: `if cap>0: atr_pct=min(atr_pct,cap)` 적용 + docstring 갱신
- [x] portfolio_monitor_v2.py `_check_stop_loss()`: 조건부 양보 (`not be_floor_on or trailing_stop>=stop_loss_price`)
- [x] portfolio_monitor_v2.py `__init__`: `self.trail_be_floor_enabled` 로드 + cap 오설정 경고 로그
- [x] portfolio_monitor_v2.py line 1338 경로: BE 청산 warning 로그 1줄 (trailing_stop is not None 가드)
- [x] tests/test_trailing_atr_cap_be_floor.py 신규 작성 (14 케이스)

## 검증 (신규 테스트 — 14건 전부 PASS)
- [x] C1: ATR폭 > cap → cap으로 클램프 (test_A1)
- [x] C2: ATR폭 == cap → cap 값 반환(경계) (test_A2)
- [x] C3: ATR폭 < cap → 클램프 미발동, 기존 max 동치 (test_A3)
- [x] C4: atr_at_buy=0 → fixed_pct 폴백 (test_A4)
- [x] C5: TRAILING_USE_ATR=False → fixed_pct (test_A5)
- [x] C6: cap=0 → 상한 없음(롤백), ATR 0 억제 아님 (test_A6)
- [x] C7: cap < fixed_pct(오설정) → max() 하한으로 고정값 보장 (test_A7)
- [x] C8: trailing_stop > stop_loss_price → return False(정상 회귀) (test_B8)
- [x] C9: trailing_stop == stop_loss_price → return False(경계) (test_B9)
- [x] C10: trailing_stop < stop_loss_price → 양보 해제, BE 손절 발동 (test_B10, 피에스케이 재현)
- [x] C11: trailing_active=True & trailing_stop=None → 양보 미진입 (test_B11, BE 로그 NoneType 버그 선제 발견·수정)
- [x] C12: TRAIL_BE_FLOOR_ENABLED=False → 무조건 양보(롤백 동치) (test_B12)
- [x] (보강) grace 기간 내 BE 손절 발동 (test_B13) + grace 내 정상 양보 (test_B14)
- [x] 기존 tests/test_monitor_state_residue.py 회귀 PASS (10/10)
- [x] `python -m py_compile config.py modules/trading_engine/portfolio_monitor_v2.py`
- [x] code-tester 에이전트 재검증 → 심각 0 (주의 1=로그레벨 info→warning 반영 완료)
- [x] 피에스케이 시뮬 확인: cap 8% → trail_pct 0.08 클램프, B 단독 → BE 바닥 보존

## 배포 (사용자 sudo 필요 — 대기)
- [ ] main 머지 (`git checkout main && git merge worktree-trailing-atr-cap-be-floor-proposal`)
- [ ] `.env` default(0.08/true) 사용 확인 — 별도 추가 불요(default ON). 보수적 운영 원하면 명시 추가
- [ ] 기존 프로세스 확인 `ps aux | grep main.py | grep -v grep`
- [ ] `sudo systemctl restart trading_system` (대덕전자/대주전자재료 트레일링 켜지기 전 우선)
- [ ] 재시작 후 로그 cap 적용/오류 없음 확인
- [x] **docs/improvements/change_log.md 1줄 추가** (TRAILING_ATR_CAP_PCT + _check_stop_loss BE 바닥)

## 문서 업데이트
- [x] docs/improvements/2026-06-16-focus-trailing.md 정정 메모(BE=first×0.99 / cap 소급 한계 / 독립 토글)
- [x] CLAUDE.md "v17 트레일링" 섹션에 cap + BE 바닥 규칙 추가 (트레일링 폭 공식 + 신규 섹션)
- [x] memory/MEMORY.md(글로벌 인덱스) 1줄 포인터 + project_trailing_atr_cap_be_floor.md 신규
- [ ] (배포 후) active/ → completed/20260616_trailing-atr-cap-be-floor/ 아카이브
