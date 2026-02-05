"""
kis_api.py - 한국투자증권 KIS API 연동 모듈

이 파일은 KIS Developers API를 통해 주식 데이터를 조회합니다.

주요 기능:
- 접근 토큰 발급 및 관리
- 현재가/시세 조회
- 투자자별 매매동향 (수급) 조회
- 기술적 지표 계산 (MA, RSI, ATR)
- 재무 데이터 조회

사용법:
    from modules.stock_screener.kis_api import KISApi
    
    kis = KISApi()
    price = kis.get_current_price("005930")  # 삼성전자
    investor = kis.get_investor_trading("005930")
"""

import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Optional

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


def _safe_int(value, default: int = 0) -> int:
    """빈 문자열이나 잘못된 값을 안전하게 int로 변환"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    """빈 문자열이나 잘못된 값을 안전하게 float로 변환"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class KISApi:
    """
    한국투자증권 KIS Developers API 클라이언트
    
    주식 시세, 수급 데이터, 재무 정보 등을 조회합니다.
    
    Attributes:
        app_key: API 앱 키
        app_secret: API 앱 시크릿
        account_no: 계좌번호
        is_mock: 모의투자 여부
        
    Example:
        >>> kis = KISApi()
        >>> price = kis.get_current_price("005930")
        >>> print(price['price'])
        75000
    """
    
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        is_mock: Optional[bool] = None
    ):
        """
        KIS API 클라이언트 초기화
        
        Args:
            app_key: API 앱 키 (None이면 환경변수/config에서 로드)
            app_secret: API 앱 시크릿
            account_no: 계좌번호 (예: "12345678-01")
            is_mock: 모의투자 여부 (True: 모의, False: 실전)
        """
        # 설정 로드
        try:
            from config import settings, get_kis_base_url
            self.app_key = app_key or settings.KIS_APP_KEY
            self.app_secret = app_secret or settings.KIS_APP_SECRET
            self.account_no = account_no or settings.KIS_ACCOUNT_NO
            self.is_mock = is_mock if is_mock is not None else settings.IS_MOCK
            self.api_delay = settings.KIS_API_DELAY
        except ImportError:
            self.app_key = app_key or os.getenv("KIS_APP_KEY", "")
            self.app_secret = app_secret or os.getenv("KIS_APP_SECRET", "")
            self.account_no = account_no or os.getenv("KIS_ACCOUNT_NO", "")
            self.is_mock = is_mock if is_mock is not None else True
            self.api_delay = 0.11
        
        # 기본 URL 설정
        if self.is_mock:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"
        
        # 토큰 관리
        self.access_token: Optional[str] = None
        self.token_expired_at: float = 0
        
        # HTTP 클라이언트
        self.client = httpx.Client(timeout=30.0)
        
        logger.info(
            f"KIS API 초기화 완료 "
            f"({'모의투자' if self.is_mock else '실전투자'})"
        )
    
    def _rate_limit(self):
        """API 호출 제한 (초당 20회 제한 준수)"""
        time.sleep(self.api_delay)
    
    def get_access_token(self) -> str:
        """
        접근 토큰 발급 (24시간 유효)
        
        이미 유효한 토큰이 있으면 재사용합니다.
        
        Returns:
            접근 토큰 문자열
            
        Raises:
            Exception: 토큰 발급 실패 시
        """
        # 토큰이 유효하면 재사용 (만료 1시간 전 갱신)
        if self.access_token and self.token_expired_at > time.time() + 3600:
            return self.access_token
        
        url = f"{self.base_url}/oauth2/tokenP"
        
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        try:
            response = self.client.post(url, headers=headers, json=body)
            response.raise_for_status()
            
            data = response.json()
            
            if "access_token" not in data:
                raise Exception(f"토큰 발급 실패: {data}")
            
            self.access_token = data["access_token"]
            # 토큰 유효기간: 24시간
            self.token_expired_at = time.time() + (24 * 60 * 60)
            
            logger.info("🔑 KIS API 토큰 발급 성공")
            return self.access_token
            
        except httpx.HTTPStatusError as e:
            logger.error(f"토큰 발급 HTTP 에러: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"토큰 발급 실패: {e}")
            raise
    
    def _get_headers(self, tr_id: str) -> dict:
        """
        API 요청 헤더 생성
        
        Args:
            tr_id: 거래 ID (예: "FHKST01010100" - 현재가 조회)
        
        Returns:
            요청 헤더 딕셔너리
        """
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }
    
    # ===== 종목명 조회 =====

    def get_stock_name(self, stock_code: str) -> str:
        """
        종목코드로 종목명 조회 (네이버 금융 사용)

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            종목명 (조회 실패 시 빈 문자열)
        """
        # 캐시에서 먼저 확인
        if not hasattr(self, '_stock_name_cache'):
            self._stock_name_cache = {}

        if stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]

        try:
            url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }
            response = httpx.get(url, headers=headers, timeout=5.0, follow_redirects=True)

            if response.status_code == 200:
                # HTML에서 종목명 추출
                import re
                match = re.search(r'<title>([^:]+):', response.text)
                if match:
                    name = match.group(1).strip()
                    self._stock_name_cache[stock_code] = name
                    return name

        except Exception as e:
            logger.debug(f"[{stock_code}] 종목명 조회 실패: {e}")

        return ""

    # ===== 현재가/시세 조회 =====

    def get_current_price(self, stock_code: str) -> Optional[dict]:
        """
        주식 현재가 조회
        
        Args:
            stock_code: 종목코드 (6자리, 예: "005930")
        
        Returns:
            가격 정보 딕셔너리:
            {
                'code': '005930',
                'name': '삼성전자',
                'price': 75000,
                'change': 500,
                'change_rate': 0.67,
                'volume': 10000000,
                'high': 75500,
                'low': 74500,
                'open': 74800
            }
            
            조회 실패 시 None
            
        Example:
            >>> price = kis.get_current_price("005930")
            >>> print(f"{price['name']}: {price['price']:,}원")
            삼성전자: 75,000원
        """
        self._rate_limit()
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        
        # 모의투자/실전투자에 따른 TR_ID
        tr_id = "FHKST01010100"
        
        headers = self._get_headers(tr_id)
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 주식
            "FID_INPUT_ISCD": stock_code
        }
        
        try:
            response = self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("rt_cd") != "0":
                logger.warning(f"[{stock_code}] 현재가 조회 실패: {data.get('msg1')}")
                return None
            
            output = data.get("output", {})

            # 종목명: API에서 없으면 네이버에서 조회
            stock_name = output.get("hts_kor_isnm", "")
            if not stock_name:
                stock_name = self.get_stock_name(stock_code)

            result = {
                "code": stock_code,
                "name": stock_name,
                "price": _safe_int(output.get("stck_prpr")),  # 현재가
                "change": _safe_int(output.get("prdy_vrss")),  # 전일대비
                "change_rate": _safe_float(output.get("prdy_ctrt")),  # 등락률
                "volume": _safe_int(output.get("acml_vol")),  # 누적거래량
                "trade_value": _safe_int(output.get("acml_tr_pbmn")),  # 누적거래대금
                "high": _safe_int(output.get("stck_hgpr")),  # 고가
                "low": _safe_int(output.get("stck_lwpr")),  # 저가
                "open": _safe_int(output.get("stck_oprc")),  # 시가
                "per": _safe_float(output.get("per")),  # PER
                "pbr": _safe_float(output.get("pbr")),  # PBR
                "market_cap": _safe_int(output.get("hts_avls")) * 100000000,  # 시가총액
            }
            
            logger.debug(f"[{stock_code}] 현재가 조회: {result['price']:,}원")
            return result
            
        except Exception as e:
            logger.error(f"[{stock_code}] 현재가 조회 실패: {e}")
            return None
    
    def get_daily_price(
        self,
        stock_code: str,
        period: str = "D",
        count: int = 60
    ) -> Optional[list[dict]]:
        """
        일별 시세 조회 (OHLCV)
        
        Args:
            stock_code: 종목코드
            period: 기간 ("D": 일, "W": 주, "M": 월)
            count: 조회할 데이터 수 (최대 100)
        
        Returns:
            일별 시세 리스트:
            [
                {
                    'date': '20240201',
                    'open': 74800,
                    'high': 75500,
                    'low': 74500,
                    'close': 75000,
                    'volume': 10000000
                },
                ...
            ]
        """
        self._rate_limit()
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        
        tr_id = "FHKST01010400"
        headers = self._get_headers(tr_id)
        
        # 날짜 범위 설정
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",  # 수정주가 사용
        }
        
        try:
            response = self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("rt_cd") != "0":
                logger.warning(f"[{stock_code}] 일별 시세 조회 실패")
                return None
            
            output = data.get("output", [])
            
            result = []
            for item in output[:count]:
                result.append({
                    "date": item.get("stck_bsop_date", ""),
                    "open": _safe_int(item.get("stck_oprc")),
                    "high": _safe_int(item.get("stck_hgpr")),
                    "low": _safe_int(item.get("stck_lwpr")),
                    "close": _safe_int(item.get("stck_clpr")),
                    "volume": _safe_int(item.get("acml_vol")),
                    "trade_value": _safe_int(item.get("acml_tr_pbmn")),
                })
            
            logger.debug(f"[{stock_code}] 일별 시세 {len(result)}건 조회")
            return result
            
        except Exception as e:
            logger.error(f"[{stock_code}] 일별 시세 조회 실패: {e}")
            return None
    
    # ===== 투자자별 매매동향 (수급) =====
    
    def get_investor_trading(
        self,
        stock_code: str,
        days: int = 5
    ) -> Optional[dict]:
        """
        투자자별 매매동향 (수급 데이터) 조회
        
        외국인, 기관, 개인의 순매수 데이터를 조회합니다.
        
        Args:
            stock_code: 종목코드
            days: 조회할 일수 (기본 5일)
        
        Returns:
            수급 정보 딕셔너리:
            {
                'code': '005930',
                'foreign_net': 50000000000,  # 외국인 순매수 (원)
                'institution_net': 30000000000,  # 기관 순매수
                'individual_net': -80000000000,  # 개인 순매수
                'foreign_ratio': 55.2,  # 외국인 보유 비율 (%)
                'daily': [...]  # 일별 상세
            }
            
        Example:
            >>> investor = kis.get_investor_trading("005930", days=5)
            >>> print(f"외국인 5일 순매수: {investor['foreign_net'] / 100000000:.0f}억원")
        """
        self._rate_limit()
        
        # 투자자별 매매동향 조회 API
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
        
        tr_id = "FHKST01010900"
        headers = self._get_headers(tr_id)
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        
        try:
            response = self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("rt_cd") != "0":
                logger.warning(f"[{stock_code}] 수급 조회 실패: {data.get('msg1')}")
                return None
            
            output = data.get("output", [])
            
            # N일간 순매수 합계 계산
            foreign_total = 0
            institution_total = 0
            individual_total = 0
            daily_data = []
            
            for i, item in enumerate(output[:days]):
                # 순매수량 * 종가 = 순매수금액 (대략)
                foreign_net = _safe_int(item.get("frgn_ntby_qty"))
                institution_net = _safe_int(item.get("orgn_ntby_qty"))
                individual_net = _safe_int(item.get("prsn_ntby_qty"))

                # 종가 정보가 있으면 금액 계산
                close_price = _safe_int(item.get("stck_clpr"))
                
                foreign_total += foreign_net * close_price
                institution_total += institution_net * close_price
                individual_total += individual_net * close_price
                
                daily_data.append({
                    "date": item.get("stck_bsop_date", ""),
                    "foreign": foreign_net,
                    "institution": institution_net,
                    "individual": individual_net,
                    "close_price": close_price
                })
            
            result = {
                "code": stock_code,
                "foreign_net": foreign_total,  # 외국인 N일 순매수 (원)
                "institution_net": institution_total,  # 기관 N일 순매수
                "individual_net": individual_total,  # 개인 N일 순매수
                "foreign_ratio": float(data.get("output2", {}).get("frgn_poss_rt", 0) or 0),
                "daily": daily_data
            }
            
            logger.debug(
                f"[{stock_code}] 수급 조회: "
                f"외국인 {foreign_total / 100000000:+.0f}억, "
                f"기관 {institution_total / 100000000:+.0f}억"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[{stock_code}] 수급 조회 실패: {e}")
            return None
    
    # ===== 기술적 지표 계산 =====
    
    def get_technical_indicators(
        self,
        stock_code: str,
        ma_periods: list[int] = [5, 20, 60],
        rsi_period: int = 14,
        atr_period: int = 14
    ) -> Optional[dict]:
        """
        기술적 지표 계산
        
        일별 시세를 바탕으로 이동평균선, RSI, ATR 등을 계산합니다.
        
        Args:
            stock_code: 종목코드
            ma_periods: 이동평균선 기간 리스트
            rsi_period: RSI 계산 기간
            atr_period: ATR 계산 기간
        
        Returns:
            기술적 지표 딕셔너리:
            {
                'code': '005930',
                'price': 75000,
                'ma_5': 74500,
                'ma_20': 73000,
                'ma_60': 72000,
                'rsi': 55.3,
                'atr': 1500,
                'volume_ratio': 1.5,  # 거래량 비율 (vs 20일 평균)
                'ma_alignment': 'bullish'  # 정배열/역배열
            }
        """
        # 일별 시세 조회
        daily = self.get_daily_price(stock_code, count=max(ma_periods + [rsi_period, atr_period]) + 10)
        
        if not daily or len(daily) < 20:
            logger.warning(f"[{stock_code}] 기술적 지표 계산 불가 (데이터 부족)")
            return None
        
        # 종가 리스트 (최신순)
        closes = [d["close"] for d in daily]
        highs = [d["high"] for d in daily]
        lows = [d["low"] for d in daily]
        volumes = [d["volume"] for d in daily]
        
        current_price = closes[0]
        
        # 이동평균선 계산
        ma_values = {}
        for period in ma_periods:
            if len(closes) >= period:
                ma_values[f"ma_{period}"] = sum(closes[:period]) / period
            else:
                ma_values[f"ma_{period}"] = None
        
        # RSI 계산
        rsi = self._calculate_rsi(closes, rsi_period)
        
        # ATR 계산
        atr = self._calculate_atr(highs, lows, closes, atr_period)
        
        # 거래량 비율 (당일 vs 20일 평균)
        avg_volume_20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        volume_ratio = volumes[0] / avg_volume_20 if avg_volume_20 > 0 else 0
        
        # 정배열/역배열 판단
        ma_alignment = "neutral"
        if all(v is not None for v in [ma_values.get("ma_5"), ma_values.get("ma_20"), ma_values.get("ma_60")]):
            if ma_values["ma_5"] > ma_values["ma_20"] > ma_values["ma_60"]:
                ma_alignment = "bullish"  # 정배열
            elif ma_values["ma_5"] < ma_values["ma_20"] < ma_values["ma_60"]:
                ma_alignment = "bearish"  # 역배열
        
        result = {
            "code": stock_code,
            "price": current_price,
            **ma_values,
            "rsi": rsi,
            "atr": atr,
            "volume_ratio": round(volume_ratio, 2),
            "ma_alignment": ma_alignment
        }
        
        logger.debug(
            f"[{stock_code}] 기술적 지표: "
            f"RSI {rsi:.1f}, ATR {atr:,}, 정배열: {ma_alignment}"
        )
        
        return result
    
    def _calculate_rsi(self, closes: list[int], period: int = 14) -> float:
        """
        RSI (Relative Strength Index) 계산
        
        Args:
            closes: 종가 리스트 (최신순)
            period: RSI 기간 (기본 14일)
        
        Returns:
            RSI 값 (0-100)
        """
        if len(closes) < period + 1:
            return 50.0  # 기본값
        
        # 가격 변화 계산
        changes = []
        for i in range(period):
            changes.append(closes[i] - closes[i + 1])
        
        # 상승/하락 분리
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return round(rsi, 2)
    
    def _calculate_atr(
        self,
        highs: list[int],
        lows: list[int],
        closes: list[int],
        period: int = 14
    ) -> float:
        """
        ATR (Average True Range) 계산
        
        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            period: ATR 기간
        
        Returns:
            ATR 값
        """
        if len(closes) < period + 1:
            return 0.0
        
        true_ranges = []
        for i in range(period):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i + 1]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        atr = sum(true_ranges) / period
        
        return round(atr, 2)
    
    # ===== 재무 정보 =====
    
    def get_financial_info(self, stock_code: str) -> Optional[dict]:
        """
        재무 정보 조회 (부채비율, 영업이익률 등)
        
        Args:
            stock_code: 종목코드
        
        Returns:
            재무 정보 딕셔너리:
            {
                'code': '005930',
                'debt_ratio': 35.2,  # 부채비율 (%)
                'operating_margin': 12.5,  # 영업이익률 (%)
                'roe': 15.3,  # ROE (%)
                'per': 12.5,
                'pbr': 1.2,
                'eps': 6000,  # 주당순이익
                'bps': 62000  # 주당순자산
            }
        """
        self._rate_limit()
        
        # 주식 기본 조회 (재무 포함)
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        
        tr_id = "FHKST01010100"
        headers = self._get_headers(tr_id)
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        
        try:
            response = self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("rt_cd") != "0":
                return None
            
            output = data.get("output", {})
            
            # KIS API에서 직접 제공하는 재무 정보
            result = {
                "code": stock_code,
                "per": float(output.get("per", 0) or 0),
                "pbr": float(output.get("pbr", 0) or 0),
                "eps": float(output.get("eps", 0) or 0),
                "bps": float(output.get("bps", 0) or 0),
                # 아래 정보는 별도 API나 크롤링 필요
                "debt_ratio": None,  # TODO: 추가 구현 필요
                "operating_margin": None,
                "roe": None
            }
            
            # 추가 재무 정보 조회 시도
            extra_info = self._get_extra_financial(stock_code)
            if extra_info:
                result.update(extra_info)
            
            logger.debug(f"[{stock_code}] 재무 조회: PER {result['per']}, PBR {result['pbr']}")
            
            return result
            
        except Exception as e:
            logger.error(f"[{stock_code}] 재무 조회 실패: {e}")
            return None
    
    def _get_extra_financial(self, stock_code: str) -> Optional[dict]:
        """
        추가 재무 정보 조회 (부채비율, 영업이익률)
        
        Note: KIS API에서 직접 제공하지 않는 정보는
              별도 크롤링이나 DART API가 필요할 수 있습니다.
        """
        # TODO: DART API 연동 또는 네이버 금융 크롤링
        # 일단 기본값 반환
        return {
            "debt_ratio": 100.0,  # 임시 기본값
            "operating_margin": 10.0,  # 임시 기본값
            "roe": 10.0  # 임시 기본값
        }
    
    # ===== 테마별 종목 조회 =====
    
    def get_theme_stocks(self, theme_code: str) -> Optional[list[dict]]:
        """
        테마별 종목 리스트 조회
        
        Args:
            theme_code: 테마 코드
        
        Returns:
            종목 리스트
        """
        # TODO: KIS API 테마 조회 구현
        # 현재는 crawlers.py의 네이버 크롤링 활용
        logger.warning("테마 종목 조회는 crawlers.py를 사용하세요")
        return None
    
    # ===== 종목 종합 정보 =====
    
    def get_stock_full_info(self, stock_code: str) -> Optional[dict]:
        """
        종목 종합 정보 조회 (현재가 + 수급 + 기술적 지표 + 재무)
        
        여러 API를 호출하여 종합 정보를 반환합니다.
        
        Args:
            stock_code: 종목코드
        
        Returns:
            종합 정보 딕셔너리
        """
        result = {"code": stock_code}
        
        # 현재가 조회
        price_info = self.get_current_price(stock_code)
        if price_info:
            result.update(price_info)
        else:
            logger.warning(f"[{stock_code}] 현재가 조회 실패")
            return None
        
        # 수급 조회
        investor_info = self.get_investor_trading(stock_code)
        if investor_info:
            result["foreign_net"] = investor_info["foreign_net"]
            result["institution_net"] = investor_info["institution_net"]
            result["foreign_ratio"] = investor_info.get("foreign_ratio", 0)
        
        # 기술적 지표
        tech_info = self.get_technical_indicators(stock_code)
        if tech_info:
            result["rsi"] = tech_info["rsi"]
            result["atr"] = tech_info["atr"]
            result["volume_ratio"] = tech_info["volume_ratio"]
            result["ma_alignment"] = tech_info["ma_alignment"]
            result["ma_5"] = tech_info.get("ma_5")
            result["ma_20"] = tech_info.get("ma_20")
            result["ma_60"] = tech_info.get("ma_60")
        
        # 재무 정보
        financial_info = self.get_financial_info(stock_code)
        if financial_info:
            result["debt_ratio"] = financial_info.get("debt_ratio")
            result["operating_margin"] = financial_info.get("operating_margin")
        
        logger.info(f"[{stock_code}] 종합 정보 조회 완료")
        
        return result
    
    def close(self):
        """HTTP 클라이언트 종료"""
        if self.client:
            self.client.close()
            logger.debug("KIS API 클라이언트 종료")


