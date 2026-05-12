"""modules.supply_collector — 외국인/기관 수급 신호 수집기 (v16, 2026-05-11).

종가베팅 시스템(closing_bet_system)이 단위 2-3 옵션 H에서 검증한 외국인 수급 데이터 소스를
메인 스윙 시스템에 이식. T-1 마감 데이터를 17:10에 1회 수집하여 다음날 아침 KIS 재호출 없이
DB 조회만으로 종목 선정에 사용한다.

수집 대상:
- 종목별 외국인/기관 5일 누적 수급 (FHKST01010900)
- 외국인/기관 순매수 상위 N (FHPTJ04400000)

Phase 1-A: 데이터 파이프라인만. 점수/필터/AI 통합은 1-B, 1-C, 1-D에서 단계적 진행.

참조:
- PLAN: docs/work-plans/active/supply-signal-integration/PLAN.md
- 원본 plan: ~/.claude/plans/kis-compiled-otter.md
"""

from .collector import SupplyCollector

__all__ = ["SupplyCollector"]
