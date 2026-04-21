# 파라미터 변경 이력 (before/after 추적용)

`trade-improvement-analyst` 에이전트의 제안 → 사용자 승인 → strategy-coder 구현으로 이어진 **파라미터 조정의 단일 진실 공급원(single source of truth)**이다. 다음 분석 사이클에서 에이전트가 이 파일을 읽어 변경 전/후 성과를 비교 보고한다.

## 기입 규칙

- **누가**: strategy-coder (CHECKLIST 배포 항목에서)
- **언제**: 제안 구현 후, 배포(재시작) 직전 또는 직후
- **어떻게**: 아래 표에 **1줄 추가만**. 기존 행 편집 금지. 롤백/재조정은 새 행으로 기록.
- **필수 항목**: 날짜, 파라미터명, 이전값, 변경값, 제안서 경로, 승인자

## 변경 이력

| 날짜 (KST) | 파라미터명 | 이전값 | 변경값 | 제안서 경로 | 승인자 | 비고 |
|-----------|-----------|-------|-------|------------|--------|------|
| _(최초 상태 — 변경 이력 없음)_ | | | | | | |

<!--
예시 행 (실제 변경 시 이런 형식으로 추가):
| 2026-05-03 | STOP_LOSS_FAST | -0.07 | -0.06 | docs/improvements/2026-W17-weekly.md | hatni | 트레일링 사전 발동 빈도 감소 목적 |
-->

## 관련 링크
- 제안서 템플릿: `docs/improvements/_TEMPLATE.md`
- 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`
- 관리 규칙: `docs/improvements/README.md`
