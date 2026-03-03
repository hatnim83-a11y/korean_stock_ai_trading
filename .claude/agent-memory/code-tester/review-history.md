# Review History (상세 내역 아카이브)

## 검증된 파일 목록 (시계열)
- database.py: update_portfolio_shares() 추가 (2026-02-23) — 주의 1건 (silent failure)
- database.py: get_all_sell_trades() 추가 (2026-02-23) — 통과
- portfolio_monitor_v2.py: _save_partial_sell_to_db() + _execute_partial_sell() 수정 (2026-02-23) — 통과
- telegram_notifier.py: 명령어 리스너 추가 (2026-02-23) — 심각 2건, 주의 3건, 수정 후 배포
- telegram_notifier.py + main.py: 실현 손익 기능 추가 (2026-02-23) — 주의 1건(float 포맷), 참고 1건, 배포 가능
- main.py + kis_api.py: 상세 매수 리포트 기능 추가 (2026-02-24) — 주의 1건(market order price=0), 참고 2건, 배포 가능
- 보안 취약점 개선 (2026-02-24) — 주의 2건, 참고 2건, 배포 가능
- web/ 대시보드 신규 (2026-02-24) — 주의 5건, 참고 4건, 수정 후 배포
- kis_websocket.py + dashboard_service.py (2026-02-25) — 참고 2건, 배포 가능
- portfolio_monitor_v2.py: buy_date 폴백 + _restore_trailing_state 추가 (2026-02-26) — 주의 2건, 참고 1건, 수정 후 배포
- trading_engine.py + portfolio_monitor_v2.py: 실제 체결가 조회 추가 (2026-02-26) — 주의 1건, 참고 1건, 배포 가능
- trading_engine.py + portfolio_monitor_v2.py: 체결가 조회 호환성 전면 리뷰 (2026-02-26) — 주의 2건, 참고 3건, 배포 가능
- 매수/매도 전수 검토 (2026-02-26) — 심각 3건, 주의 4건, 참고 4건 — 즉시 수정 필요
- DB 스키마 마이그레이션 전수 검토 (2026-02-26) — 주의 2건, 참고 2건, 배포 가능
- 대시보드 전체 리뷰 (2026-02-26) — 심각 1건(_get_kis 미정의), 주의 3건
- 테마 파이프라인 리뷰 (2026-02-27) — 심각 1건(url 키 누락), 주의 1건, 배포 보류
- 테마 로테이션 월요일 기반 전환 (2026-02-27) — 주의 1건(ISO week 연말 edge case), 참고 2건, 배포 가능
- datetime 버그 수정 + 휴장일 체크 추가 (2026-03-02) — 주의 3건, 참고 3건, 배포 가능

## 주요 구조적 버그 (수정 완료)
1. partial_X_executed 재시작 후 초기화 → 이중 매도: DB position_state에 partial 상태 저장으로 수정됨
2. 3차 익절 profit_amount=0: _close_position_in_db sell_shares 파라미터 추가로 수정됨
3. execute_sell _get_kis() → _get_kis_api() NameError: 수정됨

## 잔존 기존 이슈 (미수정)
- database.py save_trade() date 기본값 date.today() UTC 버그 (line 524)
- _close_position_in_db/_save_partial_sell_to_db DB finally 없음 (패턴 불일치)
- telegram_notifier.py send_daily_report() date.today() UTC 버그
- database.py log_system_status() date.today() UTC 버그
- formatter.py date.today() 3곳 (운영 경로 호출 제한적, 실용상 허용)
- trading_engine._execute_stop_loss/take_profit: sync time.sleep(1) 이벤트루프 블로킹

## 테마 파이프라인 상세 (2026-02-27)
- crawlers.py line 351-352: now_kst()로 수정 완료
- url 키 누락 버그: 재시작 후 비월요일에만 발생 → 재사용 판정 조건에 url 체크 추가 필요
- .env TOP_THEME_COUNT=4 미수정 → config.py default=5 변경 미반영 (운영자 수동 수정 필요)

## is_trading_day() 특성 (2026-03-02 확인)
- holidays.KR: 법정 공휴일 + 대체 공휴일 포함 (2026-03-02 3.1절 대체공휴일 정확히 감지)
- 미포함: KRX 임시 특별 휴장일 (연말, 재난 등) — 알려진 한계
- docstring에 이 한계 미언급 → 참고 사항

## 대시보드 상세 이슈 (2026-02-26)
- execute_sell save_trade: stock_name=stock_code, price=0, amount=0 → 실현손익 합산 누락
- check_rate_limit: 성공/실패 모두 카운트 → 정상 로그인 5회 후 잠금
- auth.py TOKEN_EXPIRE_HOURS=24, MAX_ATTEMPTS=5, WINDOW_SECONDS=60 하드코딩
- datetime.utcnow() in create_token: jose JWT exp은 UTC 기준으로 올바름 (now_kst 교체 금지)
