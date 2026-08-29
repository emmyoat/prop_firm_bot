"""
Multi-Variant Backtest — SMC & RSI Filter Impact Test
=====================================================
Runs 4 configurations on the same historical data to compare:
  1. BASELINE   — Current live settings (no RSI, no SMC)
  2. +RSI       — RSI confirmation filter enabled
  3. +SMC       — SMC Order Block + FVG confluence filter enabled
  4. +RSI+SMC   — Both filters enabled

Usage:
    python backtest_filters.py [--days 60] [--symbol XAUUSD] [--bars 5000]
"""

import argparse
import copy
import sys
import logging
import time
import os
from datetime import datetime
from math import floor

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config_loader import load_config, load_credentials
from src.utils.logger import setup_logger
from src.data.twelvedata_loader import TwelveDataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score
from src.models import SignalType, Signal

# Suppress noisy logs during backtest
logger = setup_logger(log_level="WARNING", log_file=None)
logging.getLogger("PropBot.Strategy").setLevel(logging.ERROR)
logging.getLogger("PropBot.Risk").setLevel(logging.ERROR)
logging.getLogger("PropBot.Data").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════════════

def get_session(hour: int) -> str:
    if 7  <= hour < 12: return "London"
    if 12 <= hour < 17: return "London/NY"
    if 17 <= hour < 22: return "New York"
    return "Asia/Off"


