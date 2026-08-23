from dataclasses import dataclass
from enum import Enum

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"

@dataclass
class Signal:
    symbol: str
    signal_type: SignalType
    price: float
    sl_price: float  # Price level for Stop Loss
    tp_price: float  # Price level for Take Profit
    is_stop_order: bool = False
    comment: str = ""
