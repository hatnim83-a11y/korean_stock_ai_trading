# 테마 분석 파이프라인 개선 - Checklist

## 구현 항목
- [x] crawlers.py: `_clean_news_html()` 헬퍼
- [x] crawlers.py: `crawl_theme_news()` 함수
- [x] scorer.py: `_collect_news_data()` 교체
- [x] scorer.py: `_enrich_theme_stocks()` 함수
- [x] scorer.py: `score_themes()` 반환값에 news 키 추가
- [x] __init__.py: `crawl_theme_news` export
- [x] database.py: DB v12 마이그레이션 (url 컬럼)
- [x] database.py: `save_theme_scores()` url 저장
- [x] weekly_aggregator.py: url 반환
- [x] main.py: 화요일 08:30 실시간 보강 로직

## 검증 항목
- [x] py_compile 전체 수정 파일
- [x] code-tester 에이전트 실행 — 심각 0건, 배포 가능
- [x] import 정상 확인

## 배포 항목
- [ ] systemctl restart trading_system
- [ ] 로그 모니터링 (에러 없는지)

## 문서 업데이트 항목
- [x] MEMORY.md 업데이트 (DB v12, 파이프라인 변경)
- [x] CLAUDE.md 필요 시 업데이트 (불필요)
