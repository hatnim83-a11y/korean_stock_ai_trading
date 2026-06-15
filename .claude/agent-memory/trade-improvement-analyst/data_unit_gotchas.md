---
name: data-unit-gotchas
description: closing_bet.db 단위·경로 함정 — net_pnl_pct와 라벨은 분수, DB는 메인 repo 절대경로.
metadata:
  type: reference
---

종가베팅 DB 분석 시 단위/경로 함정:

- DB 절대경로: `/home/hatni/korean_stock_ai_trading/data/closing_bet.db` (worktree 아님, 메인 repo).
  worktree에서 cwd 상대경로 쓰면 빈/부재 DB 가리킴.
- `candidates.net_pnl_pct`, `candidate_labels.next_open_pct/next_morning_high_pct/next_morning_low_pct`는
  **모두 분수**(0.03 = +3%). `:+.2f%` 포맷으로 출력하면 -0.04(=-4%)가 "-0.04%"로 보여 오독함 → ×100 필수.
- 라벨 정의(label_provider.py): next_open_pct=(익일시가-당일종가)/종가, high/low는 익일 오전 고/저.
  net_ev_positive는 "오전고가가 비용선 도달" 낙관 라벨(고점매도 가정)이라 실현과 별개.
- entered 실거래 식별: `entry_time IS NOT NULL` (candidate_status='entered'와 동치, 현재 5건).
- 메인 분석 문서: docs/improvements/20260615_closing_bet_candidate_full_analysis.md (worktree docs 디렉토리).
