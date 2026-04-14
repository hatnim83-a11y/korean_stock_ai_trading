# BE 손절 프리-트레일링 도입

## 목표
+5% 도달 시 손절가를 매수가 -1% (buy_price × 0.99)로 상향. +5~+8% 방어 공백 제거.

## 배경
- 오이솔루션(trade_reviews.id=27): max +5.44% → -3.36% 손절 마감. AI 교훈이 명시적으로 +5% 구간 방어 필요성 지적
- 전수조사(`docs/analysis/dip_recovery_trades.md`): Day2+ 저점 -5% 이하였던 이익 포지션 0건 — BE 도입이 조기 청산 위험 키우지 않음
- 플랜 파일: `/home/hatni/.claude/plans/parallel-jingling-lake.md`

## 구현 단계

### Step 1 — config.py 상수 추가
- [ ] TRAIL_BE_ENABLED (default True)
- [ ] TRAIL_BE_ACTIVATE_PCT (default 0.05)
- [ ] TRAIL_BE_STOP_PCT (default -0.01)

### Step 2 — portfolio_monitor_v2.py
- [ ] `__init__` 3개 설정 읽기 추가
- [ ] `_update_trailing_stop()` BE 블록 추가 (max_profit_rate 기준)

### Step 3 — 검증
- [ ] py_compile 2파일
- [ ] code-tester 에이전트 리뷰

### Step 4 — 배포
- [ ] 장마감 후 systemctl restart trading_system
- [ ] 다음 거래일 로그 🛡️ 확인

## 변경 파일
- `config.py`
- `modules/trading_engine/portfolio_monitor_v2.py`

## 접근 방식
- `max_profit_rate`(이미 DB 영속화) 기준으로 BE 트리거 → 재시작 시 자동 복원
- `if stop_loss_price < be_stop:` 단조성 가드 → L1/L2 덮어쓰기 방지
- `enable_profit_trailing`과 독립적 플래그로 방어 기능 항상 유지 가능

## 롤백
`TRAIL_BE_ENABLED=False` + 재시작 (코드 제거 불필요)

## 완료 기준
- py_compile 통과
- code-tester 심각 이슈 0
- 장마감 후 재시작 성공
- 다음 거래일 +5% 이상 포지션에서 BE 활성화 로그 관찰
