"""
Prop Firm Signal Bot — main.py
==============================
  • Market data   → TwelveData API
  • Signal output → Telegram alerts + console log
  • Risk engine   → Virtual paper account (configured in config.yaml)
"""

import time
import argparse
import os
import logging
from datetime import datetime, timezone

from src.utils.logger import setup_logger
from src.utils.config_loader import load_config, load_credentials
from src.utils.state_store import StateStore
from src.utils.health import HealthMonitor
from src.data.twelvedata_loader import TwelveDataLoader
from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.risk.risk_manager import RiskManager
from src.utils.notifications import TelegramNotifier
from src.utils.stats import StatsReporter
from src.utils.journal import TradeJournal
import src.models as models
from src.strategies.smc_detector import detect_fvg_zones, detect_order_blocks, calculate_confluence_score


# ── Tick-value estimates for lot sizing without MT5 ───────────────────────────
TICK_VALUE_MAP = {
    "XAUUSD": 100.0,
    "GBPUSD": 10.0,
    "EURUSD": 10.0,
    "USDJPY": 9.1,
    "GBPJPY": 9.1,
    "US30":   1.0,
    "NAS100": 2.0,
    "US100":  2.0,
    "US500":  5.0,
    "GER30":  1.0,
    "GER40":  1.0,
}

TICK_SIZE_MAP = {
    "XAUUSD": 0.01,
    "GBPUSD": 0.0001,
    "EURUSD": 0.0001,
    "USDJPY": 0.001,
    "GBPJPY": 0.001,
    "US30":   1.0,
    "NAS100": 1.0,
    "US100":  1.0,
    "US500":  0.1,
    "GER30":  1.0,
    "GER40":  1.0,
}


def get_active_session(config: dict) -> str:
    sessions = config["system"].get("active_sessions", [])
    if not sessions:
        return "24/7"
    utc_hour = datetime.now(timezone.utc).hour
    for s in sessions:
        if s["start_utc"] <= utc_hour < s["end_utc"]:
            return s["name"]
    return "Off-Hours"


def in_active_session(config: dict) -> bool:
    sessions = config["system"].get("active_sessions", [])
    if not sessions:
        return True
    utc_hour = datetime.now(timezone.utc).hour
    return any(s["start_utc"] <= utc_hour < s["end_utc"] for s in sessions)


def is_friday_close(config: dict) -> bool:
    now = datetime.now(timezone.utc)
    exit_hour = config["risk"].get("friday_exit_hour", 21)
    return now.weekday() == 4 and now.hour >= exit_hour


