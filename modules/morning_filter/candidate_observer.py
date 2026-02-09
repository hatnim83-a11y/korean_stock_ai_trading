"""
candidate_observer.py - 09:05~09:23 실시간 관찰 루프

스크리닝 후 매수 전까지 3분 간격으로 후보 종목의 가격/수급/거래량을
누적 추적하여, 부정적 트렌드가 감지된 종목을 매수 전에 제외합니다.

타임라인:
    09:05  스크리닝 완료 → 관찰 시작
    09:08  [Observation 1/6]
    09:11  [Observation 2/6]
    ...
    09:23  [Observation 6/6] → analyze_trends()
    09:25  execute_buy_orders() → Step 5 트렌드 필터에서 결과 사용
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional

from logger import logger
from config import settings, now_kst


@dataclass
class ObservationSnapshot:
    """단일 관찰 시점의 데이터"""
    timestamp: datetime
    price: int
    open_price: int
    volume: int
    change_rate: float
    foreign_net: int
    institution_net: int


@dataclass
class StockObservation:
    """종목별 관찰 누적 데이터 + 트렌드 분석 결과"""
    stock_code: str
    stock_name: str
    prev_close: int
    snapshots: list[ObservationSnapshot] = field(default_factory=list)
    # 트렌드 분석 결과
    price_weakness: bool = False
    supply_reversal: bool = False
    volume_stagnation: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)


@dataclass
class ObservationResult:
    """전체 관찰 결과"""
    total_snapshots: int
    observation_start: datetime
    observation_end: datetime
    stocks_observed: int
    stocks_flagged: int
    observations: dict[str, StockObservation] = field(default_factory=dict)


class CandidateObserver:
    """
    후보 종목 실시간 관찰기

    3분 간격으로 가격/수급/거래량을 수집하고,
    부정적 트렌드가 감지된 종목을 플래그합니다.
    """

    def __init__(self, candidates: list[dict], notifier=None):
        """
        Args:
            candidates: 스크리닝 통과 후보 종목 리스트
            notifier: TelegramNotifier (선택)
        """
        self.candidates = candidates
        self.notifier = notifier
        self._running = False
        self._kis_api = None

        # 종목별 관찰 데이터 초기화
        self.observations: dict[str, StockObservation] = {}
        for c in candidates:
            code = c.get("stock_code", c.get("code", ""))
            name = c.get("stock_name", c.get("name", ""))
            prev_close = c.get("prev_close", 0)
            self.observations[code] = StockObservation(
                stock_code=code,
                stock_name=name,
                prev_close=prev_close,
            )

    @property
    def kis_api(self):
        if self._kis_api is None:
            from modules.stock_screener.kis_api import KISApi
            self._kis_api = KISApi()
        return self._kis_api

    # ===== 관찰 루프 =====

    async def start_observation(self) -> ObservationResult:
        """
        관찰 루프 실행 (09:05 ~ 09:23)

        Returns:
            ObservationResult: 전체 관찰 결과
        """
        self._running = True
        start_time = now_kst()
        deadline = datetime.combine(
            start_time.date(), dtime(9, 23), tzinfo=start_time.tzinfo
        )

        # 남은 시간으로 가능한 사이클 수 계산
        remaining = (deadline - start_time).total_seconds()
        interval = settings.OBSERVATION_INTERVAL_SECONDS
        max_cycles = min(
            settings.OBSERVATION_MAX_CYCLES,
            max(1, int(remaining // interval)),
        )

        logger.info(f"👁️ 관찰 시작 ({len(self.candidates)}종목, 최대 {max_cycles}회)")
        logger.info(f"   간격: {interval}초, 마감: 09:23")

        cycle = 0
        while self._running and cycle < max_cycles:
            now = now_kst()
            if now >= deadline:
                logger.info("⏰ 관찰 마감 시간 도달")
                break

            cycle += 1
            logger.info(f"\n📡 [Observation {cycle}/{max_cycles}]")

            try:
                await asyncio.to_thread(self._collect_snapshot, cycle)
            except Exception as e:
                logger.warning(f"스냅샷 수집 실패 (cycle {cycle}): {e}")

            # 다음 사이클 대기 (마지막 사이클 제외)
            if cycle < max_cycles and self._running:
                now = now_kst()
                next_time = start_time.timestamp() + cycle * interval
                wait = max(0, next_time - now.timestamp())
                if wait > 0:
                    await asyncio.sleep(wait)

        end_time = now_kst()

        # 트렌드 분석
        flagged = self.analyze_trends()

        result = ObservationResult(
            total_snapshots=cycle,
            observation_start=start_time,
            observation_end=end_time,
            stocks_observed=len(self.observations),
            stocks_flagged=flagged,
            observations=self.observations,
        )

        elapsed = (end_time - start_time).total_seconds()
        logger.info(f"\n✅ 관찰 완료 ({elapsed:.0f}초, {cycle}회)")
        logger.info(f"   관찰 종목: {len(self.observations)}개")
        logger.info(f"   플래그 종목: {flagged}개")

        # 플래그된 종목 알림
        if flagged > 0 and self.notifier:
            flagged_list = []
            for obs in self.observations.values():
                if obs.exclusion_reasons:
                    reasons = ", ".join(obs.exclusion_reasons)
                    flagged_list.append(f"  - {obs.stock_name}: {reasons}")
            flag_text = "\n".join(flagged_list)
            self.notifier.send_message(
                f"👁️ 관찰 결과 ({cycle}회 수집)\n\n"
                f"⚠️ 부정 신호 {flagged}건:\n{flag_text}\n\n"
                f"09:25 매수 시 제외 예정"
            )

        return result

    def stop(self):
        """관찰 중단"""
        self._running = False

    # ===== 데이터 수집 =====

    def _collect_snapshot(self, cycle: int):
        """
        전 종목 스냅샷 1회 수집 (동기, to_thread로 호출)
        """
        ts = now_kst()
        collected = 0

        for code, obs in self.observations.items():
            try:
                price_data = self.kis_api.get_current_price(code)
                if not price_data:
                    continue

                # 당일 수급 (days=1 → 오늘만)
                supply_data = self.kis_api.get_investor_trading(code, days=1)
                foreign_net = 0
                institution_net = 0
                if supply_data:
                    foreign_net = supply_data.get("foreign_net", 0)
                    institution_net = supply_data.get("institution_net", 0)

                snapshot = ObservationSnapshot(
                    timestamp=ts,
                    price=price_data.get("price", 0),
                    open_price=price_data.get("open", 0),
                    volume=price_data.get("volume", 0),
                    change_rate=price_data.get("change_rate", 0.0),
                    foreign_net=foreign_net,
                    institution_net=institution_net,
                )
                obs.snapshots.append(snapshot)
                collected += 1

                logger.debug(
                    f"  [{obs.stock_name}] {snapshot.price:,}원 "
                    f"({snapshot.change_rate:+.2f}%) "
                    f"vol={snapshot.volume:,}"
                )

            except Exception as e:
                logger.warning(f"  [{code}] 수집 실패: {e}")

            time.sleep(settings.KIS_API_DELAY)

        logger.info(f"   수집 완료: {collected}/{len(self.observations)}종목")

    # ===== 트렌드 분석 =====

    def analyze_trends(self) -> int:
        """
        전 종목 트렌드 분석 실행

        Returns:
            플래그된 종목 수
        """
        flagged = 0
        for code, obs in self.observations.items():
            if len(obs.snapshots) < 2:
                continue

            obs.exclusion_reasons = []

            self._check_price_weakness(obs)
            if settings.TREND_SUPPLY_REVERSAL_ENABLED:
                self._check_supply_reversal(obs)
            self._check_volume_stagnation(obs)

            if obs.exclusion_reasons:
                flagged += 1
                logger.info(
                    f"⚠️ [{obs.stock_name}] 제외 신호: "
                    f"{', '.join(obs.exclusion_reasons)}"
                )

        return flagged

    def _check_price_weakness(self, obs: StockObservation):
        """
        가격 약세 감지

        - 시가 대비 현재가 ≤ threshold → 제외
        - 연속 N회 이상 가격 하락 → 제외
        """
        latest = obs.snapshots[-1]
        open_price = latest.open_price

        # 시가 대비 하락률 체크
        if open_price > 0:
            drop_from_open = ((latest.price - open_price) / open_price) * 100
            if drop_from_open <= settings.TREND_PRICE_DROP_THRESHOLD:
                obs.price_weakness = True
                obs.exclusion_reasons.append(
                    f"시가대비 {drop_from_open:+.1f}%"
                )
                return

        # 연속 하락 체크
        consecutive_down = 0
        for i in range(1, len(obs.snapshots)):
            if obs.snapshots[i].price < obs.snapshots[i - 1].price:
                consecutive_down += 1
            else:
                consecutive_down = 0

        if consecutive_down >= settings.TREND_PRICE_DOWNTREND_COUNT:
            obs.price_weakness = True
            obs.exclusion_reasons.append(
                f"연속 {consecutive_down}회 하락"
            )

    def _check_supply_reversal(self, obs: StockObservation):
        """
        수급 반전 감지

        초반 2회 외인+기관 순매수 > 0 → 최근 2회 < 0 → 제외
        """
        snaps = obs.snapshots
        if len(snaps) < 4:
            return

        def avg_net(slc):
            total = sum(s.foreign_net + s.institution_net for s in slc)
            return total / len(slc) if slc else 0

        early_avg = avg_net(snaps[:2])
        recent_avg = avg_net(snaps[-2:])

        if early_avg > 0 and recent_avg < 0:
            obs.supply_reversal = True
            obs.exclusion_reasons.append(
                f"수급반전 ({early_avg/1e8:+.1f}억→{recent_avg/1e8:+.1f}억)"
            )

    def _check_volume_stagnation(self, obs: StockObservation):
        """
        거래량 정체 감지

        최근 2회 관찰에서 3분간 누적 거래량 증가율 < 0.5% → 제외
        """
        snaps = obs.snapshots
        if len(snaps) < 3:
            return

        # 최근 2개 구간의 거래량 증가율 체크
        stagnant_count = 0
        for i in range(len(snaps) - 2, len(snaps)):
            prev_vol = snaps[i - 1].volume
            curr_vol = snaps[i].volume
            if prev_vol > 0:
                increase_rate = ((curr_vol - prev_vol) / prev_vol) * 100
                if increase_rate < 0.5:
                    stagnant_count += 1

        if stagnant_count >= 2:
            obs.volume_stagnation = True
            obs.exclusion_reasons.append("거래량 정체")
