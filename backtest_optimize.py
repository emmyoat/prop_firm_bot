"""
SMC Threshold Optimization Backtest
Tests multiple confluence score thresholds to find optimal balance.
"""
import yfinance as yf
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score
from src.models import SignalType
import pandas as pd
import warnings
warnings.filterwarnings('ignore')


def run_threshold_optimization():
    """Test multiple confluence thresholds and compare results."""
    
    config = load_config()
    creds = load_credentials()
    
    loader = MT5DataLoader(config)
    loader.connect(creds)
    
    symbols = config['system'].get('symbol_list', ["XAUUSD"])
    active_pairs = config['strategy'].get('active_pairs', [])
    strategy = LiquidityWickStrategy(config)
    
    ticker_map = {"XAUUSD": "GC=F", "US30": "YM=F", "NAS100": "NQ=F", "USTECH100": "NQ=F"}
    
    # Thresholds to test (0 = no filter, baseline)
    thresholds = [0, 20, 30, 40, 50, 60, 70]
    
    print("\n" + "=" * 70)
    print("    SMC THRESHOLD OPTIMIZATION BACKTEST")
    print("    Testing: " + ", ".join(str(t) for t in thresholds))
    print("=" * 70)
    
    # Storage for all threshold results
    all_results = {t: {"trades": 0, "wins": 0, "pnl": 0.0} for t in thresholds}
    
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
                        continue
                    
                    df_entry.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() 
                                        for c in df_entry.columns]
                    df_trend.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() 
                                        for c in df_trend.columns]
                    df_entry.index = df_entry.index.tz_localize(None)
                    df_trend.index = df_trend.index.tz_localize(None)
                except:
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
            
            # Run backtest for each threshold
            for threshold in thresholds:
                trades = _run_backtest(
                    df_entry, df_trend, strategy, config, symbol, 
                    trading_start, pip_unit, min_score=threshold
                )
                
                wins = len([t for t in trades if t['pnl'] > 0])
                all_results[threshold]["trades"] += len(trades)
                all_results[threshold]["wins"] += wins
                all_results[threshold]["pnl"] += sum(t['pnl'] for t in trades)
    
    # --- Print Results Table ---
    print("\n" + "=" * 70)
    print("                    OPTIMIZATION RESULTS")
    print("=" * 70)
    
    print(f"\n{'Threshold':>10} {'Trades':>10} {'Wins':>10} {'Win Rate':>12} {'PnL':>12}")
    print("-" * 54)
    
    baseline_trades = all_results[0]["trades"]
    
    best_balance = None
    best_balance_score = 0
    
    for threshold in thresholds:
        r = all_results[threshold]
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0
        
        # Calculate a "balance score" - weighted combo of WR and trade count
        # Higher is better: WR matters more, but we want reasonable trade count
        trade_retention = (r["trades"] / baseline_trades * 100) if baseline_trades else 0
        balance_score = (wr * 0.7) + (trade_retention * 0.3)
        
        if balance_score > best_balance_score:
            best_balance_score = balance_score
            best_balance = threshold
        
        label = ""
        if threshold == 0:
            label = " (BASELINE)"
        elif threshold == best_balance:
            label = " <-- BEST BALANCE"
            
        print(f"{threshold:>10} {r['trades']:>10} {r['wins']:>10} {wr:>11.1f}% {r['pnl']:>11.2f}{label}")
    
    print("-" * 54)
    
    # Recommendation
    print("\n" + "=" * 70)
    print("                    RECOMMENDATION")
    print("=" * 70)
    
    baseline_wr = (all_results[0]["wins"] / all_results[0]["trades"] * 100) if all_results[0]["trades"] else 0
    best_r = all_results[best_balance]
    best_wr = (best_r["wins"] / best_r["trades"] * 100) if best_r["trades"] else 0
    
    print(f"\nOptimal Confluence Threshold: {best_balance}")
    print(f"  - Trades: {best_r['trades']} ({best_r['trades']/baseline_trades*100:.0f}% of baseline)")
    print(f"  - Win Rate: {best_wr:.1f}% (vs {baseline_wr:.1f}% baseline)")
    print(f"  - Improvement: +{best_wr - baseline_wr:.1f}% win rate")
    
    if best_balance <= 30:
        print("\n[AGGRESSIVE] Low threshold = More trades, slightly filtered")
    elif best_balance <= 50:
        print("\n[BALANCED] Medium threshold = Good trade count with improved quality")
    else:
        print("\n[CONSERVATIVE] High threshold = Fewer but higher probability trades")
    
    print("\n" + "=" * 70)
    
    return all_results


def _run_backtest(df_entry, df_trend, strategy, config, symbol, 
                  trading_start, pip_unit, min_score=0):
    """Run a single backtest pass with specified min_score."""
    
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
                    'tp': order['tp']
                })
                pending_orders.remove(order)
        
        # Trade management
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
                # SMC Filter (if min_score > 0)
                if min_score > 0:
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
                        continue
                
                # Place order
                if signal.is_stop_order:
                    pending_orders.append({
                        'type': 'BUY_STOP' if signal.signal_type == SignalType.BUY else 'SELL_STOP',
                        'entry': signal.price,
                        'sl': signal.sl_price,
                        'tp': signal.tp_price,
                        'placed_time': curr_time
                    })
                else:
                    active_trades.append({
                        'type': 'BUY' if signal.signal_type == SignalType.BUY else 'SELL',
                        'entry': signal.price,
                        'sl': signal.sl_price,
                        'tp': signal.tp_price
                    })
    
    return closed_trades


if __name__ == "__main__":
    run_threshold_optimization()
