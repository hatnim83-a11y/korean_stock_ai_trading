# CHECKLIST — Claude Code Max bridge 전환

## 구현
- [x] `tests/test_claude_code_bridge.py` 작성 (RED)
- [x] `modules/claude_code_bridge.py` 구현 (GREEN)
- [x] `config.py` flag 4개 추가
- [x] `tests/test_post_trade_analyzer_claude_bridge.py` 작성 (RED)
- [x] `post_trade_analyzer/analyzer.py` `_call_llm_json` + 리팩터 (GREEN)
- [x] `tests/test_theme_analyzer_shadow.py` 작성 (RED)
- [x] `theme_analyzer/ai_analyzer.py` batch bridge + shadow 훅 (GREEN)
- [x] `theme_analyzer/__init__.py` export 추가 (`analyze_themes_batch_bridge`) — PLAN 원안 외 추가 변경

## 검증 (2026-07-27 실행 결과)
- [x] test_claude_code_bridge.py PASS
- [x] test_post_trade_analyzer_claude_bridge.py PASS
- [x] test_theme_analyzer_shadow.py PASS
  → 3파일 합계 **29 passed** (4.03s). 전부 fake runner / MagicMock — 실제 CLI·API 호출 0
- [x] 기존 관련 테스트 미회귀 (theme 관련)
  → `test_theme_dict_normalization.py` + `test_theme_selector.py` + `test_theme_supply_score.py` **48 passed** (12.18s)
  → post_trade_analyzer 전용 기존 테스트 파일은 레포에 존재하지 않음 (신규 파일이 최초)
- [x] `py_compile` 통과 (변경/신규 8파일 전부)
- [x] `git diff --check` clean (공백/충돌 마커 없음)
- [x] env 제거 검증(ANTHROPIC_API_KEY/AUTH_TOKEN subprocess 미노출)
  → `strip_anthropic_env` 실측: 두 키 제거 True, `PATH` 보존 True, 원본 env 불변
- [x] flag OFF 시 기존 API 경로 유지 확인
  → code default / .env 반영 effective 값 양쪽 모두:
    `CLAUDE_CODE_BRIDGE_ENABLED=False`, `CLAUDE_CODE_THEME_SHADOW=False`,
    `CLAUDE_CODE_CLI_PATH='claude'`, `CLAUDE_CODE_TIMEOUT_SEC=120`
  → 현재 `.env` 에 bridge 관련 키 없음(effective == default) → **머지만으로 behavior 변화 0**
- [x] code-tester 에이전트 심각 이슈 0
  → 심각 0 / 주의 3 / 참고 3, 종합 "배포 가능"

### code-tester 주의 3건 (모두 비차단 — 후속 개선 대상)
1. `analyzer.py:150` `_call_api_json` 예외 로그에서 종목명 컨텍스트 소실 (리팩터 전에는 `(stock_name)` 포함). 반환값은 동일하게 `None`
2. `analyzer.py` 주간 종합 실패 사유가 4가지(브릿지 실패/키 미설정/API 예외/파싱 실패)로 갈리는데 로그는 한 줄로 통합됨 — 원인 특정이 한 단계 느려짐
3. shadow ON 시 `analyze_themes_sync` 가 같은 스레드에서 최대 `CLAUDE_CODE_TIMEOUT_SEC`(120초) 동안 추가 블로킹. 호출부가 `asyncio.to_thread` 라 이벤트 루프는 무영향. 기본 OFF 라 현재 운영 무관 — shadow 실제 활성화 시 고려

## 배포
- [x] 서비스 재시작 안 함 (사용자 지시) — PID 3002350 / 2026-07-13 00:54:33 UTC 기동 상태 그대로 유지
- [x] `.env` bridge flag 활성화 안 함 (전부 미설정 = default OFF)
- [x] change_log 해당 없음 (파라미터/전략 변경 아님)
- [x] 임시 러너 `scripts/_run_pytest.py` / `scripts/_run_script.py` 삭제 (커밋 제외)

## 문서 업데이트
- [x] active → completed 아카이브 (`completed/20260703_claude-code-max-bridge/`)
- [ ] ~~`memory/MEMORY.md` 1줄 추가~~ — **미수행**: 이번 세션 허용 파일 범위 밖. 후속 작업으로 이관
- [x] 산출물 보고 (변경 파일/테스트결과/안전설명/flag)

## 활성화 절차 (후속 — 사용자 승인 필요)
1. `.env` 에 `CLAUDE_CODE_BRIDGE_ENABLED=true` 추가 (post_trade 부터 단독 검증 권장)
2. `sudo systemctl restart trading_system`
3. 17:00 일일 분석 / 금 17:30 주간 종합 로그에서 `[PostTradeAnalyzer] Claude Code bridge 응답 사용` 확인
4. 안정 확인 후 `CLAUDE_CODE_THEME_SHADOW=true` 로 테마 shadow 관찰 (운영 결과 무영향)
5. 롤백: 해당 키 제거 + restart → 기존 API 경로 100% 복귀
