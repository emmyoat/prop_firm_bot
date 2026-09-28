"""
Backtest Engine — Prop Firm Signal Bot
======================================
Fetches historical OHLCV data from TwelveData and runs one or more strategies
in a bar-by-bar simulation, then prints a side-by-side comparison.

Usage:
    python backtest.py [--days 30] [--symbol XAUUSD] [--compare]
    python backtest.py [--days 30] [--tournament]   # full 4-way tournament

Strategies available:
    A  = LiquidityWickStrategy      (current live strategy)
    B  = EMAWickStrategy            (EMA 50/200 crossover + wick confirmation)
    C  = SessionORBStrategy         (Session Open Range Breakout — London/NY)
    D  = InsideBarBreakoutStrategy  (Inside bar breakout in trend direction)
"""

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging
import time
import os

import pandas as pd
import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config_loader import load_config, load_credentials
from src.utils.logger import setup_logger
from src.data.twelvedata_loader import TwelveDataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.strategies.ema_pullback_strategy import EMAPullbackStrategy
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score
from src.models import SignalType

logger = setup_logger(log_level="WARNING", log_file=None)
logging.getLogger("PropBot.Strategy").setLevel(logging.ERROR)
logging.getLogger("PropBot.Risk").setLevel(logging.ERROR)
logging.getLogger("PropBot.Data").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════════
# Strategy B — EMA Wick Strategy
# Entry: Price sweeps a swing high/low, closes back inside, AND EMA 50 > EMA 200
# ══════════════════════════════════════════════════════════════════════════════

class EMAWickStrategy:
    """
    Alternative strategy for head-to-head comparison.
    Uses EMA 50/200 trend filter + wick sweep confirmation.
    Difference from Strategy A:
      - Trend: EMA 50 vs EMA 200 (vs SMA 40 in A)
      - Wick threshold: 0.30 (vs 0.25 in A)
      - No breakout logic — sweep only
      - TP: fixed 2.5R (vs structural)
    """

    NAME = "EMAWickStrategy (B)"

    def __init__(self, config: dict):
        self.config = config
        self.lookback     = 15
        self.wick_thresh  = 0.30
        self.rr_target    = 2.5

    def generate_signal(self, data: dict, symbol: str, label: str = ""):
        from src.models import Signal

        df_entry = data.get("LowTF")
        df_trend = data.get("HighTF")

        if df_entry is None or len(df_entry) < 210:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Insufficient data")

        close = df_entry["close"]

        # EMA trend filter
        ema50  = close.ewm(span=50,  adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        if ema50.iloc[-1] > ema200.iloc[-1]:
            trend = SignalType.BUY
        elif ema50.iloc[-1] < ema200.iloc[-1]:
            trend = SignalType.SELL
        else:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "EMA flat")

        # Higher-TF alignment check (optional)
        if df_trend is not None and len(df_trend) >= 210:
            htf_close  = df_trend["close"]
            htf_ema50  = htf_close.ewm(span=50,  adjust=False).mean()
            htf_ema200 = htf_close.ewm(span=200, adjust=False).mean()
            if trend == SignalType.BUY  and htf_ema50.iloc[-1] < htf_ema200.iloc[-1]:
                return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "HTF misalign")
            if trend == SignalType.SELL and htf_ema50.iloc[-1] > htf_ema200.iloc[-1]:
                return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "HTF misalign")

        last    = df_entry.iloc[-1]
        window  = df_entry.iloc[-self.lookback:-1]

        if trend == SignalType.BUY:
            support = window["low"].min()
            if last["low"] < support and last["close"] > support and last["close"] > last["open"]:
                lower_wick = min(last["open"], last["close"]) - last["low"]
                total      = last["high"] - last["low"]
                if total > 0 and (lower_wick / total) >= self.wick_thresh:
                    atr   = self._atr(df_entry)
                    entry = last["high"] + atr * 0.1
                    sl    = last["low"]  - atr * 0.5
                    risk  = abs(entry - sl)
                    tp    = entry + risk * self.rr_target
                    from src.models import Signal
                    return Signal(symbol, SignalType.BUY, entry, sl, tp,
                                  is_stop_order=True, comment=f"EMA Wick BUY (2.5R)")

        elif trend == SignalType.SELL:
            resistance = window["high"].max()
            if last["high"] > resistance and last["close"] < resistance and last["close"] < last["open"]:
                upper_wick = last["high"] - max(last["open"], last["close"])
                total      = last["high"] - last["low"]
                if total > 0 and (upper_wick / total) >= self.wick_thresh:
                    atr   = self._atr(df_entry)
                    entry = last["low"]  - atr * 0.1
                    sl    = last["high"] + atr * 0.5
                    risk  = abs(sl - entry)
                    tp    = entry - risk * self.rr_target
                    from src.models import Signal
                    return Signal(symbol, SignalType.SELL, entry, sl, tp,
                                  is_stop_order=True, comment=f"EMA Wick SELL (2.5R)")

        from src.models import Signal
        return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "No setup")

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.001


# ══════════════════════════════════════════════════════════════════════════════
# Strategy C — Session Open Range Breakout (ORB)
# Captures the high/low of the first N candles after session open.
# A strong close beyond that range signals continuation.
# ══════════════════════════════════════════════════════════════════════════════

