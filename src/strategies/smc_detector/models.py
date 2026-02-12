"""
SMC Detector - Data Models
Fair Value Gaps (FVG) and Order Blocks (OB) data structures.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class FVG:
    """Fair Value Gap - Price inefficiency/imbalance zone."""
    fvg_type: str        # 'bullish' | 'bearish'
    top: float           # Upper boundary
    bottom: float        # Lower boundary
    timestamp: datetime  # When formed
    origin_index: int    # Index in dataframe
    filled: bool = False # Has price returned?
    strength: float = 0.0  # 0-1 score based on gap size relative to ATR

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return abs(self.top - self.bottom)


@dataclass
class OrderBlock:
    """Order Block - Institutional entry zone."""
    ob_type: str         # 'bullish' | 'bearish'
    top: float           # Upper boundary (candle high)
    bottom: float        # Lower boundary (candle low)
    timestamp: datetime
    origin_index: int    # Index of OB candle
    impulse_strength: float = 0.0  # ATR multiple of impulse move
    mitigated: bool = False  # Has price returned and broken through?

    @property
    def midpoint(self) -> float:
        """Optimal entry point (50% level)."""
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return abs(self.top - self.bottom)


@dataclass
class SMCZone:
    """Combined SMC Zone with confluence score."""
    zone_type: str       # 'bullish' | 'bearish'
    top: float
    bottom: float
    has_ob: bool
    has_fvg: bool
    confluence_score: int  # 0-100
    order_block: Optional[OrderBlock] = None
    fvg: Optional[FVG] = None
