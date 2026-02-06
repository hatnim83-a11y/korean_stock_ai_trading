"""
backtest_52week_high.py - 52주 신고가 근접 종목 기반 백테스트

전략 컨셉:
- 테마 대신 52주 신고가에 근접한 종목으로 시작
- 이후 스크리닝 프로세스는 기존 시스템과 동일
- 이익 추종 전략 (트레일링 스탑) 적용

52주 신고가 근접 기준:
- 현재가가 52주 최고가 대비 X% 이내
- 기본값: 5% (강한 모멘텀 신호)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum

from logger import setup_logger, logger

setup_logger(log_level="INFO")


class SellReason(Enum):
    """매도 사유"""
    STOP_LOSS = "손절"
    TRAILING_L1 = "트레일링L1"
    TRAILING_L2 = "트레일링L2"
    TRAILING_L3 = "트레일링L3"
    HOLDING_EXPIRED = "보유기간만료"
    BACKTEST_END = "백테스트종료"


@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    stock_name: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    highest_price: float = 0.0
    trailing_stop: float = 0.0
    trailing_level: int = 0
    high_52week: float = 0.0  # 진입 시점 52주 신고가

    def __post_init__(self):
        self.highest_price = self.entry_price


@dataclass
class Trade:
    """거래 기록"""
    stock_code: str
    stock_name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    holding_days: int
    sell_reason: SellReason
    high_52week: float = 0.0


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    # 기간
    start_date: str = "2023-01-01"
    end_date: str = "2026-01-31"
    initial_capital: int = 100_000_000

    # 52주 신고가 필터
    high_52week_pct: float = 0.05  # 52주 고가 대비 5% 이내
    min_volume_ratio: float = 1.0  # 평균 거래량 대비 최소 비율

    # 포트폴리오
    max_positions: int = 10
    rebalance_days: int = 7  # 리밸런싱 주기

    # 손절/트레일링
    stop_loss_pct: float = -0.07
    max_holding_days: int = 14

    # 이익 추종 전략
    enable_profit_trailing: bool = True
    trail_activation_pct: float = 0.08
    trail_level1_pct: float = 0.05
    trail_level2_threshold: float = 0.15
    trail_level2_pct: float = 0.03
    trail_level3_threshold: float = 0.25
    trail_level3_pct: float = 0.02


class High52WeekBacktester:
    """52주 신고가 근접 종목 백테스터"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.price_data: dict[str, pd.DataFrame] = {}
        self.positions: list[Position] = []
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self.daily_returns: list[float] = []
        self.cash = config.initial_capital

        # 종목 정보 (기존 테마 종목들 활용)
        self.stock_universe = self._get_stock_universe()

    def _get_stock_universe(self) -> dict[str, str]:
        """전체 종목 유니버스 (기존 테마 종목 + 추가 종목)"""
        return {
            # 반도체
            "005930": "삼성전자", "000660": "SK하이닉스", "042700": "한미반도체",
            # 2차전지
            "373220": "LG에너지솔루션", "006400": "삼성SDI", "051910": "LG화학",
            "247540": "에코프로비엠", "086520": "에코프로",
            # 방산
            "012450": "한화에어로스페이스", "047810": "한국항공우주", "014820": "동원시스템즈",
            "067390": "아스트로젠",
            # 조선
            "009540": "HD한국조선해양", "010140": "삼성중공업", "042660": "대우조선해양",
            # 자동차
            "005380": "현대차", "000270": "기아", "012330": "현대모비스",
            # 금융
            "055550": "신한지주", "086790": "하나금융지주", "105560": "KB금융",
            # 바이오
            "207940": "삼성바이오로직스", "068270": "셀트리온", "326030": "SK바이오팜",
            # 엔터
            "352820": "하이브", "041510": "에스엠", "122870": "와이지엔터테인먼트",
            # 화학
            "011170": "롯데케미칼", "010950": "S-Oil", "096770": "SK이노베이션",
            # 철강
            "005490": "POSCO홀딩스", "004020": "현대제철",
            # IT/플랫폼
            "035720": "카카오", "035420": "NAVER", "263750": "펄어비스",
            # 건설
            "000720": "현대건설", "006360": "GS건설",
            # 유통
            "139480": "이마트", "004170": "신세계",
        }

    def load_data(self) -> None:
        """주가 데이터 로드"""
        import FinanceDataReader as fdr

        logger.info("📥 주가 데이터 로드 중...")

        start = (datetime.strptime(self.config.start_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        end = self.config.end_date

        for code, name in self.stock_universe.items():
            try:
                df = fdr.DataReader(code, start, end)
                if df is not None and len(df) > 252:  # 최소 1년 데이터
                    df = df.reset_index()
                    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'change']
                    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

                    # 52주(252일) 최고가 계산
                    df['high_52week'] = df['high'].rolling(window=252, min_periods=252).max()
                    df['high_52week_ratio'] = (df['high_52week'] - df['close']) / df['high_52week']

                    # 평균 거래량 (20일)
                    df['avg_volume_20'] = df['volume'].rolling(window=20).mean()
                    df['volume_ratio'] = df['volume'] / df['avg_volume_20']

                    self.price_data[code] = df
            except Exception as e:
                logger.debug(f"{name} 데이터 로드 실패: {e}")

        logger.info(f"✅ {len(self.price_data)}개 종목 데이터 로드 완료")

    def get_52week_high_stocks(self, date: str) -> list[dict]:
        """52주 신고가 근접 종목 스크리닝 + 수급/기술적 필터"""
        candidates = []

        for code, df in self.price_data.items():
            date_data = df[df['date'] == date]
            if date_data.empty:
                continue

            row = date_data.iloc[0]

            # 52주 고가 데이터 확인
            if pd.isna(row['high_52week']) or pd.isna(row['high_52week_ratio']):
                continue

            high_52week_ratio = row['high_52week_ratio']

            # ===== 1단계: 52주 신고가 근접 필터 =====
            if high_52week_ratio > self.config.high_52week_pct:
                continue

            # ===== 2단계: 거래량 필터 =====
            if row['volume_ratio'] < self.config.min_volume_ratio:
                continue

            # ===== 3단계: 이동평균 정배열 필터 =====
            if not self._check_ma_alignment(df, date):
                continue

            # ===== 4단계: RSI 과매수 제외 =====
            rsi = self._calculate_rsi(df, date)
            if rsi is None or rsi > 80:  # RSI 80 이상 과매수 제외
                continue

            # ===== 5단계: 수급 필터 (5일 기준) =====
            supply_score = self._calculate_supply_score(df, date)

            # 점수 계산 (52주 신고가 근접도 + 수급 + RSI)
            momentum_score = (1 - high_52week_ratio) * 50  # 신고가에 가까울수록 높음
            rsi_score = (rsi / 100) * 20 if rsi else 0  # 적당한 RSI
            total_score = momentum_score + supply_score + rsi_score

            candidates.append({
                'code': code,
                'name': self.stock_universe[code],
                'price': row['close'],
                'high_52week': row['high_52week'],
                'high_52week_ratio': high_52week_ratio,
                'volume_ratio': row['volume_ratio'],
                'rsi': rsi,
                'supply_score': supply_score,
                'score': total_score
            })

        # 점수 기준 정렬
        candidates.sort(key=lambda x: x['score'], reverse=True)

        return candidates

    def _check_ma_alignment(self, df: pd.DataFrame, date: str) -> bool:
        """이동평균 정배열 확인 (5일 > 20일 > 60일)"""
        try:
            idx = df[df['date'] == date].index[0]
            if idx < 60:
                return False

            # 이동평균 계산
            ma5 = df['close'].iloc[idx-4:idx+1].mean()
            ma20 = df['close'].iloc[idx-19:idx+1].mean()
            ma60 = df['close'].iloc[idx-59:idx+1].mean()

            # 정배열: 5일 > 20일 > 60일
            return ma5 > ma20 > ma60
        except Exception:
            return False

    def _calculate_rsi(self, df: pd.DataFrame, date: str, period: int = 14) -> Optional[float]:
        """RSI 계산"""
        try:
            idx = df[df['date'] == date].index[0]
            if idx < period:
                return None

            close_prices = df['close'].iloc[idx-period:idx+1]
            deltas = close_prices.diff()

            gains = deltas.where(deltas > 0, 0)
            losses = -deltas.where(deltas < 0, 0)

            avg_gain = gains.rolling(window=period).mean().iloc[-1]
            avg_loss = losses.rolling(window=period).mean().iloc[-1]

            if avg_loss == 0:
                return 100

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            return rsi
        except Exception:
            return None

    def _calculate_supply_score(self, df: pd.DataFrame, date: str) -> float:
        """
        수급 점수 계산 (거래량 증가 기반)
        실제 외국인/기관 데이터는 백테스트에서 구하기 어려우므로
        거래량 증가율로 대체
        """
        try:
            idx = df[df['date'] == date].index[0]
            if idx < 20:
                return 0

            # 최근 5일 평균 거래량
            recent_5d_vol = df['volume'].iloc[idx-4:idx+1].mean()
            # 이전 20일 평균 거래량
            prev_20d_vol = df['volume'].iloc[idx-24:idx-4].mean()

            if prev_20d_vol == 0:
                return 0

            # 거래량 증가율
            vol_increase = (recent_5d_vol - prev_20d_vol) / prev_20d_vol

            # 점수 변환 (0~30점)
            score = min(max(vol_increase * 100, 0), 30)

            return score
        except Exception:
            return 0

    def calculate_weights(self, candidates: list[dict]) -> list[dict]:
        """동일 가중치 계산"""
        if not candidates:
            return []

        weight = 1.0 / len(candidates)
        for c in candidates:
            c['weight'] = weight

        return candidates

    def open_position(self, stock: dict, date: str, available_cash: float) -> Optional[Position]:
        """포지션 진입"""
        code = stock['code']
        price = stock['price']

        if price <= 0:
            return None

        invest_amount = available_cash * stock['weight']
        quantity = int(invest_amount / price)

        if quantity <= 0:
            return None

        stop_loss = price * (1 + self.config.stop_loss_pct)

        position = Position(
            stock_code=code,
            stock_name=stock['name'],
            entry_date=date,
            entry_price=price,
            quantity=quantity,
            stop_loss=stop_loss,
            high_52week=stock['high_52week']
        )

        return position

    def update_trailing_stop(self, pos: Position, current_price: float) -> None:
        """트레일링 스탑 업데이트"""
        if not self.config.enable_profit_trailing:
            return

        # 최고가 업데이트
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        current_profit_pct = (current_price - pos.entry_price) / pos.entry_price

        # 트레일링 레벨 결정
        if current_profit_pct >= self.config.trail_level3_threshold:
            if pos.trailing_level < 3:
                pos.trailing_level = 3
        elif current_profit_pct >= self.config.trail_level2_threshold:
            if pos.trailing_level < 2:
                pos.trailing_level = 2
        elif current_profit_pct >= self.config.trail_activation_pct:
            if pos.trailing_level < 1:
                pos.trailing_level = 1
                pos.stop_loss = pos.entry_price  # 본전 손절

        # 트레일링 스탑 계산
        if pos.trailing_level > 0:
            if pos.trailing_level == 3:
                trail_pct = self.config.trail_level3_pct
            elif pos.trailing_level == 2:
                trail_pct = self.config.trail_level2_pct
            else:
                trail_pct = self.config.trail_level1_pct

            new_trailing_stop = pos.highest_price * (1 - trail_pct)

            if new_trailing_stop > pos.trailing_stop:
                pos.trailing_stop = new_trailing_stop

            if pos.trailing_stop > pos.stop_loss:
                pos.stop_loss = pos.trailing_stop

    def check_exit(self, pos: Position, current_price: float, holding_days: int) -> Optional[tuple[SellReason, float]]:
        """청산 조건 확인"""
        # 트레일링 스탑 업데이트
        self.update_trailing_stop(pos, current_price)

        # 손절/트레일링 스탑
        if current_price <= pos.stop_loss:
            if pos.trailing_level >= 3:
                return (SellReason.TRAILING_L3, current_price)
            elif pos.trailing_level >= 2:
                return (SellReason.TRAILING_L2, current_price)
            elif pos.trailing_level >= 1:
                return (SellReason.TRAILING_L1, current_price)
            else:
                return (SellReason.STOP_LOSS, pos.stop_loss)

        # 보유기간 만료
        if holding_days >= self.config.max_holding_days:
            return (SellReason.HOLDING_EXPIRED, current_price)

        return None

    def close_position(self, pos: Position, exit_date: str, exit_price: float, reason: SellReason) -> Trade:
        """포지션 청산"""
        pnl = (exit_price - pos.entry_price) * pos.quantity
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price

        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
        holding_days = (exit_dt - entry_dt).days

        trade = Trade(
            stock_code=pos.stock_code,
            stock_name=pos.stock_name,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_days=holding_days,
            sell_reason=reason,
            high_52week=pos.high_52week
        )

        return trade

    def run(self) -> None:
        """백테스트 실행"""
        self.load_data()

        logger.info("=" * 60)
        logger.info("🚀 52주 신고가 근접 종목 백테스트 시작")
        logger.info("=" * 60)
        logger.info(f"기간: {self.config.start_date} ~ {self.config.end_date}")
        logger.info(f"초기 자본: {self.config.initial_capital:,}원")
        logger.info(f"52주 고가 대비: {self.config.high_52week_pct:.1%} 이내")
        logger.info("-" * 60)

        # 거래일 목록
        sample_df = list(self.price_data.values())[0]
        trading_days = sample_df[
            (sample_df['date'] >= self.config.start_date) &
            (sample_df['date'] <= self.config.end_date)
        ]['date'].tolist()

        logger.info(f"거래일: {len(trading_days)}일")

        last_rebalance = None
        prev_equity = self.config.initial_capital

        for date in trading_days:
            date_dt = datetime.strptime(date, "%Y-%m-%d")

            # 주말 제외
            if date_dt.weekday() >= 5:
                continue

            # 포지션 업데이트 및 청산 확인
            positions_to_close = []
            for pos in self.positions:
                df = self.price_data.get(pos.stock_code)
                if df is None:
                    continue

                date_data = df[df['date'] == date]
                if date_data.empty:
                    continue

                current_price = date_data.iloc[0]['close']
                entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
                holding_days = (date_dt - entry_dt).days

                exit_result = self.check_exit(pos, current_price, holding_days)
                if exit_result:
                    reason, exit_price = exit_result
                    positions_to_close.append((pos, exit_price, reason))

            # 청산 실행
            for pos, exit_price, reason in positions_to_close:
                trade = self.close_position(pos, date, exit_price, reason)
                self.trades.append(trade)
                self.cash += exit_price * pos.quantity
                self.positions.remove(pos)

            # 리밸런싱 (주기적)
            if last_rebalance is None or (date_dt - last_rebalance).days >= self.config.rebalance_days:
                if len(self.positions) < self.config.max_positions:
                    # 52주 신고가 근접 종목 스크리닝
                    candidates = self.get_52week_high_stocks(date)

                    # 이미 보유 중인 종목 제외
                    held_codes = {p.stock_code for p in self.positions}
                    candidates = [c for c in candidates if c['code'] not in held_codes]

                    # 필요한 포지션 수만큼 선택
                    slots_available = self.config.max_positions - len(self.positions)
                    candidates = candidates[:slots_available]

                    if candidates:
                        candidates = self.calculate_weights(candidates)

                        for stock in candidates:
                            pos = self.open_position(stock, date, self.cash)
                            if pos:
                                self.positions.append(pos)
                                self.cash -= pos.entry_price * pos.quantity

                        if len(candidates) > 0:
                            logger.info(f"\n📊 [{date}] 리밸런싱: {len(candidates)}개 종목 매수")
                            for c in candidates[:3]:
                                logger.info(f"   {c['name']}: 52주고가 대비 {c['high_52week_ratio']:.1%}")

                last_rebalance = date_dt

            # 일일 평가
            portfolio_value = self.cash
            for pos in self.positions:
                df = self.price_data.get(pos.stock_code)
                if df is not None:
                    date_data = df[df['date'] == date]
                    if not date_data.empty:
                        portfolio_value += date_data.iloc[0]['close'] * pos.quantity

            self.equity_curve.append({
                'date': date,
                'equity': portfolio_value,
                'positions': len(self.positions),
                'cash': self.cash
            })

            if prev_equity > 0:
                daily_return = (portfolio_value - prev_equity) / prev_equity
                self.daily_returns.append(daily_return)
            prev_equity = portfolio_value

        # 잔여 포지션 청산
        if self.positions:
            logger.info(f"\n📊 백테스트 종료 - 잔여 포지션 청산")
            final_date = trading_days[-1]
            for pos in self.positions[:]:
                df = self.price_data.get(pos.stock_code)
                if df is not None:
                    date_data = df[df['date'] == final_date]
                    if not date_data.empty:
                        exit_price = date_data.iloc[0]['close']
                        trade = self.close_position(pos, final_date, exit_price, SellReason.BACKTEST_END)
                        self.trades.append(trade)
                        self.cash += exit_price * pos.quantity
            self.positions.clear()

        self.print_results()

    def print_results(self) -> None:
        """결과 출력"""
        logger.info("\n" + "=" * 60)
        logger.info("📊 백테스트 결과")
        logger.info("=" * 60)

        if not self.equity_curve:
            logger.warning("거래 데이터 없음")
            return

        initial = self.config.initial_capital
        final = self.equity_curve[-1]['equity']
        total_return = (final - initial) / initial

        # CAGR
        days = len(self.equity_curve)
        years = days / 252
        cagr = (final / initial) ** (1 / years) - 1 if years > 0 else 0

        # MDD
        equity = pd.Series([e['equity'] for e in self.equity_curve])
        mdd = ((equity - equity.cummax()) / equity.cummax()).min()

        # 샤프
        if self.daily_returns:
            avg_return = np.mean(self.daily_returns)
            std_return = np.std(self.daily_returns)
            sharpe = (avg_return * 252) / (std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0

        print("\n" + "=" * 60)
        print("📈 수익률 지표")
        print("-" * 60)
        print(f"초기 자본:         {initial:>15,}원")
        print(f"최종 자본:         {final:>15,.0f}원")
        print(f"총 수익률:         {total_return:>15.2%}")
        print(f"연평균 수익률:     {cagr:>15.2%}")
        print(f"MDD:               {mdd:>15.2%}")
        print(f"샤프 비율:         {sharpe:>15.2f}")

        # 거래 통계
        if self.trades:
            wins = [t for t in self.trades if t.pnl > 0]
            losses = [t for t in self.trades if t.pnl <= 0]
            win_rate = len(wins) / len(self.trades) if self.trades else 0

            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
            avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
            avg_holding = np.mean([t.holding_days for t in self.trades])

            print("\n" + "-" * 60)
            print("📊 거래 통계")
            print("-" * 60)
            print(f"총 거래 수:        {len(self.trades):>15}회")
            print(f"승률:              {win_rate:>15.1%}")
            print(f"평균 수익:         {avg_win:>15.2%}")
            print(f"평균 손실:         {avg_loss:>15.2%}")
            print(f"평균 보유일:       {avg_holding:>15.1f}일")

            # 청산 사유별 통계
            print("\n" + "-" * 60)
            print("📋 청산 사유별 통계")
            print("-" * 60)

            reason_stats = {}
            for t in self.trades:
                reason = t.sell_reason.value
                if reason not in reason_stats:
                    reason_stats[reason] = {'count': 0, 'pnl_sum': 0}
                reason_stats[reason]['count'] += 1
                reason_stats[reason]['pnl_sum'] += t.pnl_pct

            for reason, stats in sorted(reason_stats.items(), key=lambda x: x[1]['count'], reverse=True):
                avg_pnl = stats['pnl_sum'] / stats['count'] if stats['count'] > 0 else 0
                print(f"{reason:<15} : {stats['count']:>5}회, 평균 {avg_pnl:+.2%}")

        print("\n" + "=" * 60)

        return {
            'total_return': total_return,
            'cagr': cagr,
            'mdd': mdd,
            'sharpe': sharpe,
            'trades': len(self.trades),
            'win_rate': win_rate if self.trades else 0
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="52주 신고가 근접 종목 백테스트")
    parser.add_argument("--high-pct", type=float, default=0.05, help="52주 고가 대비 비율 (기본: 0.05 = 5%%)")
    parser.add_argument("--test-grid", action="store_true", help="그리드 테스트 (3%/5%/7%/10%)")
    args = parser.parse_args()

    if args.test_grid:
        # 그리드 테스트
        print("\n" + "=" * 80)
        print("🔬 52주 신고가 근접 비율 그리드 테스트")
        print("=" * 80)

        pct_list = [0.03, 0.05, 0.07, 0.10]
        results = {}

        for pct in pct_list:
            print(f"\n{'=' * 60}")
            print(f"📊 52주 고가 대비 {pct:.0%} 이내")
            print("=" * 60)

            config = BacktestConfig(high_52week_pct=pct)
            bt = High52WeekBacktester(config)
            bt.run()

            if bt.equity_curve:
                initial = config.initial_capital
                final = bt.equity_curve[-1]['equity']
                equity = pd.Series([e['equity'] for e in bt.equity_curve])
                mdd = ((equity - equity.cummax()) / equity.cummax()).min()

                results[pct] = {
                    'return': (final - initial) / initial * 100,
                    'final': final,
                    'mdd': mdd * 100,
                    'trades': len(bt.trades),
                    'win_rate': len([t for t in bt.trades if t.pnl > 0]) / len(bt.trades) * 100 if bt.trades else 0
                }

        # 비교 결과
        print("\n" + "=" * 80)
        print("📈 그리드 테스트 결과 비교")
        print("=" * 80)
        print(f"{'구분':<15} {'3%':>15} {'5%':>15} {'7%':>15} {'10%':>15}")
        print("-" * 80)

        print(f"{'총 수익률':.<15}", end="")
        for pct in pct_list:
            print(f" {results[pct]['return']:>14.2f}%", end="")
        print()

        print(f"{'MDD':.<15}", end="")
        for pct in pct_list:
            print(f" {results[pct]['mdd']:>14.2f}%", end="")
        print()

        print(f"{'총 거래':.<15}", end="")
        for pct in pct_list:
            print(f" {results[pct]['trades']:>15}", end="")
        print()

        print(f"{'승률':.<15}", end="")
        for pct in pct_list:
            print(f" {results[pct]['win_rate']:>14.1f}%", end="")
        print()

        print("=" * 80)

        # 최고 성과
        best_pct = max(pct_list, key=lambda p: results[p]['return'])
        print(f"\n✅ 결론: {best_pct:.0%} 이내가 가장 좋은 성과! (수익률 {results[best_pct]['return']:.2f}%)")

    else:
        # 단일 테스트
        config = BacktestConfig(high_52week_pct=args.high_pct)
        bt = High52WeekBacktester(config)
        bt.run()


if __name__ == "__main__":
    main()