class SessionORBStrategy:
    """
    Session Open Range Breakout strategy.

    Logic:
      - At London open (08:00 UTC) and NY open (13:00 UTC), track the
        high/low of the first `orb_candles` bars to form the Opening Range.
      - When a subsequent candle closes ABOVE the ORB high with a strong
        body (>= body_ratio of range) => BUY Stop above that candle.
      - When it closes BELOW the ORB low with a strong body => SELL Stop.
      - SL: opposite side of ORB. TP: fixed R:R.
      - Only fires once per session open (dedup via session tracking).
    """

    NAME = "SessionORB (C)"

    # Session opens in UTC hours
    SESSION_OPENS = {8: "London", 13: "NY"}

    def __init__(self, config: dict):
        self.config     = config
        self.orb_candles  = 2     # bars forming the opening range
        self.body_ratio   = 0.55  # min body / total_range for breakout bar
        self.rr_target    = 2.5
        self._orb_cache: dict = {}   # {(symbol, label, session_date, open_hour): (orb_high, orb_low)}

    def generate_signal(self, data: dict, symbol: str, label: str = ""):
        from src.models import Signal

        df_entry = data.get("LowTF")
        df_trend = data.get("HighTF")

        if df_entry is None or len(df_entry) < 30:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Insufficient data")

        # Only act on bars at or after a session open
        last_bar  = df_entry.iloc[-1]
        last_time = last_bar.get("time", None)
        if last_time is None and hasattr(df_entry.index[-1], "hour"):
            last_time = df_entry.index[-1]
        try:
            last_time = pd.Timestamp(last_time, tz="UTC") if last_time is not None else None
        except Exception:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "No timestamp")

        if last_time is None:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "No timestamp")

        hour = last_time.hour
        session_date = last_time.date()

        # Find which session open we are past (if any)
        active_open = None
        for open_hour in sorted(self.SESSION_OPENS.keys(), reverse=True):
            if hour >= open_hour:
                active_open = open_hour
                break

        if active_open is None:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Not in session")

        cache_key = (symbol, label, session_date, active_open)

        # Build ORB if not cached for this session
        if cache_key not in self._orb_cache:
            # Find the orb_candles bars starting at open_hour on this date
            orb_bars = [
                row for _, row in df_entry.iterrows()
                if (pd.Timestamp(row.get("time", _.name), tz="UTC").date() == session_date
                    and pd.Timestamp(row.get("time", _.name), tz="UTC").hour == active_open)
            ]
            if len(orb_bars) < self.orb_candles:
                return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "ORB forming")
            orb_high = max(r["high"] for r in orb_bars[:self.orb_candles])
            orb_low  = min(r["low"]  for r in orb_bars[:self.orb_candles])
            self._orb_cache[cache_key] = (orb_high, orb_low)

        orb_high, orb_low = self._orb_cache[cache_key]
        orb_range = orb_high - orb_low
        if orb_range <= 0:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "ORB zero range")

        # We need to be at least orb_candles bars after the open
        if hour == active_open:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Still in ORB window")

        last   = df_entry.iloc[-1]
        total  = last["high"] - last["low"]
        if total == 0:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Zero range bar")

        body = abs(last["close"] - last["open"])

        # HTF trend filter (same as Strategy B)
        trend = SignalType.NEUTRAL
        if df_trend is not None and len(df_trend) >= 50:
            ema50  = df_trend["close"].ewm(span=50,  adjust=False).mean()
            ema200 = df_trend["close"].ewm(span=200, adjust=False).mean() if len(df_trend) >= 200 else ema50
            if ema50.iloc[-1] > ema200.iloc[-1]:
                trend = SignalType.BUY
            elif ema50.iloc[-1] < ema200.iloc[-1]:
                trend = SignalType.SELL

        atr  = self._atr(df_entry)

        # Bullish breakout
        if (last["close"] > orb_high
                and last["close"] > last["open"]
                and (body / total) >= self.body_ratio
                and (trend == SignalType.BUY or trend == SignalType.NEUTRAL)):
            entry = last["high"] + atr * 0.1
            sl    = orb_low - atr * 0.3
            risk  = abs(entry - sl)
            tp    = entry + risk * self.rr_target
            return Signal(symbol, SignalType.BUY, entry, sl, tp,
                          is_stop_order=True, comment="ORB Bullish Breakout")

        # Bearish breakout
        if (last["close"] < orb_low
                and last["close"] < last["open"]
                and (body / total) >= self.body_ratio
                and (trend == SignalType.SELL or trend == SignalType.NEUTRAL)):
            entry = last["low"] - atr * 0.1
            sl    = orb_high + atr * 0.3
            risk  = abs(sl - entry)
            tp    = entry - risk * self.rr_target
            return Signal(symbol, SignalType.SELL, entry, sl, tp,
                          is_stop_order=True, comment="ORB Bearish Breakout")

        return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "No ORB breakout")

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.001


# ══════════════════════════════════════════════════════════════════════════════
# Strategy D — Inside Bar Breakout
# Mother candle contains the next (inside) bar. Breakout of mother in
# trend direction = high-probability continuation entry.
# ══════════════════════════════════════════════════════════════════════════════

