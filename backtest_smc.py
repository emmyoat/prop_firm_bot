"""
SMC Comparative Backtest
Compares win rates: Original Strategy vs. Strategy + OB/FVG Filters

This backtest will run two scenarios:
1. BASELINE: Original Liquidity Wick Strategy (no SMC filters)
2. ENHANCED: Liquidity Wick + Order Block + FVG Confluence Filter
"""
import yfinance as yf
from src.utils.logger import setup_logger
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score
from src.models import SignalType
import pandas as pd
import numpy as np
from datetime import datetime
import logging

# Suppress yfinance warnings
import warnings
warnings.filterwarnings('ignore')


def run_comparative_backtest(min_confluence_score: int = 50):
    """
    Run comparative backtest: Baseline vs. SMC-Enhanced.
    
    Parameters:
    - min_confluence_score: Minimum score required for SMC-enhanced trades
    """
    config = load_config()
    creds = load_credentials()
    
    setup_logger("PropBot.Strategy", logging.WARNING)  # Suppress debug logs
    
    loader = MT5DataLoader(config)
    loader.connect(creds)
    
    symbols = config['system'].get('symbol_list', ["XAUUSD"])
    active_pairs = config['strategy'].get('active_pairs', [])
    strategy = LiquidityWickStrategy(config)
    
    ticker_map = {"XAUUSD": "GC=F", "US30": "YM=F", "NAS100": "NQ=F", "USTECH100": "NQ=F"}
    
    print("\n" + "=" * 60)
    print("    SMC COMPARATIVE BACKTEST")
    print("    Baseline vs. Order Block + FVG Confluence")
    print("=" * 60)
    
    # Results storage
    baseline_results = {"trades": 0, "wins": 0, "pnl": 0.0}
    enhanced_results = {"trades": 0, "wins": 0, "pnl": 0.0}
    
    for symbol in symbols:
        yf_ticker = ticker_map.get(symbol, "GC=F")
        
        for pair in active_pairs:
            label = pair['label']
            tf_entry = pair['low']
            tf_trend = pair['high']
            
            print(f"\n[{symbol} - {label}] Loading data...")
            
            # Fetch data
            df_entry = loader.fetch_data(symbol, tf_entry, 2500)
            df_trend = loader.fetch_data(symbol, tf_trend, 2500)
            
            if df_entry is None or df_trend is None:
                try:
                    tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", 
                              "H1": "1h", "H4": "1h", "D1": "1d"}
                    df_entry = yf.download(yf_ticker, period="1mo", 
                                           interval=tf_map.get(tf_entry, "1h"), progress=False)
                    df_trend = yf.download(yf_ticker, period="3mo", 
                                           interval=tf_map.get(tf_trend, "1d"), progress=False)
                    
                    if df_entry.empty:
                        print(f"   [SKIP] No data available")
                        continue
                    
                    df_entry.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() 
                                        for c in df_entry.columns]
                    df_trend.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() 
                                        for c in df_trend.columns]
                    df_entry.index = df_entry.index.tz_localize(None)
                    df_trend.index = df_trend.index.tz_localize(None)
                except Exception as e:
                    print(f"   [ERROR] {e}")
                    continue
            
            if df_entry is not None and not df_entry.empty:
                if 'time' in df_entry.columns:
                    df_entry.set_index('time', inplace=True, drop=False)
                df_entry.index = pd.to_datetime(df_entry.index)
                
            if df_trend is not None and not df_trend.empty:
                if 'time' in df_trend.columns:
                    df_trend.set_index('time', inplace=True, drop=False)
                df_trend.index = pd.to_datetime(df_trend.index)
            
            pip_unit = 0.1 if "XAU" in symbol else 1.0
            trading_start = pd.Timestamp.now() - pd.Timedelta(days=30)
            
            # --- Run BASELINE backtest ---
            baseline_trades = _run_single_backtest(
                df_entry, df_trend, strategy, config, symbol, trading_start, pip_unit,
                use_smc_filter=False
            )
            
            # --- Run ENHANCED backtest ---
            enhanced_trades = _run_single_backtest(
                df_entry, df_trend, strategy, config, symbol, trading_start, pip_unit,
                use_smc_filter=True, min_score=min_confluence_score
            )
            
            # Aggregate results
            baseline_wins = len([t for t in baseline_trades if t['pnl'] > 0])
            enhanced_wins = len([t for t in enhanced_trades if t['pnl'] > 0])
            
            baseline_results["trades"] += len(baseline_trades)
            baseline_results["wins"] += baseline_wins
            baseline_results["pnl"] += sum(t['pnl'] for t in baseline_trades)
            
            enhanced_results["trades"] += len(enhanced_trades)
            enhanced_results["wins"] += enhanced_wins
            enhanced_results["pnl"] += sum(t['pnl'] for t in enhanced_trades)
            
            # Print per-symbol results
            base_wr = (baseline_wins / len(baseline_trades) * 100) if baseline_trades else 0
            enh_wr = (enhanced_wins / len(enhanced_trades) * 100) if enhanced_trades else 0
            
            print(f"   BASELINE: {len(baseline_trades)} trades | {base_wr:.1f}% WR")
            print(f"   ENHANCED: {len(enhanced_trades)} trades | {enh_wr:.1f}% WR")
    
    # --- Final Summary ---
    print("\n" + "=" * 60)
    print("                    FINAL RESULTS")
    print("=" * 60)
    
    base_wr = (baseline_results["wins"] / baseline_results["trades"] * 100) if baseline_results["trades"] else 0
    enh_wr = (enhanced_results["wins"] / enhanced_results["trades"] * 100) if enhanced_results["trades"] else 0
    
    print(f"\n{'Metric':<25} {'Baseline':>15} {'SMC Enhanced':>15}")
    print("-" * 55)
    print(f"{'Total Trades':<25} {baseline_results['trades']:>15} {enhanced_results['trades']:>15}")
    print(f"{'Winning Trades':<25} {baseline_results['wins']:>15} {enhanced_results['wins']:>15}")
    print(f"{'Win Rate':<25} {base_wr:>14.1f}% {enh_wr:>14.1f}%")
    print(f"{'Total PnL (points)':<25} {baseline_results['pnl']:>15.2f} {enhanced_results['pnl']:>15.2f}")
    
    # Improvement calculation
    if baseline_results["trades"] > 0 and enhanced_results["trades"] > 0:
        wr_diff = enh_wr - base_wr
        trade_reduction = ((baseline_results["trades"] - enhanced_results["trades"]) 
                           / baseline_results["trades"] * 100)
        
        print("\n" + "-" * 55)
        print(f"{'Win Rate Change':<25} {'+' if wr_diff >= 0 else ''}{wr_diff:>14.1f}%")
        print(f"{'Trade Reduction':<25} {trade_reduction:>14.1f}%")
        
        if wr_diff > 0:
            print("\n[+] SMC Filter IMPROVED win rate!")
        elif wr_diff < 0:
            print("\n[-] SMC Filter REDUCED win rate (consider adjusting min_score)")
        else:
            print("\n[=] No significant change in win rate")
    
    print("=" * 60 + "\n")
    
    return baseline_results, enhanced_results


