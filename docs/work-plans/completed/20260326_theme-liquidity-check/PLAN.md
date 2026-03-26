# 테마 유동성 사전 검증

## 목표
테마 선정 시 screening_log 통과율을 점수에 반영, 소형주/저유동성 테마 사전 감점/제외

## 구현 단계
1. config.py: Settings 클래스에 THEME_LIQUIDITY_CHECK_* 상수 6개 추가
2. database.py: get_theme_pass_rates() 메서드 추가
3. scorer.py: calculate_liquidity_penalty() + score_themes() pass_rates 파라미터 추가
4. main.py: DB 조회 → scorer 연결 + 텔레그램 알림 통과율 표시

## 변경 파일
- config.py, database.py, modules/theme_analyzer/scorer.py, main.py

## 롤백
THEME_LIQUIDITY_CHECK_ENABLED = False

## 완료 기준
- 비철금속(통과율 7%)이 -8점 감점되어 42.4점으로 하위 밀림
- 신규 테마/데이터 부족은 감점 없음 (판단 보류)
- 기존 테스트/흐름에 영향 없음
