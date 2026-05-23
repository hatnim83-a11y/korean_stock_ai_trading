# CLAUDE.md - 프로젝트 규칙

## 서비스 운영 규칙

### 반드시 systemd로만 구동
- 실행: `sudo systemctl start trading_system`
- 중지: `sudo systemctl stop trading_system`
- 재시작: `sudo systemctl restart trading_system`
- 상태: `sudo systemctl status trading_system`
- **절대 `nohup python main.py &` 또는 백그라운드 직접 실행 금지**
- 수동 테스트는 `python main.py --manual --test --real` (포그라운드, 1회성)만 허용

### 이중 실행 방지
- 서비스 시작/재시작 전 반드시 기존 프로세스 확인: `ps aux | grep main.py | grep -v grep`
- systemd 외 프로세스가 있으면 먼저 kill 후 서비스 시작
- PID 파일(`trading_system.pid`) 잔여 시 삭제 후 시작

## 계정 관리
- `.env` 파일에 활성/대기 계정 구분 (주석 처리로 전환)
- KIS API 토큰: 앱키당 1분에 1회 발급 제한 — `kis_api.py`와 `kis_order_api.py`가 `_shared_token`으로 공유

## 코드 규칙
- KIS API 응답 파싱 시 `_safe_int()`/`_safe_float()` 사용 (빈 문자열 방어)
- pandas 값 → float 변환 전 `pd.isna()` 체크 필수
- KST 타임존: `datetime.now()` 대신 `from config import now_kst` 사용 (서버가 UTC)

## 매도 동시성 잠금 (SellLock — 2026-05-06 도입)
- **모듈**: `modules/trading_engine/sell_lock.py` (싱글톤 `sell_lock`, threading.Lock 기반)
- **목적**: 09:00 조기 모니터링 도입 후 분할익절/손절/트레일링 모니터와 09:00/09:10/09:15 매도 잡(midweek/hold_period)이 동일 종목을 동시 매도 발주하는 race 봉쇄
- **패턴**: 매도 진입 직전 `sell_lock.acquire(stock_code, owner)` → True면 진행, False면 다른 매도 경로가 처리 중이므로 해당 종목 스킵
- **release 정책**: 매도 함수 종료 시 release하지 **않음** → 15:30 `stop_monitoring()`에서 `clear_all()` 일괄 해제 (race window 봉쇄). 같은 거래일 내 한 종목에 두 매도 잡이 발화하지 않으므로 누수 위험 없음
- **무력화**: `PARTIAL_PROFIT_EARLY_MONITORING_ENABLED=False` + systemctl restart → acquire 호출 자체가 분기되어 NO-OP, 09:26 legacy 시작

## 분할 진입 + 불타기 + ATR 트레일링 (v17 — 2026-05-20 코드, 2026-05 활성화)

### 핵심 정책 분기 (절대 헷갈리지 말 것)
- **익절 트리거** (`_check_and_execute_partial_profit`) → **`avg_buy_price`** 기준 (수익 실현 직관)
- **손절/BE/트레일링/2차 진입 트리거** → **`first_buy_price`** 기준 (평균단가 함정 회피)
- `pos.buy_price` 직접 참조 금지 (deprecated). `pos.first_buy_price` 또는 `pos.avg_buy_price` 명시 사용
- 코드 변경 시 의도가 "리스크 트리거"인지 "손익 보고/실현"인지 확인 후 기준 선택

### 운용 파라미터
- 1차 진입: 50% (`TRANCHE_FIRST_RATIO=0.5`)
- 2차 진입(불타기): 50%, 트리거 **+5% first 기준** (`PYRAMID_TRIGGER_PCT=0.05`)
- 분할 익절: **+12%/+20%/+30% avg 기준** × 25/20/20% (잔여 35%)
- 트레일링 폭: `max(L1=4%/L2=3%/L3=2%, 2.0×ATR(14)/first_buy_price)`

### 토글 (`.env` 또는 환경변수)
- `TRANCHE_ENTRY_ENABLED` (default **False** 안전): True=1차 50% + 2차 트리거 활성
- `DRY_RUN_PYRAMID` (default **True** 안전): True=2차 진입 시뮬레이션(실발주 차단)
- `PARTIAL_PROFIT_BASE` (default `'avg'`): 'first' 토글로 익절 기준 기존 복귀
- `TRAILING_USE_ATR` (default True): False 토글로 고정값만 사용

