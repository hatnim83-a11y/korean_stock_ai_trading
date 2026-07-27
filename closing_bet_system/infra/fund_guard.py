"""closing_bet_system.infra.fund_guard

종가베팅 주문 직전 자금/비중/종목 한도 미들웨어 (P0-1).

같은 KIS 계좌에서 종가베팅과 스윙이 자금을 공유하므로, 종가베팅이 한도를 무시하고
주문하면 스윙 매수가 증거금 부족으로 실패할 수 있다. 이 모듈은 모든 종가베팅
주문의 첫 번째 게이트키퍼다.

검사 순서 (모두 AND, 하나라도 위반 시 차단):

1. **입력 검증** — ticker 6자리 숫자, amount 양의 정수
2. **총 자산 조회** — KIS API 실패 시 보수적 차단
3. **스윙 중복** — 절대 우선 안전 규칙 (DB 조회 전에 차단)
4. **1종목 비중** — ``신규 주문 ≤ total_value × capital_ratio × max_position_per_stock``
5. **자금 한도** — ``누적 사용액 + 신규 주문 ≤ total_value × capital_ratio``
6. **동시 보유 한도** — ``현재 entered 수 < max_concurrent_positions`` (새 종목인 경우)
7. **1일 진입 한도** — ``오늘 entered 수 < max_daily_entries`` (새 종목인 경우)

향후 Phase 1에서 추가될 검사 (settings.yaml ``fund.weekly_loss_limit`` 참조):
- 주간 손실 -5% 도달 시 매매 중지 (현재는 미구현, 운영자 오인 방지를 위해 명시)

사용:
    from closing_bet_system.infra.fund_guard import FundGuard

    guard = FundGuard()
    allowed, reason = guard.allow_order(ticker="005930", amount=300_000)
    if allowed:
        order_api.buy_limit_order(...)
    else:
        logger.warning(f"주문 차단: {reason}")
"""

from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

from closing_bet_system.storage.db import (
    ClosingBetDatabase,
    _load_settings,
    _resolve_db_path,
)


# DB 조회 실패 시 보수적 차단을 위한 sentinel.
# 실제 어떤 자금/종목/거래도 이 값을 초과할 수 없을 만큼 큼.
_CONSERVATIVE_LARGE_AMOUNT = 10**12   # 1조원 (자금 한도 트리거)
_CONSERVATIVE_LARGE_COUNT = 10**6     # 100만 (카운트 한도 트리거)

# 잔고 조회 실패 시 차단 사유 문자열 (기존 로그/테스트 호환 유지).
_BALANCE_FAIL_REASON = "총 자산 조회 실패 또는 0원 (보수적 차단)"


@dataclass(frozen=True)
class OrderDecision:
    """주문 허용 판정 결과.

    Attributes:
        allowed: 허용 여부.
        reason: 차단 사유(허용 시 빈 문자열).
        transient: True 면 **일시적/인프라성 실패**(KIS 5xx 잔고조회·DB 조회 실패 등).
            호출 측은 이 경우 후보를 영구 ``rejected_filter`` 로 확정하지 말고
            ``recommended`` 유지(다음 재시도/관측 가능)해야 한다. 진짜 한도 초과·
            입력오류는 ``transient=False`` (영구 거부 정당).
    """

    allowed: bool
    reason: str
    transient: bool = False


