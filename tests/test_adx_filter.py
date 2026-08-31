"""Tests for the ADX Range Filter in LiquidityWickStrategy."""
import numpy as np
import pandas as pd
import pytest
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType


def _make_config(adx_enabled, adx_threshold=25):
    return {
        "strategy": {
            "wick_threshold_ratio": 0.40, "liquidity_lookback": 15,
            "rsi_buy_threshold": 70, "rsi_sell_threshold": 30,
            "sma_period": 40, "atr_multiplier": 1.5,
            "atr_multiplier_map": {}, "entry_atr_multiplier": 0.1,
            "atr_period": 14, "sl_buffer_map": {"default": 0.50},
            "tp_mode": "fixed_rr", "risk_reward_ratio": 3.0,
            "adx_filter_enabled": adx_enabled,
            "adx_min_threshold": adx_threshold,
            "adx_period": 14, "smc_filter_enabled": False,
            "session_atr_multiplier_map": {}, "symbol_overrides": {},
        }
    }


def _make_ranging_df(n=60, base=4440.0, noise=0.5):
    # Mean-centred random walk: tiny alternating moves, no directional bias -> low ADX.
    np.random.seed(99)
    times = pd.date_range("2026-08-31 07:00", periods=n, freq="15min")
    deltas = np.random.uniform(-noise, noise, n)
    closes = base + np.cumsum(deltas - deltas.mean())
    opens = np.roll(closes, 1); opens[0] = closes[0]
    highs = np.maximum(opens, closes) + 0.2
    lows  = np.minimum(opens, closes) - 0.2
    return pd.DataFrame({"time": times, "open": opens, "high": highs,
                          "low": lows, "close": closes, "volume": 100})


def _make_trending_df(n=60, start=4500.0, step=-2.0):
    times = pd.date_range("2026-08-31 07:00", periods=n, freq="15min")
    closes = np.array([start + i * step for i in range(n)])
    highs = closes + 3.0
    lows = closes - 3.0
    opens = np.roll(closes, 1); opens[0] = closes[0]
    return pd.DataFrame({"time": times, "open": opens, "high": highs,
                          "low": lows, "close": closes, "volume": 100})


def test_adx_filter_blocks_ranging_market():
    strategy = LiquidityWickStrategy(_make_config(adx_enabled=True, adx_threshold=25))
    df = _make_ranging_df()
    adx = strategy._calculate_adx(df, 14)
    assert adx < 25, f"Expected low ADX for ranging data, got {adx:.2f}"
    signal = strategy.generate_signal(
        {"LowTF": df, "HighTF": df, "MacroTF": df, "session_name": "London"},
        "XAUUSD", label="SCALP")
    assert signal.signal_type == SignalType.NEUTRAL
    assert "ADX too low" in signal.comment


def test_adx_filter_disabled_allows_ranging():
    strategy = LiquidityWickStrategy(_make_config(adx_enabled=False))
    df = _make_ranging_df()
    signal = strategy.generate_signal(
        {"LowTF": df, "HighTF": df, "MacroTF": df, "session_name": "London"},
        "XAUUSD", label="SCALP")
    assert "ADX too low" not in signal.comment


def test_adx_calculation_trending_data():
    strategy = LiquidityWickStrategy(_make_config(adx_enabled=True))
    df = _make_trending_df()
    adx = strategy._calculate_adx(df, 14)
    assert adx >= 25, f"Expected high ADX for trending data, got {adx:.2f}"


def test_adx_calculation_insufficient_data():
    strategy = LiquidityWickStrategy(_make_config(adx_enabled=True))
    df = _make_ranging_df(n=10)
    adx = strategy._calculate_adx(df, 14)
    assert adx == 0.0