### 동시성 — OrderLock 우선순위 (Sell > Buy)
- **모듈**: `modules/trading_engine/buy_lock.py` (싱글톤 `buy_lock`, SellLock 동일 패턴)
- **우선순위 가드**: `_check_all_positions` 흐름에서 분할익절 발화 시 그 사이클 2차 진입 skip → 다음 사이클 재평가
- **release**: 발주 완료 후 try/finally 1차 해제 + 15:30 `clear_all()` 2차 안전망
- SellLock 동일 종목 점유 중이면 BuyLock acquire 자체 skip

### 종가베팅 충돌 가드 (2026-05-23 hotfix — closing_bet absorb_swing_idle 정합)
- **가드 A (시간 차단)**: v17 2차 진입은 **15:15 이후 차단** — 종가베팅 phase1(15:18)/phase2(15:25) 보호
- **가드 B' (자본 한도)**: v17 2차 진입에 **swing_capital_pool(총자본 × SWING_CAPITAL_RATIO=0.9) 한도** 적용 — `target_amount = min(target_amount, swing_pool - swing_used)`. swing_used = `Σ(p.first_buy_price × p.shares)` (fund_guard.compute_capital_limit cost_basis 식과 동일)
- **위치**: `_check_and_execute_pyramid_in()` 1-B(시간) + 7-B(자본). 모니터 진입 순간 차단 → BuyLock acquire 자체 안 함
- **로그**: 시간 차단 = debug / 자본 축소 = info. 무음 차단 아니므로 운영 가시성 보장
- **연동**: closing_bet `settings.yaml fund.absorb_swing_idle=true / closing_bet_pool_cap=0.5` 정책과 양방향 대칭 (스윙→종가베팅 침해 차단)

### ATR 폴백 정책
- pykrx 우선, KIS API 폴백, 양쪽 실패 시 `atr_at_buy=0.0`
- 호출 측 `effective_trailing_pct()`에서 `max(고정, 0)` = 고정값 안전 디그레이드
- 매수 직후 동기 호출 (캐시 hit 우선), 09:20 prefetch 잡은 추후 작업
- 매수 시점 박제만 (보유 중 미갱신, Phase 2에서 일일 재계산 검토)

### monitor_state.json sanity 분기 (v17 확장)
- `tranche_count==1`: 기존 `first_buy_price × 1.02` 임계 유지
- `tranche_count==2`: `max(first×1.02, avg×1.02)` 임계 완화 (2차 진입 후 정상 데이터 보호)
- 복원 시 `avg_buy_price <= 0` 강제 폴백 `avg = first` (catastrophic bug 방어)

### 활성화 절차 (안전)
1. 머지: `git checkout main && git merge worktree-tranche-entry-pyramid`
2. `.env`에 두 줄 추가: `TRANCHE_ENTRY_ENABLED=true` + `DRY_RUN_PYRAMID=false`
3. `sudo systemctl restart trading_system`
4. DB v17 마이그레이션 자동 실행 + 기존 holding 백필 UPDATE 4문장 자동 처리

### 롤백
- `.env` 두 줄 false/true 토글 + restart
- 익절 임계 강제 원복: `TAKE_PROFIT_1/2/3 = 0.10/0.15/0.20` + `PARTIAL_SELL_RATIO_1 = 0.30`

**상세**: `memory/project_tranche_entry.md`

## 모니터 상태 정합성 (monitor_state.json — 2026-05-13 도입)
- **3중 동기화**: 매도 시 메모리/DB/JSON 동시 정리 (`portfolio_monitor_v2.remove_position()` 내부)
  - JSON 잔재로 BE 손절가 즉시 활성화되던 회귀 버그 차단 (2026-05-12 한화오션 사건)
