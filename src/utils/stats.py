import MetaTrader5 as mt5
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("PropBot.Stats")

class StatsReporter:
    def __init__(self, magic_number: int):
        self.magic_number = magic_number

    def get_stats(self, days: int = 0) -> dict:
        """
        Calculates stats for the last 'days'. 
        If days=0, calculates for 'All Time' (or sensible limit like 30 days).
        Returns dict with: total_trades, wins, losses, win_rate, profit, equity, balance.
        """
        now = datetime.now()
        
        # Determine FROM date
        if days > 0:
            from_date = now - timedelta(days=days)
        else:
            from_date = datetime(2020, 1, 1) # Arbitrary start for "All Time"

        # Fetch History
        # We want 'Deals' (actual executions), filtering by ENTRY_OUT (Closures)
        try:
            deals = mt5.history_deals_get(from_date, now, group="*")
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
        
        report = (
            f"📊 **Performance Report**\n"
            f"------------------------\n"
            f"💰 Balance: ${balance:.2f} | Equity: ${equity:.2f}\n"
            f"\n"
            f"📅 **Today**\n"
            f"Trades: {daily['trades']} (W: {daily['wins']} | L: {daily['losses']})\n"
            f"Win Rate: {daily['win_rate']:.1f}%\n"
            f"PnL: ${daily['profit']:.2f}\n"
            f"\n"
            f"📈 **Total (All Time)**\n"
            f"Trades: {total['trades']}\n"
            f"Win Rate: {total['win_rate']:.1f}%\n"
            f"PnL: ${total['profit']:.2f}\n"
        )
        return report
