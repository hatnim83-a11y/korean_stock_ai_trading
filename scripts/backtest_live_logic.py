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
    trailing_active: bool = False  # 트레일링 활성화 여부
    trailing_level: int = 0  # 트레일링 레벨 (0=미활성, 1=5%, 2=3%, 3=2%)
    max_profit_pct: float = 0.0  # 최대 수익률 기록

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
    theme_rotation_days: int = 7  # 테마 로테이션 주기 (7일이 14일 대비 +75% 수익)

    # 종목 선정
    max_positions: int = 10  # 최대 종목 수
    stocks_per_theme: int = 3  # 테마당 종목 수

    # 기술적 필터
    min_rsi: float = 30
    max_rsi: float = 70
    min_volume_ratio: float = 0.5  # 평균 대비

    # 매매 전략
    stop_loss_pct: float = -0.07  # -7%
    max_holding_days: int = 14  # 최대 보유일 (늘림)

    # 이익 추종 전략 (Let Profits Run)
    enable_profit_trailing: bool = True  # 이익 추종 전략 활성화

    # 단계별 트레일링 스탑
    trail_activation_pct: float = 0.08  # +8%에서 트레일링 시작
    trail_level1_pct: float = 0.05  # 8%~15%: 고점 대비 5% 트레일링
    trail_level2_threshold: float = 0.15  # +15%에서 트레일링 강화
    trail_level2_pct: float = 0.03  # 15%~25%: 고점 대비 3% 트레일링
    trail_level3_threshold: float = 0.25  # +25%에서 트레일링 더 강화
    trail_level3_pct: float = 0.02  # 25%+: 고점 대비 2% 트레일링

    # 기존 전략 (비활성화 가능)
    enable_fixed_take_profit: bool = False  # 고정 익절 비활성화
    take_profit_pct: float = 0.15  # (비활성화됨)
    enable_partial_profit: bool = False  # 분할 익절 비활성화
    partial_profit_pct: float = 0.10
    partial_sell_ratio: float = 0.5
    # 분할 익절 후 트레일링 (기존 전략용)
    enable_trailing_after_partial: bool = False
    trailing_after_partial_pct: float = 0.05  # 고점 대비 5%

    # 터틀 트레이딩 옵션
    turtle_exit: bool = False  # 터틀 청산 (10일 저가 이탈 + 2xATR 손절)
    no_max_hold: bool = False  # 보유기간 제한 해제
    pure_turtle: bool = False  # 순수 터틀 (20일 돌파 진입 + ATR 사이징)
    atr_stop_multiplier: float = 2.0  # ATR 손절 배수
    turtle_risk_pct: float = 0.01  # 순수 터틀 1유닛 = 자본 1% / ATR

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

        # 돌파/이탈 기준 (터틀 트레이딩용)
        df['High_20'] = df['High'].rolling(20).max()
        df['Low_10'] = df['Low'].rolling(10).min()

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
            if self.config.turtle_exit:
                # 터틀 모드: ATR 기반 손절
                df = self.price_data.get(stock["code"])
                if df is not None:
                    df_until = df[df.index <= pd.Timestamp(date)]
                    atr_val = float(df_until.iloc[-1]['ATR']) if len(df_until) > 0 and not pd.isna(df_until.iloc[-1]['ATR']) else price * 0.03
                else:
                    atr_val = price * 0.03
                stop_loss = price - self.config.atr_stop_multiplier * atr_val
            else:
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

    def execute_pure_turtle_entry(self, date: datetime):
        """순수 터틀 진입: 20일 고가 돌파 종목 매수 (ATR 사이징)"""
        if len(self.positions) >= self.config.max_positions:
            return

        all_symbols = set()
        for info in THEME_STOCKS.values():
            all_symbols.update(info["stocks"])

        # 종목명 매핑
        name_map = {}
        theme_map = {}
        for theme, info in THEME_STOCKS.items():
            for i, sym in enumerate(info["stocks"]):
                if i < len(info["names"]):
                    name_map[sym] = info["names"][i]
                theme_map[sym] = theme

        candidates = []
        for symbol in sorted(all_symbols):
            if symbol not in self.price_data:
                continue
            if any(p.code == symbol for p in self.positions):
                continue

            df = self.price_data[symbol]
            df_until = df[df.index <= pd.Timestamp(date)]
            if len(df_until) < 21:
                continue

            latest = df_until.iloc[-1]
            prev = df_until.iloc[-2]

            close = float(latest['Close'])
            high_20_prev = float(prev['High_20']) if not pd.isna(prev['High_20']) else None
            atr = float(latest['ATR']) if not pd.isna(latest['ATR']) else None

            if high_20_prev is None or atr is None or atr <= 0:
                continue

            # 20일 고가 돌파: 오늘 종가 > 어제까지의 20일 최고가
            if close > high_20_prev:
                candidates.append({
                    "code": symbol,
                    "name": name_map.get(symbol, symbol),
                    "theme": theme_map.get(symbol, ""),
                    "price": close,
                    "atr": atr,
                })

        # ATR 기반 포지션 사이징으로 진입
        for stock in candidates:
            if len(self.positions) >= self.config.max_positions:
                break

            price = stock["price"] * (1 + self.config.slippage)
            atr = stock["atr"]

            # ATR 기반 사이징 (총 자산 기준, 최소 슬롯 배분 보장)
            total_equity = self.cash + sum(
                float(self.price_data[p.code][self.price_data[p.code].index <= pd.Timestamp(date)].iloc[-1]['Close']) * p.remaining_shares
                for p in self.positions if p.code in self.price_data and not self.price_data[p.code][self.price_data[p.code].index <= pd.Timestamp(date)].empty
            )
            slots = self.config.max_positions - len(self.positions)
            max_per_slot = total_equity / max(slots, 1) * 0.95
            unit_value = total_equity * self.config.turtle_risk_pct / atr
            # ATR 사이즈가 너무 작으면 슬롯 균등배분으로 보완
            invest_amount = min(max(unit_value, total_equity * 0.05), max_per_slot)
            shares = int(invest_amount / price)
            if shares <= 0:
                continue

            commission = price * shares * self.config.commission
            actual_amount = price * shares + commission
            if actual_amount > self.cash:
                continue

            # ATR 기반 손절
            stop_loss = price - self.config.atr_stop_multiplier * atr

            position = Position(
                code=stock["code"],
                name=stock["name"],
                theme=stock["theme"],
                entry_date=date,
                entry_price=price,
                shares=shares,
                weight=0,
                stop_loss=stop_loss,
                take_profit=price * 2,  # 사용 안함
            )
            self.positions.append(position)
            self.cash -= actual_amount
            logger.debug(f"  📈 터틀매수: {stock['name']} {shares}주 @ {price:,.0f} (ATR={atr:.0f})")

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

            current_price = float(df_until.iloc[-1]['Close'])
            holding_days = (date - pos.entry_date).days
            current_profit_pct = (current_price - pos.entry_price) / pos.entry_price

            # 최대 수익률/고점 갱신
            if current_price > pos.highest_price:
                pos.highest_price = current_price
                # 기존 전략: 분할 익절 후 트레일링 스탑 갱신
                if self.config.enable_trailing_after_partial:
                    pos.trailing_stop = current_price * (1 - self.config.trailing_after_partial_pct)
            if current_profit_pct > pos.max_profit_pct:
                pos.max_profit_pct = current_profit_pct

            exit_reason = None
            exit_price = current_price

            # ===== 터틀 청산 모드 =====
            if self.config.turtle_exit:
                low = float(df_until.iloc[-1]['Low'])
                # 10일 저가 이탈 청산 (전일 기준 Low_10 사용)
                if len(df_until) >= 2:
                    prev_low_10 = df_until.iloc[-2]['Low_10']
                    if not pd.isna(prev_low_10) and current_price <= float(prev_low_10):
                        exit_reason = "터틀10일이탈"
                        exit_price = float(prev_low_10)

                # 2xATR 손절
                if not exit_reason and low <= pos.stop_loss:
                    exit_reason = "ATR손절"
                    exit_price = pos.stop_loss

                # 보유기간 제한 (no_max_hold가 아닐 때만)
                if not exit_reason and not self.config.no_max_hold:
                    if holding_days >= self.config.max_holding_days:
                        exit_reason = "보유기간만료"

                if exit_reason:
                    positions_to_close.append((pos, exit_price, exit_reason, holding_days))
                continue  # 터틀 모드면 아래 기존 청산 로직 스킵

            # ===== 이익 추종 전략 (Let Profits Run) =====
            if self.config.enable_profit_trailing:
                # 트레일링 레벨 업데이트
                if current_profit_pct >= self.config.trail_level3_threshold:
                    # +25% 이상: 레벨 3 (2% 트레일링)
                    if pos.trailing_level < 3:
                        pos.trailing_level = 3
                        pos.trailing_active = True
                        logger.debug(f"  🔥 {pos.name} 레벨3 트레일링 (2%)")
                elif current_profit_pct >= self.config.trail_level2_threshold:
                    # +15% 이상: 레벨 2 (3% 트레일링)
                    if pos.trailing_level < 2:
                        pos.trailing_level = 2
                        pos.trailing_active = True
                        logger.debug(f"  🔥 {pos.name} 레벨2 트레일링 (3%)")
                elif current_profit_pct >= self.config.trail_activation_pct:
                    # +8% 이상: 레벨 1 (5% 트레일링) + 본전 손절
                    if pos.trailing_level < 1:
                        pos.trailing_level = 1
                        pos.trailing_active = True
                        pos.stop_loss = pos.entry_price  # 본전 손절로 이동
                        logger.debug(f"  📈 {pos.name} 트레일링 활성화 (5%), 본전 손절")

                # 트레일링 스탑 가격 계산 (레벨별)
                if pos.trailing_active:
                    if pos.trailing_level == 3:
                        trail_pct = self.config.trail_level3_pct  # 2%
                    elif pos.trailing_level == 2:
                        trail_pct = self.config.trail_level2_pct  # 3%
                    else:
                        trail_pct = self.config.trail_level1_pct  # 5%

                    new_trailing_stop = pos.highest_price * (1 - trail_pct)
                    # 트레일링 스탑은 올라가기만 함 (내려가지 않음)
                    if new_trailing_stop > pos.trailing_stop:
                        pos.trailing_stop = new_trailing_stop

                    # 트레일링 스탑보다 손절가가 낮으면 손절가 상향
                    if pos.trailing_stop > pos.stop_loss:
                        pos.stop_loss = pos.trailing_stop

            # ===== 청산 조건 체크 =====

            # 1. 손절 (기본 -7% 또는 트레일링 스탑)
            if current_price <= pos.stop_loss:
                if pos.trailing_active:
                    exit_reason = f"트레일링L{pos.trailing_level}"
                    exit_price = pos.trailing_stop  # 트레일링 스탑 가격
                else:
                    exit_reason = "손절"
                    exit_price = pos.stop_loss  # 손절 가격 (원본과 동일)

            # 2. 고정 익절 (비활성화 가능)
            elif self.config.enable_fixed_take_profit and current_price >= pos.take_profit:
                exit_reason = "익절"
                exit_price = pos.take_profit

            # 3. 분할 익절 (비활성화 가능)
            elif self.config.enable_partial_profit and not pos.partial_sold:
                if current_price >= pos.entry_price * (1 + self.config.partial_profit_pct):
                    sell_shares = int(pos.remaining_shares * self.config.partial_sell_ratio)
                    if sell_shares > 0:
                        pos.partial_sold = True
                        pos.partial_sold_date = date
                        pos.partial_sold_price = current_price
                        pos.remaining_shares -= sell_shares

                        commission = current_price * sell_shares * self.config.commission
                        self.cash += current_price * sell_shares - commission

                        # 분할 익절 후 트레일링 활성화 (기존 전략)
                        if self.config.enable_trailing_after_partial:
                            pos.trailing_active = True
                            pos.stop_loss = pos.entry_price  # 본전 손절

                        logger.debug(f"  📊 분할익절: {pos.name} {sell_shares}주 @ {current_price:,.0f}")

            # 3-1. 분할 익절 후 트레일링 스탑 (기존 전략)
            if self.config.enable_trailing_after_partial and pos.partial_sold and not exit_reason:
                # 트레일링 스탑 도달 시 청산
                if current_price <= pos.trailing_stop:
                    exit_reason = "트레일링스탑"
                    exit_price = current_price

            # 4. 보유기간 초과
            if not exit_reason and holding_days >= self.config.max_holding_days:
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

            # 청산 조건 체크 (시장 상황과 무관하게 항상 실행)
            self.check_exits(date_dt)

            # 신규 진입
            if self.config.pure_turtle:
                # 순수 터틀: 20일 고가 돌파 진입
                self.execute_pure_turtle_entry(date_dt)
            elif len(self.positions) < self.config.max_positions:
                # 테마 기반 진입
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
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        print(f"평균 수익:     {avg_win:>14.2f}%")
        print(f"평균 손실:     {avg_loss:>14.2f}%")
        print(f"손익비:        {profit_loss_ratio:>14.2f}")
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
    import argparse

    parser = argparse.ArgumentParser(description="실전 로직 백테스트")
    parser.add_argument("--old-strategy", action="store_true", help="기존 전략 (고정 익절)")
    parser.add_argument("--compare", action="store_true", help="기존 vs 이익추종 비교 실행")
    parser.add_argument("--test-stoploss", action="store_true", help="손절률 비교 테스트 (-7%% vs -5%%)")
    parser.add_argument("--test-rotation", action="store_true", help="테마 로테이션 주기 테스트 (14일 vs 7일)")
    parser.add_argument("--test-holding", action="store_true", help="보유기간 비교 테스트 (14일/20일/25일/30일)")
    parser.add_argument("--test-themes", action="store_true", help="테마 수 비교 테스트 (2/3/4/5개)")
    parser.add_argument("--test-stocks", action="store_true", help="테마당 종목 수 비교 테스트 (2/3/4/5개)")
    parser.add_argument("--compare-turtle", action="store_true", help="4전략 비교: 기존 최적화 vs 터틀청산 vs 터틀무제한 vs 순수터틀")
    parser.add_argument("--turtle-exit", action="store_true", help="터틀 청산 단독 실행")
    parser.add_argument("--pure-turtle", action="store_true", help="순수 터틀 단독 실행")
    parser.add_argument("--no-max-hold", action="store_true", help="보유기간 제한 해제")
    args = parser.parse_args()

    # ============================================================
    # 기본 설정 (원본 - 수정 금지)
    # ============================================================
    # 이 설정이 백테스트의 기준선입니다.
    # 새로운 테스트는 이 값을 기준으로 비교합니다.
    # ============================================================
    base_config = {
        "start_date": "2023-01-01",
        "end_date": "2026-01-31",
        "initial_capital": 100_000_000,  # 1억원
        "top_themes": 3,                  # 상위 3개 테마
        "theme_rotation_days": 7,         # 테마 로테이션 1주 (14일 대비 +75% 수익)
        "max_positions": 10,              # 최대 10종목
        "stocks_per_theme": 3,            # 테마당 3종목
        "stop_loss_pct": -0.07,           # 손절 -7% (원본)
    }

    # ============================================================
    # 이익 추종 전략 기본 설정 (최적화 완료)
    # 백테스트 결과: +261.66%, CAGR 51.70%, MDD -9.88%
    # ============================================================
    profit_trailing_config = {
        "enable_profit_trailing": True,
        "enable_fixed_take_profit": False,
        "enable_partial_profit": False,
        "max_holding_days": 14,
        "trail_activation_pct": 0.08,    # +8%에서 트레일링 시작
        "trail_level1_pct": 0.05,        # L1: 고점 -5%
        "trail_level2_threshold": 0.15,  # +15%에서 L2
        "trail_level2_pct": 0.03,        # L2: 고점 -3%
        "trail_level3_threshold": 0.25,  # +25%에서 L3
        "trail_level3_pct": 0.02,        # L3: 고점 -2%
    }

    if args.compare:
        # 비교 모드: 기존 전략 vs 이익추종 전략
        print("\n" + "=" * 70)
        print("🔬 기존 전략 vs 이익 추종 전략 비교")
        print("=" * 70)

        results = {}

        # 1. 기존 전략 (고정 익절 +15%, 분할 익절 +10%, 분할 후 트레일링)
        print("\n" + "=" * 60)
        print("📊 [1/2] 기존 전략 (분할익절 + 트레일링)")
        print("=" * 60)
        config_old = BacktestConfig(
            **base_config,
            enable_profit_trailing=False,
            enable_fixed_take_profit=True,
            take_profit_pct=0.15,
            enable_partial_profit=True,
            partial_profit_pct=0.10,
            partial_sell_ratio=0.5,
            enable_trailing_after_partial=True,  # 분할 익절 후 트레일링
            trailing_after_partial_pct=0.05,
            max_holding_days=10,
        )
        bt_old = LiveLogicBacktester(config_old)
        bt_old.run()

        # MDD 계산
        equity_old = pd.Series([e["equity"] for e in bt_old.equity_curve])
        mdd_old = ((equity_old - equity_old.cummax()) / equity_old.cummax() * 100).min()

        results['old'] = {
            'final': bt_old.equity_curve[-1]["equity"] if bt_old.equity_curve else 0,
            'trades': len(bt_old.trades),
            'mdd': mdd_old,
            'wins': len([t for t in bt_old.trades if t.pnl > 0]),
        }

        # 2. 이익 추종 전략 (단계별 트레일링)
        print("\n" + "=" * 60)
        print("📊 [2/2] 이익 추종 전략 (단계별 트레일링)")
        print("=" * 60)
        config_new = BacktestConfig(
            **base_config,
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
        bt_new = LiveLogicBacktester(config_new)
        bt_new.run()

        # MDD 계산
        equity_new = pd.Series([e["equity"] for e in bt_new.equity_curve])
        mdd_new = ((equity_new - equity_new.cummax()) / equity_new.cummax() * 100).min()

        results['new'] = {
            'final': bt_new.equity_curve[-1]["equity"] if bt_new.equity_curve else 0,
            'trades': len(bt_new.trades),
            'mdd': mdd_new,
            'wins': len([t for t in bt_new.trades if t.pnl > 0]),
        }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 70)
        print("📈 비교 결과 요약")
        print("=" * 70)
        print(f"{'구분':<20} {'기존 전략':>15} {'이익 추종':>15} {'차이':>15}")
        print("-" * 70)

        ret_old = (results['old']['final'] - initial) / initial * 100
        ret_new = (results['new']['final'] - initial) / initial * 100
        print(f"{'총 수익률':.<20} {ret_old:>14.2f}% {ret_new:>14.2f}% {ret_new - ret_old:>+14.2f}%")
        print(f"{'최종 자산':.<20} {results['old']['final']:>14,.0f} {results['new']['final']:>14,.0f} {results['new']['final'] - results['old']['final']:>+14,.0f}")
        print(f"{'MDD':.<20} {results['old']['mdd']:>14.2f}% {results['new']['mdd']:>14.2f}% {results['new']['mdd'] - results['old']['mdd']:>+14.2f}%")
        print(f"{'총 거래 수':.<20} {results['old']['trades']:>15} {results['new']['trades']:>15} {results['new']['trades'] - results['old']['trades']:>+15}")
        print(f"{'승리 거래':.<20} {results['old']['wins']:>15} {results['new']['wins']:>15} {results['new']['wins'] - results['old']['wins']:>+15}")

        win_rate_old = results['old']['wins'] / results['old']['trades'] * 100 if results['old']['trades'] > 0 else 0
        win_rate_new = results['new']['wins'] / results['new']['trades'] * 100 if results['new']['trades'] > 0 else 0
        print(f"{'승률':.<20} {win_rate_old:>14.1f}% {win_rate_new:>14.1f}% {win_rate_new - win_rate_old:>+14.1f}%")
        print("=" * 70)

    # ============================================================
    # 손절률 비교 테스트: -7% vs -5%
    # ============================================================
    elif args.test_stoploss:
        print("\n" + "=" * 70)
        print("🔬 손절률 비교 테스트: -7% vs -5%")
        print("=" * 70)
        print("기준: 이익 추종 전략 (Let Profits Run)")
        print("=" * 70)

        results = {}

        # base_config에서 stop_loss_pct 제외한 설정 생성
        base_without_stoploss = {k: v for k, v in base_config.items() if k != 'stop_loss_pct'}

        # 1. 원본: 손절 -7%
        print("\n" + "=" * 60)
        print("📊 [1/2] 손절 -7% (원본)")
        print("=" * 60)
        config_7pct = BacktestConfig(
            **base_without_stoploss,
            **profit_trailing_config,
            stop_loss_pct=-0.07,  # 원본: -7%
        )
        bt_7pct = LiveLogicBacktester(config_7pct)
        bt_7pct.run()

        equity_7pct = pd.Series([e["equity"] for e in bt_7pct.equity_curve])
        mdd_7pct = ((equity_7pct - equity_7pct.cummax()) / equity_7pct.cummax() * 100).min()
        avg_loss_7pct = np.mean([t.pnl_pct for t in bt_7pct.trades if t.pnl < 0]) if bt_7pct.trades else 0

        results['7pct'] = {
            'final': bt_7pct.equity_curve[-1]["equity"] if bt_7pct.equity_curve else 0,
            'trades': len(bt_7pct.trades),
            'mdd': mdd_7pct,
            'wins': len([t for t in bt_7pct.trades if t.pnl > 0]),
            'losses': len([t for t in bt_7pct.trades if t.pnl <= 0]),
            'avg_loss': avg_loss_7pct,
        }

        # 2. 테스트: 손절 -5%
        print("\n" + "=" * 60)
        print("📊 [2/2] 손절 -5% (테스트)")
        print("=" * 60)
        config_5pct = BacktestConfig(
            **base_without_stoploss,
            **profit_trailing_config,
            stop_loss_pct=-0.05,  # 테스트: -5%
        )
        bt_5pct = LiveLogicBacktester(config_5pct)
        bt_5pct.run()

        equity_5pct = pd.Series([e["equity"] for e in bt_5pct.equity_curve])
        mdd_5pct = ((equity_5pct - equity_5pct.cummax()) / equity_5pct.cummax() * 100).min()
        avg_loss_5pct = np.mean([t.pnl_pct for t in bt_5pct.trades if t.pnl < 0]) if bt_5pct.trades else 0

        results['5pct'] = {
            'final': bt_5pct.equity_curve[-1]["equity"] if bt_5pct.equity_curve else 0,
            'trades': len(bt_5pct.trades),
            'mdd': mdd_5pct,
            'wins': len([t for t in bt_5pct.trades if t.pnl > 0]),
            'losses': len([t for t in bt_5pct.trades if t.pnl <= 0]),
            'avg_loss': avg_loss_5pct,
        }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 70)
        print("📈 손절률 비교 결과")
        print("=" * 70)
        print(f"{'구분':<20} {'손절 -7%':>15} {'손절 -5%':>15} {'차이':>15}")
        print("-" * 70)

        ret_7pct = (results['7pct']['final'] - initial) / initial * 100
        ret_5pct = (results['5pct']['final'] - initial) / initial * 100
        print(f"{'총 수익률':.<20} {ret_7pct:>14.2f}% {ret_5pct:>14.2f}% {ret_5pct - ret_7pct:>+14.2f}%")
        print(f"{'최종 자산':.<20} {results['7pct']['final']:>14,.0f} {results['5pct']['final']:>14,.0f} {results['5pct']['final'] - results['7pct']['final']:>+14,.0f}")
        print(f"{'MDD':.<20} {results['7pct']['mdd']:>14.2f}% {results['5pct']['mdd']:>14.2f}% {results['5pct']['mdd'] - results['7pct']['mdd']:>+14.2f}%")
        print(f"{'총 거래':.<20} {results['7pct']['trades']:>15} {results['5pct']['trades']:>15} {results['5pct']['trades'] - results['7pct']['trades']:>+15}")
        print(f"{'손절 횟수':.<20} {results['7pct']['losses']:>15} {results['5pct']['losses']:>15} {results['5pct']['losses'] - results['7pct']['losses']:>+15}")
        print(f"{'평균 손실':.<20} {results['7pct']['avg_loss']:>14.2f}% {results['5pct']['avg_loss']:>14.2f}% {results['5pct']['avg_loss'] - results['7pct']['avg_loss']:>+14.2f}%")

        win_rate_7 = results['7pct']['wins'] / results['7pct']['trades'] * 100 if results['7pct']['trades'] > 0 else 0
        win_rate_5 = results['5pct']['wins'] / results['5pct']['trades'] * 100 if results['5pct']['trades'] > 0 else 0
        print(f"{'승률':.<20} {win_rate_7:>14.1f}% {win_rate_5:>14.1f}% {win_rate_5 - win_rate_7:>+14.1f}%")
        print("=" * 70)

        # 결론
        if ret_5pct > ret_7pct:
            print("\n✅ 결론: 손절 -5%가 더 좋은 성과!")
        else:
            print("\n❌ 결론: 손절 -7% 유지 권장")

    # ============================================================
    # 테마 로테이션 주기 테스트: 14일 vs 7일
    # ============================================================
    elif args.test_rotation:
        print("\n" + "=" * 70)
        print("🔬 테마 로테이션 주기 테스트: 14일 vs 7일")
        print("=" * 70)
        print("기준: 이익 추종 전략 (Let Profits Run)")
        print("=" * 70)

        results = {}

        # base_config에서 theme_rotation_days 제외 (중복 방지)
        base_without_rotation = {k: v for k, v in base_config.items() if k != 'theme_rotation_days'}

        # 1. 원본: 14일
        print("\n" + "=" * 60)
        print("📊 [1/2] 테마 로테이션 14일 (원본)")
        print("=" * 60)
        config_14d = BacktestConfig(
            **base_without_rotation,
            **profit_trailing_config,
            theme_rotation_days=14,  # 원본: 14일
        )
        bt_14d = LiveLogicBacktester(config_14d)
        bt_14d.run()

        equity_14d = pd.Series([e["equity"] for e in bt_14d.equity_curve])
        mdd_14d = ((equity_14d - equity_14d.cummax()) / equity_14d.cummax() * 100).min()

        results['14d'] = {
            'final': bt_14d.equity_curve[-1]["equity"] if bt_14d.equity_curve else 0,
            'trades': len(bt_14d.trades),
            'mdd': mdd_14d,
            'wins': len([t for t in bt_14d.trades if t.pnl > 0]),
        }

        # 2. 테스트: 7일
        print("\n" + "=" * 60)
        print("📊 [2/2] 테마 로테이션 7일 (테스트)")
        print("=" * 60)
        config_7d = BacktestConfig(
            **base_without_rotation,
            **profit_trailing_config,
            theme_rotation_days=7,  # 테스트: 7일
        )
        bt_7d = LiveLogicBacktester(config_7d)
        bt_7d.run()

        equity_7d = pd.Series([e["equity"] for e in bt_7d.equity_curve])
        mdd_7d = ((equity_7d - equity_7d.cummax()) / equity_7d.cummax() * 100).min()

        results['7d'] = {
            'final': bt_7d.equity_curve[-1]["equity"] if bt_7d.equity_curve else 0,
            'trades': len(bt_7d.trades),
            'mdd': mdd_7d,
            'wins': len([t for t in bt_7d.trades if t.pnl > 0]),
        }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 70)
        print("📈 테마 로테이션 주기 비교 결과")
        print("=" * 70)
        print(f"{'구분':<20} {'14일 (원본)':>15} {'7일 (테스트)':>15} {'차이':>15}")
        print("-" * 70)

        ret_14d = (results['14d']['final'] - initial) / initial * 100
        ret_7d = (results['7d']['final'] - initial) / initial * 100
        print(f"{'총 수익률':.<20} {ret_14d:>14.2f}% {ret_7d:>14.2f}% {ret_7d - ret_14d:>+14.2f}%")
        print(f"{'최종 자산':.<20} {results['14d']['final']:>14,.0f} {results['7d']['final']:>14,.0f} {results['7d']['final'] - results['14d']['final']:>+14,.0f}")
        print(f"{'MDD':.<20} {results['14d']['mdd']:>14.2f}% {results['7d']['mdd']:>14.2f}% {results['7d']['mdd'] - results['14d']['mdd']:>+14.2f}%")
        print(f"{'총 거래':.<20} {results['14d']['trades']:>15} {results['7d']['trades']:>15} {results['7d']['trades'] - results['14d']['trades']:>+15}")

        win_rate_14 = results['14d']['wins'] / results['14d']['trades'] * 100 if results['14d']['trades'] > 0 else 0
        win_rate_7 = results['7d']['wins'] / results['7d']['trades'] * 100 if results['7d']['trades'] > 0 else 0
        print(f"{'승률':.<20} {win_rate_14:>14.1f}% {win_rate_7:>14.1f}% {win_rate_7 - win_rate_14:>+14.1f}%")
        print("=" * 70)

        # 결론
        if ret_7d > ret_14d:
            print("\n✅ 결론: 7일 로테이션이 더 좋은 성과!")
        else:
            print("\n❌ 결론: 14일 로테이션 유지 권장")

    # ============================================================
    # 보유기간 비교 테스트: 14일 vs 20일 vs 25일 vs 30일
    # ============================================================
    elif args.test_holding:
        print("\n" + "=" * 80)
        print("🔬 수익 종목 보유기간 비교 테스트: 14일 vs 20일 vs 25일 vs 30일")
        print("=" * 80)
        print("기준: 이익 추종 전략 (Let Profits Run) + 7일 테마 로테이션")
        print("=" * 80)

        holding_days_list = [14, 20, 25, 30]
        results = {}

        # base_config에서 max_holding_days 제외 (중복 방지)
        base_without_holding = {k: v for k, v in base_config.items()}
        # profit_trailing_config에서도 max_holding_days 제외
        trailing_without_holding = {k: v for k, v in profit_trailing_config.items() if k != 'max_holding_days'}

        for i, days in enumerate(holding_days_list, 1):
            print(f"\n{'=' * 60}")
            print(f"📊 [{i}/{len(holding_days_list)}] 보유기간 {days}일")
            print("=" * 60)

            config = BacktestConfig(
                **base_without_holding,
                **trailing_without_holding,
                max_holding_days=days,
            )
            bt = LiveLogicBacktester(config)
            bt.run()

            equity = pd.Series([e["equity"] for e in bt.equity_curve])
            mdd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

            # 평균 보유일 계산
            if bt.trades:
                avg_hold = sum(t.holding_days for t in bt.trades) / len(bt.trades)
            else:
                avg_hold = 0

            results[days] = {
                'final': bt.equity_curve[-1]["equity"] if bt.equity_curve else 0,
                'trades': len(bt.trades),
                'mdd': mdd,
                'wins': len([t for t in bt.trades if t.pnl > 0]),
                'avg_hold': avg_hold,
            }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 80)
        print("📈 보유기간 비교 결과")
        print("=" * 80)
        print(f"{'구분':<15} {'14일 (원본)':>15} {'20일':>15} {'25일':>15} {'30일':>15}")
        print("-" * 80)

        # 수익률
        returns = {d: (results[d]['final'] - initial) / initial * 100 for d in holding_days_list}
        print(f"{'총 수익률':.<15}", end="")
        for d in holding_days_list:
            print(f" {returns[d]:>14.2f}%", end="")
        print()

        # 최종 자산
        print(f"{'최종 자산':.<15}", end="")
        for d in holding_days_list:
            print(f" {results[d]['final']:>14,.0f}", end="")
        print()

        # MDD
        print(f"{'MDD':.<15}", end="")
        for d in holding_days_list:
            print(f" {results[d]['mdd']:>14.2f}%", end="")
        print()

        # 거래 수
        print(f"{'총 거래':.<15}", end="")
        for d in holding_days_list:
            print(f" {results[d]['trades']:>15}", end="")
        print()

        # 평균 보유일
        print(f"{'평균 보유일':.<15}", end="")
        for d in holding_days_list:
            print(f" {results[d]['avg_hold']:>14.1f}일", end="")
        print()

        # 승률
        print(f"{'승률':.<15}", end="")
        for d in holding_days_list:
            wr = results[d]['wins'] / results[d]['trades'] * 100 if results[d]['trades'] > 0 else 0
            print(f" {wr:>14.1f}%", end="")
        print()
        print("=" * 80)

        # 최고 성과 찾기
        best_days = max(holding_days_list, key=lambda d: returns[d])
        print(f"\n✅ 결론: {best_days}일 보유가 가장 좋은 성과! (수익률 {returns[best_days]:.2f}%)")

        # 14일 대비 개선폭
        if best_days != 14:
            improvement = returns[best_days] - returns[14]
            print(f"   14일 대비 +{improvement:.2f}% 개선")

    # ============================================================
    # 테마 수 비교 테스트: 2개 vs 3개 vs 4개 vs 5개
    # ============================================================
    elif args.test_themes:
        print("\n" + "=" * 80)
        print("🔬 테마 수 비교 테스트: 2개 vs 3개 vs 4개 vs 5개")
        print("=" * 80)
        print("기준: 이익 추종 전략 + 7일 로테이션 + 14일 보유")
        print("=" * 80)

        themes_list = [2, 3, 4, 5]
        results = {}

        # base_config에서 top_themes 제외 (중복 방지)
        base_without_themes = {k: v for k, v in base_config.items() if k != 'top_themes'}

        for i, num_themes in enumerate(themes_list, 1):
            print(f"\n{'=' * 60}")
            print(f"📊 [{i}/{len(themes_list)}] 테마 {num_themes}개")
            print("=" * 60)

            config = BacktestConfig(
                **base_without_themes,
                **profit_trailing_config,
                top_themes=num_themes,
            )
            bt = LiveLogicBacktester(config)
            bt.run()

            equity = pd.Series([e["equity"] for e in bt.equity_curve])
            mdd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

            # 테마별 거래 수 계산
            theme_counts = {}
            for t in bt.trades:
                theme_counts[t.theme] = theme_counts.get(t.theme, 0) + 1

            results[num_themes] = {
                'final': bt.equity_curve[-1]["equity"] if bt.equity_curve else 0,
                'trades': len(bt.trades),
                'mdd': mdd,
                'wins': len([t for t in bt.trades if t.pnl > 0]),
                'unique_themes': len(theme_counts),
            }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 80)
        print("📈 테마 수 비교 결과")
        print("=" * 80)
        print(f"{'구분':<15} {'2개':>15} {'3개 (원본)':>15} {'4개':>15} {'5개':>15}")
        print("-" * 80)

        # 수익률
        returns = {n: (results[n]['final'] - initial) / initial * 100 for n in themes_list}
        print(f"{'총 수익률':.<15}", end="")
        for n in themes_list:
            print(f" {returns[n]:>14.2f}%", end="")
        print()

        # 최종 자산
        print(f"{'최종 자산':.<15}", end="")
        for n in themes_list:
            print(f" {results[n]['final']:>14,.0f}", end="")
        print()

        # MDD
        print(f"{'MDD':.<15}", end="")
        for n in themes_list:
            print(f" {results[n]['mdd']:>14.2f}%", end="")
        print()

        # 거래 수
        print(f"{'총 거래':.<15}", end="")
        for n in themes_list:
            print(f" {results[n]['trades']:>15}", end="")
        print()

        # 승률
        print(f"{'승률':.<15}", end="")
        for n in themes_list:
            wr = results[n]['wins'] / results[n]['trades'] * 100 if results[n]['trades'] > 0 else 0
            print(f" {wr:>14.1f}%", end="")
        print()
        print("=" * 80)

        # 최고 성과 찾기
        best_themes = max(themes_list, key=lambda n: returns[n])
        print(f"\n✅ 결론: {best_themes}개 테마가 가장 좋은 성과! (수익률 {returns[best_themes]:.2f}%)")

        # 3개 대비 개선폭
        if best_themes != 3:
            improvement = returns[best_themes] - returns[3]
            print(f"   3개 대비 {improvement:+.2f}% {'개선' if improvement > 0 else '하락'}")

    # ============================================================
    # 테마당 종목 수 비교 테스트: 2개 vs 3개 vs 4개 vs 5개
    # ============================================================
    elif args.test_stocks:
        print("\n" + "=" * 80)
        print("🔬 테마당 종목 수 비교 테스트: 2개 vs 3개 vs 4개 vs 5개")
        print("=" * 80)
        print("기준: 이익 추종 전략 + 7일 로테이션 + 3개 테마 + 14일 보유")
        print("=" * 80)

        stocks_list = [2, 3, 4, 5]
        results = {}

        # base_config에서 stocks_per_theme 제외 (중복 방지)
        base_without_stocks = {k: v for k, v in base_config.items() if k != 'stocks_per_theme'}

        for i, num_stocks in enumerate(stocks_list, 1):
            print(f"\n{'=' * 60}")
            print(f"📊 [{i}/{len(stocks_list)}] 테마당 {num_stocks}개 종목")
            print("=" * 60)

            config = BacktestConfig(
                **base_without_stocks,
                **profit_trailing_config,
                stocks_per_theme=num_stocks,
            )
            bt = LiveLogicBacktester(config)
            bt.run()

            equity = pd.Series([e["equity"] for e in bt.equity_curve])
            mdd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

            results[num_stocks] = {
                'final': bt.equity_curve[-1]["equity"] if bt.equity_curve else 0,
                'trades': len(bt.trades),
                'mdd': mdd,
                'wins': len([t for t in bt.trades if t.pnl > 0]),
            }

        # 비교 결과
        initial = base_config["initial_capital"]
        print("\n" + "=" * 80)
        print("📈 테마당 종목 수 비교 결과")
        print("=" * 80)
        print(f"{'구분':<15} {'2개':>15} {'3개 (원본)':>15} {'4개':>15} {'5개':>15}")
        print("-" * 80)

        # 수익률
        returns = {n: (results[n]['final'] - initial) / initial * 100 for n in stocks_list}
        print(f"{'총 수익률':.<15}", end="")
        for n in stocks_list:
            print(f" {returns[n]:>14.2f}%", end="")
        print()

        # 최종 자산
        print(f"{'최종 자산':.<15}", end="")
        for n in stocks_list:
            print(f" {results[n]['final']:>14,.0f}", end="")
        print()

        # MDD
        print(f"{'MDD':.<15}", end="")
        for n in stocks_list:
            print(f" {results[n]['mdd']:>14.2f}%", end="")
        print()

        # 거래 수
        print(f"{'총 거래':.<15}", end="")
        for n in stocks_list:
            print(f" {results[n]['trades']:>15}", end="")
        print()

        # 승률
        print(f"{'승률':.<15}", end="")
        for n in stocks_list:
            wr = results[n]['wins'] / results[n]['trades'] * 100 if results[n]['trades'] > 0 else 0
            print(f" {wr:>14.1f}%", end="")
        print()
        print("=" * 80)

        # 최고 성과 찾기
        best_stocks = max(stocks_list, key=lambda n: returns[n])
        print(f"\n✅ 결론: 테마당 {best_stocks}개 종목이 가장 좋은 성과! (수익률 {returns[best_stocks]:.2f}%)")

        # 3개 대비 개선폭
        if best_stocks != 3:
            improvement = returns[best_stocks] - returns[3]
            print(f"   3개 대비 {improvement:+.2f}% {'개선' if improvement > 0 else '하락'}")

    # ============================================================
    # 4전략 비교: 기존 최적화 vs 터틀청산 vs 터틀무제한 vs 순수터틀
    # ============================================================
    elif args.compare_turtle:
        print("\n" + "=" * 90)
        print("  4전략 비교 백테스트: 기존 최적화 vs 터틀청산 vs 터틀무제한 vs 순수터틀")
        print("=" * 90)

        strategies = {
            "A_기존최적화": BacktestConfig(
                **base_config,
                **profit_trailing_config,
            ),
            "B_테마+터틀청산": BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=False,
                enable_partial_profit=False,
                turtle_exit=True,
                no_max_hold=False,
                max_holding_days=14,
            ),
            "C_테마+터틀무제한": BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=False,
                enable_partial_profit=False,
                turtle_exit=True,
                no_max_hold=True,
            ),
            "D_순수터틀": BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=False,
                enable_partial_profit=False,
                turtle_exit=True,
                no_max_hold=True,
                pure_turtle=True,
            ),
        }

        results = {}
        initial = base_config["initial_capital"]

        # 데이터를 한번만 로드하고 공유
        shared_data = None

        for idx, (name, config) in enumerate(strategies.items(), 1):
            print(f"\n{'=' * 70}")
            print(f"  [{idx}/4] {name}")
            print("=" * 70)

            bt = LiveLogicBacktester(config)

            if shared_data is None:
                bt.load_data()
                shared_data = bt.price_data
            else:
                bt.price_data = shared_data
                logger.info(f"  (캐시된 데이터 사용: {len(shared_data)}개 종목)")

            # run 대신 내부 로직 직접 실행 (데이터 로드 스킵)
            sample_df = list(bt.price_data.values())[0]
            trading_days = sample_df.index.tolist()

            last_rotation = None
            current_themes = []

            for i, date in enumerate(trading_days):
                date_dt = date.to_pydatetime()

                # 테마 로테이션
                if not config.pure_turtle:
                    if last_rotation is None or (date_dt - last_rotation).days >= config.theme_rotation_days:
                        bt.theme_scores = bt.calculate_theme_scores(date_dt)
                        sorted_themes = sorted(bt.theme_scores.items(), key=lambda x: x[1], reverse=True)
                        current_themes = [t[0] for t in sorted_themes[:config.top_themes]]
                        last_rotation = date_dt

                # 청산
                bt.check_exits(date_dt)

                # 진입
                if config.pure_turtle:
                    bt.execute_pure_turtle_entry(date_dt)
                elif len(bt.positions) < config.max_positions:
                    all_candidates = []
                    for theme in current_themes:
                        stocks = bt.select_stocks(theme, date_dt)
                        all_candidates.extend(stocks)
                    all_candidates.sort(key=lambda x: x["score"], reverse=True)
                    all_candidates = bt.calculate_weights(all_candidates[:config.max_positions])
                    if all_candidates:
                        bt.execute_entry(all_candidates, date_dt)

                # 자산 기록
                equity = bt.calculate_equity(date_dt)
                bt.equity_curve.append({"date": date_dt, "equity": equity, "positions": len(bt.positions)})
                if i > 0:
                    prev_eq = bt.equity_curve[-2]["equity"]
                    bt.daily_returns.append((equity - prev_eq) / prev_eq * 100)

            # 최종 청산
            final_date = trading_days[-1].to_pydatetime()
            for pos in list(bt.positions):
                if pos.code in bt.price_data:
                    df = bt.price_data[pos.code]
                    exit_price = float(df.iloc[-1]['Close'])
                    pnl = (exit_price - pos.entry_price) * pos.remaining_shares
                    pnl_pct = pnl / (pos.entry_price * pos.shares) * 100
                    bt.trades.append(Trade(
                        code=pos.code, name=pos.name, theme=pos.theme,
                        entry_date=pos.entry_date, exit_date=final_date,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        shares=pos.shares, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="백테스트종료",
                        holding_days=(final_date - pos.entry_date).days,
                    ))
                    bt.cash += exit_price * pos.remaining_shares
            bt.positions = []

            # 결과 수집
            final_eq = bt.equity_curve[-1]["equity"] if bt.equity_curve else initial
            total_ret = (final_eq - initial) / initial * 100

            years = (datetime.strptime(config.end_date, "%Y-%m-%d") - datetime.strptime(config.start_date, "%Y-%m-%d")).days / 365
            cagr = ((final_eq / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

            equity_s = pd.Series([e["equity"] for e in bt.equity_curve])
            mdd = ((equity_s - equity_s.cummax()) / equity_s.cummax() * 100).min()

            if bt.daily_returns:
                avg_r = np.mean(bt.daily_returns)
                std_r = np.std(bt.daily_returns)
                sharpe = (avg_r * 252) / (std_r * np.sqrt(252)) if std_r > 0 else 0
            else:
                sharpe = 0

            wins = [t for t in bt.trades if t.pnl > 0]
            losses = [t for t in bt.trades if t.pnl <= 0]
            win_rate = len(wins) / len(bt.trades) * 100 if bt.trades else 0
            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
            avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
            plr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
            avg_hold = np.mean([t.holding_days for t in bt.trades]) if bt.trades else 0

            results[name] = {
                'total_return': total_ret, 'cagr': cagr, 'mdd': mdd, 'sharpe': sharpe,
                'win_rate': win_rate, 'plr': plr, 'avg_hold': avg_hold,
                'trades': len(bt.trades), 'avg_win': avg_win, 'avg_loss': avg_loss,
            }

            # 청산 사유별 통계
            reasons = {}
            for t in bt.trades:
                reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
            reason_str = ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))

            print(f"  수익률: {total_ret:+.2f}% | CAGR: {cagr:.1f}% | MDD: {mdd:.2f}% | Sharpe: {sharpe:.2f}")
            print(f"  거래: {len(bt.trades)}회 | 승률: {win_rate:.1f}% | 손익비: {plr:.2f} | 평균보유: {avg_hold:.1f}일")
            print(f"  청산사유: {reason_str}")

        # ===== 최종 비교 테이블 =====
        print("\n" + "=" * 110)
        print("  4전략 최종 비교")
        print("=" * 110)

        header = f"{'지표':<12}"
        for name in strategies:
            label = name.split("_", 1)[1]
            header += f" {label:>22}"
        print(header)
        print("-" * 110)

        rows = [
            ("수익률", "total_return", "%", ".2f"),
            ("CAGR", "cagr", "%", ".1f"),
            ("MDD", "mdd", "%", ".2f"),
            ("Sharpe", "sharpe", "", ".2f"),
            ("승률", "win_rate", "%", ".1f"),
            ("손익비", "plr", "", ".2f"),
            ("평균보유일", "avg_hold", "일", ".1f"),
            ("거래수", "trades", "회", "d"),
            ("평균수익", "avg_win", "%", ".2f"),
            ("평균손실", "avg_loss", "%", ".2f"),
        ]

        for label, key, unit, fmt in rows:
            line = f"{label:<12}"
            for name in strategies:
                val = results[name][key]
                if fmt == "d":
                    line += f" {int(val):>20}{unit:>2}"
                else:
                    line += f" {val:>20{fmt}}{unit:>2}"
            print(line)

        print("=" * 110)

        # 최고 성과
        best = max(results.items(), key=lambda x: x[1]['total_return'])
        best_label = best[0].split("_", 1)[1]
        print(f"\n  최고 수익률: {best_label} ({best[1]['total_return']:+.2f}%)")

        # 리스크 대비 최고
        best_sharpe = max(results.items(), key=lambda x: x[1]['sharpe'])
        best_s_label = best_sharpe[0].split("_", 1)[1]
        print(f"  최고 Sharpe: {best_s_label} ({best_sharpe[1]['sharpe']:.2f})")

    else:
        # ============================================================
        # 단일 실행 모드
        # ============================================================
        if args.pure_turtle:
            print("  순수 터틀 (20일 돌파 + ATR사이징 + 10일저가 청산)")
            config = BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=False,
                enable_partial_profit=False,
                turtle_exit=True,
                no_max_hold=True,
                pure_turtle=True,
            )
        elif args.turtle_exit:
            no_hold = args.no_max_hold
            print(f"  테마+터틀청산 (10일저가 + 2xATR, 보유제한={'없음' if no_hold else '14일'})")
            config = BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=False,
                enable_partial_profit=False,
                turtle_exit=True,
                no_max_hold=no_hold,
            )
        elif args.old_strategy:
            print("  기존 전략 (분할익절 + 트레일링)")
            config = BacktestConfig(
                **base_config,
                enable_profit_trailing=False,
                enable_fixed_take_profit=True,
                take_profit_pct=0.15,
                enable_partial_profit=True,
                partial_profit_pct=0.10,
                partial_sell_ratio=0.5,
                enable_trailing_after_partial=True,
                trailing_after_partial_pct=0.05,
                max_holding_days=10,
            )
        else:
            print("  이익 추종 전략 (단계별 트레일링)")
            print("   손절: -7%, 트레일링 L1(5%)/L2(3%)/L3(2%)")
            config = BacktestConfig(
                **base_config,
                **profit_trailing_config,
            )

        backtester = LiveLogicBacktester(config)
        backtester.run()


if __name__ == "__main__":
    main()
