import pandas as pd
import numpy as np
import logging
print("### STRATEGY MODULE LOADED ###")
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
        self.rsi_confirmation = False           # Per-symbol: RSI momentum filter

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
        
        # LOGGING
        logger.debug(f"DEBUG: {symbol} TrendTF: {trend_major}, EntryTF: {trend_entry}")

        current_trend = SignalType.NEUTRAL
        if trend_major == SignalType.BUY and trend_entry == SignalType.BUY:
            current_trend = SignalType.BUY
        elif trend_major == SignalType.SELL and trend_entry == SignalType.SELL:
            current_trend = SignalType.SELL
        else:
             if self.require_trend_alignment:
                 return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Trend Misalignment")
             current_trend = trend_entry



        if current_trend == SignalType.NEUTRAL:
             return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Structure Neutral")

        # 2. RSI Confirmation Filter (optional per-symbol)
        if self.rsi_confirmation and len(df_entry) > 20:
            rsi = self._calculate_rsi(df_entry['close'], self.config['strategy'].get('rsi_period', 14))
            
            # Use configured thresholds if available, else standard 50 centerline
            buy_limit = self.rsi_buy_threshold if hasattr(self, 'rsi_buy_threshold') else 50
            sell_limit = self.rsi_sell_threshold if hasattr(self, 'rsi_sell_threshold') else 50

            if current_trend == SignalType.BUY and rsi > buy_limit:
                return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, f"RSI too high for buy ({rsi:.0f} > {buy_limit})")
            elif current_trend == SignalType.SELL and rsi < sell_limit:
                return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, f"RSI too low for sell ({rsi:.0f} < {sell_limit})")

        # 3. Identify Liquidity (Recent Swing Points on Entry TF)
        liquidity_level = self._find_recent_liquidity(df_entry, current_trend)
        
        if liquidity_level is None:
             return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "No recent liquidity found")
        
        logger.info(f"DEBUG: {symbol} [{label}] Trend is {current_trend} (Major={trend_major}, Entry={trend_entry}). Checking for {current_trend} setups...")
        # logger.debug(f"DEBUG: {symbol} Trend {current_trend}. Liquidity Level: {liquidity_level}")

        # 3. Check for Sweep (Wick)
        last_candle = df_entry.iloc[-1]
        
        signal_type = SignalType.NEUTRAL
        stop_loss = 0.0
        
        if current_trend == SignalType.BUY:
            # 1. SWEEP BUY (Reversal at Lows)
            # Find Support Level
            support_level = liquidity_level # Already found min()
            
            # Check for Sweep (Wick Rejection)
            # STRICT: Candle MUST be Green (Close > Open) to confirm buyer strength
            if last_candle['low'] < support_level and last_candle['close'] > support_level and last_candle['close'] > last_candle['open']:
                # Check Wick Quality
                body_size = abs(last_candle['close'] - last_candle['open'])
                lower_wick = last_candle['open'] - last_candle['low'] if last_candle['open'] < last_candle['close'] else last_candle['close'] - last_candle['low']
                total_range = last_candle['high'] - last_candle['low']
                
                ratio = lower_wick / total_range if total_range > 0 else 0
                if total_range > 0 and ratio >= self.wick_threshold_ratio:
                    signal_type = SignalType.BUY
                    stop_loss = last_candle['low'] 
                    price = last_candle['close']
                    # Limit logic handled below
                else:
                    logger.info(f"DEBUG: {symbol} Low-Test: Ratio {ratio:.2f} < {self.wick_threshold_ratio}")
            else:
                 # Debug: Why no sweep buy?
                 if last_candle['low'] < support_level and last_candle['close'] > support_level:
                     logger.info(f"DEBUG: {symbol} Sweep Buy Rejected: Candle not Green (Close {last_candle['close']} !> Open {last_candle['open']})")
            
            # 2. BREAKOUT BUY (Continuation through Highs)
            # We need to find Resistance Level
            resistance_level = df_entry.iloc[-self.lookback:-1]['high'].max()
            
            # Check for Breakout (Strong Close above Resistance)
            if last_candle['close'] > resistance_level and last_candle['open'] < last_candle['close']:
                # Filter: Strong Body (Momentum)
                body = last_candle['close'] - last_candle['open']
                total = last_candle['high'] - last_candle['low']
                if total > 0 and (body / total) >= 0.50: # Body is >= 50% of candle
                    signal_type = SignalType.BUY
                    stop_loss = last_candle['low'] # SL below breakout candle
                    price = last_candle['close']
                    # Breakouts are immediate market entries
                else:
                     logger.info(f"DEBUG: {symbol} Buy-Breakout: Weak Body {(body/total):.2f} < 0.50")
            else:
                 logger.info(f"DEBUG: {symbol} No Buy Setup (Close {last_candle['close']:.5f} !> Res {resistance_level:.5f})")
        
        elif current_trend == SignalType.SELL:
            # 1. SWEEP SELL (Reversal at Highs)
            resistance_level = liquidity_level # Already found max()
            
            if last_candle['high'] > resistance_level and last_candle['close'] < resistance_level and last_candle['close'] < last_candle['open']:
                # Check Wick Quality
                upper_wick = last_candle['high'] - last_candle['open'] if last_candle['open'] > last_candle['close'] else last_candle['high'] - last_candle['close']
                total_range = last_candle['high'] - last_candle['low']
                
                ratio = upper_wick / total_range if total_range > 0 else 0
                if total_range > 0 and ratio >= self.wick_threshold_ratio:
                    signal_type = SignalType.SELL
                    stop_loss = last_candle['high']
                    price = last_candle['close']
                else:
                    logger.info(f"DEBUG: {symbol} High-Test: Ratio {ratio:.2f} < {self.wick_threshold_ratio}")
            else:
                 if last_candle['high'] > resistance_level and last_candle['close'] < resistance_level:
                     logger.info(f"DEBUG: {symbol} Sweep Sell Rejected: Candle not Red (Close {last_candle['close']} !< Open {last_candle['open']})")
            
            # 2. BREAKOUT SELL (Continuation through Lows)
            support_level = df_entry.iloc[-self.lookback:-1]['low'].min()
            
            if last_candle['close'] < support_level and last_candle['open'] > last_candle['close']:
                # Filter: Strong Body
                body = last_candle['open'] - last_candle['close']
                total = last_candle['high'] - last_candle['low']
                if total > 0 and (body / total) >= 0.50:
                    signal_type = SignalType.SELL
                    stop_loss = last_candle['high']
                    price = last_candle['close']
                else:
                    logger.info(f"DEBUG: {symbol} Sell-Breakout: Weak Body {(body/total):.2f} < 0.50")
            else:
                 logger.info(f"DEBUG: {symbol} No Sell Setup (Close {last_candle['close']:.5f} !< Supp {support_level:.5f})")

        if signal_type != SignalType.NEUTRAL:
            # VALIDATION: Use STOP ORDERS to confirm breakout.
            # Instead of entering at Market, we place a STOP order at the wick extreme.
            
            is_stop_order = True
            is_limit = False
            
            last_candle = df_entry.iloc[-1]
            
            # --- VOLATILITY-BASED RISK (ATR) ---
            atr_multiplier = self.config['strategy'].get('atr_multiplier', 1.5)
            # Check for generic multiplier map
            multiplier_map = self.config['strategy'].get('atr_multiplier_map', {})
            atr_multiplier = multiplier_map.get(symbol, atr_multiplier)
            
            # Force/Ensure XAUUSD is low (0.5) if not caught by map for some reason
            if "XAU" in symbol and atr_multiplier > 0.5:
                 atr_multiplier = 0.5
                 logger.info(f"DEBUG: {symbol} Forced ATR to 0.5 (Was {atr_multiplier})")
                 
            logger.info(f"DEBUG: {symbol} ATR Multiplier Lookup: Pct={atr_multiplier} (Map={multiplier_map})")

            entry_multiplier = self.config['strategy'].get('entry_atr_multiplier', 0.1)
            atr_period = self.config['strategy'].get('atr_period', 14)
            atr_value = self._calculate_atr(df_entry, atr_period)
            
            # SL Buffer (Safety)
            sl_buffers = self.config['strategy'].get('sl_buffer_map', {})
            fallback_buffer = sl_buffers.get(symbol, sl_buffers.get('default', 0.50))
            
            sl_buffer_price = atr_value * atr_multiplier if (atr_value and not pd.isna(atr_value)) else fallback_buffer
            sl_buffer_price = max(sl_buffer_price, fallback_buffer)

            # Entry Buffer (Tighter confirmations)
            entry_buffer_price = atr_value * entry_multiplier if (atr_value and not pd.isna(atr_value)) else (fallback_buffer * 0.2)
            
            logger.info(f"DEBUG: {symbol} SL Buffer: {sl_buffer_price:.5f} | Entry Buffer: {entry_buffer_price:.5f} (ATR: {atr_value if not pd.isna(atr_value) else 'NaN'})")
            
            if signal_type == SignalType.BUY:
                 # Buy Stop at High of signal candle + Small Entry Buffer
                 price = last_candle['high'] + entry_buffer_price
                 stop_loss = last_candle['low'] - sl_buffer_price
            else:
                 # Sell Stop at Low of signal candle - Small Entry Buffer
                 price = last_candle['low'] - entry_buffer_price
                 stop_loss = last_candle['high'] + sl_buffer_price
            
            # 4. RSI Filter (Optimization for Higher Win Rate)
            rsi_period = self.config['strategy'].get('rsi_period', 14)
            rsi_value = self._calculate_rsi(df_entry['close'], rsi_period)
            
            # Simple Filter: If Trend is BUY, we want RSI to be somewhat oversold (pullback)
            # or at least NOT overbought.
            # User wants 85% WR. Let's be strict: RSI < 50 for Buy? Or RSI < 30?
            # RSI < 30 is rare. Let's try RSI < 55 (buying dip) and RSI > 45 (selling rally).


            # Define Take Profit (Targeting recent structure with Cap)
            tp_price = self._find_target(df_entry, signal_type, price, stop_loss)


            # Check Risk:Reward Ratio (Informational Only)
            risk = abs(price - stop_loss)
            reward = abs(tp_price - price)
            rr_ratio = reward / risk if risk > 0 else 0

            return Signal(symbol, signal_type, price, stop_loss, tp_price, is_limit_order=is_limit, is_stop_order=is_stop_order, comment=f"Liquidity Wick Sweep (R:R {rr_ratio:.2f})")

        return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0)

    def _get_trend(self, df: pd.DataFrame) -> SignalType:
        """Price vs SMA for trend direction."""
        sma = df['close'].rolling(window=self.sma_period).mean()
        
        if df['close'].iloc[-1] > sma.iloc[-1]:
             return SignalType.BUY
        elif df['close'].iloc[-1] < sma.iloc[-1]:
             return SignalType.SELL
        
        return SignalType.NEUTRAL

    def _find_recent_liquidity(self, df: pd.DataFrame, trend: SignalType) -> float:
        # If Bullish, we look for recent SELL SIDE Liquidity (previous Lows) to be swept.
        # If Bearish, we look for recent BUY SIDE Liquidity (previous Highs) to be swept.
        
        # Look back N candles to capture the significant High/Low before the shift
        window = df.iloc[-self.lookback:-1] # Exclude current candle
        
        if trend == SignalType.BUY:
            # We are verifying a BUY setup, so we look for valid Support (Lows)
            # OR if we are looking for a TARGET for a Sell trade (TP), we look for Lows.
            return window['low'].min()
        elif trend == SignalType.SELL:
             # We are verifying a SELL setup, so we look for Resistance (Highs)
             # OR if we are looking for a TARGET for a Buy trade (TP), we look for Highs.
            return window['high'].max()
            
        return None

    def _find_target(self, df: pd.DataFrame, signal_type: SignalType, entry_price: float, sl_price: float = 0.0) -> float:
        """
        Finds the Take Profit target.
        Hybrid Approach:
        1. Identify Structural Target (Peak High/Low).
        2. Identify Conservative Target (e.g., 2.0R or 3.0R).
        3. Take the CLOSER of the two. This respects structure but prevents "Greedy" targets that reduce Win Rate.
        """
        # Look back for Structure
        window = df.iloc[-self.lookback:-1]
        
        structure_target = 0.0
        risk = abs(entry_price - sl_price)
        if risk == 0: risk = 0.0010 # Fallback 10 pips equivalent
        
        # Max R:R Cap (Relaxed to 5.0 to allow dynamic structure targeting)
        max_rr = 5.0 
        conservative_target = 0.0

        # Check for Infinite TP (Runner Mode)
        if self.config['strategy'].get('infinite_tp', False):
            return 0.0 # No TP, let Trail Stop handle it

        if self.config['strategy'].get('tp_mode') == 'fixed_rr':
            rr = self.risk_reward_ratio
            if signal_type == SignalType.BUY:
                return entry_price + (risk * rr)
            else:
                return entry_price - (risk * rr)

        if signal_type == SignalType.BUY:
            # 1. Structural
            structure_target = window['high'].max()
            if structure_target <= entry_price: structure_target = entry_price + (risk * 2) # Fallback

            # 2. Conservative Cap
            conservative_target = entry_price + (risk * max_rr)
            
            # 3. Decision: Take the closer one
            return min(structure_target, conservative_target)
        
        elif signal_type == SignalType.SELL:
            # 1. Structural
            structure_target = window['low'].min()
            if structure_target >= entry_price: structure_target = entry_price - (risk * 2)

            # 2. Conservative Cap
            conservative_target = entry_price - (risk * max_rr)
            
            # 3. Decision: Take the closer one (Highest value for sell relative to price? No, 'min' distance)
            # For SELL, 'closer' means HIGHER price (less drop required).
            return max(structure_target, conservative_target)
        
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
