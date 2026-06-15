"""005935(삼성전자우) 종가베팅 미청산 고아분 수동 청산 — 일회성.

candidate_id=437 (2026-06-12 진입, 7주 @206,500). 정상 청산잡이 못 잡는 과거 고아분.
앱 매도 불가 상황이라 코드로 내일(화) 장중 매도.

안전 설계:
  - 기본은 DRY-RUN(조회만). 실발주는 --execute 명시해야만.
  - 매도 전 KIS 실잔고 재조회 → 005935 실보유/수량 확인. 미보유/0주면 중단.
  - 실보유 수량과 --shares 불일치 시 경고 후 실보유 수량으로 클립(과매도 방지).
  - 정규장(09:00~15:20) 가드. 시장가는 15:20 이후 동시호가 영향 → --force로만 우회.
  - 시장가 전량 매도(삼성전자우 대형주, 슬리피지 미미). 발주 후 체결 잔고 재확인.

⚠️ 라이브 서비스(PID)와 KIS 토큰(_shared_token, 프로세스 메모리) 경합 주의.
   장중 한가한 시간대(예: 11:00~14:30, 오전 청산/매수/트레일링·종가진입 사이) 실행 권장.
   실행 후 서비스는 다음 KIS 호출 때 토큰 자연 재발급.

사용법 (deploy 체크아웃에서):
  조회:  venv/bin/python scripts/manual_close_005935.py
  매도:  venv/bin/python scripts/manual_close_005935.py --execute
"""
import argparse
import sys
import time

sys.path.insert(0, "/home/hatni/korean_stock_ai_trading")

from datetime import time as T
from config import now_kst
from modules.trading_engine.kis_order_api import KISOrderApi

CODE = "005935"
EXPECTED_SHARES = 7
ENTRY_PRICE = 206500


def get_holding(api):
    """잔고에서 CODE 보유 dict 반환 (없으면 None). 잔고 조회 실패 시 'ERROR'."""
    bal = api.get_balance()
    if not bal:
        return "ERROR"
    for p in bal.get("positions", []):
        if p.get("stock_code") == CODE:
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="실제 시장가 매도 발주 (없으면 조회만)")
    ap.add_argument("--shares", type=int, default=EXPECTED_SHARES, help=f"매도 수량 (기본 {EXPECTED_SHARES})")
    ap.add_argument("--force", action="store_true", help="정규장 시간 가드 우회")
    args = ap.parse_args()

    n = now_kst()
    print(f"[시각] {n.strftime('%Y-%m-%d %H:%M:%S %A')} KST")
    in_session = T(9, 0) <= n.time() <= T(15, 20)
    print(f"[장중] 정규장(09:00~15:20) 중? {in_session}")

    api = KISOrderApi()
    print(f"[계좌] is_mock={api.is_mock}")

    # 1) 실보유 확인
    h = get_holding(api)
    if h == "ERROR":
        print("[중단] 잔고 조회 실패(500 등). 매도 보류 — KIS API 회복 후 재시도.")
        return 3
    if h is None:
        print(f"[중단] {CODE} 미보유 — 매도할 물량 없음(이미 청산됐을 수 있음).")
        return 4

    real_qty = int(h.get("quantity") or 0)
    print(f"[보유] {CODE} {h.get('stock_name')} {real_qty}주 "
          f"매입@{h.get('buy_price')} 현재@{h.get('current_price')} "
          f"평가손익 {h.get('profit')}({h.get('profit_rate')}%)")

    if real_qty <= 0:
        print("[중단] 실보유 0주.")
        return 4

    sell_qty = min(args.shares, real_qty)  # 과매도 방지 클립
    if sell_qty != args.shares:
        print(f"[조정] 요청 {args.shares}주 > 실보유 {real_qty}주 → {sell_qty}주로 클립")

    # 2) dry-run / 시간 가드
    if not args.execute:
        print(f"\n[DRY-RUN] 매도 예정: {CODE} {sell_qty}주 시장가. "
              f"실발주하려면 --execute 추가.")
        return 0

    if not in_session and not args.force:
        print(f"[중단] 정규장 시간 아님 → 시장가 매도 보류. 장중 재실행 or --force.")
        return 5

    # 3) 시장가 매도 발주
    print(f"\n[발주] {CODE} {sell_qty}주 시장가 매도 …")
    res = api.sell_market_order(CODE, sell_qty)
    print(f"[결과] success={res.get('success')} order_id={res.get('order_id')} "
          f"msg={res.get('message')}")
    if not res.get("success"):
        print("[실패] 매도 발주 실패 — 위 메시지 확인.")
        return 6

    order_id = res.get("order_id")

    # 4) 체결 확인 (잔고 재조회)
    print("[확인] 4초 후 잔고 재조회로 체결 검증 …")
    time.sleep(4)
    h2 = get_holding(api)
    if h2 == "ERROR":
        print("[주의] 체결 확인용 잔고 조회 실패. 발주는 접수됨(order_id 위). 수동 재확인 필요.")
        return 0
    after_qty = 0 if h2 is None else int(h2.get("quantity") or 0)
    print(f"[체결검증] 매도 전 {real_qty}주 → 매도 후 {after_qty}주 (감소 {real_qty - after_qty}주)")
    if after_qty <= real_qty - sell_qty:
        print(f"\n✅ 매도 체결 확인. order_id={order_id}")
        print(f"   → 이제 closing_bet.db candidate 437의 exit 정리 필요(Claude에게 요청).")
        return 0
    else:
        print(f"\n⚠️ 체결 미확인(잔고 미감소). 미체결/지연 가능 — order_id={order_id} 상태 재확인.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
