import csv
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("PropBot.Journal")

class TradeJournal:
    def __init__(self, filename: str = "trades.csv"):
        self.filename = filename
        self._initialize_csv()

    def _initialize_csv(self):
        """Creates the CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.filename):
            headers = [
                "Ticket", "Symbol", "Type", "Entry Time", "Exit Time", 
                "Duration (Min)", "Volume", "Entry Price", "Exit Price", 
                "Profit", "Commission", "Swap", "Total PnL", "Session", "Comment"
            ]
            try:
                parent_dir = os.path.dirname(self.filename)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(self.filename, mode='w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                logger.info(f"Journal: Created new trade log at {self.filename}")
            except Exception as e:
                logger.error(f"Journal: Failed to initialize CSV: {e}")

    def _get_session(self, dt: datetime) -> str:
        """Determines the trading session based on UTC time."""
        hour = dt.hour
        if 8 <= hour < 13:
            return "London"
        elif 13 <= hour < 17:
            return "London/NY"
        elif 17 <= hour < 22:
            return "New York"
        elif 22 <= hour or hour < 8:
            return "Asia/Sydney"
        return "Unknown"

    def log_virtual_trade(self, trade: dict, exit_type: str, exit_price: float, pnl_pips: float, session: str = ""):
        """
        Logs a virtual (signal-only) trade closure to the CSV file.
        Uses the trade dict from StateStore instead of MT5 deal objects.
        """
        try:
            entry_time_str = trade.get("created_at", "")
            exit_time = datetime.now(timezone.utc)

            # Calculate duration
            duration = 0.0
            try:
                if entry_time_str:
                    # Handle ISO format with or without timezone
                    entry_clean = entry_time_str.replace("+00:00", "").replace("Z", "")
                    entry_time = datetime.fromisoformat(entry_clean)
                    duration = (exit_time - entry_time).total_seconds() / 60.0
            except Exception:
                pass

            # Determine session from entry time if not provided
            if not session:
                try:
                    entry_clean = entry_time_str.replace("+00:00", "").replace("Z", "")
                    entry_dt = datetime.fromisoformat(entry_clean)
                    session = self._get_session(entry_dt)
                except Exception:
                    session = "Unknown"

            # Build comment from exit type and label
            label = trade.get("label", "")
            comment = f"{label} {exit_type}".strip()

            row = [
                trade.get("trade_id", ""),
                trade.get("symbol", ""),
                trade.get("direction", ""),
                entry_time_str,
                exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                round(duration, 2),
                trade.get("lot_size", 0.01),
                trade.get("entry", 0.0),
                round(exit_price, 5),
                round(pnl_pips, 2),      # Profit in pips
                0.0,                      # Commission (N/A in signal mode)
                0.0,                      # Swap (N/A in signal mode)
                round(pnl_pips, 2),       # Total PnL = pips (no commission/swap)
                session,
                comment,
            ]

            with open(self.filename, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)

            logger.info(f"Journal: Logged virtual trade {trade.get('trade_id', '?')} — {exit_type} ({pnl_pips:+.0f} pips)")

        except Exception as e:
            logger.error(f"Journal: Error logging virtual trade: {e}")

