import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import logging
from typing import Optional

logger = logging.getLogger("PropBot.Data")

class MT5DataLoader:
    def __init__(self, config):
        self.config = config
        self.connected = False

    def connect(self, credentials):
        """
        Initializes the MT5 connection.
        """
        path = self.config['system'].get('mt5_path')
        if not mt5.initialize(path=path) if path else mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            mt5.shutdown()
            return False

        # Attempt to login if credentials provided
        if credentials["login"]:
            authorized = mt5.login(
                credentials["login"],
                password=credentials["password"],
                server=credentials["server"]
            )
            if not authorized:
                logger.error(f"MT5 Login failed: {mt5.last_error()}")
                return False
        
        logger.info("MT5 Connected Successfully")
        self.connected = True
        return True

    def get_timeframe_constant(self, tf_str):
        """Converts string timeframe to MT5 constant"""
        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1
        }
        return tf_map.get(tf_str, mt5.TIMEFRAME_M15)

    def fetch_data(self, symbol: str, timeframe: str, n_bars: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetches OHLCV data for a symbol.
        """
        if not self.connected:
            logger.error("Not connected to MT5")
            return None

        tf = self.get_timeframe_constant(timeframe)
        
        # Check if symbol is available in MarketWatch
        symbol_info = mt5.symbol_info(symbol)
        
        # If not found immediately, try to select it first (it might not be in Market Watch)
        # If not found immediately, try to select it first (it might not be in Market Watch)
        if symbol_info is None:
            if mt5.symbol_select(symbol, True):
                # Verify it's actually available (sometimes takes milliseconds to propagate)
                import time
                for _ in range(5): # Retry up to 5 times
                    symbol_info = mt5.symbol_info(symbol)
                    if symbol_info is not None:
                        break
                    time.sleep(0.5) 
            
        if symbol_info is None:
            logger.error(f"Symbol {symbol} not found (Could not select)")
            return None
            
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to select symbol {symbol}")
                return None

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
        
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to get rates for {symbol}")
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Rename columns to match standard conventions (Open, High, Low, Close, Volume)
        df.rename(columns={'tick_volume': 'volume', 'real_volume': 'vol_real'}, inplace=True)
        
        return df

    def get_current_price(self, symbol):
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return tick.ask, tick.bid
        return None, None

    def shutdown(self):
        mt5.shutdown()
