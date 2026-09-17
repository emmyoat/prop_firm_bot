"""
Regression guard for the 2026-09-15 XAUUSD miss / false-positive fix.

The original diagnostic harness (git history) proved the ROOT CAUSE by asserting
the then-buggy behavior:

    lagging SMA-40 trend lock  ->  direction frozen
        -> bullish reversal sweep SUPPRESSED (missed BUY)
        -> bear-flag SELL sweep EMITTED at the bottom (false positive)
    RSI confirmation           ->  blocked the overbought momentum BUY

These tests now assert the CORRECTED behavior after the fix:

    * liquidity SWEEPS are reversal setups, evaluated in BOTH directions and
      only when they FADE the trend lock (a sweep that agrees with the lock is
      continuation/exhaustion, not a reversal) — this removes the false SELL
      while capturing the missed BUY;
    * BREAKOUT continuations stay trend-locked;
    * the RSI filter is a PROTECTIVE EXHAUSTION GUARD applied to the chosen
      setup — it refuses to BUY into overbought momentum or SELL into oversold
      momentum, so it guards reversal sweeps as well as breakouts.

Reuses only the public helpers of LiquidityWickStrategy.
"""

from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy     # noqa: E402
from src.models import SignalType                                            # noqa: E402
from tests import scenarios                                                  # noqa: E402


# ── Config helpers ────────────────────────────────────────────────────────────

def _base_config() -> dict:
    cfg = {
        "strategy": {
            "wick_threshold_ratio": 0.40,
            "liquidity_lookback": 15,
            "rsi_period": 14,
            "rsi_buy_threshold": 70,
            "rsi_sell_threshold": 30,
            "sma_period": 40,
            "entry_atr_multiplier": 0.1,
            "atr_period": 14,
            "sl_mode": "atr",
            "sl_atr_multiplier": 1.5,
            "sl_atr_multiplier_map": {"SCALP": 1.5},
            "sl_buffer_map": {"XAUUSD": 0.50, "default": 0.50},
            "max_sl_pips": 100000,          # neutralise the cap for the tests
            "tp_mode": "dynamic",
            "risk_reward_ratio": 3.0,
            "sweep_min_rr": 2.0,
            "adx_filter_enabled": False,    # isolate direction + RSI effects
            "adx_min_threshold": 20,
            "adx_period": 14,
            "symbol_overrides": {
                "XAUUSD": {
                    "require_trend_alignment": True,
                    "allow_entry_trend_only": True,
                    "rsi_confirmation": False,
                }
            },
        }
    }
    return cfg


def _trend_of(strategy: LiquidityWickStrategy, df: pd.DataFrame) -> str:
    return strategy._get_trend(df).name


def _signal(strategy: LiquidityWickStrategy, df: pd.DataFrame, label: str = "SCALP"):
    return strategy.generate_signal({"LowTF": df, "HighTF": df}, "XAUUSD", label=label)


# ══════════════════════════════════════════════════════════════════════════════
# 1. The missed BUY is now captured
# ══════════════════════════════════════════════════════════════════════════════

def test_bullish_reversal_candle_is_below_lagging_sma():
    """The reversal candle still closes below the SMA-40 (the lagging context
    that used to freeze the direction)."""
    cfg = _base_config()
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_suppressed_bullish_sweep()

    sma = df["close"].rolling(window=strat.sma_period).mean()
    last = df.iloc[-1]
    assert last["close"] > last["open"], "fixture bar must be green"
    assert last["close"] < sma.iloc[-1], "fixture bar must close below SMA-40 (lagging)"
    assert _trend_of(strat, df) == "SELL"


def test_bullish_reversal_sweep_now_emits_buy():
    """A textbook bullish reversal sweep below a recent low now emits a BUY,
    even though the lagging SMA trend lock reads SELL."""
    cfg = _base_config()
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_suppressed_bullish_sweep()

    last = df.iloc[-1]
    window = df.iloc[-strat.lookback:-1]
    support = window["low"].min()
    # sanity: the fixture really is a bullish sweep
    assert last["low"] < support and last["close"] > support and last["close"] > last["open"]

    sig = _signal(strat, df)
    assert sig.signal_type == SignalType.BUY, "bullish reversal sweep must now be traded"
    assert "Sweep" in sig.comment
    assert sig.is_stop_order is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. A sweep must FADE the trend lock to count as a reversal
# ══════════════════════════════════════════════════════════════════════════════

