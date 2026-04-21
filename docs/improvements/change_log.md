# 파라미터 변경 이력 (before/after 추적용)

`trade-improvement-analyst` 에이전트의 제안 → 사용자 승인 → strategy-coder 구현으로 이어진 **파라미터 조정의 단일 진실 공급원(single source of truth)**이다. 다음 분석 사이클에서 에이전트가 이 파일을 읽어 변경 전/후 성과를 비교 보고한다.

## 기입 규칙

- **누가**: strategy-coder (CHECKLIST 배포 항목에서)
- **언제**: 제안 구현 후, 배포(재시작) 직전 또는 직후
- **어떻게**: 아래 표에 **1줄 추가만**. 기존 행 편집 금지. 롤백/재조정은 새 행으로 기록.
- **필수 항목**: 날짜, 파라미터명, 이전값, 변경값, 제안서 경로, 승인자

## 변경 이력

> 에이전트 도입(2026-04-21) 이전 변경 3건은 **소급 기록**(제안서 경로 대신 커밋 해시)이므로 "제안서 경로" 칼럼에 `commit:<hash>` 표기. 이후 변경은 반드시 에이전트 제안서 경로를 기록할 것.

| 날짜 (KST) | 파라미터명 | 이전값 | 변경값 | 제안서 경로 | 승인자 | 비고 |
|-----------|-----------|-------|-------|------------|--------|------|
| 2026-04-14 | TRAIL_BE_ACTIVATION / TRAIL_BE_STOP | (미구현) | +5% 도달 시 매수가 -1% 손절 | commit:562e1d5 | hatni | 소급 기록. +5% 도달 후 하락 케이스(오이솔루션형) 방어. Phase 1 focus:stop_loss 제안서가 효과 측정 대기 중 |
| 2026-04-16 | ORDER_TYPE_DEFAULT | "market" (시장가 01) | "limit_aggressive" (지정가 00 + 매도 1호가) | commit:7161210 | hatni | 소급 기록. 증거금 1.3→1.04배로 슬롯당 실제 투자금 73→91% 확대 목표. Phase 5 실전 관찰 중 |
| 2026-04-21 | THEME_MOMENTUM_BOOST_FACTOR / CLAMP / DROP_COOLDOWN | (기존 ×1.5 무제한, 쿨다운 없음) | factor 0.7 / clamp ±8 / top_k 30 / cooldown ON | commit:d820638 | hatni | 소급 기록. 화요일 테마 재선정 회전문 방지 Phase 1+2 |

<!--
예시 행 (실제 변경 시 이런 형식으로 추가):
| 2026-05-03 | STOP_LOSS_FAST | -0.07 | -0.06 | docs/improvements/2026-W17-weekly.md | hatni | 트레일링 사전 발동 빈도 감소 목적 |
-->

## 관련 링크
- 제안서 템플릿: `docs/improvements/_TEMPLATE.md`
- 에이전트 정의: `.claude/agents/trade-improvement-analyst.md`
- 관리 규칙: `docs/improvements/README.md`
