# Checklist

## 구현
- [x] config.py: THEME_LIQUIDITY_CHECK_* 상수 6개 (Field 패턴)
- [x] database.py: get_theme_pass_rates() 메서드
- [x] scorer.py: calculate_liquidity_penalty() 함수
- [x] scorer.py: score_themes() pass_rates 파라미터 + 유동성보정 적용
- [x] main.py: 비화요일 경로 — pass_rates 조회 + score_themes 전달
- [x] main.py: 화요일 경로 — pass_rates 조회 + 보강 후 유동성 보정
- [x] main.py: 텔레그램 알림에 통과율 표시

## 검증
- [x] py_compile 4개 파일
- [x] MCP SQLite로 통과율 쿼리 검증
- [x] code-tester 에이전트 실행 (심각 0건, 주의 3건 → 2건 수정 반영)
- [x] 실데이터 시뮬레이션 (비철금속 -8.0점→42.4점, 음성인식 -3.8점, 엣지케이스 5건 통과)

## 문서 업데이트
- [x] CLAUDE.md memory 업데이트
- [x] active/ → completed/ 아카이브