def calculate_metrics(closed_trades: list, label: str = "ALL") -> dict | None:
    if not closed_trades:
        return None

    pnls = [t["pnl"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = len(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total * 100 if total else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    net_pnl = sum(pnls)
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 0.0
    exp = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Max DD
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.max(peak - equity)) if len(equity) else 0.0

    # Sharpe
    sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if len(pnls) > 1 and np.std(pnls) > 0 else 0.0

    # Session breakdown
    session_dist = {}
    for t in closed_trades:
        s = t.get("session", "Unknown")
        session_dist.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
        session_dist[s]["trades"] += 1
        session_dist[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            session_dist[s]["wins"] += 1

    return {
        "label": label,
        "total_trades": total,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": exp,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "session_dist": session_dist,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Core backtest engine (with optional SMC filter)
# ══════════════════════════════════════════════════════════════════════════════

def run_variant(strategy, data_cache: dict, config: dict, symbols: list,
                active_pairs: list, backtest_days: int,
                smc_enabled: bool = False, smc_min_score: int = 20,
                friday_exit: bool = True, variant_name: str = "") -> list:
    """
    Runs a bar-by-bar backtest for a single strategy variant.
    If smc_enabled, applies SMC confluence filter before accepting signals.
    Returns list of closed trades.
    """
    all_trades = []

    for symbol in symbols:
        pip_unit = 0.1 if "XAU" in symbol else 1.0

        for pair in active_pairs:
            label = pair["label"]
            tf_low = pair["low"]
            tf_high = pair["high"]

            key_low = f"{symbol}_{tf_low}"
            key_high = f"{symbol}_{tf_high}"

            df_low = data_cache.get(key_low)
            df_high = data_cache.get(key_high)

            if df_low is None or df_high is None or df_low.empty:
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

            active_trades = []
            pending_orders = []
            closed_trades = []

            trailing_enabled = config["risk"].get("trailing_stop_enabled", True)
            trailing_activation = config["risk"].get("trailing_stop_activation_pips", 100) * pip_unit
            trailing_distance = config["risk"].get("trailing_stop_distance_pips", 40) * pip_unit

            signals_blocked_smc = 0

            for i in range(210, len(df_low)):
                bar = df_low.iloc[i]
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
                        t["pnl"] = (bar["close"] - t["entry"]) if t["type"] == "BUY" else (t["entry"] - bar["close"])
                        t["session"] = get_session(t.get("entry_hour", 12))
                        closed_trades.append(t)
                        active_trades.remove(t)
                    continue

                # Pending order management
                for order in pending_orders[:]:
                    if (curr_time - order["placed_time"]).total_seconds() > 4 * 3600:
                        pending_orders.remove(order)
                        continue
                    triggered = (order["type"] == "BUY_STOP" and bar["high"] >= order["entry"]) or \
                                (order["type"] == "SELL_STOP" and bar["low"] <= order["entry"])
                    if triggered:
                        active_trades.append({
                            "type": "BUY" if "BUY" in order["type"] else "SELL",
                            "entry": order["entry"],
                            "sl": order["sl"],
                            "tp": order["tp"],
                            "entry_hour": curr_time.hour,
                        })
                        pending_orders.remove(order)

                # Trade management
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
                        t["pnl"] = (exit_price - t["entry"]) if t["type"] == "BUY" else (t["entry"] - exit_price)
                        t["session"] = get_session(t.get("entry_hour", 12))
                        closed_trades.append(t)
                        active_trades.remove(t)

                # Signal generation (only when flat & in active session)
                if not active_trades and not pending_orders:
                    active_sessions = config.get("system", {}).get("active_sessions", [])
                    if active_sessions:
                        in_session = any(
                            s.get("start_utc", 0) <= curr_time.hour < s.get("end_utc", 24)
                            for s in active_sessions
                        )
                        if not in_session:
                            continue

                    htf_slice = df_high[df_high.index <= curr_time]
                    if len(htf_slice) < 20:
                        continue

                    # MacroTF (D1) gate
                    macro_slice = None
                    df_macro_all = data_cache.get(f"{symbol}_D1")
                    if df_macro_all is not None and tf_high != "D1":
                        macro_slice = df_macro_all[df_macro_all.index <= curr_time]
                        if len(macro_slice) < 50:
                            macro_slice = None

                    data_map = {"LowTF": df_low.iloc[:i+1], "HighTF": htf_slice, "MacroTF": macro_slice}
                    signal = strategy.generate_signal(data_map, symbol, label=label)

                    if signal.signal_type != SignalType.NEUTRAL:
                        # SMC filter
                        if smc_enabled:
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
                                if score < smc_min_score:
                                    signals_blocked_smc += 1
                                    continue
                            except Exception:
                                pass

                        if signal.is_stop_order:
                            pending_orders.append({
                                "type": "BUY_STOP" if signal.signal_type == SignalType.BUY else "SELL_STOP",
                                "entry": signal.price,
                                "sl": signal.sl_price,
                                "tp": signal.tp_price,
                                "placed_time": curr_time,
                            })
                        else:
                            active_trades.append({
                                "type": signal.signal_type.name,
                                "entry": signal.price,
                                "sl": signal.sl_price,
                                "tp": signal.tp_price,
                                "entry_hour": curr_time.hour,
                            })

            all_trades.extend(closed_trades)
            print(f"  {variant_name} | {symbol} [{label}] {tf_low}/{tf_high} -> {len(closed_trades)} trades"
                  + (f" ({signals_blocked_smc} blocked by SMC)" if smc_enabled and signals_blocked_smc else ""))

    return all_trades


# ══════════════════════════════════════════════════════════════════════════════
# Data fetching
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_data(loader: TwelveDataLoader, symbols: list, pairs: list, n_bars: int = 5000) -> dict:
    needed: set[tuple] = set()
    for sym in symbols:
        for pair in pairs:
            needed.add((sym, pair["low"]))
            needed.add((sym, pair["high"]))
        # Always fetch D1 for macro gate
        needed.add((sym, "D1"))

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
            print(" FAILED")
        time.sleep(0.5)

    return cache


# ══════════════════════════════════════════════════════════════════════════════
# Comparison display
# ══════════════════════════════════════════════════════════════════════════════

def print_comparison_table(results: list[dict]):
    """Print a formatted comparison table of all variants."""

    w = 100
    print(f"\n{'='*w}")
    print(f"  FILTER IMPACT COMPARISON - SMC & RSI")
    print(f"{'='*w}")

    # Header
    header = f"  {'Metric':<24}"
    for r in results:
        header += f" {r['label']:>17}"
    print(header)
    print(f"  {'-'*(w-4)}")

    def row(metric_name, key, fmt="{:.1f}", better="high", suffix=""):
        line = f"  {metric_name:<24}"
        values = []
        for r in results:
            if r is None:
                values.append(None)
            else:
                values.append(r.get(key, 0))

        valid = [v for v in values if v is not None]
        if better == "high":
            best = max(valid) if valid else None
        else:
            best = min(valid) if valid else None

        for v in values:
            if v is None:
                line += f" {'N/A':>17}"
            else:
                formatted = fmt.format(v)
                marker = " *" if v == best and len(valid) > 1 and valid.count(best) == 1 else ""
                line += f" {formatted + suffix + marker:>17}"
        print(line)

    row("Total Trades",     "total_trades",    "{:.0f}", "high")
    row("Wins",             "win_count",       "{:.0f}", "high")
    row("Losses",           "loss_count",      "{:.0f}", "low")
    row("Win Rate",         "win_rate",        "{:.1f}", "high", suffix="%")
    row("Net PnL",          "net_pnl",         "{:+.2f}", "high")
    row("Profit Factor",    "profit_factor",   "{:.2f}", "high")
    row("Avg Win",          "avg_win",         "{:.4f}", "high")
    row("Avg Loss",         "avg_loss",        "{:.4f}", "low")
    row("Expectancy",       "expectancy",      "{:+.4f}", "high")
    row("Max Drawdown",     "max_drawdown",    "{:.4f}", "low")
    row("Sharpe Ratio",     "sharpe_ratio",    "{:.2f}", "high")
    print(f"{'='*w}")

    # Per-session breakdown for each variant
    for r in results:
        if r and r.get("session_dist"):
            print(f"\n  {r['label']} - Session Breakdown")
            print(f"  {'Session':<18} {'Trades':>7} {'WR%':>6} {'PnL':>10}")
            print(f"  {'-'*44}")
            for s, d in sorted(r["session_dist"].items()):
                wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
                print(f"  {s:<18} {d['trades']:>7} {wr:>5.1f}% {d['pnl']:>+10.4f}")

    # Delta analysis
    print(f"\n{'='*w}")
    print(f"  DELTA vs BASELINE")
    print(f"{'='*w}")
    baseline = results[0]
    if baseline:
        for r in results[1:]:
            if r:
                wr_delta = r["win_rate"] - baseline["win_rate"]
                pf_delta = r["profit_factor"] - baseline["profit_factor"] if baseline["profit_factor"] != float("inf") else 0
                pnl_delta = r["net_pnl"] - baseline["net_pnl"]
                trade_delta = r["total_trades"] - baseline["total_trades"]
                dd_delta = r["max_drawdown"] - baseline["max_drawdown"]
                print(f"  {r['label']:<24} WR: {wr_delta:+.1f}%  |  PF: {pf_delta:+.2f}  |  "
                      f"PnL: {pnl_delta:+.2f}  |  Trades: {trade_delta:+.0f}  |  DD: {dd_delta:+.4f}")
    print(f"{'='*w}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Multi-Variant Filter Backtest")
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument("--env",    type=str, default=".env")
    parser.add_argument("--days",   type=int, default=60,   help="Calendar days to backtest")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol (e.g. XAUUSD)")
    parser.add_argument("--bars",   type=int, default=5000, help="Bars per timeframe to download")
    parser.add_argument("--smc-score", type=int, default=20, help="Minimum SMC confluence score")
    parser.add_argument("--no-friday-exit", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    creds = load_credentials(args.env)

    api_key = (
        config.get("data_source", {}).get("api_key")
        or creds.get("twelvedata_api_key")
        or os.environ.get("TWELVEDATA_API_KEY", "")
    )

    if not api_key:
        print("\n[ERROR] TWELVEDATA_API_KEY not set.")
        print("  Get a free key at https://twelvedata.com/register")
        print("  Then add to .env:  TWELVEDATA_API_KEY=your_key_here\n")
        sys.exit(1)

    loader = TwelveDataLoader(config, api_key=api_key)
    symbols = [args.symbol] if args.symbol else config["system"]["symbol_list"]
    active_pairs = config["strategy"].get("active_pairs", [{"low": "H4", "high": "D1", "label": "SWING"}])
    friday_exit = not args.no_friday_exit

    print(f"\n{'='*70}")
    print(f"  FILTER IMPACT BACKTEST - SMC & RSI")
    print(f"  Symbols:     {', '.join(symbols)}")
    print(f"  Pairs:       {', '.join(p['label'] for p in active_pairs)}")
    print(f"  Period:      Last {args.days} calendar days")
    print(f"  SMC Score:   {args.smc_score}")
    print(f"  Friday exit: {'ON' if friday_exit else 'OFF'}")
    print(f"{'='*70}")

    # Fetch data once
    data_cache = fetch_all_data(loader, symbols, active_pairs, n_bars=args.bars)

    all_results = []

    # -- Variant 1: BASELINE (no RSI, no SMC) --
    print(f"\n{'-'*60}")
    print(f"  VARIANT 1: BASELINE (current live)")
    print(f"{'-'*60}")
    cfg_base = copy.deepcopy(config)
    cfg_base["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = False
    strategy_base = LiquidityWickStrategy(cfg_base)
    trades_base = run_variant(
        strategy_base, data_cache, cfg_base, symbols,
        active_pairs, args.days, smc_enabled=False,
        friday_exit=friday_exit, variant_name="BASELINE"
    )
    m_base = calculate_metrics(trades_base, "BASELINE")
    all_results.append(m_base)

    # -- Variant 2: +RSI --
    print(f"\n{'-'*60}")
    print(f"  VARIANT 2: +RSI (rsi_confirmation=True)")
    print(f"{'-'*60}")
    cfg_rsi = copy.deepcopy(config)
    cfg_rsi["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = True
    strategy_rsi = LiquidityWickStrategy(cfg_rsi)
    trades_rsi = run_variant(
        strategy_rsi, data_cache, cfg_rsi, symbols,
        active_pairs, args.days, smc_enabled=False,
        friday_exit=friday_exit, variant_name="+RSI"
    )
    m_rsi = calculate_metrics(trades_rsi, "+RSI")
    all_results.append(m_rsi)

    # -- Variant 3: +SMC --
    print(f"\n{'-'*60}")
    print(f"  VARIANT 3: +SMC (min_score={args.smc_score})")
    print(f"{'-'*60}")
    cfg_smc = copy.deepcopy(config)
    cfg_smc["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = False
    strategy_smc = LiquidityWickStrategy(cfg_smc)
    trades_smc = run_variant(
        strategy_smc, data_cache, cfg_smc, symbols,
        active_pairs, args.days, smc_enabled=True, smc_min_score=args.smc_score,
        friday_exit=friday_exit, variant_name="+SMC"
    )
    m_smc = calculate_metrics(trades_smc, "+SMC")
    all_results.append(m_smc)

    # -- Variant 4: +RSI+SMC --
    print(f"\n{'-'*60}")
    print(f"  VARIANT 4: +RSI+SMC (both enabled)")
    print(f"{'-'*60}")
    cfg_both = copy.deepcopy(config)
    cfg_both["strategy"]["symbol_overrides"]["XAUUSD"]["rsi_confirmation"] = True
    strategy_both = LiquidityWickStrategy(cfg_both)
    trades_both = run_variant(
        strategy_both, data_cache, cfg_both, symbols,
        active_pairs, args.days, smc_enabled=True, smc_min_score=args.smc_score,
        friday_exit=friday_exit, variant_name="+RSI+SMC"
    )
    m_both = calculate_metrics(trades_both, "+RSI+SMC")
    all_results.append(m_both)

    # -- Comparison --
    valid_results = [r for r in all_results if r is not None]
    if valid_results:
        print_comparison_table(valid_results)
    else:
        print("\nNo trades generated by any variant. Try increasing --days or --bars.")

    loader.shutdown()
    print("Done.")


if __name__ == "__main__":
    main()
