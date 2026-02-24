"""
kis_websocket.py - 한국투자증권 실시간 시세 WebSocket 모듈

이 파일은 KIS WebSocket API를 통한 실시간 시세 수신 기능을 제공합니다.

주요 기능:
- 실시간 시세 구독
- 체결가 모니터링
- 호가 데이터 수신
- 자동 재연결
- 콜백 기반 이벤트 처리

사용법:
    from modules.trading_engine.kis_websocket import KISWebSocket
    
    ws = KISWebSocket()
    ws.subscribe(["005930", "000660"])
    ws.on_price_update = my_callback
    await ws.start()
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Callable, Any
from dataclasses import dataclass, field

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst

# websockets 라이브러리 임포트
try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets 라이브러리가 설치되지 않았습니다")


# ===== 상수 정의 =====
# WebSocket URL
# NOTE: KIS 공식 API는 ws:// 만 제공 (wss:// 미지원, 2026-02 확인)
# 데이터 자체는 AES256으로 암호화되어 전송됨 (approval_key 기반)
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_MOCK = "ws://ops.koreainvestment.com:31000"

# TR 코드
TR_PRICE = "H0STCNT0"      # 실시간 체결가
TR_ORDERBOOK = "H0STASP0"  # 실시간 호가
TR_NOTICE = "H0STCNI0"     # 체결 통보

# 최대 구독 종목 수
MAX_SUBSCRIPTIONS = 40


@dataclass
class PriceData:
    """실시간 체결가 데이터"""
    stock_code: str
    stock_name: str = ""
    current_price: int = 0
    change: int = 0
    change_rate: float = 0.0
    volume: int = 0
    trade_time: str = ""
    high_price: int = 0
    low_price: int = 0
    open_price: int = 0
    prev_close: int = 0
    
    def __post_init__(self):
        # 등락률 계산
        if self.prev_close > 0 and self.change_rate == 0:
            self.change_rate = (self.change / self.prev_close) * 100


@dataclass
class OrderbookData:
    """실시간 호가 데이터"""
    stock_code: str
    bids: list = field(default_factory=list)  # 매수 호가
    asks: list = field(default_factory=list)  # 매도 호가
    total_bid_volume: int = 0
    total_ask_volume: int = 0
    timestamp: str = ""


class KISWebSocket:
    """
    한국투자증권 실시간 시세 WebSocket 클라이언트
    
    실시간 체결가, 호가 데이터를 수신합니다.
    
    Attributes:
        is_mock: 모의투자 여부
        subscriptions: 구독 중인 종목 코드
        on_price_update: 체결가 업데이트 콜백
        on_orderbook_update: 호가 업데이트 콜백
        
    Example:
        >>> ws = KISWebSocket()
        >>> ws.subscribe(["005930", "000660"])
        >>> ws.on_price_update = lambda data: print(f"{data.stock_code}: {data.current_price}")
        >>> await ws.start()
    """
    
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        is_mock: Optional[bool] = None
    ):
        """
        WebSocket 클라이언트 초기화
        
        Args:
            app_key: KIS API 앱 키
            app_secret: KIS API 앱 시크릿
            is_mock: 모의투자 여부
        """
        self.app_key = app_key or settings.KIS_APP_KEY
        self.app_secret = app_secret or settings.KIS_APP_SECRET
        self.is_mock = is_mock if is_mock is not None else settings.IS_MOCK
        
        # WebSocket URL
        self.ws_url = WS_URL_MOCK if self.is_mock else WS_URL_REAL
        
        # 구독 종목
        self.subscriptions: set[str] = set()
        
        # 콜백 함수
        self.on_price_update: Optional[Callable[[PriceData], None]] = None
        self.on_orderbook_update: Optional[Callable[[OrderbookData], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        
        # 상태
        self._ws = None
        self._running = False
        self._approval_key: Optional[str] = None
        self._reconnect_count = 0
        self._max_reconnect = 5
        self._heartbeat_task = None
        
        # 가격 캐시 (종목별 최신 가격)
        self.price_cache: dict[str, PriceData] = {}
        
        logger.info(f"KIS WebSocket 초기화 ({'모의' if self.is_mock else '실전'})")
    
    # ===== 인증 =====
    
    def _get_approval_key(self) -> str:
        """
        WebSocket 접속 승인키 발급
        
        Returns:
            승인키 문자열
        """
        if self._approval_key:
            return self._approval_key
        
        base_url = "https://openapivts.koreainvestment.com:29443" if self.is_mock else "https://openapi.koreainvestment.com:9443"
        url = f"{base_url}/oauth2/Approval"
        
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }
        
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            self._approval_key = data.get("approval_key", "")
            logger.info("WebSocket 승인키 발급 성공")
            return self._approval_key
            
        except Exception as e:
            logger.error(f"승인키 발급 실패: {e}")
            raise
    
    # ===== 구독 관리 =====
    
    def subscribe(self, stock_codes: list[str]) -> None:
        """
        종목 구독 추가
        
        Args:
            stock_codes: 종목코드 리스트
        """
        for code in stock_codes:
            if len(self.subscriptions) >= MAX_SUBSCRIPTIONS:
                logger.warning(f"최대 구독 수({MAX_SUBSCRIPTIONS}) 초과")
                break
            self.subscriptions.add(code)
        
        logger.info(f"구독 종목: {len(self.subscriptions)}개")
    
    def unsubscribe(self, stock_codes: list[str]) -> None:
        """
        종목 구독 해제
        
        Args:
            stock_codes: 종목코드 리스트
        """
        for code in stock_codes:
            self.subscriptions.discard(code)
        
        logger.info(f"구독 종목: {len(self.subscriptions)}개")
    
    def clear_subscriptions(self) -> None:
        """모든 구독 해제"""
        self.subscriptions.clear()
        logger.info("모든 구독 해제")
    
    # ===== WebSocket 연결 =====
    
    async def start(self) -> None:
        """
        WebSocket 연결 시작
        
        연결 후 구독 종목의 실시간 시세를 수신합니다.
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets 라이브러리가 필요합니다")
            return
        
        if not self.subscriptions:
            logger.warning("구독 종목이 없습니다")
            return
        
        self._running = True
        
        while self._running and self._reconnect_count < self._max_reconnect:
            try:
                await self._connect_and_run()
            except Exception as e:
                self._reconnect_count += 1
                logger.warning(f"WebSocket 연결 끊김 (재시도 {self._reconnect_count}/{self._max_reconnect}): {e}")
                
                if self.on_error:
                    self.on_error(e)
                
                if self._running:
                    await asyncio.sleep(5)  # 5초 대기 후 재연결
        
        if self._reconnect_count >= self._max_reconnect:
            logger.error("최대 재연결 횟수 초과")
    
    async def _connect_and_run(self) -> None:
        """WebSocket 연결 및 실행"""
        approval_key = self._get_approval_key()
        
        async with websockets.connect(
            self.ws_url,
            ping_interval=30,
            ping_timeout=10
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0
            logger.info(f"WebSocket 연결 성공: {self.ws_url}")
            
            if self.on_connect:
                self.on_connect()
            
            # 구독 요청
            await self._subscribe_all()
            
            # 하트비트 시작
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
            
            try:
                # 메시지 수신 루프
                async for message in ws:
                    await self._handle_message(message)
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                
                if self.on_disconnect:
                    self.on_disconnect()
    
    async def _subscribe_all(self) -> None:
        """모든 종목 구독 요청"""
        for stock_code in self.subscriptions:
            await self._send_subscribe(stock_code, TR_PRICE)
            await asyncio.sleep(0.1)  # 요청 간 딜레이
    
    async def _send_subscribe(self, stock_code: str, tr_id: str) -> None:
        """
        구독 요청 전송
        
        Args:
            stock_code: 종목코드
            tr_id: TR 코드
        """
        if not self._ws:
            return
        
        message = {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1",  # 구독
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": stock_code
                }
            }
        }
        
        await self._ws.send(json.dumps(message))
        logger.debug(f"구독 요청: {stock_code} ({tr_id})")
    
    async def _heartbeat(self) -> None:
        """하트비트 전송"""
        while self._running:
            try:
                if self._ws:
                    await self._ws.ping()
                await asyncio.sleep(30)
            except Exception:
                break
    
    async def stop(self) -> None:
        """WebSocket 연결 종료"""
        self._running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("WebSocket 연결 종료")
    
    # ===== 메시지 처리 =====
    
    async def _handle_message(self, message: str) -> None:
        """
        수신 메시지 처리
        
        Args:
            message: 수신된 메시지
        """
        try:
            # 메시지 파싱
            if message.startswith('{'):
                # JSON 형식 (에러, 구독 응답)
                data = json.loads(message)
                await self._handle_json_message(data)
            else:
                # 파이프 구분 형식 (시세 데이터)
                await self._handle_pipe_message(message)
                
        except Exception as e:
            logger.error(f"메시지 처리 오류: {e}")
    
    async def _handle_json_message(self, data: dict) -> None:
        """JSON 메시지 처리"""
        header = data.get("header", {})
        tr_id = header.get("tr_id", "")
        
        if "PINGPONG" in tr_id:
            # Ping/Pong
            return
        
        body = data.get("body", {})
        rt_cd = body.get("rt_cd", "")
        msg = body.get("msg1", "")
        
        if rt_cd == "0":
            logger.debug(f"구독 성공: {tr_id}")
        else:
            logger.warning(f"구독 실패: {tr_id} - {msg}")
    
    async def _handle_pipe_message(self, message: str) -> None:
        """
        파이프 구분 메시지 처리 (실시간 시세)

        KIS WebSocket 메시지 형식:
        암호화구분(0/1) | TR_ID | 건수 | 데이터(^구분)
        """
        parts = message.split('|')

        if len(parts) < 4:
            return

        tr_id = parts[1]  # parts[0]은 암호화구분, parts[1]이 TR_ID

        if tr_id == TR_PRICE or "STCNT" in tr_id:
            await self._parse_price_data(parts)
        elif tr_id == TR_ORDERBOOK or "STASP" in tr_id:
            await self._parse_orderbook_data(parts)
    
    async def _parse_price_data(self, parts: list[str]) -> None:
        """
        체결가 데이터 파싱

        KIS WebSocket 메시지 형식: 암호화구분|TR_ID|건수|데이터
        데이터는 ^(caret)으로 구분된 필드:
        [0] 종목코드  [1] 체결시간  [2] 현재가  [3] 전일대비부호
        [4] 전일대비  [5] 전일대비율  [6] 가중평균가  [7] 시가
        [8] 고가  [9] 저가  [10] 매도호가1  [11] 매수호가1
        [12] 체결거래량  [13] 누적거래량  ...
        """
        try:
            if len(parts) < 4 or not parts[3]:
                return

            # parts[3]에 ^로 구분된 실제 데이터 필드가 있음
            data_fields = parts[3].split('^')

            if len(data_fields) < 14:
                return

            stock_code = data_fields[0]

            price_data = PriceData(
                stock_code=stock_code,
                trade_time=data_fields[1] if len(data_fields) > 1 else "",
                current_price=int(data_fields[2]) if len(data_fields) > 2 and data_fields[2] else 0,
                change=int(data_fields[4]) if len(data_fields) > 4 and data_fields[4] else 0,
                change_rate=float(data_fields[5]) if len(data_fields) > 5 and data_fields[5] else 0.0,
                open_price=int(data_fields[7]) if len(data_fields) > 7 and data_fields[7] else 0,
                high_price=int(data_fields[8]) if len(data_fields) > 8 and data_fields[8] else 0,
                low_price=int(data_fields[9]) if len(data_fields) > 9 and data_fields[9] else 0,
                volume=int(data_fields[13]) if len(data_fields) > 13 and data_fields[13] else 0
            )

            # 캐시 업데이트
            self.price_cache[stock_code] = price_data

            # 콜백 호출
            if self.on_price_update:
                self.on_price_update(price_data)

        except Exception as e:
            logger.error(f"체결가 파싱 오류: {e} | raw={parts[3][:100] if len(parts) > 3 else 'N/A'}")
    
    async def _parse_orderbook_data(self, parts: list[str]) -> None:
        """
        호가 데이터 파싱

        KIS H0STASP0 데이터 형식 (^구분):
        [0] 종목코드  [1] 영업시간
        [2] 매도호가1  [3] 매도호가2  ...  [11] 매도호가10
        [12] 매수호가1  [13] 매수호가2  ...  [21] 매수호가10
        [22] 매도호가잔량1  ...  [31] 매도호가잔량10
        [32] 매수호가잔량1  ...  [41] 매수호가잔량10
        """
        try:
            if len(parts) < 4 or not parts[3]:
                return

            data_fields = parts[3].split('^')

            if len(data_fields) < 42:
                return

            stock_code = data_fields[0]

            # 매도호가 (상위 5개)
            asks = []
            for i in range(5):
                price_idx = 2 + i      # 매도호가1~5: index 2~6
                volume_idx = 22 + i    # 매도호가잔량1~5: index 22~26
                if len(data_fields) > volume_idx:
                    asks.append({
                        "price": int(data_fields[price_idx]) if data_fields[price_idx] else 0,
                        "volume": int(data_fields[volume_idx]) if data_fields[volume_idx] else 0
                    })

            # 매수호가 (하위 5개)
            bids = []
            for i in range(5):
                price_idx = 12 + i     # 매수호가1~5: index 12~16
                volume_idx = 32 + i    # 매수호가잔량1~5: index 32~36
                if len(data_fields) > volume_idx:
                    bids.append({
                        "price": int(data_fields[price_idx]) if data_fields[price_idx] else 0,
                        "volume": int(data_fields[volume_idx]) if data_fields[volume_idx] else 0
                    })

            orderbook = OrderbookData(
                stock_code=stock_code,
                asks=asks,
                bids=bids,
                timestamp=now_kst().strftime("%H:%M:%S")
            )

            if self.on_orderbook_update:
                self.on_orderbook_update(orderbook)

        except Exception as e:
            logger.error(f"호가 파싱 오류: {e}")
    
    # ===== 유틸리티 =====
    
    def get_current_price(self, stock_code: str) -> Optional[int]:
        """
        캐시된 현재가 조회
        
        Args:
            stock_code: 종목코드
        
        Returns:
            현재가 (없으면 None)
        """
        if stock_code in self.price_cache:
            return self.price_cache[stock_code].current_price
        return None
    
    def get_price_data(self, stock_code: str) -> Optional[PriceData]:
        """
        캐시된 가격 데이터 조회
        
        Args:
            stock_code: 종목코드
        
        Returns:
            PriceData (없으면 None)
        """
        return self.price_cache.get(stock_code)


