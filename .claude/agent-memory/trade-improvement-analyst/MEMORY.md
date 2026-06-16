# trade-improvement-analyst memory index

- [Closing-bet exit logic finding](closing_bet_exit_finding.md) — 종가베팅 청산 시장가 투매가 엣지 파괴 (실거래 5건 mechanism), 반사실 추정치
- [Data unit gotchas](data_unit_gotchas.md) — closing_bet.db net_pnl_pct·라벨 단위는 분수(0.03=3%), DB 절대경로 메인 repo
- [atr_overheat filter finding](closing_bet_atr_overheat_filter.md) — 과열필터 1.8 하드필터 위치/PRD 근거 + 비단조(non-monotonic) 라벨 분포: 2.2+ 극과열이 최고, 1.8~2.2 중과열이 최악
- [Trailing width / ATR cap finding](trailing_width_cap_finding.md) — 스윙 트레일링 cap 8% 사후검증: cap은 catastrophic 꼬리 1건만 차단(정상18건 무영향), 좁히면 휩쏘 반반. max_hold 만료=활성화 미달 약종목(폭 무관). cap 8% 유지 권고
- [Trailing MULTIPLIER dead-param finding](trailing_multiplier_dead_param_finding.md) — ATR_MULTIPLIER 2.0→1.5 축소 무효: 박제11건 중 10건 cap 8%에 묶여 불변. 폭 줄이려면 cap이 유일 레버(그래도 비권고). MULT 2.0 유지
