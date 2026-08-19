from datetime import datetime, timedelta, timezone

from src.risk.risk_manager import RiskManager
from src.utils.state_store import StateStore


def risk_config():
    return {
        "system": {"magic_number": 77},
        "runtime": {},
        "virtual_account": {"balance": 1000.0},
        "risk": {
            "account_equity_risk_pct": 1.0,
            "max_daily_loss_pct": 5.0,
            "max_overall_drawdown_pct": 10.0,
            "max_spread_points": 15,
            "martingale_multiplier": 1.0,
            "profit_target_daily_pct": 5.0,
        },
    }


def test_state_store_dedup_offset_and_runtime_round_trip(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))

    assert store.claim_signal("XAUUSD_DAY", "2026-08-18T12:00:00") is True
    assert store.claim_signal("XAUUSD_DAY", "2026-08-18T12:00:00") is False
    store.release_signal("XAUUSD_DAY", "2026-08-18T12:00:00")
    assert store.claim_signal("XAUUSD_DAY", "2026-08-18T12:00:00") is True

    store.save_telegram_offset(42, "telegram:123")
    store.set_runtime_value("last_clean_shutdown", False)
    assert store.get_telegram_offset("telegram:123") == 42
    assert store.get_runtime_value("last_clean_shutdown") is False
    assert store.integrity_check() is True


def test_risk_state_survives_restart_and_rolls_over_utc_day(tmp_path):
    path = str(tmp_path / "state.db")
    config = risk_config()

    first = RiskManager(config, StateStore(path))
    first.initialize_state()
    first.record_signal_sent()
    first.record_paper_trade(25.0)

    second = RiskManager(config, StateStore(path))
    second.initialize_state()
    assert second.signals_today == 1
    assert second.paper_pnl == 25.0
    assert second.daily_pnl == 25.0

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    changed = second.ensure_daily_rollover(tomorrow)
    assert changed is True
    assert second.daily_pnl == 0.0
    assert second.signals_today == 0
    assert second.paper_pnl == 25.0


def test_active_trade_lifecycle_persistence(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    trade = {
        "trade_id": "XAUUSD_DAY_12345",
        "symbol": "XAUUSD",
        "label": "DAY",
        "direction": "BUY",
        "entry": 4350.0,
        "sl": 4330.0,
        "tp": 4410.0,
        "initial_sl": 4330.0,
        "current_sl": 4330.0,
        "is_stop_order": True,
        "triggered": False,
        "be_alerted": False,
        "last_trail_sl": 0.0,
        "highest_price": 4350.0,
        "lowest_price": 4350.0,
        "lot_size": 0.01,
    }

    store.save_active_trade(trade)
    trades = store.get_active_trades("XAUUSD")
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "XAUUSD_DAY_12345"
    assert trades[0]["entry"] == 4350.0

    # Update trade with breakeven
    trade["triggered"] = True
    trade["be_alerted"] = True
    trade["current_sl"] = 4350.0
    store.save_active_trade(trade)

    updated = store.get_active_trades("XAUUSD")[0]
    assert updated["triggered"] == 1
    assert updated["be_alerted"] == 1
    assert updated["current_sl"] == 4350.0

    # Remove trade upon exit
    store.remove_active_trade("XAUUSD_DAY_12345")
    assert len(store.get_active_trades()) == 0