@dataclass(frozen=True)
class GuardConfig:
    """fund_guard 한도 파라미터. settings.yaml 의 ``fund:`` 섹션을 매핑.

    2026-05-23: 동적 자본 분리(absorb_swing_idle) 필드 5개 추가.
    """

    capital_ratio: float = 0.10                 # 종가베팅 base 자금 비중 (기본 10%)
    max_position_per_stock: float = 0.25        # 1종목 최대 비중 (absorb 활성 시 unused)
    max_concurrent_positions: int = 4
    max_daily_entries: int = 4                  # 2026-05-23: 4종목 강제 진입에 맞춰 2→4
    weekly_loss_limit: float = -0.05
    # 동적 자본 분리 (2026-05-23 도입)
    absorb_swing_idle: bool = False             # 기본 False (롤백 안전)
    swing_capital_ratio: float = 0.9            # 스윙 풀 비율
    closing_bet_pool_cap: float = 0.5           # 종가베팅 풀 최대 cap
    swing_used_source: str = "cost_basis"       # 'cost_basis' | 'evaluation'
    disable_absorb_on_crisis: bool = True       # CRISIS 시 absorb 비활성
    crisis_absorb_ratio: float = 0.0            # 2026-05-29: 위기 시 부분 흡수 비율 (0.0=무흡수, 1.0=전체)
    # 잔고 조회 일시적 실패(KIS 5xx) 재시도 (2026-07-10 도입)
    balance_retry_count: int = 2                # 최초 1회 실패 후 추가 재시도 횟수 (총 시도=1+N)
    balance_retry_backoff_sec: float = 0.5      # 재시도 사이 대기(초). 테스트는 sleep_func 주입

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None) -> "GuardConfig":
        s = settings if settings is not None else _load_settings()
        f = s.get("fund", {})
        # 범위 검증 (DC 주의 5): closing_bet_pool_cap 0 < val <= 1.0
        cap = float(f.get("closing_bet_pool_cap", cls.closing_bet_pool_cap))
        if not (0.0 < cap <= 1.0):
            logger.warning(
                f"[fund_guard] closing_bet_pool_cap={cap} 범위 이탈 → 기본값 {cls.closing_bet_pool_cap} 사용"
            )
            cap = cls.closing_bet_pool_cap
        used_src = str(f.get("swing_used_source", cls.swing_used_source))
        if used_src not in ("cost_basis", "evaluation"):
            logger.warning(
                f"[fund_guard] swing_used_source={used_src!r} 미지원 → 'cost_basis' 폴백"
            )
            used_src = "cost_basis"
        # 범위 검증 (2026-05-29): crisis_absorb_ratio 0.0~1.0
        absorb_ratio = float(f.get("crisis_absorb_ratio", cls.crisis_absorb_ratio))
        if not (0.0 <= absorb_ratio <= 1.0):
            logger.warning(
                f"[fund_guard] crisis_absorb_ratio={absorb_ratio} 범위 이탈 → 기본값 {cls.crisis_absorb_ratio} 사용"
            )
            absorb_ratio = cls.crisis_absorb_ratio
        return cls(
            capital_ratio=float(f.get("capital_ratio", cls.capital_ratio)),
            max_position_per_stock=float(
                f.get("max_position_per_stock", cls.max_position_per_stock)
            ),
            max_concurrent_positions=int(
                f.get("max_concurrent_positions", cls.max_concurrent_positions)
            ),
            max_daily_entries=int(f.get("max_daily_entries", cls.max_daily_entries)),
            weekly_loss_limit=float(f.get("weekly_loss_limit", cls.weekly_loss_limit)),
            absorb_swing_idle=bool(f.get("absorb_swing_idle", cls.absorb_swing_idle)),
            swing_capital_ratio=float(
                f.get("swing_capital_ratio", cls.swing_capital_ratio)
            ),
            closing_bet_pool_cap=cap,
            swing_used_source=used_src,
            disable_absorb_on_crisis=bool(
                f.get("disable_absorb_on_crisis", cls.disable_absorb_on_crisis)
            ),
            crisis_absorb_ratio=absorb_ratio,
            balance_retry_count=max(0, int(f.get("balance_retry_count", cls.balance_retry_count))),
            balance_retry_backoff_sec=max(
                0.0, float(f.get("balance_retry_backoff_sec", cls.balance_retry_backoff_sec))
            ),
        )


