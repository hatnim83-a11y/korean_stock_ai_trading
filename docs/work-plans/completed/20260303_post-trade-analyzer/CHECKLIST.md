# CHECKLIST: Post-Trade Analyzer

## 구현 항목
- [x] DB 마이그레이션 v9 (_migrate_v9, post_trade_prices 테이블)
- [x] DB CRUD (save_post_trade_prices, get_post_trade_prices, get_reviews_ready_for_analysis)
- [x] modules/post_trade_analyzer/__init__.py
- [x] modules/post_trade_analyzer/price_tracker.py (PostTradePriceTracker)
- [x] modules/post_trade_analyzer/prompts.py (개별 + 주간 프롬프트)
- [x] modules/post_trade_analyzer/analyzer.py (PostTradeAnalyzer)
- [x] scheduler.py 콜백 슬롯 2개 (on_post_trade_analysis, on_weekly_trade_review)
- [x] scheduler.py 스케줄 등록 (17:00, 금 17:30)
- [x] main.py PostTradeAnalyzer 통합
- [x] telegram_notifier.py send_post_trade_report()

## 검증 항목
- [x] py_compile 전체 파일 통과
- [x] `from modules.post_trade_analyzer import PostTradeAnalyzer` 임포트 성공
- [x] DB 마이그레이션 성공 (post_trade_prices 테이블 존재, schema v9)
- [x] code-tester 에이전트 검증 통과 (심각 1건 수정 완료)

## 배포 항목
- [x] systemd 재시작 (`sudo systemctl restart trading_system`) — 2026-03-03 20:28 KST

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트 (DB Schema v9, 새 모듈 정보)
