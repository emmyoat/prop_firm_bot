import pytest
from main import (
    check_directional_conflict,
    sort_active_pairs_by_hierarchy,
)


def test_sort_active_pairs_by_hierarchy():
    """Verify that active pairs are sorted macro-to-micro (higher TF first)."""
    unordered_pairs = [
        {"low": "M5", "high": "H1", "label": "SCALP_M5"},
        {"low": "H4", "high": "D1", "label": "SWING"},
        {"low": "M15", "high": "H1", "label": "SCALP"},
        {"low": "H1", "high": "H4", "label": "DAY"},
    ]

    sorted_pairs = sort_active_pairs_by_hierarchy(unordered_pairs)
    labels = [p["label"] for p in sorted_pairs]

    # SWING (D1/H4) -> DAY (H4/H1) -> SCALP (H1/M15) -> SCALP_M5 (H1/M5)
    assert labels == ["SWING", "DAY", "SCALP", "SCALP_M5"]


def test_directional_conflict_no_active_trades():
    """When no active trades exist, any direction is allowed."""
    active_trades = []
    allowed, reason = check_directional_conflict(active_trades, "BUY", allow_opposing=False)
    assert allowed is True
    assert reason == ""

    allowed, reason = check_directional_conflict(active_trades, "SELL", allow_opposing=False)
    assert allowed is True
    assert reason == ""


def test_directional_conflict_blocks_opposing_direction():
    """An active BUY trade must block an incoming SELL trade on the same symbol."""
    active_trades = [
        {"trade_id": "XAUUSD_SCALP_1001", "symbol": "XAUUSD", "label": "SCALP", "direction": "BUY"}
    ]

    # Incoming SELL must be blocked
    allowed, reason = check_directional_conflict(active_trades, "SELL", allow_opposing=False)
    assert allowed is False
    assert "SCALP:BUY" in reason

    # Incoming BUY (same direction) is allowed
    allowed, reason = check_directional_conflict(active_trades, "BUY", allow_opposing=False)
    assert allowed is True
    assert reason == ""


def test_directional_conflict_blocks_buy_when_sell_active():
    """An active SELL trade must block an incoming BUY trade on the same symbol."""
    active_trades = [
        {"trade_id": "XAUUSD_SCALP_M5_2001", "symbol": "XAUUSD", "label": "SCALP_M5", "direction": "SELL"}
    ]

    # Incoming BUY must be blocked
    allowed, reason = check_directional_conflict(active_trades, "BUY", allow_opposing=False)
    assert allowed is False
    assert "SCALP_M5:SELL" in reason

    # Incoming SELL (same direction) is allowed
    allowed, reason = check_directional_conflict(active_trades, "SELL", allow_opposing=False)
    assert allowed is True
    assert reason == ""


def test_directional_conflict_allowed_when_configured():
    """When allow_opposing is True, opposing trades are not blocked."""
    active_trades = [
        {"trade_id": "XAUUSD_SCALP_1001", "symbol": "XAUUSD", "label": "SCALP", "direction": "BUY"}
    ]

    allowed, reason = check_directional_conflict(active_trades, "SELL", allow_opposing=True)
    assert allowed is True
    assert reason == ""


def test_multi_timeframe_scenario_today_reproduction():
    """
    Simulates the exact scenario that happened today:
    1. SCALP (M15) generates BUY.
    2. Trade is saved to active trades.
    3. SCALP_M5 (M5) evaluates and generates SELL.
    4. Directional lock must prevent SCALP_M5 SELL from firing.
    """
    simulated_active_trades = []

    # Step 1: SCALP emits BUY
    allowed_scalp, _ = check_directional_conflict(simulated_active_trades, "BUY", allow_opposing=False)
    assert allowed_scalp is True

    # Save active trade for SCALP BUY
    simulated_active_trades.append({
        "trade_id": "XAUUSD_SCALP_TODAY",
        "symbol": "XAUUSD",
        "label": "SCALP",
        "direction": "BUY",
        "entry": 4350.0,
        "sl": 4340.0,
        "tp": 4380.0,
    })

    # Step 2: SCALP_M5 generates SELL at the same minute
    allowed_m5, reason_m5 = check_directional_conflict(simulated_active_trades, "SELL", allow_opposing=False)

    # Must be BLOCKED
    assert allowed_m5 is False
    assert "Opposing active trade already exists" in reason_m5
    assert "SCALP:BUY" in reason_m5
