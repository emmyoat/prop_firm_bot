import yfinance as yf
from src.utils.logger import setup_logger
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType
import pandas as pd
import numpy as np
from datetime import datetime
import logging

def calculate_metrics(closed_trades, symbol="ALL", label="ALL"):
    """Calculate comprehensive backtest metrics from a list of closed trades."""
    if not closed_trades:
        return None

    pnls = [t['pnl'] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    net_pnl = sum(pnls)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Max Consecutive Losses
    max_consec_losses = 0
    current_streak = 0
    for p in pnls:
        if p <= 0:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Max Consecutive Wins
    max_consec_wins = 0
    current_streak = 0
    for p in pnls:
        if p > 0:
            current_streak += 1
            max_consec_wins = max(max_consec_wins, current_streak)
        else:
            current_streak = 0

    # Max Drawdown (equity curve based)
    equity_curve = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity_curve)
    drawdown = peak - equity_curve
    max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0

    # Sharpe Ratio (annualized, assuming ~252 trading days)
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252)
    else:
        sharpe = 0

    # Session Distribution
    session_dist = {}
    for t in closed_trades:
        session = t.get('session', 'Unknown')
        if session not in session_dist:
            session_dist[session] = {'trades': 0, 'wins': 0, 'pnl': 0}
        session_dist[session]['trades'] += 1
        session_dist[session]['pnl'] += t['pnl']
        if t['pnl'] > 0:
            session_dist[session]['wins'] += 1

    return {
        'symbol': symbol,
        'label': label,
        'total_trades': total_trades,
        'win_count': win_count,
        'loss_count': loss_count,
        'win_rate': win_rate,
        'net_pnl': net_pnl,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'expectancy': expectancy,
        'max_consec_wins': max_consec_wins,
        'max_consec_losses': max_consec_losses,
        'max_drawdown': max_drawdown,
        'sharpe_ratio': sharpe,
        'session_dist': session_dist,
    }


def print_metrics(metrics):
    """Print a formatted metrics summary."""
    if not metrics:
        print("  No trades to report.")
        return

    m = metrics
    print(f"\n{'='*55}")
    print(f"  {m['symbol']} ({m['label']}) — BACKTEST RESULTS")
    print(f"{'='*55}")
    print(f"  Total Trades:          {m['total_trades']}")
    print(f"  Wins / Losses:         {m['win_count']} / {m['loss_count']}")
    print(f"  Win Rate:              {m['win_rate']:.1f}%")
    print(f"  Net PnL (pips):        {m['net_pnl']:.2f}")
    print(f"  Gross Profit:          {m['gross_profit']:.2f}")
    print(f"  Gross Loss:            {m['gross_loss']:.2f}")
    pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] != float('inf') else "inf (no losses)"
    print(f"  Profit Factor:         {pf_str}")
    print(f"  Avg Win / Avg Loss:    {m['avg_win']:.4f} / {m['avg_loss']:.4f}")
    print(f"  Expectancy:            {m['expectancy']:.4f}")
    print(f"  Max Consec Wins:       {m['max_consec_wins']}")
    print(f"  Max Consec Losses:     {m['max_consec_losses']}")
    print(f"  Max Drawdown (pips):   {m['max_drawdown']:.2f}")
    print(f"  Sharpe Ratio:          {m['sharpe_ratio']:.2f}")

    if m['session_dist']:
        print(f"\n  {'Session':<18} {'Trades':>7} {'WR':>7} {'PnL':>10}")
        print(f"  {'-'*44}")
        for session, data in sorted(m['session_dist'].items()):
            wr = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
            print(f"  {session:<18} {data['trades']:>7} {wr:>6.1f}% {data['pnl']:>+10.2f}")
    print(f"{'='*55}")


def get_session(hour):
    """Determine trading session from an hour value."""
    if 7 <= hour < 12:
        return "London"
    elif 12 <= hour < 17:
        return "London/NY"
    elif 17 <= hour < 22:
        return "New York"
    else:
        return "Asia/Sydney"


