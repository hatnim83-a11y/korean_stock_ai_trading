"""SupplyCollector — 일별 외국인/기관 수급 수집기 (Phase 1-A).

매일 17:10 KST에 다음을 수행:
1. 종목 풀 결정 (테마 종목 + 보유 종목 + 시총 TOP200 합집합)
2. 종목별 FHKST01010900 5일 수급 조회 → daily_supply_snapshot 저장
   (stock_name 컬럼은 Phase 1-A에서 미수집 — KIS get_investor_trading 응답에 없음)
3. FHPTJ04400000 외국인 순매수 상위 N 조회 → foreign_top_ranking 저장
4. FHPTJ04400000 기관 순매수 상위 N (Phase 1-A에서는 스킵 — 후속 단위에서 etc_cls='2' 직접 호출 헬퍼 추가 시 활성화)
5. 결과 dict 반환 (텔레그램 알림용)

설계 원칙:
- KIS rate limit 준수 (api_delay 0.11s × 200종목 ≈ 22초)
- 단일 종목 3회 retry (지수 백오프 1·2·4초). 그래도 실패 시 NULL 행 저장 + 다음 진행
- 멱등성: INSERT OR REPLACE (같은 trade_date 재실행 안전)
- 동기 메서드 (KIS API가 동기 requests 기반). main.py에서 asyncio.to_thread()로 래핑.

참조: docs/work-plans/active/supply-signal-integration/PLAN.md
"""
from __future__ import annotations

import time
from datetime import date
from typing import Optional

from logger import logger
from config import now_kst, settings


# 진행률 로그 출력 주기 (universe 처리 종목 수 기준)
_LOG_EVERY_N = 50


