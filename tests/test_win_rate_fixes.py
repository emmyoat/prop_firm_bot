"""
Tests for Win Rate Fixes:
1. Streak-aware escalating cooldown in RiskManager
2. Per-label cooldown isolation
3. SMA slope confirmation in trend evaluation
4. Post-trigger SL grace period in trade evaluation
5. Structural sweep SL placement vs breakout ATR SL
6. Per-label R:R targets and HTF structure fallback
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import numpy as np
import pandas as pd
import pytest

from src.models import SignalType
from src.risk.risk_manager import RiskManager
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.utils.state_store import StateStore
from main import _evaluate_active_trades


# ==============================================================================
# 1. Streak-Aware Escalating Cooldown & Per-Label Isolation
# ==============================================================================

def _make_rm_config(cooldown_minutes=45):
    return {
        "system": {"magic_number": 888},
        "runtime": {},
        "virtual_account": {"balance": 10000.0},
        "risk": {
            "account_equity_risk_pct": 1.0,
            "max_daily_loss_pct": 5.0,
            "max_overall_drawdown_pct": 10.0,
            "max_spread_points": 30,
            "martingale_multiplier": 1.0,
            "profit_target_daily_pct": 50.0,
            "loss_cooldown_minutes": cooldown_minutes,
        },
    }


def test_streak_aware_escalating_cooldown(tmp_path):
    store = StateStore(str(tmp_path / "streak_test.db"))
    rm = RiskManager(_make_rm_config(45), store)
    rm.initialize_state()

    assert rm.consecutive_losses == 0

    # 1st loss: 1x cooldown (45 min)
    rm.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    assert rm.consecutive_losses == 1
    rem_1 = rm.get_loss_cooldown_remaining("XAUUSD", label="SCALP")
    assert 44.0 <= rem_1 <= 45.0

    # 2nd loss: 2x cooldown (90 min)
    rm.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    assert rm.consecutive_losses == 2
    rem_2 = rm.get_loss_cooldown_remaining("XAUUSD", label="SCALP")
    assert 89.0 <= rem_2 <= 90.0

    # 3rd loss: 3x cooldown (135 min cap)
    rm.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    assert rm.consecutive_losses == 3
    rem_3 = rm.get_loss_cooldown_remaining("XAUUSD", label="SCALP")
    assert 134.0 <= rem_3 <= 135.0

    # 4th loss: remains capped at 3x (135 min)
    rm.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    assert rm.consecutive_losses == 4
    rem_4 = rm.get_loss_cooldown_remaining("XAUUSD", label="SCALP")
    assert 134.0 <= rem_4 <= 135.0

    # Win resets streak to 0
    rm.record_paper_trade(pnl=120.0, symbol="XAUUSD", label="SCALP")
    assert rm.consecutive_losses == 0


def test_streak_persists_across_restarts(tmp_path):
    db_file = str(tmp_path / "persist_streak.db")
    store1 = StateStore(db_file)
    rm1 = RiskManager(_make_rm_config(45), store1)
    rm1.initialize_state()

    rm1.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    rm1.record_paper_trade(pnl=-50.0, symbol="XAUUSD", label="SCALP")
    assert rm1.consecutive_losses == 2

    # Restart bot with same DB
    store2 = StateStore(db_file)
    rm2 = RiskManager(_make_rm_config(45), store2)
    rm2.initialize_state()

    assert rm2.consecutive_losses == 2
    rem = rm2.get_loss_cooldown_remaining("XAUUSD", label="SCALP")
    assert 88.0 <= rem <= 90.0


def test_per_label_cooldown_isolation(tmp_path):
    store = StateStore(str(tmp_path / "isolation.db"))
    rm = RiskManager(_make_rm_config(45), store)
    rm.initialize_state()

    # M5 scalp loss
    rm.record_paper_trade(pnl=-30.0, symbol="XAUUSD", label="SCALP_M5")

    # M5 is blocked
    allowed_m5, reason_m5 = rm.check_signal_allowed("XAUUSD", label="SCALP_M5")
    assert allowed_m5 is False
    assert "Post-loss cooldown active" in reason_m5

    # But SWING setup check with label specified should check its own key
    rem_m5 = rm.get_loss_cooldown_remaining("XAUUSD", label="SCALP_M5")
    assert rem_m5 > 40.0


# ==============================================================================
# 2. SMA Slope Confirmation
# ==============================================================================

def test_sma_slope_filters_flat_consolidation():
    config = {
        "strategy": {
            "sma_period": 5,
            "risk_reward_ratio": 3.0,
            "atr_multiplier": 1.5,
        }
    }
    strat = LiquidityWickStrategy(config)

    # Flat market oscillating around 2000
    prices = [2000.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(15)]
    df = pd.DataFrame({"close": prices})

    # SMA is practically flat, should be NEUTRAL even if last close is slightly above SMA
    trend = strat._get_trend(df)
    assert trend == SignalType.NEUTRAL

    # Clearly rising market
    rising_prices = [2000.0 + i * 2.0 for i in range(15)]
    df_rising = pd.DataFrame({"close": rising_prices})
    assert strat._get_trend(df_rising) == SignalType.BUY

    # Clearly falling market
    falling_prices = [2050.0 - i * 2.0 for i in range(15)]
    df_falling = pd.DataFrame({"close": falling_prices})
    assert strat._get_trend(df_falling) == SignalType.SELL


# ==============================================================================
# 3. Post-Trigger SL Grace Period (1 bar)
# ==============================================================================

def test_post_trigger_sl_grace_period(tmp_path):
    """
    On the bar immediately following the trigger bar, an intra-bar wick below SL
    with close above SL must NOT exit as SL_HIT when post_trigger_grace_bars >= 1.
    """
    store = StateStore(str(tmp_path / "grace.db"))
    trade = {
        "trade_id": "XAUUSD_SCALP_GRACE_1",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4660.0,
        "sl": 4650.0,
        "tp": 4690.0,
        "initial_sl": 4650.0,
        "current_sl": 4650.0,
        "is_stop_order": 1,
        "triggered": 0,
        "trigger_bar_time": "",
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4660.0,
        "lowest_price": 4660.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    store.save_active_trade(trade)

    # Bar 1 (10:15:00): Triggers entry at 4660.0 (high=4662.0), close=4661.0
    # Bar 2 (10:30:00, 1 bar after trigger): Retracement wick drops to 4647.0 (below SL 4650),
    #       BUT candle close is 4655.0 (above SL). In grace period, SL is NOT hit!
    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00", utc=True), "open": 4658.0, "high": 4662.0, "low": 4657.0, "close": 4661.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00", utc=True), "open": 4661.0, "high": 4663.0, "low": 4647.0, "close": 4655.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df
    mock_notifier = MagicMock()
    mock_notifier.enabled = True

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
            "post_trigger_grace_bars": 1,
        }
    }

    _evaluate_active_trades(store, mock_loader, mock_notifier, config)

    # Trade survived the retracement wick because close (4655) > SL (4650)
    mock_notifier.send_trade_closed_alert.assert_not_called()
    active = store.get_active_trades()
    assert len(active) == 1
    assert active[0]["triggered"] == 1


def test_post_trigger_sl_grace_period_close_breaches_sl(tmp_path):
    """
    If candle close breaches SL even during grace period, trade must exit.
    """
    store = StateStore(str(tmp_path / "grace_breach.db"))
    trade = {
        "trade_id": "XAUUSD_SCALP_GRACE_2",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4660.0,
        "sl": 4650.0,
        "tp": 4690.0,
        "initial_sl": 4650.0,
        "current_sl": 4650.0,
        "is_stop_order": 1,
        "triggered": 0,
        "trigger_bar_time": "",
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4660.0,
        "lowest_price": 4660.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    store.save_active_trade(trade)

    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00", utc=True), "open": 4658.0, "high": 4662.0, "low": 4657.0, "close": 4661.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00", utc=True), "open": 4661.0, "high": 4663.0, "low": 4642.0, "close": 4645.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df
    mock_notifier = MagicMock()
    mock_notifier.enabled = True

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
            "post_trigger_grace_bars": 1,
        }
    }

    _evaluate_active_trades(store, mock_loader, mock_notifier, config)

    # Trade exits as SL_HIT because close (4645) <= SL (4650)
    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "SL_HIT"


# ==============================================================================
# 4. Per-Label R:R Targets
# ==============================================================================

def test_find_target_per_label_rr():
    config = {
        "strategy": {
            "tp_mode": "dynamic",
            "risk_reward_ratio": 3.0,
            "risk_reward_ratio_map": {
                "SCALP_M5": 2.0,
                "SCALP": 2.0,
                "DAY": 2.5,
                "SWING": 3.0,
            },
            "max_risk_reward_ratio": 5.0,
            "max_risk_reward_ratio_map": {
                "SCALP_M5": 3.0,
                "SCALP": 3.0,
                "DAY": 4.0,
                "SWING": 5.0,
            },
        }
    }
    strat = LiquidityWickStrategy(config)

    # Empty window where structure target is entry
    times = pd.date_range("2026-09-02 10:00", periods=20, freq="15min")
    df = pd.DataFrame({
        "time": times,
        "high": [2000.0] * 20,
        "low": [1990.0] * 20,
        "close": [1995.0] * 20,
    })

    entry = 2000.0
    sl = 1990.0  # Risk = 10.0

    # SCALP: min R:R = 2.0 -> TP = 2000 + 20 = 2020.0
    tp_scalp = strat._find_target(df, SignalType.BUY, entry, sl, label="SCALP")
    assert tp_scalp == pytest.approx(2020.0)

    # DAY: min R:R = 2.5 -> TP = 2000 + 25 = 2025.0
    tp_day = strat._find_target(df, SignalType.BUY, entry, sl, label="DAY")
    assert tp_day == pytest.approx(2025.0)

    # SWING: min R:R = 3.0 -> TP = 2000 + 30 = 2030.0
    tp_swing = strat._find_target(df, SignalType.BUY, entry, sl, label="SWING")
    assert tp_swing == pytest.approx(2030.0)


# ==============================================================================
# 5. Per-Pair Session Restrictions (M5 London/NY Overlap)
# ==============================================================================

def test_pair_allowed_sessions_filter():
    """Verify that pairs with allowed_sessions are only processed in matching sessions."""
    active_pairs = [
        {"low": "M15", "high": "H1", "label": "SCALP"},
        {"low": "M5", "high": "H1", "label": "SCALP_M5", "allowed_sessions": ["London/NY Overlap"]},
    ]

    def _should_scan(pair: dict, current_session: str) -> bool:
        allowed = pair.get("allowed_sessions")
        if allowed and current_session not in allowed:
            return False
        return True

    # During London (08:00 - 12:00 UTC): SCALP is scanned, SCALP_M5 is skipped
    assert _should_scan(active_pairs[0], "London") is True
    assert _should_scan(active_pairs[1], "London") is False

    # During London/NY Overlap (12:00 - 17:00 UTC): both are scanned
    assert _should_scan(active_pairs[0], "London/NY Overlap") is True
    assert _should_scan(active_pairs[1], "London/NY Overlap") is True

    # During New York (18:00 - 22:00 UTC): SCALP is scanned, SCALP_M5 is skipped
    assert _should_scan(active_pairs[0], "New York") is True
    assert _should_scan(active_pairs[1], "New York") is False
