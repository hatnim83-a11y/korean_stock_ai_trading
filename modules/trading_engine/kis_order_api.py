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
import hashlib
import json
from datetime import datetime, date
from typing import Optional

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


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
    
    # ===== 인증 관련 =====
    
    def _rate_limit(self) -> None:
        """API 호출 속도 제한"""
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_time = time.time()
    
    def get_access_token(self) -> str:
        """
        접근 토큰 발급 (24시간 유효)
        
        Returns:
            접근 토큰 문자열
        """
        # 토큰이 유효하면 재사용
        if self.access_token and self.token_expired_at > time.time():
            return self.access_token
        
        url = f"{self.base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        try:
            self._rate_limit()
            response = httpx.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            self.access_token = data["access_token"]
            # 23시간 후 만료로 설정 (여유 확보)
            self.token_expired_at = time.time() + (23 * 60 * 60)
            
            logger.info("KIS API 토큰 발급 성공")
            return self.access_token
            
        except Exception as e:
            logger.error(f"토큰 발급 실패: {e}")
            raise
    
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
            order_date = date.today().strftime("%Y%m%d")
        
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
                        "order_qty": int(item.get("ord_qty", 0)),
                        "filled_qty": int(item.get("tot_ccld_qty", 0)),
                        "order_price": int(item.get("ord_unpr", 0)),
                        "filled_price": int(float(item.get("avg_prvs", 0))),
                        "order_time": item.get("ord_tmd", ""),
                        "order_type": item.get("sll_buy_dvsn_cd_name", ""),
                        "status": "체결" if item.get("ord_qty") == item.get("tot_ccld_qty") else "미체결"
                    })
                
                logger.info(f"주문 상태 조회: {len(orders)}건")
                return orders
            else:
                logger.error(f"주문 상태 조회 실패: {data.get('msg1', '')}")
                return []
                
        except Exception as e:
            logger.error(f"주문 상태 조회 중 오류: {e}")
            return []
    
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
            data = response.json()
            
            rt_cd = data.get("rt_cd", "1")
            
            if rt_cd == "0":
                output1 = data.get("output1", [])  # 종목별
                output2 = data.get("output2", [{}])[0]  # 합계
                
                # 보유 종목
                positions = []
                for item in output1:
                    qty = int(item.get("hldg_qty", 0))
                    if qty > 0:
                        buy_price = int(float(item.get("pchs_avg_pric", 0)))
                        current_price = int(item.get("prpr", 0))
                        profit = int(item.get("evlu_pfls_amt", 0))
                        profit_rate = float(item.get("evlu_pfls_rt", 0))
                        
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
                    "total_value": int(output2.get("tot_evlu_amt", 0)),
                    "cash": int(output2.get("dnca_tot_amt", 0)),
                    "total_buy_amount": int(output2.get("pchs_amt_smtl_amt", 0)),
                    "total_eval_amount": int(output2.get("evlu_amt_smtl_amt", 0)),
                    "total_profit": int(output2.get("evlu_pfls_smtl_amt", 0)),
                    "profit_rate": float(output2.get("tot_evlu_pfls_rt", 0)) if output2.get("tot_evlu_pfls_rt") else 0,
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
            "order_time": datetime.now().strftime("%H%M%S"),
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
            "order_time": datetime.now().strftime("%H%M%S"),
            "stock_code": stock_code,
            "quantity": quantity,
            "price": mock_price,
            "action": "매도",
            "message": "모의 주문 성공"
        }
        self.orders.append(order)
        
        logger.info(f"[모의] 매도 주문: {stock_code} {quantity}주 @ {mock_price:,}원")
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