class InsideBarBreakoutStrategy:
    """
    Inside Bar Breakout in trend direction.

    Logic:
      - "Inside bar": current candle's high < prev candle high AND
        current candle's low > prev candle low (entirely inside mother candle).
      - Trend: EMA-50 slope on Entry TF + EMA-50 vs EMA-200 on Trend TF.
      - BUY Stop above mother candle high (if trend = BUY).
      - SELL Stop below mother candle low (if trend = SELL).
      - SL: opposite side of mother candle + ATR buffer.
      - TP: fixed R:R from config.
    """

    NAME = "InsideBar (D)"

    def __init__(self, config: dict):
        self.config    = config
        self.rr_target = 2.5
        self.min_mother_atr_mult = 0.8   # Mother candle must be >= 0.8x ATR (avoid tiny ranges)

    def generate_signal(self, data: dict, symbol: str, label: str = ""):
        from src.models import Signal

        df_entry = data.get("LowTF")
        df_trend = data.get("HighTF")

        if df_entry is None or len(df_entry) < 60:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Insufficient data")

        last   = df_entry.iloc[-1]   # current (completed) bar — the inside bar candidate
        mother = df_entry.iloc[-2]   # mother candle

        # Inside bar check: current bar must be entirely inside the mother
        if not (last["high"] < mother["high"] and last["low"] > mother["low"]):
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Not inside bar")

        # Mother candle size filter — must be meaningful
        atr = self._atr(df_entry)
        mother_range = mother["high"] - mother["low"]
        if mother_range < atr * self.min_mother_atr_mult:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "Mother too small")

        # Trend detection: EMA-50 slope on Entry TF
        ema50_entry = df_entry["close"].ewm(span=50, adjust=False).mean()
        ema50_rising  = ema50_entry.iloc[-1] > ema50_entry.iloc[-5]
        ema50_falling = ema50_entry.iloc[-1] < ema50_entry.iloc[-5]
        price_above   = df_entry["close"].iloc[-1] > ema50_entry.iloc[-1]
        price_below   = df_entry["close"].iloc[-1] < ema50_entry.iloc[-1]

        if price_above and ema50_rising:
            entry_trend = SignalType.BUY
        elif price_below and ema50_falling:
            entry_trend = SignalType.SELL
        else:
            return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "No EMA trend")

        # HTF confirmation
        if df_trend is not None and len(df_trend) >= 50:
            ema50_htf  = df_trend["close"].ewm(span=50,  adjust=False).mean()
            ema200_htf = df_trend["close"].ewm(span=200, adjust=False).mean() if len(df_trend) >= 200 else ema50_htf
            htf_bull = ema50_htf.iloc[-1] > ema200_htf.iloc[-1]
            htf_bear = ema50_htf.iloc[-1] < ema200_htf.iloc[-1]
            if entry_trend == SignalType.BUY  and not htf_bull:
                return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "HTF misalign")
            if entry_trend == SignalType.SELL and not htf_bear:
                return Signal(symbol, SignalType.NEUTRAL, 0, 0, 0, "HTF misalign")

        sl_buffer = atr * 0.3

        if entry_trend == SignalType.BUY:
            entry = mother["high"] + atr * 0.1
            sl    = mother["low"]  - sl_buffer
            risk  = abs(entry - sl)
            tp    = entry + risk * self.rr_target
            return Signal(symbol, SignalType.BUY, entry, sl, tp,
                          is_stop_order=True, comment="InsideBar BUY Breakout")
        else:
            entry = mother["low"]  - atr * 0.1
            sl    = mother["high"] + sl_buffer
            risk  = abs(sl - entry)
            tp    = entry - risk * self.rr_target
            return Signal(symbol, SignalType.SELL, entry, sl, tp,
                          is_stop_order=True, comment="InsideBar SELL Breakout")

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.001

# ══════════════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════════════

def calculate_metrics(closed_trades: list, label: str = "ALL") -> dict | None:
    if not closed_trades:
        return None

    pnls         = [t["pnl"] for t in closed_trades]
    wins         = [p for p in pnls if p > 0]
    losses       = [p for p in pnls if p <= 0]
    total        = len(pnls)
    win_count    = len(wins)
    loss_count   = len(losses)
    win_rate     = win_count / total * 100 if total else 0.0

    gross_profit = sum(wins)  if wins   else 0.0
    gross_loss   = abs(sum(losses)) if losses else 0.0
    net_pnl      = sum(pnls)
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win  = np.mean(wins)           if wins   else 0.0
    avg_loss = abs(np.mean(losses))    if losses else 0.0
    exp      = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Max DD
    equity = np.cumsum(pnls)
    peak   = np.maximum.accumulate(equity)
    max_dd = float(np.max(peak - equity)) if len(equity) else 0.0

    # Sharpe
    sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if len(pnls) > 1 and np.std(pnls) > 0 else 0.0

    # Consecutive stats
    def _consec(seq, positive):
        best = cur = 0
        for v in seq:
            if (v > 0) == positive:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    # Session & Hourly breakdown
    session_dist: dict = {}
    hourly_dist: dict = {}
    for t in closed_trades:
        s = t.get("session", "Unknown")
        h = t.get("entry_hour", 0)

        session_dist.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
        session_dist[s]["trades"] += 1
        session_dist[s]["pnl"]   += t["pnl"]
        if t["pnl"] > 0:
            session_dist[s]["wins"] += 1

        hourly_dist.setdefault(h, {"trades": 0, "wins": 0, "pnl": 0.0})
        hourly_dist[h]["trades"] += 1
        hourly_dist[h]["pnl"]   += t["pnl"]
        if t["pnl"] > 0:
            hourly_dist[h]["wins"] += 1

    return {
        "label":           label,
        "total_trades":    total,
        "win_count":       win_count,
        "loss_count":      loss_count,
        "win_rate":        win_rate,
        "net_pnl":         net_pnl,
        "gross_profit":    gross_profit,
        "gross_loss":      gross_loss,
        "profit_factor":   pf,
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "expectancy":      exp,
        "max_consec_wins": _consec(pnls, True),
        "max_consec_losses": _consec(pnls, False),
        "max_drawdown":    max_dd,
        "sharpe_ratio":    sharpe,
        "session_dist":    session_dist,
        "hourly_dist":     hourly_dist,
    }