# ===== 편의 함수 =====

def get_kis_api() -> KISApi:
    """
    KIS API 인스턴스 반환 (싱글톤 패턴)
    """
    return KISApi()


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 KIS API 테스트")
    print("=" * 60)
    
    kis = KISApi()
    
    print(f"\n모드: {'모의투자' if kis.is_mock else '실전투자'}")
    print(f"API URL: {kis.base_url}")
    
    # API 키 확인
    if not kis.app_key or kis.app_key.startswith("your_"):
        print("\n⚠️  KIS API 키가 설정되지 않았습니다.")
        print("   .env 파일에 실제 API 키를 설정해주세요.")
        print("\n임시 테스트 데이터로 함수 테스트:")
        
        # RSI 계산 테스트
        closes = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 
                  110, 108, 106, 105, 107, 109, 111, 110, 112, 113]
        rsi = kis._calculate_rsi(closes, 14)
        print(f"   RSI 계산 테스트: {rsi:.1f}")
        
        # ATR 계산 테스트
        highs = [102, 104, 103, 105, 107, 106, 108, 110, 109, 111,
                 112, 110, 108, 107, 109, 111, 113, 112, 114, 115]
        lows = [98, 100, 99, 101, 103, 102, 104, 106, 105, 107,
                108, 106, 104, 103, 105, 107, 109, 108, 110, 111]
        atr = kis._calculate_atr(highs, lows, closes, 14)
        print(f"   ATR 계산 테스트: {atr:.1f}")
    else:
        print("\n🔑 API 키 확인됨. 실제 API 테스트...")
        
        try:
            # 토큰 발급 테스트
            token = kis.get_access_token()
            print(f"   토큰 발급: {'성공' if token else '실패'}")
            
            # 삼성전자 현재가 테스트
            test_code = "005930"
            price = kis.get_current_price(test_code)
            if price:
                print(f"\n   📈 {price['name']} ({test_code})")
                print(f"      현재가: {price['price']:,}원")
                print(f"      등락률: {price['change_rate']:+.2f}%")
                print(f"      거래량: {price['volume']:,}주")
            
        except Exception as e:
            print(f"\n   ❌ API 테스트 실패: {e}")
    
    kis.close()
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
