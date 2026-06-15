"""
kis_order_api.py - 한국투자증권 주문 API 모듈

이 파일은 KIS API를 통한 주식 매매 주문 기능을 제공합니다.

주요 기능:
- 시장가/지정가 매수 주문
- 시장가/지정가 매도 주문
- 주문 상태 조회
- 주문 취소
- 잔고 조회
- 체결 내역 조회

사용법:
    from modules.trading_engine.kis_order_api import KISOrderApi
    
    api = KISOrderApi()
    order = api.buy_market_order("005930", 10)  # 삼성전자 10주 매수
    api.sell_market_order("005930", 10)  # 삼성전자 10주 매도
"""

import time
import threading
import hashlib
import json
from datetime import datetime, date
from typing import Optional

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst


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


# ===== 상수 정의 =====
# 주문 유형 코드
ORDER_TYPE_MARKET = "01"  # 시장가
ORDER_TYPE_LIMIT = "00"   # 지정가
ORDER_TYPE_CONDITIONAL = "02"  # 조건부지정가

# TR ID (거래 코드)
# 실전투자
TR_BUY_REAL = "TTTC0802U"     # 현금 매수
TR_SELL_REAL = "TTTC0801U"    # 현금 매도
TR_CANCEL_REAL = "TTTC0803U"  # 주문 취소
TR_MODIFY_REAL = "TTTC0804U"  # 주문 정정

# 모의투자
TR_BUY_MOCK = "VTTC0802U"
TR_SELL_MOCK = "VTTC0801U"
TR_CANCEL_MOCK = "VTTC0803U"
TR_MODIFY_MOCK = "VTTC0804U"

# 조회 TR ID
TR_BALANCE = "TTTC8434R"      # 잔고 조회 (실전)
TR_BALANCE_MOCK = "VTTC8434R"  # 잔고 조회 (모의)
TR_ORDER_STATUS = "TTTC8001R"  # 주문 상태 조회 (실전)
TR_ORDER_STATUS_MOCK = "VTTC8001R"
TR_ASKING_PRICE = "FHKST01010200"  # 호가창 조회 (실전/모의 공용)


