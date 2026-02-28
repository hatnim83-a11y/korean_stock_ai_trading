# 대시보드 코드 리뷰 메모 (2026-02-26)

## 검사 파일
web/app.py, web/auth.py, web/api_routes.py, web/sse_routes.py, web/dashboard_service.py, web_server.py, web/templates/dashboard.html

## py_compile 결과
모두 통과

## 발견된 이슈

### 주의 (개선 권장)

1. **execute_sell save_trade 데이터 부정확** (dashboard_service.py line 390-399)
   - `stock_name`: KIS `_place_order` result에 없음 → stock_code(6자리)로 저장
   - `price`: 시장가 매도는 항상 0 → 실제 체결가 미기록
   - `amount`: result에 없음 → 0 저장
   - `profit_rate`, `profit_amount`: save_trade dict에 미전달 → NULL
   - **핵심 영향**: `profit_amount=NULL` → `get_all_sell_trades()` 실현손익 합산에서 수동매도분 제외
   - `realized_pnl = sum(t.get('profit_amount') or 0 for t in sell_trades)` → NULL은 0 처리
   - 수정: pos에서 buy_price 조회, 현재가로 profit 계산 후 save_trade에 포함

2. **check_rate_limit 성공/실패 모두 카운트** (auth.py line 68)
   - 정상 로그인 5회 후 1분 잠금됨 (브루트포스 방어 목적이나 관리자도 잠김)
   - 수정 권장: 성공 시 IP의 attempts 리스트 클리어

3. **execute_sell get_portfolio 중복 조회** (dashboard_service.py line 364-378)
   - `quantity=None` 경우 get_portfolio 2번 호출 → 단일 호출로 통합 가능
   - 두 조회 사이에 봇이 포지션 청산하면 `pos=None → total_shares=0` → race condition

4. **close_position 후 delete_position_state 미호출** (dashboard_service.py line 385)
   - 수동 전량 매도 시 position_state에 고아 row 잔존
   - 영향: 없음 (load_positions_from_db는 실보유 종목만 참조), 미관 이슈

### 참고 (선택적 개선)

5. **get_system_status subprocess.run 블로킹** (line 316)
   - `async` 함수에서 동기 `subprocess.run(timeout=5)` → 이벤트루프 최대 5초 블로킹
   - 실용 영향: systemctl은 <0.1초 → 허용 가능
   - 수정: `await asyncio.to_thread(subprocess.run, ...)`

6. **get_news_data 5종목 직렬 HTTP** (line 298-304)
   - holdings[:5] 루프에서 asyncio.to_thread 직렬 실행 → ~5초 지연
   - 수정: `asyncio.gather(*[asyncio.to_thread(fetch_stock_news, ...) for h in holdings[:5]])`

7. **하드코딩** (auth.py line 27-37)
   - `TOKEN_EXPIRE_HOURS=24`, `MAX_ATTEMPTS=5`, `WINDOW_SECONDS=60` → .env 미참조

8. **서비스명 하드코딩** (dashboard_service.py line 317)
   - `"trading_system"` 하드코딩 → settings에 없음

9. **SSE 5초마다 get_all_sell_trades() 풀스캔** (sse_routes.py → get_portfolio_data)
   - 레코드 증가 시 성능 저하 가능

### 보안

10. **뉴스 title innerHTML 삽입** (dashboard.html line 459)
    - 네이버 크롤링 텍스트를 innerHTML로 삽입 → 낮은 XSS 위험
    - 수정: `textContent` 또는 `escapeHtml()` 함수 적용

11. **SECRET_KEY 기본값** (auth.py line 25)
    - `"change-me-in-production"` 하드코딩 기본값
    - .env에 올바르게 설정됨 확인 → 실용 위험 없음

## 통과 항목
- py_compile 전체 통과
- auth 미들웨어: exact match + prefix 이중 검사 (이전 버그 수정됨)
- validate_stock_code: SQL injection 방어 올바름
- JWT exp: datetime.utcnow() 사용 (jose 라이브러리 표준 UTC 기준) → 올바름
- SSE MAX_ERRORS=5 서킷브레이커 있음
- execute_sell close_position: WHERE status='holding' 조건 → 이중 호출 무해
- current_total 계산: 분할매도 후에도 수치 정확함 (수학적으로 검증됨)
- profit_rate 단위: trades.profit_rate=% 단위, snapshots=% 단위 → 화면 표시 일치
- max_profit_rate: position_state에서 비율(0.1253)로 저장되나 HTML 미표시 → 이슈 없음

## 배포 판정
수정 후 배포 (주의 이슈 1번이 실현손익 데이터 정확성에 영향)