def main():
    # ── CLI args ──────────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Prop Firm Signal Bot")
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument("--env",    type=str, default=".env")
    args = parser.parse_args()

    # ── Config & logging ──────────────────────────────────────────────────────
    config = load_config(args.config)
    log_file = f"bot_{config['system']['magic_number']}.log"
    logger = setup_logger(log_level=config["system"]["log_level"], log_file=log_file)
    logger.info("=" * 60)
    logger.info("  Prop Firm Signal Bot  |  Signal-Only  |  TwelveData")
    logger.info("=" * 60)

    creds = load_credentials(args.env)

    # ── Durable state and health ───────────────────────────────────────────────
    runtime_cfg = config.get("runtime", {})
    state_store = StateStore(runtime_cfg.get("state_db_path", "runtime_state.db"))
    if not state_store.integrity_check():
        raise RuntimeError("Runtime state database failed integrity check")
    health = HealthMonitor(state_store, config)
    state_store.set_runtime_value("last_clean_shutdown", False)
    state_store.set_runtime_value("process_started_at", datetime.now(timezone.utc).isoformat())

    # ── TwelveData loader ─────────────────────────────────────────────────────
    api_key = (
        config.get("data_source", {}).get("api_key")
        or creds.get("twelvedata_api_key")
        or os.environ.get("TWELVEDATA_API_KEY", "")
    )

    if not api_key:
        logger.warning("TWELVEDATA_API_KEY not set. Get a free key at https://twelvedata.com/register")
        logger.warning("Set it in .env as TWELVEDATA_API_KEY=your_key")

    data_loader = TwelveDataLoader(config, api_key=api_key)

    if api_key:
        if data_loader.is_connected():
            logger.info("TwelveData API: Connected")
        else:
            logger.warning("TwelveData API: Connection check failed — will retry on first fetch.")

    # ── Telegram ──────────────────────────────────────────────────────────────
    tg_token   = config["telegram"].get("token")   or creds.get("telegram_token")
    tg_chat_id = config["telegram"].get("chat_id") or creds.get("telegram_chat_id")

    notifier = TelegramNotifier(
        token=tg_token,
        chat_id=tg_chat_id,
        enabled=config["telegram"].get("enabled", True),
        config=config,
        state_store=state_store,
    )

    if tg_token and tg_chat_id:
        logger.info(f"Telegram alerts enabled (Chat ID: {tg_chat_id})")
        notifier.send_message("⚡ *PropBot Signal Engine Started*")
    else:
        logger.warning("Telegram token/chat_id missing — notifications disabled.")

    # ── Strategy ──────────────────────────────────────────────────────────────
    strategy = LiquidityWickStrategy(config)
    logger.info("Strategy loaded: LiquidityWickStrategy")

    # ── Risk manager ──────────────────────────────────────────────────────────
    risk_manager = RiskManager(config, state_store=state_store)
    risk_manager.health_monitor = health
    risk_manager.initialize_state()
    logger.info(f"Virtual account: ${risk_manager.virtual_balance:,.2f}")

    # ── News & journal ────────────────────────────────────────────────────────
    stats_reporter = StatsReporter(config["system"]["magic_number"])
    last_report_time = time.time()

    symbols      = config["system"]["symbol_list"]
    active_pairs = config["strategy"].get("active_pairs", [{"low": "H4", "high": "D1", "label": "SWING"}])

    logger.info("Bot initialised. Entering signal scan loop...")
    logger.info(f"Watching: {', '.join(symbols)}")

    try:
        while True:
            time.sleep(config.get("runtime", {}).get("scan_interval_seconds", 5))
            health.heartbeat()
            risk_manager.ensure_daily_rollover()
            health.evaluate()
            for transition in health.drain_transitions():
                _send_health_transition(notifier, transition, logger)
            # Keep fetching while degraded or unhealthy so the data source can
            # recover. Failed fetches below suppress signal processing naturally.
            # ── Friday close check ────────────────────────────────────────────
            if is_friday_close(config):
                logger.warning("FRIDAY EXIT: Halting new signals for the weekend.")
                time.sleep(300)
                continue

            # ── Telegram command handling ─────────────────────────────────────
            if config["telegram"]["enabled"] and tg_token and tg_chat_id:
                commands = notifier.get_updates()
                for cmd in commands:
                    _handle_telegram_command(cmd, notifier, risk_manager, stats_reporter, logger)

            # ── Drawdown / profit target checks ───────────────────────────────
            breached, breach_reason = risk_manager.check_emergency_exit()
            if breached:
                logger.error(f"RISK BREACH: {breach_reason} — Pausing signals.")
                notifier.send_message(f"RISK BREACH\n{breach_reason}\nSignals paused.")
                time.sleep(600)
                continue

            target_hit, target_msg = risk_manager.check_profit_target()
            if target_hit:
                logger.info(f"DAILY TARGET HIT: {target_msg}")
                notifier.send_message(f"DAILY TARGET HIT\n{target_msg}\nSignals paused until tomorrow.")
                time.sleep(900)
                continue

            # ── Active Trade Lifecycle Tracker (Breakeven & Trailing Alerts) ──
            try:
                _evaluate_active_trades(state_store, data_loader, notifier, config, logger)
            except Exception as trade_err:
                logger.error(f"Error evaluating active trades: {trade_err}")

            # ── Session filter ────────────────────────────────────────────────
            session_name = get_active_session(config)
            if not in_active_session(config):
                logger.debug(f"Outside active sessions ({session_name}). Waiting...")
                time.sleep(30)
                continue

            # ── Periodic performance report ───────────────────────────────────
            reports_enabled = config["telegram"].get("enable_reports", False)
            report_interval = config["telegram"].get("report_interval_hours", 4) * 3600
            if reports_enabled and (time.time() - last_report_time >= report_interval):
                _send_performance_report(notifier, stats_reporter, logger)
                last_report_time = time.time()

            logger.debug(f"Scanning symbols | Session: {session_name}")

            # ── Main signal scan ──────────────────────────────────────────────
            for symbol in symbols:
                try:
                    for pair in active_pairs:
                        tf_low  = pair["low"]
                        tf_high = pair["high"]
                        label   = pair["label"]

                        # Per-symbol pair filter
                        sym_overrides = config["strategy"].get("symbol_overrides", {}).get(symbol, {})
                        allowed = sym_overrides.get("allowed_pairs")
                        if allowed and label not in allowed:
                            continue

                        # Suppress duplicate signals if a trade is already open on this timeframe label
                        active_for_symbol = state_store.get_active_trades(symbol)
                        if any(t.get("label") == label for t in active_for_symbol):
                            logger.debug(f"[{label}] {symbol}: Active trade already open — skipping duplicate scan.")
                            continue

                        # A. Fetch candle data
                        df_low  = data_loader.fetch_data(symbol, tf_low,  n_bars=100)
                        df_high = data_loader.fetch_data(symbol, tf_high, n_bars=100)

                        if df_low is None or df_high is None:
                            health.record_failure(
                                "market_data", f"no data for {symbol} {tf_low}/{tf_high}"
                            )
                            logger.warning(f"No data for {symbol} {tf_low}/{tf_high} — skipping.")
                            continue
                        health.record_success("market_data", data_loader.get_metrics())
                        market_data = health.get_component("market_data")
                        if market_data and market_data["status"] == "unhealthy":
                            logger.warning(
                                "Market data remains unhealthy after fetch; suppressing signal."
                            )
                            continue

                        # B. Generate signal on closed candle — pass D1 as MacroTF gate
                        df_macro = None
                        if tf_high != "D1":
                            df_macro = data_loader.fetch_data(symbol, "D1", n_bars=100)

                        # Evaluate on completed/closed candles to prevent repainting
                        df_low_eval = df_low.iloc[:-1].copy() if len(df_low) > 2 else df_low
                        df_high_eval = df_high.iloc[:-1].copy() if len(df_high) > 2 else df_high
                        df_macro_eval = df_macro.iloc[:-1].copy() if (df_macro is not None and len(df_macro) > 2) else df_macro

                        signal = strategy.generate_signal(
                            {"LowTF": df_low_eval, "HighTF": df_high_eval, "MacroTF": df_macro_eval},
                            symbol, label=label
                        )

                        if signal.signal_type == models.SignalType.NEUTRAL:
                            logger.debug(f"[{label}] {symbol}: No setup — {signal.comment}")
                            continue

                        # C. Durable dedup claim — locked to closed candle timestamp
                        candle_time_str = str(df_low_eval.iloc[-1]["time"])
                        dedup_key = f"{symbol}_{label}"
                        if not state_store.claim_signal(dedup_key, candle_time_str):
                            continue

                        signal.comment = f"{label} {signal.comment}"
                        logger.info(f"Signal [{label}]: {symbol} {signal.signal_type.name} @ {signal.price:.5f}")

                        # D. SMC confluence filter
                        if config["strategy"].get("smc_filter_enabled", False):
                            smc_min = config["strategy"].get("smc_min_confluence_score", 20)
                            try:
                                fvgs  = detect_fvg_zones(df_low)
                                obs   = detect_order_blocks(df_low)
                                score, _ = calculate_confluence_score(
                                    current_price=float(df_low.iloc[-1]["close"]),
                                    signal_type=signal.signal_type.name,
                                    order_blocks=obs,
                                    fvg_zones=fvgs,
                                    entry_price=signal.price,
                                    stop_loss=signal.sl_price,
                                )
                                if score < smc_min:
                                    state_store.release_signal(dedup_key, candle_time_str)
                                    logger.debug(f"SMC Filter: {symbol} skipped — score {score} < {smc_min}")
                                    continue
                                signal.comment = f"{signal.comment} [SMC:{score}]"
                            except Exception as smc_err:
                                logger.warning(f"SMC filter error: {smc_err}")

                        # E. Risk check
                        allowed_signal, block_reason = risk_manager.check_signal_allowed(symbol)
                        if not allowed_signal:
                            state_store.release_signal(dedup_key, candle_time_str)
                            logger.warning(f"Signal blocked: {block_reason}")
                            continue

                        # F. Lot size recommendation
                        sl_dist  = abs(signal.price - signal.sl_price)
                        lot_size = risk_manager.calculate_lot_size(
                            stop_loss_dist=sl_dist,
                            tick_value=TICK_VALUE_MAP.get(symbol, 10.0),
                            tick_size=TICK_SIZE_MAP.get(symbol, 0.0001),
                            symbol=symbol,
                        )

                        risk   = abs(signal.price - signal.sl_price)
                        reward = abs(signal.tp_price - signal.price)
                        rr     = reward / risk if risk > 0 else 0.0

                        # G. Telegram alert
                        dd_metrics = risk_manager.get_drawdown_metrics()
                        delivered = notifier.send_signal_alert(
                            symbol=symbol,
                            direction=signal.signal_type.name,
                            entry=signal.price,
                            sl=signal.sl_price,
                            tp=signal.tp_price,
                            rr=rr,
                            lot_size=lot_size,
                            timeframe=tf_low,
                            label=label,
                            comment=signal.comment,
                            dd_metrics=dd_metrics,
                        )
                        if not delivered:
                            state_store.release_signal(dedup_key, candle_time_str)
                            health.record_failure("telegram", "signal delivery failed")
                            continue
                        health.record_success("telegram", notifier.get_metrics())

                        risk_manager.record_signal_sent()
                        logger.info(f"Signal sent: {symbol} {signal.signal_type.name} | R:R {rr:.2f} | Lot {lot_size}")

                        # H. Register active trade in durable tracker for Breakeven & Trailing alerts
                        trade_id = f"{symbol}_{label}_{int(time.time())}"
                        state_store.save_active_trade({
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "label": label,
                            "direction": signal.signal_type.name,
                            "entry": signal.price,
                            "sl": signal.sl_price,
                            "tp": signal.tp_price,
                            "initial_sl": signal.sl_price,
                            "current_sl": signal.sl_price,
                            "is_stop_order": signal.is_stop_order,
                            "triggered": 0 if signal.is_stop_order else 1,
                            "be_alerted": 0,
                            "last_trail_sl": 0.0,
                            "highest_price": signal.price,
                            "lowest_price": signal.price,
                            "lot_size": lot_size,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        })

                except Exception as e:
                    import traceback
                    health.record_failure("scan_loop", f"{symbol}: {e}")
                    logger.error(f"Error scanning {symbol}: {e}")
                    logger.debug(traceback.format_exc())

    except KeyboardInterrupt:
        logger.info("Bot stopping...")
        notifier.send_message("*PropBot Signal Engine stopped.*")
    finally:
        state_store.set_runtime_value("last_clean_shutdown", True)
        state_store.set_runtime_value("process_stopped_at", datetime.now(timezone.utc).isoformat())
        data_loader.shutdown()
        notifier.shutdown()
        state_store.close()
        logger.info("Bot stopped.")


