from dataclasses import dataclass
from enum import Enum
from typing import Optional

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"

@dataclass
class Signal:
    symbol: str
    signal_type: SignalType
    price: float
    sl_price: float # Price level for Stop Loss
    tp_price: float # Price level for Take Profit
    is_limit_order: bool = False
    is_stop_order: bool = False
    comment: str = ""

@dataclass
class TradeRequest:
    symbol: str
    order_type: int # MT5 constant
    volume: float
    price: float
    sl: float
    tp: float
    comment: str