def _run_single_backtest(df_entry, df_trend, strategy, config, symbol, 
                         trading_start, pip_unit, use_smc_filter=False, min_score=50):
    """
    Run a single backtest pass.
    
    Parameters:
    - use_smc_filter: If True, only take trades with confluence score >= min_score
    """
    active_trades = []
    pending_orders = []
    closed_trades = []
    
    trailing_activation = config['risk'].get('trailing_stop_activation_pips', 50) * pip_unit
    trailing_step = config['risk'].get('trailing_step_pips', 25) * pip_unit
    
    for i in range(100, len(df_entry)):
        bar = df_entry.iloc[i]
        curr_time = bar.name
        
        if not isinstance(curr_time, pd.Timestamp):
            try:
                curr_time = pd.to_datetime(curr_time)
            except:
                continue
        
        if curr_time < trading_start:
            continue
        
        # Friday exit
        if curr_time.weekday() == 4 and curr_time.hour >= 21:
            pending_orders = []
            for t in active_trades[:]:
                t['pnl'] = (bar['close'] - t['entry']) if t['type'] == 'BUY' else (t['entry'] - bar['close'])
                closed_trades.append(t)
                active_trades.remove(t)
            continue
        
        # Pending order management
        for order in pending_orders[:]:
            if (curr_time - order['placed_time']).total_seconds() > (4 * 3600):
                pending_orders.remove(order)
                continue
            
            triggered = False
            if order['type'] == 'BUY_STOP' and bar['high'] >= order['entry']:
                triggered = True
            elif order['type'] == 'SELL_STOP' and bar['low'] <= order['entry']:
                triggered = True
            
            if triggered:
                active_trades.append({
                    'type': 'BUY' if 'BUY' in order['type'] else 'SELL',
                    'entry': order['entry'],
                    'sl': order['sl'],
                    'tp': order['tp'],
                    'confluence_score': order.get('confluence_score', 0)
                })
                pending_orders.remove(order)
        
        # Trade management (SL/TP/Trailing)
        for t in active_trades[:]:
            exit_price = None
            
            if t['type'] == 'BUY':
                if bar['low'] <= t['sl']:
                    exit_price = t['sl']
                elif t['tp'] > 0 and bar['high'] >= t['tp']:
                    exit_price = t['tp']
                else:
                    profit_dist = bar['high'] - t['entry']
                    if profit_dist >= trailing_activation:
                        new_sl = t['entry'] + (profit_dist - trailing_step)
                        if new_sl > t['sl']:
                            t['sl'] = new_sl
            else:
                if bar['high'] >= t['sl']:
                    exit_price = t['sl']
                elif t['tp'] > 0 and bar['low'] <= t['tp']:
                    exit_price = t['tp']
                else:
                    profit_dist = t['entry'] - bar['low']
                    if profit_dist >= trailing_activation:
                        new_sl = t['entry'] - (profit_dist - trailing_step)
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
            
            if exit_price:
                t['pnl'] = (exit_price - t['entry']) if t['type'] == 'BUY' else (t['entry'] - exit_price)
                closed_trades.append(t)
                active_trades.remove(t)
        
        # Signal generation
        if len(active_trades) == 0 and len(pending_orders) == 0:
            data_map = {
                "LowTF": df_entry.iloc[:i+1], 
                "HighTF": df_trend[df_trend.index <= curr_time]
            }
            
            signal = strategy.generate_signal(data_map, symbol)
            
            if signal.signal_type != SignalType.NEUTRAL:
                # --- SMC FILTER ---
                if use_smc_filter:
                    # Get SMC zones from the entry timeframe data
                    entry_data = df_entry.iloc[:i+1]
                    fvgs = detect_fvg_zones(entry_data)
                    obs = detect_order_blocks(entry_data)
                    
                    score, zone = calculate_confluence_score(
                        current_price=bar['close'],
                        signal_type=signal.signal_type.name,
                        order_blocks=obs,
                        fvg_zones=fvgs,
                        entry_price=signal.price,
                        stop_loss=signal.sl_price
                    )
                    
                    if score < min_score:
                        continue  # Skip low-confluence signals
                else:
                    score = 0
                
                # Place order
                if signal.is_stop_order:
                    pending_orders.append({
                        'type': 'BUY_STOP' if signal.signal_type == SignalType.BUY else 'SELL_STOP',
                        'entry': signal.price,
                        'sl': signal.sl_price,
                        'tp': signal.tp_price,
                        'placed_time': curr_time,
                        'confluence_score': score
                    })
                else:
                    active_trades.append({
                        'type': 'BUY' if signal.signal_type == SignalType.BUY else 'SELL',
                        'entry': signal.price,
                        'sl': signal.sl_price,
                        'tp': signal.tp_price,
                        'confluence_score': score
                    })
    
    return closed_trades


if __name__ == "__main__":
    # Run with default 50-point minimum confluence
    run_comparative_backtest(min_confluence_score=50)
    
    print("\n--- Testing with higher confluence threshold ---")
    run_comparative_backtest(min_confluence_score=70)
