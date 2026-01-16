"""
SMC Detector - Fair Value Gap Detection
Detects bullish and bearish FVG patterns (3-candle gaps).
"""
import pandas as pd
from typing import List
from .models import FVG


def detect_fvg_zones(df: pd.DataFrame, min_gap_atr_ratio: float = 0.3) -> List[FVG]:
    """
    Detect Fair Value Gaps in OHLC data.
    
    A Bullish FVG: Gap between C1.high and C3.low (C3.low > C1.high)
    A Bearish FVG: Gap between C1.low and C3.high (C3.high < C1.low)
    
    Parameters:
    - df: DataFrame with columns [open, high, low, close] and datetime index
    - min_gap_atr_ratio: Minimum gap size as ratio of ATR to be considered valid
    
    Returns:
    - List of FVG objects
    """
    if len(df) < 20:
        return []
    
    fvg_zones = []
    
    # Calculate ATR for gap significance filtering
    atr = _calculate_atr(df, period=14)
    
    for i in range(2, len(df)):
        c1 = df.iloc[i - 2]  # First candle
        c2 = df.iloc[i - 1]  # Impulse candle (middle)
        c3 = df.iloc[i]      # Third candle
        
        current_atr = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 1.0
        
        # Check for Bullish FVG: Gap between C1.high and C3.low
        if c3['low'] > c1['high']:
            gap_size = c3['low'] - c1['high']
            
            # Filter: Gap must be significant
            if gap_size >= current_atr * min_gap_atr_ratio:
                fvg_zones.append(FVG(
                    fvg_type='bullish',
                    top=c3['low'],
                    bottom=c1['high'],
                    timestamp=c3.name if hasattr(c3, 'name') else df.index[i],
                    origin_index=i,
                    strength=min(gap_size / current_atr, 2.0) / 2.0  # Normalize to 0-1
                ))
        
        # Check for Bearish FVG: Gap between C1.low and C3.high
        if c3['high'] < c1['low']:
            gap_size = c1['low'] - c3['high']
            
            if gap_size >= current_atr * min_gap_atr_ratio:
                fvg_zones.append(FVG(
                    fvg_type='bearish',
                    top=c1['low'],
                    bottom=c3['high'],
                    timestamp=c3.name if hasattr(c3, 'name') else df.index[i],
                    origin_index=i,
                    strength=min(gap_size / current_atr, 2.0) / 2.0
                ))
    
    # Mark filled FVGs (price has returned)
    if len(fvg_zones) > 0 and len(df) > fvg_zones[-1].origin_index:
        for fvg in fvg_zones:
            # Check if any subsequent candle filled the gap
            subsequent = df.iloc[fvg.origin_index + 1:]
            if len(subsequent) > 0:
                if fvg.fvg_type == 'bullish':
                    # Bullish FVG filled if price drops into the gap
                    if subsequent['low'].min() <= fvg.midpoint:
                        fvg.filled = True
                else:
                    # Bearish FVG filled if price rises into the gap
                    if subsequent['high'].max() >= fvg.midpoint:
                        fvg.filled = True
    
    return fvg_zones


def get_active_fvg_zones(df: pd.DataFrame, lookback: int = 50) -> List[FVG]:
    """
    Get only unfilled (active) FVG zones within the lookback period.
    """
    all_fvgs = detect_fvg_zones(df)
    
    # Filter to recent and unfilled
    active = [f for f in all_fvgs 
              if not f.filled and f.origin_index >= len(df) - lookback]
    
    return active


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
