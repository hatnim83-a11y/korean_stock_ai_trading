"""
backtest_live_logic.py - 실전 로직 적용 백테스트

실제 매매 시스템의 로직을 최대한 적용한 백테스트

적용 로직:
1. 테마 모멘텀 점수 (주가 기반)
2. 기술적 필터 (RSI, MA, 거래량)
3. 점수 기반 가중치 배분
4. 하이브리드 전략 (분할익절, 트레일링스탑, 보유기간)

제외 로직 (과거 데이터 없음):
- 뉴스 화제성
- AI 감성 분석
- 실시간 수급 데이터

사용법:
    python scripts/backtest_live_logic.py
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


# ===== 테마 및 종목 정의 =====
# 실제 테마별 대표 종목 (과거~현재 존재하는 종목)
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
    take_profit: float

    # 분할 익절 상태
    partial_sold: bool = False
    partial_sold_date: Optional[datetime] = None
    partial_sold_price: float = 0.0
    remaining_shares: int = 0

    # 트레일링 스탑
    highest_price: float = 0.0
    trailing_stop: float = 0.0

    def __post_init__(self):
        self.remaining_shares = self.shares
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


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    start_date: str = "2023-01-01"
    end_date: str = "2026-01-31"
    initial_capital: float = 100_000_000  # 1억

    # 테마 선정
    top_themes: int = 3  # 상위 N개 테마
    theme_rotation_days: int = 14  # 테마 로테이션 주기

    # 종목 선정
    max_positions: int = 10  # 최대 종목 수
    stocks_per_theme: int = 3  # 테마당 종목 수

    # 기술적 필터
    min_rsi: float = 30
    max_rsi: float = 70
    min_volume_ratio: float = 0.5  # 평균 대비

    # 매매 전략
    stop_loss_pct: float = -0.07  # -7%
    take_profit_pct: float = 0.15  # +15%
    partial_profit_pct: float = 0.10  # +10%에서 절반 익절
    partial_sell_ratio: float = 0.5  # 50% 매도
    trailing_stop_pct: float = 0.05  # 고점 대비 5% 하락
    max_holding_days: int = 10  # 최대 보유일

    # 기타
    commission: float = 0.00015  # 수수료 0.015%
    slippage: float = 0.001  # 슬리피지 0.1%


class LiveLogicBacktester:
    """실전 로직 백테스터"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.capital = config.initial_capital
        self.cash = config.initial_capital

        # 데이터 캐시
        self.price_data: Dict[str, pd.DataFrame] = {}
        self.theme_scores: Dict[str, float] = {}

        # 기록
        self.equity_curve = []
        self.daily_returns = []

    def load_data(self):
        """주가 데이터 로드"""
        logger.info("📥 주가 데이터 로드 중...")

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

                if not df.empty and len(df) > 20:
                    # MultiIndex 컬럼 평탄화
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]

                    # 기술적 지표 계산
                    df = self._calculate_indicators(df)
                    self.price_data[symbol] = df
                    logger.debug(f"  {symbol}: {len(df)}일 로드")

            except Exception as e:
                logger.warning(f"  {symbol} 로드 실패: {e}")

        logger.info(f"✅ {len(self.price_data)}개 종목 데이터 로드 완료")

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 계산"""
        # MA
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # ATR
        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # 거래량 비율
        df['Volume_Ratio'] = df['Volume'] / df['Volume'].rolling(20).mean()

        # 수익률
        df['Return_5D'] = df['Close'].pct_change(5) * 100
        df['Return_20D'] = df['Close'].pct_change(20) * 100

        return df

    def calculate_theme_scores(self, date: datetime) -> Dict[str, float]:
        """테마별 점수 계산 (모멘텀 기반)"""
        scores = {}

        for theme, info in THEME_STOCKS.items():
            theme_returns = []
            valid_stocks = 0

            for symbol in info["stocks"]:
                if symbol not in self.price_data:
                    continue

                df = self.price_data[symbol]

                # 해당 날짜까지의 데이터만
                df_until = df[df.index <= pd.Timestamp(date)]
                if len(df_until) < 20:
                    continue

                # 5일 수익률
                ret_5d_val = df_until['Return_5D'].iloc[-1]
                ret_5d = float(ret_5d_val) if not pd.isna(ret_5d_val) else 0
                theme_returns.append(ret_5d)
                valid_stocks += 1

            if valid_stocks >= 2:
                avg_return = np.mean(theme_returns)

                # 모멘텀 점수 (30점 만점, -15%~+15% → 0~30)
                momentum_score = ((avg_return + 15) / 30) * 30
                momentum_score = max(0, min(30, momentum_score))

                # 종목수 보너스 (최대 10점)
                size_bonus = min(10, valid_stocks * 2)

                # 기본 점수 (뉴스/AI 대신)
                base_score = 25

                total_score = momentum_score + size_bonus + base_score
                scores[theme] = total_score

        return scores

    def select_stocks(self, theme: str, date: datetime) -> List[dict]:
        """테마 내 종목 선정 (기술적 필터 적용)"""
        candidates = []
        info = THEME_STOCKS.get(theme, {})

        for i, symbol in enumerate(info.get("stocks", [])):
            if symbol not in self.price_data:
                continue

            df = self.price_data[symbol]
            df_until = df[df.index <= pd.Timestamp(date)]

            if len(df_until) < 20:
                continue

            latest = df_until.iloc[-1]

            # 필터 조건 (스칼라 값으로 변환)
            rsi = float(latest['RSI']) if not pd.isna(latest['RSI']) else 50
            volume_ratio = float(latest['Volume_Ratio']) if not pd.isna(latest['Volume_Ratio']) else 1
            ma5 = float(latest['MA5']) if not pd.isna(latest['MA5']) else 0
            ma20 = float(latest['MA20']) if not pd.isna(latest['MA20']) else 0
            price = float(latest['Close'])

            # RSI 필터
            if rsi < self.config.min_rsi or rsi > self.config.max_rsi:
                continue

            # 거래량 필터
            if volume_ratio < self.config.min_volume_ratio:
                continue

            # 점수 계산
            score = 50  # 기본

            # RSI 점수 (40~60이 이상적)
            if 40 <= rsi <= 60:
                score += 15
            elif 35 <= rsi <= 65:
                score += 10

            # MA 정배열
            if ma5 > ma20 > 0:
                score += 15

            # 거래량 활발
            if volume_ratio > 1.5:
                score += 10
            elif volume_ratio > 1.0:
                score += 5

            # 5일 모멘텀
            ret_5d = float(latest['Return_5D']) if not pd.isna(latest['Return_5D']) else 0
            if ret_5d > 3:
                score += 10
            elif ret_5d > 0:
                score += 5

            candidates.append({
                "code": symbol,
                "name": info["names"][i] if i < len(info["names"]) else symbol,
                "theme": theme,
                "price": price,
                "score": score,
                "rsi": rsi,
                "volume_ratio": volume_ratio,
            })

        # 점수순 정렬
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:self.config.stocks_per_theme]

    def calculate_weights(self, stocks: List[dict]) -> List[dict]:
        """점수 기반 가중치 계산"""
        if not stocks:
            return []

        total_score = sum(s["score"] for s in stocks)

        for stock in stocks:
            raw_weight = stock["score"] / total_score if total_score > 0 else 1/len(stocks)
            # 5%~25% 제한
            weight = max(0.05, min(0.25, raw_weight))
            stock["weight"] = weight

        # 정규화
        total_weight = sum(s["weight"] for s in stocks)
        for stock in stocks:
            stock["weight"] = stock["weight"] / total_weight

        return stocks

    def execute_entry(self, stocks: List[dict], date: datetime):
        """진입 실행"""
        investable = self.cash * 0.95  # 5% 현금 버퍼

        for stock in stocks:
            if len(self.positions) >= self.config.max_positions:
                break

            # 이미 보유 중인지 체크
            if any(p.code == stock["code"] for p in self.positions):
                continue

            amount = investable * stock["weight"]
            price = stock["price"] * (1 + self.config.slippage)  # 슬리피지
            shares = int(amount / price)

            if shares <= 0:
                continue

            # 수수료
            commission = price * shares * self.config.commission
            actual_amount = price * shares + commission

            if actual_amount > self.cash:
                continue

            # 손절/익절 가격
            stop_loss = price * (1 + self.config.stop_loss_pct)
            take_profit = price * (1 + self.config.take_profit_pct)

            position = Position(
                code=stock["code"],
                name=stock["name"],
                theme=stock["theme"],
                entry_date=date,
                entry_price=price,
                shares=shares,
                weight=stock["weight"],
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            self.positions.append(position)
            self.cash -= actual_amount

            logger.debug(f"  📈 매수: {stock['name']} {shares}주 @ {price:,.0f}")

    def check_exits(self, date: datetime):
        """청산 조건 체크"""
        positions_to_close = []

        for pos in self.positions:
            if pos.code not in self.price_data:
                continue

            df = self.price_data[pos.code]
            df_until = df[df.index <= pd.Timestamp(date)]

            if df_until.empty:
                continue

            current_price = df_until.iloc[-1]['Close']
            holding_days = (date - pos.entry_date).days

            # 고점 갱신
            if current_price > pos.highest_price:
                pos.highest_price = current_price
                pos.trailing_stop = current_price * (1 - self.config.trailing_stop_pct)

            exit_reason = None
            exit_price = current_price

            # 1. 손절 (-7%)
            if current_price <= pos.stop_loss:
                exit_reason = "손절"
                exit_price = pos.stop_loss

            # 2. 익절 (+15%)
            elif current_price >= pos.take_profit:
                exit_reason = "익절"
                exit_price = pos.take_profit

            # 3. 분할 익절 (+10%에서 절반)
            elif not pos.partial_sold and current_price >= pos.entry_price * (1 + self.config.partial_profit_pct):
                # 절반 매도
                sell_shares = int(pos.remaining_shares * self.config.partial_sell_ratio)
                if sell_shares > 0:
                    pos.partial_sold = True
                    pos.partial_sold_date = date
                    pos.partial_sold_price = current_price
                    pos.remaining_shares -= sell_shares

                    # 수익 실현
                    pnl = (current_price - pos.entry_price) * sell_shares
                    commission = current_price * sell_shares * self.config.commission
                    self.cash += current_price * sell_shares - commission

                    # 손절가 조정 (본전으로)
                    pos.stop_loss = pos.entry_price

                    logger.debug(f"  📊 분할익절: {pos.name} {sell_shares}주 @ {current_price:,.0f}")

            # 4. 트레일링 스탑 (분할 익절 후)
            elif pos.partial_sold and current_price <= pos.trailing_stop:
                exit_reason = "트레일링스탑"

            # 5. 보유기간 초과
            elif holding_days >= self.config.max_holding_days:
                exit_reason = "보유기간만료"

            if exit_reason:
                positions_to_close.append((pos, exit_price, exit_reason, holding_days))

        # 청산 실행
        for pos, exit_price, reason, days in positions_to_close:
            sell_shares = pos.remaining_shares
            pnl = (exit_price - pos.entry_price) * sell_shares

            # 분할 익절 수익 포함
            if pos.partial_sold:
                partial_pnl = (pos.partial_sold_price - pos.entry_price) * int(pos.shares * self.config.partial_sell_ratio)
                pnl += partial_pnl

            pnl_pct = pnl / (pos.entry_price * pos.shares) * 100

            # 수수료
            commission = exit_price * sell_shares * self.config.commission
            self.cash += exit_price * sell_shares - commission

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
            )
            self.trades.append(trade)
            self.positions.remove(pos)

            logger.debug(f"  📉 청산: {pos.name} @ {exit_price:,.0f} ({reason}, {pnl_pct:+.1f}%)")

    def calculate_equity(self, date: datetime) -> float:
        """현재 자산 가치 계산"""
        equity = self.cash

        for pos in self.positions:
            if pos.code not in self.price_data:
                continue

            df = self.price_data[pos.code]
            df_until = df[df.index <= pd.Timestamp(date)]

            if not df_until.empty:
                current_price = df_until.iloc[-1]['Close']
                equity += current_price * pos.remaining_shares

        return equity

    def run(self):
        """백테스트 실행"""
        logger.info("=" * 60)
        logger.info("🚀 실전 로직 백테스트 시작")
        logger.info("=" * 60)
        logger.info(f"기간: {self.config.start_date} ~ {self.config.end_date}")
        logger.info(f"초기 자본: {self.config.initial_capital:,}원")

        # 데이터 로드
        self.load_data()

        if not self.price_data:
            logger.error("데이터 로드 실패")
            return

        # 거래일 목록
        sample_df = list(self.price_data.values())[0]
        trading_days = sample_df.index.tolist()

        logger.info(f"거래일: {len(trading_days)}일")
        logger.info("-" * 60)

        last_rotation = None
        current_themes = []

        for i, date in enumerate(trading_days):
            date_dt = date.to_pydatetime()

            # 테마 로테이션 (2주마다)
            if last_rotation is None or (date_dt - last_rotation).days >= self.config.theme_rotation_days:
                # 테마 점수 계산
                self.theme_scores = self.calculate_theme_scores(date_dt)

                # 상위 테마 선정
                sorted_themes = sorted(self.theme_scores.items(), key=lambda x: x[1], reverse=True)
                current_themes = [t[0] for t in sorted_themes[:self.config.top_themes]]

                if current_themes:
                    logger.info(f"\n📊 [{date_dt.strftime('%Y-%m-%d')}] 테마 로테이션")
                    for t, s in sorted_themes[:self.config.top_themes]:
                        logger.info(f"   {t}: {s:.1f}점")

                last_rotation = date_dt

                # 기존 포지션 정리 (테마 변경 시)
                # 주석: 실전에서는 테마 변경 시 즉시 청산하지 않을 수 있음

            # 청산 조건 체크
            self.check_exits(date_dt)

            # 신규 진입 (포지션 여유 있을 때)
            if len(self.positions) < self.config.max_positions:
                all_candidates = []

                for theme in current_themes:
                    stocks = self.select_stocks(theme, date_dt)
                    all_candidates.extend(stocks)

                # 점수순 정렬 및 가중치 계산
                all_candidates.sort(key=lambda x: x["score"], reverse=True)
                all_candidates = self.calculate_weights(all_candidates[:self.config.max_positions])

                # 진입
                if all_candidates:
                    self.execute_entry(all_candidates, date_dt)

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

        # 최종 청산
        logger.info("\n📊 백테스트 종료 - 잔여 포지션 청산")
        final_date = trading_days[-1].to_pydatetime()
        for pos in list(self.positions):
            if pos.code in self.price_data:
                df = self.price_data[pos.code]
                exit_price = df.iloc[-1]['Close']
                pnl = (exit_price - pos.entry_price) * pos.remaining_shares
                pnl_pct = pnl / (pos.entry_price * pos.shares) * 100

                trade = Trade(
                    code=pos.code,
                    name=pos.name,
                    theme=pos.theme,
                    entry_date=pos.entry_date,
                    exit_date=final_date,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    shares=pos.shares,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    exit_reason="백테스트종료",
                    holding_days=(final_date - pos.entry_date).days,
                )
                self.trades.append(trade)
                self.cash += exit_price * pos.remaining_shares

        self.positions = []

        # 결과 출력
        self.print_results()

    def print_results(self):
        """결과 출력"""
        logger.info("\n" + "=" * 60)
        logger.info("📊 백테스트 결과")
        logger.info("=" * 60)

        if not self.equity_curve:
            logger.warning("거래 데이터 없음")
            return

        initial = self.config.initial_capital
        final = self.equity_curve[-1]["equity"]
        total_return = (final - initial) / initial * 100

        # CAGR
        start = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.config.end_date, "%Y-%m-%d")
        years = (end - start).days / 365
        cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

        # MDD
        equity_series = pd.Series([e["equity"] for e in self.equity_curve])
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max * 100
        mdd = drawdown.min()

        # 샤프 비율
        if self.daily_returns:
            avg_return = np.mean(self.daily_returns)
            std_return = np.std(self.daily_returns)
            sharpe = (avg_return * 252) / (std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0

        # 거래 통계
        if self.trades:
            wins = [t for t in self.trades if t.pnl > 0]
            losses = [t for t in self.trades if t.pnl <= 0]
            win_rate = len(wins) / len(self.trades) * 100
            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
            avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
            avg_holding = np.mean([t.holding_days for t in self.trades])
        else:
            win_rate = avg_win = avg_loss = avg_holding = 0

        print("\n" + "=" * 60)
        print("📈 수익률 지표")
        print("-" * 60)
        print(f"초기 자본:     {initial:>15,.0f}원")
        print(f"최종 자본:     {final:>15,.0f}원")
        print(f"총 수익률:     {total_return:>14.2f}%")
        print(f"연평균 수익률: {cagr:>14.2f}%")
        print(f"MDD:           {mdd:>14.2f}%")
        print(f"샤프 비율:     {sharpe:>14.2f}")

        print("\n" + "-" * 60)
        print("📊 거래 통계")
        print("-" * 60)
        print(f"총 거래 수:    {len(self.trades):>15}회")
        print(f"승률:          {win_rate:>14.1f}%")
        print(f"평균 수익:     {avg_win:>14.2f}%")
        print(f"평균 손실:     {avg_loss:>14.2f}%")
        print(f"평균 보유일:   {avg_holding:>14.1f}일")

        # 청산 사유별 통계
        print("\n" + "-" * 60)
        print("📋 청산 사유별 통계")
        print("-" * 60)

        reasons = {}
        for t in self.trades:
            if t.exit_reason not in reasons:
                reasons[t.exit_reason] = {"count": 0, "pnl": 0}
            reasons[t.exit_reason]["count"] += 1
            reasons[t.exit_reason]["pnl"] += t.pnl_pct

        for reason, data in sorted(reasons.items(), key=lambda x: x[1]["count"], reverse=True):
            avg_pnl = data["pnl"] / data["count"] if data["count"] > 0 else 0
            print(f"{reason:<15}: {data['count']:>5}회, 평균 {avg_pnl:+.2f}%")

        # 테마별 성과
        print("\n" + "-" * 60)
        print("🎯 테마별 성과")
        print("-" * 60)

        theme_stats = {}
        for t in self.trades:
            if t.theme not in theme_stats:
                theme_stats[t.theme] = {"count": 0, "pnl": 0}
            theme_stats[t.theme]["count"] += 1
            theme_stats[t.theme]["pnl"] += t.pnl_pct

        for theme, data in sorted(theme_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            avg_pnl = data["pnl"] / data["count"] if data["count"] > 0 else 0
            print(f"{theme:<12}: {data['count']:>5}회, 평균 {avg_pnl:+.2f}%")

        print("\n" + "=" * 60)

        # 자산 곡선 저장
        self.save_results()

    def save_results(self):
        """결과 저장"""
        # 자산 곡선
        equity_df = pd.DataFrame(self.equity_curve)
        equity_df.to_csv("data/backtest_equity_curve.csv", index=False)

        # 거래 내역
        trades_data = [
            {
                "code": t.code,
                "name": t.name,
                "theme": t.theme,
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d"),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "holding_days": t.holding_days,
            }
            for t in self.trades
        ]
        trades_df = pd.DataFrame(trades_data)
        trades_df.to_csv("data/backtest_trades.csv", index=False)

        logger.info(f"\n💾 결과 저장 완료")
        logger.info(f"   - data/backtest_equity_curve.csv")
        logger.info(f"   - data/backtest_trades.csv")


def main():
    """메인 함수"""
    config = BacktestConfig(
        start_date="2023-01-01",
        end_date="2026-01-31",
        initial_capital=100_000_000,  # 1억
        top_themes=3,
        theme_rotation_days=14,
        max_positions=10,
        stocks_per_theme=3,
        stop_loss_pct=-0.07,
        take_profit_pct=0.15,
        partial_profit_pct=0.10,
        trailing_stop_pct=0.05,
        max_holding_days=10,
    )

    backtester = LiveLogicBacktester(config)
    backtester.run()


if __name__ == "__main__":
    main()
