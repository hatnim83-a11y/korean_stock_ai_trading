# PLAN — AI 매수 사유 풀텍스트 + 대시보드 매수 메시지 호버 툴팁

## 목표
1. 텔레그램 매수 리포트의 AI 사유 truncate(`reason[:40]`) 제거 — 전체 출력
2. 대시보드 portfolio 탭에서 종목 호버 시 매수 당시 텔레그램 전문(테마/수급/RSI/AI 등)을 툴팁으로 표시

## 배경
- 매수 리포트 예: `🤖 AI 7.0/10 (75%) — 1분기 편의점 점포 효율화로 외형 성장과 수익성 동반 개선. 외국인 5일` ← 40자에서 잘림
- 보유 종목 사후 점검 시 텔레그램 히스토리를 뒤져야 매수 컨텍스트를 알 수 있음

## 구현 단계
1. **DB 마이그레이션 v15** — `database.py`
   - `portfolio.buy_message TEXT` 컬럼 추가 (`_migrate_v15`)
   - `update_portfolio_buy_message(stock_code, message)` 헬퍼: 가장 최근 holding row UPDATE
2. **main.py** `_send_buy_summary()` 수정
   - `reason[:40]` 제거, 전체 reason 사용
   - 종목별 메시지 블록 추출 dict (`per_stock_messages`)
   - `notifier.send_message()` 직전 DB UPDATE 일괄 실행
   - 최종 메시지 `len > 4000` 가드 추가
3. **web/dashboard_service.py**
   - `get_portfolio_data()` 결과 dict에 `buy_message` 필드 포함
4. **web/templates/dashboard.html**
   - `escapeHtml()` 헬퍼 (XSS 방어)
   - `<tr data-buy-msg="...">` 속성으로 메시지 전달
   - 커스텀 CSS 툴팁 (`.buy-msg-tooltip`)
   - `mouseover/mousemove/mouseout` 이벤트 위임 (textContent로 안전 출력)
   - 기존 보유 종목 폴백 메시지

## 변경 파일 목록
- `database.py` — 마이그레이션 + 헬퍼
- `main.py` — `_send_buy_summary()` 수정
- `web/dashboard_service.py` — API 응답 확장
- `web/templates/dashboard.html` — UI 툴팁

## 롤백 계획
- DB: `_migrate()`가 자동 백업(`*.bak.YYYYMMDD_HHMMSS`) 생성 → 백업 복원 가능
- 코드: 단순 추가형 변경, 문제 시 git revert 1커밋
- 기능 즉시 무력화: dashboard.html `.buy-msg-tooltip { display: none !important; }`

## 완료 기준
- 텔레그램 매수 메시지에 AI reason 전체 표시
- 대시보드 호버 시 매수 당시 메시지 전문 툴팁 표시 (이모지/줄바꿈 정상)
- DB에 신규 매수 종목의 `buy_message` 저장 확인
- 마이그레이션 v15 적용 (`schema_version` 테이블에 row 추가)
- code-tester 에이전트 검증 통과
- systemd 양 서비스(main + dashboard) 정상 재시작
