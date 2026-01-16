"""
SMC Detector - Confluence Scoring
Combines Order Blocks and FVGs to calculate trade quality scores.
"""
import pandas as pd
from typing import List, Optional, Tuple
from .models import FVG, OrderBlock, SMCZone


def calculate_confluence_score(
    current_price: float,
    signal_type: str,  # 'BUY' | 'SELL'
    order_blocks: List[OrderBlock],
    fvg_zones: List[FVG],
    entry_price: float,
    stop_loss: float
) -> Tuple[int, Optional[SMCZone]]:
    """
    Calculate confluence score for a potential trade entry.
    
    Scoring:
    - OB present at entry zone: +30 points
    - FVG present at entry zone: +25 points
    - OB + FVG overlap: +20 points
    - Fresh (unmitigated) zone: +10 points
    - Strong impulse (>2x ATR): +10 points
    - Price within zone: +15 points
    
    Parameters:
    - current_price: Current market price
    - signal_type: 'BUY' or 'SELL'
    - order_blocks: List of detected Order Blocks
    - fvg_zones: List of detected FVG zones
    - entry_price: Proposed entry price
    - stop_loss: Proposed stop loss
    
    Returns:
    - Tuple of (score: int, zone: Optional[SMCZone])
    """
    score = 0
    matching_ob = None
    matching_fvg = None
    
    # Define the zone of interest (between entry and SL for validation)
    if signal_type == 'BUY':
        zone_top = entry_price
        zone_bottom = stop_loss
    else:
        zone_top = stop_loss
        zone_bottom = entry_price
    
    # --- Check for Order Block ---
    for ob in order_blocks:
        if signal_type == 'BUY' and ob.ob_type == 'bullish':
            # Check if OB overlaps with our entry zone
            if _zones_overlap(zone_bottom, zone_top, ob.bottom, ob.top):
                matching_ob = ob
                score += 30
                
                if not ob.mitigated:
                    score += 10
                
                if ob.impulse_strength >= 2.0:
                    score += 10
                break
                
        elif signal_type == 'SELL' and ob.ob_type == 'bearish':
            if _zones_overlap(zone_bottom, zone_top, ob.bottom, ob.top):
                matching_ob = ob
                score += 30
                
                if not ob.mitigated:
                    score += 10
                
                if ob.impulse_strength >= 2.0:
                    score += 10
                break
    
    # --- Check for Fair Value Gap ---
    for fvg in fvg_zones:
        if signal_type == 'BUY' and fvg.fvg_type == 'bullish':
            if _zones_overlap(zone_bottom, zone_top, fvg.bottom, fvg.top):
                matching_fvg = fvg
                score += 25
                
                if not fvg.filled:
                    score += 10
                break
                
        elif signal_type == 'SELL' and fvg.fvg_type == 'bearish':
            if _zones_overlap(zone_bottom, zone_top, fvg.bottom, fvg.top):
                matching_fvg = fvg
                score += 25
                
                if not fvg.filled:
                    score += 10
                break
    
    # --- Confluence Bonus ---
    if matching_ob and matching_fvg:
        # Check if OB and FVG overlap with each other
        if _zones_overlap(matching_ob.bottom, matching_ob.top, 
                          matching_fvg.bottom, matching_fvg.top):
            score += 20
    
    # --- Price Proximity Bonus ---
    if matching_ob:
        if matching_ob.bottom <= current_price <= matching_ob.top:
            score += 15
    elif matching_fvg:
        if matching_fvg.bottom <= current_price <= matching_fvg.top:
            score += 15
    
    # Build SMCZone result
    zone = None
    if matching_ob or matching_fvg:
        if matching_ob and matching_fvg:
            # Use the overlapping area
            zone = SMCZone(
                zone_type=signal_type.lower(),
                top=min(matching_ob.top, matching_fvg.top),
                bottom=max(matching_ob.bottom, matching_fvg.bottom),
                has_ob=True,
                has_fvg=True,
                confluence_score=min(score, 100),
                order_block=matching_ob,
                fvg=matching_fvg
            )
        elif matching_ob:
            zone = SMCZone(
                zone_type=signal_type.lower(),
                top=matching_ob.top,
                bottom=matching_ob.bottom,
                has_ob=True,
                has_fvg=False,
                confluence_score=min(score, 100),
                order_block=matching_ob
            )
        elif matching_fvg:
            zone = SMCZone(
                zone_type=signal_type.lower(),
                top=matching_fvg.top,
                bottom=matching_fvg.bottom,
                has_ob=False,
                has_fvg=True,
                confluence_score=min(score, 100),
                fvg=matching_fvg
            )
    
    return min(score, 100), zone


def _zones_overlap(z1_bottom: float, z1_top: float, 
                   z2_bottom: float, z2_top: float) -> bool:
    """Check if two price zones overlap."""
    return z1_bottom <= z2_top and z2_bottom <= z1_top


def filter_signals_by_confluence(
    signals: list,
    df: pd.DataFrame,
    min_score: int = 50
) -> list:
    """
    Filter a list of signals to only include those with sufficient confluence.
    
    This is a helper for backtesting integration.
    """
    from .fvg_detector import get_active_fvg_zones
    from .order_block import get_active_order_blocks
    
    fvgs = get_active_fvg_zones(df)
    obs = get_active_order_blocks(df)
    
    filtered = []
    for signal in signals:
        score, zone = calculate_confluence_score(
            current_price=df.iloc[-1]['close'],
            signal_type=signal.signal_type.name,
            order_blocks=obs,
            fvg_zones=fvgs,
            entry_price=signal.price,
            stop_loss=signal.sl_price
        )
        
        if score >= min_score:
            signal.confluence_score = score
            signal.smc_zone = zone
            filtered.append(signal)
    
    return filtered
