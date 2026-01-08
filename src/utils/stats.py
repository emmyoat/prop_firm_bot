import MetaTrader5 as mt5
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("PropBot.Stats")

class StatsReporter:
    def __init__(self, magic_number: int):
        self.magic_number = magic_number

    def get_stats(self, days: int = 0, since_midnight: bool = False) -> dict:
        """
        Calculates stats.
        days > 0: Rolling window (e.g., last 24h).
        since_midnight=True: From 00:00 today (overrides days).
        days=0: All Time.
        """
        now = datetime.now()
        
        # Determine FROM date
        if since_midnight:
             from_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif days > 0:
            from_date = now - timedelta(days=days)
        else:
            from_date = datetime(2020, 1, 1) # Arbitrary start for "All Time"

        # Fetch History
        # We want 'Deals' (actual executions), filtering by ENTRY_OUT (Closures)
        try:
            # FIX: Add buffer to 'to_date' to account for Server Time being ahead of Local Time
            to_date = now + timedelta(days=1)
            deals = mt5.history_deals_get(from_date, to_date, group="*")
        except Exception as e:
            logger.error(f"Failed to fetch history: {e}")
            return None

        if deals is None:
            return None

        total_trades = 0
        wins = 0
        losses = 0
        total_profit = 0.0
        
        for deal in deals:
            # Check Magical Number
            if deal.magic != self.magic_number:
                continue
                
            # We only care about exits (Profit realization)
            if deal.entry == mt5.DEAL_ENTRY_OUT or deal.entry == mt5.DEAL_ENTRY_OUT_BY:
                total_trades += 1
                total_profit += deal.profit + deal.swap + deal.commission
                
                if deal.profit > 0:
                    wins += 1
                else:
                    # Including 0 as loss or break-even (usually counts against win-rate in prop firms)
                    losses += 1

        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        return {
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "profit": total_profit
        }

    def format_report(self, daily: dict, total: dict) -> str:
        """Formats stats into a readable string for Logs/Telegram"""
        
        # Get Current Account Info for Context
        acc = mt5.account_info()
        balance = acc.balance if acc else 0.0
        equity = acc.equity if acc else 0.0
        # Get Currency Symbol (or Code)
        currency = acc.currency if acc else "$"
        
        report = (
            f"📊 **Performance Report**\n"
            f"------------------------\n"
            f"💰 Balance: {currency}{balance:.2f} | Equity: {currency}{equity:.2f}\n"
            f"\n"
            f"📅 **Today**\n"
            f"Trades: {daily['trades']} (W: {daily['wins']} | L: {daily['losses']})\n"
            f"Win Rate: {daily['win_rate']:.1f}%\n"
            f"PnL: {currency}{daily['profit']:.2f}\n"
            f"\n"
            f"📈 **Total (All Time)**\n"
            f"Trades: {total['trades']}\n"
            f"Win Rate: {total['win_rate']:.1f}%\n"
            f"PnL: {currency}{total['profit']:.2f}\n"
        )
        return report
