# CONTEXT — AI 매수 사유 풀텍스트 + 호버 툴팁

## 변경 이유
사용자가 텔레그램 매수 리포트에서 AI 사유가 잘려 있어 전체 매수 근거를 확인할 수 없다고 요청. 또한 대시보드에서 보유 종목을 점검할 때 매수 당시 컨텍스트(테마/수급/RSI/AI 사유)를 즉시 확인할 수 있도록 툴팁 추가 요청.

## 현재 코드 상태

### main.py:1416-1586 — `_send_buy_summary()`
- 매수 직후(`execute_buy_orders` 끝, `:1406`) 호출됨
- 종목별 메시지 블록 구성: `:1512-1578` for-loop
- AI 사유 truncate: `:1562` `reason_short = reason[:40] if reason else ""`
- 최종 발송: `:1586` `self.notifier.send_message("\n".join(lines))`

### database.py
- `:526-543` portfolio 테이블 정의 (PK = `id` autoincrement, status로 holding/replaced/closed 구분)
- `:150-209` `_migrate()` 시스템 (현재 v14, schema_version 테이블)
- `:231-251` `_migrate_v2()` — 컬럼 추가 패턴
- `:907-929` `update_portfolio_price()` — UPDATE 헬퍼 패턴
- `:931-973` `save_holding_position()` — 같은 종목 기존 holding을 `replaced`로 마킹 후 새 INSERT (ID autoincrement이므로 재매수 시 새 row)

### web/dashboard_service.py:75-167 — `get_portfolio_data()`
- `holdings = db.get_portfolio(status="holding")` → SELECT * 이므로 새 컬럼 자동 포함
- `result_holdings.append({...})` (`:128-146`) dict에 `buy_message` 추가 필요

### web/templates/dashboard.html
- `:707-723` portfolio 초기 렌더링 (`tbody.innerHTML = holdingsData.map(...)`)
- `:1101-1127` `updatePortfolioTable(d)` SSE 갱신 시 재렌더링
- `:711-722`, `:1115-1126` 두 곳에 동일한 `<tr>` 패턴 → 동일한 escapeHtml 적용 필요
- 현재 `${h.stock_name}`, `${h.stock_code}` 등을 `innerHTML`에 직접 삽입 → XSS 위험

## 핵심 스니펫

### main.py:1556-1568 (현재)
```python
sentiment = ai.get('ai_sentiment', 0)
confidence = ai.get('ai_confidence', 0)
reason = ai.get('ai_reason', '')
if sentiment:
    conf_pct = f"{confidence * 100:.0f}%" if confidence else ""
    reason_short = reason[:40] if reason else ""   # ← 이 줄 제거
    ai_line = f"🤖 AI {sentiment:.1f}/10"
    if conf_pct:
        ai_line += f" ({conf_pct})"
    if reason_short:
        ai_line += f" — {reason_short}"
    lines.append(ai_line)
```

### main.py:1512 for-loop 구조
```python
for i, o in enumerate(new_buy_orders, 1):
    code = o.get('stock_code', '')
    # ... 종목별 라인들 lines.append() ...
    lines.append("")  # 종목 간 빈 줄
```
→ 종목별 시작 인덱스를 추적해 `lines[start:end]` 슬라이스로 종목별 텍스트 추출

## 과거 버그 / 주의사항
- `passlib` 사용 금지 (web/CLAUDE.md) — 인증 변경 시 hashlib만 사용
- `INSERT OR REPLACE` 시 컬럼 누락하면 NULL로 초기화됨 → `_migrate_v15`에서 `DEFAULT NULL`만 정의하고 기존 INSERT는 건드리지 않음 (자동으로 NULL 채워짐)
- `_send_buy_summary()`는 매수 INSERT 후에 호출됨 (`main.py:1406`) → DB UPDATE 시 row 존재 보장됨
- KST 사용: `datetime.now()` 금지, `from config import now_kst`
- code-tester 검토 결과 (50525 토큰):
  - **XSS**: dashboard.html의 `tbody.innerHTML` 직접 삽입은 XSS 취약 → escapeHtml 필수
  - **메시지 4096자 한도**: Telegram API 제한, 평상시 안전하나 가드 1줄 추가 권장
  - **rowcount 확인**: UPDATE 실패(0 row) 시 logger.warning로 가시성 확보

## 영향 범위
- 텔레그램 매수 리포트: 메시지 길이 평균 +200~400자 (AI reason 풀텍스트)
- DB: portfolio 테이블 1개 컬럼 + 마이그레이션 1회 (자동 백업)
- 대시보드: 신규 CSS 클래스, 신규 JS 헬퍼/이벤트 핸들러 (기존 동작 영향 없음)
- 영향 없는 영역: 매도 로직, 모니터링, 종가베팅, 트레일링, 백테스트
