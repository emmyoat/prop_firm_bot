from pathlib import Path
from src.utils.journal import TradeJournal


def test_log_virtual_trade(tmp_path: Path):
    test_csv = tmp_path / "test_trades.csv"
    journal = TradeJournal(filename=str(test_csv))

    virtual_trade = {
        "trade_id": "987654321",
        "symbol": "XAUUSD",
        "direction": "BUY",
        "label": "SCALP_M5",
        "entry": 2030.50,
        "current_sl": 2025.00,
        "current_tp": 2045.00,
        "lot_size": 0.1,
        "created_at": "2026-01-08T20:40:00Z",
    }

    journal.log_virtual_trade(
        trade=virtual_trade,
        exit_type="TP",
        exit_price=2045.00,
        pnl_pips=145.0,
        session="New York",
    )

    assert test_csv.exists()
    lines = test_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, f"Expected header + 1 trade row, got {len(lines)}"

    header = lines[0].split(",")
    assert header[0] == "Ticket"
    assert "Symbol" in header
    assert "Total PnL" in header

    trade_row = lines[1].split(",")
    assert trade_row[0] == "987654321"
    assert trade_row[1] == "XAUUSD"
    assert trade_row[2] == "BUY"
    assert float(trade_row[7]) == 2030.50
    assert float(trade_row[8]) == 2045.00
    assert float(trade_row[9]) == 145.0
    assert trade_row[13] == "New York"
    assert "SCALP_M5 TP" in trade_row[14]
