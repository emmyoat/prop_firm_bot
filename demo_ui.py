"""
Quick demo of the Rich terminal UI with mock data.
Run: python demo_ui.py
"""
import sys, time, threading
sys.path.insert(0, '.')

# Minimal mock config
config = {
    "system": {"magic_number": 123456, "symbol_list": ["XAUUSD", "GBPUSD"]},
    "virtual_account": {"balance": 10000.0},
    "strategy": {"active_pairs": [
        {"low": "H4", "high": "D1", "label": "SWING"},
        {"low": "H1", "high": "H4", "label": "DAY"},
    ]},
}

from src.ui.terminal_ui import TerminalUI

ui = TerminalUI(config)
ui.print_startup_banner()
ui.start()

# Populate with mock data
ui.update_status(state="RUNNING", session="London", news_blocked=False)
ui.update_price("XAUUSD", 2384.55)
ui.update_price("GBPUSD", 1.26812)

ui.add_log("TwelveData API: Connected ✓", "SUCCESS")
ui.add_log("Strategy loaded: LiquidityWickStrategy", "INFO")
ui.add_log("Virtual account: $10,000.00", "INFO")
ui.add_log("Session: London (07:00–12:00 UTC)", "INFO")
ui.add_log("Scanning XAUUSD H4/D1...", "INFO")

time.sleep(1)

ui.add_signal({
    "time": "08:14",
    "symbol": "XAUUSD",
    "timeframe": "H4",
    "direction": "BUY",
    "entry": 2384.55,
    "sl":    2372.10,
    "tp":    2421.00,
    "rr":    2.93,
    "lot":   0.05,
    "comment": "SWING Liquidity Wick Sweep [SMC:35]",
})
ui.add_log("✅ Signal sent: XAUUSD BUY | R:R 2.93 | Lot 0.05", "SUCCESS")
ui.update_status(signals_today=1, wins_today=0, losses_today=0, paper_pnl=0.0)

time.sleep(1.5)

ui.add_signal({
    "time": "09:47",
    "symbol": "GBPUSD",
    "timeframe": "H1",
    "direction": "SELL",
    "entry": 1.26812,
    "sl":    1.27150,
    "tp":    1.26120,
    "rr":    2.05,
    "lot":   0.03,
    "comment": "DAY Liquidity Wick Sweep",
})
ui.add_log("✅ Signal sent: GBPUSD SELL | R:R 2.05 | Lot 0.03", "SUCCESS")
ui.update_status(signals_today=2, paper_pnl=-12.50)

time.sleep(1.5)
ui.add_log("Scanning XAUUSD H1/H4...", "INFO")
ui.add_log("[DEBUG] XAUUSD: No Buy Setup (Close 2385.10 !> Res 2391.50)", "DEBUG")
ui.add_log("[DEBUG] GBPUSD: Trend Misalignment — skipping.", "DEBUG")
ui.add_log("Waiting for next candle close...", "INFO")

time.sleep(30)

ui.stop()
print("\nDemo complete.")