def _handle_telegram_command(cmd: str, notifier: TelegramNotifier, risk_manager: RiskManager, stats_reporter, logger=None):
    if logger is None:
        logger = logging.getLogger("PropBot")
    summary = risk_manager.get_summary()

    if cmd in ["/start", "/help"]:
        notifier.send_message(
            "⚡ *PropBot Engine*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "/status  — Account health & drawdown status\n"
            "/health  — Runtime dependency health\n"
            "/stats   — Today's trading performance\n"
            "/help    — This menu"
        )
    elif cmd == "/health":
        health_monitor = getattr(risk_manager, "health_monitor", None)
        if health_monitor is None:
            notifier.send_message("Health monitor is not available.")
        else:
            health = health_monitor.summary()
            lines = [f"*Runtime Health:* `{health['status']}`"]
            for component in health["components"]:
                reason = f" — {component['reason']}" if component["reason"] else ""
                lines.append(
                    f"`{component['component']}`: *{component['status']}*"
                    f" ({component['consecutive_failures']} failures){reason}"
                )
            notifier.send_message("\n".join(lines))
    elif cmd == "/status":
        notifier.send_message(
            f"📊 *Bot & Account Status*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Balance:   `${summary['virtual_balance']:,.2f}`\n"
            f"Equity:    `${summary['virtual_equity']:,.2f}`\n"
            f"P&L:       `{'+' if summary['paper_pnl']>=0 else ''}{summary['paper_pnl']:.2f}`"
        )
        logger.info("Telegram /status requested")
    elif cmd == "/stats":
        notifier.send_message(
            f"*Today's Signals*\n"
            f"Signals: `{summary['signals_today']}`\n"
            f"Wins:    `{summary['wins_today']}`\n"
            f"Losses:  `{summary['losses_today']}`\n"
            f"Paper PnL: `{summary['daily_pnl']:+.2f}`"
        )
        logger.info("Telegram /stats requested")
    else:
        notifier.send_message(f"Unknown command: `{cmd}`\nType /help for the menu.")