- **`_execute_partial_sell()` 전량 익절(`remaining_shares <= 0`) 분기**: `_close_position_in_db` 직후 `self.remove_position()` 호출 의무 — 신규 매도 경로 추가 시 반드시 같은 패턴 적용
- **재시작 sanity check**: `_restore_trailing_state()` JSON 폴백 경로에서 `state[code].highest_price > pos.buy_price × 1.02` 면 잔재로 판단해 스킵 (당일 갭상승 +2% 까진 정상 허용). 임계값 변경 시 `tests/test_monitor_state_residue.py` 의 두 경계 케이스 재조정 필수
- **`stop_monitoring()`**: 정지 직전 `_dump_monitor_state()` 호출 보장 — SellLock `clear_all()` 보다 먼저 실행되어야 race 봉쇄
- **정화 도구**: `scripts/cleanup_monitor_state_json.py` (systemctl is-active 가드 + KST 백업 + `portfolio.status='holding'` 외 키 제거). 운영 점검에서 잔재 발견 시 stop → cleanup → start
- **상세**: `memory/project_monitor_state_residue_fix.md`

## MCP 서버
프로젝트에 3개 MCP 서버(`SQLite`, `Fetch`, `Sequential Thinking`)가 `.mcp.json`에 등록되어 있다.
**상세 사용법**: [`docs/mcp-usage.md`](docs/mcp-usage.md) 참조.

> 요약: 봇 상태/DB 조회는 Python 실행 대신 SQLite MCP 우선, 복잡 분석은 Sequential Thinking 활용.

## 시스템별 규칙
- **웹 대시보드** (`web/`): [`web/CLAUDE.md`](web/CLAUDE.md)
- **전략/스코어링 모듈** (`modules/`): [`modules/CLAUDE.md`](modules/CLAUDE.md)
- **종가베팅 시스템** (`closing_bet_system/`): 별도 모듈 + 별도 DB (`data/closing_bet.db`) + 별도 텔레그램 봇

## 종가베팅 시스템 운영 규칙 (Phase 1 — 2026-05-04 도입)
- **위치**: `closing_bet_system/` (같은 프로세스/계좌, KIS 토큰 공유)
- **별도 텔레그램 봇**: `.env` `CLOSING_BET_TELEGRAM_BOT_TOKEN`/`CHAT_ID`
- **Phase 1 (알림형) 정책**: **자동매수 절대 금지**. `MainOrchestrator` 는 후보 등록 + 알림만
- **APScheduler 잡 시간** (PRD 16-3, mon-fri):
  - 15:10 `run_daily_pipeline` (Layer 1+2+DART → 점수 → DB → 알림)
  - 15:35 `run_daily_summary` (DB 집계 + 텔레그램)
  - 10:00 `run_label_yesterday` (T+1 사후 라벨링)
- **현재 상태**: main.py 통합 완료, **placeholder universe(빈 리스트)** 라 잡은 등록되지만 실제 데이터 처리는 무동작 → Phase 2 collector 도입 시 활성화
- **fund_guard 미들웨어**: 같은 KIS 계좌 자금 풀 강제 분리 (스윙 매수 실패 방지)
- **상세 컨텍스트**: `memory/project_closing_bet_system.md`, `종가베팅_트레이딩_시스템_PRD_v2.0.md`

## 코드 변경 후 필수 프로세스
- **코드를 작성하거나 수정한 뒤 반드시 code-tester 에이전트로 검증**
- 에이전트 정의: `.claude/agents/code-tester.md`
- 수정된 파일을 대상으로 code-tester 에이전트 실행 → 심각/주의 이슈 발견 시 즉시 수정
- py_compile + 기존 테스트 통과 확인 후 서비스 재시작

## 전략/파라미터 변경 시 필수 프로세스
- 파라미터(손절/익절/트레일링/보유기간/테마 점수 등) 변경은 **`trade-improvement-analyst` 에이전트의 제안서 근거**가 있어야 한다 (`/improve` 명령)
- 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`
- 구현 완료 후 **반드시 `docs/improvements/change_log.md`에 1줄 추가** (before/after 추적용) — CHECKLIST 배포 항목에 포함

## 문서 갱신 대상 (작업 완료 시)
- `CLAUDE.md` (이 파일) — 새 규칙/교훈 발견 시
- `memory/MEMORY.md` — 중요 결과/설정 변경 시
- `docs/INDEX.md` — 새 문서 추가 시
- 해당 시스템의 `CLAUDE.md` — 시스템 특화 변경 시