class KISOrderApi:
    """
    한국투자증권 주문 API 클라이언트
    
    주식 매수/매도 주문 및 조회 기능을 제공합니다.
    
    Attributes:
        is_mock: 모의투자 여부
        base_url: API 기본 URL
        access_token: 접근 토큰
        
    Example:
        >>> api = KISOrderApi()
        >>> # 시장가 매수
        >>> order = api.buy_market_order("005930", 10)
        >>> print(f"주문번호: {order['order_id']}")
        >>> # 잔고 조회
        >>> balance = api.get_balance()
    """
    
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        is_mock: Optional[bool] = None
    ):
        """
        KIS 주문 API 초기화
        
        Args:
            app_key: KIS API 앱 키 (없으면 환경변수에서 로드)
            app_secret: KIS API 앱 시크릿
            account_no: 계좌번호
            is_mock: 모의투자 여부
        """
        self.app_key = app_key or settings.KIS_APP_KEY
        self.app_secret = app_secret or settings.KIS_APP_SECRET
        self.account_no = account_no or settings.KIS_ACCOUNT_NO
        self.is_mock = is_mock if is_mock is not None else settings.IS_MOCK
        
        # 기본 URL 설정
        if self.is_mock:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
            logger.info("KIS 주문 API 초기화 (모의투자)")
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"
            logger.info("KIS 주문 API 초기화 (실전투자)")
        
        # 토큰 관리
        self.access_token: Optional[str] = None
        self.token_expired_at: float = 0
        
        # 해시키 캐시
        self._hashkey_cache: dict = {}
        
        # API 호출 간격 (초당 20회 제한)
        self._last_call_time: float = 0
        self._min_interval: float = 0.06  # 60ms = 초당 약 16회
        self._rate_limit_lock = threading.Lock()  # 재시도 루프 병렬화 대비

    # ===== 인증 관련 =====

    def _rate_limit(self) -> None:
        """API 호출 속도 제한 (스레드 안전)"""
        with self._rate_limit_lock:
            elapsed = time.time() - self._last_call_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_time = time.time()
    
    def get_access_token(self) -> str:
        """
        접근 토큰 발급 (24시간 유효, 최대 3회 재시도)

        KISApi(시세조회)가 이미 발급한 공유 토큰이 있으면 재사용하여
        1분당 1회 토큰 발급 제한 충돌을 방지합니다.

        Returns:
            접근 토큰 문자열
        """
        # 1) 인스턴스 토큰이 유효하면 재사용 (1시간 버퍼)
        if self.access_token and self.token_expired_at > time.time() + 3600:
            return self.access_token

        # 2) KISApi(시세조회) 공유 토큰이 있으면 재사용
        try:
            from modules.stock_screener.kis_api import KISApi
            if KISApi._shared_token and KISApi._shared_token_expired_at > time.time() + 3600:
                self.access_token = KISApi._shared_token
                self.token_expired_at = KISApi._shared_token_expired_at
                logger.info("🔑 KIS 주문 API: 시세조회 공유 토큰 재사용")
                return self.access_token
        except (ImportError, AttributeError):
            pass

        # 3) 새로 발급
        url = f"{self.base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }

        max_retries = 3
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                self._rate_limit()
                response = httpx.post(url, headers=headers, json=body, timeout=10)
                response.raise_for_status()
                data = response.json()

                self.access_token = data["access_token"]
                # 24시간 후 만료로 설정 (KISApi와 동일)
                self.token_expired_at = time.time() + (24 * 60 * 60)

                # KISApi 공유 토큰에도 저장 (시세조회 모듈이 재사용)
                try:
                    from modules.stock_screener.kis_api import KISApi
                    KISApi._shared_token = self.access_token
                    KISApi._shared_token_expired_at = self.token_expired_at
                except (ImportError, AttributeError):
                    pass

                logger.info("KIS API 토큰 발급 성공")
                return self.access_token

            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code
                logger.warning(f"토큰 발급 HTTP {status_code} (시도 {attempt}/{max_retries})")

                if status_code == 403:
                    # 기존 토큰 초기화
                    self.access_token = None
                    self.token_expired_at = 0

                if attempt < max_retries:
                    backoff = 2 ** attempt  # 2초, 4초, 8초
                    logger.info(f"토큰 발급 재시도 대기: {backoff}초")
                    time.sleep(backoff)

            except Exception as e:
                last_exception = e
                logger.warning(f"토큰 발급 실패 (시도 {attempt}/{max_retries}): {e}")

                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.info(f"토큰 발급 재시도 대기: {backoff}초")
                    time.sleep(backoff)

        logger.error(f"토큰 발급 최종 실패 ({max_retries}회 시도): {last_exception}")
        raise last_exception
    
    def _get_hashkey(self, body: dict) -> str:
        """
        해시키 생성 (POST 요청 시 필요)
        
        Args:
            body: 요청 본문
        
        Returns:
            해시키 문자열
        """
        # 캐시 확인
        cache_key = json.dumps(body, sort_keys=True)
        if cache_key in self._hashkey_cache:
            return self._hashkey_cache[cache_key]
        
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        try:
            self._rate_limit()
            response = httpx.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            hashkey = data.get("HASH", "")
            self._hashkey_cache[cache_key] = hashkey
            
            return hashkey
            
        except Exception as e:
            logger.error(f"해시키 생성 실패: {e}")
            return ""
    
    def _get_headers(self, tr_id: str, use_hashkey: bool = False) -> dict:
        """공통 헤더 생성"""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P"  # 개인
        }
    
    # ===== 매수 주문 =====
    
    def buy_market_order(
        self,
        stock_code: str,
        quantity: int
    ) -> dict:
        """
        시장가 매수 주문
        
        Args:
            stock_code: 종목코드 (예: "005930")
            quantity: 매수 수량
        
        Returns:
            {
                'success': True,
                'order_id': '0000123456',
                'order_time': '093015',
                'stock_code': '005930',
                'quantity': 10,
                'message': '주문 성공'
            }
            
        Example:
            >>> order = api.buy_market_order("005930", 10)
            >>> print(f"주문번호: {order['order_id']}")
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            price=0,  # 시장가는 0
            order_type=ORDER_TYPE_MARKET,
            is_buy=True
        )
    
    def buy_limit_order(
        self,
        stock_code: str,
        quantity: int,
        price: int
    ) -> dict:
        """
        지정가 매수 주문
        
        Args:
            stock_code: 종목코드
            quantity: 매수 수량
            price: 매수 가격
        
        Returns:
            주문 결과 딕셔너리
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            order_type=ORDER_TYPE_LIMIT,
            is_buy=True
        )
    
    # ===== 매도 주문 =====
    
    def sell_market_order(
        self,
        stock_code: str,
        quantity: int
    ) -> dict:
        """
        시장가 매도 주문
        
        Args:
            stock_code: 종목코드
            quantity: 매도 수량
        
        Returns:
            주문 결과 딕셔너리
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            price=0,
            order_type=ORDER_TYPE_MARKET,
            is_buy=False
        )
    
    def sell_limit_order(
        self,
        stock_code: str,
        quantity: int,
        price: int
    ) -> dict:
        """
        지정가 매도 주문
        
        Args:
            stock_code: 종목코드
            quantity: 매도 수량
            price: 매도 가격
        
        Returns:
            주문 결과 딕셔너리
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            order_type=ORDER_TYPE_LIMIT,
            is_buy=False
        )
    
    # ===== 주문 실행 (내부) =====
    
    def _place_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        order_type: str,
        is_buy: bool
    ) -> dict:
        """
        주문 실행 (내부 함수)
        
        Args:
            stock_code: 종목코드
            quantity: 수량
            price: 가격 (시장가는 0)
            order_type: 주문 유형 (00: 지정가, 01: 시장가)
            is_buy: 매수 여부
        
        Returns:
            주문 결과
        """
        # TR ID 선택
        if self.is_mock:
            tr_id = TR_BUY_MOCK if is_buy else TR_SELL_MOCK
        else:
            tr_id = TR_BUY_REAL if is_buy else TR_SELL_REAL
        
        action = "매수" if is_buy else "매도"
        order_type_name = "시장가" if order_type == ORDER_TYPE_MARKET else "지정가"
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        
        # 계좌번호 분리 (8자리 + 2자리)
        # 계좌번호에서 CANO와 ACNT_PRDT_CD 추출 (하이픈 처리)
        if "-" in self.account_no:
            cano, acnt_prdt_cd = self.account_no.split("-")
        else:
            cano = self.account_no[:8]
            acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price)
        }
        
        headers = self._get_headers(tr_id)
        headers["hashkey"] = self._get_hashkey(body)
        
        try:
            self._rate_limit()
            response = httpx.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()

            # 성공 여부 확인
            rt_cd = data.get("rt_cd", "1")
            msg = data.get("msg1", "알 수 없는 오류")
            
            if rt_cd == "0":
                output = data.get("output", {})
                result = {
                    "success": True,
                    "order_id": output.get("ODNO", ""),
                    "order_time": output.get("ORD_TMD", ""),
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "price": price,
                    "order_type": order_type_name,
                    "action": action,
                    "message": msg
                }
                logger.info(f"✅ {action} 주문 성공: {stock_code} {quantity}주 ({order_type_name})")
                logger.info(f"   주문번호: {result['order_id']}")
                return result
            else:
                logger.error(f"❌ {action} 주문 실패: {stock_code} - {msg}")
                return {
                    "success": False,
                    "order_id": "",
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "message": msg
                }
                
        except Exception as e:
            logger.error(f"주문 실행 중 오류: {e}")
            return {
                "success": False,
                "order_id": "",
                "stock_code": stock_code,
                "quantity": quantity,
                "message": str(e)
            }
    
    # ===== 주문 취소/정정 =====
    
    def cancel_order(
        self,
        order_id: str,
        stock_code: str,
        quantity: int
    ) -> dict:
        """
        주문 취소
        
        Args:
            order_id: 원 주문번호
            stock_code: 종목코드
            quantity: 취소 수량 (전량 취소 시 원 주문 수량)
        
        Returns:
            취소 결과
        """
        tr_id = TR_CANCEL_MOCK if self.is_mock else TR_CANCEL_REAL
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        
        # 계좌번호에서 CANO와 ACNT_PRDT_CD 추출 (하이픈 처리)
        if "-" in self.account_no:
            cano, acnt_prdt_cd = self.account_no.split("-")
        else:
            cano = self.account_no[:8]
            acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"
        
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y"  # 전량
        }
        
        headers = self._get_headers(tr_id)
        headers["hashkey"] = self._get_hashkey(body)
        
        try:
            self._rate_limit()
            response = httpx.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()

            rt_cd = data.get("rt_cd", "1")
            msg = data.get("msg1", "")

            if rt_cd == "0":
                logger.info(f"✅ 주문 취소 성공: {order_id}")
                return {"success": True, "order_id": order_id, "message": msg}
            else:
                logger.error(f"❌ 주문 취소 실패: {order_id} - {msg}")
                return {"success": False, "order_id": order_id, "message": msg}

        except Exception as e:
            logger.error(f"주문 취소 중 오류: {e}")
            return {"success": False, "order_id": order_id, "message": str(e)}
    
    # ===== 조회 =====
    
    def get_order_status(
        self,
        order_id: Optional[str] = None,
        order_date: Optional[str] = None
    ) -> list[dict]:
        """
        주문 상태 조회
        
        Args:
            order_id: 주문번호 (없으면 당일 전체)
            order_date: 주문일자 (YYYYMMDD, 없으면 오늘)
        
        Returns:
            주문 상태 리스트
        """
        tr_id = TR_ORDER_STATUS_MOCK if self.is_mock else TR_ORDER_STATUS
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        
        if order_date is None:
            order_date = now_kst().strftime("%Y%m%d")
        
        # 계좌번호에서 CANO와 ACNT_PRDT_CD 추출 (하이픈 처리)
        if "-" in self.account_no:
            cano, acnt_prdt_cd = self.account_no.split("-")
        else:
            cano = self.account_no[:8]
            acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": order_date,
            "INQR_END_DT": order_date,
            "SLL_BUY_DVSN_CD": "00",  # 전체
            "INQR_DVSN": "01",  # 역순
            "PDNO": "",
            "CCLD_DVSN": "00",  # 전체
            "ORD_GNO_BRNO": "",
            "ODNO": order_id or "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        headers = self._get_headers(tr_id)
        
        try:
            self._rate_limit()
            response = httpx.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            rt_cd = data.get("rt_cd", "1")

            if rt_cd == "0":
                output = data.get("output1", [])
                orders = []
                
                for item in output:
                    orders.append({
                        "order_id": item.get("odno", ""),
                        "stock_code": item.get("pdno", ""),
                        "stock_name": item.get("prdt_name", ""),
                        "order_qty": _safe_int(item.get("ord_qty")),
                        "filled_qty": _safe_int(item.get("tot_ccld_qty")),
                        "order_price": _safe_int(item.get("ord_unpr")),
                        "filled_price": _safe_int(_safe_float(item.get("avg_prvs"))),
                        "order_time": item.get("ord_tmd", ""),
                        "order_type": item.get("sll_buy_dvsn_cd_name", ""),
                        "status": "체결" if (
                            _safe_int(item.get("ord_qty")) > 0 and
                            _safe_int(item.get("ord_qty")) == _safe_int(item.get("tot_ccld_qty"))
                        ) else "미체결"
                    })
                
                logger.info(f"주문 상태 조회: {len(orders)}건")
                return orders
            else:
                logger.error(f"주문 상태 조회 실패: {data.get('msg1', '')}")
                return []
                
        except Exception as e:
            logger.error(f"주문 상태 조회 중 오류: {e}")
            return []

    def get_orderable_cash(self) -> int:
        """
        주문가능현금 조회 (매수가능조회 API)

        예수금과 달리 실제 주문에 사용할 수 있는 금액을 반환합니다.
        (D+2 미결제, 수수료 등 반영)

        Returns:
            주문가능현금 (원), 실패 시 0
        """
        tr_id = "VTTC8908R" if self.is_mock else "TTTC8908R"

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"

        if "-" in self.account_no:
            cano, acnt_prdt_cd = self.account_no.split("-")
        else:
            cano = self.account_no[:8]
            acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": "005930",  # 조회용 더미 종목 (삼성전자)
            "ORD_UNPR": "0",   # 시장가
            "ORD_DVSN": "01",  # 시장가
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }

        headers = self._get_headers(tr_id)

        try:
            self._rate_limit()
            response = httpx.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("rt_cd") == "0":
                output = data.get("output", {})
                orderable = _safe_int(output.get("ord_psbl_cash"))
                nrcvb = _safe_int(output.get("nrcvb_buy_amt"))
                # 미수 없는 매수금액이 더 보수적 → 이 값 사용
                result = nrcvb if nrcvb > 0 else orderable
                logger.info(f"주문가능현금 조회: {result:,}원 (예수금 기준 주문가능: {orderable:,}원)")
                return result
            else:
                logger.warning(f"주문가능현금 조회 실패: {data.get('msg1', '')}")
                return 0
        except Exception as e:
            logger.error(f"주문가능현금 조회 중 오류: {e}")
            return 0

    def get_balance(self) -> dict:
        """
        잔고 조회
        
        Returns:
            {
                'total_value': 10500000,      # 총 평가금액
                'cash': 1000000,              # 예수금
                'total_profit': 500000,       # 총 평가손익
                'profit_rate': 5.0,           # 수익률 (%)
                'positions': [                 # 보유 종목
                    {
                        'stock_code': '005930',
                        'stock_name': '삼성전자',
                        'quantity': 100,
                        'buy_price': 70000,
                        'current_price': 75000,
                        'profit': 500000,
                        'profit_rate': 7.14
                    },
                    ...
                ]
            }
        """
        tr_id = TR_BALANCE_MOCK if self.is_mock else TR_BALANCE
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        
        # 계좌번호에서 CANO와 ACNT_PRDT_CD 추출 (하이픈 처리)
        if "-" in self.account_no:
            cano, acnt_prdt_cd = self.account_no.split("-")
        else:
            cano = self.account_no[:8]
            acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        headers = self._get_headers(tr_id)
        
        try:
            self._rate_limit()
            response = httpx.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            rt_cd = data.get("rt_cd", "1")

            if rt_cd == "0":
                output1 = data.get("output1", [])  # 종목별
                output2_list = data.get("output2") or [{}]
                output2 = output2_list[0] if output2_list else {}

                # 보유 종목
                positions = []
                for item in output1:
                    qty = _safe_int(item.get("hldg_qty"))
                    if qty > 0:
                        buy_price = _safe_int(_safe_float(item.get("pchs_avg_pric")))
                        current_price = _safe_int(item.get("prpr"))
                        profit = _safe_int(item.get("evlu_pfls_amt"))
                        profit_rate = _safe_float(item.get("evlu_pfls_rt"))

                        positions.append({
                            "stock_code": item.get("pdno", ""),
                            "stock_name": item.get("prdt_name", ""),
                            "quantity": qty,
                            "buy_price": buy_price,
                            "current_price": current_price,
                            "buy_amount": qty * buy_price,
                            "current_amount": qty * current_price,
                            "profit": profit,
                            "profit_rate": profit_rate
                        })

                # 총계
                result = {
                    "total_value": _safe_int(output2.get("tot_evlu_amt")),
                    "cash": _safe_int(output2.get("dnca_tot_amt")),
                    "total_buy_amount": _safe_int(output2.get("pchs_amt_smtl_amt")),
                    "total_eval_amount": _safe_int(output2.get("evlu_amt_smtl_amt")),
                    "total_profit": _safe_int(output2.get("evlu_pfls_smtl_amt")),
                    "profit_rate": _safe_float(output2.get("tot_evlu_pfls_rt")),
                    "positions": positions,
                    "position_count": len(positions)
                }
                
                logger.info(f"잔고 조회: {len(positions)}개 종목, 총 평가액 {result['total_value']:,}원")
                return result
            else:
                logger.error(f"잔고 조회 실패: {data.get('msg1', '')}")
                return {"positions": [], "total_value": 0, "cash": 0}
                
        except Exception as e:
            logger.error(f"잔고 조회 중 오류: {e}")
            return {"positions": [], "total_value": 0, "cash": 0}

    # ===== 호가 조회 =====

    def inquire_asking_price(self, stock_code: str) -> dict:
        """
        호가창(매도/매수 1~10단계) 조회

        공격적 지정가 주문 시 매도 1호가를 가격으로 사용하기 위해 호출한다.
        최유리지정가(ORD_DVSN=03)가 1.3배 증거금을 적용하는 문제를 우회하기 위한 보조 API.

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            {
                "ask1": int,          # 매도 1호가 (가장 낮은 매도 희망가)
                "bid1": int,          # 매수 1호가
                "ask_volume1": int,   # 매도 1호가 잔량
                "bid_volume1": int,   # 매수 1호가 잔량
                "current_price": int, # 현재가 (폴백용)
                "success": bool,
                "message": str
            }
            실패 시 success=False, 가격 0
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 주식
            "FID_INPUT_ISCD": stock_code,
        }

        headers = self._get_headers(TR_ASKING_PRICE)

        try:
            self._rate_limit()
            response = httpx.get(url, headers=headers, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()

            if data.get("rt_cd") != "0":
                logger.warning(f"호가 조회 실패 ({stock_code}): {data.get('msg1', '')}")
                return {
                    "success": False,
                    "ask1": 0, "bid1": 0,
                    "ask_volume1": 0, "bid_volume1": 0,
                    "current_price": 0,
                    "message": data.get("msg1", "호가 조회 실패"),
                }

            # output1: 10단계 호가, output2: 현재가 등
            output1 = data.get("output1", {})
            output2 = data.get("output2", {})

            return {
                "success": True,
                "ask1": _safe_int(output1.get("askp1")),
                "bid1": _safe_int(output1.get("bidp1")),
                "ask_volume1": _safe_int(output1.get("askp_rsqn1")),
                "bid_volume1": _safe_int(output1.get("bidp_rsqn1")),
                "current_price": _safe_int(output2.get("stck_prpr")),
                "message": "",
            }
        except Exception as e:
            logger.error(f"호가 조회 중 오류 ({stock_code}): {e}")
            return {
                "success": False,
                "ask1": 0, "bid1": 0,
                "ask_volume1": 0, "bid_volume1": 0,
                "current_price": 0,
                "message": str(e),
            }

    # ===== 일괄 주문 =====

    def execute_buy_orders(
        self,
        orders: list[dict],
        delay: float = 0.5
    ) -> list[dict]:
        """
        매수 주문 일괄 실행
        
        Args:
            orders: 주문 리스트 [{'stock_code': '005930', 'quantity': 10}, ...]
            delay: 주문 간 딜레이 (초)
        
        Returns:
            실행 결과 리스트
        """
        results = []
        
        for i, order in enumerate(orders, 1):
            stock_code = order.get("stock_code")
            quantity = order.get("quantity", 0)
            price = order.get("price", 0)
            order_type = order.get("order_type", "market")
            
            logger.info(f"[{i}/{len(orders)}] 매수 주문: {stock_code} {quantity}주")
            
            if order_type == "market" or price == 0:
                result = self.buy_market_order(stock_code, quantity)
            else:
                result = self.buy_limit_order(stock_code, quantity, price)
            
            results.append(result)
            
            # 주문 간 딜레이
            if i < len(orders):
                time.sleep(delay)
        
        # 결과 요약
        success_count = sum(1 for r in results if r.get("success"))
        logger.info(f"매수 주문 완료: {success_count}/{len(orders)}건 성공")
        
        return results
    
    def execute_sell_orders(
        self,
        orders: list[dict],
        delay: float = 0.5
    ) -> list[dict]:
        """
        매도 주문 일괄 실행
        
        Args:
            orders: 주문 리스트
            delay: 주문 간 딜레이 (초)
        
        Returns:
            실행 결과 리스트
        """
        results = []
        
        for i, order in enumerate(orders, 1):
            stock_code = order.get("stock_code")
            quantity = order.get("quantity", 0)
            price = order.get("price", 0)
            order_type = order.get("order_type", "market")
            
            logger.info(f"[{i}/{len(orders)}] 매도 주문: {stock_code} {quantity}주")
            
            if order_type == "market" or price == 0:
                result = self.sell_market_order(stock_code, quantity)
            else:
                result = self.sell_limit_order(stock_code, quantity, price)
            
            results.append(result)
            
            if i < len(orders):
                time.sleep(delay)
        
        success_count = sum(1 for r in results if r.get("success"))
        logger.info(f"매도 주문 완료: {success_count}/{len(orders)}건 성공")
        
        return results


# ===== 모의 주문 API (테스트용) =====

class MockOrderApi:
    """
    모의 주문 API (테스트용)
    
    실제 API를 호출하지 않고 주문을 시뮬레이션합니다.
    """
    
    def __init__(self):
        self.orders: list[dict] = []
        self.positions: dict = {}
        self.cash = 10_000_000
        self._order_id_counter = 1000
        # 지정가 재시도 루프 테스트용 시나리오 제어
        self._pending_orders: dict = {}  # order_id -> {stock_code, qty, price, filled_qty, filled_price}
        self.scenario_fill_ratio: float = 1.0  # 0.0 ~ 1.0 (buy_limit_order 1회 호출당 체결 비율)
        self.scenario_ask1_offset: int = 0  # inquire_asking_price의 ask1에 더할 오프셋 (급등 시뮬레이션)
        self.scenario_cancel_race_qty: int = 0  # 취소 직전 추가 체결 수량 (엣지케이스)
        self.scenario_next_error: Optional[str] = None  # 다음 주문에서 발생시킬 에러 메시지
        logger.info("모의 주문 API 초기화")

    def _generate_order_id(self) -> str:
        self._order_id_counter += 1
        return str(self._order_id_counter)
    
    def buy_market_order(self, stock_code: str, quantity: int) -> dict:
        order_id = self._generate_order_id()
        
        # 모의 가격 (실제로는 현재가 조회 필요)
        mock_price = 75000
        amount = mock_price * quantity
        
        if amount > self.cash:
            return {
                "success": False,
                "order_id": "",
                "stock_code": stock_code,
                "message": "잔고 부족"
            }
        
        self.cash -= amount
        
        if stock_code in self.positions:
            self.positions[stock_code]["quantity"] += quantity
        else:
            self.positions[stock_code] = {
                "quantity": quantity,
                "buy_price": mock_price
            }
        
        order = {
            "success": True,
            "order_id": order_id,
            "order_time": now_kst().strftime("%H%M%S"),
            "stock_code": stock_code,
            "quantity": quantity,
            "price": mock_price,
            "action": "매수",
            "message": "모의 주문 성공"
        }
        self.orders.append(order)
        
        logger.info(f"[모의] 매수 주문: {stock_code} {quantity}주 @ {mock_price:,}원")
        return order
    
    def sell_market_order(self, stock_code: str, quantity: int) -> dict:
        order_id = self._generate_order_id()
        
        if stock_code not in self.positions:
            return {
                "success": False,
                "order_id": "",
                "stock_code": stock_code,
                "message": "보유 종목 없음"
            }
        
        pos = self.positions[stock_code]
        if pos["quantity"] < quantity:
            return {
                "success": False,
                "order_id": "",
                "stock_code": stock_code,
                "message": "수량 부족"
            }
        
        mock_price = 76000
        amount = mock_price * quantity
        
        self.cash += amount
        pos["quantity"] -= quantity
        
        if pos["quantity"] == 0:
            del self.positions[stock_code]
        
        order = {
            "success": True,
            "order_id": order_id,
            "order_time": now_kst().strftime("%H%M%S"),
            "stock_code": stock_code,
            "quantity": quantity,
            "price": mock_price,
            "filled_price": mock_price,
            "action": "매도",
            "message": "모의 주문 성공"
        }
        self.orders.append(order)

        logger.info(f"[모의] 매도 주문: {stock_code} {quantity}주 @ {mock_price:,}원")
        return order

    def sell_limit_order(self, stock_code: str, quantity: int, price: int) -> dict:
        """모의 지정가 매도 — scenario_fill_ratio 기반 부분체결 시뮬 (지정가 폴백 테스트용).

        buy_limit_order 와 대칭: 체결분만큼 포지션 차감 + 현금 가산, _pending_orders 등록
        (get_order_status/fill_checker 가 부분체결/미체결을 조회). scenario_next_error 1회성 소비.
        """
        if self.scenario_next_error:
            err = self.scenario_next_error
            self.scenario_next_error = None  # 1회성 소비
            return {"success": False, "order_id": "", "stock_code": stock_code, "message": err}

        if stock_code not in self.positions:
            return {"success": False, "order_id": "", "stock_code": stock_code, "message": "보유 종목 없음"}

        pos = self.positions[stock_code]
        if pos["quantity"] < quantity:
            return {"success": False, "order_id": "", "stock_code": stock_code, "message": "수량 부족"}

        order_id = self._generate_order_id()
        filled_qty = int(quantity * self.scenario_fill_ratio)
        filled_price = price if filled_qty > 0 else 0

        # 체결된 수량만큼 포지션 차감 / 현금 가산
        if filled_qty > 0:
            self.cash += filled_price * filled_qty
            pos["quantity"] -= filled_qty
            if pos["quantity"] == 0:
                del self.positions[stock_code]

        self._pending_orders[order_id] = {
            "stock_code": stock_code,
            "quantity": quantity,
            "price": price,
            "filled_qty": filled_qty,
            "filled_price": filled_price,
        }

        order = {
            "success": True,
            "order_id": order_id,
            "order_time": now_kst().strftime("%H%M%S"),
            "stock_code": stock_code,
            "quantity": quantity,
            "price": price,
            "action": "매도",
            "order_type": "지정가",
            "message": "모의 지정가 매도 접수",
        }
        self.orders.append(order)
        logger.info(
            f"[모의] 지정가 매도: {stock_code} {quantity}주 @ {price:,}원 "
            f"(시나리오 체결 {filled_qty}주, {self.scenario_fill_ratio:.0%})"
        )
        return order

    def get_balance(self) -> dict:
        positions = []
        total_value = self.cash
        
        for code, pos in self.positions.items():
            current_price = pos["buy_price"] * 1.02  # 2% 상승 가정
            value = pos["quantity"] * current_price
            total_value += value
            
            positions.append({
                "stock_code": code,
                "quantity": pos["quantity"],
                "buy_price": pos["buy_price"],
                "current_price": int(current_price),
                "profit_rate": 2.0
            })
        
        return {
            "total_value": int(total_value),
            "cash": self.cash,
            "positions": positions,
            "position_count": len(positions)
        }
    
    def get_order_status(self, order_id: Optional[str] = None, order_date: Optional[str] = None) -> list[dict]:
        """모의 주문 상태 조회 — _pending_orders에서 해당 order_id를 찾아 반환"""
        if order_id and order_id in self._pending_orders:
            po = self._pending_orders[order_id]
            return [{
                "order_id": order_id,
                "stock_code": po["stock_code"],
                "order_qty": po["quantity"],
                "filled_qty": po["filled_qty"],
                "order_price": po["price"],
                "filled_price": po["filled_price"],
                "status": "체결" if po["filled_qty"] >= po["quantity"] else "미체결",
            }]
        return []

    def inquire_asking_price(self, stock_code: str) -> dict:
        """모의 호가 조회 — scenario_ask1_offset으로 급등 시뮬 가능"""
        base_price = 75000 + self.scenario_ask1_offset
        return {
            "success": True,
            "ask1": base_price,
            "bid1": base_price - 100,
            "ask_volume1": 1000,
            "bid_volume1": 1000,
            "current_price": base_price - 50,
            "message": "",
        }

    def buy_limit_order(self, stock_code: str, quantity: int, price: int) -> dict:
        """
        모의 지정가 매수 — scenario_fill_ratio 기반 부분체결 시뮬레이션

        시나리오:
        - scenario_next_error가 설정되어 있으면 해당 에러 반환 (수량/증거금 시나리오용)
        - scenario_fill_ratio만큼만 즉시 체결, 나머지는 _pending_orders에 미체결로 대기
        - 체결된 수량은 positions/cash에 즉시 반영
        """
        if self.scenario_next_error:
            err = self.scenario_next_error
            self.scenario_next_error = None  # 1회성 소비
            return {
                "success": False,
                "order_id": "",
                "stock_code": stock_code,
                "message": err,
            }

        order_id = self._generate_order_id()
        filled_qty = int(quantity * self.scenario_fill_ratio)
        filled_price = price if filled_qty > 0 else 0

        # 체결된 수량만큼 포지션/현금 반영
        if filled_qty > 0:
            amount = filled_price * filled_qty
            if amount > self.cash:
                return {
                    "success": False,
                    "order_id": "",
                    "stock_code": stock_code,
                    "message": "주문가능금액 초과",
                }
            self.cash -= amount
            if stock_code in self.positions:
                self.positions[stock_code]["quantity"] += filled_qty
            else:
                self.positions[stock_code] = {
                    "quantity": filled_qty,
                    "buy_price": filled_price,
                }

        self._pending_orders[order_id] = {
            "stock_code": stock_code,
            "quantity": quantity,
            "price": price,
            "filled_qty": filled_qty,
            "filled_price": filled_price,
        }

        order = {
            "success": True,
            "order_id": order_id,
            "order_time": now_kst().strftime("%H%M%S"),
            "stock_code": stock_code,
            "quantity": quantity,
            "price": price,
            "action": "매수",
            "order_type": "지정가",
            "message": "모의 지정가 주문 접수",
        }
        self.orders.append(order)
        logger.info(
            f"[모의] 지정가 매수: {stock_code} {quantity}주 @ {price:,}원 "
            f"(시나리오 체결 {filled_qty}주, {self.scenario_fill_ratio:.0%})"
        )
        return order

    def cancel_order(self, order_id: str, stock_code: str, quantity: int) -> dict:
        """
        모의 주문 취소 — scenario_cancel_race_qty로 취소 직전 추가 체결 시뮬 가능
        """
        if order_id not in self._pending_orders:
            return {
                "success": False,
                "order_id": order_id,
                "stock_code": stock_code,
                "message": "주문번호를 찾을 수 없음",
            }

        po = self._pending_orders[order_id]

        # 이미 전량 체결된 상태에서 취소 요청 → KIS 에러 시뮬레이션
        if po["filled_qty"] >= po["quantity"]:
            return {
                "success": False,
                "order_id": order_id,
                "stock_code": stock_code,
                "message": "취소가능수량 초과 (이미 전량 체결)",
            }

        # 취소 직전 추가 체결 시나리오 (race condition 검증용)
        if self.scenario_cancel_race_qty > 0:
            extra_fill = min(self.scenario_cancel_race_qty, po["quantity"] - po["filled_qty"])
            po["filled_qty"] += extra_fill
            # 포지션 반영
            if extra_fill > 0:
                amount = po["price"] * extra_fill
                self.cash -= amount
                if stock_code in self.positions:
                    self.positions[stock_code]["quantity"] += extra_fill
                else:
                    self.positions[stock_code] = {
                        "quantity": extra_fill,
                        "buy_price": po["price"],
                    }
            self.scenario_cancel_race_qty = 0  # 1회성 소비

        logger.info(
            f"[모의] 취소 성공: {order_id} {stock_code} (체결 {po['filled_qty']}/{po['quantity']})"
        )
        return {
            "success": True,
            "order_id": order_id,
            "stock_code": stock_code,
            "message": "취소 성공",
        }

    def execute_buy_orders(self, orders: list[dict], delay: float = 0) -> list[dict]:
        results = []
        for order in orders:
            result = self.buy_market_order(
                order.get("stock_code"),
                order.get("quantity", 0)
            )
            results.append(result)
        return results


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 KIS 주문 API 테스트 (모의)")
    print("=" * 60)
    
    # 모의 API 테스트
    api = MockOrderApi()
    
    # 매수 테스트
    print("\n1️⃣ 매수 주문 테스트:")
    buy_result = api.buy_market_order("005930", 10)
    print(f"   결과: {'성공' if buy_result['success'] else '실패'}")
    print(f"   주문번호: {buy_result['order_id']}")
    
    # 잔고 확인
    print("\n2️⃣ 잔고 조회:")
    balance = api.get_balance()
    print(f"   총 평가액: {balance['total_value']:,}원")
    print(f"   보유 현금: {balance['cash']:,}원")
    print(f"   보유 종목: {balance['position_count']}개")
    
    # 매도 테스트
    print("\n3️⃣ 매도 주문 테스트:")
    sell_result = api.sell_market_order("005930", 5)
    print(f"   결과: {'성공' if sell_result['success'] else '실패'}")
    
    # 일괄 주문 테스트
    print("\n4️⃣ 일괄 매수 테스트:")
    orders = [
        {"stock_code": "005930", "quantity": 5},
        {"stock_code": "000660", "quantity": 3}
    ]
    results = api.execute_buy_orders(orders)
    success_count = sum(1 for r in results if r.get("success"))
    print(f"   결과: {success_count}/{len(orders)}건 성공")
    
    print("\n" + "=" * 60)
    print("✅ KIS 주문 API 테스트 완료!")
    print("=" * 60)