def run_backtest(friday_exit_enabled=True, backtest_days=30):
    config = load_config("config.yaml")
    creds = load_credentials(".env")

    # Enable logging for strategy debugging
    setup_logger("PropBot.Strategy", logging.INFO)
    setup_logger("PropBot.Risk", logging.INFO)
    
    loader = MT5DataLoader(config)
    if not loader.connect(creds):
        pass
    
    symbols = config['system'].get('symbol_list', ["XAUUSD"])
    active_pairs = config['strategy'].get('active_pairs', [])
    strategy = LiquidityWickStrategy(config)
    
    # Yahoo Finance ticker mapping (fallback when MT5 data unavailable)
    ticker_map = {
        "XAUUSD": "GC=F",      # Gold Futures
        "US30": "YM=F",        # Dow Jones Futures
        "NAS100": "NQ=F",      # Nasdaq Futures
        "USTECH100": "NQ=F",
        "GBPUSD": "GBPUSD=X",  # GBP/USD Forex
        "EURUSD": "EURUSD=X",  # EUR/USD Forex
    }

    mode_str = "FRIDAY EXIT" if friday_exit_enabled else "WEEKEND HOLDING"
    print(f"\n--- {mode_str} BACKTEST ({backtest_days} days) ---")

    fetch_bars = 10000
    all_closed_trades = []  # Aggregated across all symbols
    symbol_results = []     # Per-symbol metrics

    for symbol in symbols:
        for tf_data in active_pairs:
            # Handle config dict vs legacy string
            if isinstance(tf_data, dict):
                label = tf_data.get('label', 'UNKNOWN')
                tf_entry = tf_data.get('low', "H1")
                tf_trend = tf_data.get('high', "H4")
            else:
                label = tf_data
                tf_entry = "M30" if label == "SCALP" else ("H1" if label == "DAY" else "H4")
                tf_trend = "H4"  if label == "SCALP" else ("H4" if label == "DAY" else "D1")

            print(f"\nProcessing {symbol} {label} ({tf_entry}/{tf_trend})...")

            # Per-symbol pair filter (e.g. GBPUSD only trades DAY)
            sym_overrides = config['strategy'].get('symbol_overrides', {}).get(symbol, {})
            allowed = sym_overrides.get('allowed_pairs')
            if allowed and label not in allowed:
                print(f"  Skipping {symbol} {label} (not in allowed_pairs: {allowed})")
                continue

            # --- FETCH DATA ---
            df_entry = loader.fetch_data(symbol, tf_entry, fetch_bars)
            df_trend = loader.fetch_data(symbol, tf_trend, fetch_bars)
            
            # Fallback to yfinance if MT5 data missing
            if df_entry is None or df_trend is None:
                yf_ticker = ticker_map.get(symbol)
                if not yf_ticker:
                    print(f"  Skipping {symbol} - No yfinance mapping")
                    continue
                    
                try:
                    print(f"  Downloading from Yahoo Finance ({yf_ticker})...")
                    tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "1h", "D1": "1d"} 
                    df_entry = yf.download(yf_ticker, period="6mo", interval=tf_map.get(tf_entry, "1h"), progress=False)
                    df_trend = yf.download(yf_ticker, period="1y", interval=tf_map.get(tf_trend, "1d"), progress=False)
                    if df_entry.empty: continue
                    df_entry.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_entry.columns]
                    df_trend.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_trend.columns]
                    df_entry.index = df_entry.index.tz_localize(None)
                    df_trend.index = df_trend.index.tz_localize(None)
                    df_entry['time'] = df_entry.index
                    df_trend['time'] = df_trend.index
                except Exception as e:
                    print(f"  Yahoo Finance error: {e}")
                    continue

            active_trades = []
            pending_orders = []
            closed_trades = []
            pip_unit = 0.1 if "XAU" in symbol else 1.0
            
            if df_entry is not None and not df_entry.empty:
                if 'time' in df_entry.columns and not isinstance(df_entry.index, pd.DatetimeIndex):
                    df_entry.set_index('time', inplace=True, drop=False)
                df_entry.index = pd.to_datetime(df_entry.index)
            if df_trend is not None and not df_trend.empty:
                if 'time' in df_trend.columns and not isinstance(df_trend.index, pd.DatetimeIndex):
                    df_trend.set_index('time', inplace=True, drop=False)
                df_trend.index = pd.to_datetime(df_trend.index)

            trading_start = pd.Timestamp.now() - pd.Timedelta(days=backtest_days)
            print(f"  Backtesting from {trading_start.strftime('%Y-%m-%d')} to {pd.Timestamp.now().strftime('%Y-%m-%d')}")

            for i in range(100, len(df_entry)):
                bar = df_entry.iloc[i]
                curr_time = bar.name
                if not isinstance(curr_time, pd.Timestamp):
                    try: curr_time = pd.to_datetime(curr_time)
                    except: continue
                
                if curr_time < trading_start:
                    continue
                
                if friday_exit_enabled and curr_time.weekday() == 4 and curr_time.hour >= 21:
                    pending_orders = []
                    for t in active_trades[:]:
                        t['pnl'] = (bar['close'] - t['entry']) if t['type'] == 'BUY' else (t['entry'] - bar['close'])
                        t['session'] = get_session(t.get('entry_hour', 12))
                        closed_trades.append(t)
                        active_trades.remove(t)
                    continue

                # --- 1. Pending Order Management ---
                for order in pending_orders[:]:
                    if (curr_time - order['placed_time']).total_seconds() > (4 * 3600):
                        pending_orders.remove(order)
                        continue

                    triggered = False
                    if order['type'] == 'BUY_STOP':
                        if bar['high'] >= order['entry']:
                            triggered = True
                    elif order['type'] == 'SELL_STOP':
                        if bar['low'] <= order['entry']:
                            triggered = True

                    if triggered:
                        active_trades.append({
                            'type': 'BUY' if 'BUY' in order['type'] else 'SELL',
                            'entry': order['entry'],
                            'sl': order['sl'],
                            'tp': order['tp'],
                            'entry_hour': curr_time.hour,
                        })
                        pending_orders.remove(order)

                # Management
                trailing_activation = config['risk'].get('trailing_stop_activation_pips', 50) * pip_unit
                trailing_step = config['risk'].get('trailing_step_pips', 25) * pip_unit
                
                for t in active_trades[:]:
                    exit_price = None
                    if t['type'] == 'BUY':
                        if bar['low'] <= t['sl']: exit_price = t['sl']
                        elif t['tp'] > 0 and bar['high'] >= t['tp']: exit_price = t['tp']
                        
                        if not exit_price:
                            profit_dist = bar['high'] - t['entry']
                            if profit_dist >= trailing_activation:
                                 new_sl = t['entry'] + (profit_dist - trailing_step)
                                 if new_sl > t['sl']: t['sl'] = new_sl

                    else: # SELL
                        if bar['high'] >= t['sl']: exit_price = t['sl']
                        elif t['tp'] > 0 and bar['low'] <= t['tp']: exit_price = t['tp']
                        
                        if not exit_price:
                            profit_dist = t['entry'] - bar['low']
                            if profit_dist >= trailing_activation:
                                new_sl = t['entry'] - (profit_dist - trailing_step)
                                if new_sl < t['sl']: t['sl'] = new_sl
                    
                    if exit_price:
                        t['pnl'] = (exit_price - t['entry']) if t['type'] == 'BUY' else (t['entry'] - exit_price)
                        t['session'] = get_session(t.get('entry_hour', 12))
                        closed_trades.append(t)
                        active_trades.remove(t)

                if len(active_trades) == 0 and len(pending_orders) == 0:
                    data_map = {"LowTF": df_entry.iloc[:i+1], "HighTF": df_trend[df_trend.index <= curr_time]}
                    signal = strategy.generate_signal(data_map, symbol)
                    
                    if signal.signal_type != SignalType.NEUTRAL:
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
                                'tp': signal.tp_price,
                                'entry_hour': curr_time.hour,
                            })

            # Calculate per-symbol metrics
            metrics = calculate_metrics(closed_trades, symbol, label)
            if metrics:
                print_metrics(metrics)
                symbol_results.append(metrics)
                all_closed_trades.extend(closed_trades)
            else:
                print(f"  -> {symbol} ({label}): No trades found in this period.")
            
    # Combined Summary
    if all_closed_trades:
        combined = calculate_metrics(all_closed_trades, "COMBINED", "ALL PAIRS")
        print_metrics(combined)
        return combined['win_rate']
    
    print("\nNo trades generated across any symbol.")
    return 0


if __name__ == "__main__":
    import sys
    days = 30
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print(f"Invalid days argument '{sys.argv[1]}', defaulting to 30.")
            
    print(f"Running backtest for last {days} days...")
    wr = run_backtest(friday_exit_enabled=True, backtest_days=days)
    
    print(f"\nFinal Combined Win Rate: {wr:.2f}%")
