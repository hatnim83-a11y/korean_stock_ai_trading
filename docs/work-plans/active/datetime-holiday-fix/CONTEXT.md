# CONTEXT: datetime 버그 수정 + 휴장일 체크

## 변경 이유
- 서버가 UTC로 동작하여 `datetime.now()`/`date.today()`가 9시간 오프셋 발생
- 3/2(삼일절 대체공휴일) 스크리닝 실행 → 결과 손실 (날짜 불일치)
- 공휴일 체크 로직 부재로 휴장일에도 불필요한 API 호출

## 핵심 패턴
- `now_kst()` (config.py) → timezone-aware KST datetime 반환
- `now_kst().date()` → `date.today()` 대체
- crawlers.py 캐시: 기존 naive datetime → aware로 전환 시 호환 처리 필요

## 영향 범위
- 직접: 스크리닝/AI검증/리포트/최적화 날짜 정합성
- 간접: 스케줄러 전체 (휴장일 스킵)
