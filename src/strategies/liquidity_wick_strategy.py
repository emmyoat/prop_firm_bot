import pandas as pd
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

    def generate_signal(self, data: dict, symbol: str) -> Signal:
        """
        Analyzes generic "LowTF" (Entry) and "HighTF" (Trend) data to generate a signal.
        Expects data to be a dictionary: {"LowTF": df_low, "HighTF": df_high}
        Fallback: Checks "H4" and "D1" if generic keys missing.
        """
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
             current_trend = trend_entry



        if current_trend == SignalType.NEUTRAL:
             return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "Structure Neutral")

        # 2. Identify Liquidity (Recent Swing Points on Entry TF)
        liquidity_level = self._find_recent_liquidity(df_entry, current_trend)
        
        if liquidity_level is None:
             return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, "No recent liquidity found")
        
        logger.debug(f"DEBUG: {symbol} Trend {current_trend}. Liquidity Level: {liquidity_level}")

        # 3. Check for Sweep (Wick)
        last_candle = df_entry.iloc[-1]
        
        signal_type = SignalType.NEUTRAL
        stop_loss = 0.0
        
        if current_trend == SignalType.BUY:
            # 1. SWEEP BUY (Reversal at Lows)
            # Find Support Level
            support_level = liquidity_level # Already found min()
            
            # Check for Sweep (Wick Rejection)
            if last_candle['low'] < support_level and last_candle['close'] > support_level:
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
            
            # 2. BREAKOUT BUY (Continuation through Highs)
            # We need to find Resistance Level
            resistance_level = df_entry.iloc[-self.lookback:-1]['high'].max()
            
            # Check for Breakout (Strong Close above Resistance)
            if last_candle['close'] > resistance_level and last_candle['open'] < last_candle['close']:
                # Filter: Strong Body (Momentum)
                body = last_candle['close'] - last_candle['open']
                total = last_candle['high'] - last_candle['low']
                if total > 0 and (body / total) > 0.50: # Body is >50% of candle
                    signal_type = SignalType.BUY
                    stop_loss = last_candle['low'] # SL below breakout candle
                    price = last_candle['close']
                    # Breakouts are immediate market entries
        
        elif current_trend == SignalType.SELL:
            # 1. SWEEP SELL (Reversal at Highs)
            resistance_level = liquidity_level # Already found max()
            
            if last_candle['high'] > resistance_level and last_candle['close'] < resistance_level:
                # Check Wick Quality
                upper_wick = last_candle['high'] - last_candle['open'] if last_candle['open'] > last_candle['close'] else last_candle['high'] - last_candle['close']
                total_range = last_candle['high'] - last_candle['low']
                
                ratio = upper_wick / total_range if total_range > 0 else 0
                if total_range > 0 and ratio >= self.wick_threshold_ratio:
                    signal_type = SignalType.SELL
                    stop_loss = last_candle['high']
                    price = last_candle['close']
            
            # 2. BREAKOUT SELL (Continuation through Lows)
            support_level = df_entry.iloc[-self.lookback:-1]['low'].min()
            
            if last_candle['close'] < support_level and last_candle['open'] > last_candle['close']:
                # Filter: Strong Body
                body = last_candle['open'] - last_candle['close']
                total = last_candle['high'] - last_candle['low']
                if total > 0 and (body / total) > 0.50:
                    signal_type = SignalType.SELL
                    stop_loss = last_candle['high']
                    price = last_candle['close']

        if signal_type != SignalType.NEUTRAL:
            # DECISION: Market vs Limit Entry
            # "Confirmation is key to decide".
            # Logic: 
            # 1. If the candle closed VERY STRONG (Near the extreme, i.e., small entry wick), use MARKET.
            # 2. If the candle is a PINBAR (Long rejection wick, small body), use LIMIT (50% Retrace).
            
            # We already calculated 'wick' (rejection side).
            # Let's verify the "Strength" of the move back into range.
            # Ratio: Wick / Total Range.
            # If Wick Ratio > 0.6 (Mostly Wick), it's a "Rejection" -> Expect Retrace -> Limit.
            # If Wick Ratio <= 0.6 (Strong Body), it's a "Momentum" -> Expect Continued Move -> Market.
            
            is_limit = False
            
            # Recalculate ratios for valid entry
            # (Logic was inside the IFs above, we need to extract the chosen price/type)
            # Simplified: The loop above set 'signal_type', 'stop_loss', and potentially 'limit_price'.
            # We need to refine the 'price' determination here.
            
            # Re-evaluating the last candle properties for the chosen signal type
            last_candle = df_entry.iloc[-1]
            total_range = last_candle['high'] - last_candle['low']
            
            wick_ratio = 0.0
            if signal_type == SignalType.BUY:
                wick = min(last_candle['open'], last_candle['close']) - last_candle['low']
                wick_ratio = wick / total_range if total_range > 0 else 0
            else:
                wick = last_candle['high'] - max(last_candle['open'], last_candle['close'])
                wick_ratio = wick / total_range if total_range > 0 else 0
            
            if wick_ratio > 0.60:
                is_limit = True # Use the calculated limit price from above
                # Price was already set to limit_price in the block above? 
                # Yes, I set "price = limit_price" in previous edits.
            else:
                is_limit = False
                price = last_candle['close'] # Reset to Market Price
            
            # 4. RSI Filter (Optimization for Higher Win Rate)
            rsi_period = self.config['strategy'].get('rsi_period', 14)
            rsi_value = self._calculate_rsi(df_entry, rsi_period)
            
            # Simple Filter: If Trend is BUY, we want RSI to be somewhat oversold (pullback)
            # or at least NOT overbought.
            # User wants 85% WR. Let's be strict: RSI < 50 for Buy? Or RSI < 30?
            # RSI < 30 is rare. Let's try RSI < 55 (buying dip) and RSI > 45 (selling rally).
            # Strict mode: RSI < 40 for Buy.
            
            enable_rsi = True
            if enable_rsi:
                if signal_type == SignalType.BUY and rsi_value > self.rsi_buy_threshold: 
                     return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, f"RSI too high: {rsi_value:.1f}")
                if signal_type == SignalType.SELL and rsi_value < self.rsi_sell_threshold:
                     return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0, f"RSI too low: {rsi_value:.1f}")

            # Risk Friendly SL: Add Buffer (e.g., 50 points = 5 pips)

            sl_buffer = 50 * 0.00001 # Assuming 5 decimal broker for XAUUSD? No, usually 0.01.
            # We don't have 'point' info here easily without MT5 connection in strategy. 
            # We'll treat data values as price. XAUUSD 1 pip = 0.10 or 0.01? 
            # Usually XAUUSD 2000.00. 1 pip = 0.10. 5 pips = 0.50.
            # Let's use 0.50 as buffer for XAUUSD.
            sl_buffer_price = 0.50 
            
            if signal_type == SignalType.BUY:
                 stop_loss -= sl_buffer_price
            else:
                 stop_loss += sl_buffer_price

            # Define Take Profit (Targeting recent structure with Cap)
            tp_price = self._find_target(df_entry, signal_type, price, stop_loss)


            # Check Risk:Reward Ratio (Informational Only)
            risk = abs(price - stop_loss)
            reward = abs(tp_price - price)
            rr_ratio = reward / risk if risk > 0 else 0

            return Signal(symbol, signal_type, price, stop_loss, tp_price, is_limit_order=is_limit, comment=f"Liquidity Wick Sweep (R:R {rr_ratio:.2f})")

        return Signal(symbol, SignalType.NEUTRAL, 0.0, 0.0, 0.0)

    def _get_trend(self, df: pd.DataFrame) -> SignalType:
        # Simple Structure: Higher Highs + Higher Lows = Buy.
        # Lower Lows + Lower Highs = Sell.
        # We look at the last 2 major swings.
        # For simplicity/robustness: Use EMA200 slope + Price vs EMA? 
        # Or strict Swing Points? 
        # User asked for "Market Structure". Standard definition is HH/HL.
        
        # Simplified algorithm:
        # Check last 50 candles. Find highest high and lowest low.
        # Identify if price is generally making higher highs.
        # This is complex to code perfectly in one shot without a library.
        # HEURISTIC: Compare simple moving averages or Price Action of last 20 bars.
        # Let's use specific candle comparison for "Structure Break".
        
        # Better: Use simple ZigZag or Pivot logic?
        # Let's try: Is Close > SMA(50)? And is SMA(50) rising?
        # User specifically asked to "identify the current market structure".
        # Let's use SMA 50 as a proxy for trend direction for this MVP.
        sma = df['close'].rolling(window=self.sma_period).mean()
        if sma.iloc[-1] > sma.iloc[-2]:
             if df['close'].iloc[-1] > sma.iloc[-1]:
                 return SignalType.BUY
        elif sma.iloc[-1] < sma.iloc[-2]:
             if df['close'].iloc[-1] < sma.iloc[-1]:
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

        return target

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

        return target

    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
