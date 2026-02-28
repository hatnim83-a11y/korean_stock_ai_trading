# WebSocket Price Parsing Bug - Detailed Analysis

## Discovery Date: 2026-02-12

## File
`/home/hatni/korean_stock_ai_trading/modules/trading_engine/kis_websocket.py`

## Problem Description

The KIS WebSocket sends real-time trade data in this format:
```
0|H0STCNT0|004|005930^134500^092500^1^500^0.37^134200^135000^134500^133000^134600^134500^10^1234567^...
```

Structure:
- Field 0 (pipe): 암호화구분 ("0" = plaintext, "1" = encrypted)
- Field 1 (pipe): TR_ID (e.g., "H0STCNT0")
- Field 2 (pipe): 데이터 건수 (e.g., "004")
- Field 3 (pipe): 실제 데이터 (caret `^` separated fields)

### Bug 1: Wrong TR_ID extraction (line ~399)
```python
# CURRENT (BROKEN):
tr_id = parts[0]  # Gets "0" (암호화구분), NOT the TR_ID

# FIX:
tr_id = parts[1]  # Gets "H0STCNT0" (actual TR_ID)
```

### Bug 2: Wrong data field parsing (line ~406-454)
```python
# CURRENT (BROKEN):
# Treats pipe-split parts as individual data fields
stock_code = parts[1]  # Gets "H0STCNT0", NOT stock code
current_price = int(parts[3])  # Gets "005930^134500^...", NOT price

# FIX:
# Need to split parts[3] by caret to get actual data fields
data_fields = parts[3].split('^')
stock_code = data_fields[0]     # "005930"
current_price = int(data_fields[2])  # "134500" (현재가 is field index 2)
```

### KIS Real-time Trade Data Fields (caret-separated within parts[3])
Reference: KIS Open API docs - H0STCNT0
```
[0]  종목코드
[1]  체결시간 (HHMMSS)
[2]  현재가
[3]  전일대비부호 (1:상한,2:상승,3:보합,4:하한,5:하락)
[4]  전일대비
[5]  전일대비율
[6]  가중평균가
[7]  시가
[8]  고가
[9]  저가
[10] 매도호가
[11] 매수호가
[12] 체결량
[13] 누적거래량
[14] 누적거래대금
[15] 매도체결건수
[16] 매수체결건수
[17] 순매수체결건수
[18] 체결강도
[19] 총매도수량
[20] 총매수수량
...
```

## Impact
- WebSocket connects successfully (approval key + connection log confirmed)
- Subscribe messages are sent correctly
- BUT incoming price data is silently dropped because TR_ID matching fails
- All positions show +0.0% profit throughout the entire monitoring period (09:26-15:30)
- Stop-loss, trailing stop, and take-profit conditions NEVER evaluated against real prices
- This means positions are held indefinitely without any risk management during market hours

## Evidence
- 2026-02-12 log: 13 status logs from 09:26 to 15:26 all show identical prices
- 2026-02-11 log: Same pattern (no `_log_status` entries found but monitoring ran)
- No `체결가 파싱 오류` errors in log = data silently dropped at TR_ID check
- error_2026-02-12.log is empty = no exceptions raised

## Recommended Fix
See the fix instructions above. After fixing, add a log line in `_parse_price_data` to confirm data reception:
```python
logger.debug(f"[{stock_code}] 체결: {current_price:,}원")
```
