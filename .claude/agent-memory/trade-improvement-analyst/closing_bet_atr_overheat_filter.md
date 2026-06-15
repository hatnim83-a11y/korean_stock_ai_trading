---
name: closing-bet-atr-overheat-filter
description: 종가베팅 atr_overheat 하드필터(1.8) 코드 위치/PRD 근거 + 비단조 라벨 분포 핵심 발견
metadata:
  type: project
---

종가베팅 atr_overheat 과열필터 재검토 (2026-06-15 분석).

- **코드 위치**: `closing_bet_system/engines/signal_score_engine.py:336-357` (하드필터), 기본값 `DEFAULT_ATR_OVERHEAT_MAX = 1.8` (line 79). settings.yaml `score:` 섹션에 `atr_overheat_max` 키 없음 → 코드 default 사용. `from_settings`가 `score_settings.get("atr_overheat_max", DEFAULT)` 폴백(line 246).
- **PRD 근거**: `종가베팅_트레이딩_시스템_PRD_v2.0.md:170` "ATR 과열도 = 당일 상승폭/ATR, 1.8 초과 시 제외 (구 +5% 룰 대체)".
- **핵심 발견 (비단조!)**: atr-rejected 33건을 밴드별로 쪼개면 단조롭지 않다. 1.8~2.0(n=7) stop위험 86% / 2.0~2.2(n=6) 67% / **2.2~2.5(n=13) 31%** / **2.5+(n=5) 0%, 아침고가 +18.6%**. 즉 "임계 2.2로 상향"하면 **최악 하위그룹(1.8~2.2)만 admit**하고 최고그룹은 여전히 거름 → 순진한 상향은 역효과. 예외룰(과열 AND close_strength≥0.7 OR foreign>0 → 27건 high +10.79%/stop 44%)이 더 정밀.

**Why:** 하드필터가 최고 알파(2.2+ 극과열 = 강모멘텀 연속)를 버리는데, 단순 임계 완화는 중간 위험구간을 먼저 푼다.
**How to apply:** 종가베팅 과열 관련 제안 시 항상 밴드별로 분해해서 보라. 단일 임계 비교 금지. 표본 33건이라 Low 신뢰도.
관련: [[closing-bet-exit-finding]], [[data-unit-gotchas]]
