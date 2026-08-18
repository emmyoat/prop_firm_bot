"""
Backtest Engine — Prop Firm Signal Bot
======================================
Fetches historical OHLCV data from TwelveData and runs one or more strategies
in a bar-by-bar simulation, then prints a side-by-side comparison.

Usage:
    python backtest.py [--days 60] [--symbol XAUUSD] [--compare]

Strategies available:
    A  = LiquidityWickStrategy  (current live strategy)
    B  = EMAWickStrategy        (EMA 50/200 crossover + wick confirmation)
"""

import argparse
import sys
import logging
import time
import os
from datetime import datetime
from math import floor

import pandas as pd
import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config_loader import load_config, load_credentials
from src.utils.logger import setup_logger
from src.data.twelvedata_loader import TwelveDataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
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

    # Session breakdown
    session_dist: dict = {}
    for t in closed_trades:
        s = t.get("session", "Unknown")
        session_dist.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
        session_dist[s]["trades"] += 1
        session_dist[s]["pnl"]   += t["pnl"]
        if t["pnl"] > 0:
            session_dist[s]["wins"] += 1

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
    if m["session_dist"]:
        print(f"\n  {'Session':<18} {'Trades':>7} {'WR%':>6} {'PnL':>10}")
        print(f"  {'-'*44}")
        for s, d in sorted(m["session_dist"].items()):
            wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            print(f"  {s:<18} {d['trades']:>7} {wr:>5.1f}% {d['pnl']:>+10.4f}")
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

    for symbol in symbols:
        pip_unit = 0.1 if "XAU" in symbol else 1.0

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
                df.index = pd.to_datetime(df.index)

            trading_start = pd.Timestamp.now() - pd.Timedelta(days=backtest_days)

            active_trades  = []
            pending_orders = []
            closed_trades  = []

            trailing_enabled = config["risk"].get("trailing_stop_enabled", True)
            trailing_activation = config["risk"].get("trailing_stop_activation_pips", 100) * pip_unit
            trailing_distance = config["risk"].get("trailing_stop_distance_pips", 40) * pip_unit

            print(f"  {symbol} [{label}] {tf_low}/{tf_high} — {len(df_low)} bars", end="", flush=True)

            for i in range(210, len(df_low)):
                bar      = df_low.iloc[i]
                curr_time = bar.name
                if not isinstance(curr_time, pd.Timestamp):
                    try:
                        curr_time = pd.to_datetime(curr_time)
                    except Exception:
                        continue

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
                            "type":       "BUY" if "BUY" in order["type"] else "SELL",
                            "entry":      order["entry"],
                            "sl":         order["sl"],
                            "tp":         order["tp"],
                            "entry_hour": curr_time.hour,
                        })
                        pending_orders.remove(order)

                # ── Trade management ──────────────────────────────────────────
                for t in active_trades[:]:
                    exit_price = None
                    if t["type"] == "BUY":
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
                        closed_trades.append(t)
                        active_trades.remove(t)

                # ── Signal generation (only when flat) ───────────────────────
                if not active_trades and not pending_orders:
                    htf_slice = df_high[df_high.index <= curr_time]
                    if len(htf_slice) < 20:
                        continue
                    data_map = {"LowTF": df_low.iloc[:i+1], "HighTF": htf_slice}
                    signal   = strategy.generate_signal(data_map, symbol, label=label)

                    if signal.signal_type != SignalType.NEUTRAL:
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

def fetch_all_data(loader: TwelveDataLoader, symbols: list, pairs: list, n_bars: int = 5000) -> dict:
    """
    Pre-fetches all required symbol+timeframe combinations once,
    returns a keyed dict: {f"{symbol}_{tf}": DataFrame}
    """
    needed: set[tuple] = set()
    for sym in symbols:
        for pair in pairs:
            needed.add((sym, pair["low"]))
            needed.add((sym, pair["high"]))

    cache = {}
    total = len(needed)
    print(f"\nFetching {total} symbol/timeframe combinations from TwelveData...")

    for idx, (sym, tf) in enumerate(sorted(needed), 1):
        key = f"{sym}_{tf}"
        print(f"  [{idx}/{total}] {sym} {tf}...", end="", flush=True)
        df = loader.fetch_data(sym, tf, n_bars=n_bars)
        if df is not None and not df.empty:
            cache[key] = df
            print(f" {len(df)} bars")
        else:
            print(" FAILED — will skip pairs using this data")
        # Respect rate limits between fetches
        time.sleep(0.5)

    return cache


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Prop Firm Signal Bot — Backtest")
    parser.add_argument("--config",  type=str,  default="config.yaml")
    parser.add_argument("--env",     type=str,  default=".env")
    parser.add_argument("--days",    type=int,  default=60,     help="Number of calendar days to backtest")
    parser.add_argument("--symbol",  type=str,  default=None,   help="Single symbol to test (e.g. XAUUSD)")
    parser.add_argument("--bars",    type=int,  default=5000,   help="Number of bars to download per timeframe")
    parser.add_argument("--compare", action="store_true",       help="Run both Strategy A and B and compare")
    parser.add_argument("--no-friday-exit", action="store_true",help="Disable Friday exit rule")
    args = parser.parse_args()

    config = load_config(args.config)
    creds  = load_credentials(args.env)

    # API key
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

    loader = TwelveDataLoader(config, api_key=api_key)

    symbols      = [args.symbol] if args.symbol else config["system"]["symbol_list"]
    active_pairs = config["strategy"].get("active_pairs", [{"low": "H4", "high": "D1", "label": "SWING"}])
    friday_exit  = not args.no_friday_exit

    mode_str = "COMPARE A vs B" if args.compare else "STRATEGY A ONLY"
    print(f"\n{'='*60}")
    print(f"  BACKTEST — {mode_str}")
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Pairs:         {', '.join(p['label'] for p in active_pairs)}")
    print(f"  Period:        Last {args.days} calendar days")
    print(f"  Friday exit:   {'ON' if friday_exit else 'OFF'}")
    print(f"{'='*60}")

    # Pre-fetch all data once
    data_cache = fetch_all_data(loader, symbols, active_pairs, n_bars=args.bars)

    # ── Strategy A (LiquidityWick) ─────────────────────────────────────────
    strategy_a = LiquidityWickStrategy(config)
    print(f"\n--- Running Strategy A: LiquidityWickStrategy ---")
    trades_a, pairs_a = run_single(
        strategy_a, data_cache, config, symbols,
        active_pairs, args.days, friday_exit
    )
    metrics_a = calculate_metrics(trades_a, "Strategy A — LiquidityWick (COMBINED)")
    for pm in pairs_a.values():
        print_metrics(pm, prefix="A | ")

    if metrics_a:
        print_metrics(metrics_a)

    if args.compare:
        # ── Strategy B (EMAWick) ───────────────────────────────────────────
        strategy_b = EMAWickStrategy(config)
        print(f"\n--- Running Strategy B: EMAWickStrategy ---")
        trades_b, pairs_b = run_single(
            strategy_b, data_cache, config, symbols,
            active_pairs, args.days, friday_exit
        )
        metrics_b = calculate_metrics(trades_b, "Strategy B — EMAWick (COMBINED)")
        for pm in pairs_b.values():
            print_metrics(pm, prefix="B | ")

        if metrics_b:
            print_metrics(metrics_b)

        # ── Head-to-head comparison ────────────────────────────────────────
        print_comparison(metrics_a, metrics_b)

    loader.shutdown()


if __name__ == "__main__":
    main()
