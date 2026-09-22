import pandas as pd
import numpy as np
import logging
from src.strategies.base_strategy import Strategy
from src.models import Signal, SignalType

logger = logging.getLogger("PropBot.Strategy")

class LiquidityWickStrategy(Strategy):
    def __init__(self, config: dict):
        super().__init__("LiquidityWick", config)
        self.swing_lookback = 10  # Lookback for identifying swing points
        self.wick_threshold_ratio = config['strategy'].get('wick_threshold_ratio', 0.35)
        self.lookback = config['strategy'].get('liquidity_lookback', 20)
        self.rsi_buy_threshold = config['strategy'].get('rsi_buy_threshold', 60)
        self.rsi_sell_threshold = config['strategy'].get('rsi_sell_threshold', 40)
        self.sma_period = config['strategy'].get('sma_period', 50)
        self.risk_reward_ratio = config['strategy'].get('risk_reward_ratio', 3.0)
        self.require_trend_alignment = False   # Per-symbol: require both TFs to agree
        self.allow_entry_trend_only = False     # Per-symbol: relax alignment — follow entry TF if D1 macro agrees
        self.rsi_confirmation = False           # Per-symbol: RSI momentum filter
        # Per-symbol: allow reversal sweeps that fade a conflicting D1 macro trend.
        # Liquidity sweeps are REVERSAL setups, so they are evaluated in both
        # directions independent of the lagging SMA trend lock (see docs below).
        self.sweep_allow_counter_macro = config['strategy'].get('sweep_allow_counter_macro', False)

    def generate_signal(self, data: dict, symbol: str, label: str = "") -> Signal:
        """
        Analyzes generic "LowTF" (Entry) and "HighTF" (Trend) data to generate a signal.
        Expects data to be a dictionary: {"LowTF": df_low, "HighTF": df_high}
        Fallback: Checks "H4" and "D1" if generic keys missing.
        """
        # Apply per-symbol overrides (restore originals at end)
        overrides = self.config['strategy'].get('symbol_overrides', {}).get(symbol, {})
        saved = {}
        if overrides:
            for key, val in overrides.items():
                attr = key  # Config key maps directly to attribute name
                if hasattr(self, attr):
                    saved[attr] = getattr(self, attr)
                    setattr(self, attr, val)

        try:
            return self._generate_signal_inner(data, symbol, label)
        finally:
            # Restore original params
            for attr, val in saved.items():
                setattr(self, attr, val)

    def _generate_signal_inner(self, data: dict, symbol: str, label: str = "") -> Signal:
        df_entry = data.get("LowTF", data.get("H4"))
        df_trend = data.get("HighTF", data.get("D1"))

        if df_entry is None or df_trend is None:
            return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Insufficient Data")

        # 1. Determine Market Structure (Trend TF & Entry TF)
        trend_major = self._get_trend(df_trend)
        trend_entry = self._get_trend(df_entry)
        logger.debug(f"{symbol} TrendTF: {trend_major.name}, EntryTF: {trend_entry.name}")

        # `current_trend` is the CONTINUATION bias (breakouts). It is frozen by the
        # lagging SMA-40 read. Liquidity sweeps below are REVERSAL setups and are
        # evaluated in BOTH directions independently of this lock — see gate 6.
        current_trend = SignalType.NEUTRAL
        if trend_major == SignalType.BUY and trend_entry == SignalType.BUY:
            current_trend = SignalType.BUY
        elif trend_major == SignalType.SELL and trend_entry == SignalType.SELL:
            current_trend = SignalType.SELL
        else:
             if self.require_trend_alignment:
                 if self.allow_entry_trend_only:
                     # Relaxed mode: follow entry TF direction — D1 macro gate below
                     # will still block genuinely counter-trend signals.
                     current_trend = trend_entry
                     logger.debug(
                         f"{symbol} [{label}] Trend misalignment (Major={trend_major.name}, "
                         f"Entry={trend_entry.name}) — using entry TF (allow_entry_trend_only)"
                     )
                 else:
                     return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Trend Misalignment")
             else:
                 current_trend = trend_entry

        # ── 2. D1 Macro Trend Gate ────────────────────────────────────────────
        # Uses EMA-20 on D1. Continuation (breakout) setups must align with it.
        # Reversal (sweep) setups may fade it ONLY when sweep_allow_counter_macro
        # is enabled for the symbol.
        df_macro = data.get("MacroTF")
        macro_trend = SignalType.NEUTRAL
        if df_macro is not None and len(df_macro) >= 20:
            macro_trend = self._get_macro_trend(df_macro)

        # ── 3. ADX Range Filter ───────────────────────────────────────────────
        # Blocks signals in consolidating/ranging markets. Gate text preserved.
        adx_enabled = self.config['strategy'].get('adx_filter_enabled', False)
        if adx_enabled:
            adx_period = self.config['strategy'].get('adx_period', 14)
            adx_threshold_map = self.config['strategy'].get('adx_min_threshold_map', {})
            adx_threshold = adx_threshold_map.get(label, self.config['strategy'].get('adx_min_threshold', 20))
            if len(df_entry) >= 28 and adx_threshold > 0:
                adx = self._calculate_adx(df_entry, adx_period)
                logger.debug(f"{symbol} [{label}] ADX={adx:.1f} (threshold={adx_threshold})")
                if adx < adx_threshold:
                    logger.debug(f"{symbol} [{label}] ADX too low ({adx:.1f} < {adx_threshold}) — ranging market, skipping signal")
                    return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, comment=f"ADX too low ({adx:.1f} < {adx_threshold}) — ranging")

            # Higher Timeframe (HTF) ADX Macro Filter
            htf_adx_enabled = self.config['strategy'].get('htf_adx_filter_enabled', False)
            if htf_adx_enabled and df_trend is not None and len(df_trend) >= 28:
                htf_adx_map = self.config['strategy'].get('htf_adx_min_threshold_map', {})
                htf_adx_threshold = htf_adx_map.get(label, self.config['strategy'].get('htf_adx_min_threshold', 0))
                if htf_adx_threshold > 0:
                    htf_adx = self._calculate_adx(df_trend, adx_period)
                    logger.debug(f"{symbol} [{label}] HTF ADX={htf_adx:.1f} (threshold={htf_adx_threshold})")
                    if htf_adx < htf_adx_threshold:
                        logger.debug(f"{symbol} [{label}] HTF ADX too low ({htf_adx:.1f} < {htf_adx_threshold}) — macro ranging market, skipping signal")
                        return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, comment=f"HTF ADX too low ({htf_adx:.1f} < {htf_adx_threshold}) — macro ranging")

        # ── 4. RSI momentum-confirmation values (optional per-symbol) ──────────
        # RSI is computed here and ENFORCED in the RSI gate below, once the setup
        # (and therefore the trade direction) is known — the check is
        # direction-aware, so it can only run after setup detection.
        rsi_enabled = bool(self.rsi_confirmation)
        rsi_value = float("nan")
        if rsi_enabled and len(df_entry) > 20:
            rsi_value = self._calculate_rsi(df_entry['close'], self.config['strategy'].get('rsi_period', 14))

        # ── 5. Identify Liquidity (Recent Swing Points on Entry TF) ────────────
        window = df_entry.iloc[-self.lookback:-1]
        support_level = window['low'].min()
        resistance_level = window['high'].max()
        last_candle = df_entry.iloc[-1]

        logger.debug(
            f"{symbol} [{label}] Continuation bias={current_trend.name} "
            f"(Major={trend_major.name}, Entry={trend_entry.name}, Macro={macro_trend.name}). "
            f"Sup={support_level:.5f} Res={resistance_level:.5f}"
        )

        # ── 6. Setup Detection ─────────────────────────────────────────────────
        # SWEEP  = reversal: evaluated BOTH ways, independent of the trend lock.
        # BREAKOUT = continuation: must agree with the trend lock.
        signal_type = SignalType.NEUTRAL
        entry_candle = None
        setup_comment = ""

        total_range = last_candle['high'] - last_candle['low']

        # 6a. Bullish reversal sweep — sweep a recent low, close back above it,
        # green, AND fade the continuation bias (a bullish sweep while the bias is
        # already BUY is continuation, handled as a breakout below).
        if (total_range > 0
                and current_trend != SignalType.BUY
                and last_candle['low'] < support_level
                and last_candle['close'] > support_level
                and last_candle['close'] > last_candle['open']):
            lower_wick = min(last_candle['open'], last_candle['close']) - last_candle['low']
            ratio = lower_wick / total_range
            if ratio >= self.wick_threshold_ratio:
                signal_type = SignalType.BUY
                entry_candle = last_candle
                setup_comment = "Liquidity Sweep (bullish reversal)"
            else:
                logger.debug(f"{symbol} [{label}] Low-Test: Ratio {ratio:.2f} < {self.wick_threshold_ratio}")

        # 6b. Bearish reversal sweep — sweep a recent high, close back below it,
        # red, AND fade the continuation bias (this is precisely the bar that used
        # to fire a false SELL at the bottom of a decline).
        elif (total_range > 0
                and current_trend != SignalType.SELL
                and last_candle['high'] > resistance_level
                and last_candle['close'] < resistance_level
                and last_candle['close'] < last_candle['open']):
            upper_wick = last_candle['high'] - max(last_candle['open'], last_candle['close'])
            ratio = upper_wick / total_range
            if ratio >= self.wick_threshold_ratio:
                signal_type = SignalType.SELL
                entry_candle = last_candle
                setup_comment = "Liquidity Sweep (bearish reversal)"
            else:
                logger.debug(f"{symbol} [{label}] High-Test: Ratio {ratio:.2f} < {self.wick_threshold_ratio}")

        # 6c. Breakout continuation — must agree with the frozen trend lock.
        if signal_type == SignalType.NEUTRAL:
            if current_trend == SignalType.BUY:
                if last_candle['close'] > resistance_level and last_candle['close'] > last_candle['open']:
                    body = last_candle['close'] - last_candle['open']
                    if total_range > 0 and (body / total_range) >= 0.50:
                        signal_type = SignalType.BUY
                        entry_candle = last_candle
                        setup_comment = "Liquidity Breakout (continuation)"
                    else:
                        logger.debug(f"{symbol} [{label}] Buy-Breakout: Weak Body")
                else:
                    logger.debug(f"{symbol} [{label}] No Buy Setup (Close {last_candle['close']:.5f} !> Res {resistance_level:.5f})")
            elif current_trend == SignalType.SELL:
                if last_candle['close'] < support_level and last_candle['close'] < last_candle['open']:
                    body = last_candle['open'] - last_candle['close']
                    if total_range > 0 and (body / total_range) >= 0.50:
                        signal_type = SignalType.SELL
                        entry_candle = last_candle
                        setup_comment = "Liquidity Breakout (continuation)"
                    else:
                        logger.debug(f"{symbol} [{label}] Sell-Breakout: Weak Body")
                else:
                    logger.debug(f"{symbol} [{label}] No Sell Setup (Close {last_candle['close']:.5f} !< Supp {support_level:.5f})")
            else:
                return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Structure Neutral")

        if signal_type != SignalType.NEUTRAL:
            is_sweep = "Sweep" in setup_comment

            # ── Allowed setup filter per timeframe label ──────────────────────
            allowed_setups_map = self.config.get('strategy', {}).get('allowed_setups_map', {})
            allowed_setups = allowed_setups_map.get(label, self.config.get('strategy', {}).get('allowed_setups', ['sweep', 'breakout']))
            if is_sweep and 'sweep' not in allowed_setups:
                logger.debug(f"{symbol} [{label}] Reversal sweep suppressed by allowed_setups_map ({allowed_setups})")
                return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, comment=f"Sweep disabled for {label}")
            if not is_sweep and 'breakout' not in allowed_setups:
                logger.debug(f"{symbol} [{label}] Continuation breakout suppressed by allowed_setups_map ({allowed_setups})")
                return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, comment=f"Breakout disabled for {label}")

            # ── Macro gate (applied to the CHOSEN setup) ──────────────────────
            if macro_trend != SignalType.NEUTRAL and macro_trend != signal_type:
                if not (is_sweep and self.sweep_allow_counter_macro):
                    logger.debug(
                        f"{symbol} [{label}] MacroTF gate: D1 EMA-20={macro_trend.name} "
                        f"conflicts with {setup_comment} — blocked"
                    )
                    return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0,
                                  f"D1 macro filter ({macro_trend.name} vs {signal_type.name})")

            # ── RSI gate (applied to the CHOSEN setup) ────────────────────────
            # Protective exhaustion filter: never BUY into overbought momentum or
            # SELL into oversold momentum — that is fading exhausted momentum and
            # was the cause of the near-bottom false-positive SELL breakouts.
            # Applied to the chosen setup so reversal sweeps are guarded too.
            if rsi_enabled and not pd.isna(rsi_value):
                buy_limit = self.rsi_buy_threshold if hasattr(self, 'rsi_buy_threshold') else 70
                sell_limit = self.rsi_sell_threshold if hasattr(self, 'rsi_sell_threshold') else 30
                if signal_type == SignalType.SELL and rsi_value < sell_limit:
                    logger.debug(f"{symbol} [{label}] RSI exhaustion guard: no SELL while oversold (RSI {rsi_value:.0f} < {sell_limit})")
                    return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0,
                                  comment=f"RSI too low for sell ({rsi_value:.0f} < {sell_limit})")
                if signal_type == SignalType.BUY and rsi_value > buy_limit:
                    logger.debug(f"{symbol} [{label}] RSI exhaustion guard: no BUY while overbought (RSI {rsi_value:.0f} > {buy_limit})")
                    return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0,
                                  comment=f"RSI too high for buy ({rsi_value:.0f} > {buy_limit})")

            # Sweeps enter at the wick extreme with a tiny confirmation buffer.
            is_stop_order = True
            last_candle = entry_candle
            price = 0.0

            # --- VOLATILITY-BASED RISK (ATR) ---
            entry_multiplier = self.config['strategy'].get('entry_atr_multiplier', 0.1)
            atr_period = self.config['strategy'].get('atr_period', 14)
            atr_value = self._calculate_atr(df_entry, atr_period)
            
            # SL Buffer (Safety)
            sl_buffers = self.config['strategy'].get('sl_buffer_map', {})
            fallback_buffer = sl_buffers.get(symbol, sl_buffers.get('default', 0.50))
            valid_atr = atr_value if (atr_value and not pd.isna(atr_value)) else fallback_buffer

            # Entry Buffer (Tighter confirmations)
            entry_buffer_price = valid_atr * entry_multiplier if (atr_value and not pd.isna(atr_value)) else (fallback_buffer * 0.2)

            if signal_type == SignalType.BUY:
                # Buy Stop at High of signal candle + Small Entry Buffer
                price = last_candle['high'] + entry_buffer_price
            else:
                # Sell Stop at Low of signal candle - Small Entry Buffer
                price = last_candle['low'] - entry_buffer_price

            # Stop Loss Calculation Mode: 'atr' (distance from entry) or 'candle_extreme' (opposite wick)
            sl_mode = self.config['strategy'].get('sl_mode', 'atr')
            if sl_mode == "atr":
                sl_atr_mult_map = self.config['strategy'].get('sl_atr_multiplier_map', {})
                sl_atr_mult = sl_atr_mult_map.get(label, self.config['strategy'].get('sl_atr_multiplier', 1.5))

                session_name = data.get("session_name", "")
                session_atr_map = self.config['strategy'].get('session_atr_multiplier_map', {})
                if session_name and session_name in session_atr_map:
                    session_factor = session_atr_map[session_name]
                    sl_atr_mult *= session_factor

                sl_dist = valid_atr * sl_atr_mult
                sl_dist = max(sl_dist, fallback_buffer)

                # Hard cap on SL distance in pips if configured
                max_sl_map = self.config['strategy'].get('max_sl_pips_map', {})
                max_pips = max_sl_map.get(label, self.config['strategy'].get('max_sl_pips', None))
                if max_pips:
                    pip_unit = 0.1 if "XAU" in symbol else (0.01 if "JPY" in symbol else 0.0001)
                    sl_dist = min(sl_dist, max_pips * pip_unit)

                if signal_type == SignalType.BUY:
                    stop_loss = price - sl_dist
                else:
                    stop_loss = price + sl_dist

                logger.debug(f"{symbol} [{label}] SL Mode=ATR | Price={price:.5f} | SL={stop_loss:.5f} | Dist={sl_dist:.2f} (ATR={valid_atr:.2f}, Mult={sl_atr_mult:.2f})")
            else:
                # Legacy: opposite side of signal candle + buffer
                atr_multiplier = self.config['strategy'].get('atr_multiplier', 1.5)
                multiplier_map = self.config['strategy'].get('atr_multiplier_map', {})
                atr_multiplier = multiplier_map.get(symbol, atr_multiplier)
                if "XAU" in symbol and atr_multiplier > 0.5:
                    atr_multiplier = 0.5

                session_name = data.get("session_name", "")
                session_atr_map = self.config['strategy'].get('session_atr_multiplier_map', {})
                if session_name and session_name in session_atr_map:
                    atr_multiplier *= session_atr_map[session_name]

                sl_buffer_price = valid_atr * atr_multiplier
                sl_buffer_price = max(sl_buffer_price, fallback_buffer)

                if signal_type == SignalType.BUY:
                    stop_loss = last_candle['low'] - sl_buffer_price
                else:
                    stop_loss = last_candle['high'] + sl_buffer_price

                logger.debug(f"{symbol} [{label}] SL Mode=CandleExtreme | Price={price:.5f} | SL={stop_loss:.5f}")

            # Take Profit — reversals may use a looser R:R floor (config sweep_min_rr)
            min_rr_override = self.config['strategy'].get('sweep_min_rr') if is_sweep else None
            tp_price = self._find_target(df_entry, signal_type, price, stop_loss,
                                         min_rr_override=min_rr_override)

            logger.debug(f"{symbol} [{label}] {setup_comment} | Price={price:.5f} | SL={stop_loss:.5f} | TP={tp_price:.5f}")
            return Signal(symbol, signal_type, price, stop_loss, tp_price,
                          is_stop_order=is_stop_order, comment=setup_comment)

        return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0)

    def _get_trend(self, df: pd.DataFrame) -> SignalType:
        """Price vs SMA for trend direction."""
        sma = df['close'].rolling(window=self.sma_period).mean()
        
        if df['close'].iloc[-1] > sma.iloc[-1]:
             return SignalType.BUY
        elif df['close'].iloc[-1] < sma.iloc[-1]:
             return SignalType.SELL
        
        return SignalType.NEUTRAL

    def _get_macro_trend(self, df: pd.DataFrame, period: int = 20) -> SignalType:
        """D1 macro trend using EMA-20 — faster and more responsive than SMA-50."""
        ema = df['close'].ewm(span=period, adjust=False).mean()
        price = df['close'].iloc[-1]
        ema_val = ema.iloc[-1]
        if price > ema_val:
            return SignalType.BUY
        elif price < ema_val:
            return SignalType.SELL
        return SignalType.NEUTRAL

    def _find_target(self, df: pd.DataFrame, signal_type: SignalType, entry_price: float,
                     sl_price: float = 0.0, min_rr_override: float = None) -> float:
        """
        Finds the Take Profit target.
        Hybrid Approach:
        1. Identify Structural Target (Peak High/Low).
        2. Enforce Minimum R:R Floor (e.g., at least 3.0R if structure is too close).
        3. Cap at Conservative Max R:R (e.g., 5.0R).
        """
        # Look back for Structure
        window = df.iloc[-self.lookback:-1]
        
        risk = abs(entry_price - sl_price)
        if risk == 0:
            risk = 0.0010  # Fallback 10 pips equivalent
        
        # Minimum R:R floor (at least 3.0R) and Max R:R Cap.
        # Reversal sweeps may pass a looser floor so a counter-trend entry is not
        # forced to chase a structure target it cannot realistically reach.
        min_rr = min_rr_override if (min_rr_override and min_rr_override > 0) else (
            self.risk_reward_ratio if self.risk_reward_ratio > 0 else 3.0)
        max_rr = self.config['strategy'].get('max_risk_reward_ratio', 5.0)

        # Check for Infinite TP (Runner Mode)
        if self.config['strategy'].get('infinite_tp', False):
            return 0.0  # No TP, let Trail Stop handle it

        if self.config['strategy'].get('tp_mode') == 'fixed_rr':
            rr = self.risk_reward_ratio
            if signal_type == SignalType.BUY:
                return entry_price + (risk * rr)
            else:
                return entry_price - (risk * rr)

        if signal_type == SignalType.BUY:
            min_target = entry_price + (risk * min_rr)
            max_target = entry_price + (risk * max_rr)
            structure_target = window['high'].max()

            if structure_target <= entry_price:
                return min_target

            # If structure is too close (< min_rr, e.g. 0.73R), enforce at least 3.0R (min_target)
            # Capped at max_rr (5.0R)
            return min(max(structure_target, min_target), max_target)
        
        elif signal_type == SignalType.SELL:
            min_target = entry_price - (risk * min_rr)
            max_target = entry_price - (risk * max_rr)
            structure_target = window['low'].min()

            if structure_target >= entry_price:
                return min_target

            # For SELL, lower price = more profit.
            # If structure is too close (higher than min_target), enforce at least 3.0R (min_target)
            # Capped at max_rr (max_target)
            return max(min(structure_target, min_target), max_target)
        
        return 0.0

    def _calculate_rsi(self, series, period):
        if len(series) < period + 1:
            return 50.0 # Neutral fallback
            
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        if loss.iloc[-1] == 0:
            return 100.0 if gain.iloc[-1] > 0 else 50.0
            
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        return rsi if not pd.isna(rsi) else 50.0

    def _calculate_atr(self, df, period=14):
        """Calculates Average True Range."""
        if len(df) < period + 1:
            return 0.0 # Signal to use fallback
            
        high = df['high']
        low = df['low']
        prev_close = df['close'].shift(1)
        
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(window=period).mean().iloc[-1]
        return atr if not pd.isna(atr) else 0.0

    def _calculate_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculates the Average Directional Index (ADX).
        ADX < 20  → ranging / consolidating market
        ADX >= 20 → trending market (trade allowed)
        Requires at least 2 * period + 1 rows for a meaningful result.
        """
        min_bars = 2 * period + 1
        if len(df) < min_bars:
            return 0.0

        high = df['high'].values
        low  = df['low'].values
        close = df['close'].values

        # Directional Movement
        up_move   = np.diff(high)
        down_move = -np.diff(low)

        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,  0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True Range
        prev_close = close[:-1]
        h = high[1:]
        l = low[1:]
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))

        # Wilder smoothing (equivalent to EWM with alpha=1/period)
        def wilder_smooth(arr, n):
            result = np.zeros(len(arr))
            result[n - 1] = arr[:n].sum()
            for i in range(n, len(arr)):
                result[i] = result[i - 1] - (result[i - 1] / n) + arr[i]
            return result

        atr_s    = wilder_smooth(tr,       period)
        plus_s   = wilder_smooth(plus_dm,  period)
        minus_s  = wilder_smooth(minus_dm, period)

        with np.errstate(divide='ignore', invalid='ignore'):
            plus_di  = np.where(atr_s > 0, 100 * plus_s  / atr_s, 0.0)
            minus_di = np.where(atr_s > 0, 100 * minus_s / atr_s, 0.0)
            dx_denom = plus_di + minus_di
            dx       = np.where(dx_denom > 0, 100 * np.abs(plus_di - minus_di) / dx_denom, 0.0)

        adx_s = wilder_smooth(dx, period)
        adx   = adx_s[-1] / period  # Normalise from accumulated Wilder sum
        return float(adx) if not np.isnan(adx) else 0.0