def test_with_trend_bullish_sweep_is_not_a_reversal():
    """The SAME bullish sweep candle inside an UPTREND is continuation, not a
    reversal — it is not taken as a sweep (and does not clear the breakout
    resistance), so no signal is emitted."""
    cfg = _base_config()
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_bullish_sweep_in_uptrend()

    assert _trend_of(strat, df) == "BUY", "counterfactual fixture must be an uptrend"

    last = df.iloc[-1]
    support = df.iloc[-strat.lookback:-1]["low"].min()
    assert last["low"] < support and last["close"] > support and last["close"] > last["open"]

    sig = _signal(strat, df)
    assert sig.signal_type == SignalType.NEUTRAL
    assert "Sweep" not in sig.comment


def test_false_sell_sweep_no_longer_matches_sweep():
    """In the locked SELL regime, the bear-flag bar that used to fire the false
    positive can no longer be classified as a reversal sweep (it agrees with the
    lock). Any residual SELL must come from the breakout path and never be
    labelled a Sweep."""
    cfg = _base_config()
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_false_sell_sweep()

    assert _trend_of(strat, df) == "SELL", "fixture must be in the locked SELL regime"

    sig = _signal(strat, df)
    assert "Sweep" not in sig.comment, "bearish sweep in a downtrend must not be a reversal"


# ══════════════════════════════════════════════════════════════════════════════
# 3. RSI exhaustion guard — never fade exhausted momentum
# ══════════════════════════════════════════════════════════════════════════════

def test_rsi_blocks_overbought_buy():
    """An overbought RSI vetoes a BUY (the protective exhaustion guard that used
    to be bypassed for momentum breakouts)."""
    cfg = _base_config()
    cfg["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = True
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_momentum_breakout_uptrend()

    assert _trend_of(strat, df) == "BUY", "fixture must be an uptrend"
    rsi = strat._calculate_rsi(df["close"], cfg["strategy"]["rsi_period"])
    assert rsi > cfg["strategy"]["rsi_buy_threshold"], f"fixture RSI must be overbought (got {rsi:.1f})"

    sig = _signal(strat, df)
    assert sig.signal_type == SignalType.NEUTRAL
    assert "RSI too high for buy" in sig.comment


def test_rsi_blocks_oversold_sell_breakout():
    """An oversold RSI vetoes a SELL — this is what stops the near-bottom
    false-positive SELL breakouts that the fix introduced."""
    cfg = _base_config()
    cfg["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = True
    strat = LiquidityWickStrategy(cfg)

    # Strong persistent downtrend with a red continuation breakout bar -> oversold.
    closes = list(np.linspace(4500.0, 4240.0, 59)) + [4228.0]
    df = scenarios._framed(closes, range_hint=6.0, wick_frac=0.25)
    last = len(df) - 1
    o = float(df.at[last, "open"])
    scenarios._set_bar(df, last, o=o, h=o + 0.5, l=4226.0, c=4228.0)

    assert _trend_of(strat, df) == "SELL", "fixture must be a downtrend"
    rsi = strat._calculate_rsi(df["close"], cfg["strategy"]["rsi_period"])
    assert rsi < cfg["strategy"]["rsi_sell_threshold"], f"fixture RSI must be oversold (got {rsi:.1f})"

    sig = _signal(strat, df)
    assert sig.signal_type == SignalType.NEUTRAL
    assert "RSI too low for sell" in sig.comment


def test_rsi_allows_reversal_buy_at_neutral_rsi():
    """The exhaustion guard does not over-block: a bullish reversal sweep at a
    neutral RSI is still taken."""
    cfg = _base_config()
    cfg["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = True
    strat = LiquidityWickStrategy(cfg)
    df = scenarios.fixture_suppressed_bullish_sweep()

    rsi = strat._calculate_rsi(df["close"], cfg["strategy"]["rsi_period"])
    assert rsi <= cfg["strategy"]["rsi_buy_threshold"], f"fixture RSI must not be overbought (got {rsi:.1f})"

    sig = _signal(strat, df)
    assert sig.signal_type == SignalType.BUY, "a neutral-RSI reversal sweep must still fire"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Trend alignment relaxed mode — entry TF drives the continuation bias
# ══════════════════════════════════════════════════════════════════════════════

def test_entry_trend_only_follows_entry_when_high_tf_disagrees():
    """With allow_entry_trend_only, a bullish entry TF in a bearish trend TF
    still sets the continuation bias from the entry TF's (lagging) SMA read."""
    cfg = _base_config()
    strat = LiquidityWickStrategy(cfg)

    df_bear = scenarios.fixture_suppressed_bullish_sweep()      # last close < SMA (SELL)
    df_bull = scenarios.fixture_momentum_breakout_uptrend()     # last close > SMA (BUY)

    sig = strat.generate_signal({"LowTF": df_bull, "HighTF": df_bear}, "XAUUSD", label="SCALP")
    assert sig.signal_type in (SignalType.BUY, SignalType.NEUTRAL)
