import os
import sys
from unittest.mock import MagicMock
# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.journal import TradeJournal

def test_journal():
    print("Starting TradeJournal Verification...")
    
    # Remove existing test file if exists
    test_file = "test_trades.csv"
    if os.path.exists(test_file):
        os.remove(test_file)
        
    journal = TradeJournal(filename=test_file)
    
    # Mock MT5 Deal Objects
    mock_entry = MagicMock()
    mock_entry.time = 1736368800 # 2026-01-08 20:40:00
    mock_entry.type = 0 # BUY
    mock_entry.price = 2030.50
    mock_entry.comment = "Liquidity Wick Sweep (BUY)"
    
    mock_exit = MagicMock()
    mock_exit.position_id = 987654321
    mock_exit.symbol = "XAUUSD"
    mock_exit.time = 1736369100 # 2026-01-08 20:45:00 (5 min duration)
    mock_exit.price = 2035.00
    mock_exit.volume = 0.1
    mock_exit.profit = 45.00
    mock_exit.commission = -0.50
    mock_exit.swap = 0.0
    
    # Log the mock trade
    journal.log_trade(mock_exit, mock_entry)
    
    # Verify file exists and has content
    assert os.path.exists(test_file), "CSV File not created."
    with open(test_file, 'r') as f:
        lines = f.readlines()
        print(f"File created with {len(lines)} lines.")
        assert len(lines) == 2, f"Unexpected line count: {len(lines)}"
        print(f"Last Line: {lines[-1].strip()}")

if __name__ == "__main__":
    if test_journal():
        print("\nVerification Passed! Journaling is working correctly.")
    else:
        print("\nVerification Failed.")
        sys.exit(1)
