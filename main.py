import time
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from src.utils.logger import setup_logger
from src.utils.config_loader import load_config, load_credentials
from src.data.mt5_loader import MT5DataLoader
from src.data.news_loader import NewsLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.risk.risk_manager import RiskManager
from src.execution.execution_engine import ExecutionEngine
from src.utils.notifications import TelegramNotifier 
from src.utils.stats import StatsReporter
from src.utils.journal import TradeJournal
import src.models as models
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score
import requests
import os


import argparse

def main():
    # Handle Command-line Arguments
    parser = argparse.ArgumentParser(description="Prop-Firm Trading Bot")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config file")
    parser.add_argument("--env", type=str, default=".env", help="Path to .env file")
    args = parser.parse_args()

    # Setup Logging
    config = load_config(args.config)
    log_file = f"bot_{config['system']['magic_number']}_new.log"
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
    # Prioritize Config file values, fallback to Environment variables
    tg_token = config['telegram'].get('token') or creds.get('telegram_token')
    tg_chat_id = config['telegram'].get('chat_id') or creds.get('telegram_chat_id')

    notifier = TelegramNotifier(
        token=tg_token,
        chat_id=tg_chat_id,
        enabled=config['telegram'].get('enabled', True)
    )
    if tg_token and tg_chat_id:
        logger.info(f"Telegram Notifications Enabled (ID: {tg_chat_id})")
        notifier.send_message("🤖 **PropBot Started**")
    else:
        logger.warning("Telegram token or chat_id missing. Notifications disabled.")

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
    last_report_time = time.time() # Start timer from NOW (skip immediate report)

    # Initialize Journal
    journal = TradeJournal()
    active_tickets = set()
    logged_tickets = set()  # Prevent duplicate journal entries

    logger.info("Bot Initialized. Entering Main Loop...")
    paused = False

    try:
        consecutive_failures = 0
        while True:
            # Add a small delay to prevent saturating MT5 API
            time.sleep(1)

            # 1. MT5 Connection & Account Check (MUST BE FIRST)
            acc_info = mt5.account_info()
            if not acc_info:
                consecutive_failures += 1
                logger.error(f"Failed to fetch account info (Attempt {consecutive_failures}). Retrying...")
                
                if consecutive_failures >= 3:
                    logger.warning("Multiple failures detected. Attempting to reconnect MT5...")
                    if data_loader.connect(creds):
                        consecutive_failures = 0 # Reset on success
                    else:
                        logger.error("Reconnection failed. Waiting longer...")
                        time.sleep(30)
                else:
                    time.sleep(5)
                continue
            
            consecutive_failures = 0 # Reset on success

            # 2. Process Telegram Commands
            if config['telegram']['enabled']:
                commands = notifier.get_updates()
                for cmd in commands:
                    # ... [existing logic omitted for clarity but preserved by tool]
                    if cmd == "/status":
                        acc = mt5.account_info()
                        if acc:
                            curr_drawdown = (risk_manager.high_water_mark - acc.equity) / risk_manager.high_water_mark * 100
                            status_msg = (
                                f"🤖 **Bot Status**\n"
                                f"Mode: {'🔴 PAUSED' if paused else '🟢 RUNNING'}\n"
                                f"Equity: {acc.currency}{acc.equity:,.2f}\n"
                                f"HWM: {acc.currency}{risk_manager.high_water_mark:,.2f}\n"
                                f"Current Drawdown: {curr_drawdown:.2f}%"
                            )
                            notifier.send_message(status_msg)
                    
                    elif cmd == "/stats":
                        daily_stats = stats_reporter.get_stats(days=1)
                        total_stats = stats_reporter.get_stats(days=0)
                        if daily_stats and total_stats:
                            notifier.send_message(stats_reporter.format_report(daily_stats, total_stats))
                    
                    elif cmd == "/pause":
                        paused = True
                        notifier.send_message("⏸️ **Bot Paused**. No new entries will be taken.")
                        logger.warning("Bot Paused via Telegram.")
                    
                    elif cmd == "/resume":
                        paused = False
                        notifier.send_message("▶️ **Bot Resumed**. Strategy scanning active.")
                        logger.info("Bot Resumed via Telegram.")
                    
                    elif cmd == "/closeall":
                        notifier.send_message("⚠️ **Closing all positions...**")
                        execution_engine.close_all_positions()
                        logger.warning("All positions closed via Telegram.")
                    
                    elif cmd == "/journal":
                        if os.path.exists("trades.csv"):
                            with open("trades.csv", "r") as f:
                                lines = f.readlines()
                                if len(lines) > 1:
                                    last_5 = lines[-5:]
                                    journal_msg = "📖 **Recent Trades**\n" + "".join(last_5)
                                    notifier.send_message(journal_msg)
                                else:
                                    notifier.send_message("📖 Journal is empty.")
                        else:
                            notifier.send_message("📖 No journal found yet. Start trading!")

                    elif cmd in ["/start", "/help"]:
                        help_msg = (
                            "👋 **PropBot Command Menu**\n"
                            "/status - Current health & equity\n"
                            "/stats - Today's performance\n"
                            "/journal - View last 5 trades\n"
                            "/pause - Halt all new entries\n"
                            "/resume - Start strategy scan\n"
                            "/closeall - Emergency exit all trades"
                        )
                        notifier.send_message(help_msg)
                    
                    else:
                        notifier.send_message(f"❓ Unknown command: {cmd}\nType /help for the menu.")
            
            # 3. Monitor Closed Trades for Journaling (Only if connected)
            current_positions = mt5.positions_get(magic=config['system']['magic_number'])
            
            # CRITICAL GUARD: Only update tickets if positions_get didn't return None (which means error)
            # If it returns empty tuple (), that's fine (no trades).
            if current_positions is not None:
                current_tickets = {p.ticket for p in current_positions}
                
                closed_tickets = active_tickets - current_tickets
                for ticket in closed_tickets:
                    if ticket in logged_tickets:
                        continue  # Already logged, skip duplicate
                    # Fetch history for this ticket to log
                    from_time = datetime.now() - timedelta(days=1)
                    to_time = datetime.now() + timedelta(hours=1)
                    history_deals = mt5.history_deals_get(from_time, to_time, position=ticket)
                    
                    if history_deals:
                        entry_deal = next((d for d in history_deals if d.entry in [mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT]), None)
                        exit_deal = next((d for d in history_deals if d.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY]), None)
                        
                        if entry_deal and exit_deal:
                            journal.log_trade(exit_deal, entry_deal)
                            logged_tickets.add(ticket)

                            # --- NOTIFICATION LOGIC ---
                            if config['telegram']['enabled']:
                                reason = exit_deal.reason
                                profit = exit_deal.profit + exit_deal.swap + exit_deal.commission
                                symbol = exit_deal.symbol
                                close_price = exit_deal.price
                                trade_type = "BUY" if entry_deal.type == 0 else "SELL"
                                
                                icon = "⚪"
                                title = "Trade Closed"
                                
                                # Detect Reason
                                if reason == mt5.DEAL_REASON_TP:
                                    icon = "✅"
                                    title = "**TAKE PROFIT HIT** 🎯"
                                elif reason == mt5.DEAL_REASON_SL:
                                    if profit >= 0:
                                        icon = "🛡️"
                                        title = "**BREAKEVEN HIT**"
                                    else:
                                        icon = "❌"
                                        title = "**STOP LOSS HIT**"
                                elif reason == mt5.DEAL_REASON_CLIENT:
                                    icon = "👋" 
                                    title = "Manual Close"
                                
                                # Format Message
                                acc = mt5.account_info()
                                currency = acc.currency if acc else "$"
                                msg = f"{icon} {title}\nSymbol: {symbol}\nProfit: {currency}{profit:.2f}"
                                notifier.send_message(msg)
                
                active_tickets = current_tickets
            else:
                logger.warning("Journaling: Connection flickered. Skipping closure check to prevent ghost logs.")

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

            # Check Profit Target
            target_hit, succ_msg = risk_manager.check_profit_target(acc_info.equity)
            if target_hit:
                logger.info(f"PROFIT TARGET REACHED: {succ_msg}. Closing all positions and pausing.")
                execution_engine.close_all_positions()
                if config['telegram']['enabled']:
                    notifier.send_message(f"💰 **DAILY TARGET HIT**\n{succ_msg}\nTrades closed. See you tomorrow! 🥂")
                time.sleep(900) # Sleep 15 mins to avoid spam
                continue

            # Performance Reporting (Configurable Interval)
            reports_enabled = config['telegram'].get('enable_reports', True)
            report_interval = config['telegram'].get('report_interval_hours', 4) * 3600
            
            if reports_enabled and (time.time() - last_report_time >= report_interval):
                try:
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
                except Exception as e:
                    logger.error(f"Failed to generate report: {e}")
                
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
            
            # --- PENDING ORDER EXPIRY ---
            expiry_hours = config['risk'].get('pending_order_expiry_hours', 4)
            pending_orders = mt5.orders_get()
            if pending_orders:
                for order in pending_orders:
                    if order.magic != config['system']['magic_number']:
                        continue
                    age_seconds = time.time() - order.time_setup
                    if age_seconds > expiry_hours * 3600:
                        cancel_request = {
                            "action": mt5.TRADE_ACTION_REMOVE,
                            "order": order.ticket,
                        }
                        result = mt5.order_send(cancel_request)
                        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                            logger.info(f"Cancelled expired pending order {order.ticket} (age: {age_seconds/3600:.1f}h)")
                        else:
                            logger.warning(f"Failed to cancel order {order.ticket}: {result}")

            # News Auto Filter
            if news_loader.is_blocked():
                logger.warning(f"NEWS PAUSE: High Impact USD News Active.")
                time.sleep(60) 
                continue # Skip this loop iteration
            # --- SESSION FILTER ---
            # Only take NEW trades during configured active sessions (UTC hours)
            # Trade management (trailing, BE) still runs 24/7
            active_sessions = config['system'].get('active_sessions', [])
            in_active_session = True  # Default: trade if no sessions configured
            if active_sessions:
                from datetime import timezone
                utc_hour = datetime.now(timezone.utc).hour
                in_active_session = any(
                    s['start_utc'] <= utc_hour < s['end_utc'] for s in active_sessions
                )
                if not in_active_session:
                    session_names = ", ".join(s['name'] for s in active_sessions)
                    logger.debug(f"SESSION FILTER: UTC hour {utc_hour} outside active sessions ({session_names}). Skipping new entries.")

            # Main Event Loop
            active_pairs = config['strategy'].get('active_pairs', [{"low": "H4", "high": "D1", "label": "SWING"}])

            for symbol in symbols:
                try:
                    # --- MANAGEMENT LOGIC (Trailing & Scaling) ---
                    positions = mt5.positions_get(symbol=symbol)
                    symbol_info = mt5.symbol_info(symbol) # Move up for shared use

                    if positions and symbol_info:
                        if paused:
                            continue # Skip entry logic if paused

                        # Check if we already have a position for this symbol
                        bot_positions = [p for p in positions if p.magic == config['system']['magic_number']]
                        if len(bot_positions) > 0:
                            # We already have a trade. Manage it (Trailing/BE) but DO NOT look for new entries.
                            # Run management logic below, then 'continue' to skip Strategy Loop
                            pass
                        else:
                            # No bot positions, so we mark to allow entry later?
                            # Actually, we need to run management code for existing trades (even if we don't own them? No, only ours)
                            pass

                    # --- MANAGEMENT LOGIC (Trailing & Scaling) ---
                    # Logic needs to iterate positions regardless.
                    # Redesign: Iterating positions is fine.
                    # But we must BLOCK the Strategy Entry loop if we have a position.
                    
                    has_open_position = False
                    if positions:
                        for pos in positions:
                            if pos.magic == config['system']['magic_number']:
                                has_open_position = True
                                # ... existing management logic checks ...
                        trail_start_pips = config['risk'].get('trailing_stop_activation_pips', 45)
                        be_start_pips = config['risk'].get('breakeven_activation_pips', 20)
                        trail_dist_pips = config['risk'].get('trailing_stop_distance_pips', 25)
                        trail_step_pips = config['risk'].get('trailing_update_step_pips', 5)
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
                                    # Trail: SL = Current - Trailing Distance
                                    trail_dist_points = (trail_dist_pips * 10 * point) if pip_factor == 10 else (trail_dist_pips * point)
                                    step_points = (trail_step_pips * 10 * point) if pip_factor == 10 else (trail_step_pips * point)
                                    new_sl = current_bid - trail_dist_points
                                    
                                    # Anti-Spam Step Logic: Only modify if new_sl is > current SL + step
                                    if new_sl > (pos.sl + step_points):
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
                                    trail_dist_points = (trail_dist_pips * 10 * point) if pip_factor == 10 else (trail_dist_pips * point)
                                    step_points = (trail_step_pips * 10 * point) if pip_factor == 10 else (trail_step_pips * point)
                                    new_sl = current_ask + trail_dist_points
                                    
                                    # Anti-Spam Step Logic: Only modify if new_sl is < current SL - step
                                    # Or if SL is 0 (first set)
                                    if pos.sl == 0 or new_sl < (pos.sl - step_points):
                                        execution_engine.modify_order(pos.ticket, sl=new_sl, tp=pos.tp)



                    # --- STRATEGY LOGIC LOOP ---
                    if has_open_position:
                         # logger.debug(f"Skipping {symbol} - Open Position exists.")
                         continue

                    if not in_active_session:
                         continue  # Outside active trading sessions, skip new entries

                    for pair in active_pairs:
                        tf_low = pair['low']
                        tf_high = pair['high']
                        label = pair['label']

                        # Per-symbol pair filter (e.g. GBPUSD only trades DAY)
                        sym_overrides = config['strategy'].get('symbol_overrides', {}).get(symbol, {})
                        allowed = sym_overrides.get('allowed_pairs')
                        if allowed and label not in allowed:
                            continue
                        
                        # A. Fetch Data
                        df_low = data_loader.fetch_data(symbol, tf_low, n_bars=100)
                        df_high = data_loader.fetch_data(symbol, tf_high, n_bars=100)
                        
                        if df_low is None or df_high is None:
                            continue
                            
                        data_dict = {"LowTF": df_low, "HighTF": df_high}

                        # B. Generate Signal
                        signal = strategy.generate_signal(data_dict, symbol, label=label)
                        
                        if signal.signal_type != models.SignalType.NEUTRAL:
                            signal.comment = f"{label} {signal.comment}"
                            logger.info(f"Signal Generated [{label}]: {signal}")
                            
                            # SMC Confluence Filter
                            smc_enabled = config['strategy'].get('smc_filter_enabled', False)
                            smc_min_score = config['strategy'].get('smc_min_confluence_score', 20)
                            
                            if smc_enabled and smc_min_score > 0:
                                try:
                                    fvgs = detect_fvg_zones(df_low)
                                    obs = detect_order_blocks(df_low)
                                    
                                    confluence_score, smc_zone = calculate_confluence_score(
                                        current_price=df_low.iloc[-1]['close'],
                                        signal_type=signal.signal_type.name,
                                        order_blocks=obs,
                                        fvg_zones=fvgs,
                                        entry_price=signal.price,
                                        stop_loss=signal.sl_price
                                    )
                                    
                                    if confluence_score < smc_min_score:
                                        logger.info(f"SMC Filter: Skipping {symbol} - Score {confluence_score} < {smc_min_score}")
                                        continue
                                    
                                    # Log SMC zone details
                                    zone_info = ""
                                    if smc_zone:
                                        zone_info = f" OB={smc_zone.has_ob} FVG={smc_zone.has_fvg}"
                                    logger.info(f"SMC Filter: PASSED - Score {confluence_score}{zone_info}")
                                    signal.comment = f"{signal.comment} [SMC:{confluence_score}]"
                                except Exception as smc_err:
                                    logger.warning(f"SMC Filter error: {smc_err}")
                            
                            # C. Execution - Initial Entry
                            acc_info = mt5.account_info()
                            symbol_info = mt5.symbol_info(symbol)
                            
                            if not acc_info:
                                continue

                            # Get Actual Loss Per 1 Lot (The most reliable way across brokers)
                            # We simulate a 1-lot order and calc profit at the SL price
                            sl_dist = abs(signal.price - signal.sl_price)
                            if sl_dist == 0: sl_dist = symbol_info.point * 100
                            
                            order_type_calc = mt5.ORDER_TYPE_BUY if signal.signal_type == models.SignalType.BUY else mt5.ORDER_TYPE_SELL
                            # Use signal.price as the entry for calculation
                            price_entry = signal.price
                            loss_per_lot = mt5.order_calc_profit(order_type_calc, symbol, 1.0, price_entry, signal.sl_price)
                            
                            # Note: Profit will be negative for a loss, so we take absolute
                            if loss_per_lot is not None:
                                loss_per_lot = abs(loss_per_lot)
                            else:
                                # Fallback if API fails
                                loss_per_lot = (sl_dist / symbol_info.trade_tick_size) * symbol_info.trade_tick_value

                            base_lot = risk_manager.calculate_lot_size(
                                acc_info.equity, 
                                sl_dist, 
                                symbol_info.trade_tick_value, 
                                symbol_info.trade_tick_size,
                                loss_per_lot_override=loss_per_lot,
                                symbol=symbol
                            )
                            
                            if base_lot > 0:
                                # Check for EXISTING PENDING ORDERS to prevent spam (Duplicate Stop Orders)
                                existing_orders = mt5.orders_get(symbol=symbol)
                                if existing_orders:
                                    # Filter for our bot's orders
                                    bot_orders = [o for o in existing_orders if o.magic == config['system']['magic_number']]
                                    if len(bot_orders) > 0:
                                        # logic: if we already have a pending order, don't place another.
                                        # Or better: if price is different? For now, simple block.
                                        logger.info(f"Skipping {symbol} - Pending Order already exists (Count: {len(bot_orders)})")
                                        continue

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
                                


                                if not config['system']['dry_run']:
                                    if signal.is_limit_order:
                                        limit_type = mt5.ORDER_TYPE_BUY_LIMIT if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT
                                        execution_engine.place_limit_order(
                                            symbol, base_lot, limit_type, price=signal.price, stop_loss=signal.sl_price, take_profit=signal.tp_price,
                                            comment=signal.comment
                                        )
                                        logger.info(f"Placed {label} LIMIT Order")
                                    elif signal.is_stop_order:
                                        stop_type = mt5.ORDER_TYPE_BUY_STOP if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL_STOP
                                        execution_engine.place_stop_order(
                                            symbol, base_lot, stop_type, price=signal.price, stop_loss=signal.sl_price, take_profit=signal.tp_price,
                                            comment=signal.comment
                                        )
                                        logger.info(f"Placed {label} STOP Order")
                                    else:
                                        execution_engine.place_market_order(
                                            symbol, base_lot, order_type, stop_loss=signal.sl_price, take_profit=signal.tp_price,
                                            comment=signal.comment
                                        )
                                        logger.info(f"Placed {label} MARKET Order")
                                else:
                                    type_str = "LIMIT" if signal.is_limit_order else ("STOP" if signal.is_stop_order else "MARKET")
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
                daily_stats = stats_reporter.get_stats(since_midnight=True)
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

                # Calculate unrealized PnL from open positions (for real-time accuracy)
                unrealized_pnl = sum(p.profit for p in positions) if positions else 0.0

                dashboard_data = {
                    "bot_id": str(config['system']['magic_number']),
                    "account_name": acc.name if acc else "Unknown",
                    "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "currency": acc.currency if acc else "USD", # Add Currency Support
                    "balance": acc.balance if acc else 0.0,
                    "equity": acc.equity if acc else 0.0,
                    "daily_pnl": (daily_stats.get('profit', 0.0) if daily_stats else 0.0) + unrealized_pnl,
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
                
                # --- CLOUD SYNC ---
                dashboard_url = os.environ.get('DASHBOARD_URL')
                api_key = os.environ.get('DASHBOARD_API_KEY')
                
                if dashboard_url:
                    try:
                        resp = requests.post(
                            f"{dashboard_url.rstrip('/')}/api/index",
                            json=dashboard_data,
                            headers={"X-API-Key": api_key if api_key else "propbot-secret"},
                            timeout=5
                        )
                        if resp.status_code == 200:
                            logger.debug("Cloud Dashboard Updated")
                        else:
                            logger.warning(f"Cloud update failed: {resp.status_code}")
                    except Exception as cloud_err:
                        logger.warning(f"Cloud sync error: {cloud_err}")
                    
            except Exception as e:
                logger.error(f"Dashboard Export Failed: {e}")

            time.sleep(10) # Simple polling delay

    except KeyboardInterrupt:
        logger.info("Bot stopping...")
        data_loader.shutdown()

if __name__ == "__main__":
    print("### NEW CODE CHECK ###")
    main()
