# PLAN — Claude API → Claude Code Max bridge 전환 (1~2단계)

## 목표
Anthropic API 직접 호출을 로컬 로그인된 Claude Code CLI(Max 세션) 호출로 전환할 수 있는
공통 bridge를 도입하고, 두 소비 지점을 안전하게(opt-in flag) 연결한다.

- **1단계**: `post_trade_analyzer` (일일/주간 분석)를 bridge 경유로 전환 + 실패 시 기존 API 폴백
- **2단계**: `theme_analyzer` batch를 **shadow mode**로 bridge에 병행 분석(운영 판단은 기존 API 유지)

## 절대 안전 규칙 (불변)
- Trading/order 실행 로직 **무변경**
- API 키 값 출력/로그 금지, `.env` 읽기/수정 금지
- default 안전: 모든 flag OFF → 기존 API behavior 유지
- bridge subprocess env 에서 `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` 제거 (Max 세션 강제)
- 외부 네트워크/주문/API 실호출 테스트 금지 — fake runner/mock 만
- 운영 DB/로그 파일 수정 금지 (코드/테스트/문서만)
- 서비스 재시작 금지

## 구현 단계
1. `modules/claude_code_bridge.py` (신규 공통 모듈)
   - `strip_anthropic_env(env)` — 두 키 제거한 env 복사본
   - `run_claude_code(prompt, *, timeout, runner=None, cli_path=None)` — `claude -p`, prompt=stdin, 실패/timeout→None
   - `extract_json(text)` — ```json 펜스 / 객체 / 배열 추출
   - `call_claude_code_json(prompt, *, ...)` — 실행+파싱, 실패→None
   - `runner` 콜러블 주입으로 테스트 가능
2. `config.py` — Field 4개 추가 (아래 flag 목록)
3. `modules/post_trade_analyzer/analyzer.py`
   - `_call_llm_json(prompt, max_tokens)` 헬퍼: bridge_enabled 시 bridge 우선 → 실패 시 기존 API
   - `_analyze_single`, `generate_weekly_summary` 를 헬퍼 경유로 리팩터
4. `modules/theme_analyzer/ai_analyzer.py`
   - `analyze_themes_batch_bridge(themes)` — batch payload → bridge → shape 검증/기본값
   - `analyze_themes_sync` 에 shadow 훅: shadow flag 시 bridge 병행 + 로그, **반환값 불변**
5. 테스트 (TDD, 각 파일 RED→구현→GREEN)

## flag (config.py, .env)
| flag | default | 효과 |
|------|---------|------|
| `CLAUDE_CODE_BRIDGE_ENABLED` | False | post_trade 분석 bridge 경유 |
| `CLAUDE_CODE_THEME_SHADOW` | False | theme batch shadow 병행 |
| `CLAUDE_CODE_CLI_PATH` | "claude" | CLI 바이너리 경로 |
| `CLAUDE_CODE_TIMEOUT_SEC` | 120 | subprocess timeout |

## 변경 파일 목록
- 신규: `modules/claude_code_bridge.py`
- 수정: `config.py`, `modules/post_trade_analyzer/analyzer.py`, `modules/theme_analyzer/ai_analyzer.py`
- 수정(실제 구현 시 추가): `modules/theme_analyzer/__init__.py` — `analyze_themes_batch_bridge` export
- 테스트 신규: `tests/test_claude_code_bridge.py`, `tests/test_post_trade_analyzer_claude_bridge.py`, `tests/test_theme_analyzer_shadow.py`

## 롤백 계획
- 모든 flag default OFF → 코드 머지만으로는 behavior 변화 0 (자연 롤백 상태)
- 문제 시 `.env` flag 제거 + restart → 기존 API 경로 100% 복귀
- 최악 시 bridge 모듈/헬퍼 호출 제거 (import + `_call_llm_json` + shadow 훅 3곳)

## 완료 기준
- 3개 테스트 파일 전부 GREEN + 기존 관련 테스트 미회귀
- `py_compile` 통과
- code-tester 심각 이슈 0
- 운영 default 안전(flag OFF 시 기존 경로) 문서로 설명
