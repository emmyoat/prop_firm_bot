import time
import MetaTrader5 as mt5
from datetime import datetime
from src.utils.logger import setup_logger
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.data.news_loader import NewsLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.risk.risk_manager import RiskManager
from src.execution.execution_engine import ExecutionEngine
from src.utils.notifications import TelegramNotifier 
from src.utils.stats import StatsReporter
import src.models as models


import argparse

def main():
    # Handle Command-line Arguments
    parser = argparse.ArgumentParser(description="Prop-Firm Trading Bot")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config file")
    parser.add_argument("--env", type=str, default=".env", help="Path to .env file")
    args = parser.parse_args()

    # Setup Logging
    config = load_config(args.config)
    log_file = f"bot_{config['system']['magic_number']}.log"
    logger = setup_logger(log_level=config['system']['log_level'], log_file=log_file)
    logger.info(f"Starting Prop-Firm Bot with config: {args.config} and env: {args.env}")


    # Load Credentials
    creds = load_credentials(args.env)
    
    # Initialize Modules
    data_loader = MT5DataLoader(config)
    if not data_loader.connect(creds):
        logger.critical("Failed to connect to MT5. Exiting.")
        return

    # Initialize News Loader
    news_loader = NewsLoader()
    news_loader.update_news() # Initial fetch

    # Initialize New Strategy
    strategy = LiquidityWickStrategy(config)
    
    # Initialize Notifier
    notifier = TelegramNotifier(
        token=creds.get('telegram_token'),
        chat_id=creds.get('telegram_chat_id'),
        enabled=True
    )
    if creds.get('telegram_token'):
        logger.info("Telegram Notifications Enabled")
        notifier.send_message("🤖 **PropBot Started**\nLiquidity Wick Mode Active")

    risk_manager = RiskManager(config)
    execution_engine = ExecutionEngine(
        magic_number=config['system']['magic_number'],
        notifier=notifier
    )
    
    symbols = config['system']['symbol_list']
    
    # Initialize Daily Equity
    account_info = mt5.account_info()
    if account_info:
        risk_manager.initialize_state(account_info, config['system']['magic_number'])
        logger.info(f"Initial Equity: {account_info.equity}")

    # Initialize Stats Reporter
    stats_reporter = StatsReporter(config['system']['magic_number'])
    last_report_time = 0 # Unix timestamp

    logger.info("Bot Initialized. Entering Main Loop...")

    try:
        while True:
            # Refresh account info for risk monitoring
            acc_info = mt5.account_info()
            if not acc_info:
                logger.error("Failed to fetch account info. Retrying...")
                time.sleep(5)
                continue

            # Update High-Water Mark and check for breaches
            risk_manager.update_high_water_mark(acc_info.equity)
            is_breached, reason = risk_manager.check_emergency_exit(acc_info)
            
            if is_breached:
                logger.critical(f"RISK BREACH: {reason}. Closing all positions!")
                execution_engine.close_all_positions()
                if config['telegram']['enabled']:
                    notifier.send_message(f"🚨 **RISK BREACH DETECTED**\n{reason}\nAll trades closed. Bot paused.")
                # Pause bot to prevent further trading
                time.sleep(600) # Sleep 10 mins
                continue

            # Performance Reporting (Every 60 mins)
            if time.time() - last_report_time >= 3600:
                daily_stats = stats_reporter.get_stats(days=1)
                total_stats = stats_reporter.get_stats(days=0)
                
                if daily_stats and total_stats:
                    report_msg = stats_reporter.format_report(daily_stats, total_stats)
                    logger.info("Generating Performance Report...")
                    # Sanitize for console/log (remove emojis)
                    clean_msg = report_msg.encode('ascii', 'ignore').decode('ascii')
                    logger.info("\n" + clean_msg)
                    if config['telegram']['enabled']:
                        notifier.send_message(report_msg)
                
                last_report_time = time.time()
            
            # Friday Exit Check
            now = datetime.now()
            exit_hour = config['risk'].get('friday_exit_hour', 21)
            if now.weekday() == 4 and now.hour >= exit_hour:
                 # Check ONLY our bot's positions
                 bot_positions = [p for p in mt5.positions_get() if p.magic == config['system']['magic_number']]
                 if len(bot_positions) > 0:
                     logger.warning("FRIDAY EXIT TRIGGERED: Closing bot positions.")
                     execution_engine.close_all_positions()
                     time.sleep(60)
            
            # News Auto Filter
            if news_loader.is_blocked():
                logger.warning(f"NEWS PAUSE: High Impact USD News Active.")
                time.sleep(60) 
                continue # Skip this loop iteration

            # Main Event Loop
            active_pairs = config['strategy'].get('active_pairs', [{"low": "H4", "high": "D1", "label": "SWING"}])

            for symbol in symbols:
                try:
                    # --- MANAGEMENT LOGIC (Trailing & Scaling) ---
                    positions = mt5.positions_get(symbol=symbol)
                    symbol_info = mt5.symbol_info(symbol) # Move up for shared use

                    if positions and symbol_info:
                        # Trailing Stop & Breakeven
                        trail_start_pips = config['risk'].get('trailing_stop_activation_pips', 45)
                        be_start_pips = config['risk'].get('breakeven_activation_pips', 20)
                        trail_step_pips = config['risk'].get('trailing_step_pips', 5)
                        min_duration = config['risk'].get('min_trade_duration_seconds', 240)
                        point = symbol_info.point
                        
                        for pos in positions:
                            if pos.type == mt5.ORDER_TYPE_BUY:
                                current_bid = mt5.symbol_info_tick(symbol).bid
                                # Dynamic Pip Factor: 10 for Forex/Gold, 1 for Indices
                                # Indices use 1 point = 1 pip logic
                                is_index = any(idx in symbol.upper() for idx in ["US30", "NAS100", "US100", "US500", "GER30", "DE30", "UK100", "JPN225"])
                                pip_factor = 1 if is_index else 10
                                profit_pips = (current_bid - pos.price_open) / point / pip_factor
                                
                                # Duration Check
                                duration_seconds = time.time() - pos.time
                                if duration_seconds < min_duration:
                                    continue 

                                # Breakeven Check
                                if profit_pips >= be_start_pips:
                                    be_level = pos.price_open + (be_start_pips * 0.1 * pip_factor * point) # Secure initial risk
                                    if pos.sl < pos.price_open:
                                        execution_engine.modify_order(pos.ticket, sl=be_level, tp=pos.tp)
                                        logger.info(f"Moved BUY {pos.ticket} to Breakeven")

                                # Trailing Check
                                if profit_pips >= trail_start_pips:
                                    # Trail: SL = Current - 15 pips
                                    trail_dist_points = 150 * point if pip_factor == 10 else 15 * point
                                    new_sl = current_bid - trail_dist_points
                                    if new_sl > pos.sl:
                                        execution_engine.modify_order(pos.ticket, sl=new_sl, tp=pos.tp)
                            
                            elif pos.type == mt5.ORDER_TYPE_SELL:
                                current_ask = mt5.symbol_info_tick(symbol).ask
                                is_index = any(idx in symbol.upper() for idx in ["US30", "NAS100", "US100", "US500", "GER30", "DE30", "UK100", "JPN225"])
                                pip_factor = 1 if is_index else 10
                                profit_pips = (pos.price_open - current_ask) / point / pip_factor
                                
                                # Duration Check
                                duration_seconds = time.time() - pos.time
                                if duration_seconds < min_duration:
                                    continue

                                # Breakeven Check
                                if profit_pips >= be_start_pips:
                                    be_level = pos.price_open - (be_start_pips * 0.1 * pip_factor * point) 
                                    if pos.sl == 0.0 or pos.sl > pos.price_open:
                                        execution_engine.modify_order(pos.ticket, sl=be_level, tp=pos.tp)
                                        logger.info(f"Moved SELL {pos.ticket} to Breakeven")

                                # Trailing Check
                                if profit_pips >= trail_start_pips:
                                    trail_dist_points = 150 * point if pip_factor == 10 else 15 * point
                                    new_sl = current_ask + trail_dist_points
                                    if pos.sl == 0 or new_sl < pos.sl:
                                        execution_engine.modify_order(pos.ticket, sl=new_sl, tp=pos.tp)



                    # --- STRATEGY LOGIC LOOP ---
                    for pair in active_pairs:
                        tf_low = pair['low']
                        tf_high = pair['high']
                        label = pair['label']
                        
                        # A. Fetch Data
                        df_low = data_loader.fetch_data(symbol, tf_low, n_bars=100)
                        df_high = data_loader.fetch_data(symbol, tf_high, n_bars=100)
                        
                        if df_low is None or df_high is None:
                            continue
                            
                        data_dict = {"LowTF": df_low, "HighTF": df_high}

                        # B. Generate Signal
                        signal = strategy.generate_signal(data_dict, symbol)
                        
                        if signal.signal_type != models.SignalType.NEUTRAL:
                            signal.comment = f"{label} {signal.comment}"
                            logger.info(f"Signal Generated [{label}]: {signal}")
                            
                            # C. Execution - Initial Entry
                            acc_info = mt5.account_info()
                            symbol_info = mt5.symbol_info(symbol)
                            
                            if not acc_info:
                                continue

                            # Risk Calculation
                            sl_dist = abs(signal.price - signal.sl_price)
                            # Ensure we don't have a zero SL
                            if sl_dist == 0: sl_dist = 100 * symbol_info.point
                            
                            base_lot = risk_manager.calculate_lot_size(
                                acc_info.equity, 
                                sl_dist, 
                                symbol_info.trade_tick_value, 
                                symbol_info.trade_tick_size
                            )
                            
                            if base_lot > 0:
                                tick_info = mt5.symbol_info_tick(symbol)
                                if not tick_info:
                                    logger.warning(f"Could not fetch tick for {symbol}")
                                    continue
                                
                                spread_points = (tick_info.ask - tick_info.bid) / symbol_info.point
                                if not risk_manager.check_trade_allowed(acc_info, symbol_info, spread_points):
                                    logger.warning("Trade blocked by Risk Manager rules.")
                                    continue

                                order_type = mt5.ORDER_TYPE_BUY if signal.signal_type == models.SignalType.BUY else mt5.ORDER_TYPE_SELL
                                price = tick_info.ask if order_type == mt5.ORDER_TYPE_BUY else tick_info.bid
                                
                                # ROI Check
                                try:
                                    margin = mt5.order_calc_margin(order_type, symbol, base_lot, price)
                                    if signal.tp_price > 0:
                                        potential_profit = mt5.order_calc_profit(order_type, symbol, base_lot, price, signal.tp_price)
                                        if margin and potential_profit:
                                            roi_ratio = potential_profit / margin
                                            if roi_ratio < 0.30:
                                                logger.warning(f"Trade Skipped [{label}]: Low ROI {roi_ratio*100:.1f}%")
                                                continue 
                                except Exception as e:
                                    logger.error(f"Error calculating ROI: {e}")
                                    continue

                                if not config['system']['dry_run']:
                                    if signal.is_limit_order:
                                        limit_type = mt5.ORDER_TYPE_BUY_LIMIT if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT
                                        execution_engine.place_limit_order(
                                            symbol, base_lot, limit_type, price=signal.price, sl=signal.sl_price, tp=signal.tp_price
                                        )
                                        logger.info(f"Placed {label} LIMIT Order")
                                    else:
                                        execution_engine.place_market_order(
                                            symbol, base_lot, order_type, sl=signal.sl_price, tp=signal.tp_price
                                        )
                                        logger.info(f"Placed {label} MARKET Order")
                                else:
                                    type_str = "LIMIT" if signal.is_limit_order else "MARKET"
                                    logger.info(f"[DRY RUN] Would Place {label} {type_str} {base_lot} Lot at {signal.price}")
                            else:
                                logger.warning(f"[{label}] Risk too high or invalid SL distance")

                except Exception as e:
                    import traceback
                    logger.error(f"Error processing {symbol}: {e}")
                    logger.error(traceback.format_exc())

            # --- DASHBOARD EXPORT ---
            try:
                import json
                
                # Snapshot Data
                acc = mt5.account_info()
                daily_stats = stats_reporter.get_stats(days=1)
                dd_metrics = risk_manager.get_drawdown_metrics(acc.equity if acc else 0.0)
                
                # Active Trades List
                open_trades = []
                positions = mt5.positions_get()
                if positions:
                    for p in positions:
                        open_trades.append({
                            "ticket": p.ticket,
                            "symbol": p.symbol,
                            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                            "lots": p.volume,
                            "open_price": p.price_open,
                            "current_price": p.price_current,
                            "profit": p.profit,
                            "sl": p.sl,
                            "tp": p.tp
                        })

                dashboard_data = {
                    "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "balance": acc.balance if acc else 0.0,
                    "equity": acc.equity if acc else 0.0,
                    "daily_pnl": daily_stats.get('profit', 0.0) if daily_stats else 0.0,
                    "daily_trades": daily_stats.get('trades', 0) if daily_stats else 0,
                    "win_rate": daily_stats.get('win_rate', 0.0) if daily_stats else 0.0,
                    "daily_dd": dd_metrics['daily_dd_pct'],
                    "overall_dd": dd_metrics['overall_dd_pct'],
                    "high_water_mark": dd_metrics['hwm'],
                    "open_positions": open_trades
                }
                
                dashboard_filename = f"dashboard_data_{config['system']['magic_number']}.json"
                with open(dashboard_filename, "w") as f:
                    json.dump(dashboard_data, f, indent=4)
                    
            except Exception as e:
                logger.error(f"Dashboard Export Failed: {e}")

            time.sleep(10) # Simple polling delay

    except KeyboardInterrupt:
        logger.info("Bot stopping...")
        data_loader.shutdown()

if __name__ == "__main__":
    main()
