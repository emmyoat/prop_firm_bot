import pandas as pd
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.utils.state_store import StateStore
from main import _evaluate_active_trades


@pytest.fixture
def memory_store(tmp_path):
    db_file = tmp_path / "test_lifecycle.db"
    store = StateStore(str(db_file))
    return store


def test_tp_hit_before_sl_on_subsequent_candle(memory_store):
    """
    Ensure that when a trade hits TP on an earlier candle,
    it exits as TP_HIT even if later candles drop below the stop loss.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_1001",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4660.0,
        "sl": 4650.0,
        "tp": 4690.0,
        "initial_sl": 4650.0,
        "current_sl": 4650.0,
        "is_stop_order": 0,
        "triggered": 1,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4660.0,
        "lowest_price": 4660.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1: Reaches TP 4695.0
    # Bar 2: Drops to 4640.0 (below SL 4650.0)
    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00"), "open": 4660.0, "high": 4695.0, "low": 4658.0, "close": 4692.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00"), "open": 4692.0, "high": 4693.0, "low": 4640.0, "close": 4645.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": True,
            "breakeven_activation_pips": 250,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    # Trade should be closed as TP_HIT
    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "TP_HIT"
    assert kwargs["exit_price"] == 4690.0
    assert kwargs["pnl_pips"] == pytest.approx(300.0)

    # Trade should be removed from active trades
    assert len(memory_store.get_active_trades()) == 0


def test_sell_tp_hit_before_sl(memory_store):
    """
    Ensure sell trade hitting TP on an earlier bar is closed as TP_HIT.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_1002",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "SELL",
        "entry": 4660.0,
        "sl": 4670.0,
        "tp": 4630.0,
        "initial_sl": 4670.0,
        "current_sl": 4670.0,
        "is_stop_order": 0,
        "triggered": 1,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4660.0,
        "lowest_price": 4660.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    memory_store.save_active_trade(trade)

    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00"), "open": 4660.0, "high": 4662.0, "low": 4625.0, "close": 4628.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00"), "open": 4628.0, "high": 4680.0, "low": 4625.0, "close": 4675.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": True,
            "breakeven_activation_pips": 250,
            "trailing_stop_enabled": False,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "TP_HIT"
    assert kwargs["exit_price"] == 4630.0
    assert kwargs["pnl_pips"] == pytest.approx(300.0)
    assert len(memory_store.get_active_trades()) == 0


def test_breakeven_alert_and_exit(memory_store):
    """
    Ensure trade triggers breakeven when profit threshold reached,
    and if price falls back to entry, sends BE_HIT alert.
    """
    trade = {
        "trade_id": "XAUUSD_DAY_1003",
        "symbol": "XAUUSD",
        "label": "DAY",
        "direction": "BUY",
        "entry": 4650.0,
        "sl": 4630.0,
        "tp": 4710.0,
        "initial_sl": 4630.0,
        "current_sl": 4630.0,
        "is_stop_order": 0,
        "triggered": 1,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4650.0,
        "lowest_price": 4650.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1: Moves to +150 pips (4665.0) -> triggers Breakeven (threshold is 100 pips)
    # Bar 2: Retraces to 4648.0 (touches new SL 4650.0) -> BE_HIT
    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00"), "open": 4650.0, "high": 4665.0, "low": 4649.0, "close": 4662.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00"), "open": 4662.0, "high": 4663.0, "low": 4648.0, "close": 4648.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": True,
            "breakeven_activation_pips": 100,  # 10.0 on Gold
            "trailing_stop_enabled": False,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    mock_notifier.send_breakeven_alert.assert_called_once()
    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "BE_HIT"
    assert kwargs["exit_price"] == 4650.0
    assert kwargs["pnl_pips"] == pytest.approx(0.0)
    assert len(memory_store.get_active_trades()) == 0


