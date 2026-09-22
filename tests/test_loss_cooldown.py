from datetime import datetime, timedelta, timezone
import pytest

from src.risk.risk_manager import RiskManager
from src.utils.state_store import StateStore


def create_test_config(cooldown_minutes=45):
    return {
        "system": {"magic_number": 999},
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


def test_no_cooldown_on_fresh_start(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    rm = RiskManager(create_test_config(45), store)
    rm.initialize_state()

    allowed, reason = rm.check_signal_allowed("XAUUSD")
    assert allowed is True
    assert reason == ""
    assert rm.get_loss_cooldown_remaining("XAUUSD") == 0.0


def test_no_cooldown_after_winning_trade(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    rm = RiskManager(create_test_config(45), store)
    rm.initialize_state()

    rm.record_paper_trade(pnl=150.0, symbol="XAUUSD")
    allowed, reason = rm.check_signal_allowed("XAUUSD")
    assert allowed is True
    assert rm.get_loss_cooldown_remaining("XAUUSD") == 0.0


def test_cooldown_triggers_on_loss(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    rm = RiskManager(create_test_config(45), store)
    rm.initialize_state()

    loss_time = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
    # Record a losing trade
    rm.record_paper_trade(pnl=-75.0, symbol="XAUUSD")

    # 10 minutes later (still inside 45-min cooldown)
    t_10m = datetime.now(timezone.utc)
    allowed, reason = rm.check_signal_allowed("XAUUSD", now=t_10m)
    assert allowed is False
    assert "Post-loss cooldown active" in reason
    assert "XAUUSD" in reason
    assert rm.get_loss_cooldown_remaining("XAUUSD", now=t_10m) > 40.0


def test_cooldown_expires_after_configured_minutes(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    rm = RiskManager(create_test_config(45), store)
    rm.initialize_state()

    rm.record_paper_trade(pnl=-50.0, symbol="XAUUSD")

    # 50 minutes into the future
    future_time = datetime.now(timezone.utc) + timedelta(minutes=50)
    allowed, reason = rm.check_signal_allowed("XAUUSD", now=future_time)
    assert allowed is True
    assert reason == ""
    assert rm.get_loss_cooldown_remaining("XAUUSD", now=future_time) == 0.0


def test_cooldown_persists_across_bot_restart(tmp_path):
    db_path = str(tmp_path / "state.db")
    store1 = StateStore(db_path)
    rm1 = RiskManager(create_test_config(45), store1)
    rm1.initialize_state()

    # Record loss in instance 1
    rm1.record_paper_trade(pnl=-60.0, symbol="XAUUSD")

    # Instance 2 (simulating worker restart)
    store2 = StateStore(db_path)
    rm2 = RiskManager(create_test_config(45), store2)
    rm2.initialize_state()

    t_check = datetime.now(timezone.utc) + timedelta(minutes=15)
    allowed, reason = rm2.check_signal_allowed("XAUUSD", now=t_check)
    assert allowed is False
    assert "Post-loss cooldown active" in reason
    # Roughly 30 minutes left
    remaining = rm2.get_loss_cooldown_remaining("XAUUSD", now=t_check)
    assert 29.0 <= remaining <= 31.0
