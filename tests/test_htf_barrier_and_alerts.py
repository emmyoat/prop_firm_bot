import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType
from src.utils.notifications import TelegramNotifier


def _make_bars(n=30, base=100.0, trend_dir=0.0):
    times = pd.date_range("2026-10-01 00:00:00", periods=n, freq="5min", tz="UTC")
    closes = [base + i * trend_dir for i in range(n)]
    opens = [c - trend_dir * 0.5 for c in closes]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100] * n,
    })


class TestHTFBarrierAndAlerts(unittest.TestCase):
    def setUp(self):
        self.config = {
            "strategy": {
                "wick_threshold_ratio": 0.35,
                "liquidity_lookback": 10,
                "rsi_period": 14,
                "sma_period": 10,
                "risk_reward_ratio": 2.0,
                "adx_filter_enabled": False,
                "htf_barrier_filter_enabled": True,
                "htf_barrier_atr_mult": 1.0,
                "sl_mode": "atr",
                "sl_atr_multiplier": 1.5,
                "allowed_setups_map": {"SCALP": ["breakout", "sweep"]},
                "symbol_overrides": {
                    "XAUUSD": {
                        "require_trend_alignment": False,
                        "allow_entry_trend_only": True,
                    }
                }
            }
        }
        self.strategy = LiquidityWickStrategy(self.config)

    def test_sell_breakout_blocked_by_htf_support(self):
        """A SELL breakout on LowTF running directly into an HTF support must be blocked."""
        # LowTF is in downtrend, breaking below local support at 98.0
        df_entry = _make_bars(n=30, base=100.0, trend_dir=-0.2)
        # Create a strong breakdown candle at the end
        df_entry.iloc[-1, df_entry.columns.get_loc("open")] = 94.5
        df_entry.iloc[-1, df_entry.columns.get_loc("close")] = 93.0
        df_entry.iloc[-1, df_entry.columns.get_loc("low")] = 92.8
        df_entry.iloc[-1, df_entry.columns.get_loc("high")] = 94.6

        # HTF has a swing low support sitting right at 92.5
        df_htf = _make_bars(n=30, base=95.0, trend_dir=-0.1)
        # Place a pivot low at bar 15: low 92.5
        df_htf.iloc[15, df_htf.columns.get_loc("low")] = 92.5
        df_htf.iloc[14, df_htf.columns.get_loc("low")] = 94.0
        df_htf.iloc[16, df_htf.columns.get_loc("low")] = 94.0

        signal = self.strategy.generate_signal(
            {"LowTF": df_entry, "HighTF": df_htf}, "XAUUSD", label="SCALP"
        )
        self.assertEqual(signal.signal_type, SignalType.NEUTRAL)
        self.assertIn("HTF support barrier", signal.comment)

    def test_buy_breakout_blocked_by_htf_resistance(self):
        """A BUY breakout on LowTF running directly into an HTF resistance must be blocked."""
        # LowTF is in uptrend, breaking above local resistance
        df_entry = _make_bars(n=30, base=100.0, trend_dir=0.2)
        df_entry.iloc[-1, df_entry.columns.get_loc("open")] = 106.0
        df_entry.iloc[-1, df_entry.columns.get_loc("close")] = 107.5
        df_entry.iloc[-1, df_entry.columns.get_loc("low")] = 105.8
        df_entry.iloc[-1, df_entry.columns.get_loc("high")] = 107.6

        # HTF has a swing high resistance sitting right at 108.0
        df_htf = _make_bars(n=30, base=105.0, trend_dir=0.1)
        df_htf.iloc[15, df_htf.columns.get_loc("high")] = 108.0
        df_htf.iloc[14, df_htf.columns.get_loc("high")] = 106.5
        df_htf.iloc[16, df_htf.columns.get_loc("high")] = 106.5

        signal = self.strategy.generate_signal(
            {"LowTF": df_entry, "HighTF": df_htf}, "XAUUSD", label="SCALP"
        )
        self.assertEqual(signal.signal_type, SignalType.NEUTRAL)
        self.assertIn("HTF resistance barrier", signal.comment)

    def test_breakout_allowed_when_htf_clearance_is_sufficient(self):
        """When HTF support is comfortably far away, the breakout is allowed."""
        df_entry = _make_bars(n=30, base=100.0, trend_dir=-0.2)
        df_entry.iloc[-1, df_entry.columns.get_loc("open")] = 94.5
        df_entry.iloc[-1, df_entry.columns.get_loc("close")] = 93.0
        df_entry.iloc[-1, df_entry.columns.get_loc("low")] = 92.8
        df_entry.iloc[-1, df_entry.columns.get_loc("high")] = 94.6

        # HTF is in clear higher territory, with nearest support down at 80.0
        df_htf = _make_bars(n=30, base=120.0, trend_dir=-0.5)
        df_htf.iloc[15, df_htf.columns.get_loc("low")] = 80.0

        signal = self.strategy.generate_signal(
            {"LowTF": df_entry, "HighTF": df_htf}, "XAUUSD", label="SCALP"
        )
        self.assertEqual(signal.signal_type, SignalType.SELL)
        self.assertIn("Breakout", signal.comment)

    def test_telegram_signal_alert_pending_order_formatting(self):
        """send_signal_alert must clearly designate PENDING STOP orders with instructions."""
        notifier = TelegramNotifier(token="fake_token", chat_id="123456", enabled=True)
        notifier._request = MagicMock(return_value={"ok": True})

        delivered = notifier.send_signal_alert(
            symbol="XAUUSD",
            direction="SELL",
            entry=4149.52,
            sl=4155.68,
            tp=4137.19,
            rr=2.0,
            lot_size=0.01,
            timeframe="M5",
            label="SCALP_M5",
            comment="Breakout",
            is_stop_order=True,
        )
        self.assertTrue(delivered)
        call_args = notifier._request.call_args[1]
        msg = call_args["json"]["text"]
        self.assertIn("SELL STOP (Pending)", msg)
        self.assertIn("PENDING STOP ORDER", msg)
        self.assertIn("DO NOT ENTER AT MARKET", msg)

    def test_telegram_signal_alert_market_order_formatting(self):
        """send_signal_alert must clearly designate MARKET orders when is_stop_order is False."""
        notifier = TelegramNotifier(token="fake_token", chat_id="123456", enabled=True)
        notifier._request = MagicMock(return_value={"ok": True})

        delivered = notifier.send_signal_alert(
            symbol="XAUUSD",
            direction="BUY",
            entry=4160.00,
            sl=4155.00,
            tp=4170.00,
            rr=2.0,
            lot_size=0.01,
            timeframe="M15",
            label="SCALP",
            comment="Market signal",
            is_stop_order=False,
        )
        self.assertTrue(delivered)
        call_args = notifier._request.call_args[1]
        msg = call_args["json"]["text"]
        self.assertIn("BUY SIGNAL (Market)", msg)
        self.assertIn("MARKET ORDER", msg)

    def test_telegram_order_triggered_and_expired_alerts(self):
        """Test send_order_triggered_alert and send_order_expired_alert."""
        notifier = TelegramNotifier(token="fake_token", chat_id="123456", enabled=True)
        notifier._request = MagicMock(return_value={"ok": True})

        trig_res = notifier.send_order_triggered_alert(
            symbol="XAUUSD",
            label="SCALP_M5",
            direction="SELL",
            entry=4149.52,
            current_price=4149.20,
            sl=4155.68,
            tp=4137.19,
        )
        self.assertTrue(trig_res)
        trig_msg = notifier._request.call_args[1]["json"]["text"]
        self.assertIn("ORDER TRIGGERED & ACTIVE", trig_msg)
        self.assertIn("LIVE", trig_msg)

        exp_res = notifier.send_order_expired_alert(
            symbol="XAUUSD",
            label="SCALP_M5",
            direction="SELL",
            entry=4149.52,
            expiry_hours=4,
        )
        self.assertTrue(exp_res)
        exp_msg = notifier._request.call_args[1]["json"]["text"]
        self.assertIn("PENDING ORDER EXPIRED", exp_msg)
        self.assertIn("Cancel pending `SELL STOP`", exp_msg)


if __name__ == "__main__":
    unittest.main()
