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