# ===== 모의 WebSocket (테스트용) =====

class MockWebSocket:
    """
    모의 WebSocket (테스트용)
    
    실제 WebSocket 연결 없이 가격 업데이트를 시뮬레이션합니다.
    """
    
    def __init__(self):
        self.subscriptions: set[str] = set()
        self.on_price_update: Optional[Callable[[PriceData], None]] = None
        self.price_cache: dict[str, PriceData] = {}
        self._running = False
        
        # 모의 가격
        self._mock_prices = {
            "005930": 75000,
            "000660": 195000,
            "373220": 420000,
            "006400": 350000,
            "051910": 310000
        }
        
        logger.info("모의 WebSocket 초기화")
    
    def subscribe(self, stock_codes: list[str]) -> None:
        for code in stock_codes:
            self.subscriptions.add(code)
        logger.info(f"[모의] 구독: {len(self.subscriptions)}개 종목")
    
    async def start(self) -> None:
        """모의 가격 업데이트 시작"""
        self._running = True
        logger.info("[모의] WebSocket 시작")
        
        import random
        
        while self._running:
            for code in self.subscriptions:
                base_price = self._mock_prices.get(code, 50000)
                
                # 랜덤 가격 변동 (-1% ~ +1%)
                change_pct = random.uniform(-0.01, 0.01)
                current_price = int(base_price * (1 + change_pct))
                change = current_price - base_price
                
                price_data = PriceData(
                    stock_code=code,
                    current_price=current_price,
                    change=change,
                    change_rate=change_pct * 100,
                    prev_close=base_price,
                    volume=random.randint(1000, 10000),
                    trade_time=now_kst().strftime("%H%M%S")
                )
                
                self.price_cache[code] = price_data
                
                if self.on_price_update:
                    self.on_price_update(price_data)
            
            await asyncio.sleep(1)  # 1초마다 업데이트
    
    async def stop(self) -> None:
        self._running = False
        logger.info("[모의] WebSocket 종료")
    
    def get_current_price(self, stock_code: str) -> Optional[int]:
        if stock_code in self.price_cache:
            return self.price_cache[stock_code].current_price
        return self._mock_prices.get(stock_code)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 KIS WebSocket 테스트 (모의)")
    print("=" * 60)
    
    async def test_mock_websocket():
        ws = MockWebSocket()
        
        # 구독
        ws.subscribe(["005930", "000660", "373220"])
        
        # 콜백 설정
        update_count = 0
        def on_price(data: PriceData):
            nonlocal update_count
            update_count += 1
            print(f"  [{data.stock_code}] {data.current_price:,}원 ({data.change_rate:+.2f}%)")
        
        ws.on_price_update = on_price
        
        # 3초간 실행
        print("\n실시간 시세 수신 (3초):")
        task = asyncio.create_task(ws.start())
        await asyncio.sleep(3)
        await ws.stop()
        
        print(f"\n총 {update_count}건 업데이트 수신")
        
        # 캐시 확인
        print("\n캐시된 가격:")
        for code, data in ws.price_cache.items():
            print(f"  {code}: {data.current_price:,}원")
    
    asyncio.run(test_mock_websocket())
    
    print("\n" + "=" * 60)
    print("✅ WebSocket 테스트 완료!")
    print("=" * 60)
