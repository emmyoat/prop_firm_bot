import yfinance as yf
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType

def run_backtest():
    # 1. Load Configuration & Credentials
    config = load_config()
    creds = load_credentials()
    
    # 2. Setup Data Loader
    loader = MT5DataLoader(config)
    # Try to connect with real credentials
    if not loader.connect(creds):
        print("[WARN] MT5 Login Failed. Falling back to offline/yfinance...")
        # Try initialize without login just for data
        pass
    
    symbol = "XAUUSD"
    # Hardcode spread for simulation (Points)
    SPREAD_POINTS = 20 # 2 pips
    
    strategy = LiquidityWickStrategy(config)
    active_pairs = config['strategy'].get('active_pairs', [])
    
    print("--- BACKTEST: BREAKEVEN & TRAILING STOP ---")
    print(f"Strategy: Liquidity Wick | Symbol: {symbol}")
    print("-" * 60)

    total_pnl_all = 0.0
    total_trades_count = 0
    total_wins = 0
    all_closed_trades = []

    for pair in active_pairs:
        label = pair['label']
        tf_entry = pair['low']
        tf_trend = pair['high']
        
        print(f"\nTesting {label} ({tf_entry}/{tf_trend})...")
        
        # Fetch Data or Mock
        count = 3000
        df_entry = loader.fetch_data(symbol, tf_entry, count)
        df_trend = loader.fetch_data(symbol, tf_trend, count)
        
        # Fallback to yfinance if MT5 data is missing
        if df_entry is None or df_trend is None:
            print(f"[{label}] MT5 Data missing. Trying yfinance fallback...")
            try:
                # Map timeframe to yfinance interval
                # H4 -> 1h (yf doesn't support 4h well maybe? 60m... valid intervals: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo)
                # D1 -> 1d
                # H1 -> 1h
                tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "1h", "D1": "1d"} 
                
                # For H4, we might have to resample or just use 1h for approximation
                # Note: yfinance only gives last 60 days for 15m/5m. 730 days for 1h.
                # XAUUSD -> GC=F (Futures) or XAUUSD=X
                ticker = "GC=F" # Gold Futures might be better proxy or XAUUSD=X
                
                interval_entry = tf_map.get(tf_entry, "1h")
                interval_trend = tf_map.get(tf_trend, "1d")
                
                df_entry = yf.download(ticker, period="3mo", interval=interval_entry, progress=False)
                df_trend = yf.download(ticker, period="1y", interval=interval_trend, progress=False)
                
                if df_entry.empty or df_trend.empty:
                    print(f"[{label}] yfinance data empty.")
                    continue
                    
                # Normalize columns: yfinance has Capital Case (Open, High...), we need lowercase
                # Handle MultiIndex (Price, Ticker) if present
                new_cols = []
                for c in df_entry.columns:
                    if isinstance(c, tuple):
                        new_cols.append(c[0].lower())
                    else:
                        new_cols.append(c.lower())
                df_entry.columns = new_cols
                
                new_cols = []
                for c in df_trend.columns:
                    if isinstance(c, tuple):
                        new_cols.append(c[0].lower())
                    else:
                        new_cols.append(c.lower())
                df_trend.columns = new_cols

                # Strip Timezone to avoid comparison errors
                try:
                    df_entry.index = df_entry.index.tz_localize(None)
                except: pass
                try:
                    df_trend.index = df_trend.index.tz_localize(None)
                except: pass
                
                # Handle H4 approximation if needed (resample 1H to 4H)
                # For simplicity, we stick to what we downloaded (might be 1H instead of 4H)
                
            except Exception as e:
                print(f"[{label}] yfinance failed: {e}")
                continue
        
        if df_entry is None or df_entry.empty or df_trend is None or df_trend.empty:
            print(f"[{label}] Skipped: No Data")
            continue

        # Determine Date Range
        if 'time' in df_entry.columns:
            start_date = df_entry['time'].iloc[0]
            end_date = df_entry['time'].iloc[-1]
        else:
            start_date = df_entry.index[0]
            end_date = df_entry.index[-1]
            
        days = (end_date - start_date).days
        print(f"[{label}] Data Range: {start_date} to {end_date} ({days} days)")
            
        # Simulation Registry
        # List of dicts: {'ticket': int, 'type': 'BUY'/'SELL', 'entry': float, 'sl': float, 'tp': float, 'pnl': float, 'closed': bool}
        active_trades = [] 
        closed_trades = []
        ticket_counter = 1
        
        # Scaling Params
        scaling_pips = config['risk'].get('scaling_threshold_pips', 15)
        
        # Loop
        start_idx = 200
        
        for i in range(start_idx, len(df_entry)):
            current_bar = df_entry.iloc[i]
            current_time = current_bar.name
            
            # 1. Update Market Price (Approximate with Close)
            current_price = current_bar['close']
            high = current_bar['high']
            low = current_bar['low']
            
            # 2. Manage Existing Trades (Check SL/TP/Scaling)
            # We treat H/L as execution triggers
            
            # Copy list to allow modification
            for trade in active_trades[:]:
                # A. Check Exit
                exit_price = None
                
                if trade['type'] == 'BUY':
                    # SL Hit?
                    if low <= trade['sl']:
                        exit_price = trade['sl']
                    # TP Hit?
                    elif trade['tp'] > 0 and high >= trade['tp']:
                        exit_price = trade['tp']
                        
                elif trade['type'] == 'SELL':
                    # SL Hit?
                    if high >= trade['sl']:
                        exit_price = trade['sl']
                    # TP Hit?
                    elif trade['tp'] > 0 and low <= trade['tp']:
                        exit_price = trade['tp']
                
                if exit_price:
                    pnl = 0.0
                    if trade['type'] == 'BUY':
                        pnl = exit_price - trade['entry'] - (SPREAD_POINTS * 0.01) # Spread cost
                    else:
                        pnl = trade['entry'] - exit_price - (SPREAD_POINTS * 0.01)
                    
                    trade['pnl'] = pnl
                    trade['exit_price'] = exit_price
                    trade['closed'] = True
                    closed_trades.append(trade)
                    active_trades.remove(trade)
                    continue
                    
                # B. Manage Stops (Breakeven & Trailing)
                BE_START_PRICE = config['risk'].get('breakeven_activation_pips', 20) * 0.10
                TRAIL_START_PRICE = config['risk'].get('trailing_stop_activation_pips', 45) * 0.10
                TRAIL_DIST_PRICE = 15 * 0.10 
                
                if trade['type'] == 'BUY':
                    max_price = high
                    profit_dist = max_price - trade['entry']
                    
                    # 1. Breakeven
                    if profit_dist >= BE_START_PRICE:
                         new_sl = trade['entry'] + (2 * 0.10)
                         if new_sl > trade['sl']: trade['sl'] = new_sl

                    # 2. Trailing Stop
                    if profit_dist >= TRAIL_START_PRICE:
                        new_sl = current_price - TRAIL_DIST_PRICE 
                        if new_sl > trade['sl']: trade['sl'] = new_sl
                            
                elif trade['type'] == 'SELL':
                    min_price = low
                    profit_dist = trade['entry'] - min_price
                    
                    # 1. Breakeven
                    if profit_dist >= BE_START_PRICE:
                        new_sl = trade['entry'] - (2 * 0.10)
                        if trade['sl'] == 0 or new_sl < trade['sl']: trade['sl'] = new_sl

                    # 2. Trailing Stop
                    if profit_dist >= TRAIL_START_PRICE:
                        new_sl = current_price + TRAIL_DIST_PRICE
                        if trade['sl'] == 0 or new_sl < trade['sl']: trade['sl'] = new_sl



            # 4. Generate New Signals (Entry)
            # Only if no position exists (or distinct logic? Main.py uses scaling when pos exists)
            # So here we only enter if NO active trades for that direction?
            # Strategy usually generates signals constantly. We filter if we already have a trade unless it's a separate setup?
            # Main.py logic: "Generate Signal" -> execute.
            # But usually we don't open a NEW unrelated trade if we are managing a sequence, 
            # UNLESS it's a completely new signal. 
            # For simplicity: If we have active trades, we rely on Scaling logic. 
            # If we have NO active trades, we look for Signal.
            
            if len(active_trades) == 0:
                # Slice Data
                curr_entry_df = df_entry.iloc[:i]
                curr_trend_df = df_trend[df_trend.index <= current_time]
                
                if len(curr_trend_df) < 50: continue
                
                data_map = {"LowTF": curr_entry_df, "HighTF": curr_trend_df}
                signal = strategy.generate_signal(data_map, symbol)
                
                if signal.signal_type != SignalType.NEUTRAL:
                    t_type = 'BUY' if signal.signal_type == SignalType.BUY else 'SELL'
                    
                    # Entry Logic
                    entry_price = signal.price
                    # Check if Limit or Market (Strategy returns price)
                    # We assume fill for simulation
                    
                    # Create Trade
                    trade = {
                        'ticket': ticket_counter,
                        'type': t_type,
                        'entry': entry_price,
                        'sl': signal.sl_price,
                        'tp': signal.tp_price,
                        'pnl': 0.0,
                        'closed': False,
                        'comment': 'Signal'
                    }
                    ticket_counter += 1
                    active_trades.append(trade)
        
        # End of Loop - Close remaining
        for trade in active_trades:
            # Force close at end
            exit_price = df_entry.iloc[-1]['close']
            pnl = 0.0
            if trade['type'] == 'BUY':
                pnl = exit_price - trade['entry']
            else:
                pnl = trade['entry'] - exit_price
            trade['pnl'] = pnl
            trade['closed'] = True
            closed_trades.append(trade)

        # Stats for Pair
        if not closed_trades:
            print(f"[{label}] No trades generated.")
            continue
            
        wins = [t for t in closed_trades if t['pnl'] > 0]
        loss = [t for t in closed_trades if t['pnl'] <= 0]
        
        pair_pnl = sum(t['pnl'] for t in closed_trades)
        wr = len(wins) / len(closed_trades) * 100
        
        print(f"[{label}] Trades: {len(closed_trades)} (Scales included) | Win Rate: {wr:.1f}%")
        print(f"[{label}] Net Profit: {pair_pnl:.2f} (Price Diff)")
        
        total_pnl_all += pair_pnl
        total_trades_count += len(closed_trades)
        total_wins += len(wins)
        all_closed_trades.extend(closed_trades)

    print("\n" + "="*60)
    print("FINAL PORTFOLIO RESULT")
    print("="*60)
    
    # Calculate Percentage Return
    # Assumption: $100k account, 1% risk per trade
    START_BALANCE = 100000
    RISK_PCT = config['risk'].get('account_equity_risk_pct', 1.0)
    current_balance = START_BALANCE
    
    # We need average SL distance to convert Price Diff to $ PnL accurately
    # Or simplified: PnL_Dollars = (PriceDiff / SL_Dist) * (Balance * Risk)
    # Let's assume Avg SL is 50 pips (5.0 price units) for Gold if not tracked
    # Better: Track R-multiples.
    
    total_r = 0.0
    for t in all_closed_trades:
        # entry - sl = risk distance
        risk_dist = abs(t['entry'] - t['sl'])
        if risk_dist == 0: risk_dist = 5.0 # fallback
        
        r_multiple = t['pnl'] / risk_dist
        total_r += r_multiple
        
    # Compounding or Simple? Simple for Prop Firms usually (no compounding lots often)
    # Simple: Profit = Total_R * (StartBal * Risk)
    total_profit_usd = total_r * (START_BALANCE * (RISK_PCT/100))
    final_balance = START_BALANCE + total_profit_usd
    roi_pct = (total_profit_usd / START_BALANCE) * 100
    
    if total_trades_count > 0:
        global_wr = total_wins / total_trades_count * 100
        print(f"Total Trades: {total_trades_count}")
        print(f"Win Rate:     {global_wr:.1f}%")
        print(f"Total PnL:    {total_pnl_all*10:.1f} Pips")
        print("-" * 30)
        print(f"Estimated ROI: {roi_pct:.1f}% (Based on {RISK_PCT}% Risk)")
        print(f"Total R:       {total_r:.1f} R")
    else:
        print("No trades executed.")

if __name__ == "__main__":
    run_backtest()
