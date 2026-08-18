from datetime import datetime, timezone

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

    changed = second.ensure_daily_rollover(
        datetime(2026, 8, 19, 0, 1, tzinfo=timezone.utc)
    )
    assert changed is True
    assert second.daily_pnl == 0.0
    assert second.signals_today == 0
    assert second.paper_pnl == 25.0
