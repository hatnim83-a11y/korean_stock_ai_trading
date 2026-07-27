# CONTEXT — Claude Code Max bridge 전환

## 변경 이유
Anthropic API 종량 과금 대신 로컬에 로그인된 Claude Code(Max 구독) 세션을 재사용하기 위함.
비-트레이딩 AI 분석(사후복기, 테마 감성)부터 안전하게 전환한다.

## 현재 코드 상태 (파일:라인)
### post_trade_analyzer/analyzer.py
- `_get_client()` (64) — Anthropic lazy init, ANTHROPIC_API_KEY / settings.ANTHROPIC_API_KEY
- `_get_model()` (82) — settings.CLAUDE_MODEL, fallback "claude-sonnet-4-6"
- `_parse_json_response()` (90) — ```json 펜스 처리 후 json.loads
- `_analyze_single()` (125) — client.messages.create (일일), timing_score clamp 0~10
- `run_daily_analysis()` (176) — reviews 루프 → `_analyze_single`
- `generate_weekly_summary()` (260) — client.messages.create (주간), inline API 호출

### theme_analyzer/ai_analyzer.py
- `_parse_claude_response()` (90) — score clamp 0~10, confidence default 0.7
- `analyze_theme_sentiment()` (183) — 동기 단일
- `analyze_theme_sentiment_async()` (292) — 비동기 단일, result["theme_name"]=name
- `analyze_themes_batch()` (367) — async 병렬, AsyncAnthropic
- `analyze_themes_sync()` (441) — asyncio.run 래퍼 ← **main.py 실제 호출 지점**
- 결과 shape: `theme_name, score, reason, risk, outlook, confidence`

### config.py
- `class Settings(BaseSettings)` (114), `class Config` (1075): env_file=.env, `extra="allow"`, case_sensitive=False
- Field 패턴: `NAME: bool = Field(default=..., description=...)`

## 호출부 (main.py)
- 3208, 3290: `analyze_themes_sync(themes)` (asyncio.to_thread)
- 3451: `post_trade_analyzer.run_daily_analysis`
- 3474: `post_trade_analyzer.generate_weekly_summary`
- reporter/telegram_notifier.py:441 — run_daily_analysis 반환값 포맷
- scripts/run_now.py:117 — run_daily_analysis

## 핵심 스니펫 — bridge 호출 규약
- CLI: `claude -p` (prompt는 stdin 전달 → arg 이스케이프/길이 회피)
- subprocess env: `strip_anthropic_env(os.environ)` — 두 키 제거
- 반환: stdout 텍스트, 비정상 종료/timeout/공백 → None
- JSON 추출: 펜스 → 전체 → 첫 `{`/`[` ~ 마지막 `}`/`]`

## 과거 버그/주의
- score/timing_score 는 반드시 clamp (0~10) — 기존 로직 유지
- theme 결과에 `theme_name` 키 필수 (main.py `r["theme_name"]` ai_map 구성)
- pydantic extra=allow 라 Field 없이도 env 읽히지만, 발견성 위해 Field 명시
- 서버 UTC → 시간은 now_kst (본 작업은 시간 로직 무관)

## 영향 범위
- flag OFF: import 추가 외 런타임 behavior 변화 0
- flag ON: post_trade 분석 응답 출처만 변경(폴백 보장), theme는 shadow(로그만)
- trading/order/monitor 경로: **무접촉**
