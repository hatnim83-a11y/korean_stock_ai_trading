"""
backtest_us_turtle.py - 미국 주식 터틀 트레이딩 백테스트

S&P 500 대상 순수 터틀 트레이딩 전략 백테스트.
3가지 전략 비교: 순수 터틀 / 터틀+모멘텀필터 / Buy&Hold SPY

사용법:
    python scripts/backtest_us_turtle.py
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yfinance as yf


# ===== S&P 500 종목 리스트 (상위 100 + 주요 섹터 대표) =====
# 전체 500개 다운로드는 시간이 오래 걸리므로, 유동성 높은 200개 선별
SP500_TICKERS = [
    # Technology (40)
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "ORCL", "CRM", "AMD", "ADBE",
    "CSCO", "ACN", "IBM", "INTC", "INTU", "NOW", "QCOM", "TXN", "AMAT", "MU",
    "LRCX", "ADI", "KLAC", "SNPS", "CDNS", "MRVL", "FTNT", "PANW", "CRWD", "MSI",
    "NXPI", "ON", "GEN", "KEYS", "ANSS", "MPWR", "SWKS", "AKAM", "FSLR", "ENPH",
    # Healthcare (25)
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "MDT", "SYK", "ISRG", "VRTX", "REGN", "ZTS", "BDX", "EW",
    "HCA", "IDXX", "DXCM", "GEHC", "BSX",
    # Financial (25)
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK",
    "AXP", "C", "SCHW", "CB", "MMC", "PGR", "ICE", "AON", "CME", "MCO",
    "MET", "AIG", "TRV", "ALL", "AFL",
    # Consumer Discretionary (20)
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG",
    "ABNB", "MAR", "ORLY", "AZO", "ROST", "DHI", "LEN", "GM", "F", "EBAY",
    # Consumer Staples (15)
    "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "GIS",
    "KHC", "STZ", "KMB", "SJM", "HSY",
    # Industrials (20)
    "CAT", "GE", "HON", "UNP", "RTX", "BA", "DE", "LMT", "UPS", "FDX",
    "GD", "NOC", "WM", "ETN", "ITW", "EMR", "ROK", "CARR", "FAST", "CTAS",
    # Energy (15)
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "PXD", "OXY",
    "DVN", "HAL", "FANG", "HES", "BKR",
    # Communication (10)
    "GOOG", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "EA", "TTWO",
    # Materials (10)
    "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "STLD", "VMC", "MLM",
    # Utilities (10)
    "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "WEC", "ES",
    # Real Estate (10)
    "PLD", "AMT", "CCI", "EQIX", "PSA", "SPG", "O", "WELL", "DLR", "AVB",
]


@dataclass
class Position:
    code: str
    entry_date: datetime
    entry_price: float
    shares: int
    stop_loss: float
    highest_price: float = 0.0

    def __post_init__(self):
        self.highest_price = self.entry_price


@dataclass
class Trade:
    code: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_days: int


class USTurtleBacktester:
    def __init__(self, initial_capital=10_000, start_date="2021-01-01",
                 end_date="2026-01-31", max_positions=20, risk_pct=0.01,
                 breakout_period=20, exit_period=10, atr_mult=2.0,
                 slippage=0.0005, use_momentum_filter=False):
        self.initial_capital = initial_capital
        self.start_date = start_date
        self.end_date = end_date
        self.max_positions = max_positions
        self.risk_pct = risk_pct
        self.breakout_period = breakout_period
        self.exit_period = exit_period
        self.atr_mult = atr_mult
        self.slippage = slippage
        self.use_momentum_filter = use_momentum_filter

        self.cash = initial_capital
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.equity_curve = []
        self.daily_returns = []
        self.price_data: Dict[str, pd.DataFrame] = {}

    def load_data(self, tickers=None):
        """주가 데이터 로드 (배치 다운로드)"""
        if tickers is None:
            tickers = SP500_TICKERS

        print(f"  데이터 로드 중... ({len(tickers)}개 종목)")

        # 배치 다운로드 (훨씬 빠름)
        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i+batch_size]
            try:
                data = yf.download(
                    batch, start=self.start_date, end=self.end_date,
                    progress=False, group_by='ticker', threads=True
                )

                for ticker in batch:
                    try:
                        if len(batch) == 1:
                            df = data.copy()
                        else:
                            df = data[ticker].copy()

                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

                        df = df.dropna(subset=['Close'])
                        if len(df) < 60:
                            continue

                        df = self._calc_indicators(df)
                        self.price_data[ticker] = df
                    except Exception:
                        continue
            except Exception as e:
                print(f"    배치 {i//batch_size+1} 실패: {e}")

        print(f"  {len(self.price_data)}개 종목 로드 완료")

    def _calc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 계산"""
        df['MA200'] = df['Close'].rolling(200).mean()
        df['ATR'] = self._calc_atr(df, 14)
        df['High_20'] = df['High'].rolling(self.breakout_period).max()
        df['Low_10'] = df['Low'].rolling(self.exit_period).min()
        df['Volume_MA20'] = df['Volume'].rolling(20).mean()
        return df

    def _calc_atr(self, df, period=14):
        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def _get_equity(self, date):
        equity = self.cash
        for pos in self.positions:
            if pos.code in self.price_data:
                df = self.price_data[pos.code]
                df_until = df[df.index <= pd.Timestamp(date)]
                if not df_until.empty:
                    equity += float(df_until.iloc[-1]['Close']) * pos.shares
        return equity

    def run(self):
        """백테스트 실행"""
        if not self.price_data:
            self.load_data()

        # 공통 거래일 (SPY 기준)
        if 'SPY' not in self.price_data:
            # SPY 가 없으면 아무 종목 기준
            sample = list(self.price_data.values())[0]
        else:
            sample = self.price_data.get('SPY', list(self.price_data.values())[0])
        trading_days = sample.index.tolist()

        self.cash = self.initial_capital
        self.positions = []
        self.trades = []
        self.equity_curve = []
        self.daily_returns = []

        for i, date in enumerate(trading_days):
            dt = date.to_pydatetime()

            # 1. 청산 체크
            self._check_exits(dt)

            # 2. 진입 체크
            if len(self.positions) < self.max_positions:
                self._check_entries(dt)

            # 3. 자산 기록
            equity = self._get_equity(dt)
            self.equity_curve.append({"date": dt, "equity": equity})
            if i > 0 and self.equity_curve[-2]["equity"] > 0:
                prev = self.equity_curve[-2]["equity"]
                self.daily_returns.append((equity - prev) / prev * 100)

        # 최종 청산
        self._close_all(trading_days[-1].to_pydatetime())

    def _check_entries(self, date):
        """20일 고가 돌파 진입"""
        candidates = []

        for ticker, df in self.price_data.items():
            if any(p.code == ticker for p in self.positions):
                continue

            df_until = df[df.index <= pd.Timestamp(date)]
            if len(df_until) < self.breakout_period + 2:
                continue

            latest = df_until.iloc[-1]
            prev = df_until.iloc[-2]

            close = float(latest['Close'])
            atr = float(latest['ATR']) if not pd.isna(latest['ATR']) else None
            high_20_prev = float(prev['High_20']) if not pd.isna(prev['High_20']) else None

            if atr is None or atr <= 0 or high_20_prev is None:
                continue

            # 20일 고가 돌파
            if close <= high_20_prev:
                continue

            # 모멘텀 필터 (옵션)
            if self.use_momentum_filter:
                ma200 = float(latest['MA200']) if not pd.isna(latest['MA200']) else None
                vol = float(latest['Volume']) if not pd.isna(latest['Volume']) else 0
                vol_ma = float(latest['Volume_MA20']) if not pd.isna(latest['Volume_MA20']) else 0

                # MA200 위 + 거래량 평균 이상
                if ma200 is None or close <= ma200:
                    continue
                if vol_ma > 0 and vol < vol_ma * 0.5:
                    continue

            # 돌파 강도 = (종가 - 20일고가) / ATR
            breakout_strength = (close - high_20_prev) / atr
            candidates.append((ticker, close, atr, breakout_strength))

        # 돌파 강도순 정렬
        candidates.sort(key=lambda x: x[3], reverse=True)

        for ticker, price, atr, _ in candidates:
            if len(self.positions) >= self.max_positions:
                break

            entry_price = price * (1 + self.slippage)

            # ATR 사이징: 자본 risk_pct / ATR, 최소 자본 3% 보장
            equity = self._get_equity(pd.Timestamp(date))
            unit_value = equity * self.risk_pct / atr
            min_invest = equity * 0.03
            slots = self.max_positions - len(self.positions)
            max_invest = self.cash * 0.95 / max(slots, 1)
            invest = min(max(unit_value, min_invest), max_invest)

            shares = int(invest / entry_price)
            if shares <= 0:
                continue

            cost = entry_price * shares
            if cost > self.cash:
                continue

            stop_loss = entry_price - self.atr_mult * atr

            self.positions.append(Position(
                code=ticker, entry_date=date, entry_price=entry_price,
                shares=shares, stop_loss=stop_loss,
            ))
            self.cash -= cost

    def _check_exits(self, date):
        """10일 저가 이탈 / ATR 손절"""
        to_close = []

        for pos in self.positions:
            if pos.code not in self.price_data:
                continue

            df = self.price_data[pos.code]
            df_until = df[df.index <= pd.Timestamp(date)]
            if len(df_until) < 2:
                continue

            latest = df_until.iloc[-1]
            prev = df_until.iloc[-2]

            close = float(latest['Close'])
            low = float(latest['Low'])

            # 고점 갱신
            if close > pos.highest_price:
                pos.highest_price = close

            exit_reason = None
            exit_price = close

            # ATR 손절
            if low <= pos.stop_loss:
                exit_reason = "ATR_stop"
                exit_price = pos.stop_loss

            # 10일 저가 이탈
            if not exit_reason:
                prev_low_10 = float(prev['Low_10']) if not pd.isna(prev['Low_10']) else None
                if prev_low_10 is not None and close <= prev_low_10:
                    exit_reason = "10d_exit"
                    exit_price = prev_low_10

            if exit_reason:
                to_close.append((pos, exit_price, exit_reason))

        for pos, exit_price, reason in to_close:
            exit_price *= (1 - self.slippage)
            pnl = (exit_price - pos.entry_price) * pos.shares
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
            holding = (date - pos.entry_date).days

            self.trades.append(Trade(
                code=pos.code, entry_date=pos.entry_date, exit_date=date,
                entry_price=pos.entry_price, exit_price=exit_price,
                shares=pos.shares, pnl=pnl, pnl_pct=pnl_pct,
                exit_reason=reason, holding_days=holding,
            ))
            self.cash += exit_price * pos.shares
            self.positions.remove(pos)

    def _close_all(self, date):
        for pos in list(self.positions):
            if pos.code in self.price_data:
                df = self.price_data[pos.code]
                price = float(df.iloc[-1]['Close'])
                pnl = (price - pos.entry_price) * pos.shares
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                self.trades.append(Trade(
                    code=pos.code, entry_date=pos.entry_date, exit_date=date,
                    entry_price=pos.entry_price, exit_price=price,
                    shares=pos.shares, pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason="backtest_end", holding_days=(date - pos.entry_date).days,
                ))
                self.cash += price * pos.shares
        self.positions = []

    def get_results(self) -> dict:
        """결과 계산"""
        if not self.equity_curve:
            return {}

        initial = self.initial_capital
        final = self.equity_curve[-1]["equity"]
        total_ret = (final - initial) / initial * 100

        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        years = (end - start).days / 365
        cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 and final > 0 else 0

        eq_s = pd.Series([e["equity"] for e in self.equity_curve])
        mdd = ((eq_s - eq_s.cummax()) / eq_s.cummax() * 100).min()

        if self.daily_returns:
            avg_r = np.mean(self.daily_returns)
            std_r = np.std(self.daily_returns)
            sharpe = (avg_r * 252) / (std_r * np.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        plr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        avg_hold = np.mean([t.holding_days for t in self.trades]) if self.trades else 0

        # 청산 사유별
        reasons = {}
        for t in self.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

        return {
            'total_return': total_ret, 'cagr': cagr, 'mdd': mdd, 'sharpe': sharpe,
            'win_rate': win_rate, 'plr': plr, 'avg_hold': avg_hold,
            'trades': len(self.trades), 'avg_win': avg_win, 'avg_loss': avg_loss,
            'final_equity': final, 'reasons': reasons,
        }


def run_spy_benchmark(start_date, end_date, initial_capital):
    """SPY Buy & Hold 벤치마크"""
    df = yf.download("SPY", start=start_date, end=end_date, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    first_price = float(df.iloc[0]['Close'])
    last_price = float(df.iloc[-1]['Close'])
    shares = int(initial_capital / first_price)
    remaining = initial_capital - shares * first_price

    final = shares * last_price + remaining
    total_ret = (final - initial_capital) / initial_capital * 100

    years = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days / 365
    cagr = ((final / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    eq = df['Close'] * shares + remaining
    mdd = ((eq - eq.cummax()) / eq.cummax() * 100).min()

    daily_ret = df['Close'].pct_change().dropna() * 100
    avg_r = daily_ret.mean()
    std_r = daily_ret.std()
    sharpe = (avg_r * 252) / (std_r * np.sqrt(252)) if std_r > 0 else 0

    return {
        'total_return': total_ret, 'cagr': cagr, 'mdd': float(mdd), 'sharpe': sharpe,
        'win_rate': 0, 'plr': 0, 'avg_hold': 0,
        'trades': 1, 'avg_win': total_ret, 'avg_loss': 0,
        'final_equity': final, 'reasons': {"buy_and_hold": 1},
    }


def main():
    print("=" * 90)
    print("  미국 주식 터틀 트레이딩 백테스트")
    print("  기간: 2021-01 ~ 2026-01 | 초기자본: $10,000 | 종목풀: S&P 500 (200선)")
    print("=" * 90)

    start_date = "2021-01-01"
    end_date = "2026-01-31"
    initial_capital = 10_000

    results = {}

    # ===== A. 순수 터틀 =====
    print(f"\n{'='*70}")
    print("  [1/3] A. 순수 터틀 (20일 돌파 + 10일 이탈)")
    print("=" * 70)

    bt_a = USTurtleBacktester(
        initial_capital=initial_capital, start_date=start_date, end_date=end_date,
        max_positions=20, risk_pct=0.01, use_momentum_filter=False,
    )
    bt_a.load_data()
    bt_a.run()
    results['A'] = bt_a.get_results()

    r = results['A']
    print(f"  수익률: {r['total_return']:+.2f}% | CAGR: {r['cagr']:.1f}% | MDD: {r['mdd']:.2f}%")
    print(f"  Sharpe: {r['sharpe']:.2f} | 승률: {r['win_rate']:.1f}% | 손익비: {r['plr']:.2f}")
    print(f"  거래: {r['trades']}회 | 평균보유: {r['avg_hold']:.1f}일")
    reasons_str = ", ".join(f"{k}:{v}" for k, v in sorted(r['reasons'].items(), key=lambda x: -x[1]))
    print(f"  청산사유: {reasons_str}")

    # ===== B. 터틀 + 모멘텀 필터 =====
    print(f"\n{'='*70}")
    print("  [2/3] B. 터틀 + 모멘텀 필터 (MA200 위 + 거래량)")
    print("=" * 70)

    bt_b = USTurtleBacktester(
        initial_capital=initial_capital, start_date=start_date, end_date=end_date,
        max_positions=20, risk_pct=0.01, use_momentum_filter=True,
    )
    bt_b.price_data = bt_a.price_data  # 데이터 공유
    bt_b.run()
    results['B'] = bt_b.get_results()

    r = results['B']
    print(f"  수익률: {r['total_return']:+.2f}% | CAGR: {r['cagr']:.1f}% | MDD: {r['mdd']:.2f}%")
    print(f"  Sharpe: {r['sharpe']:.2f} | 승률: {r['win_rate']:.1f}% | 손익비: {r['plr']:.2f}")
    print(f"  거래: {r['trades']}회 | 평균보유: {r['avg_hold']:.1f}일")
    reasons_str = ", ".join(f"{k}:{v}" for k, v in sorted(r['reasons'].items(), key=lambda x: -x[1]))
    print(f"  청산사유: {reasons_str}")

    # ===== C. SPY Buy & Hold =====
    print(f"\n{'='*70}")
    print("  [3/3] C. Buy & Hold SPY (벤치마크)")
    print("=" * 70)

    results['C'] = run_spy_benchmark(start_date, end_date, initial_capital)
    r = results['C']
    print(f"  수익률: {r['total_return']:+.2f}% | CAGR: {r['cagr']:.1f}% | MDD: {r['mdd']:.2f}%")
    print(f"  Sharpe: {r['sharpe']:.2f}")

    # ===== 최종 비교 테이블 =====
    print("\n" + "=" * 90)
    print("  최종 비교")
    print("=" * 90)

    labels = {
        'A': '순수 터틀',
        'B': '터틀+모멘텀',
        'C': 'SPY B&H',
    }

    header = f"{'지표':<12}"
    for key in ['A', 'B', 'C']:
        header += f" {labels[key]:>20}"
    print(header)
    print("-" * 90)

    rows = [
        ("수익률", "total_return", "%", ".2f"),
        ("최종자산", "final_equity", "$", ",.0f"),
        ("CAGR", "cagr", "%", ".1f"),
        ("MDD", "mdd", "%", ".2f"),
        ("Sharpe", "sharpe", "", ".2f"),
        ("승률", "win_rate", "%", ".1f"),
        ("손익비", "plr", "", ".2f"),
        ("평균보유일", "avg_hold", "일", ".1f"),
        ("거래수", "trades", "회", "d"),
    ]

    for label, key, unit, fmt in rows:
        line = f"{label:<12}"
        for s in ['A', 'B', 'C']:
            val = results[s].get(key, 0)
            if key == "final_equity":
                line += f" ${val:>18,.0f}"
            elif fmt == "d":
                line += f" {int(val):>18}{unit:>2}"
            else:
                line += f" {val:>18{fmt}}{unit:>2}"
        print(line)

    print("=" * 90)

    # 최고 성과
    best = max(['A', 'B'], key=lambda x: results[x]['total_return'])
    print(f"\n  최고 수익률: {labels[best]} ({results[best]['total_return']:+.2f}%)")
    spy_ret = results['C']['total_return']
    for key in ['A', 'B']:
        alpha = results[key]['total_return'] - spy_ret
        print(f"  {labels[key]} vs SPY: {alpha:+.2f}% {'(초과)' if alpha > 0 else '(미달)'}")


if __name__ == "__main__":
    main()
