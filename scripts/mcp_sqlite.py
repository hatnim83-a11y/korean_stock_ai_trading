#!/usr/bin/env python3
"""MCP SQLite server wrapper - hardcoded DB path."""
import sys
sys.argv = ["mcp-server-sqlite", "--db-path", "/home/hatni/korean_stock_ai_trading/data/trading.db"]
from mcp_server_sqlite import main
main()
