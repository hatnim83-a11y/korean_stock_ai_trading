"""
data_loader.py - 백테스트 데이터 로더

과거 주가 데이터를 로드하고 전처리합니다.

기능:
- 주가 데이터 다운로드 (KIS API 또는 CSV)
- 벡터화된 데이터 구조 변환
- 기술적 지표 계산 (MA, RSI, ATR 등)

사용법:
    from modules.backtester.data_loader import DataLoader
    
    loader = DataLoader()
    data = loader.load_stock_data("005930", "2023-01-01", "2023-12-31")
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import pickle

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
import yfinance as yf

from logger import logger
from config import settings


@dataclass
class MarketData:
    """시장 데이터"""
    symbol: str                              # 종목 코드
    name: str = ""                           # 종목명
    data: pd.DataFrame = field(default_factory=pd.DataFrame)  # OHLCV 데이터
    
    # 메타데이터
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_days: int = 0
    
    def __post_init__(self):
        if not self.data.empty:
            self.start_date = self.data.index[0]
            self.end_date = self.data.index[-1]
            self.total_days = len(self.data)


class DataLoader:
    """
    백테스트 데이터 로더
    
    과거 주가 데이터를 벡터화된 형태로 로드합니다.
    """
    
    def __init__(
        self,
        cache_dir: str = "data/backtest_cache",
        use_cache: bool = True
    ):
        """
        Args:
            cache_dir: 캐시 디렉토리
            use_cache: 캐시 사용 여부
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        
        # KIS API (지연 로딩)
        self._kis_api = None
        
        logger.info(f"📂 데이터 로더 초기화 (캐시: {'ON' if use_cache else 'OFF'})")
    
    @property
    def kis_api(self):
        """KIS API 지연 로딩"""
        if self._kis_api is None:
            try:
                from modules.stock_screener.kis_api import KISApi
                self._kis_api = KISApi()
            except Exception as e:
                logger.warning(f"KIS API 로딩 실패: {e}")
        return self._kis_api
    
    def _get_cache_path(self, symbol: str, start_date: str, end_date: str) -> Path:
        """캐시 파일 경로"""
        return self.cache_dir / f"{symbol}_{start_date}_{end_date}.pkl"
    
    def _load_from_cache(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """캐시에서 로드"""
        cache_path = self._get_cache_path(symbol, start_date, end_date)
        
        if cache_path.exists():
            try:
                with open(cache_path, 'rb') as f:
                    data = pickle.load(f)
                logger.debug(f"[{symbol}] 캐시에서 로드")
                return data
            except Exception as e:
                logger.warning(f"캐시 로드 실패: {e}")
        
        return None
    
    def _save_to_cache(self, symbol: str, start_date: str, end_date: str, data: pd.DataFrame):
        """캐시에 저장"""
        cache_path = self._get_cache_path(symbol, start_date, end_date)
        
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"[{symbol}] 캐시에 저장")
        except Exception as e:
            logger.warning(f"캐시 저장 실패: {e}")
    
    def load_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        name: str = ""
    ) -> MarketData:
        """
        종목 데이터 로드
        
        Args:
            symbol: 종목 코드
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            name: 종목명
            
        Returns:
            MarketData
        """
        logger.info(f"📊 [{symbol}] 데이터 로드: {start_date} ~ {end_date}")
        
        # 캐시 확인
        if self.use_cache:
            cached_data = self._load_from_cache(symbol, start_date, end_date)
            if cached_data is not None and not cached_data.empty:
                return MarketData(
                    symbol=symbol,
                    name=name,
                    data=cached_data
                )
        
        # API에서 로드
        data = self._fetch_from_api(symbol, start_date, end_date)
        
        if data is None or data.empty:
            # 모의 데이터 생성 (API 실패 시)
            logger.warning(f"[{symbol}] API 실패, 모의 데이터 생성")
            data = self._generate_mock_data(symbol, start_date, end_date)
        
        # 캐시에 저장
        if self.use_cache and not data.empty:
            self._save_to_cache(symbol, start_date, end_date, data)
        
        return MarketData(
            symbol=symbol,
            name=name,
            data=data
        )
    
    def _fetch_from_api(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """
        yfinance 또는 KIS API에서 일봉 데이터 조회
        
        Returns:
            DataFrame with columns: ['open', 'high', 'low', 'close', 'volume']
        """
        # 1순위: yfinance (빠르고 안정적)
        data = self._fetch_from_yfinance(symbol, start_date, end_date)
        if data is not None and not data.empty:
            return data
        
        # 2순위: KIS API (yfinance 실패 시)
        logger.warning(f"[{symbol}] yfinance 실패, KIS API 시도")
        data = self._fetch_from_kis_api(symbol, start_date, end_date)
        if data is not None and not data.empty:
            return data
        
        return None
    
    def _fetch_from_yfinance(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """
        yfinance에서 데이터 조회
        
        한국 주식은 종목코드.KS 형식 (예: 005930.KS)
        코스닥은 종목코드.KQ 형식 (예: 247540.KQ)
        """
        try:
            # 한국 주식 티커 변환 (6자리 종목코드 → 종목코드.KS)
            ticker_symbol = f"{symbol}.KS"
            
            logger.debug(f"[{symbol}] yfinance 조회: {ticker_symbol}")
            
            ticker = yf.Ticker(ticker_symbol)
            data = ticker.history(start=start_date, end=end_date)
            
            # 데이터가 없으면 코스닥 시도
            if data.empty:
                ticker_symbol = f"{symbol}.KQ"
                logger.debug(f"[{symbol}] 코스닥 시도: {ticker_symbol}")
                ticker = yf.Ticker(ticker_symbol)
                data = ticker.history(start=start_date, end=end_date)
            
            if data.empty:
                logger.warning(f"[{symbol}] yfinance 데이터 없음")
                return None
            
            # 컬럼명 소문자로 변환
            data.columns = data.columns.str.lower()
            
            # 필요한 컬럼만 선택
            result = pd.DataFrame({
                'open': data['open'],
                'high': data['high'],
                'low': data['low'],
                'close': data['close'],
                'volume': data['volume']
            })
            
            logger.info(f"[{symbol}] yfinance 조회 성공: {len(result)}일")
            return result
            
        except Exception as e:
            logger.error(f"[{symbol}] yfinance 조회 실패: {e}")
            return None
    
    def _fetch_from_kis_api(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """
        KIS API에서 일봉 데이터 조회 (100일씩 반복 호출)
        
        Returns:
            DataFrame with columns: ['open', 'high', 'low', 'close', 'volume']
        """
        if not self.kis_api:
            return None
        
        try:
            # 날짜 범위 계산
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            total_days = (end - start).days
            
            # 100일씩 반복 호출
            all_data = []
            calls_needed = (total_days // 100) + 1
            
            logger.info(f"[{symbol}] KIS API {calls_needed}회 호출 필요 ({total_days}일)")
            
            for i in range(calls_needed):
                count = min(100, total_days - (i * 100))
                if count <= 0:
                    break
                
                daily_data = self.kis_api.get_daily_price(symbol, period="D", count=count)
                
                if daily_data:
                    all_data.extend(daily_data)
                    logger.debug(f"[{symbol}] KIS API {i+1}/{calls_needed}: {len(daily_data)}건")
                
                # API 제한 고려
                if i < calls_needed - 1:
                    import time
                    time.sleep(0.2)  # 200ms 대기
            
            if not all_data:
                return None
            
            # DataFrame 변환
            df = pd.DataFrame(all_data)
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
            df.set_index('date', inplace=True)
            df.sort_index(inplace=True)
            
            # 필요한 컬럼만 선택
            result = pd.DataFrame({
                'open': df['open'],
                'high': df['high'],
                'low': df['low'],
                'close': df['close'],
                'volume': df['volume']
            })
            
            logger.info(f"[{symbol}] KIS API 조회 성공: {len(result)}일")
            return result
            
        except Exception as e:
            logger.error(f"[{symbol}] KIS API 조회 실패: {e}")
            return None
    
    def _generate_mock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        base_price: int = 50000
    ) -> pd.DataFrame:
        """
        모의 데이터 생성 (백테스트용)
        
        Args:
            symbol: 종목 코드
            start_date: 시작일
            end_date: 종료일
            base_price: 기준 가격
            
        Returns:
            DataFrame with OHLCV
        """
        # 날짜 범위 생성
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        dates = dates[dates.dayofweek < 5]  # 주말 제외
        
        np.random.seed(hash(symbol) % (2**32))  # 종목별 일관된 시드
        
        n_days = len(dates)
        
        # 랜덤 워크 생성
        returns = np.random.normal(0.001, 0.02, n_days)  # 평균 +0.1%, 변동성 2%
        price_series = base_price * np.exp(np.cumsum(returns))
        
        # OHLCV 생성
        data = pd.DataFrame({
            'open': price_series * (1 + np.random.uniform(-0.01, 0.01, n_days)),
            'high': price_series * (1 + np.random.uniform(0, 0.02, n_days)),
            'low': price_series * (1 + np.random.uniform(-0.02, 0, n_days)),
            'close': price_series,
            'volume': np.random.randint(100000, 10000000, n_days)
        }, index=dates)
        
        # 고가가 시가/종가보다 높고, 저가가 낮도록 조정
        data['high'] = data[['open', 'high', 'close']].max(axis=1)
        data['low'] = data[['open', 'low', 'close']].min(axis=1)
        
        logger.debug(f"[{symbol}] 모의 데이터 생성: {len(data)}일")
        
        return data
    
    def add_technical_indicators(
        self,
        data: pd.DataFrame,
        indicators: List[str] = None
    ) -> pd.DataFrame:
        """
        기술적 지표 추가 (벡터화)
        
        Args:
            data: OHLCV DataFrame
            indicators: 지표 리스트 ['ma', 'rsi', 'atr', 'bbands']
            
        Returns:
            지표가 추가된 DataFrame
        """
        if indicators is None:
            indicators = ['ma_20', 'ma_60', 'rsi', 'atr']
        
        df = data.copy()
        
        # 이동평균 (MA)
        if 'ma_20' in indicators or 'ma' in indicators:
            df['ma_20'] = df['close'].rolling(window=20).mean()
        
        if 'ma_60' in indicators or 'ma' in indicators:
            df['ma_60'] = df['close'].rolling(window=60).mean()
        
        # RSI
        if 'rsi' in indicators:
            df['rsi'] = self._calculate_rsi(df['close'])
        
        # ATR (Average True Range)
        if 'atr' in indicators:
            df['atr'] = self._calculate_atr(df)
        
        # Bollinger Bands
        if 'bbands' in indicators or 'bb' in indicators:
            bb_mid, bb_upper, bb_lower = self._calculate_bollinger_bands(df['close'])
            df['bb_mid'] = bb_mid
            df['bb_upper'] = bb_upper
            df['bb_lower'] = bb_lower
        
        return df
    
    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """RSI 계산 (벡터화)"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR 계산 (벡터화)"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    @staticmethod
    def _calculate_bollinger_bands(
        prices: pd.Series,
        period: int = 20,
        num_std: float = 2.0
    ) -> tuple:
        """볼린저 밴드 계산 (벡터화)"""
        ma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        
        upper = ma + (std * num_std)
        lower = ma - (std * num_std)
        
        return ma, upper, lower
    
    def load_multiple_stocks(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str
    ) -> Dict[str, MarketData]:
        """
        여러 종목 데이터 일괄 로드
        
        Args:
            symbols: 종목 코드 리스트
            start_date: 시작일
            end_date: 종료일
            
        Returns:
            {symbol: MarketData} 딕셔너리
        """
        logger.info(f"📊 {len(symbols)}개 종목 데이터 로드")
        
        data_dict = {}
        
        for symbol in symbols:
            try:
                data = self.load_stock_data(symbol, start_date, end_date)
                data_dict[symbol] = data
            except Exception as e:
                logger.error(f"[{symbol}] 로드 실패: {e}")
        
        logger.info(f"✅ {len(data_dict)}개 종목 로드 완료")
        
        return data_dict


# ===== 간편 함수 =====

def load_backtest_data(
    symbol: str,
    start_date: str,
    end_date: str,
    with_indicators: bool = True
) -> MarketData:
    """
    백테스트 데이터 로드 (간편 함수)
    
    Args:
        symbol: 종목 코드
        start_date: 시작일
        end_date: 종료일
        with_indicators: 기술적 지표 추가 여부
        
    Returns:
        MarketData
    """
    loader = DataLoader()
    market_data = loader.load_stock_data(symbol, start_date, end_date)
    
    if with_indicators and not market_data.data.empty:
        market_data.data = loader.add_technical_indicators(market_data.data)
    
    return market_data


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📂 데이터 로더 테스트")
    print("=" * 60)
    
    loader = DataLoader()
    
    # 1개 종목 로드
    print("\n1️⃣ 단일 종목 로드:")
    data = loader.load_stock_data("005930", "2023-01-01", "2023-12-31", "삼성전자")
    
    print(f"   종목: {data.name} ({data.symbol})")
    print(f"   기간: {data.start_date.date()} ~ {data.end_date.date()}")
    print(f"   일수: {data.total_days}일")
    print(f"\n   데이터 샘플:\n{data.data.head()}")
    
    # 기술적 지표 추가
    print("\n2️⃣ 기술적 지표 추가:")
    data_with_indicators = loader.add_technical_indicators(data.data)
    print(f"   컬럼: {list(data_with_indicators.columns)}")
    print(f"\n   샘플:\n{data_with_indicators[['close', 'ma_20', 'rsi', 'atr']].tail()}")
    
    # 여러 종목 로드
    print("\n3️⃣ 여러 종목 로드:")
    symbols = ["005930", "000660", "035420"]
    data_dict = loader.load_multiple_stocks(symbols, "2023-01-01", "2023-03-31")
    
    for symbol, market_data in data_dict.items():
        print(f"   {symbol}: {len(market_data.data)}일")
    
    print("\n" + "=" * 60)
    print("✅ 데이터 로더 테스트 완료!")
    print("=" * 60)