def get_session(hour: int) -> str:
    if 7  <= hour < 12: return "London"
    if 12 <= hour < 17: return "London/NY"
    if 17 <= hour < 22: return "New York"
    return "Asia/Off"


def print_metrics(m: dict, prefix: str = ""):
    if not m:
        print("  No trades to report.")
        return
    w = 58
    sep = "=" * w
    pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "∞  (no losses)"
    print(f"\n{sep}")
    print(f"  {prefix}{m['label']}")
    print(sep)
    print(f"  {'Total Trades':<28} {m['total_trades']:>10}")
    print(f"  {'Wins / Losses':<28} {m['win_count']:>10} / {m['loss_count']}")
    print(f"  {'Win Rate':<28} {m['win_rate']:>9.1f}%")
    print(f"  {'Net PnL (price units)':<28} {m['net_pnl']:>+10.4f}")
    print(f"  {'Gross Profit':<28} {m['gross_profit']:>10.4f}")
    print(f"  {'Gross Loss':<28} {m['gross_loss']:>10.4f}")
    print(f"  {'Profit Factor':<28} {pf_str:>10}")
    print(f"  {'Avg Win / Avg Loss':<28} {m['avg_win']:>8.4f} / {m['avg_loss']:.4f}")
    print(f"  {'Expectancy':<28} {m['expectancy']:>+10.4f}")
    print(f"  {'Max Consec Wins':<28} {m['max_consec_wins']:>10}")
    print(f"  {'Max Consec Losses':<28} {m['max_consec_losses']:>10}")
    print(f"  {'Max Drawdown':<28} {m['max_drawdown']:>10.4f}")
    print(f"  {'Sharpe Ratio':<28} {m['sharpe_ratio']:>10.2f}")
    if m.get("session_dist"):
        print(f"\n  {'Session':<18} {'Trades':>7} {'WR%':>6} {'PnL':>10}")
        print(f"  {'-'*44}")
        for s, d in sorted(m["session_dist"].items()):
            wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            print(f"  {s:<18} {d['trades']:>7} {wr:>5.1f}% {d['pnl']:>+10.4f}")
    if m.get("hourly_dist"):
        print(f"\n  {'Hour (UTC)':<18} {'Trades':>7} {'WR%':>6} {'PnL':>10}")
        print(f"  {'-'*44}")
        for h, d in sorted(m["hourly_dist"].items()):
            wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            print(f"  {f'{h:02d}:00 UTC':<18} {d['trades']:>7} {wr:>5.1f}% {d['pnl']:>+10.4f}")
    print(sep)


def print_comparison(ma: dict, mb: dict):
    """Side-by-side comparison of two strategy result dicts."""
    if not ma or not mb:
        print("Cannot compare — one or both strategies produced no trades.")
        return

    w = 78
    print(f"\n{'='*w}")
    print(f"  STRATEGY COMPARISON")
    print(f"  {'Metric':<30} {'Strategy A':>20} {'Strategy B':>20}")
    print(f"  {'-'*w}")

    def row(label, va, vb, fmt="{:.2f}", better="high"):
        sa = fmt.format(va)
        sb = fmt.format(vb)
        if better == "high":
            mark_a = " <--" if va > vb else ""
            mark_b = " <--" if vb > va else ""
        else:
            mark_a = " <--" if va < vb else ""
            mark_b = " <--" if vb < va else ""
        print(f"  {label:<30} {sa+mark_a:>20} {sb+mark_b:>20}")

    row("Total Trades",      ma["total_trades"],    mb["total_trades"],    "{:.0f}", "high")
    row("Win Rate (%)",      ma["win_rate"],         mb["win_rate"],         "{:.1f}", "high")
    row("Profit Factor",     ma["profit_factor"] if ma["profit_factor"] != float("inf") else 99,
                             mb["profit_factor"] if mb["profit_factor"] != float("inf") else 99, "{:.2f}", "high")
    row("Net PnL",           ma["net_pnl"],          mb["net_pnl"],          "{:+.4f}", "high")
    row("Expectancy",        ma["expectancy"],        mb["expectancy"],        "{:+.4f}", "high")
    row("Max Drawdown",      ma["max_drawdown"],      mb["max_drawdown"],      "{:.4f}", "low")
    row("Max Consec Losses", ma["max_consec_losses"], mb["max_consec_losses"], "{:.0f}", "low")
    row("Sharpe Ratio",      ma["sharpe_ratio"],      mb["sharpe_ratio"],      "{:.2f}", "high")
    print(f"{'='*w}")

    winner_a = sum([
        ma["win_rate"]      > mb["win_rate"],
        ma["profit_factor"] > mb["profit_factor"],
        ma["net_pnl"]       > mb["net_pnl"],
        ma["expectancy"]    > mb["expectancy"],
        ma["max_drawdown"]  < mb["max_drawdown"],
        ma["sharpe_ratio"]  > mb["sharpe_ratio"],
    ])
    winner_b = 6 - winner_a
    print(f"\n  Score: Strategy A {winner_a}/6 — Strategy B {winner_b}/6")
    if winner_a > winner_b:
        print("  VERDICT: Strategy A (LiquidityWick) wins this comparison.")
    elif winner_b > winner_a:
        print("  VERDICT: Strategy B (EMAWick) wins this comparison.")
    else:
        print("  VERDICT: TIE — Consider running with more data (--days 90)")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# Core backtest engine
