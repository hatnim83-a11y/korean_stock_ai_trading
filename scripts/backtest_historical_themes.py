"""
backtest_historical_themes.py - 역사적 테마 기반 백테스트

2023-2026년의 실제 시장 테마를 재현하여 백테스트합니다.

사용법:
    python scripts/backtest_historical_themes.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from typing import Dict, List
import pandas as pd

from logger import logger
from modules.backtester import DataLoader, StrategySimulator, BacktestEngine, BacktestConfig


# ===== 역사적 테마 정의 =====

HISTORICAL_THEMES = {
    # 2023년 상반기: 2차전지 붐
    "2023-Q1": {
        "period": ("2023-02-01", "2023-03-31"),
        "themes": {
            "2차전지": ["373220", "086520", "247540", "003670", "051910"],  # LG에너지, 에코프로, 에코프로비엠, 포스코퓨처엠, LG화학
            "전기차": ["005380", "000270"],  # 현대차, 기아
        }
    },
    "2023-Q2": {
        "period": ("2023-04-01", "2023-06-30"),
        "themes": {
            "2차전지": ["373220", "086520", "247540", "003670"],
            "바이오": ["207940", "068270", "028300"],  # 삼성바이오, 셀트리온, HLB
        }
    },
    
    # 2023년 하반기: AI 반도체 시작
    "2023-Q3": {
        "period": ("2023-07-01", "2023-09-30"),
        "themes": {
            "AI 반도체": ["000660", "005930"],  # SK하이닉스, 삼성전자
            "2차전지": ["373220", "086520"],
            "IT 플랫폼": ["035420", "035720"],  # NAVER, 카카오
        }
    },
    "2023-Q4": {
        "period": ("2023-10-01", "2023-12-31"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "반도체 장비": ["042700", "036930"],  # 한미반도체, 주성엔지니어링
            "IT 플랫폼": ["035420"],
        }
    },
    
    # 2024년: AI/클라우드, 바이오 강세
    "2024-Q1": {
        "period": ("2024-01-01", "2024-03-31"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "IT 플랫폼": ["035420", "035720"],
            "바이오": ["207940", "068270"],
        }
    },
    "2024-Q2": {
        "period": ("2024-04-01", "2024-06-30"),
        "themes": {
            "AI 반도체": ["000660"],  # SK하이닉스 집중
            "IT 플랫폼": ["035420"],
            "자동차": ["005380", "000270"],
            "바이오": ["068270"],
        }
    },
    "2024-Q3": {
        "period": ("2024-07-01", "2024-09-30"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "2차전지": ["373220"],  # 재부상
            "방산": ["272210", "047810"],  # 한화시스템, 한화에어로스페이스 (예시)
        }
    },
    "2024-Q4": {
        "period": ("2024-10-01", "2024-12-31"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "IT 플랫폼": ["035420"],
            "2차전지": ["373220"],
        }
    },
    
    # 2025년: AI 반도체 재부상 (HBM 메모리)
    "2025-Q1": {
        "period": ("2025-01-01", "2025-03-31"),
        "themes": {
            "AI 반도체": ["000660", "005930"],  # HBM 메모리 붐
            "반도체 장비": ["042700"],
            "IT 플랫폼": ["035420"],
        }
    },
    "2025-Q2": {
        "period": ("2025-04-01", "2025-06-30"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "AI 서버": ["005930"],  # 삼성전자 AI 서버
            "엔터테인먼트": ["041510", "035900"],  # 에스엠, JYP
        }
    },
    "2025-Q3": {
        "period": ("2025-07-01", "2025-09-30"),
        "themes": {
            "AI 반도체": ["000660", "005930"],
            "IT 플랫폼": ["035420", "035720"],
            "자동차": ["005380"],
        }
    },
    "2025-Q4": {
        "period": ("2025-10-01", "2025-12-31"),
        "themes": {
            "AI 반도체": ["000660", "005930"],  # 대폭발!
            "IT 플랫폼": ["035420"],
            "2차전지": ["373220"],
        }
    },
    
    # 2026년
    "2026-Q1": {
        "period": ("2026-01-01", "2026-02-01"),
        "themes": {
            "AI 반도체": ["000660", "005930"],  # 지속 강세
            "IT 플랫폼": ["035420"],
        }
    },
}


# ===== 종목 정보 =====

STOCK_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "373220": "LG에너지솔루션",
    "086520": "에코프로",
    "247540": "에코프로비엠",
    "003670": "포스코퓨처엠",
    "005380": "현대차",
    "000270": "기아",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "028300": "HLB",
    "042700": "한미반도체",
    "036930": "주성엔지니어링",
    "272210": "한화시스템",
    "047810": "한화에어로스페이스",
    "041510": "에스엠",
    "035900": "JYP Ent.",
}


def get_quarterly_portfolio(quarter: str) -> List[str]:
    """
    분기별 포트폴리오 종목 추출
    
    각 테마에서 상위 종목을 선정하여 포트폴리오 구성
    """
    theme_data = HISTORICAL_THEMES[quarter]
    themes = theme_data["themes"]
    
    # 각 테마에서 종목 선정 (중복 제거)
    portfolio = []
    for theme_name, stocks in themes.items():
        # 각 테마에서 최대 2개씩 선정
        portfolio.extend(stocks[:2])
    
    # 중복 제거 및 최대 10개로 제한
    portfolio = list(dict.fromkeys(portfolio))[:10]
    
    logger.info(f"📊 {quarter} 포트폴리오: {len(portfolio)}개 종목")
    for symbol in portfolio:
        stock_name = STOCK_NAMES.get(symbol, "알 수 없음")
        logger.debug(f"  - {stock_name} ({symbol})")
    
    return portfolio


def backtest_quarterly_rotation():
    """
    분기별 테마 로테이션 백테스트
    """
    logger.info("=" * 70)
    logger.info("🔄 역사적 테마 기반 백테스트 (분기별 로테이션)")
    logger.info("=" * 70)
    
    data_loader = DataLoader()
    total_capital = 10_000_000  # 1,000만원
    current_capital = total_capital
    
    # 전체 거래 기록
    all_trades = []
    quarterly_returns = []
    
    for quarter in sorted(HISTORICAL_THEMES.keys()):
        logger.info(f"\n{'=' * 70}")
        logger.info(f"📅 {quarter} 백테스트")
        logger.info(f"{'=' * 70}")
        
        theme_data = HISTORICAL_THEMES[quarter]
        start_date, end_date = theme_data["period"]
        
        # 포트폴리오 구성
        portfolio_stocks = get_quarterly_portfolio(quarter)
        
        if not portfolio_stocks:
            logger.warning(f"{quarter}: 종목 없음, 스킵")
            continue
        
        # 각 종목에 균등 배분
        capital_per_stock = current_capital / len(portfolio_stocks)
        
        quarter_returns = []
        
        for symbol in portfolio_stocks:
            stock_name = STOCK_NAMES.get(symbol, symbol)
            
            try:
                # 데이터 로드
                market_data = data_loader.load_stock_data(symbol, start_date, end_date, stock_name)
                
                if market_data.data.empty:
                    logger.warning(f"  [{stock_name}] 데이터 없음")
                    continue
                
                # 기술적 지표 추가
                market_data.data = data_loader.add_technical_indicators(market_data.data)
                
                # 백테스트 설정
                # 백테스트 실행 (Buy & Hold 방식)
                start_price = market_data.data.iloc[0]['close']
                end_price = market_data.data.iloc[-1]['close']
                return_pct = (end_price - start_price) / start_price * 100
                quarter_returns.append(return_pct)
                
                logger.info(f"  [{stock_name}] 수익률: {return_pct:+.2f}%")
                
            except Exception as e:
                logger.error(f"  [{stock_name}] 백테스트 실패: {e}")
                quarter_returns.append(0.0)
        
        # 분기 평균 수익률
        if quarter_returns:
            avg_return = sum(quarter_returns) / len(quarter_returns)
            quarterly_returns.append({
                "quarter": quarter,
                "return": avg_return,
                "stocks": len(portfolio_stocks)
            })
            
            # 자본 업데이트
            current_capital = current_capital * (1 + avg_return / 100)
            
            logger.info(f"\n🎯 {quarter} 평균 수익률: {avg_return:+.2f}%")
            logger.info(f"💰 현재 자본: {current_capital:,.0f}원")
    
    # 최종 결과
    logger.info("\n" + "=" * 70)
    logger.info("📊 최종 성과 요약")
    logger.info("=" * 70)
    
    total_return = (current_capital - total_capital) / total_capital * 100
    logger.info(f"\n초기 자본: {total_capital:,.0f}원")
    logger.info(f"최종 자본: {current_capital:,.0f}원")
    logger.info(f"총 수익률: {total_return:+.2f}%")
    logger.info(f"CAGR: {((current_capital / total_capital) ** (1/3) - 1) * 100:.2f}%")
    
    # 분기별 상세
    logger.info(f"\n📅 분기별 수익률:")
    for qr in quarterly_returns:
        logger.info(f"  {qr['quarter']:10s}: {qr['return']:+7.2f}% ({qr['stocks']}개 종목)")
    
    # 연도별 집계
    logger.info(f"\n📈 연도별 집계:")
    yearly_data = {}
    for qr in quarterly_returns:
        year = qr['quarter'][:4]
        if year not in yearly_data:
            yearly_data[year] = []
        yearly_data[year].append(qr['return'])
    
    for year, returns in sorted(yearly_data.items()):
        year_return = sum(returns)
        logger.info(f"  {year}년: {year_return:+.2f}% (평균 {sum(returns)/len(returns):+.2f}%)")
    
    logger.info("\n" + "=" * 70)
    logger.info("✅ 역사적 테마 백테스트 완료!")
    logger.info("=" * 70)


def compare_with_buyandhold():
    """
    테마 로테이션 vs Buy & Hold 비교
    """
    logger.info("\n" + "=" * 70)
    logger.info("📊 전략 비교: 테마 로테이션 vs Buy & Hold")
    logger.info("=" * 70)
    
    # Buy & Hold: SK하이닉스 단일 종목
    data_loader = DataLoader()
    market_data = data_loader.load_stock_data("000660", "2023-02-01", "2026-02-01", "SK하이닉스")
    
    if not market_data.data.empty:
        start_price = market_data.data.iloc[0]['close']
        end_price = market_data.data.iloc[-1]['close']
        bnh_return = (end_price - start_price) / start_price * 100
        
        logger.info(f"\n📈 Buy & Hold (SK하이닉스):")
        logger.info(f"  수익률: {bnh_return:+.2f}%")
        logger.info(f"  CAGR: {((1 + bnh_return/100) ** (1/3) - 1) * 100:.2f}%")


if __name__ == "__main__":
    logger.info("🚀 역사적 테마 기반 백테스트 시작\n")
    
    # 분기별 로테이션 백테스트
    backtest_quarterly_rotation()
    
    # 비교 분석
    compare_with_buyandhold()
    
    logger.info("\n✅ 모든 백테스트 완료!")
