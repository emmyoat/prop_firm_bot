import yfinance as yf
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType
import pandas as pd
import numpy as np
from datetime import datetime

def run_backtest(friday_exit_enabled=True):
    config = load_config()
    creds = load_credentials()
    
    loader = MT5DataLoader(config)
    if not loader.connect(creds):
        pass
    
    symbols = config['system'].get('symbol_list', ["XAUUSD", "US30", "NAS100"])
    active_pairs = config['strategy'].get('active_pairs', [])
    strategy = LiquidityWickStrategy(config)
    
    ticker_map = {"XAUUSD": "GC=F", "US30": "YM=F", "NAS100": "NQ=F"}

    mode_str = "FRIDAY EXIT" if friday_exit_enabled else "WEEKEND HOLDING"
    print(f"\n--- {mode_str} BACKTEST ---")

    total_wins = 0
    total_trades_count = 0

    for symbol in symbols:
        yf_ticker = ticker_map.get(symbol, "GC=F")
        for pair in active_pairs:
            label = pair['label']
            tf_entry = pair['low']
            tf_trend = pair['high']
            
            df_entry = loader.fetch_data(symbol, tf_entry, 2500)
            df_trend = loader.fetch_data(symbol, tf_trend, 2500)
            
            if df_entry is None or df_trend is None:
                try:
                    print(f"DEBUG: Downloading data for {symbol}...")
                    tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "1h", "D1": "1d"} 
                    df_entry = yf.download(yf_ticker, period="1mo", interval=tf_map.get(tf_entry, "1h"), progress=False)
                    df_trend = yf.download(yf_ticker, period="3mo", interval=tf_map.get(tf_trend, "1d"), progress=False)
                    print(f"DEBUG: Data downloaded. Entry={len(df_entry)}, Trend={len(df_trend)}")
                    if df_entry.empty: continue
                    df_entry.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_entry.columns]
                    df_trend.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_trend.columns]
                    df_entry.index = df_entry.index.tz_localize(None)
                    df_trend.index = df_trend.index.tz_localize(None)
                except: continue

            active_trades = []
            pending_orders = [] # New: Store pending orders
            closed_trades = []
            closed_trades = []
            pip_unit = 0.1 if "XAU" in symbol else 1.0
            
            if df_entry is not None and not df_entry.empty:
                df_entry.index = pd.to_datetime(df_entry.index)
            if df_trend is not None and not df_trend.empty:
                df_trend.index = pd.to_datetime(df_trend.index)

            for i in range(100, len(df_entry)):
                bar = df_entry.iloc[i]
                curr_time = bar.name
                if not isinstance(curr_time, pd.Timestamp):
                    try: curr_time = pd.to_datetime(curr_time)
                    except: continue
                
                if friday_exit_enabled and curr_time.weekday() == 4 and curr_time.hour >= 21:
                    pending_orders = [] # Clear pending on Friday
                    for t in active_trades[:]:
                        t['pnl'] = (bar['close'] - t['entry']) if t['type'] == 'BUY' else (t['entry'] - bar['close'])
                        closed_trades.append(t)
                        active_trades.remove(t)
                    continue

                # --- 1. Pending Order Management ---
                # Check if price triggered any pending stop orders
                for order in pending_orders[:]:
                    # Expiration check (4 hours = 4 candles for H1, 1 candle for H4?)
                    # Simplified: timestamp check
                    if (curr_time - order['placed_time']).total_seconds() > (4 * 3600):
                        pending_orders.remove(order)
                        continue

                    triggered = False
                    if order['type'] == 'BUY_STOP':
                        if bar['high'] >= order['entry']: # Price crossed up
                            triggered = True # Trigger at Order Price
                            # Slippage simulation? Let's assume perfect fill for now
                    elif order['type'] == 'SELL_STOP':
                        if bar['low'] <= order['entry']: # Price crossed down
                            triggered = True

                    if triggered:
                        # Convert to Active Trade
                        active_trades.append({
                            'type': 'BUY' if 'BUY' in order['type'] else 'SELL',
                            'entry': order['entry'],
                            'sl': order['sl'],
                            'tp': order['tp']
                        })
                        pending_orders.remove(order)

                # Management
                trailing_activation = config['risk'].get('trailing_stop_activation_pips', 50) * pip_unit
                trailing_step = config['risk'].get('trailing_step_pips', 25) * pip_unit
                
                for t in active_trades[:]:
                    exit_price = None
                    # Verify TP/SL hit FIRST
                    if t['type'] == 'BUY':
                        if bar['low'] <= t['sl']: exit_price = t['sl']
                        elif t['tp'] > 0 and bar['high'] >= t['tp']: exit_price = t['tp']
                        
                        # Trailing Stop Update (High of bar - Entry > Activation)
                        if not exit_price:
                            profit_dist = bar['high'] - t['entry']
                            if profit_dist >= trailing_activation:
                                new_sl = t['entry'] + (profit_dist - trailing_step) # Very rough
                                if new_sl > t['sl']: t['sl'] = new_sl

                    else: # SELL
                        if bar['high'] >= t['sl']: exit_price = t['sl']
                        elif t['tp'] > 0 and bar['low'] <= t['tp']: exit_price = t['tp']
                        
                        # Trailing Stop Update
                        if not exit_price:
                            profit_dist = t['entry'] - bar['low']
                            if profit_dist >= trailing_activation:
                                new_sl = t['entry'] - (profit_dist - trailing_step)
                                if new_sl < t['sl']: t['sl'] = new_sl
                    
                    if exit_price:
                        t['pnl'] = (exit_price - t['entry']) if t['type'] == 'BUY' else (t['entry'] - exit_price)
                        closed_trades.append(t)
                        active_trades.remove(t)

                # Entry (Only if no active trades AND no pending orders)
                if len(active_trades) == 0 and len(pending_orders) == 0:
                    data_map = {"LowTF": df_entry.iloc[:i], "HighTF": df_trend[df_trend.index <= curr_time]}
                    signal = strategy.generate_signal(data_map, symbol)
                    
                    if signal.signal_type != SignalType.NEUTRAL:
                        # Logic for Stop Orders vs Market
                        if signal.is_stop_order:
                            pending_orders.append({
                                'type': 'BUY_STOP' if signal.signal_type == SignalType.BUY else 'SELL_STOP',
                                'entry': signal.price,
                                'sl': signal.sl_price,
                                'tp': signal.tp_price,
                                'placed_time': curr_time
                            })
                        else:
                            # Immediate Market Entry (fallback)
                            active_trades.append({'type': 'BUY' if signal.signal_type == SignalType.BUY else 'SELL', 'entry': signal.price, 'sl': signal.sl_price, 'tp': signal.tp_price})

            wins = [t for t in closed_trades if t['pnl'] > 0]
            total_wins += len(wins)
            total_trades_count += len(closed_trades)
            
    if total_trades_count > 0:
        wr = (total_wins / total_trades_count) * 100
        print(f"[{mode_str}] Total Trades: {total_trades_count} | Combined Win Rate: {wr:.2f}%")
        return wr
    return 0

if __name__ == "__main__":
    wr_exit = run_backtest(friday_exit_enabled=True)
    wr_hold = run_backtest(friday_exit_enabled=False)
    
    print("\n" + "="*40)
    print(f"COMPARISON SUMMARY")
    print(f"Friday Exit Win Rate:    {wr_exit:.2f}%")
    print(f"Weekend Holding Win Rate: {wr_hold:.2f}%")
    print("="*40)
