"""
거래내역 전수조사: 보유 기간 중 최저 손실 분석.

목적: 손절라인을 -5%로 올려도 되는지 검토.
- 이익으로 마감한 거래 중 보유 중 -5% 이하로 떨어진 적이 있는지
- 각 거래의 최저 수익률(min_profit_during_hold) 계산
"""
import sqlite3
from collections import defaultdict
from pykrx import stock
from datetime import datetime

DB_PATH = "/home/hatni/korean_stock_ai_trading/data/trading.db"


def fetch_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, stock_code, stock_name, buy_date, sell_date, buy_price,
               sell_price, profit_rate, max_profit_during_hold, hold_days, sell_reason
        FROM trade_reviews
        ORDER BY buy_date, stock_code, sell_date
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_positions(rows):
    """동일 (stock_code, buy_date) 묶음 — 분할매도는 동일 포지션."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["stock_code"], r["buy_date"])].append(r)
    positions = []
    for (code, buy_date), trs in groups.items():
        max_sell_date = max(t["sell_date"] for t in trs)
        # 가중평균 수익률 (분할매도 가중)
        total_profit_amt = sum(t["profit_rate"] for t in trs)  # 단순 평균이 아닌 마지막 sell의 profit_rate를 쓰는 게 맞을 수 있음
        last_sell = sorted(trs, key=lambda t: t["sell_date"])[-1]
        max_profit = max(t["max_profit_during_hold"] or 0 for t in trs)
        # "포지션 손익"은 분할 평균치
        positions.append({
            "stock_code": code,
            "stock_name": trs[0]["stock_name"],
            "buy_date": buy_date,
            "sell_date": max_sell_date,
            "buy_price": trs[0]["buy_price"],
            "final_sell_price": last_sell["sell_price"],
            "final_profit_rate": last_sell["profit_rate"],
            "avg_profit_rate": sum(t["profit_rate"] for t in trs) / len(trs),
            "max_profit_during_hold": max_profit,
            "sell_reasons": [t["sell_reason"] for t in trs],
            "n_partials": len(trs),
        })
    positions.sort(key=lambda p: p["buy_date"])
    return positions


def compute_min_profit(pos):
    """pykrx로 buy_date~sell_date 일별 OHLC 가져와 최저 수익률 계산."""
    code = pos["stock_code"]
    buy_dt = pos["buy_date"].replace("-", "")
    sell_dt = pos["sell_date"].replace("-", "")
    try:
        df = stock.get_market_ohlcv_by_date(buy_dt, sell_dt, code)
    except Exception as e:
        return None, f"pykrx 에러: {e}"
    if df is None or df.empty:
        return None, "데이터 없음"
    lowest_low = df["저가"].min()
    lowest_date = df["저가"].idxmin().strftime("%Y-%m-%d")
    min_profit_rate = (lowest_low - pos["buy_price"]) / pos["buy_price"] * 100
    return {
        "lowest_low": float(lowest_low),
        "lowest_date": lowest_date,
        "min_profit_rate": min_profit_rate,
    }, None


def main():
    rows = fetch_positions()
    positions = group_positions(rows)
    print(f"총 거래 row: {len(rows)}, 고유 포지션: {len(positions)}\n")

    results = []
    for pos in positions:
        info, err = compute_min_profit(pos)
        results.append({**pos, "ohlc": info, "ohlc_err": err})

    # 결과 출력
    print(f"{'번호':<4}{'코드':<8}{'종목':<14}{'매수일':<12}{'매도일':<12}"
          f"{'최종수익%':>10}{'최대수익%':>10}{'최저수익%':>10}{'최저일':<12}{'사유'}")
    for i, r in enumerate(results, 1):
        ohlc = r["ohlc"]
        if ohlc:
            min_pr = f"{ohlc['min_profit_rate']:.2f}"
            low_dt = ohlc["lowest_date"]
        else:
            min_pr = f"ERR({r['ohlc_err']})"
            low_dt = "-"
        print(f"{i:<4}{r['stock_code']:<8}{r['stock_name']:<14}{r['buy_date']:<12}{r['sell_date']:<12}"
              f"{r['final_profit_rate']:>10.2f}{r['max_profit_during_hold']:>10.2f}{min_pr:>10}{low_dt:<14}"
              f"{','.join(set(r['sell_reasons']))}")

    # 통계
    profit_pos = [r for r in results if r["avg_profit_rate"] > 0 and r["ohlc"]]
    loss_pos = [r for r in results if r["avg_profit_rate"] <= 0 and r["ohlc"]]

    dip5_recovered = [r for r in profit_pos if r["ohlc"]["min_profit_rate"] <= -5.0]
    dip3_recovered = [r for r in profit_pos if r["ohlc"]["min_profit_rate"] <= -3.0]
    dip2_recovered = [r for r in profit_pos if r["ohlc"]["min_profit_rate"] <= -2.0]

    print("\n--- 통계 ---")
    print(f"이익 포지션 (OHLC 조회 성공): {len(profit_pos)}")
    print(f"  -2% 이하 하락 후 회복: {len(dip2_recovered)}")
    print(f"  -3% 이하 하락 후 회복: {len(dip3_recovered)}")
    print(f"  -5% 이하 하락 후 회복: {len(dip5_recovered)}")
    print(f"손실 포지션 (OHLC 조회 성공): {len(loss_pos)}")

    if dip5_recovered:
        print("\n[-5% 이하 → 이익 마감 사례]")
        for r in dip5_recovered:
            print(f"  {r['stock_name']:12} {r['buy_date']} → 최저 {r['ohlc']['min_profit_rate']:.2f}% "
                  f"({r['ohlc']['lowest_date']}) → 최종 {r['final_profit_rate']:+.2f}%")

    return results


if __name__ == "__main__":
    main()