def _send_health_transition(notifier: TelegramNotifier, transition: dict, logger=None):
    if logger is None:
        logger = logging.getLogger("PropBot")
    component = transition.get("component", "unknown")
    status    = transition.get("status", "unknown")
    reason    = transition.get("reason") or "status changed"
    # Log only — health transitions are operational noise, not user-facing alerts
    logger.warning("Health transition: %s=%s (%s)", component, status, reason)


def _send_performance_report(notifier: TelegramNotifier, stats_reporter, logger=None):
    if logger is None:
        logger = logging.getLogger("PropBot")
    try:
        daily = stats_reporter.get_stats()
        if daily:
            notifier.send_message(stats_reporter.format_report(daily, daily))
            logger.info("Performance report sent.")
    except Exception as e:
        logger.error(f"Report failed: {e}")


def _evaluate_active_trades(state_store: StateStore, data_loader: TwelveDataLoader, notifier: TelegramNotifier, config: dict, logger=None):
    if logger is None:
        logger = logging.getLogger("PropBot")

    trades = state_store.get_active_trades()
    if not trades:
        return

    now_utc = datetime.now(timezone.utc)
    risk_cfg = config.get("risk", {})
    be_enabled = risk_cfg.get("breakeven_enabled", True)
    be_pips = risk_cfg.get("breakeven_activation_pips", 100)
    trail_enabled = risk_cfg.get("trailing_stop_enabled", True)
    trail_activation_pips = risk_cfg.get("trailing_stop_activation_pips", 100)
    trail_dist_pips = risk_cfg.get("trailing_stop_distance_pips", 40)
    trail_step_pips = risk_cfg.get("trailing_step_pips", 40)
    pending_expiry_hours = risk_cfg.get("pending_order_expiry_hours", 4)

    for trade in trades:
        symbol = trade["symbol"]
        label = trade["label"]
        trade_id = trade["trade_id"]
        pip_unit = 0.1 if "XAU" in symbol else (0.01 if "JPY" in symbol else 0.0001)

        # Fetch latest candle data (uses unified cache)
        tf = "M15" if label == "SCALP" else ("H1" if label == "DAY" else "H4")
        df = data_loader.fetch_data(symbol, tf, n_bars=100)
        if df is None or df.empty:
            continue

        latest_bar = df.iloc[-1]
        current_high = float(latest_bar["high"])
        current_low = float(latest_bar["low"])
        current_close = float(latest_bar["close"])
        is_buy = trade["direction"] == "BUY"

        # Check pending stop-order trigger
        if trade.get("is_stop_order") and not trade.get("triggered"):
            # Expiry check
            try:
                created_dt = datetime.fromisoformat(trade["created_at"])
                if (now_utc - created_dt).total_seconds() > pending_expiry_hours * 3600:
                    state_store.remove_active_trade(trade_id)
                    logger.info(f"Pending order expired: {symbol} [{label}] {trade['direction']} @ {trade['entry']:.2f}")
                    continue
            except Exception:
                pass

            triggered = (is_buy and current_high >= trade["entry"]) or (not is_buy and current_low <= trade["entry"])
            if triggered:
                trade["triggered"] = 1
                trade["highest_price"] = max(trade.get("highest_price", trade["entry"]), current_close)
                trade["lowest_price"] = min(trade.get("lowest_price", trade["entry"]), current_close)
                state_store.save_active_trade(trade)
                logger.info(f"Pending order triggered: {symbol} [{label}] {trade['direction']} @ {trade['entry']:.2f}")
            else:
                continue

        # Trade is actively open — track high/low excursion
        trade["highest_price"] = max(trade.get("highest_price", trade["entry"]), current_close)
        trade["lowest_price"] = min(trade.get("lowest_price", trade["entry"]), current_close)

        profit_dist = (trade["highest_price"] - trade["entry"]) if is_buy else (trade["entry"] - trade["lowest_price"])
        profit_pips = profit_dist / pip_unit

        # ── 1. Breakeven Alert (Strictly Once at +100 pips) ───────────────────
        if be_enabled and not trade.get("be_alerted") and profit_pips >= be_pips:
            trade["be_alerted"] = 1
            trade["current_sl"] = trade["entry"]
            state_store.save_active_trade(trade)
            if notifier.enabled and notifier.token and notifier.chat_id:
                notifier.send_breakeven_alert(
                    symbol=symbol,
                    label=label,
                    direction=trade["direction"],
                    entry=trade["entry"],
                    current_price=current_close,
                    profit_pips=profit_pips,
                )
            logger.info(f"Breakeven alert sent: {symbol} [{label}] {trade['direction']} (+{profit_pips:.0f} pips)")

        # ── 2. Trade Exit (TP / SL / Breakeven Hit on Live Price) ────────────
        if is_buy:
            if current_close <= trade["current_sl"]:
                exit_type = "BE_HIT" if trade.get("be_alerted") else "SL_HIT"
                exit_price = trade["current_sl"]
                pnl_pips = (exit_price - trade["entry"]) / pip_unit
                if notifier.enabled and notifier.token and notifier.chat_id:
                    notifier.send_trade_closed_alert(
                        symbol=symbol,
                        label=label,
                        direction=trade["direction"],
                        exit_type=exit_type,
                        entry=trade["entry"],
                        exit_price=exit_price,
                        pnl_pips=pnl_pips,
                    )
                state_store.remove_active_trade(trade_id)
                logger.info(f"Trade closed: {symbol} [{label}] {exit_type} @ {exit_price:.2f} ({pnl_pips:+.0f} pips)")
            elif trade["tp"] > 0 and current_close >= trade["tp"]:
                exit_price = trade["tp"]
                pnl_pips = (exit_price - trade["entry"]) / pip_unit
                if notifier.enabled and notifier.token and notifier.chat_id:
                    notifier.send_trade_closed_alert(
                        symbol=symbol,
                        label=label,
                        direction=trade["direction"],
                        exit_type="TP_HIT",
                        entry=trade["entry"],
                        exit_price=exit_price,
                        pnl_pips=pnl_pips,
                    )
                state_store.remove_active_trade(trade_id)
                logger.info(f"Trade closed: {symbol} [{label}] TP_HIT @ {exit_price:.2f} ({pnl_pips:+.0f} pips)")
        else:
            if current_close >= trade["current_sl"]:
                exit_type = "BE_HIT" if trade.get("be_alerted") else "SL_HIT"
                exit_price = trade["current_sl"]
                pnl_pips = (trade["entry"] - exit_price) / pip_unit
                if notifier.enabled and notifier.token and notifier.chat_id:
                    notifier.send_trade_closed_alert(
                        symbol=symbol,
                        label=label,
                        direction=trade["direction"],
                        exit_type=exit_type,
                        entry=trade["entry"],
                        exit_price=exit_price,
                        pnl_pips=pnl_pips,
                    )
                state_store.remove_active_trade(trade_id)
                logger.info(f"Trade closed: {symbol} [{label}] {exit_type} @ {exit_price:.2f} ({pnl_pips:+.0f} pips)")
            elif trade["tp"] > 0 and current_close <= trade["tp"]:
                exit_price = trade["tp"]
                pnl_pips = (trade["entry"] - exit_price) / pip_unit
                if notifier.enabled and notifier.token and notifier.chat_id:
                    notifier.send_trade_closed_alert(
                        symbol=symbol,
                        label=label,
                        direction=trade["direction"],
                        exit_type="TP_HIT",
                        entry=trade["entry"],
                        exit_price=exit_price,
                        pnl_pips=pnl_pips,
                    )
                state_store.remove_active_trade(trade_id)
                logger.info(f"Trade closed: {symbol} [{label}] TP_HIT @ {exit_price:.2f} ({pnl_pips:+.0f} pips)")


if __name__ == "__main__":
    main()