def test_trailing_stop_update_and_exit(memory_store):
    """
    Ensure trailing stop updates SL as price moves in profit and exits with TRAIL_HIT.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_1004",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4660.0,
        "sl": 4650.0,
        "tp": 4720.0,
        "initial_sl": 4650.0,
        "current_sl": 4650.0,
        "is_stop_order": 0,
        "triggered": 1,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4660.0,
        "lowest_price": 4660.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1: Moves up to 4680.0 (+200 pips) -> trailing activation at 100 pips, dist 40 pips -> new SL = 4660 + 16 = 4676.0
    # Bar 2: Drops to 4675.0 (below 4676.0) -> TRAIL_HIT
    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00"), "open": 4660.0, "high": 4680.0, "low": 4660.0, "close": 4679.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00"), "open": 4679.0, "high": 4680.0, "low": 4674.0, "close": 4675.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": True,
            "trailing_stop_activation_pips": 100,
            "trailing_stop_distance_pips": 40,
            "trailing_step_pips": 20,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    mock_notifier.send_trailing_stop_alert.assert_called_once()
    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "TRAIL_HIT"
    assert kwargs["exit_price"] == 4676.0
    assert kwargs["pnl_pips"] == pytest.approx(160.0)
    assert len(memory_store.get_active_trades()) == 0


def test_pending_order_trigger_then_tp(memory_store):
    """
    Ensure a pending stop order is triggered on an intermediate bar and then hits TP.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_1005",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4670.0,
        "sl": 4655.0,
        "tp": 4690.0,
        "initial_sl": 4655.0,
        "current_sl": 4655.0,
        "is_stop_order": 1,
        "triggered": 0,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4670.0,
        "lowest_price": 4670.0,
        "lot_size": 0.01,
        "created_at": "2026-08-25T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1: Below entry (high 4668.0) -> not triggered
    # Bar 2: Reaches 4672.0 -> triggered
    # Bar 3: Rallies to 4695.0 -> TP_HIT
    df_data = [
        {"time": pd.to_datetime("2026-08-25 10:15:00"), "open": 4660.0, "high": 4668.0, "low": 4658.0, "close": 4665.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:30:00"), "open": 4665.0, "high": 4672.0, "low": 4664.0, "close": 4671.0, "volume": 100},
        {"time": pd.to_datetime("2026-08-25 10:45:00"), "open": 4671.0, "high": 4695.0, "low": 4670.0, "close": 4692.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    mock_notifier.send_trade_closed_alert.assert_called_once()
    kwargs = mock_notifier.send_trade_closed_alert.call_args[1]
    assert kwargs["exit_type"] == "TP_HIT"
    assert kwargs["exit_price"] == 4690.0
    assert kwargs["pnl_pips"] == pytest.approx(200.0)
    assert len(memory_store.get_active_trades()) == 0


def test_pending_sell_stop_trigger_no_false_sl(memory_store):
    """
    Ensure a pending SELL stop order triggered on a candle where the high reached
    the SL level BEFORE breakdown does not falsely exit as SL_HIT on the trigger candle.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_1006",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "SELL",
        "entry": 4437.0,
        "sl": 4455.0,
        "tp": 4380.0,
        "initial_sl": 4455.0,
        "current_sl": 4455.0,
        "is_stop_order": 1,
        "triggered": 0,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4437.0,
        "lowest_price": 4437.0,
        "lot_size": 0.01,
        "created_at": "2026-08-31T09:15:00+00:00",
        "updated_at": "2026-08-31T09:15:00+00:00",
    }
    memory_store.save_active_trade(trade)

    # Candle reaches high 4456.0 (before breakdown), then dumps to low 4435.0 (triggering entry 4437.0) and closes at 4438.0
    df_data = [
        {"time": pd.to_datetime("2026-08-31 09:15:00"), "open": 4450.0, "high": 4456.0, "low": 4435.0, "close": 4438.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    # Trade should be TRIGGERED, but NOT closed as SL_HIT
    mock_notifier.send_trade_closed_alert.assert_not_called()
    active = memory_store.get_active_trades()
    assert len(active) == 1
    assert active[0]["triggered"] == 1


def test_scalp_m5_fetches_correct_timeframe(memory_store):
    """
    Ensure SCALP_M5 and other active pair labels fetch their exact entry TF (e.g. M5 for SCALP_M5).
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_M5_1007",
        "symbol": "XAUUSD",
        "label": "SCALP_M5",
        "direction": "SELL",
        "entry": 4307.95,
        "sl": 4314.87,
        "tp": 4287.19,
        "initial_sl": 4314.87,
        "current_sl": 4314.87,
        "is_stop_order": 1,
        "triggered": 0,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4307.95,
        "lowest_price": 4307.95,
        "lot_size": 0.01,
        "created_at": "2026-09-02T11:12:05+00:00",
        "updated_at": "2026-09-02T11:12:05+00:00",
    }
    memory_store.save_active_trade(trade)

    df_data = [
        {"time": pd.to_datetime("2026-09-02 11:10:00"), "open": 4309.0, "high": 4310.0, "low": 4308.5, "close": 4309.2, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = False

    config = {
        "risk": {
            "breakeven_enabled": True,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        },
        "strategy": {
            "active_pairs": [
                {"low": "M5", "high": "M15", "label": "SCALP_M5"},
                {"low": "M15", "high": "H1", "label": "SCALP"},
            ]
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    # Verify fetch_data was called with M5 (not H4 or M15)
    mock_loader.fetch_data.assert_called_once_with("XAUUSD", "M5", n_bars=100)


def test_pending_sell_stop_trigger_no_false_tp_on_trigger_bar(memory_store):
    """
    Ensure a pending SELL stop order triggered on a candle where the low reached
    the TP level BEFORE/during trigger does not falsely exit as TP_HIT on the trigger candle
    if the candle close is above TP.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_M5_1008",
        "symbol": "XAUUSD",
        "label": "SCALP_M5",
        "direction": "SELL",
        "entry": 4385.42,
        "sl": 4388.67,
        "tp": 4375.68,
        "initial_sl": 4388.67,
        "current_sl": 4388.67,
        "is_stop_order": 1,
        "triggered": 0,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4385.42,
        "lowest_price": 4385.42,
        "lot_size": 0.03,
        "created_at": "2026-09-02T22:47:40+00:00",
        "updated_at": "2026-09-02T22:47:40+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1 (Trigger bar): Opens at 4387.5, High 4388.0, Low 4374.0 (deep wick), Close 4386.0
    # Entry (4385.42) triggers, but Close (4386.0) is above TP (4375.68). Should NOT exit as TP_HIT.
    df_data = [
        {"time": pd.to_datetime("2026-09-02 22:45:00"), "open": 4387.5, "high": 4388.0, "low": 4374.0, "close": 4386.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    # Trade should be TRIGGERED, but NOT closed as TP_HIT
    mock_notifier.send_trade_closed_alert.assert_not_called()
    active = memory_store.get_active_trades()
    assert len(active) == 1
    assert active[0]["triggered"] == 1


def test_pending_buy_stop_trigger_no_false_tp_on_trigger_bar(memory_store):
    """
    Ensure a pending BUY stop order triggered on a candle where the high reached
    the TP level BEFORE/during trigger does not falsely exit as TP_HIT on the trigger candle
    if the candle close is below TP.
    """
    trade = {
        "trade_id": "XAUUSD_SCALP_M5_1009",
        "symbol": "XAUUSD",
        "label": "SCALP_M5",
        "direction": "BUY",
        "entry": 4385.00,
        "sl": 4380.00,
        "tp": 4400.00,
        "initial_sl": 4380.00,
        "current_sl": 4380.00,
        "is_stop_order": 1,
        "triggered": 0,
        "be_alerted": 0,
        "last_trail_sl": 0.0,
        "highest_price": 4385.00,
        "lowest_price": 4385.00,
        "lot_size": 0.03,
        "created_at": "2026-09-02T22:47:40+00:00",
        "updated_at": "2026-09-02T22:47:40+00:00",
    }
    memory_store.save_active_trade(trade)

    # Bar 1 (Trigger bar): Opens at 4382.0, Low 4381.0, High 4405.0 (deep wick), Close 4386.0
    # Entry (4385.0) triggers, but Close (4386.0) is below TP (4400.0). Should NOT exit as TP_HIT.
    df_data = [
        {"time": pd.to_datetime("2026-09-02 22:45:00"), "open": 4382.0, "high": 4405.0, "low": 4381.0, "close": 4386.0, "volume": 100},
    ]
    df = pd.DataFrame(df_data)

    mock_loader = MagicMock()
    mock_loader.fetch_data.return_value = df

    mock_notifier = MagicMock()
    mock_notifier.enabled = True
    mock_notifier.token = "fake_token"
    mock_notifier.chat_id = "fake_chat"

    config = {
        "risk": {
            "breakeven_enabled": False,
            "trailing_stop_enabled": False,
            "pending_order_expiry_hours": 4,
        }
    }

    _evaluate_active_trades(memory_store, mock_loader, mock_notifier, config)

    # Trade should be TRIGGERED, but NOT closed as TP_HIT
    mock_notifier.send_trade_closed_alert.assert_not_called()
    active = memory_store.get_active_trades()
    assert len(active) == 1
    assert active[0]["triggered"] == 1



