# PLAN: 손절 재검토 v2 — 이란 전쟁 표본 오염 경고 + 재평가 체크리스트

## 목표
`docs/improvements/2026-04-21-focus-stop_loss.md` 제안서의 "D+1~D+2 반등 83%" 패턴이 **이란 전쟁(2026-03-03 개전 ~ 04-08 휴전) 표본 오염**에 크게 영향받았음을 사용자 지적으로 확인. v1 플랜에서 Phase B(`GRACE_PERIOD_DAYS 1→2`)를 즉시 적용하려 했으나 근거가 흔들려 **Phase B를 연기**하고 메모리·문서 정합성만 먼저 정리한다.

## 배경
- 2026-03-03 이란 전쟁 개전: 코스피 -7.24% (역대급 폭락)
- 2026-03 전체 -19.08%, 04-08 휴전 합의 후 +6.87% 급반등
- 제안서 D+5 추적 6건 **전원**이 전쟁 기간 내 매도, 특히 오이솔루션 D+5(4/9)는 휴전 다음날 V자 수혜
- 제안서 본문이 "즉각 config.py 수정 요청하지 않음" 명시 → v1 플랜이 Phase B로 끌어올린 것은 무리

## 구현 단계

### Phase A-1: memory/project_stop_loss_review.md 업데이트
- "focus:stop_loss 제안서(2026-04-21) 표본 오염 경고" 단락 삽입
- 5/1 재평가 통합 체크리스트 6건 신설 (평시 표본 분리 + BE/Market Guard 추적)
- Phase B 연기 결정 명시

### Phase A-2: memory/project_strategy.md 표기 통일
- Line 15 "손절 보호기간 | 매수 후 2거래일 -8%" → "**매수당일 + GRACE_PERIOD_DAYS영업일 = 총 (N+1)거래일 -8%**"
- 숫자 불변 (현재 N=1, 총 2거래일). Phase B 연기 이유로 숫자 변경 없음

### Phase A-3: docs/improvements/queries.md 보조 쿼리 2건 추가
- 섹션 2-1 하위에 삽입
- (a) 전쟁 기간 제외 (`NOT BETWEEN '2026-03-03' AND '2026-04-08'`)
- (b) 평시 표본 필터 (`sell_date >= '2026-04-09' AND sell_reason='손절'`)
- "전쟁 오염 주의" 주석 박스

## 변경 파일 목록
**수정**:
- `memory/project_stop_loss_review.md` (auto-memory, git 비추적)
- `memory/project_strategy.md` (auto-memory, git 비추적)
- `docs/improvements/queries.md`

**Python 코드 변경**: **0건**. 서비스 재시작 **불필요**.

## 롤백 계획
- memory 2건: Edit 도구로 추가 섹션 제거
- queries.md: git revert 또는 Edit로 쿼리 2건 제거
- 전체 되돌리는 데 수 분 이내

## 완료 기준
CHECKLIST.md 참조 — 구현 3건 + 검증 2건 + 문서 업데이트 2건 모두 `[x]`

## 후속
- 5/1 ~ 5/10 사이 `/improve monthly` 자동 리마인더로 평시 표본 누적 확인
- 평시 손절 10건 이상 누적 시 Phase B 재판단 별도 `/plan`
