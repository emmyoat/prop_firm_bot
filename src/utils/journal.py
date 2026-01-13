import csv
import os
import logging
from datetime import datetime

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
                with open(self.filename, mode='w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                logger.info(f"Journal: Created new trade log at {self.filename}")
            except Exception as e:
                logger.error(f"Journal: Failed to initialize CSV: {e}")

    def _get_session(self, dt: datetime) -> str:
        """Determines the trading session based on UTC time."""
        # Note: MT5 time is usually Broker time. We assume internal logic uses UTC or UTC+2/3.
        # For simplicity, we use the hour of the entry time.
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

    def log_trade(self, deal_exit, deal_entry):
        """
        Logs a closed trade to the CSV file.
        deal_exit: The exit deal from MT5 history.
        deal_entry: The corresponding entry deal.
        """
        try:
            # Entry Info
            entry_time = datetime.fromtimestamp(deal_entry.time)
            exit_time = datetime.fromtimestamp(deal_exit.time)
            
            # Duration in Minutes
            duration = (exit_time - entry_time).total_seconds() / 60
            
            # Type String
            trade_type = "BUY" if deal_entry.type == 0 else "SELL" # 0=BUY, 1=SELL
            
            # Determine Session
            session = self._get_session(entry_time)
            
            row = [
                deal_exit.position_id,
                deal_exit.symbol,
                trade_type,
                entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                round(duration, 2),
                deal_exit.volume,
                deal_entry.price,
                deal_exit.price,
                round(deal_exit.profit, 2),
                round(deal_exit.commission, 2),
                round(deal_exit.swap, 2),
                round(deal_exit.profit + deal_exit.commission + deal_exit.swap, 2),
                session,
                deal_entry.comment
            ]
            
            with open(self.filename, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
            
            logger.info(f"Journal: Logged trade for ticket {deal_exit.position_id} to CSV.")
            
        except Exception as e:
            logger.error(f"Journal: Error logging trade: {e}")
