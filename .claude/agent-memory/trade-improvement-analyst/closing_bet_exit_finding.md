---
name: closing-bet-exit-finding
description: 종가베팅 실거래 5건 손실(-3.00% avg)의 원인은 신호가 아니라 청산 시장가 투매. 반사실 추정과 mechanism.
metadata:
  type: project
---

종가베팅(closing_bet_system) 실거래 5건 실현손익 평균 -3.00%, 합계 -14.98%, 승률 20%인데,
같은 종목 라벨(candidate_labels)은 익일 시가갭 평균 +0.90%, 아침고가 +3.02%로 양호.

**Why (mechanism, 로그·DB로 검증됨):** exit_executor.py가 09:01 emergency_stop(hard_stop_loss=-0.01)
+ 09:02 morning_exit 모두 `sell_market_order`(시장가)만 호출 → 시가 직후 형성되는 오전 dip에 시장가 투매.
실제 청산가가 익일 시가 대비 -1.78% ~ -6.30% 아래에서 체결됨 (5건 전부). HPSP 5/26: 시가 +3.00%인데
청산가는 시가 대비 -6.30%(=net -3.9%). HPSP 6/9: -1% 손절 직후 아침고가 +4.56% 반등.

**반사실 추정 (비용 ~0.41% 차감, n=5):**
- 현행 실현: avg -3.00% / sum -14.98% / win 1/5
- [A] 시가매도(open_pct): avg +0.49% / sum +2.45% / win 2/5
- [B] (시가+오전고가)/2 캡처: avg +1.55% / sum +7.75% / win 3/5
- [C] emergency만 시가매도+나머지 고가-1.5%트레일링: avg +0.94% / sum +4.72% / win 2/5

**How to apply:** sell_limit_order(price)는 이미 존재(kis_order_api.py:377). morning snapshot은 이미 open_price
포함. 제안 = 시장가→시가 지정가 전환 + 오전 트레일링/부분익절. 표본 5건뿐이라 dry_run 재검증 필수.
관련 제안서: docs/improvements/20260615_closing_bet_exit_logic_proposal.md
