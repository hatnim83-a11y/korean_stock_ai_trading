"""
backtest_turtle.py - 터틀 트레이딩 백테스트

시나리오 3개:
1. 순수 터틀: 20일 돌파 진입, 2ATR 손절, 10일 저가 청산, ATR 포지션 사이징
2. 하이브리드: 테마 모멘텀 + 20일 돌파 필터 + ATR 포지션 사이징 + 트레일링
3. 기존 전략(비교용): 테마 모멘텀 + 균등 배분 + 트레일링

사용법:
    python scripts/backtest_turtle.py
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from math import sqrt
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yfinance as yf


# ===== 테마 및 종목 =====
THEME_STOCKS = {
    "2차전지": {
        "stocks": ["373220.KS", "006400.KS", "051910.KS", "086520.KS", "247540.KS"],
        "names": ["LG에너지솔루션", "삼성SDI", "LG화학", "에코프로비엠", "에코프로"],
    },
    "반도체": {
        "stocks": ["005930.KS", "000660.KS", "042700.KS", "058470.KS"],
        "names": ["삼성전자", "SK하이닉스", "한미반도체", "리노공업"],
    },
    "바이오": {
        "stocks": ["207940.KS", "068270.KS", "128940.KS", "145020.KS"],
        "names": ["삼성바이오로직스", "셀트리온", "한미약품", "휴젤"],
    },
    "자동차": {
        "stocks": ["005380.KS", "000270.KS", "012330.KS", "011210.KS"],
        "names": ["현대차", "기아", "현대모비스", "현대위아"],
    },
    "조선": {
        "stocks": ["009540.KS", "010140.KS", "042660.KS"],
        "names": ["HD한국조선해양", "삼성중공업", "대우조선해양"],
    },
    "방산": {
        "stocks": ["012450.KS", "047810.KS", "082740.KS", "003570.KS"],
        "names": ["한화에어로스페이스", "한국항공우주", "한화시스템", "SNT다이나믹스"],
    },
    "엔터": {
        "stocks": ["352820.KS", "122870.KS", "041510.KS", "035900.KS"],
        "names": ["하이브", "와이지엔터테인먼트", "에스엠", "JYP엔터테인먼트"],
    },
    "금융": {
        "stocks": ["105560.KS", "055550.KS", "086790.KS", "024110.KS"],
        "names": ["KB금융", "신한지주", "하나금융지주", "기업은행"],
    },
    "철강": {
        "stocks": ["005490.KS", "004020.KS", "001230.KS"],
        "names": ["POSCO홀딩스", "현대제철", "동국제강"],
    },
    "화학": {
        "stocks": ["051910.KS", "010950.KS", "006120.KS", "011170.KS"],
        "names": ["LG화학", "S-Oil", "SK디스커버리", "롯데케미칼"],
    },
}


@dataclass
class Position:
    code: str
    name: str
    theme: str
    entry_date: datetime
    entry_price: float
    shares: int
    stop_loss: float
    highest_price: float = 0.0
    trailing_stop: float = 0.0
    trailing_level: int = 0
    atr_at_entry: float = 0.0  # 터틀: 진입 시 ATR

    def __post_init__(self):
        self.highest_price = self.entry_price


@dataclass
class Trade:
    code: str
    name: str
    theme: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_days: int


class TurtleBacktester:

    def __init__(self, mode: str = "pure_turtle"):
        """
        mode:
          - "pure_turtle": 순수 터틀 (20일 돌파, 2ATR 손절, 10일 저가 청산)
          - "theme_turtle_exit": 테마 진입 + 터틀 청산 (10일 저가, 보유일 무제한)
          - "theme_turtle_exit_atr": 테마 진입 + 터틀 청산 + ATR 사이징
          - "hybrid": 테마 + 20일 돌파 필터 + ATR 사이징 + 트레일링
          - "baseline": 기존 전략 (테마 + 균등 배분 + 트레일링)
        """
        self.mode = mode
        self.start_date = "2023-01-01"
        self.end_date = "2026-01-31"
        self.initial_capital = 100_000_000
        self.max_positions = 10

        # 공통
        self.commission = 0.00015
        self.slippage = 0.001

        # 터틀 파라미터
        self.breakout_period = 20       # 20일 고가 돌파
        self.exit_period = 10           # 10일 저가 이탈 청산
        self.atr_stop_multiplier = 2.0  # 2×ATR 손절
        self.risk_per_trade = 0.01      # 1 유닛 = 자본의 1%

        # 테마 파라미터 (hybrid/baseline)
        self.theme_rotation_days = 7
        self.top_themes = 3
        self.stop_loss_pct = -0.07

        # 트레일링 (hybrid/baseline)
        self.trail_l1_threshold = 0.08
        self.trail_l1_pct = 0.05
        self.trail_l2_threshold = 0.15
        self.trail_l2_pct = 0.03
        self.trail_l3_threshold = 0.25
        self.trail_l3_pct = 0.02

        # 상태
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.cash = self.initial_capital
        self.price_data: Dict[str, pd.DataFrame] = {}
        self.equity_curve = []
        self.daily_returns = []

    def load_data(self):
        print("📥 주가 데이터 로드 중...")
        all_symbols = set()
        for info in THEME_STOCKS.values():
            all_symbols.update(info["stocks"])

        for symbol in sorted(all_symbols):
            try:
                df = yf.download(symbol, start=self.start_date, end=self.end_date, progress=False)
                if not df.empty and len(df) > 30:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]
                    df = self._calc_indicators(df)
                    self.price_data[symbol] = df
            except Exception as e:
                print(f"  ⚠ {symbol} 실패: {e}")

        print(f"✅ {len(self.price_data)}개 종목 로드 완료")

    def _calc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # ATR (14일)
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # 거래량 비율
        df['Volume_Ratio'] = df['Volume'] / df['Volume'].rolling(20).mean()

        # 20일 고가 / 10일 저가 (터틀 돌파)
        df['High_20'] = df['High'].rolling(20).max()
        df['Low_10'] = df['Low'].rolling(10).min()
        df['High_55'] = df['High'].rolling(55).max()
        df['Low_20'] = df['Low'].rolling(20).min()

        # 수익률
        df['Return_5D'] = df['Close'].pct_change(5) * 100

        return df

    # ===== 테마 점수 (hybrid/baseline) =====

    def _calc_theme_scores(self, date: datetime) -> Dict[str, float]:
        scores = {}
        for theme, info in THEME_STOCKS.items():
            rets = []
            for sym in info["stocks"]:
                if sym not in self.price_data:
                    continue
                df = self.price_data[sym]
                d = df[df.index <= pd.Timestamp(date)]
                if len(d) < 20:
                    continue
                r = d['Return_5D'].iloc[-1]
                if not pd.isna(r):
                    rets.append(float(r))
            if len(rets) >= 2:
                avg = np.mean(rets)
                scores[theme] = round(((avg + 15) / 30) * 30 + 25 + min(10, len(rets) * 2), 2)
        return scores

    # ===== ATR 포지션 사이징 (터틀) =====

    def _turtle_position_size(self, price: float, atr: float) -> int:
        if atr <= 0 or price <= 0:
            return 0
        # 1유닛 = (자본 × risk_per_trade) / ATR
        dollar_risk = self.cash * self.risk_per_trade
        shares = int(dollar_risk / atr)
        # 최대 자본의 25% 제한
        max_shares = int(self.cash * 0.25 / price)
        return min(shares, max_shares)

    # ===== 균등 배분 사이징 (baseline) =====

    def _equal_position_size(self, price: float) -> int:
        slots = self.max_positions - len(self.positions)
        if slots <= 0 or price <= 0:
            return 0
        per_slot = self.cash / max(slots, 1)
        per_slot = min(per_slot, self.cash * 0.25)
        return int(per_slot / price)

    # ===== 진입 로직 =====

    def _get_row(self, symbol: str, date: datetime) -> Optional[pd.Series]:
        if symbol not in self.price_data:
            return None
        df = self.price_data[symbol]
        d = df[df.index <= pd.Timestamp(date)]
        if len(d) < 25:
            return None
        return d.iloc[-1]

    def _check_pure_turtle_entry(self, date: datetime) -> List[dict]:
        """순수 터틀: 20일 고가 돌파 종목 탐색"""
        candidates = []
        held_codes = {p.code for p in self.positions}

        for theme, info in THEME_STOCKS.items():
            for i, sym in enumerate(info["stocks"]):
                if sym in held_codes:
                    continue
                row = self._get_row(sym, date)
                if row is None:
                    continue

                close = float(row['Close'])
                high_20 = float(row['High_20']) if not pd.isna(row.get('High_20')) else 0
                atr = float(row['ATR']) if not pd.isna(row.get('ATR')) else 0

                if atr <= 0 or high_20 <= 0:
                    continue

                # 20일 고가 돌파
                prev_high_20 = self._get_prev_high20(sym, date)
                if prev_high_20 > 0 and close > prev_high_20:
                    candidates.append({
                        "code": sym,
                        "name": info["names"][i] if i < len(info["names"]) else sym,
                        "theme": theme,
                        "price": close,
                        "atr": atr,
                        "breakout_strength": (close - prev_high_20) / prev_high_20,
                    })

        # 돌파 강도 순 정렬
        candidates.sort(key=lambda x: x["breakout_strength"], reverse=True)
        return candidates

    def _get_prev_high20(self, symbol: str, date: datetime) -> float:
        """전일의 20일 고가 (오늘 종가와 비교하기 위해)"""
        df = self.price_data[symbol]
        d = df[df.index < pd.Timestamp(date)]
        if len(d) < 21:
            return 0
        val = d['High_20'].iloc[-1]
        return float(val) if not pd.isna(val) else 0

    def _check_hybrid_entry(self, date: datetime, current_themes: List[str]) -> List[dict]:
        """하이브리드: 테마 종목 중 20일 돌파 + RSI/거래량 필터"""
        candidates = []
        held_codes = {p.code for p in self.positions}

        for theme in current_themes:
            if theme not in THEME_STOCKS:
                continue
            info = THEME_STOCKS[theme]
            for i, sym in enumerate(info["stocks"]):
                if sym in held_codes:
                    continue
                row = self._get_row(sym, date)
                if row is None:
                    continue

                close = float(row['Close'])
                atr = float(row['ATR']) if not pd.isna(row.get('ATR')) else 0
                rsi = float(row['RSI']) if not pd.isna(row.get('RSI')) else 50
                vol_ratio = float(row['Volume_Ratio']) if not pd.isna(row.get('Volume_Ratio')) else 1
                ma5 = float(row['MA5']) if not pd.isna(row.get('MA5')) else close
                ma20 = float(row['MA20']) if not pd.isna(row.get('MA20')) else close

                if atr <= 0:
                    continue

                # 20일 돌파 확인
                prev_high_20 = self._get_prev_high20(sym, date)
                is_breakout = prev_high_20 > 0 and close > prev_high_20

                # RSI 필터
                if rsi < 30 or rsi > 70:
                    continue

                # 거래량 필터
                if vol_ratio < 0.5:
                    continue

                if not is_breakout:
                    continue

                score = 0
                score += (1 - abs(rsi - 50) / 50) * 30  # RSI 적정대
                score += 20 if ma5 > ma20 else 0          # MA 정배열
                score += min(vol_ratio, 3) / 3 * 20        # 거래량
                score += min((close - prev_high_20) / prev_high_20, 0.05) / 0.05 * 30  # 돌파 강도

                candidates.append({
                    "code": sym,
                    "name": info["names"][i] if i < len(info["names"]) else sym,
                    "theme": theme,
                    "price": close,
                    "atr": atr,
                    "score": score,
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    def _check_baseline_entry(self, date: datetime, current_themes: List[str]) -> List[dict]:
        """기존 전략: 테마 종목 중 RSI/거래량/MA 필터"""
        candidates = []
        held_codes = {p.code for p in self.positions}

        for theme in current_themes:
            if theme not in THEME_STOCKS:
                continue
            info = THEME_STOCKS[theme]
            for i, sym in enumerate(info["stocks"]):
                if sym in held_codes:
                    continue
                row = self._get_row(sym, date)
                if row is None:
                    continue

                close = float(row['Close'])
                atr = float(row['ATR']) if not pd.isna(row.get('ATR')) else 0
                rsi = float(row['RSI']) if not pd.isna(row.get('RSI')) else 50
                vol_ratio = float(row['Volume_Ratio']) if not pd.isna(row.get('Volume_Ratio')) else 1
                ma5 = float(row['MA5']) if not pd.isna(row.get('MA5')) else close
                ma20 = float(row['MA20']) if not pd.isna(row.get('MA20')) else close

                if rsi < 30 or rsi > 70:
                    continue
                if vol_ratio < 0.5:
                    continue

                score = 0
                score += (1 - abs(rsi - 50) / 50) * 30
                score += 20 if ma5 > ma20 else 0
                score += min(vol_ratio, 3) / 3 * 20
                ret5 = float(row['Return_5D']) if not pd.isna(row.get('Return_5D')) else 0
                score += min(max(ret5, 0), 10) / 10 * 30

                candidates.append({
                    "code": sym,
                    "name": info["names"][i] if i < len(info["names"]) else sym,
                    "theme": theme,
                    "price": close,
                    "atr": atr,
                    "score": score,
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # ===== 청산 로직 =====

    def _check_exits(self, date: datetime):
        """포지션 청산 체크"""
        to_close = []

        for pos in self.positions:
            row = self._get_row(pos.code, date)
            if row is None:
                continue

            close = float(row['Close'])
            low = float(row['Low'])
            holding_days = (date - pos.entry_date).days
            profit_pct = (close - pos.entry_price) / pos.entry_price

            # 최고가 갱신
            if close > pos.highest_price:
                pos.highest_price = close

            exit_reason = None

            if self.mode in ("pure_turtle", "theme_turtle_exit", "theme_turtle_exit_atr"):
                # 터틀: 2ATR 손절
                turtle_stop = pos.entry_price - self.atr_stop_multiplier * pos.atr_at_entry
                if low <= turtle_stop:
                    exit_reason = "turtle_stop_2atr"
                    close = turtle_stop

                # 터틀: 10일 저가 이탈
                if exit_reason is None:
                    low_10 = float(row['Low_10']) if not pd.isna(row.get('Low_10')) else 0
                    if low_10 > 0 and close <= low_10:
                        exit_reason = "turtle_exit_10d"

            else:
                # hybrid/baseline: 손절
                if low <= pos.stop_loss:
                    exit_reason = "stop_loss"
                    close = pos.stop_loss

                # 트레일링 스탑
                if exit_reason is None:
                    pos = self._update_trailing(pos, close)
                    if pos.trailing_stop > 0 and low <= pos.trailing_stop:
                        exit_reason = f"trailing_L{pos.trailing_level}"
                        close = pos.trailing_stop

                # 최대 보유일
                if exit_reason is None and holding_days >= 14:
                    exit_reason = "max_hold"

            if exit_reason:
                to_close.append((pos, close, exit_reason, date, holding_days))

        for pos, price, reason, d, hdays in to_close:
            self._close_position(pos, price, reason, d, hdays)

    def _update_trailing(self, pos: Position, price: float) -> Position:
        """트레일링 스탑 업데이트 (hybrid/baseline)"""
        profit_pct = (price - pos.entry_price) / pos.entry_price

        if profit_pct >= self.trail_l3_threshold:
            new_level = 3
            trail_pct = self.trail_l3_pct
        elif profit_pct >= self.trail_l2_threshold:
            new_level = 2
            trail_pct = self.trail_l2_pct
        elif profit_pct >= self.trail_l1_threshold:
            new_level = 1
            trail_pct = self.trail_l1_pct
        else:
            return pos

        pos.trailing_level = new_level
        new_stop = pos.highest_price * (1 - trail_pct)
        if new_stop > pos.trailing_stop:
            pos.trailing_stop = new_stop
        if pos.trailing_stop > pos.stop_loss:
            pos.stop_loss = pos.trailing_stop

        return pos

    def _close_position(self, pos: Position, price: float, reason: str, date: datetime, holding_days: int):
        exit_price = price * (1 - self.slippage)
        pnl = (exit_price - pos.entry_price) * pos.shares
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100

        # 수수료
        pnl -= (pos.entry_price * pos.shares + exit_price * pos.shares) * self.commission

        self.cash += exit_price * pos.shares
        self.trades.append(Trade(
            code=pos.code, name=pos.name, theme=pos.theme,
            entry_date=pos.entry_date, exit_date=date,
            entry_price=pos.entry_price, exit_price=exit_price,
            shares=pos.shares, pnl=pnl, pnl_pct=pnl_pct,
            exit_reason=reason, holding_days=holding_days,
        ))
        self.positions.remove(pos)

    # ===== 메인 루프 =====

    def run(self):
        self.load_data()
        if not self.price_data:
            print("❌ 데이터 없음")
            return

        # 거래일 목록
        sample_df = list(self.price_data.values())[0]
        trading_days = sample_df.index.tolist()

        current_themes = []
        last_rotation = datetime(2000, 1, 1)
        prev_equity = self.initial_capital

        print(f"\n🚀 백테스트 시작 [{self.mode}]")
        print(f"   기간: {self.start_date} ~ {self.end_date}")
        print(f"   자본: {self.initial_capital:,.0f}원\n")

        for i, ts_date in enumerate(trading_days):
            date = ts_date.to_pydatetime()

            # 테마 로테이션 (pure_turtle 제외 전부)
            if self.mode != "pure_turtle":
                if (date - last_rotation).days >= self.theme_rotation_days:
                    scores = self._calc_theme_scores(date)
                    sorted_themes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    current_themes = [t[0] for t in sorted_themes[:self.top_themes]]
                    last_rotation = date

            # 청산 체크
            self._check_exits(date)

            # 진입
            if len(self.positions) < self.max_positions:
                if self.mode == "pure_turtle":
                    candidates = self._check_pure_turtle_entry(date)
                elif self.mode == "hybrid":
                    candidates = self._check_hybrid_entry(date, current_themes)
                elif self.mode in ("theme_turtle_exit", "theme_turtle_exit_atr"):
                    candidates = self._check_baseline_entry(date, current_themes)
                else:
                    candidates = self._check_baseline_entry(date, current_themes)

                for c in candidates:
                    if len(self.positions) >= self.max_positions:
                        break

                    price = c["price"] * (1 + self.slippage)
                    atr = c["atr"]

                    # 포지션 사이징
                    if self.mode in ("pure_turtle", "hybrid", "theme_turtle_exit_atr"):
                        shares = self._turtle_position_size(price, atr)
                    else:
                        shares = self._equal_position_size(price)

                    if shares <= 0:
                        continue

                    cost = price * shares
                    if cost > self.cash:
                        shares = int(self.cash * 0.95 / price)
                        if shares <= 0:
                            continue
                        cost = price * shares

                    # 손절가
                    if self.mode in ("pure_turtle", "theme_turtle_exit", "theme_turtle_exit_atr"):
                        stop = price - self.atr_stop_multiplier * atr
                    else:
                        atr_stop_pct = -atr / price * 2
                        stop_pct = max(self.stop_loss_pct, min(-0.05, atr_stop_pct))
                        stop = price * (1 + stop_pct)

                    self.cash -= cost
                    self.positions.append(Position(
                        code=c["code"], name=c["name"], theme=c["theme"],
                        entry_date=date, entry_price=price, shares=shares,
                        stop_loss=stop, atr_at_entry=atr,
                    ))

            # 자산 기록
            equity = self.cash
            for pos in self.positions:
                row = self._get_row(pos.code, date)
                if row is not None:
                    equity += float(row['Close']) * pos.shares
                else:
                    equity += pos.entry_price * pos.shares

            self.equity_curve.append({"date": date, "equity": equity, "positions": len(self.positions)})
            if prev_equity > 0:
                self.daily_returns.append((equity - prev_equity) / prev_equity * 100)
            prev_equity = equity

        # 잔여 포지션 강제 청산
        if self.positions:
            last_date = trading_days[-1].to_pydatetime()
            for pos in list(self.positions):
                row = self._get_row(pos.code, last_date)
                price = float(row['Close']) if row is not None else pos.entry_price
                hdays = (last_date - pos.entry_date).days
                self._close_position(pos, price, "backtest_end", last_date, hdays)

        self._print_results()

    def _print_results(self):
        final = self.equity_curve[-1]["equity"] if self.equity_curve else self.initial_capital
        total_ret = (final - self.initial_capital) / self.initial_capital * 100

        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        years = max((end - start).days / 365, 0.01)
        cagr = ((final / self.initial_capital) ** (1 / years) - 1) * 100

        # MDD
        eq = pd.Series([e["equity"] for e in self.equity_curve])
        running_max = eq.cummax()
        mdd = ((eq - running_max) / running_max * 100).min()

        # Sharpe
        if self.daily_returns:
            dr = np.array(self.daily_returns)
            sharpe = (dr.mean() * 252) / (dr.std() * sqrt(252)) if dr.std() > 0 else 0
        else:
            sharpe = 0

        # 거래 통계
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        avg_hold = np.mean([t.holding_days for t in self.trades]) if self.trades else 0

        # 손익비
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        mode_name = {
            "pure_turtle": "순수 터틀 트레이딩",
            "theme_turtle_exit": "테마진입 + 터틀청산 (균등배분)",
            "theme_turtle_exit_atr": "테마진입 + 터틀청산 (ATR사이징)",
            "hybrid": "하이브리드 (테마+터틀)",
            "baseline": "기존 전략 (테마+트레일링)",
        }[self.mode]

        print(f"\n{'=' * 60}")
        print(f"  {mode_name} 결과")
        print(f"{'=' * 60}")
        print(f"\n  📊 수익")
        print(f"  {'초기 자본:':<20} {self.initial_capital:>15,.0f}원")
        print(f"  {'최종 자본:':<20} {final:>15,.0f}원")
        print(f"  {'총 수익률:':<20} {total_ret:>14.2f}%")
        print(f"  {'CAGR:':<20} {cagr:>14.2f}%")
        print(f"  {'MDD:':<20} {mdd:>14.2f}%")
        print(f"  {'Sharpe:':<20} {sharpe:>14.2f}")
        print(f"\n  📈 거래")
        print(f"  {'총 거래:':<20} {len(self.trades):>10}건")
        print(f"  {'승률:':<20} {win_rate:>13.1f}%")
        print(f"  {'평균 수익 (승):':<20} {avg_win:>13.2f}%")
        print(f"  {'평균 손실 (패):':<20} {avg_loss:>13.2f}%")
        print(f"  {'손익비:':<20} {payoff:>14.2f}")
        print(f"  {'평균 보유일:':<20} {avg_hold:>12.1f}일")

        # 청산 사유별
        reason_stats = {}
        for t in self.trades:
            r = t.exit_reason
            if r not in reason_stats:
                reason_stats[r] = {"count": 0, "pnls": []}
            reason_stats[r]["count"] += 1
            reason_stats[r]["pnls"].append(t.pnl_pct)

        print(f"\n  🔚 청산 사유")
        for r, s in sorted(reason_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            avg_pnl = np.mean(s["pnls"])
            print(f"  {r:<25} {s['count']:>4}건, 평균 {avg_pnl:>+7.2f}%")

        # 테마별 성과
        theme_stats = {}
        for t in self.trades:
            if t.theme not in theme_stats:
                theme_stats[t.theme] = {"count": 0, "pnl": 0, "wins": 0}
            theme_stats[t.theme]["count"] += 1
            theme_stats[t.theme]["pnl"] += t.pnl
            if t.pnl > 0:
                theme_stats[t.theme]["wins"] += 1

        print(f"\n  🏷️ 테마별 성과")
        for th, s in sorted(theme_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
            print(f"  {th:<10} {s['count']:>3}건, 승률 {wr:>5.1f}%, 손익 {s['pnl']:>+12,.0f}원")

        print(f"\n{'=' * 60}\n")

        return {
            "mode": self.mode,
            "total_return": total_ret,
            "cagr": cagr,
            "mdd": mdd,
            "sharpe": sharpe,
            "trades": len(self.trades),
            "win_rate": win_rate,
            "payoff": payoff,
        }


def main():
    results = []

    for mode in ["theme_turtle_exit", "theme_turtle_exit_atr", "baseline"]:
        bt = TurtleBacktester(mode=mode)
        r = bt.run()
        if r:
            results.append(r)
        print()

    # 비교표
    if len(results) >= 2:
        print("=" * 80)
        print("  📊 전략 비교 요약")
        print("=" * 80)
        header = f"  {'전략':<30} {'수익률':>8} {'CAGR':>8} {'MDD':>8} {'Sharpe':>8} {'승률':>7} {'손익비':>7}"
        print(header)
        print("  " + "-" * 76)
        for r in results:
            name = {
                "pure_turtle": "순수 터틀",
                "theme_turtle_exit": "테마진입+터틀청산(균등)",
                "theme_turtle_exit_atr": "테마진입+터틀청산(ATR)",
                "hybrid": "테마+돌파필터+ATR",
                "baseline": "기존(테마+트레일링)",
            }[r["mode"]]
            print(f"  {name:<30} {r['total_return']:>7.1f}% {r['cagr']:>7.1f}% {r['mdd']:>7.1f}% {r['sharpe']:>8.2f} {r['win_rate']:>6.1f}% {r['payoff']:>7.2f}")
        print("=" * 80)


if __name__ == "__main__":
    main()