# ══════════════════════════════════════════════════════════════════════════════

def run_single(strategy, data_cache: dict, config: dict, symbols: list,
               active_pairs: list, backtest_days: int,
               friday_exit: bool = True) -> tuple[list, dict]:
    """
    Runs a bar-by-bar backtest for a single strategy.
    Returns (all_closed_trades, per_pair_metrics_dict)
    """
    all_trades   = []
    pair_metrics = {}

    # Ensure all cached DataFrames have a UTC DatetimeIndex
    for df in data_cache.values():
        if df is not None and not df.empty:
            if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                df.set_index("time", inplace=True, drop=False)
            df.index = pd.to_datetime(df.index, utc=True)

    for symbol in symbols:
        pip_unit = 0.1 if "XAU" in symbol else (0.01 if "JPY" in symbol else 0.0001)

        for pair in active_pairs:
            label    = pair["label"]
            tf_low   = pair["low"]
            tf_high  = pair["high"]

            key_low  = f"{symbol}_{tf_low}"
            key_high = f"{symbol}_{tf_high}"

            df_low  = data_cache.get(key_low)
            df_high = data_cache.get(key_high)

            if df_low is None or df_high is None or df_low.empty:
                print(f"  SKIP {symbol} {label}: no data for {tf_low} or {tf_high}")
                continue

            # Per-symbol allowed pairs check
            sym_overrides = config["strategy"].get("symbol_overrides", {}).get(symbol, {})
            allowed = sym_overrides.get("allowed_pairs")
            if allowed and label not in allowed:
                continue

            # Ensure datetime index
            for df in [df_low, df_high]:
                if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                    df.set_index("time", inplace=True, drop=False)
                df.index = pd.to_datetime(df.index, utc=True)

            trading_start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=backtest_days)

            active_trades  = []
            pending_orders = []
            closed_trades  = []

            trailing_enabled = config["risk"].get("trailing_stop_enabled", False)
            trailing_activation = config["risk"].get("trailing_stop_activation_pips", 100) * pip_unit
            trailing_distance = config["risk"].get("trailing_stop_distance_pips", 40) * pip_unit

            be_enabled = config["risk"].get("breakeven_enabled", True)
            be_activation = config["risk"].get("breakeven_activation_pips", 250) * pip_unit
            grace_bars = config["risk"].get("post_trigger_grace_bars", 1)
            cooldown_mins = config["risk"].get("loss_cooldown_minutes", 45)
            last_loss_time = None
            consecutive_losses = 0

            print(f"  {symbol} [{label}] {tf_low}/{tf_high} — {len(df_low)} bars", end="", flush=True)

            for i in range(210, len(df_low)):
                bar      = df_low.iloc[i]
                curr_time = bar.name
                if not isinstance(curr_time, pd.Timestamp):
                    try:
                        curr_time = pd.to_datetime(curr_time, utc=True)
                    except Exception:
                        continue
                elif curr_time.tzinfo is None:
                    curr_time = curr_time.tz_localize("UTC")

                if curr_time < trading_start:
                    continue

                # Friday exit
                if friday_exit and curr_time.weekday() == 4 and curr_time.hour >= 21:
                    pending_orders.clear()
                    for t in active_trades[:]:
                        t["pnl"]     = (bar["close"] - t["entry"]) if t["type"] == "BUY" else (t["entry"] - bar["close"])
                        t["session"] = get_session(t.get("entry_hour", 12))
                        closed_trades.append(t)
                        active_trades.remove(t)
                    continue

                # ── Pending order management ──────────────────────────────────
                for order in pending_orders[:]:
                    if (curr_time - order["placed_time"]).total_seconds() > 4 * 3600:
                        pending_orders.remove(order)
                        continue
                    triggered = (order["type"] == "BUY_STOP"  and bar["high"] >= order["entry"]) or \
                                (order["type"] == "SELL_STOP" and bar["low"]  <= order["entry"])
                    if triggered:
                        active_trades.append({
                            "type":          "BUY" if "BUY" in order["type"] else "SELL",
                            "entry":         order["entry"],
                            "sl":            order["sl"],
                            "tp":            order["tp"],
                            "entry_hour":    curr_time.hour,
                            "trigger_bar":   True,
                            "bars_held":     0,
                            "be_activated":  False,
                        })
                        pending_orders.remove(order)

                # ── Trade management ──────────────────────────────────────────
                for t in active_trades[:]:
                    exit_price = None
                    is_trigger = t.get("trigger_bar", False)
                    in_grace = is_trigger or (t.get("bars_held", 0) <= grace_bars)

                    # Breakeven check
                    if be_enabled and not t.get("be_activated"):
                        profit = (bar["high"] - t["entry"]) if t["type"] == "BUY" else (t["entry"] - bar["low"])
                        if profit >= be_activation:
                            t["sl"] = t["entry"]
                            t["be_activated"] = True

                    if t["type"] == "BUY":
                        if is_trigger:
                            # Trigger bar: candle close beyond SL/TP
                            if bar["close"] <= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and bar["close"] >= t["tp"]:
                                exit_price = t["tp"]
                        elif in_grace and not t.get("be_activated"):
                            # Grace period: bar_close for SL to avoid retracement wicks
                            if bar["close"] <= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and (bar["high"] >= t["tp"] or bar["close"] >= t["tp"]):
                                exit_price = t["tp"]
                        else:
                            # Normal evaluation
                            if bar["low"] <= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and bar["high"] >= t["tp"]:
                                exit_price = t["tp"]
                            elif trailing_enabled:
                                profit_dist = bar["high"] - t["entry"]
                                if profit_dist >= trailing_activation:
                                    new_sl = t["entry"] + (profit_dist - trailing_distance)
                                    if new_sl > t["sl"]:
                                        t["sl"] = new_sl
                    else:
                        if is_trigger:
                            # Trigger bar: candle close beyond SL/TP
                            if bar["close"] >= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and bar["close"] <= t["tp"]:
                                exit_price = t["tp"]
                        elif in_grace and not t.get("be_activated"):
                            # Grace period: bar_close for SL to avoid retracement wicks
                            if bar["close"] >= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and (bar["low"] <= t["tp"] or bar["close"] <= t["tp"]):
                                exit_price = t["tp"]
                        else:
                            # Normal evaluation
                            if bar["high"] >= t["sl"]:
                                exit_price = t["sl"]
                            elif t["tp"] > 0 and bar["low"] <= t["tp"]:
                                exit_price = t["tp"]
                            elif trailing_enabled:
                                profit_dist = t["entry"] - bar["low"]
                                if profit_dist >= trailing_activation:
                                    new_sl = t["entry"] - (profit_dist - trailing_distance)
                                    if new_sl < t["sl"]:
                                        t["sl"] = new_sl

                    if exit_price is not None:
                        t["pnl"]     = (exit_price - t["entry"]) if t["type"] == "BUY" else (t["entry"] - exit_price)
                        t["session"] = get_session(t.get("entry_hour", 12))
                        if t["pnl"] < 0:
                            consecutive_losses += 1
                            last_loss_time = curr_time
                        else:
                            consecutive_losses = 0
                        closed_trades.append(t)
                        active_trades.remove(t)
                    else:
                        t["trigger_bar"] = False
                        t["bars_held"] = t.get("bars_held", 0) + 1

                # ── Signal generation (only when flat & in active session) ────
                if not active_trades and not pending_orders:
                    # Escalating post-loss cooldown check
                    if cooldown_mins > 0 and last_loss_time is not None:
                        eff_cooldown = cooldown_mins * min(max(1, consecutive_losses), 3)
                        elapsed = (curr_time - last_loss_time).total_seconds() / 60.0
                        if 0 <= elapsed < eff_cooldown:
                            continue

                    # Respect active_sessions from config (mirrors live bot behaviour)
                    active_sessions = config.get("system", {}).get("active_sessions", [])
                    curr_session = None
                    if active_sessions:
                        for s in active_sessions:
                            if s.get("start_utc", 0) <= curr_time.hour < s.get("end_utc", 24):
                                curr_session = s.get("name")
                                break
                        if curr_session is None:
                            continue

                    # Respect per-pair allowed_sessions (e.g. SCALP_M5 restricted to London/NY Overlap)
                    allowed_sess = pair.get("allowed_sessions")
                    if allowed_sess and curr_session not in allowed_sess:
                        continue
                    htf_slice = df_high[df_high.index <= curr_time]
                    if len(htf_slice) < 20:
                        continue
                    # MacroTF (D1) gate for SCALP & DAY pairs
                    macro_slice = None
                    df_macro_all = data_cache.get(f"{symbol}_D1")
                    if df_macro_all is not None and tf_high != "D1":
                        macro_slice = df_macro_all[df_macro_all.index <= curr_time]
                        if len(macro_slice) < 50:
                            macro_slice = None
                    data_map = {"LowTF": df_low.iloc[:i+1], "HighTF": htf_slice, "MacroTF": macro_slice}
                    signal   = strategy.generate_signal(data_map, symbol, label=label)

                    if signal.signal_type != SignalType.NEUTRAL:
                        # SMC confluence filter
                        if config.get("strategy", {}).get("smc_filter_enabled", False):
                            smc_map = config["strategy"].get("smc_min_confluence_map", {})
                            smc_min = smc_map.get(label, config["strategy"].get("smc_min_confluence_score", 20))
                            if smc_min > 0:
                                try:
                                    df_slice = df_low.iloc[max(0, i - 100):i+1]
                                    fvgs = detect_fvg_zones(df_slice)
                                    obs = detect_order_blocks(df_slice)
                                    score, _ = calculate_confluence_score(
                                        current_price=float(bar["close"]),
                                        signal_type=signal.signal_type.name,
                                        order_blocks=obs,
                                        fvg_zones=fvgs,
                                        entry_price=signal.price,
                                        stop_loss=signal.sl_price,
                                    )
                                    if score < smc_min:
                                        continue
                                except Exception:
                                    pass

                        if signal.is_stop_order:
                            pending_orders.append({
                                "type":        "BUY_STOP" if signal.signal_type == SignalType.BUY else "SELL_STOP",
                                "entry":       signal.price,
                                "sl":          signal.sl_price,
                                "tp":          signal.tp_price,
                                "placed_time": curr_time,
                            })
                        else:
                            active_trades.append({
                                "type":       signal.signal_type.name,
                                "entry":      signal.price,
                                "sl":         signal.sl_price,
                                "tp":         signal.tp_price,
                                "entry_hour": curr_time.hour,
                            })

            metrics = calculate_metrics(closed_trades, f"{symbol} [{label}]")
            print(f" → {len(closed_trades)} trades")
            if metrics:
                pair_metrics[f"{symbol}_{label}"] = metrics
                all_trades.extend(closed_trades)

    return all_trades, pair_metrics


