"""
database.py - SQLite 데이터베이스 관리 모듈

이 파일은 시스템의 모든 데이터베이스 작업을 관리합니다.
- 테이블 생성 및 관리
- 데이터 CRUD 작업
- 커넥션 풀 관리

사용법:
    from database import Database
    
    db = Database()
    db.connect()
    db.save_theme_scores(themes, date.today())
    portfolio = db.get_portfolio()
    db.close()
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from logger import logger
from config import now_kst


class Database:
    """
    SQLite 데이터베이스 관리 클래스
    
    Attributes:
        db_path: 데이터베이스 파일 경로
        conn: SQLite 연결 객체
    
    Example:
        >>> db = Database("data/trading.db")
        >>> db.connect()
        >>> db.init_tables()
        >>> db.close()
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        데이터베이스 초기화
        
        Args:
            db_path: DB 파일 경로 (None이면 config에서 로드)
        """
        if db_path is None:
            try:
                from config import settings
                db_path = settings.DATABASE_PATH
            except ImportError:
                db_path = "data/trading.db"
        
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        
        # 데이터 디렉토리 생성
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def connect(self) -> None:
        """
        데이터베이스 연결
        
        연결 후 row_factory를 설정하여 딕셔너리 형태로 데이터 반환
        """
        try:
            self.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,  # 멀티스레드 환경 지원
                timeout=30.0  # 락 대기 시간
            )
            # 쿼리 결과를 딕셔너리 형태로 반환
            self.conn.row_factory = sqlite3.Row
            # WAL 모드 활성화 (동시 읽기/쓰기 성능 향상)
            self.conn.execute("PRAGMA journal_mode=WAL")
            # 외래키 제약 활성화
            self.conn.execute("PRAGMA foreign_keys=ON")
            
            logger.info(f"📁 데이터베이스 연결 성공: {self.db_path}")
            
        except sqlite3.Error as e:
            logger.error(f"데이터베이스 연결 실패: {e}")
            raise
    
    def close(self) -> None:
        """데이터베이스 연결 종료"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("데이터베이스 연결 종료")
    
    @contextmanager
    def get_cursor(self):
        """
        커서를 반환하는 컨텍스트 매니저
        
        자동으로 커밋/롤백 처리
        
        Example:
            >>> with db.get_cursor() as cursor:
            >>>     cursor.execute("SELECT * FROM themes")
            >>>     rows = cursor.fetchall()
        """
        if not self.conn:
            raise RuntimeError("데이터베이스가 연결되지 않았습니다. connect()를 먼저 호출하세요.")
        
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"데이터베이스 작업 실패: {e}")
            raise
        finally:
            cursor.close()
    
    def init_tables(self) -> None:
        """
        모든 테이블 생성
        
        이미 존재하는 테이블은 무시됩니다 (IF NOT EXISTS 사용)
        """
        with self.get_cursor() as cursor:
            # ===== 1. 테마 점수 이력 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    theme_name VARCHAR(50) NOT NULL,
                    score REAL NOT NULL,
                    momentum REAL,
                    supply_ratio REAL,
                    news_count INTEGER,
                    ai_sentiment REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    -- 인덱스를 위한 유니크 제약
                    UNIQUE(date, theme_name)
                )
            """)
            
            # ===== 2. 종목 스크리닝 이력 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    theme VARCHAR(50),
                    supply_score REAL,
                    technical_score REAL,
                    ai_sentiment REAL,
                    ai_reason TEXT,
                    final_score REAL,
                    selected BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    UNIQUE(date, stock_code)
                )
            """)
            
            # ===== 3. 포트폴리오 현황 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    theme VARCHAR(50),
                    weight REAL,
                    shares INTEGER,
                    buy_price REAL,
                    current_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    profit_rate REAL,
                    profit_amount REAL,
                    status VARCHAR(20) DEFAULT 'holding',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ===== 4. 매매 기록 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    time TIME,
                    stock_code VARCHAR(10) NOT NULL,
                    stock_name VARCHAR(50) NOT NULL,
                    action VARCHAR(10) NOT NULL,
                    shares INTEGER,
                    price REAL,
                    amount REAL,
                    reason VARCHAR(50),
                    profit_rate REAL,
                    profit_amount REAL,
                    order_id VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ===== 5. 성과 지표 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL UNIQUE,
                    total_value REAL,
                    total_cost REAL,
                    cash REAL,
                    daily_return REAL,
                    cumulative_return REAL,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL,
                    mdd REAL,
                    sharpe_ratio REAL,
                    num_positions INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ===== 6. 시스템 상태 테이블 =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    message TEXT,
                    error_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ===== 인덱스 생성 =====
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_themes_date ON themes(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_date ON stocks(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_code ON stocks(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code)")
        
        logger.info("📊 데이터베이스 테이블 초기화 완료")
    
    # ===== 테마 관련 메서드 =====
    
    def save_theme_scores(self, themes: list[dict], target_date: date) -> None:
        """
        테마 점수 저장
        
        Args:
            themes: 테마 리스트 [{'theme': '2차전지', 'score': 87.5, ...}, ...]
            target_date: 날짜
            
        Example:
            >>> themes = [
            >>>     {'theme': '2차전지', 'score': 87.5, 'momentum': 5.2, 
            >>>      'supply_ratio': 68, 'news_count': 127, 'ai_sentiment': 8.5},
            >>>     {'theme': 'AI반도체', 'score': 82.3, ...}
            >>> ]
            >>> db.save_theme_scores(themes, date.today())
        """
        with self.get_cursor() as cursor:
            for theme in themes:
                cursor.execute("""
                    INSERT OR REPLACE INTO themes (
                        date, theme_name, score, momentum, supply_ratio, 
                        news_count, ai_sentiment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    theme['theme'],
                    theme['score'],
                    theme.get('momentum', 0),
                    theme.get('supply_ratio', 0),
                    theme.get('news_count', 0),
                    theme.get('ai_sentiment', 0)
                ))
        
        logger.info(f"📈 {len(themes)}개 테마 점수 저장 완료 ({target_date})")
    
    def get_top_themes(self, target_date: date, count: int = 5) -> list[dict]:
        """
        상위 테마 조회
        
        Args:
            target_date: 조회할 날짜
            count: 조회할 테마 수
            
        Returns:
            테마 리스트 (점수 순)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM themes 
                WHERE date = ?
                ORDER BY score DESC
                LIMIT ?
            """, (target_date, count))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ===== 종목 관련 메서드 =====
    
    def save_screened_stocks(self, stocks: list[dict], target_date: date) -> None:
        """
        스크리닝된 종목 저장
        
        Args:
            stocks: 종목 리스트
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            for stock in stocks:
                cursor.execute("""
                    INSERT OR REPLACE INTO stocks (
                        date, stock_code, stock_name, theme, supply_score,
                        technical_score, ai_sentiment, ai_reason, final_score, selected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    stock['stock_code'],
                    stock['stock_name'],
                    stock.get('theme'),
                    stock.get('supply_score'),
                    stock.get('technical_score'),
                    stock.get('ai_sentiment'),
                    stock.get('ai_reason'),
                    stock.get('final_score'),
                    stock.get('selected', False)
                ))
        
        logger.info(f"📊 {len(stocks)}개 종목 스크리닝 결과 저장 완료")
    
    # ===== 포트폴리오 관련 메서드 =====
    
    def save_portfolio(self, portfolio: list[dict], target_date: date) -> None:
        """
        포트폴리오 저장
        
        Args:
            portfolio: 포트폴리오 종목 리스트
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            for position in portfolio:
                cursor.execute("""
                    INSERT INTO portfolio (
                        date, stock_code, stock_name, theme, weight, shares,
                        buy_price, stop_loss, take_profit, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_date,
                    position['stock_code'],
                    position['stock_name'],
                    position.get('theme'),
                    position.get('weight'),
                    position.get('shares'),
                    position.get('buy_price'),
                    position.get('stop_loss'),
                    position.get('take_profit'),
                    position.get('status', 'holding')
                ))
        
        logger.info(f"💼 포트폴리오 {len(portfolio)}개 종목 저장 완료")
    
    def get_portfolio(self, status: str = "holding") -> list[dict]:
        """
        현재 포트폴리오 조회
        
        Args:
            status: 상태 필터 ('holding' 또는 'closed')
            
        Returns:
            포트폴리오 종목 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM portfolio 
                WHERE status = ?
                ORDER BY created_at DESC
            """, (status,))
            
            rows = cursor.fetchall()
            portfolio = [dict(row) for row in rows]
            
        logger.info(f"💼 포트폴리오 조회: {len(portfolio)}개 종목 ({status})")
        return portfolio
    
    def update_portfolio_price(
        self, 
        stock_code: str, 
        current_price: float,
        profit_rate: float,
        profit_amount: float
    ) -> None:
        """
        포트폴리오 현재가 업데이트
        
        Args:
            stock_code: 종목코드
            current_price: 현재가
            profit_rate: 수익률
            profit_amount: 수익금액
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio 
                SET current_price = ?, profit_rate = ?, profit_amount = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (current_price, profit_rate, profit_amount, stock_code))
    
    def close_position(self, stock_code: str, reason: str) -> None:
        """
        포지션 청산 (상태 변경)
        
        Args:
            stock_code: 종목코드
            reason: 청산 사유 (손절/익절/수급이탈)
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE portfolio 
                SET status = 'closed', updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = ? AND status = 'holding'
            """, (stock_code,))
        
        logger.info(f"📤 포지션 청산: {stock_code} ({reason})")
    
    # ===== 매매 기록 관련 메서드 =====
    
    def save_trade(self, trade: dict) -> None:
        """
        매매 기록 저장
        
        Args:
            trade: 매매 정보 딕셔너리
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trades (
                    date, time, stock_code, stock_name, action, shares,
                    price, amount, reason, profit_rate, profit_amount, order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get('date', date.today()),
                trade.get('time', now_kst().strftime("%H:%M:%S")),
                trade['stock_code'],
                trade['stock_name'],
                trade['action'],
                trade.get('shares'),
                trade.get('price'),
                trade.get('amount'),
                trade.get('reason'),
                trade.get('profit_rate'),
                trade.get('profit_amount'),
                trade.get('order_id')
            ))
        
        action_emoji = "📈" if trade['action'] == 'buy' else "📉"
        logger.info(f"{action_emoji} 매매 기록 저장: {trade['action']} {trade['stock_name']}")
    
    def get_trades(self, target_date: date) -> list[dict]:
        """
        특정 날짜의 매매 기록 조회
        
        Args:
            target_date: 조회할 날짜
            
        Returns:
            매매 기록 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trades 
                WHERE date = ?
                ORDER BY time DESC
            """, (target_date,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ===== 성과 지표 관련 메서드 =====
    
    def save_performance(self, performance: dict, target_date: date) -> None:
        """
        일일 성과 지표 저장
        
        Args:
            performance: 성과 지표 딕셔너리
            target_date: 날짜
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO performance (
                    date, total_value, total_cost, cash, daily_return,
                    cumulative_return, win_count, loss_count, win_rate,
                    mdd, sharpe_ratio, num_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                target_date,
                performance.get('total_value'),
                performance.get('total_cost'),
                performance.get('cash'),
                performance.get('daily_return'),
                performance.get('cumulative_return'),
                performance.get('win_count', 0),
                performance.get('loss_count', 0),
                performance.get('win_rate'),
                performance.get('mdd'),
                performance.get('sharpe_ratio'),
                performance.get('num_positions')
            ))
        
        logger.info(f"📊 일일 성과 저장: {target_date}")
    
    def get_performance_history(self, days: int = 30) -> list[dict]:
        """
        최근 N일간 성과 이력 조회
        
        Args:
            days: 조회할 일수
            
        Returns:
            성과 이력 리스트
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM performance 
                ORDER BY date DESC
                LIMIT ?
            """, (days,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ===== 시스템 상태 관련 메서드 =====
    
    def log_system_status(self, status: str, message: str = "") -> None:
        """
        시스템 상태 로깅
        
        Args:
            status: 상태 (running, stopped, error)
            message: 상태 메시지
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO system_status (date, status, message)
                VALUES (?, ?, ?)
            """, (date.today(), status, message))


# ===== 편의 함수 =====

def get_database() -> Database:
    """
    데이터베이스 인스턴스 반환 (싱글톤 패턴)
    
    Returns:
        연결된 Database 인스턴스
    """
    db = Database()
    db.connect()
    db.init_tables()
    return db


# 직접 실행 시 테이블 초기화
if __name__ == "__main__":
    print("=" * 50)
    print("📁 데이터베이스 초기화")
    print("=" * 50)
    
    db = Database()
    db.connect()
    db.init_tables()
    
    # 테스트 데이터 삽입
    test_themes = [
        {
            'theme': '2차전지',
            'score': 87.5,
            'momentum': 5.2,
            'supply_ratio': 68,
            'news_count': 127,
            'ai_sentiment': 8.5
        },
        {
            'theme': 'AI반도체',
            'score': 82.3,
            'momentum': 3.8,
            'supply_ratio': 71,
            'news_count': 95,
            'ai_sentiment': 8.0
        }
    ]
    
    db.save_theme_scores(test_themes, date.today())
    
    # 저장된 데이터 확인
    top_themes = db.get_top_themes(date.today())
    print("\n📊 저장된 테마:")
    for theme in top_themes:
        print(f"  - {theme['theme_name']}: {theme['score']}점")
    
    db.close()
    
    print("\n✅ 데이터베이스 초기화 완료!")
    print("=" * 50)
