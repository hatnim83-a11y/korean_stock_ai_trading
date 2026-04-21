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

## MCP 서버
프로젝트에 3개 MCP 서버(`SQLite`, `Fetch`, `Sequential Thinking`)가 `.mcp.json`에 등록되어 있다.
**상세 사용법**: [`docs/mcp-usage.md`](docs/mcp-usage.md) 참조.

> 요약: 봇 상태/DB 조회는 Python 실행 대신 SQLite MCP 우선, 복잡 분석은 Sequential Thinking 활용.

## 시스템별 규칙
- **웹 대시보드** (`web/`): [`web/CLAUDE.md`](web/CLAUDE.md)
- **전략/스코어링 모듈** (`modules/`): [`modules/CLAUDE.md`](modules/CLAUDE.md)

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