class FundGuard:
    """종가베팅 주문 게이트키퍼.

    의존성 주입 가능:
    - ``total_value_provider``: 0-인자 callable, 총 평가금액(원) 반환
    - ``swing_holdings_provider``: 0-인자 callable, set[str] 반환

    기본값은 ``infra.kis_client`` / ``infra.swing_db_reader`` 사용.
    테스트 시 위 두 콜백을 주입해 KIS API / 스윙 DB 의존성을 mock 한다.
    """

    def __init__(
        self,
        config: Optional[GuardConfig] = None,
        db_path: Optional[Path] = None,
        total_value_provider: Optional[Callable[[], int]] = None,
        swing_holdings_provider: Optional[Callable[[], set[str]]] = None,
        swing_used_provider: Optional[Callable[[str], int]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ):
        self.config = config or GuardConfig.from_settings()
        self.db_path = db_path or _resolve_db_path(_load_settings())

        # 콜백 lazy import — 테스트 시 주입 가능, 운영 시 기본 구현 사용
        self._total_value_provider = total_value_provider
        self._swing_holdings_provider = swing_holdings_provider
        # 2026-05-23 동적 자본 분리 — 스윙 사용 자본 조회 콜백 (테스트 mock용)
        self._swing_used_provider = swing_used_provider
        # 2026-07-10 잔고 재시도 backoff sleep (테스트는 no-op 주입해 실대기 회피)
        self._sleep = sleep_func or time.sleep

    # ===== 외부 API =====

    def allow_order(
        self,
        ticker: str,
        amount: int,
        *,
        external_risk_active: bool = False,
    ) -> tuple[bool, str]:
        """주문 허용 여부 + 차단 사유 (후방호환 wrapper).

        내부적으로 :meth:`evaluate_order` 를 호출하고 ``(allowed, reason)`` 만 반환한다.
        일시적/영구 구분(``transient``)이 필요하면 :meth:`evaluate_order` 를 직접 쓴다.
        """
        d = self.evaluate_order(ticker, amount, external_risk_active=external_risk_active)
        return d.allowed, d.reason

    def evaluate_order(
        self,
        ticker: str,
        amount: int,
        *,
        external_risk_active: bool = False,
    ) -> OrderDecision:
        """주문 허용 판정 (transient 구분 포함).

        Args:
            ticker: 6자리 종목코드
            amount: 주문 금액(원). 호출 측이 (1주 가격 × 수량 + 수수료 추정치)
                를 ``int`` 로 올림 처리하여 전달해야 한다. ``float``/음수는 차단.
            external_risk_active: MarketGuard CRISIS/DANGER 상태 (2026-05-23 추가).

        Returns:
            :class:`OrderDecision`. ``transient=True`` 인 차단은 인프라 일시 실패
            (잔고조회 5xx·종가베팅 DB 조회 실패)로, 호출 측은 후보를 영구 확정하지
            말아야 한다. **어떤 경우에도 잔고를 추정해 허용하지 않는다.**
        """
        # 1) 입력 검증 (영구 — 재시도해도 안 바뀜)
        if not isinstance(ticker, str) or not ticker.isdigit() or len(ticker) != 6:
            return OrderDecision(False, f"유효하지 않은 종목코드: {ticker!r}")
        # bool 은 int 의 subclass 이므로 명시 거부
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return OrderDecision(False, f"주문 금액 오류 (양의 정수 필요): {amount!r}")

        cfg = self.config

        # 2) 총 자산 조회 — 일시적 실패(5xx) 재시도 후에도 0 이면 transient 차단.
        #    추정치로 대체하지 않는다(항상 차단측 안전).
        total_value = self._fetch_total_value_with_retry()
        if total_value <= 0:
            return OrderDecision(False, _BALANCE_FAIL_REASON, transient=True)

        # 3) 스윙 중복 — 절대 우선. DB 조회 전에 차단해 자원 낭비 방지.
        swing_held = self._get_swing_holdings()
        if ticker in swing_held:
            return OrderDecision(False, f"스윙 시스템이 이미 보유 중인 종목: {ticker}")

        # 4) 동적 capital_limit 계산 (SoT, 2026-05-23) — S-1/S-2 반영
        capital_limit, debug_info = self.compute_capital_limit(
            total_value, external_risk_active=external_risk_active
        )
        # per_stock_limit: cfg.max_concurrent_positions 등분 (4 하드코딩 회피)
        per_stock_limit = (
            capital_limit // cfg.max_concurrent_positions
            if cfg.max_concurrent_positions > 0 else 0
        )

        # 5) 1종목 비중 한도 (DB 조회 불필요)
        if amount > per_stock_limit:
            return OrderDecision(False, (
                f"1종목 비중 초과: 주문 {amount:,}원 > 한도 {per_stock_limit:,}원 "
                f"(capital_limit {capital_limit:,}원 ÷ {cfg.max_concurrent_positions}종목 | "
                f"{debug_info.get('mode', '?')})"
            ))

        # 5~7) DB 조회 통합 — 단일 connection + 단일 트랜잭션 스냅샷 (TOCTOU 방지)
        try:
            db_state = self._fetch_db_state()
        except sqlite3.Error as e:
            logger.error(f"[fund_guard] DB 상태 조회 실패 (보수적 차단): {e}")
            # DB 조회 실패도 인프라 일시 실패 → transient (영구 확정 금지)
            return OrderDecision(False, f"종가베팅 DB 조회 실패: {e}", transient=True)

        # 5) 자금 한도 (누적 + 신규)
        if db_state["active_amount"] + amount > capital_limit:
            return OrderDecision(False, (
                f"자금 한도 초과: 현재 사용 {db_state['active_amount']:,}원 + 주문 {amount:,}원 "
                f"= {db_state['active_amount'] + amount:,}원 > 한도 {capital_limit:,}원"
            ))

        # 6~7) 동시 보유 / 일일 진입 — 이미 활성화된 ticker 면 추가 매수 허용
        is_new_ticker = ticker not in db_state["active_tickers"]
        if is_new_ticker:
            active_count = len(db_state["active_tickers"])
            if active_count >= cfg.max_concurrent_positions:
                return OrderDecision(False, (
                    f"동시 보유 한도 초과: 활성 {active_count}종목 "
                    f">= 한도 {cfg.max_concurrent_positions}종목"
                ))

            if db_state["today_entries"] >= cfg.max_daily_entries:
                return OrderDecision(False, (
                    f"1일 진입 한도 초과: 오늘 {db_state['today_entries']}건 "
                    f">= 한도 {cfg.max_daily_entries}건"
                ))

        # 8) 주간 손실 한도 — 최근 7일 누적 net_pnl_pct 가 임계값 이하면 매매 중지.
        # entered + exit_time 채워진 후보들의 net_pnl_pct 합계 (개별 거래 손익률을 단순 합산).
        # 임계값(-0.05) 이하 → 차단. weekly_pnl 이 None(데이터 없음) 시 통과.
        weekly_pnl = db_state.get("weekly_pnl")
        if weekly_pnl is not None and weekly_pnl <= cfg.weekly_loss_limit:
            return OrderDecision(False, (
                f"주간 손실 한도 초과: 최근 7일 누적 {weekly_pnl:+.2%} "
                f"<= 한도 {cfg.weekly_loss_limit:+.2%}"
            ))

        return OrderDecision(True, "")

    def _fetch_total_value_with_retry(self) -> int:
        """총 자산 조회 — 일시적 실패(예외/0) 시 제한된 재시도.

        시도 횟수 = ``1 + balance_retry_count``. 각 실패 후 ``balance_retry_backoff_sec``
        만큼 ``self._sleep`` (테스트는 no-op 주입). 유효값(>0) 확보 즉시 반환.
        모두 실패 시 0 반환 → 호출 측이 transient 차단 (추정 대체 없음).
        """
        attempts = 1 + max(0, self.config.balance_retry_count)
        backoff = self.config.balance_retry_backoff_sec
        for i in range(attempts):
            try:
                value = self._get_total_value()
            except Exception as e:  # KIS 5xx 등 — 다음 시도로
                value = 0
                logger.warning(
                    f"[fund_guard] 총 자산 조회 예외(시도 {i + 1}/{attempts}): {e}"
                )
            if value and value > 0:
                if i > 0:
                    logger.info(
                        f"[fund_guard] 총 자산 조회 재시도 성공(시도 {i + 1}/{attempts}): {value:,}원"
                    )
                return int(value)
            if i < attempts - 1:
                logger.warning(
                    f"[fund_guard] 총 자산 조회 실패(시도 {i + 1}/{attempts}) → "
                    f"{backoff}s 후 재시도"
                )
                if backoff > 0:
                    self._sleep(backoff)
        logger.error(
            f"[fund_guard] 총 자산 조회 {attempts}회 모두 실패 — 일시적(transient) 차단"
        )
        return 0

    # ===== Single Source of Truth: 동적 자본 한도 계산 (2026-05-23) =====

    def compute_capital_limit(
        self,
        total_value: int,
        *,
        external_risk_active: bool = False,
    ) -> tuple[int, dict]:
        """동적 종가베팅 capital_limit 계산 — fund_guard + entry_executor 공통 진입점.

        흐름 (Plan 알고리즘 정합):
            1. base_pool = total × capital_ratio (10%)
            2. absorb_swing_idle=False 또는 (external_risk_active AND disable_absorb_on_crisis)
               → base_pool 반환 (흡수 비활성)
            3. swing_pool = total × swing_capital_ratio (90%)
            4. swing_used = get_swing_used_value(source)
               SwingDBUnavailable → swing_pool 폴백 (swing_idle=0, 보수)
            5. swing_idle = max(0, swing_pool - swing_used)
            5-b. (2026-05-29 추가) external_risk_active AND NOT disable_absorb_on_crisis
                 → swing_idle = int(swing_idle × crisis_absorb_ratio) (부분 흡수)
            6. cap_amount = total × closing_bet_pool_cap (50%)
            7. return min(base_pool + swing_idle, cap_amount)

        Args:
            total_value: KIS 총 평가금액 (원)
            external_risk_active: CRISIS/DANGER 시 True

        Returns:
            ``(capital_limit, debug_info)``
            debug_info keys (정상): mode / base_pool / swing_pool / swing_used /
                swing_idle_raw / swing_idle / cap_amount / dynamic_pool / capped /
                external_risk_active / crisis_absorb_ratio
            debug_info keys (base_only): mode / base_pool [+ external_risk if crisis]
        """
        cfg = self.config
        base_pool = int(total_value * cfg.capital_ratio)

        # 흡수 비활성 분기
        if not cfg.absorb_swing_idle:
            logger.debug(
                f"[fund_guard] base_only(absorb_off) — capital_limit={base_pool:,}원 "
                f"(absorb_swing_idle=False)"
            )
            return base_pool, {"mode": "base_only(absorb_off)", "base_pool": base_pool}
        if external_risk_active and cfg.disable_absorb_on_crisis:
            logger.debug(
                f"[fund_guard] base_only(crisis) — capital_limit={base_pool:,}원 "
                f"(external_risk_active=True, disable_absorb_on_crisis=True)"
            )
            return base_pool, {
                "mode": "base_only(crisis)",
                "base_pool": base_pool,
                "external_risk": True,
            }

        # 스윙 풀 + 유휴자본
        swing_pool = int(total_value * cfg.swing_capital_ratio)
        try:
            swing_used = self._get_swing_used_value()
        except Exception as e:
            # DB 파일 없음 / 쿼리 실패 → swing_pool 폴백 (idle=0, 보수)
            logger.warning(
                f"[fund_guard] swing_used 조회 실패 ({e}) → swing_pool 폴백 (idle=0)"
            )
            swing_used = swing_pool  # idle=0 강제

        swing_idle_raw = max(0, swing_pool - swing_used)
        # 위기 시 부분 흡수 (2026-05-29): external_risk_active 시 swing_idle × crisis_absorb_ratio
        # disable_absorb_on_crisis=true 분기는 위에서 이미 base만 반환했으므로 여기 도달 X
        # disable_absorb_on_crisis=false + external_risk_active 시 부분 흡수 적용
        if external_risk_active and cfg.crisis_absorb_ratio < 1.0:
            swing_idle = int(swing_idle_raw * cfg.crisis_absorb_ratio)
            absorb_mode = "absorb_swing_idle(crisis_partial)"
        else:
            swing_idle = swing_idle_raw
            absorb_mode = "absorb_swing_idle"
        cap_amount = int(total_value * cfg.closing_bet_pool_cap)
        dynamic_pool = base_pool + swing_idle
        capital_limit = min(dynamic_pool, cap_amount)

        debug_info = {
            "mode": absorb_mode,
            "base_pool": base_pool,
            "swing_pool": swing_pool,
            "swing_used": swing_used,
            "swing_idle_raw": swing_idle_raw,
            "swing_idle": swing_idle,
            "cap_amount": cap_amount,
            "dynamic_pool": dynamic_pool,
            "capped": dynamic_pool > cap_amount,
            "external_risk_active": external_risk_active,
            "crisis_absorb_ratio": cfg.crisis_absorb_ratio,
        }
        logger.info(
            f"[fund_guard] capital_limit={capital_limit:,}원 — "
            f"base={base_pool:,}/idle={swing_idle:,}(raw={swing_idle_raw:,})/cap={cap_amount:,} "
            f"(used={swing_used:,}원/pool={swing_pool:,}원, capped={debug_info['capped']}, "
            f"mode={absorb_mode})"
        )
        return capital_limit, debug_info

    # ===== 내부 헬퍼 (override 가능) =====

    def _get_total_value(self) -> int:
        if self._total_value_provider is not None:
            return self._total_value_provider()
        # lazy import — 모듈 로드 시 KIS 토큰 발급 회피
        from closing_bet_system.infra.kis_client import get_total_account_value

        return get_total_account_value()

    def _get_swing_used_value(self) -> int:
        """스윙 사용 자본 조회 (provider 주입 가능, 2026-05-23 동적 자본 분리).

        Raises:
            SwingDBUnavailable: 스윙 DB 파일 없음 또는 쿼리 실패. compute_capital_limit
                에서 swing_pool 폴백 처리.
        """
        if self._swing_used_provider is not None:
            return self._swing_used_provider(self.config.swing_used_source)
        # lazy import
        from closing_bet_system.infra.swing_db_reader import get_swing_used_value
        return get_swing_used_value(source=self.config.swing_used_source)

    def _get_swing_holdings(self) -> set[str]:
        if self._swing_holdings_provider is not None:
            return self._swing_holdings_provider()
        from closing_bet_system.infra.swing_db_reader import get_swing_holding_codes

        return get_swing_holding_codes()

    def _open_db(self) -> sqlite3.Connection:
        """closing_bet.db read-only 연결 (조회 전용)."""
        return sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=10.0,
        )

    def _fetch_db_state(self) -> dict:
        """allow_order() 한 번에 필요한 모든 DB 정보를 단일 connection 으로 조회.

        TOCTOU(Time-of-check-to-time-of-use) 위험 회피 — 4 쿼리가 같은 스냅샷을 본다.

        Returns:
            dict 키: ``active_amount`` (int), ``active_tickers`` (set[str]),
            ``today_entries`` (int), ``weekly_pnl`` (Optional[float] — 최근 7일 net_pnl_pct 합계,
            데이터 없으면 None)

        Raises:
            ``sqlite3.Error`` — 호출 측이 catch 하여 보수적 차단 처리
        """
        conn = self._open_db()
        try:
            cur = conn.cursor()

            # 활성 포지션 (entered + 미청산) — 자금 한도 + 동시 보유 동시 활용
            cur.execute(
                """
                SELECT ticker, entry_amount
                FROM candidates
                WHERE candidate_status = 'entered'
                  AND exit_time IS NULL
                """
            )
            rows = cur.fetchall()
            active_amount = sum(int(r[1] or 0) for r in rows)
            active_tickers = {r[0] for r in rows if r[0]}

            # 오늘 (KST) entered 처리 수
            today_dt = now_kst().date()
            today = today_dt.isoformat()
            cur.execute(
                """
                SELECT COUNT(*)
                FROM candidates
                WHERE candidate_status = 'entered'
                  AND trade_date = ?
                """,
                (today,),
            )
            row = cur.fetchone()
            today_entries = int(row[0]) if row and row[0] else 0

            # 최근 7일 누적 net_pnl_pct (entered + exit_time 채워진 후보).
            # 영업일 5일 ≒ 캘린더 7일. exit_time 은 TIMESTAMP 라 SUBSTR 비교 회피하려고
            # trade_date(DATE) 로 비교 — Phase 1/2 종가베팅은 T 진입 → T+1 매도 패턴 (16~18시간)
            # 이라 trade_date 기준 7일 윈도가 정확함.
            # Phase 3 다일 보유 도입 시 (T 진입 → T+N 매도, N>1), trade_date 가 윈도 밖이지만
            # exit_time 이 윈도 안인 거래가 누락되므로 exit_date 기준 쿼리로 재검토 필요.
            week_start = (today_dt - timedelta(days=7)).isoformat()
            cur.execute(
                """
                SELECT SUM(net_pnl_pct), COUNT(net_pnl_pct)
                FROM candidates
                WHERE candidate_status = 'entered'
                  AND exit_time IS NOT NULL
                  AND trade_date >= ?
                """,
                (week_start,),
            )
            row = cur.fetchone()
            weekly_pnl: Optional[float] = None
            if row and row[1] and int(row[1]) > 0:
                # SUM 이 None 일 가능성 (모든 net_pnl_pct 가 NULL): 가드
                if row[0] is not None:
                    weekly_pnl = float(row[0])

            return {
                "active_amount": active_amount,
                "active_tickers": active_tickers,
                "today_entries": today_entries,
                "weekly_pnl": weekly_pnl,
            }
        finally:
            conn.close()