# ══════════════════════════════════════════════════════════════════════════════
# Data fetching
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_data(loader: TwelveDataLoader, symbols: list, pairs: list, n_bars: int = 5000, cache_dir: str = ".cache/data") -> dict:
    """
    Pre-fetches all required symbol+timeframe combinations once,
    caching to disk so repeated runs save TwelveData API quota.
    returns a keyed dict: {f"{symbol}_{tf}": DataFrame}
    """
    needed: set[tuple] = set()
    for sym in symbols:
        for pair in pairs:
            needed.add((sym, pair["low"]))
            needed.add((sym, pair["high"]))
        needed.add((sym, "D1"))

    os.makedirs(cache_dir, exist_ok=True)
    cache = {}
    total = len(needed)
    print(f"\nFetching {total} symbol/timeframe combinations (cache: {cache_dir})...")

    for idx, (sym, tf) in enumerate(sorted(needed), 1):
        key = f"{sym}_{tf}"
        csv_path = os.path.join(cache_dir, f"{key}.csv")
        if os.path.exists(csv_path):
            try:
                cached_df = pd.read_csv(csv_path)
                cached_df["time"] = pd.to_datetime(cached_df["time"], utc=True)
                for col in ("open", "high", "low", "close", "volume"):
                    if col in cached_df.columns:
                        cached_df[col] = pd.to_numeric(cached_df[col], errors="coerce")
                cached_df = cached_df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
                if len(cached_df) >= 200:
                    cache[key] = cached_df
                    print(f"  [{idx}/{total}] {sym} {tf}... [cache hit] {len(cached_df)} bars")
                    continue
            except Exception:
                pass

        print(f"  [{idx}/{total}] {sym} {tf}...", end="", flush=True)
        df = loader.fetch_data(sym, tf, n_bars=n_bars)
        if df is not None and not df.empty:
            cache[key] = df
            try:
                df.to_csv(csv_path, index=False)
            except Exception:
                pass
            print(f" {len(df)} bars")
        else:
            print(" FAILED — will skip pairs using this data")
        time.sleep(0.5)

    return cache




