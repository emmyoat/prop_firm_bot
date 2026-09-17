"""
Deterministic market fixtures for the signal-chain regression tests.

Each `fixture_*()` returns a single-timeframe frame with *explicit* candle
geometry, used by `tests/test_replay_diagnostic.py`. Each one is constructed so
the relevant liquidity level and SMA-40 are unambiguous, so the tests assert
mechanism, not luck.

The overall narrative reproduced is the 2026-09-15 XAUUSD event:

  1. price is BELOW its SMA-40 on the entry TF  ->  trend lock = SELL
  2. a bear-flag bar sweeps a recent high and closes red
     ->  the bot used to emit a SELL sweep (FALSE POSITIVE) right at the bottom
  3. a bullish reversal bar sweeps a recent low and closes green
     ->  the BUY used to be suppressed by the trend lock
  4. a momentum rally follows, where the RSI filter used to forbid the BUY
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# Builders
# ══════════════════════════════════════════════════════════════════════════════

def build_ohlc(times: pd.DatetimeIndex, closes, bar_range: float = 6.0, wick_frac: float = 0.30) -> pd.DataFrame:
    """Build a coherent OHLC frame from a close path."""
    closes = list(closes)
    rows = []
    prev = closes[0]
    for t, c in zip(times, closes):
        o = prev
        hi = max(o, c) + bar_range * wick_frac
        lo = min(o, c) - bar_range * wick_frac
        rows.append({"time": t, "open": o, "high": hi, "low": lo, "close": c, "volume": 100.0})
        prev = c
    return pd.DataFrame(rows)


def _framed(closes, start="2026-09-01 00:00", freq="15min", range_hint=5.0, wick_frac=0.30) -> pd.DataFrame:
    t = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return build_ohlc(t, closes, bar_range=range_hint, wick_frac=wick_frac)


def _set_bar(df: pd.DataFrame, idx: int, o: float, h: float, l: float, c: float) -> None:
    df.at[idx, "open"] = o
    df.at[idx, "high"] = h
    df.at[idx, "low"] = l
    df.at[idx, "close"] = c


# ══════════════════════════════════════════════════════════════════════════════
# Single-TF fixtures with explicit geometry (used by the regression tests)
# ══════════════════════════════════════════════════════════════════════════════

def fixture_suppressed_bullish_sweep() -> pd.DataFrame:
    """60 declining M15 bars (price < lagging SMA-40 -> trend lock = SELL) with a
    final bullish reversal sweep (deep low, green close above recent support).

    The strategy must now EMIT a BUY — proving the missed buy is captured.
    """
    closes = list(np.linspace(4400.0, 4215.0, 59)) + [4220.0]
    df = _framed(closes, range_hint=5.0, wick_frac=0.30)
    last = len(df) - 1
    # sweep below recent support (~4213), close green back above it, still < SMA-40
    _set_bar(df, last, o=4215.0, h=4235.0, l=4200.0, c=4220.0)
    return df


def fixture_false_sell_sweep() -> pd.DataFrame:
    """60 declining M15 bars (trend lock = SELL) with a final bear-flag bar that
    sweeps a recent high and closes red — the former false-positive SELL sweep.
    """
    closes = list(np.linspace(4400.0, 4290.0, 59)) + [4274.0]
    df = _framed(closes, range_hint=5.0, wick_frac=0.30)
    last = len(df) - 1
    # sweep above recent resistance (~4318), close red back below it
    _set_bar(df, last, o=4306.0, h=4335.0, l=4272.0, c=4274.0)
    return df


def fixture_bullish_sweep_in_uptrend() -> pd.DataFrame:
    """The SAME bullish reversal sweep candle as `fixture_suppressed_bullish_sweep`,
    but inside an UPTREND (price > SMA-40 -> trend lock = BUY).

    Counterfactual proof: identical candle, only the lagging trend context
    differs, and the sweep is continuation (not a reversal) — so it is not taken.
    """
    closes = list(np.linspace(4400.0, 4555.0, 45)) + [4560.0] * 14 + [4565.0]
    df = _framed(closes, range_hint=6.0, wick_frac=0.25)
    last = len(df) - 1
    # sweep below the recent (flat) support, close green back above it
    _set_bar(df, last, o=4565.0, h=4580.0, l=4530.0, c=4570.0)
    return df


def fixture_momentum_breakout_uptrend() -> pd.DataFrame:
    """60 rising M15 bars (price > SMA-40 -> trend lock = BUY) with a strong
    breakout close on the final bar, driving RSI overbought.
    """
    closes = list(np.linspace(4100.0, 4360.0, 59)) + [4380.0]
    df = _framed(closes, range_hint=6.0, wick_frac=0.25)
    last = len(df) - 1
    o = float(df.at[last, "open"])
    _set_bar(df, last, o=o, h=4399.0, l=o - 0.5, c=4396.0)
    return df


def synthetic_reversal_scenario() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Generates synthetic M15, H1, and D1 data reproducing the 2026-09-15 event."""
    start_time = pd.Timestamp("2026-09-13 00:00:00", tz="UTC")
    end_time = pd.Timestamp("2026-09-16 00:00:00", tz="UTC")
    times_m15 = pd.date_range(start_time, end_time, freq="15min")
    
    # Downtrend leading up to 2026-09-15 18:00
    n = len(times_m15)
    t_1800 = pd.Timestamp("2026-09-15 18:00:00", tz="UTC")
    idx_1800 = times_m15.get_loc(t_1800)
    
    closes = np.linspace(4450.0, 4220.0, idx_1800 + 1)
    post_closes = np.linspace(4225.0, 4350.0, n - (idx_1800 + 1))
    all_closes = np.concatenate([closes, post_closes])
    
    df_low = build_ohlc(times_m15, all_closes, bar_range=5.0, wick_frac=0.25)
    
    # Bar at 18:00: Bear-flag bar sweeping resistance (former false SELL)
    _set_bar(df_low, idx_1800, o=4228.0, h=4242.0, l=4218.0, c=4220.0)
    
    # Bar at 18:15: Bullish reversal sweep (missed BUY)
    idx_1815 = idx_1800 + 1
    _set_bar(df_low, idx_1815, o=4220.0, h=4238.0, l=4205.0, c=4226.0)
    
    # Subsequent rally
    for k in range(idx_1815 + 1, min(idx_1815 + 10, n)):
        c_k = 4226.0 + (k - idx_1815) * 6.0
        _set_bar(df_low, k, o=c_k - 4.0, h=c_k + 3.0, l=c_k - 5.0, c=c_k)
        
    times_h1 = pd.date_range(start_time, end_time, freq="1h")
    closes_h1 = np.interp(np.linspace(0, 1, len(times_h1)), np.linspace(0, 1, n), all_closes)
    df_high = build_ohlc(times_h1, closes_h1, bar_range=10.0, wick_frac=0.25)
    
    times_d1 = pd.date_range("2026-08-01 00:00:00", "2026-09-16 00:00:00", freq="1D", tz="UTC")
    closes_d1 = np.linspace(4500.0, 4250.0, len(times_d1))
    df_macro = build_ohlc(times_d1, closes_d1, bar_range=25.0, wick_frac=0.20)
    
    return df_low, df_high, df_macro, {"event_time": t_1800}

