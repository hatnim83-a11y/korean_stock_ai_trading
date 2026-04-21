거래 개선 제안서를 생성합니다.

## 지시사항

1. 사용자 입력에서 모드를 파악하라:
   - `weekly` (또는 인자 없음) → 지난 7일
   - `monthly` → 지난 30일
   - `focus:<topic>` → 특정 주제 집중 (`focus:stop_loss`, `focus:gap_filter`, `focus:hold_days`, `focus:trailing`, `focus:theme_selection`)

2. **반드시 `trade-improvement-analyst` 에이전트를 호출하여 분석을 수행**하라 (Task 도구 사용).
   - 메인 에이전트가 직접 분석하지 말 것
   - 에이전트에 전달할 프롬프트: 모드 + 사용자 추가 지시사항 전체
   - 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`

3. 에이전트가 리턴한 결과:
   - 제안서 파일 절대 경로
   - 3줄 요약
   - 승인 필요 플래그

4. 사용자에게 보고 형식:
   - 제안서 경로
   - 핵심 발견 요약
   - **승인 필요** 시: "승인하시면 `/plan [제안서 경로]` 명령으로 구현 계획 수립 단계로 넘어갑니다"
   - **판단 유보** 시: 표본 부족 사유 + 다음 분석 권장 시점

## 주의사항

- 이 명령은 제안서만 생성한다. 코드 수정/config 변경을 절대 수행하지 말라.
- 승인 이후 구현은 **사용자가 직접** `/plan [제안서 경로]`를 호출해야 시작된다.
- 분석은 에이전트(`trade-improvement-analyst`)가 전담 — 메인에서 DB 쿼리/Claude 호출을 하지 말 것.
- 제안 구현 시 CHECKLIST 배포 항목에 "`docs/improvements/change_log.md`에 1줄 추가"가 반드시 포함되어야 before/after 루프가 유지된다.

## 예시

```
/improve
→ weekly 모드로 에이전트 호출

/improve monthly
→ monthly 모드

/improve focus:stop_loss
→ 손절 파라미터 집중 분석
```