# =============================================================================
# Tournament leaderboard printer
# =============================================================================

def print_tournament(results):
    """Ranked leaderboard for all strategies. results = [(name, metrics_dict), ...]"""
    w = 100
    valid = [(n, m) for n, m in results if m and m["total_trades"] > 0]
    if not valid:
        print("No strategies produced trades.")
        return None

    metrics_order = [
        ("profit_factor",    "high"),
        ("win_rate",         "high"),
        ("net_pnl",          "high"),
        ("expectancy",       "high"),
        ("sharpe_ratio",     "high"),
        ("max_drawdown",     "low"),
        ("max_consec_losses","low"),
    ]

    scores = {n: 0 for n, _ in valid}
    for key, direction in metrics_order:
        vals = [(n, m[key]) for n, m in valid]
        vals_sorted = sorted(vals, key=lambda x: x[1], reverse=(direction == "high"))
        for rank, (name, _) in enumerate(vals_sorted, 1):
            scores[name] += rank

    ranked = sorted(valid, key=lambda x: scores[x[0]])

    print("\n" + "=" * w)
    print("  STRATEGY TOURNAMENT  --  RANKED LEADERBOARD")
    print("=" * w)
    print(f"  {'Rank':<8}{'Strategy':<30}{'Trades':>7}{'WR%':>7}{'PF':>7}{'Net PnL':>10}{'Expect':>10}{'MaxDD':>9}{'Sharpe':>8}  Score")
    print("  " + "-" * (w - 2))
    medals = {1: "[GOLD]  ", 2: "[SILVER]", 3: "[BRONZE]"}
    for pos, (name, m) in enumerate(ranked, 1):
        pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "  inf"
        medal  = medals.get(pos, "        ")
        print(
            f"  #{pos} {medal}  {name:<28} {m['total_trades']:>7}"
            f" {m['win_rate']:>6.1f}%{pf_str:>7} {m['net_pnl']:>+10.2f}"
            f" {m['expectancy']:>+10.4f} {m['max_drawdown']:>9.2f}"
            f" {m['sharpe_ratio']:>8.2f}  {scores[name]}"
        )
    print("=" * w)

    winner_name, winner_m = ranked[0]
    pf_w = f"{winner_m['profit_factor']:.2f}" if winner_m["profit_factor"] != float("inf") else "inf"
    print(f"\n  >> WINNER: {winner_name}")
    print(f"     Profit Factor : {pf_w}")
    print(f"     Win Rate      : {winner_m['win_rate']:.1f}%")
    print(f"     Net PnL       : {winner_m['net_pnl']:+.2f}")
    print(f"     Expectancy    : {winner_m['expectancy']:+.4f} per trade")
    print(f"     Sharpe Ratio  : {winner_m['sharpe_ratio']:.2f}")
    print(f"     Total Trades  : {winner_m['total_trades']}")
    print("=" * w + "\n")
    return winner_name


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Prop Firm Signal Bot -- Backtest")
    parser.add_argument("--config",         type=str,  default="config.yaml")
    parser.add_argument("--env",            type=str,  default=".env")
    parser.add_argument("--days",           type=int,  default=60,    help="Calendar days to backtest")
    parser.add_argument("--symbol",         type=str,  default=None,  help="Single symbol (e.g. XAUUSD)")
    parser.add_argument("--bars",           type=int,  default=5000,  help="Bars per timeframe")
    parser.add_argument("--compare",        action="store_true",      help="Run Strategy A vs B")
    parser.add_argument("--tournament",     action="store_true",      help="Run all strategies A-E and rank")
    parser.add_argument("--no-friday-exit", action="store_true",      help="Disable Friday exit rule")
    args = parser.parse_args()

    config = load_config(args.config)
    creds  = load_credentials(args.env)

    api_key = (
        config.get("data_source", {}).get("api_key")
        or creds.get("twelvedata_api_key")
        or os.environ.get("TWELVEDATA_API_KEY", "")
    )
    if not api_key:
        print("\n[ERROR] TWELVEDATA_API_KEY not set.")
        print("  Get a free key at https://twelvedata.com/register")
        print("  Then add it to .env:  TWELVEDATA_API_KEY=your_key_here\n")
        sys.exit(1)

    loader       = TwelveDataLoader(config, api_key=api_key)
    symbols      = [args.symbol] if args.symbol else config["system"]["symbol_list"]
    active_pairs = config["strategy"].get("active_pairs", [{"low": "H4", "high": "D1", "label": "SWING"}])
    friday_exit  = not args.no_friday_exit

    mode_str = "TOURNAMENT (A/B/C/D/E)" if args.tournament else ("COMPARE A vs B" if args.compare else "STRATEGY A ONLY")
    print(f"\n{'='*60}")
    print(f"  BACKTEST -- {mode_str}")
    print(f"  Symbols:  {', '.join(symbols)}")
    print(f"  Pairs:    {', '.join(p['label'] for p in active_pairs)}")
    print(f"  Period:   Last {args.days} calendar days")
    print(f"  Fri exit: {'ON' if friday_exit else 'OFF'}")
    print(f"{'='*60}")

    data_cache = fetch_all_data(loader, symbols, active_pairs, n_bars=args.bars)

    # A: LiquidityWick (live)
    strategy_a = LiquidityWickStrategy(config)
    print(f"\n--- Running A: LiquidityWickStrategy (current live) ---")
    trades_a, pairs_a = run_single(strategy_a, data_cache, config, symbols, active_pairs, args.days, friday_exit)
    metrics_a = calculate_metrics(trades_a, "A -- LiquidityWick (live)")
    for pm in pairs_a.values():
        print_metrics(pm, prefix="A | ")
    if metrics_a:
        print_metrics(metrics_a)

    metrics_b = metrics_c = metrics_d = metrics_e = None

    if args.compare or args.tournament:
        # B: EMAWick
        strategy_b = EMAWickStrategy(config)
        print(f"\n--- Running B: EMAWickStrategy ---")
        trades_b, pairs_b = run_single(strategy_b, data_cache, config, symbols, active_pairs, args.days, friday_exit)
        metrics_b = calculate_metrics(trades_b, "B -- EMAWick")
        for pm in pairs_b.values():
            print_metrics(pm, prefix="B | ")
        if metrics_b:
            print_metrics(metrics_b)
        if args.compare and not args.tournament:
            print_comparison(metrics_a, metrics_b)

    if args.tournament:
        # C: Session ORB
        strategy_c = SessionORBStrategy(config)
        print(f"\n--- Running C: SessionORBStrategy ---")
        trades_c, pairs_c = run_single(strategy_c, data_cache, config, symbols, active_pairs, args.days, friday_exit)
        metrics_c = calculate_metrics(trades_c, "C -- SessionORB")
        for pm in pairs_c.values():
            print_metrics(pm, prefix="C | ")
        if metrics_c:
            print_metrics(metrics_c)

        # D: Inside Bar Breakout
        strategy_d = InsideBarBreakoutStrategy(config)
        print(f"\n--- Running D: InsideBarBreakoutStrategy ---")
        trades_d, pairs_d = run_single(strategy_d, data_cache, config, symbols, active_pairs, args.days, friday_exit)
        metrics_d = calculate_metrics(trades_d, "D -- InsideBar")
        for pm in pairs_d.values():
            print_metrics(pm, prefix="D | ")
        if metrics_d:
            print_metrics(metrics_d)

        # E: EMA Pullback
        strategy_e = EMAPullbackStrategy(config)
        print(f"\n--- Running E: EMAPullbackStrategy ---")
        trades_e, pairs_e = run_single(strategy_e, data_cache, config, symbols, active_pairs, args.days, friday_exit)
        metrics_e = calculate_metrics(trades_e, "E -- EMAPullback")
        for pm in pairs_e.values():
            print_metrics(pm, prefix="E | ")
        if metrics_e:
            print_metrics(metrics_e)

        # Tournament leaderboard
        all_results = [
            ("A -- LiquidityWick (live)", metrics_a),
            ("B -- EMAWick",              metrics_b),
            ("C -- SessionORB",           metrics_c),
            ("D -- InsideBar",            metrics_d),
            ("E -- EMAPullback",          metrics_e),
        ]
        print_tournament(all_results)

    loader.shutdown()


if __name__ == "__main__":
    main()
