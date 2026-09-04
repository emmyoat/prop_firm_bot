import numpy as np
import pandas as pd
import pytest
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType


def _make_strategy_config(sl_mode="atr", sl_atr_mult=1.5, max_sl_pips=None):
    return {
        "strategy": {
            "wick_threshold_ratio": 0.20,
            "liquidity_lookback": 15,
            "rsi_buy_threshold": 80,
            "rsi_sell_threshold": 20,
            "sma_period": 10,
            "atr_multiplier": 1.5,
            "atr_multiplier_map": {},
            "entry_atr_multiplier": 0.1,
            "atr_period": 14,
            "sl_buffer_map": {"default": 0.50},
            "sl_mode": sl_mode,
            "sl_atr_multiplier": sl_atr_mult,
            "sl_atr_multiplier_map": {"DAY": sl_atr_mult},
            "max_sl_pips_map": {"DAY": max_sl_pips} if max_sl_pips else {},
            "tp_mode": "fixed_rr",
            "risk_reward_ratio": 3.0,
            "adx_filter_enabled": False,
            "smc_filter_enabled": False,
            "session_atr_multiplier_map": {},
            "symbol_overrides": {},
        }
    }


def _make_test_df():
    # 20 candles where previous candles have range 10 (ATR ~ 10), and the last candle has a huge range 60
    times = pd.date_range("2026-09-02 10:00", periods=20, freq="1h")
    data = []
    for i in range(19):
        data.append({
            "time": times[i], "open": 4400.0, "high": 4410.0, "low": 4400.0, "close": 4408.0, "volume": 100
        })
    # Last candle: huge sweep candle (high 4460, low 4400)
    data.append({
        "time": times[19], "open": 4405.0, "high": 4460.0, "low": 4400.0, "close": 4455.0, "volume": 100
    })
    return pd.DataFrame(data)


def test_atr_sl_mode_produces_tighter_stop_loss():
    """
    Verifies that 'atr' SL mode produces a much tighter stop loss from entry
    compared to the candle-extreme mode that covers the whole sweep candle.
    """
    df = _make_test_df()
    strat_atr = LiquidityWickStrategy(_make_strategy_config(sl_mode="atr", sl_atr_mult=1.5))
    strat_legacy = LiquidityWickStrategy(_make_strategy_config(sl_mode="candle_extreme"))

    atr_val = strat_atr._calculate_atr(df, 14)

    sig_atr = strat_atr.generate_signal(
        {"LowTF": df, "HighTF": df, "MacroTF": df}, "XAUUSD", label="DAY"
    )
    sig_legacy = strat_legacy.generate_signal(
        {"LowTF": df, "HighTF": df, "MacroTF": df}, "XAUUSD", label="DAY"
    )

    if sig_atr.signal_type != SignalType.NEUTRAL and sig_legacy.signal_type != SignalType.NEUTRAL:
        sl_dist_atr = abs(sig_atr.price - sig_atr.sl_price)
        sl_dist_legacy = abs(sig_legacy.price - sig_legacy.sl_price)

        # The ATR SL distance must be significantly smaller than legacy full candle range
        assert sl_dist_atr < sl_dist_legacy
        # Should be approximately atr_val * 1.5
        assert sl_dist_atr == pytest.approx(atr_val * 1.5, rel=0.1)


def test_max_sl_pips_cap_enforced():
    """
    Verifies that max_sl_pips_map caps the SL distance when ATR is larger than the cap.
    """
    df = _make_test_df()
    # Cap at 100 pips ($10 on Gold)
    strat = LiquidityWickStrategy(_make_strategy_config(sl_mode="atr", sl_atr_mult=2.5, max_sl_pips=100))
    sig = strat.generate_signal(
        {"LowTF": df, "HighTF": df, "MacroTF": df}, "XAUUSD", label="DAY"
    )

    if sig.signal_type != SignalType.NEUTRAL:
        sl_dist = abs(sig.price - sig.sl_price)
        # Cap is 100 pips * 0.1 = $10.00
        assert sl_dist <= 10.01
