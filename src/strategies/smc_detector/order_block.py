"""
SMC Detector - Order Block Detection
Detects institutional Order Blocks based on impulse moves and BOS.
"""
import pandas as pd
from typing import List
from .models import OrderBlock


def detect_order_blocks(df: pd.DataFrame, 
                        atr_period: int = 14,
                        impulse_multiplier: float = 1.5,
                        bos_lookback: int = 5) -> List[OrderBlock]:
    """
    Detect Order Blocks in OHLC data.
    
    An Order Block is the last opposing candle before a strong impulsive move
    that creates a Break of Structure (BOS).
    
    Parameters:
    - df: DataFrame with columns [open, high, low, close] and datetime index
    - atr_period: ATR calculation period
    - impulse_multiplier: Minimum ATR multiple for valid impulse
    - bos_lookback: Number of candles to look back for swing high/low
    
    Returns:
    - List of OrderBlock objects
    """
    if len(df) < atr_period + bos_lookback + 5:
        return []
    
    order_blocks = []
    atr = _calculate_atr(df, atr_period)
    
    for i in range(atr_period + bos_lookback, len(df)):
        current = df.iloc[i]
        current_atr = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 1.0
        
        # --- Check for BULLISH impulse (Break of Structure up) ---
        if _is_bullish_bos(df, i, bos_lookback):
            impulse_size = current['close'] - current['open']
            
            # Validate impulse strength
            if impulse_size > current_atr * impulse_multiplier:
                # Find last bearish candle before impulse
                ob_candle = _find_last_opposing_candle(df, i, is_bullish=True)
                
                if ob_candle is not None:
                    ob_idx, ob_row = ob_candle
                    order_blocks.append(OrderBlock(
                        ob_type='bullish',
                        top=ob_row['high'],
                        bottom=ob_row['low'],
                        timestamp=ob_row.name if hasattr(ob_row, 'name') else df.index[ob_idx],
                        origin_index=ob_idx,
                        impulse_strength=impulse_size / current_atr
                    ))
        
        # --- Check for BEARISH impulse (Break of Structure down) ---
        if _is_bearish_bos(df, i, bos_lookback):
            impulse_size = current['open'] - current['close']
            
            if impulse_size > current_atr * impulse_multiplier:
                # Find last bullish candle before impulse
                ob_candle = _find_last_opposing_candle(df, i, is_bullish=False)
                
                if ob_candle is not None:
                    ob_idx, ob_row = ob_candle
                    order_blocks.append(OrderBlock(
                        ob_type='bearish',
                        top=ob_row['high'],
                        bottom=ob_row['low'],
                        timestamp=ob_row.name if hasattr(ob_row, 'name') else df.index[ob_idx],
                        origin_index=ob_idx,
                        impulse_strength=impulse_size / current_atr
                    ))
    
    # Mark mitigated OBs (price has returned and broken through)
    _mark_mitigated_obs(df, order_blocks)
    
    return order_blocks


def get_active_order_blocks(df: pd.DataFrame, lookback: int = 100) -> List[OrderBlock]:
    """
    Get only unmitigated (active) Order Blocks within the lookback period.
    """
    all_obs = detect_order_blocks(df)
    
    # Filter to recent and unmitigated
    active = [ob for ob in all_obs 
              if not ob.mitigated and ob.origin_index >= len(df) - lookback]
    
    return active


def _is_bullish_bos(df: pd.DataFrame, i: int, lookback: int) -> bool:
    """Check if candle at index i creates a Break of Structure (upward)."""
    if i < lookback:
        return False
    
    # Find recent swing high
    recent_high = df.iloc[i - lookback:i]['high'].max()
    
    # BOS = current candle closes above recent swing high
    return df.iloc[i]['close'] > recent_high


def _is_bearish_bos(df: pd.DataFrame, i: int, lookback: int) -> bool:
    """Check if candle at index i creates a Break of Structure (downward)."""
    if i < lookback:
        return False
    
    # Find recent swing low
    recent_low = df.iloc[i - lookback:i]['low'].min()
    
    # BOS = current candle closes below recent swing low
    return df.iloc[i]['close'] < recent_low


def _find_last_opposing_candle(df: pd.DataFrame, i: int, is_bullish: bool, max_lookback: int = 5):
    """
    Find the last opposing candle before the impulse.
    
    For bullish OB: Find last bearish candle
    For bearish OB: Find last bullish candle
    """
    for j in range(i - 1, max(0, i - max_lookback - 1), -1):
        candle = df.iloc[j]
        
        if is_bullish:
            # Looking for bearish candle (close < open)
            if candle['close'] < candle['open']:
                return (j, candle)
        else:
            # Looking for bullish candle (close > open)
            if candle['close'] > candle['open']:
                return (j, candle)
    
    return None


def _mark_mitigated_obs(df: pd.DataFrame, order_blocks: List[OrderBlock]):
    """Mark Order Blocks that have been mitigated (price returned and broke through)."""
    for ob in order_blocks:
        if ob.origin_index >= len(df) - 1:
            continue
            
        subsequent = df.iloc[ob.origin_index + 1:]
        
        if len(subsequent) == 0:
            continue
        
        if ob.ob_type == 'bullish':
            # Bullish OB mitigated if price closes below OB low
            if (subsequent['close'] < ob.bottom).any():
                ob.mitigated = True
        else:
            # Bearish OB mitigated if price closes above OB high
            if (subsequent['close'] > ob.top).any():
                ob.mitigated = True


def _calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    
    return tr.rolling(window=period).mean()