class SupplyCollector:
    """일별 외국인/기관 수급 수집기.

    Args:
        db: Database 인스턴스
        kis_api: KISApi 인스턴스 (modules.stock_screener.kis_api)
        market_provider: KisMarketProvider 인스턴스 (closing_bet_system.collectors.kis_market_provider)
            None이면 외국인/기관 TOP 랭킹 수집 스킵 (종목별 수급만 수집).

    Example:
        >>> from database import get_database
        >>> from modules.stock_screener.kis_api import KISApi
        >>> db = get_database()
        >>> kis = KISApi()
        >>> collector = SupplyCollector(db, kis)
        >>> result = collector.run_collection(date(2026, 5, 8))
        >>> print(result)
        {'success': 198, 'failure': 2, 'codes': 200, 'duration_sec': 25.3, ...}
    """

    def __init__(self, db, kis_api, market_provider=None) -> None:
        self.db = db
        self.kis = kis_api
        self.market_provider = market_provider
        self.max_retry = 3
        self.backoff_seconds = [1.0, 2.0, 4.0]  # 지수 백오프
        # 종목별 호출 간격은 KIS 인스턴스의 _rate_limit() 내부에서 처리됨 (api_delay=0.11s)
        # 추가 sleep 불필요 (이중 sleep 방지)

    def _build_universe(self, trade_date: date) -> list[str]:
        """수급 수집 대상 종목 풀 구성.

        universe = (오늘 선정된 테마의 상위 8개 종목)
                 ∪ (현재 보유 종목, portfolio status='holding')
                 ∪ (시총 TOP200, market_provider 있을 때만)

        Returns:
            6자리 종목코드 리스트 (중복 제거, 정렬됨)
        """
        codes: set[str] = set()

        # 1) 오늘 선정 테마의 종목
        try:
            top_themes = self.db.get_top_themes(
                trade_date, count=settings.SUPPLY_THEME_TOP_K
            )
            for theme in top_themes:
                # themes.stocks_json 또는 별도 stocks 테이블 — 환경에 맞게 조회
                # Phase 1-A에서는 stocks 테이블 + screening_log 활용
                stock_codes = self._get_theme_stocks(theme.get("theme_name"), trade_date)
                codes.update(stock_codes)
            logger.info(f"[SupplyCollector] 테마 종목: {len(codes)}개")
        except Exception as e:
            logger.warning(f"[SupplyCollector] 테마 종목 조회 실패 (계속): {e}")

        # 2) 현재 보유 종목
        try:
            holdings = self.db.get_portfolio(status="holding")
            holding_count = 0
            for pos in holdings:
                code = pos.get("stock_code")
                if code and self._is_valid_code(code):
                    codes.add(code)
                    holding_count += 1
            logger.info(f"[SupplyCollector] 보유 종목 추가: {holding_count}개 (누적 {len(codes)})")
        except Exception as e:
            logger.warning(f"[SupplyCollector] 보유 종목 조회 실패 (계속): {e}")

        # 3) 시총 TOP N (market_provider 있을 때만)
        if (
            self.market_provider is not None
            and settings.SUPPLY_UNIVERSE_TOP_MARKET_CAP > 0
        ):
            try:
                top_cap = self.market_provider.get_top_market_cap_data(
                    top_n=settings.SUPPLY_UNIVERSE_TOP_MARKET_CAP
                )
                before = len(codes)
                for code in top_cap.keys():
                    if self._is_valid_code(code):
                        codes.add(code)
                logger.info(
                    f"[SupplyCollector] 시총 TOP{settings.SUPPLY_UNIVERSE_TOP_MARKET_CAP} "
                    f"추가: +{len(codes) - before}개 (누적 {len(codes)})"
                )
            except Exception as e:
                logger.warning(f"[SupplyCollector] 시총 TOP 조회 실패 (계속): {e}")

        return sorted(codes)

    def _get_theme_stocks(self, theme_name: Optional[str], trade_date: date) -> list[str]:
        """테마 종목 조회. stocks 테이블 + screening_log 합집합.

        룩백 기간은 config.SUPPLY_STOCK_LOOKBACK_DAYS (기본 7일).
        """
        if not theme_name:
            return []
        codes: set[str] = set()
        td_iso = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
        lookback = f"-{settings.SUPPLY_STOCK_LOOKBACK_DAYS} days"
        with self.db.get_cursor() as cursor:
            # 최근 N일 내 같은 테마로 스크리닝/저장된 종목들 (점수 0인 박제도 포함하기 위해 stocks 우선)
            cursor.execute("""
                SELECT DISTINCT stock_code FROM stocks
                WHERE theme = ? AND date >= DATE(?, ?)
            """, (theme_name, td_iso, lookback))
            for row in cursor.fetchall():
                code = row[0]
                if self._is_valid_code(code):
                    codes.add(code)
            cursor.execute("""
                SELECT DISTINCT stock_code FROM screening_log
                WHERE theme = ? AND date >= DATE(?, ?)
            """, (theme_name, td_iso, lookback))
            for row in cursor.fetchall():
                code = row[0]
                if self._is_valid_code(code):
                    codes.add(code)
        return list(codes)

    @staticmethod
    def _is_valid_code(code: Optional[str]) -> bool:
        """6자리 숫자 종목코드 검증 (CLAUDE.md modules 규칙)."""
        if not code or not isinstance(code, str):
            return False
        return len(code) == 6 and code.isdigit()

    def _collect_one_stock(self, code: str, trade_date: date) -> bool:
        """단일 종목 수급 수집 + DB 저장. 성공 시 True, 실패 시 False (NULL 행 저장).

        retry 정책: 3회 (지수 백오프 1·2·4초).
        kis_api가 None이면 즉시 NULL 행 저장 후 False (CLI dry-run 오용 방어).
        """
        if self.kis is None:
            logger.warning(f"[SupplyCollector] {code} kis_api=None → NULL 저장 후 skip")
            try:
                self.db.save_supply_snapshot_null(trade_date, code)
            except Exception as e:
                logger.error(f"[SupplyCollector] {code} NULL 저장 실패: {e}")
            return False

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self.kis.get_investor_trading(code, days=5)
                if data:
                    self.db.save_supply_snapshot(trade_date, code, data)
                    return True
                # data=None은 KIS rt_cd != "0" (응답 자체 실패). retry
                last_err = RuntimeError("KIS returned None (rt_cd != 0)")
            except Exception as e:
                last_err = e

            if attempt < self.max_retry - 1:
                time.sleep(self.backoff_seconds[attempt])

        logger.warning(
            f"[SupplyCollector] {code} failed after {self.max_retry} retries: {last_err}"
        )
        try:
            self.db.save_supply_snapshot_null(trade_date, code)
        except Exception as e:
            logger.error(f"[SupplyCollector] {code} NULL 저장 실패: {e}")
        return False

    def _collect_top_ranking(self, trade_date: date, kind: str, etc_cls: str) -> int:
        """외국인/기관 순매수 상위 N 종목 수집 + DB 저장.

        Args:
            kind: 'foreign_buy' / 'institution_buy'
            etc_cls: '1'=외국인 / '2'=기관

        Returns: 저장된 종목 수
        """
        if self.market_provider is None:
            logger.debug(f"[SupplyCollector] market_provider 없음 → {kind} 수집 스킵")
            return 0
        try:
            top_n = settings.SUPPLY_RANKING_TOP_N
            # market_provider는 외인용 메서드(etc_cls=1 고정)만 제공.
            # 기관 호출은 _call_ranking 직접 사용 또는 params 변경 호출 필요.
            # Phase 1-A에서는 외인만 우선 수집. 기관은 후속(get_top_foreign_buy_codes 확장 시).
            if etc_cls == "1":
                codes = self.market_provider.get_top_foreign_buy_codes(top_n=top_n)
            else:
                # 기관 TOP은 market_provider에 별도 메서드 없으므로 Phase 1-A에서 스킵
                logger.info(f"[SupplyCollector] 기관 TOP({kind}) — Phase 1-A에서 스킵 (후속 추가 예정)")
                return 0

            items = [{"stock_code": code, "source": "FHPTJ04400000"} for code in codes
                     if self._is_valid_code(code)]
            self.db.save_foreign_top_ranking(trade_date, kind, items)
            logger.info(f"[SupplyCollector] {kind} TOP{top_n}: {len(items)}건 저장")
            return len(items)
        except Exception as e:
            logger.warning(f"[SupplyCollector] {kind} 수집 실패 (계속): {e}")
            return 0

    def run_collection(self, trade_date: date) -> dict:
        """전체 수급 수집 실행.

        Args:
            trade_date: 수집 기준일 (보통 오늘 17:10 시점이면 오늘 KRX 종가 마감 데이터)

        Returns:
            {'success': int, 'failure': int, 'codes': int, 'duration_sec': float,
             'foreign_ranking_count': int, 'institution_ranking_count': int}
        """
        start = time.time()
        logger.info("=" * 60)
        logger.info(f"📊 SupplyCollector 시작 (trade_date={trade_date})")
        logger.info("=" * 60)

        # 1) Universe 구성
        codes = self._build_universe(trade_date)
        if not codes:
            logger.warning("[SupplyCollector] universe가 비어있음 → 수집 스킵")
            return {
                "success": 0, "failure": 0, "codes": 0,
                "duration_sec": 0.0,
                "foreign_ranking_count": 0, "institution_ranking_count": 0,
                "trade_date": trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date),
            }
        logger.info(f"[SupplyCollector] universe={len(codes)}종목")

        # 2) 종목별 수급 수집
        success, failure = 0, 0
        for i, code in enumerate(codes, start=1):
            ok = self._collect_one_stock(code, trade_date)
            if ok:
                success += 1
            else:
                failure += 1
            if i % _LOG_EVERY_N == 0:
                elapsed = time.time() - start
                logger.info(
                    f"[SupplyCollector] 진행 {i}/{len(codes)} "
                    f"(success={success}, fail={failure}, elapsed={elapsed:.1f}s)"
                )

        # 3) 외국인 순매수 상위
        foreign_count = self._collect_top_ranking(trade_date, "foreign_buy", "1")
        time.sleep(settings.SUPPLY_RANKING_CALL_SLEEP_SEC)

        # 4) 기관 순매수 상위 (Phase 1-A에서는 스킵, 후속 추가)
        institution_count = self._collect_top_ranking(trade_date, "institution_buy", "2")

        duration = time.time() - start
        result = {
            "success": success,
            "failure": failure,
            "codes": len(codes),
            "duration_sec": round(duration, 1),
            "foreign_ranking_count": foreign_count,
            "institution_ranking_count": institution_count,
            "trade_date": trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date),
        }
        logger.info("=" * 60)
        logger.info(f"✅ SupplyCollector 완료: {result}")
        logger.info("=" * 60)
        return result


