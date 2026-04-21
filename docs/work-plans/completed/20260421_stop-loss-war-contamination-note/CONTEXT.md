# CONTEXT: 손절 재검토 v2 — 전쟁 오염 경고

## 변경 이유
v1 플랜이 `GRACE_PERIOD_DAYS 1→2` (Phase B)를 즉시 적용하려 했으나, 사용자 지적으로 2026-03-03 이란 전쟁 개전·04-08 휴전 합의 V자가 제안서 표본 전체에 영향 줬음이 확인. 코드 변경 보류, 문서 정합성만 정리.

## 전쟁 타임라인 증거
- 2026-03-03: 코스피 -7.24% (역대급 폭락)
- 2026-03 전체: -19.08% (월별 역대 4위)
- 2026-04-08: 2주 휴전 합의 → +6.87% 급반등

## 제안서 손절 9건 전수 매칭
| 매도일 | 건수 | 전쟁 맥락 |
|-------|------|----------|
| 2026-03-04 | 5건 | 폭락 D+1 (Market Guard 미도입) |
| 2026-03-24 | 2건 | 3월 하락장 중반 |
| 2026-04-02 | 1건 | 휴전 6일 전 (D+5=4/9 휴전 다음날) |
| 2026-04-13, 04-20 | 2건 | 휴전 후, AI 분석 미완 |

**D+5 추적 6건 전원이 전쟁 기간 내**. 특히 4/2 오이솔루션은 D+5가 휴전 다음날로 V자 수혜 직접 표본.

## 현재 코드 상태 (파일:라인, 참조용 — 변경 없음)
- `_check_stop_loss()`: `modules/trading_engine/portfolio_monitor_v2.py:855-894`
- Grace Period: `config.py:287-294`
  - `GRACE_PERIOD_DAYS = 1`, `GRACE_PERIOD_STOP_LOSS = -0.08`
- BE 손절: `TRAIL_BE_ACTIVATE_PCT = 0.05`, `TRAIL_BE_STOP_PCT = -0.01`
- BE×Grace 교차: `portfolio_monitor_v2.py:878-885` (BE 활성 시 `max(grace_stop, pos.stop_loss_price)`)

## 메모리 파일 경로 주의
프로젝트 루트 `memory/` 디렉토리는 **없음** (심볼릭 링크 미설정). 실제 경로:
- `/home/hatni/.claude/projects/-home-hatni-korean-stock-ai-trading/memory/project_stop_loss_review.md`
- `/home/hatni/.claude/projects/-home-hatni-korean-stock-ai-trading/memory/project_strategy.md`

## 기존 메모리 주요 기록 (변경 대상)

### project_stop_loss_review.md
- 현재: 2026-04-13 기준 20포지션 분석, "Day2+ -5% 축소 안전" 결론
- 재평가 일정: 2026-05-01
- 후속: BE 손절(4/14), 종목당 예산 동적화(4/14)

### project_strategy.md (Line 15)
- 기존 표현: "손절 보호기간 | 매수 후 2거래일 -8% | (2026-04-05)"
- 문제: `GRACE_PERIOD_DAYS=1` 값과 "2거래일" 표현이 초보자에게 혼선 유발
- 해결: "매수당일 + N영업일 = 총 (N+1)거래일" 형식으로 통일

## 영향 범위
- `portfolio_monitor_v2.py`: 코드 변경 없음 → 영향 없음
- `config.py`: 변경 없음
- 운영 시스템: 재시작 불필요 (문서만 변경)

## 과거 유사 메타 교훈
Phase 1 focus:stop_loss 리허설 때 `project_stop_loss_review.md` 방향 재검토 필요성을 발견했으나, 당시 제안서 신중한 표현("즉각 수정 요청하지 않음")을 v1 플랜이 과대 적용. 향후 에이전트 제안 수용 시 **신뢰도 등급(Low/Medium/High)을 엄격히 따를 것**.
