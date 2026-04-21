# CONTEXT: 거래 개선 전문 에이전트 도입

## 변경 이유
현재 사후 분석 시스템은 **일방향 모니터링**에 머물러 있다. AI가 매도 건마다 `timing_score`, `pattern`, `parameter_suggestion`, `lesson`을 생성하고 주간 종합까지 만들지만, **그 결과가 `config.py`의 손절/익절/트레일링/테마 파라미터에 반영되는 자동 경로가 없다**. 사용자가 텔레그램을 읽고 수동으로 판단해야 하며, 판단이 지연되면 개선 기회가 소실된다. 또한 "데이터 축적 후 재검토" 상태의 메모리 항목들이 트리거되지 않는다.

## 현재 코드 상태

### 사후 분석 루틴 (`modules/post_trade_analyzer/`)
- `analyzer.py:generate_weekly_summary()` — 주간 종합 생성, Claude 호출
- `prompts.py:WEEKLY_SUMMARY_PROMPT` — `parameter_suggestions` 배열 출력 포함 (자유 서술)
- `prompts.py:INDIVIDUAL_ANALYSIS_PROMPT` — `timing_score`(0-10), `overall_assessment`(Excellent/Good/Neutral/Poor/Bad 5단계), `parameter_suggestion`(자유 문자열), `lesson` 출력
- 텔레그램 전송: `send_post_trade_report()` (analyzer.py) — 최대 5건
- main.py 호출: `run_post_trade_analysis()` (17:00), `run_weekly_trade_review()` (금 17:30)

### 데이터 스키마 (database.py)
- `trade_reviews`(DB v5+, database.py:298-321): id, trade_id, stock_code/name, buy_date, sell_date, buy_price, sell_price, shares, hold_days, profit_rate, profit_amount, **ai_review** (JSON TEXT), **lesson_learned** (TEXT)
- `post_trade_prices`(DB v9, database.py:377-395): review_id FK, check_date, days_after_sell, close/high/low_price, volume, change_from_sell(%), UNIQUE(review_id, check_date)
- `strategy_stats`(DB v8): 일일 전략별 집계
- 실제 DB 경로: `data/trading.db` (루트 `trading.db`는 빈 파일)

### 기존 에이전트 (`.claude/agents/`)
- `strategy-planner.md` — model:sonnet, 계획 수립 반응형
- `strategy-coder.md` — model:opus, 전략 코드 구현
- `code-tester.md` — model:sonnet, 코드 품질 검증
- `bot-health-checker.md` — model:opus, 봇 운영 상태

### 미결 검토 항목 (`memory/`)
- `project_stop_loss_review.md` — 2026-05-01 재평가 예정 (4월 데이터 축적 후)
- `project_gap_filter_review.md` — 2026-04 초 재분석 예정
- `project_hold_days_review.md` — 20건 이상 누적 시 재검토

### MCP/인프라
- `.mcp.json`에 sqlite, fetch, sequential-thinking 3서버 등록됨
- `docs/mcp-usage.md` — 사용법
- `scripts/mcp_sqlite.sh` — MCP 래퍼 (서버에 `sqlite3` CLI 미설치)
- Python 폴백: `source venv/bin/activate && python -c "from database import Database; ..."`

## 핵심 스니펫

### WEEKLY_SUMMARY_PROMPT 출력 예시 (prompts.py:76~)
```json
{
  "weekly_pattern": "...",
  "strategy_feedback": {...},
  "parameter_suggestions": ["현재 트레일링 파라미터 유지 적절"],
  "next_week_cautions": [...],
  "avg_timing_score": 7.2
}
```

### ai_review JSON 구조 (prompts.py:38-48)
```json
{
  "timing_score": 7,
  "opportunity_cost": "...",
  "pattern": "...",
  "parameter_suggestion": "자유 서술 문자열",
  "lesson": "...",
  "overall_assessment": "Good"
}
```

## 영향 범위
**이번 작업(Phase 1)은 Python 코드 미변경** — 신규 에이전트/명령어/문서만 추가하므로 기존 기능 무영향. `systemctl status trading_system` 정상, 스케줄 작업 무변경, `generate_weekly_summary()` 보존.

## 과거 버그/학습
- `parameter_suggestion`은 자유 서술이므로 건수 집계 금지 (coder 리뷰 지적) → 인용+수동 분류로만 활용
- `WEEKLY_SUMMARY_PROMPT`와 중복 Claude 호출 주의 — 에이전트는 기존 결과를 **입력으로 흡수**
- `date +%V`는 시간대 무관하지만 KST 일관성을 위해 `now_kst().isocalendar()` 사용
- `memory/MEMORY.md`는 auto-memory 파일이라 `git revert`로 완전 복원 불가 — Edit 수동 제거

## 참고 경로
- 플랜 원본: `/home/hatni/.claude/plans/cosmic-juggling-locket.md`
- MCP 사용: `docs/mcp-usage.md`
- 프로젝트 규칙: `CLAUDE.md`, `~/.claude/CLAUDE.md`
