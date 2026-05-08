# CHECKLIST — AI 매수 사유 풀텍스트 + 호버 툴팁

## 구현
- [x] `database.py` `_migrate()` migrations 리스트에 `(15, ...)` 추가
- [x] `database.py` `_migrate_v15()` 메서드 추가 (`buy_message TEXT` 컬럼)
- [x] `database.py` `update_portfolio_buy_message(stock_code, message)` 헬퍼 추가 (rowcount 반환)
- [x] `main.py:1556-1568` `reason[:40]` truncate 제거 → 전체 reason 사용
- [x] `main.py:1512-1578` for-loop에서 종목별 메시지 블록 추출 (`per_stock_messages` dict)
- [x] `main.py:1586` `send_message` 직전 DB UPDATE 일괄 실행 (try/except로 격리)
- [x] `main.py:1586` 최종 메시지 4000자 가드 추가
- [x] `self.db` 인스턴스 접근 가능 여부 확인 (`main.py:147 self.db = Database()` 확인됨)
- [x] `web/dashboard_service.py:128` result dict에 `"buy_message": h.get("buy_message") or ""` 추가
- [x] `web/templates/dashboard.html` style 섹션에 `.buy-msg-tooltip` CSS 추가
- [x] `web/templates/dashboard.html` script 상단에 `escapeHtml()` 헬퍼 추가
- [x] `web/templates/dashboard.html:711-722` 초기 렌더링 `<tr>`에 `data-buy-msg` 속성 + escapeHtml 적용
- [x] `web/templates/dashboard.html:1115-1126` SSE 갱신부에 동일 적용
- [x] `web/templates/dashboard.html` 툴팁 마우스 이벤트 위임 (mouseover/mousemove/mouseleave + scroll)
- [x] 툴팁 폴백 메시지 ("이 종목은 매수 메시지 저장 기능 도입 전 매수")

## 검증
- [x] `python -m py_compile database.py main.py web/dashboard_service.py` 통과
- [ ] `sqlite3 data/trading.db "PRAGMA table_info(portfolio)" | grep buy_message` 컬럼 존재 확인 (서비스 재시작 후)
- [ ] `sqlite3 data/trading.db "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"` v15 확인 (서비스 재시작 후)
- [x] `grep -n "reason\[:40\]" main.py` 결과 비어있음 (잔존 없음)
- [x] code-tester 에이전트 실행 → 심각 0건, 주의 1건(rowcount=0 로그로 방어됨), 종합 판정 **배포 가능**
- [ ] 대시보드 브라우저 진입 → 종목 호버 시 툴팁 표시 (이모지/줄바꿈 정상) — 서비스 재시작 후
- [ ] SSE 30초 갱신 후 호버 다시 → 툴팁 정상 — 서비스 재시작 후
- [ ] 기존 보유 종목 호버 → 폴백 메시지 표시 — 서비스 재시작 후
- [ ] XSS 검증: AI reason에 `<script>` 수동 INSERT 후 호버 → 텍스트로만 표시 (실행 안 됨)

## 배포
- [ ] 변경 사항 커밋 (commit message: feat/buy-msg-tooltip)
- [ ] `sudo systemctl restart trading_system` 후 status 정상 확인
- [ ] `sudo systemctl restart trading_dashboard` 후 status 정상 확인
- [ ] 다음 거래일 09:25 매수 시 텔레그램 메시지 풀텍스트 출력 확인
- [ ] 다음 거래일 매수 후 DB `buy_message` 컬럼 채워짐 확인

## 문서 업데이트
- [ ] `memory/MEMORY.md` — DB Schema 섹션에 v15 추가 (`portfolio.buy_message`)
- [ ] active/ → completed/YYYYMMDD_buy-message-tooltip/ 아카이브
