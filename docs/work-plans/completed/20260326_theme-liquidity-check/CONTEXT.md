# Context

## 변경 이유
3/24 주 테마 선정에서 비철금속(0%), 음성인식(0~15%), 핵융합에너지(12.5%) 테마의 종목이 09:05 스크리닝에서 대량 탈락하여 매수 불가 상황 발생.

## 핵심 코드 위치
- scorer.py:541-550 — 종목수 보너스 및 total 계산 (여기에 유동성보정 추가)
- scorer.py:491-616 — score_themes() 전체 흐름
- database.py — screening_log 관련 기존 함수: save_screening_log()
- config.py — Settings 클래스 (Field 패턴, MARKET_GUARD_* 참조)
- main.py:394-530 — run_theme_analysis_daily() 테마 분석 흐름
- main.py:2264-2377 — _enrich_tuesday_themes() 화요일 보강

## 설계 결정
- scorer.py에서 점수 항목으로 추가 (selector.py 아님) — 재정렬 문제 방지
- 신규 테마 감점 없음 — 데이터 없이 페널티 주는 것은 근거 부족
- 7일 룩백 — 주간 로테이션 주기와 일치
- retained 테마에도 감점 반영됨 (total_score 기준이므로) — 의도적
