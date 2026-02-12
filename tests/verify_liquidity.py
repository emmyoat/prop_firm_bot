import pandas as pd
import sys
import os

# Fix Path: Add project root (one level up from tests/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.strategies.liquidity_wick_strategy import LiquidityWickStrategy
from src.models import SignalType

def create_mock_data(candle_color="GREEN"):
    data = {
        'open': [50.0] * 100,
        'high': [50.5] * 100,
        'low': [49.5] * 100,
        'close': [50.0] * 100,
        'tick_volume': [1000] * 100,
        'spread': [10] * 100,
        'real_volume': [1000] * 100
    }
    df_h4 = pd.DataFrame(data)
    
    # Recent history construction to satisfy:
    # 1. SMA(40) < Price (Trend Buy) -> Needs old data (60-74) to be LOW.
    # 2. Support(25) > Low (Sweep) -> Needs recent data (75-99) to be HIGH.
    
    # 60-74: Low Prices (Anchor SMA down)
    for i in range(60, 75):
         df_h4.loc[i, 'close'] = 50.0
         df_h4.loc[i, 'low'] = 49.0
    
    # 75-98: High Prices (Set Support Context)
    for i in range(75, 99):
        df_h4.loc[i, 'close'] = 110.0
        df_h4.loc[i, 'low'] = 108.0 
        df_h4.loc[i, 'open'] = 109.0
        df_h4.loc[i, 'high'] = 111.0

    # Swing Low at index 80 (The level to sweep)
    df_h4.loc[80, 'low'] = 105.0 
    df_h4.loc[80, 'high'] = 110.0
    df_h4.loc[80, 'close'] = 109.0
    
    # df_h4.loc[85:98, 'close'] = 107.0 (Removed)
    
    # Candle 99: Sweep the low (105.0)
    
    if candle_color == "GREEN":
        # Valid: Close > Open
        df_h4.loc[99, 'open'] = 105.2  # Open higher makes lower wick longer (Open-Low)
        df_h4.loc[99, 'high'] = 106.0 # Wick Up
        df_h4.loc[99, 'low'] = 104.5  # Sweep Low
        df_h4.loc[99, 'close'] = 105.5 # Close Green
    else:
        # Invalid: Close < Open (Red)
        df_h4.loc[99, 'open'] = 106.0
        df_h4.loc[99, 'high'] = 106.5
        df_h4.loc[99, 'low'] = 104.5 
        df_h4.loc[99, 'close'] = 105.5 # Close Red
    
    df_h4.loc[99, 'time'] = pd.Timestamp.now()
    
    df_d1 = df_h4.copy()
    return {"H4": df_h4, "D1": df_d1}

def test_strategy():
    config = {
        'system': {'log_level': 'DEBUG', 'symbol_list': ['TEST_SYMBOL']},
        'strategy': {
            'wick_threshold_ratio': 0.35,
            'lookback': 25,
            'ema_period_fast': 50,
            'ema_period_slow': 200,
            'rsi_period': 14,
            'sma_period': 40,
            'atr_multiplier': 1.5,
            'atr_multiplier_map': {},
            'entry_atr_multiplier': 0.1,
            'atr_period': 14
        },
    }
    
    # Configure Logging to console
    import logging
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    
    strategy = LiquidityWickStrategy(config)
    
    print("--- Test 1: GREEN Candle (Valid) ---")
    data_valid = create_mock_data("GREEN")
    signal_valid = strategy.generate_signal(data_valid, "TEST_VALID")
    
    if signal_valid.signal_type == SignalType.BUY:
        print("PASS: Generated BUY signal for Green Candle")
    else:
        print(f"FAIL: Did NOT generate BUY for Green Candle. Got {signal_valid.signal_type}")

    print("\n--- Test 2: RED Candle (Invalid) ---")
    data_invalid = create_mock_data("RED")
    signal_invalid = strategy.generate_signal(data_invalid, "TEST_INVALID")
    
    if signal_invalid.signal_type == SignalType.NEUTRAL:
        print("PASS: Rejected Red Candle (NEUTRAL)")
    else:
        print(f"FAIL: Accepted Red Candle! Got {signal_invalid.signal_type}")

if __name__ == "__main__":
    test_strategy()
