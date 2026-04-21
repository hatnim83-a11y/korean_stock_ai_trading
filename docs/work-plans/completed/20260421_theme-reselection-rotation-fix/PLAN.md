# PLAN — 테마 재선정 회전문 방지 (Phase 1+2)

## 목표
2026-04-21 화요일 재선정에서 발생한 "retention 탈락 4개 테마의 즉시 재진입"(0교체) 사건을 구조적으로 차단. 기존 retention 48 vs 신규 30 철학은 유지.

## 배경
- 2026-04-21(화) 08:30 주간 재선정: 통신만 유지, 금융/아이폰/조선/CXL 4개 drop → 같은 4개 재진입
- 09:25 매수 실행: AI 검증 0/3 통과 → 매수 0건
- DB 재검증으로 로직 버그 없음 확인, 설계 결함 4가지 상호 증폭이 원인

## 구현 단계
### Phase 1 (핫픽스)
- **1-1** `main.py:2519` 증폭 계수 `×1.5` → `× THEME_MOMENTUM_BOOST_FACTOR(0.7)`
- **1-2** `main.py:2519` 직후 adjustment clamp `±THEME_MOMENTUM_BOOST_CLAMP(8.0)`
- **1-3** `_enrich_tuesday_themes` 진입/종료 로그 보강

### Phase 2 (구조 보강)
- **2-1** `main.py:2490` `top_15` → `top_k`(`THEME_ENRICH_TOP_K=30`)
- **2-2** `selector.py select_themes_with_retention()` 쿨다운 (`THEME_DROP_COOLDOWN_ENABLED=True`)

## 변경 파일
- `config.py` — 상수 4개 신규
- `main.py` — `_enrich_tuesday_themes` 2470-2583
- `modules/theme_analyzer/selector.py` — `select_themes_with_retention` 138-244
- `tests/test_theme_selector.py` — 신규
- `scripts/replay_20260421.py` — 신규

## 완료 기준
- pytest 3개 케이스 전부 통과
- replay 스크립트에서 쿨다운 ON → 금융/아이폰/조선/CXL 전원 재진입 차단 확인
- code-tester 에이전트에서 심각/주의 이슈 0건
- systemd 재시작 후 정상 가동

## 롤백 계획
4개 상수 각각 독립 롤백 가능. DB 스키마 변경 없음.

## 참고
- 원본 계획서: `/home/hatni/.claude/plans/radiant-chasing-minsky.md`
