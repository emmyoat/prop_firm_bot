"""
Stats Reporter — Paper Account / Signal-Only Mode
=================================================
Calculates and formats performance stats without MT5.
"""

from datetime import datetime, timedelta
import logging

logger = logging.getLogger("PropBot.Stats")


class StatsReporter:
    def __init__(self, magic_number: int):
        self.magic_number = magic_number

    def get_stats(self, risk_manager=None) -> dict:
        """
        Calculates stats from risk manager paper account.
        """
        if risk_manager is None:
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "profit": 0.0,
            }

        summary = risk_manager.get_summary()
        return {
            "trades": summary["signals_today"],
            "wins": summary["wins_today"],
            "losses": summary["losses_today"],
            "win_rate": (summary["wins_today"] / summary["signals_today"] * 100) if summary["signals_today"] > 0 else 0.0,
            "profit": summary["daily_pnl"],
        }

    def format_report(self, daily: dict, total: dict) -> str:
        """Formats stats into a readable string for Telegram."""
        return (
            f"📊 *Performance Report*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 *Today's Signals*\n"
            f"Signals: `{daily.get('trades', 0)}` (W: `{daily.get('wins', 0)}` | L: `{daily.get('losses', 0)}`)\n"
            f"Win Rate: `{daily.get('win_rate', 0.0):.1f}%`\n"
            f"Paper PnL: `${daily.get('profit', 0.0):+.2f}`\n"
        )