# ===== CLI 진입점 (dry-run / 수동 실행) =====

def _main():
    """python -m modules.supply_collector.collector --date YYYY-MM-DD [--dry-run]"""
    import argparse
    import sys
    from datetime import date as _date

    parser = argparse.ArgumentParser(description="SupplyCollector CLI")
    parser.add_argument("--date", type=str, default=None,
                        help="수집 대상 거래일 (YYYY-MM-DD). 미지정 시 오늘 KST.")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 KIS 호출 없이 universe 종목 수만 출력")
    args = parser.parse_args()

    if args.date:
        try:
            trade_date = _date.fromisoformat(args.date)
        except ValueError:
            print(f"❌ 잘못된 날짜 형식: {args.date} (YYYY-MM-DD 필요)", file=sys.stderr)
            sys.exit(1)
    else:
        trade_date = now_kst().date()

    from database import get_database
    db = get_database()

    if args.dry_run:
        # KIS 클라이언트 없이 universe만 계산
        collector = SupplyCollector(db, kis_api=None, market_provider=None)
        codes = collector._build_universe(trade_date)
        print(f"📋 [dry-run] universe={len(codes)}종목 (trade_date={trade_date})")
        print(f"   sample: {codes[:10]}")
        sys.exit(0)

    # 실제 KIS 호출
    from modules.stock_screener.kis_api import KISApi
    try:
        from closing_bet_system.collectors.kis_market_provider import get_kis_market_provider
        market_provider = get_kis_market_provider()
    except Exception as e:
        logger.warning(f"market_provider 로드 실패 (외인 TOP 스킵): {e}")
        market_provider = None

    kis = KISApi()
    collector = SupplyCollector(db, kis, market_provider=market_provider)
    result = collector.run_collection(trade_date)
    print(f"✅ 수집 완료: {result}")


if __name__ == "__main__":
    _main()
