"""
backtest_supply_momentum.py - 수급 모멘텀 전략 백테스트

전략 핵심:
- 외국인/기관 동시 순매수를 핵심 진입 시그널로 격상
- yfinance에 수급 데이터 없으므로 Volume-Price 기반 프록시 사용:
  1. Money Flow (거래량 * 가격방향) 5일 연속 양수 = 매수세 지속
  2. OBV(On-Balance Volume) 상승 추세 = 누적 매수 우위
  3. Volume Ratio > 1.2 = 거래량 증가 동반

기존 전략과 차이점:
- 기존: 테마 모멘텀(주가 5일 수익률) → 테마 선정 → 종목 기술적 필터
- 수급: 개별 종목 수급 시그널 → 수급 강한 종목만 진입 (테마 무관)

사용법:
    python scripts/backtest_supply_momentum.py
    python scripts/backtest_supply_momentum.py --compare       # 기존 전략 비교
    python scripts/backtest_supply_momentum.py --test-days     # 연속일수 테스트 (3/5/7일)
    python scripts/backtest_supply_momentum.py --test-combo    # 수급+테마 결합 테스트
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yfinance as yf

from logger import logger


# ===== 동일 종목 유니버스 (backtest_live_logic.py에서 가져옴) =====
THEME_STOCKS = {
    "2차전지": {
        "stocks": ["373220.KS", "006400.KS", "051910.KS", "086520.KS", "247540.KS"],
        "names": ["LG에너지솔루션", "삼성SDI", "LG화학", "에코프로비엠", "에코프로"],
        "category": "신성장"
    },
    "반도체": {
        "stocks": ["005930.KS", "000660.KS", "042700.KS", "058470.KS"],
        "names": ["삼성전자", "SK하이닉스", "한미반도체", "리노공업"],
        "category": "반도체"
    },
    "바이오": {
        "stocks": ["207940.KS", "068270.KS", "128940.KS", "145020.KS"],
        "names": ["삼성바이오로직스", "셀트리온", "한미약품", "휴젤"],
        "category": "헬스케어"
    },
    "자동차": {
        "stocks": ["005380.KS", "000270.KS", "012330.KS", "011210.KS"],
        "names": ["현대차", "기아", "현대모비스", "현대위아"],
        "category": "자동차"
    },
    "조선": {
        "stocks": ["009540.KS", "010140.KS", "042660.KS"],
        "names": ["HD한국조선해양", "삼성중공업", "대우조선해양"],
        "category": "산업재"
    },
    "방산": {
        "stocks": ["012450.KS", "047810.KS", "082740.KS", "003570.KS"],
        "names": ["한화에어로스페이스", "한국항공우주", "한화시스템", "SNT다이나믹스"],
        "category": "방위산업"
    },
    "엔터": {
        "stocks": ["352820.KS", "122870.KS", "041510.KS", "035900.KS"],
        "names": ["하이브", "와이지엔터테인먼트", "에스엠", "JYP엔터테인먼트"],
        "category": "엔터테인먼트"
    },
    "금융": {
        "stocks": ["105560.KS", "055550.KS", "086790.KS", "024110.KS"],
        "names": ["KB금융", "신한지주", "하나금융지주", "기업은행"],
        "category": "금융"
    },
    "철강": {
        "stocks": ["005490.KS", "004020.KS", "001230.KS"],
        "names": ["POSCO홀딩스", "현대제철", "동국제강"],
        "category": "소재"
    },
    "화학": {
        "stocks": ["051910.KS", "010950.KS", "006120.KS", "011170.KS"],
        "names": ["LG화학", "S-Oil", "SK디스커버리", "롯데케미칼"],
        "category": "화학"
    },
}

# 종목 코드 → (이름, 테마) 매핑
STOCK_INFO = {}
for theme, info in THEME_STOCKS.items():
    for i, symbol in enumerate(info["stocks"]):
        if symbol not in STOCK_INFO:
            STOCK_INFO[symbol] = {
                "name": info["names"][i] if i < len(info["names"]) else symbol,
                "theme": theme,
            }


@dataclass
class Position:
    """포지션 정보"""
    code: str
    name: str
    theme: str
    entry_date: datetime
    entry_price: float
    shares: int
    weight: float
    stop_loss: float
    supply_score: float  # 진입 시 수급 점수

    # 트레일링 스탑
    highest_price: float = 0.0
    trailing_stop: float = 0.0
    trailing_active: bool = False
    trailing_level: int = 0
    max_profit_pct: float = 0.0

    def __post_init__(self):
        self.highest_price = self.entry_price


@dataclass
class Trade:
    """거래 기록"""
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
    supply_score: float = 0.0


@dataclass
class SupplyMomentumConfig:
    """수급 모멘텀 백테스트 설정"""
    start_date: str = "2023-01-01"
    end_date: str = "2026-01-31"
    initial_capital: float = 100_000_000  # 1억

    # === 수급 시그널 설정 ===
    consecutive_days: int = 5           # N일 연속 양의 Money Flow
    min_volume_ratio: float = 1.2       # 최소 거래량 비율 (20일 평균 대비)
    obv_slope_days: int = 5             # OBV 기울기 계산 기간
    min_obv_slope: float = 0.0          # 최소 OBV 기울기 (양수 = 상승)
    mfi_period: int = 14                # MFI 기간
    min_mfi: float = 50                 # 최소 MFI (50 이상 = 매수 우위)

    # === 기술적 필터 (보조) ===
    min_rsi: float = 30
    max_rsi: float = 75                 # 과열 구간 약간 여유
    require_ma_alignment: bool = True   # MA5 > MA20 필수 여부

    # === 포지션 관리 ===
    max_positions: int = 10
    scan_interval_days: int = 1         # 매일 스캔 (수급은 일일 변동)

    # === 매매 전략 (동일 트레일링) ===
    stop_loss_pct: float = -0.07
    max_holding_days: int = 14

    # 단계별 트레일링
    trail_activation_pct: float = 0.08
    trail_level1_pct: float = 0.05
    trail_level2_threshold: float = 0.15
    trail_level2_pct: float = 0.03
    trail_level3_threshold: float = 0.25
    trail_level3_pct: float = 0.02

    # 기타
    commission: float = 0.00015
    slippage: float = 0.001


class SupplyMomentumBacktester:
    """수급 모멘텀 전략 백테스터"""

    def __init__(self, config: SupplyMomentumConfig):
        self.config = config
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.capital = config.initial_capital
        self.cash = config.initial_capital

        # 데이터 캐시
        self.price_data: Dict[str, pd.DataFrame] = {}

        # 기록
        self.equity_curve = []
        self.daily_returns = []

    def load_data(self):
        """주가 데이터 로드 + 수급 지표 계산"""
        logger.info("Loading price data...")

        all_symbols = []
        for theme, info in THEME_STOCKS.items():
            all_symbols.extend(info["stocks"])
        all_symbols = list(set(all_symbols))

        for symbol in all_symbols:
            try:
                df = yf.download(
                    symbol,
                    start=self.config.start_date,
                    end=self.config.end_date,
                    progress=False
                )

                if not df.empty and len(df) > 30:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]

                    df = self._calculate_indicators(df)
                    df = self._calculate_supply_indicators(df)
                    self.price_data[symbol] = df
                    logger.debug(f"  {symbol}: {len(df)}d loaded")

            except Exception as e:
                logger.warning(f"  {symbol} load failed: {e}")

        logger.info(f"  {len(self.price_data)} stocks loaded")

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기본 기술적 지표"""
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 거래량 비율
        df['Volume_Ratio'] = df['Volume'] / df['Volume'].rolling(20).mean()

        return df

    def _calculate_supply_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        수급 프록시 지표 계산

        1. Money Flow Direction: close > open이면 매수일, 반대면 매도일
        2. Money Flow Volume: 방향 * 거래량 (큰 거래량 + 양봉 = 강한 매수세)
        3. OBV (On-Balance Volume): 누적 매수/매도 압력
        4. MFI (Money Flow Index): 가격-거래량 종합 지표
        5. Consecutive Positive MF Days: 연속 양의 Money Flow 일수
        """
        # 1. Money Flow Direction (+1/-1)
        df['MF_Direction'] = np.where(df['Close'] >= df['Open'], 1, -1)

        # 2. Money Flow Volume (방향 * 거래량, 정규화)
        avg_volume = df['Volume'].rolling(20).mean()
        df['MF_Volume'] = df['MF_Direction'] * (df['Volume'] / avg_volume.replace(0, 1))

        # 3. OBV (On-Balance Volume)
        obv = pd.Series(0.0, index=df.index)
        for i in range(1, len(df)):
            if df['Close'].iloc[i] > df['Close'].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] + df['Volume'].iloc[i]
            elif df['Close'].iloc[i] < df['Close'].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] - df['Volume'].iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i-1]
        df['OBV'] = obv

        # OBV N일 기울기 (정규화)
        n = self.config.obv_slope_days
        obv_ma = df['OBV'].rolling(n).mean()
        obv_shift = df['OBV'].shift(n)
        # 기울기를 평균 거래량으로 정규화
        df['OBV_Slope'] = (obv_ma - obv_shift) / avg_volume.replace(0, 1)

        # 4. MFI (Money Flow Index)
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        raw_money_flow = typical_price * df['Volume']

        positive_flow = pd.Series(0.0, index=df.index)
        negative_flow = pd.Series(0.0, index=df.index)

        for i in range(1, len(df)):
            if typical_price.iloc[i] > typical_price.iloc[i-1]:
                positive_flow.iloc[i] = raw_money_flow.iloc[i]
            else:
                negative_flow.iloc[i] = raw_money_flow.iloc[i]

        mfi_period = self.config.mfi_period
        pos_sum = positive_flow.rolling(mfi_period).sum()
        neg_sum = negative_flow.rolling(mfi_period).sum()
        money_ratio = pos_sum / neg_sum.replace(0, 1)
        df['MFI'] = 100 - (100 / (1 + money_ratio))

        # 5. 연속 양의 Money Flow 일수
        consecutive = pd.Series(0, index=df.index)
        for i in range(1, len(df)):
            if df['MF_Direction'].iloc[i] > 0 and df['Volume_Ratio'].iloc[i] > 0.8:
                consecutive.iloc[i] = consecutive.iloc[i-1] + 1
            else:
                consecutive.iloc[i] = 0
        df['Consecutive_Positive_MF'] = consecutive

        # 6. 수급 종합 점수 (0~100)
        # MFI 점수 (0~40): MFI 50이상이면 매수우위
        mfi_score = ((df['MFI'] - 30) / 40 * 40).clip(0, 40)

        # 연속일수 점수 (0~30): 5일 연속이면 만점
        consec_score = (df['Consecutive_Positive_MF'] / 5 * 30).clip(0, 30)

        # OBV 기울기 점수 (0~20): 양의 기울기면 가점
        obv_score = (df['OBV_Slope'].clip(-2, 2) / 2 * 10 + 10).clip(0, 20)

        # 거래량 점수 (0~10): 평균 이상이면 가점
        vol_score = ((df['Volume_Ratio'] - 0.5) / 1.5 * 10).clip(0, 10)

        df['Supply_Score'] = mfi_score + consec_score + obv_score + vol_score

        return df

    def get_supply_signals(self, date: datetime) -> List[dict]:
        """
        수급 시그널 기반 매수 후보 생성

        조건:
        1. N일 연속 양의 Money Flow
        2. MFI > min_mfi (매수 우위)
        3. OBV 기울기 양수 (매수 누적 증가)
        4. 거래량 비율 > min_volume_ratio
        5. RSI 필터 (과매수/과매도 배제)
        6. MA 정배열 (선택)
        """
        candidates = []

        for symbol, df in self.price_data.items():
            df_until = df[df.index <= pd.Timestamp(date)]
            if len(df_until) < 30:
                continue

            latest = df_until.iloc[-1]
            info = STOCK_INFO.get(symbol, {"name": symbol, "theme": "기타"})

            # --- 수급 조건 (핵심) ---

            # 1. N일 연속 양의 Money Flow
            consec = int(latest['Consecutive_Positive_MF'])
            if consec < self.config.consecutive_days:
                continue

            # 2. MFI 조건
            mfi = float(latest['MFI']) if not pd.isna(latest['MFI']) else 0
            if mfi < self.config.min_mfi:
                continue

            # 3. OBV 기울기
            obv_slope = float(latest['OBV_Slope']) if not pd.isna(latest['OBV_Slope']) else 0
            if obv_slope < self.config.min_obv_slope:
                continue

            # 4. 거래량 비율
            vol_ratio = float(latest['Volume_Ratio']) if not pd.isna(latest['Volume_Ratio']) else 0
            if vol_ratio < self.config.min_volume_ratio:
                continue

            # --- 기술적 필터 (보조) ---

            # 5. RSI 필터
            rsi = float(latest['RSI']) if not pd.isna(latest['RSI']) else 50
            if rsi < self.config.min_rsi or rsi > self.config.max_rsi:
                continue

            # 6. MA 정배열 (선택)
            price = float(latest['Close'])
            ma5 = float(latest['MA5']) if not pd.isna(latest['MA5']) else 0
            ma20 = float(latest['MA20']) if not pd.isna(latest['MA20']) else 0

            if self.config.require_ma_alignment and ma5 > 0 and ma20 > 0:
                if ma5 <= ma20:
                    continue

            # 수급 점수
            supply_score = float(latest['Supply_Score']) if not pd.isna(latest['Supply_Score']) else 0

            candidates.append({
                "code": symbol,
                "name": info["name"],
                "theme": info["theme"],
                "price": price,
                "supply_score": supply_score,
                "consecutive_days": consec,
                "mfi": mfi,
                "obv_slope": obv_slope,
                "volume_ratio": vol_ratio,
                "rsi": rsi,
            })

        # 수급 점수순 정렬
        candidates.sort(key=lambda x: x["supply_score"], reverse=True)
        return candidates

    def execute_entry(self, candidates: List[dict], date: datetime):
        """진입 실행"""
        available_slots = self.config.max_positions - len(self.positions)
        if available_slots <= 0 or not candidates:
            return

        investable = self.cash * 0.95  # 5% 현금 버퍼
        selected = candidates[:available_slots]

        # 균등 배분 (수급 점수 기반 가중 배분도 가능하지만 단순화)
        if not selected:
            return

        total_score = sum(c["supply_score"] for c in selected)

        for stock in selected:
            # 이미 보유 중인지 체크
            if any(p.code == stock["code"] for p in self.positions):
                continue

            # 점수 기반 가중치
            if total_score > 0:
                weight = stock["supply_score"] / total_score
            else:
                weight = 1 / len(selected)

            weight = max(0.05, min(0.3, weight))

            amount = investable * weight
            price = stock["price"] * (1 + self.config.slippage)
            shares = int(amount / price)

            if shares <= 0:
                continue

            commission = price * shares * self.config.commission
            actual_amount = price * shares + commission

            if actual_amount > self.cash:
                continue

            stop_loss = price * (1 + self.config.stop_loss_pct)

            position = Position(
                code=stock["code"],
                name=stock["name"],
                theme=stock["theme"],
                entry_date=date,
                entry_price=price,
                shares=shares,
                weight=weight,
                stop_loss=stop_loss,
                supply_score=stock["supply_score"],
            )

            self.positions.append(position)
            self.cash -= actual_amount

            logger.debug(
                f"  BUY: {stock['name']} {shares}sh @ {price:,.0f} "
                f"(supply={stock['supply_score']:.0f}, consec={stock['consecutive_days']}d)"
            )

    def check_exits(self, date: datetime):
        """청산 조건 체크 (동일 트레일링 전략)"""
        positions_to_close = []

        for pos in self.positions:
            if pos.code not in self.price_data:
                continue

            df = self.price_data[pos.code]
            df_until = df[df.index <= pd.Timestamp(date)]

            if df_until.empty:
                continue

            current_price = float(df_until.iloc[-1]['Close'])
            holding_days = (date - pos.entry_date).days
            current_profit_pct = (current_price - pos.entry_price) / pos.entry_price

            # 고점 갱신
            if current_price > pos.highest_price:
                pos.highest_price = current_price
            if current_profit_pct > pos.max_profit_pct:
                pos.max_profit_pct = current_profit_pct

            exit_reason = None
            exit_price = current_price

            # === 트레일링 레벨 업데이트 ===
            if current_profit_pct >= self.config.trail_level3_threshold:
                if pos.trailing_level < 3:
                    pos.trailing_level = 3
                    pos.trailing_active = True
            elif current_profit_pct >= self.config.trail_level2_threshold:
                if pos.trailing_level < 2:
                    pos.trailing_level = 2
                    pos.trailing_active = True
            elif current_profit_pct >= self.config.trail_activation_pct:
                if pos.trailing_level < 1:
                    pos.trailing_level = 1
                    pos.trailing_active = True
                    pos.stop_loss = pos.entry_price  # 본전 손절

            # 트레일링 스탑 가격 계산
            if pos.trailing_active:
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

            # === 청산 조건 ===

            # 1. 손절 / 트레일링 스탑
            if current_price <= pos.stop_loss:
                if pos.trailing_active:
                    exit_reason = f"trailing_L{pos.trailing_level}"
                    exit_price = pos.trailing_stop
                else:
                    exit_reason = "stop_loss"
                    exit_price = pos.stop_loss

            # 2. 보유기간 초과
            if not exit_reason and holding_days >= self.config.max_holding_days:
                exit_reason = "max_hold"

            # 3. 수급 악화 (추가 청산 조건: 수급 점수 급락 시 조기 청산)
            if not exit_reason and holding_days >= 3:
                latest = df_until.iloc[-1]
                current_supply = float(latest['Supply_Score']) if not pd.isna(latest['Supply_Score']) else 0
                # 진입 시 수급 점수 대비 40% 이하로 하락하면 수급 이탈
                if current_supply < pos.supply_score * 0.4 and current_profit_pct < 0.03:
                    exit_reason = "supply_exit"

            if exit_reason:
                positions_to_close.append((pos, exit_price, exit_reason, holding_days))

        # 청산 실행
        for pos, exit_price, reason, days in positions_to_close:
            pnl = (exit_price - pos.entry_price) * pos.shares
            pnl_pct = pnl / (pos.entry_price * pos.shares) * 100

            commission = exit_price * pos.shares * self.config.commission
            self.cash += exit_price * pos.shares - commission

            trade = Trade(
                code=pos.code,
                name=pos.name,
                theme=pos.theme,
                entry_date=pos.entry_date,
                exit_date=date,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                shares=pos.shares,
                pnl=pnl,
                pnl_pct=pnl_pct,
                exit_reason=reason,
                holding_days=days,
                supply_score=pos.supply_score,
            )
            self.trades.append(trade)
            self.positions.remove(pos)

    def calculate_equity(self, date: datetime) -> float:
        """현재 자산 가치"""
        equity = self.cash
        for pos in self.positions:
            if pos.code not in self.price_data:
                continue
            df = self.price_data[pos.code]
            df_until = df[df.index <= pd.Timestamp(date)]
            if not df_until.empty:
                equity += float(df_until.iloc[-1]['Close']) * pos.shares
        return equity

    def run(self):
        """백테스트 실행"""
        logger.info("=" * 60)
        logger.info("Supply Momentum Backtest Start")
        logger.info("=" * 60)
        logger.info(f"Period: {self.config.start_date} ~ {self.config.end_date}")
        logger.info(f"Capital: {self.config.initial_capital:,}")
        logger.info(f"Consecutive days: {self.config.consecutive_days}")

        self.load_data()

        if not self.price_data:
            logger.error("No data loaded")
            return

        # 거래일 목록
        sample_df = list(self.price_data.values())[0]
        trading_days = sample_df.index.tolist()

        logger.info(f"Trading days: {len(trading_days)}")
        logger.info("-" * 60)

        for i, date in enumerate(trading_days):
            date_dt = date.to_pydatetime()

            # 청산 체크 (매일)
            self.check_exits(date_dt)

            # 수급 시그널 스캔 (매일)
            if len(self.positions) < self.config.max_positions:
                candidates = self.get_supply_signals(date_dt)
                if candidates:
                    self.execute_entry(candidates, date_dt)

            # 자산 기록
            equity = self.calculate_equity(date_dt)
            self.equity_curve.append({
                "date": date_dt,
                "equity": equity,
                "positions": len(self.positions),
            })

            if i > 0:
                prev_equity = self.equity_curve[-2]["equity"]
                daily_return = (equity - prev_equity) / prev_equity * 100
                self.daily_returns.append(daily_return)

        # 잔여 포지션 청산
        final_date = trading_days[-1].to_pydatetime()
        for pos in list(self.positions):
            if pos.code in self.price_data:
                df = self.price_data[pos.code]
                exit_price = float(df.iloc[-1]['Close'])
                pnl = (exit_price - pos.entry_price) * pos.shares
                pnl_pct = pnl / (pos.entry_price * pos.shares) * 100

                trade = Trade(
                    code=pos.code, name=pos.name, theme=pos.theme,
                    entry_date=pos.entry_date, exit_date=final_date,
                    entry_price=pos.entry_price, exit_price=exit_price,
                    shares=pos.shares, pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason="backtest_end",
                    holding_days=(final_date - pos.entry_date).days,
                    supply_score=pos.supply_score,
                )
                self.trades.append(trade)
                self.cash += exit_price * pos.shares

        self.positions = []
        self.print_results()

    def print_results(self):
        """결과 출력"""
        if not self.equity_curve:
            print("No data")
            return

        initial = self.config.initial_capital
        final = self.equity_curve[-1]["equity"]
        total_return = (final - initial) / initial * 100

        start = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.config.end_date, "%Y-%m-%d")
        years = (end - start).days / 365
        cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

        equity_series = pd.Series([e["equity"] for e in self.equity_curve])
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max * 100
        mdd = drawdown.min()

        if self.daily_returns:
            avg_return = np.mean(self.daily_returns)
            std_return = np.std(self.daily_returns)
            sharpe = (avg_return * 252) / (std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0

        if self.trades:
            wins = [t for t in self.trades if t.pnl > 0]
            losses = [t for t in self.trades if t.pnl <= 0]
            win_rate = len(wins) / len(self.trades) * 100
            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
            avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
            avg_holding = np.mean([t.holding_days for t in self.trades])
            avg_supply = np.mean([t.supply_score for t in self.trades])
        else:
            win_rate = avg_win = avg_loss = avg_holding = avg_supply = 0

        print("\n" + "=" * 60)
        print("  Supply Momentum Strategy Results")
        print("=" * 60)

        print(f"\n{'Config':}")
        print(f"  Consecutive MF days: {self.config.consecutive_days}")
        print(f"  Min volume ratio:    {self.config.min_volume_ratio}")
        print(f"  Min MFI:             {self.config.min_mfi}")
        print(f"  MA alignment:        {self.config.require_ma_alignment}")

        print(f"\n{'Returns':}")
        print(f"  Initial capital: {initial:>15,.0f}")
        print(f"  Final capital:   {final:>15,.0f}")
        print(f"  Total return:    {total_return:>14.2f}%")
        print(f"  CAGR:            {cagr:>14.2f}%")
        print(f"  MDD:             {mdd:>14.2f}%")
        print(f"  Sharpe ratio:    {sharpe:>14.2f}")

        print(f"\n{'Trades':}")
        print(f"  Total trades:    {len(self.trades):>15}")
        print(f"  Win rate:        {win_rate:>14.1f}%")
        print(f"  Avg win:         {avg_win:>14.2f}%")
        print(f"  Avg loss:        {avg_loss:>14.2f}%")
        print(f"  Avg holding:     {avg_holding:>14.1f}d")
        print(f"  Avg supply score:{avg_supply:>14.1f}")

        # 청산 사유별 통계
        print(f"\n{'Exit reasons':}")
        reasons = {}
        for t in self.trades:
            if t.exit_reason not in reasons:
                reasons[t.exit_reason] = {"count": 0, "pnl": 0}
            reasons[t.exit_reason]["count"] += 1
            reasons[t.exit_reason]["pnl"] += t.pnl_pct

        for reason, data in sorted(reasons.items(), key=lambda x: x[1]["count"], reverse=True):
            avg_pnl = data["pnl"] / data["count"] if data["count"] > 0 else 0
            print(f"  {reason:<20}: {data['count']:>5}x, avg {avg_pnl:+.2f}%")

        # 테마별 성과
        print(f"\n{'Theme performance':}")
        theme_stats = {}
        for t in self.trades:
            if t.theme not in theme_stats:
                theme_stats[t.theme] = {"count": 0, "pnl": 0}
            theme_stats[t.theme]["count"] += 1
            theme_stats[t.theme]["pnl"] += t.pnl_pct

        for theme, data in sorted(theme_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            avg_pnl = data["pnl"] / data["count"] if data["count"] > 0 else 0
            print(f"  {theme:<12}: {data['count']:>5}x, avg {avg_pnl:+.2f}%")

        # 수급 점수 구간별 승률
        print(f"\n{'Win rate by supply score':}")
        score_bins = [(0, 40), (40, 60), (60, 80), (80, 100)]
        for low, high in score_bins:
            bin_trades = [t for t in self.trades if low <= t.supply_score < high]
            if bin_trades:
                bin_wins = len([t for t in bin_trades if t.pnl > 0])
                bin_wr = bin_wins / len(bin_trades) * 100
                bin_avg = np.mean([t.pnl_pct for t in bin_trades])
                print(f"  {low:>3}~{high:<3}: {len(bin_trades):>4}x, win {bin_wr:.1f}%, avg {bin_avg:+.2f}%")

        print("\n" + "=" * 60)

        # 결과 저장
        self.save_results()

    def save_results(self):
        """결과 저장"""
        Path("data").mkdir(exist_ok=True)

        equity_df = pd.DataFrame(self.equity_curve)
        equity_df.to_csv("data/backtest_supply_equity.csv", index=False)

        trades_data = [
            {
                "code": t.code, "name": t.name, "theme": t.theme,
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d"),
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "shares": t.shares, "pnl": t.pnl, "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason, "holding_days": t.holding_days,
                "supply_score": t.supply_score,
            }
            for t in self.trades
        ]
        trades_df = pd.DataFrame(trades_data)
        trades_df.to_csv("data/backtest_supply_trades.csv", index=False)

        print(f"\nSaved: data/backtest_supply_equity.csv")
        print(f"Saved: data/backtest_supply_trades.csv")

    def get_summary_dict(self) -> dict:
        """비교용 요약 딕셔너리"""
        if not self.equity_curve:
            return {}

        initial = self.config.initial_capital
        final = self.equity_curve[-1]["equity"]
        total_return = (final - initial) / initial * 100

        equity_series = pd.Series([e["equity"] for e in self.equity_curve])
        mdd = ((equity_series - equity_series.cummax()) / equity_series.cummax() * 100).min()

        if self.daily_returns:
            std_return = np.std(self.daily_returns)
            avg_return = np.mean(self.daily_returns)
            sharpe = (avg_return * 252) / (std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0

        wins = len([t for t in self.trades if t.pnl > 0])
        win_rate = wins / len(self.trades) * 100 if self.trades else 0

        return {
            'final': final,
            'return': total_return,
            'mdd': mdd,
            'sharpe': sharpe,
            'trades': len(self.trades),
            'wins': wins,
            'win_rate': win_rate,
        }


# ===== 기존 전략 (비교용 - backtest_live_logic에서 간략화) =====

def run_theme_baseline(start_date="2023-01-01", end_date="2026-01-31"):
    """기존 테마 전략 실행 (비교 기준선)"""
    # backtest_live_logic.py 임포트
    from scripts.backtest_live_logic import LiveLogicBacktester, BacktestConfig

    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000,
        top_themes=3,
        theme_rotation_days=7,
        max_positions=10,
        stocks_per_theme=3,
        stop_loss_pct=-0.07,
        enable_profit_trailing=True,
        enable_fixed_take_profit=False,
        enable_partial_profit=False,
        max_holding_days=14,
        trail_activation_pct=0.08,
        trail_level1_pct=0.05,
        trail_level2_threshold=0.15,
        trail_level2_pct=0.03,
        trail_level3_threshold=0.25,
        trail_level3_pct=0.02,
    )

    bt = LiveLogicBacktester(config)
    bt.run()

    initial = config.initial_capital
    final = bt.equity_curve[-1]["equity"] if bt.equity_curve else 0
    total_return = (final - initial) / initial * 100

    equity_series = pd.Series([e["equity"] for e in bt.equity_curve])
    mdd = ((equity_series - equity_series.cummax()) / equity_series.cummax() * 100).min()

    if bt.daily_returns:
        avg_r = np.mean(bt.daily_returns)
        std_r = np.std(bt.daily_returns)
        sharpe = (avg_r * 252) / (std_r * np.sqrt(252)) if std_r > 0 else 0
    else:
        sharpe = 0

    wins = len([t for t in bt.trades if t.pnl > 0])
    win_rate = wins / len(bt.trades) * 100 if bt.trades else 0

    return {
        'final': final,
        'return': total_return,
        'mdd': mdd,
        'sharpe': sharpe,
        'trades': len(bt.trades),
        'wins': wins,
        'win_rate': win_rate,
    }


def print_comparison(name_a, result_a, name_b, result_b, initial=100_000_000):
    """두 전략 비교 출력"""
    print("\n" + "=" * 75)
    print("  Strategy Comparison")
    print("=" * 75)
    print(f"{'Metric':<20} {name_a:>20} {name_b:>20} {'Diff':>12}")
    print("-" * 75)

    metrics = [
        ("Total return", "return", "%", ".2f"),
        ("Final capital", "final", "", ",.0f"),
        ("MDD", "mdd", "%", ".2f"),
        ("Sharpe ratio", "sharpe", "", ".2f"),
        ("Total trades", "trades", "", "d"),
        ("Win rate", "win_rate", "%", ".1f"),
    ]

    for label, key, suffix, fmt in metrics:
        a = result_a.get(key, 0)
        b = result_b.get(key, 0)
        diff = b - a

        a_str = f"{a:{fmt}}{suffix}"
        b_str = f"{b:{fmt}}{suffix}"

        if key in ("trades",):
            diff_str = f"{diff:+d}"
        elif suffix == "%":
            diff_str = f"{diff:+.2f}%"
        else:
            diff_str = f"{diff:+{fmt}}"

        print(f"{label:<20} {a_str:>20} {b_str:>20} {diff_str:>12}")

    print("=" * 75)


def main():
    """메인 함수"""
    import argparse

    parser = argparse.ArgumentParser(description="Supply Momentum Backtest")
    parser.add_argument("--compare", action="store_true",
                        help="Compare with theme baseline strategy")
    parser.add_argument("--test-days", action="store_true",
                        help="Test consecutive days (3/5/7)")
    parser.add_argument("--test-combo", action="store_true",
                        help="Test supply + theme combo strategy")
    parser.add_argument("--relaxed", action="store_true",
                        help="Use relaxed filters (no MA alignment, lower MFI)")
    args = parser.parse_args()

    if args.compare:
        # ============================================================
        # 기존 테마 전략 vs 수급 모멘텀 비교
        # ============================================================
        print("\n" + "=" * 75)
        print("  Theme Momentum vs Supply Momentum Comparison")
        print("=" * 75)

        # 1. 기존 테마 전략
        print("\n[1/2] Running theme momentum baseline...")
        theme_result = run_theme_baseline()

        # 2. 수급 모멘텀 전략
        print("\n[2/2] Running supply momentum strategy...")
        config = SupplyMomentumConfig()
        bt = SupplyMomentumBacktester(config)
        bt.run()
        supply_result = bt.get_summary_dict()

        # 비교
        print_comparison("Theme Momentum", theme_result, "Supply Momentum", supply_result)

        # 판정
        if supply_result.get('return', 0) > theme_result.get('return', 0):
            diff = supply_result['return'] - theme_result['return']
            print(f"\n  Supply Momentum wins by +{diff:.2f}%!")
        else:
            diff = theme_result['return'] - supply_result['return']
            print(f"\n  Theme Momentum wins by +{diff:.2f}%")
            print("  Consider combining both strategies (--test-combo)")

    elif args.test_days:
        # ============================================================
        # 연속일수 비교: 3일 vs 5일 vs 7일
        # ============================================================
        print("\n" + "=" * 75)
        print("  Consecutive Days Test: 3 vs 5 vs 7")
        print("=" * 75)

        days_list = [3, 5, 7]
        results = {}

        for i, days in enumerate(days_list, 1):
            print(f"\n[{i}/{len(days_list)}] Consecutive days = {days}")
            config = SupplyMomentumConfig(consecutive_days=days)
            bt = SupplyMomentumBacktester(config)
            bt.run()
            results[days] = bt.get_summary_dict()

        # 비교 테이블
        print("\n" + "=" * 80)
        print("  Consecutive Days Comparison")
        print("=" * 80)
        print(f"{'Metric':<20} {'3 days':>18} {'5 days':>18} {'7 days':>18}")
        print("-" * 80)

        for label, key, suffix, fmt in [
            ("Total return", "return", "%", ".2f"),
            ("MDD", "mdd", "%", ".2f"),
            ("Sharpe", "sharpe", "", ".2f"),
            ("Trades", "trades", "", "d"),
            ("Win rate", "win_rate", "%", ".1f"),
        ]:
            vals = []
            for d in days_list:
                v = results[d].get(key, 0)
                vals.append(f"{v:{fmt}}{suffix}")
            print(f"{label:<20} {vals[0]:>18} {vals[1]:>18} {vals[2]:>18}")
        print("=" * 80)

        best = max(days_list, key=lambda d: results[d].get('return', 0))
        print(f"\n  Best: {best} consecutive days ({results[best]['return']:.2f}%)")

    elif args.test_combo:
        # ============================================================
        # 수급 + 테마 결합 전략
        # ============================================================
        print("\n" + "=" * 75)
        print("  Supply + Theme Combo Strategy Test")
        print("=" * 75)
        print("  Filter: Only enter when supply signal AND theme is top 3")

        # 수급 단독
        print("\n[1/3] Supply-only strategy...")
        config_supply = SupplyMomentumConfig()
        bt_supply = SupplyMomentumBacktester(config_supply)
        bt_supply.run()
        supply_result = bt_supply.get_summary_dict()

        # 수급 (완화 조건)
        print("\n[2/3] Supply relaxed strategy...")
        config_relaxed = SupplyMomentumConfig(
            consecutive_days=3,
            min_volume_ratio=1.0,
            min_mfi=40,
            require_ma_alignment=False,
        )
        bt_relaxed = SupplyMomentumBacktester(config_relaxed)
        bt_relaxed.run()
        relaxed_result = bt_relaxed.get_summary_dict()

        # 테마 기준선
        print("\n[3/3] Theme baseline...")
        theme_result = run_theme_baseline()

        # 3자 비교
        print("\n" + "=" * 80)
        print("  3-Way Comparison")
        print("=" * 80)
        print(f"{'Metric':<17} {'Theme':>18} {'Supply(5d)':>18} {'Supply(3d,relax)':>18}")
        print("-" * 80)

        for label, key, suffix, fmt in [
            ("Total return", "return", "%", ".2f"),
            ("MDD", "mdd", "%", ".2f"),
            ("Sharpe", "sharpe", "", ".2f"),
            ("Trades", "trades", "", "d"),
            ("Win rate", "win_rate", "%", ".1f"),
        ]:
            vals = []
            for r in [theme_result, supply_result, relaxed_result]:
                v = r.get(key, 0)
                vals.append(f"{v:{fmt}}{suffix}")
            print(f"{label:<17} {vals[0]:>18} {vals[1]:>18} {vals[2]:>18}")
        print("=" * 80)

    else:
        # ============================================================
        # 단일 실행
        # ============================================================
        if args.relaxed:
            print("  Supply Momentum (Relaxed filters)")
            config = SupplyMomentumConfig(
                consecutive_days=3,
                min_volume_ratio=1.0,
                min_mfi=40,
                require_ma_alignment=False,
            )
        else:
            print("  Supply Momentum Strategy (Default)")
            config = SupplyMomentumConfig()

        bt = SupplyMomentumBacktester(config)
        bt.run()


if __name__ == "__main__":
    main()
