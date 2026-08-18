"""
TwelveData Market Data Loader
Fetches OHLCV candlestick data via the TwelveData REST API.
Drop-in replacement for MT5DataLoader — returns identical pd.DataFrame format.

Free Tier: 800 calls/day, 8 req/min
Sign up: https://twelvedata.com/register
"""

import requests
import pandas as pd
import logging
import time
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger("PropBot.Data")

# Symbol mapping: internal name → TwelveData symbol format
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "GBPUSD": "GBP/USD",
    "EURUSD": "EUR/USD",
    "USDJPY": "USD/JPY",
    "GBPJPY": "GBP/JPY",
    "US30":   "DJI",
    "NAS100": "NDX",
    "US100":  "NDX",
    "US500":  "SPX",
    "GER30":  "DAX",
    "GER40":  "DAX",
    "UK100":  "FTSE",
    "JPN225": "NIKKEI",
    "BTCUSD": "BTC/USD",
}

# Timeframe mapping: internal string → TwelveData interval string
TIMEFRAME_MAP = {
    "M1":  "1min",
    "M5":  "5min",
    "M15": "15min",
    "M30": "30min",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1day",
    "W1":  "1week",
}


class TwelveDataLoader:
    """
    Fetches market data from TwelveData API.
    Supports Forex, Metals (Gold/Silver), and Indices.
    """

    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, config: dict, api_key: str):
        self.config = config
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"apikey {api_key}"})

        # In-memory cache: (symbol, timeframe, n_bars) → (timestamp, DataFrame)
        self._cache: dict = {}
        self._cache_ttl = config.get("data_source", {}).get("cache_seconds", 60)

        # Rate limiting: TwelveData free = 8 req/min
        self._last_request_time: float = 0.0
        self._min_request_gap: float = 8.0  # seconds between requests (conservative)

        self.connected = True  # Always "connected" — no persistent session needed

    def _to_td_symbol(self, symbol: str) -> str:
        """Converts internal symbol name to TwelveData format."""
        return SYMBOL_MAP.get(symbol.upper(), symbol)

    def _to_td_timeframe(self, tf_str: str) -> str:
        """Converts internal timeframe string to TwelveData interval."""
        return TIMEFRAME_MAP.get(tf_str.upper(), "1h")

    def _throttle(self):
        """Ensures we respect TwelveData rate limits (8 req/min)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_gap:
            sleep_time = self._min_request_gap - elapsed
            logger.debug(f"TwelveData: Rate limit — sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def fetch_data(self, symbol: str, timeframe: str, n_bars: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetches OHLCV candlestick data.
        Returns a DataFrame with columns: [time, open, high, low, close, volume]
        Identical format to MT5DataLoader.fetch_data().
        """
        cache_key = (symbol, timeframe, n_bars)
        now = time.time()

        # Return cached data if still fresh
        if cache_key in self._cache:
            cached_at, df = self._cache[cache_key]
            if now - cached_at < self._cache_ttl:
                logger.debug(f"TwelveData: Cache hit for {symbol} {timeframe}")
                return df

        td_symbol = self._to_td_symbol(symbol)
        td_interval = self._to_td_timeframe(timeframe)

        self._throttle()

        try:
            url = f"{self.BASE_URL}/time_series"
            params = {
                "symbol":   td_symbol,
                "interval": td_interval,
                "outputsize": n_bars,
                "format":   "JSON",
                "order":    "ASC",  # Oldest first, same as MT5
            }

            logger.debug(f"TwelveData: Fetching {symbol} ({td_symbol}) {timeframe} ({td_interval}) x{n_bars}")
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "error":
                logger.error(f"TwelveData API error for {symbol}: {data.get('message')}")
                return None

            values = data.get("values", [])
            if not values:
                logger.warning(f"TwelveData: No data returned for {symbol} {timeframe}")
                return None

            df = pd.DataFrame(values)

            # Standardise column names and types to match MT5DataLoader output
            df.rename(columns={"datetime": "time"}, inplace=True)
            df["time"]   = pd.to_datetime(df["time"])
            df["open"]   = pd.to_numeric(df["open"],   errors="coerce")
            df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
            df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["volume"] = pd.to_numeric(df.get("volume", pd.Series([0]*len(df))), errors="coerce").fillna(0)

            df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
            df = df.reset_index(drop=True)

            # Cache result
            self._cache[cache_key] = (now, df)

            logger.debug(f"TwelveData: Loaded {len(df)} bars for {symbol} {timeframe}")
            return df

        except requests.exceptions.RequestException as e:
            logger.error(f"TwelveData: Network error fetching {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"TwelveData: Unexpected error for {symbol}: {e}")
            return None

    def get_current_price(self, symbol: str) -> tuple[Optional[float], Optional[float]]:
        """
        Returns (ask, bid) for a symbol using the /price endpoint.
        Note: TwelveData free tier returns a single mid-price.
        We apply a synthetic spread estimate for bid/ask.
        """
        td_symbol = self._to_td_symbol(symbol)
        self._throttle()

        try:
            url = f"{self.BASE_URL}/price"
            params = {"symbol": td_symbol}
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if "price" not in data:
                return None, None

            mid = float(data["price"])

            # Synthetic spread: 1 pip for forex, 0.5 for gold, 5 for indices
            spread = self._estimate_spread(symbol)
            ask = mid + spread / 2
            bid = mid - spread / 2
            return ask, bid

        except Exception as e:
            logger.error(f"TwelveData: Error fetching price for {symbol}: {e}")
            return None, None

    def _estimate_spread(self, symbol: str) -> float:
        """Rough spread estimate in price units for synthetic ask/bid."""
        sym_upper = symbol.upper()
        if "XAU" in sym_upper:
            return 0.30    # Gold ~ $0.30 spread
        elif "JPY" in sym_upper:
            return 0.02    # JPY pairs
        elif any(i in sym_upper for i in ["US30", "DJI", "DOW"]):
            return 3.0     # Dow Jones
        elif any(i in sym_upper for i in ["NAS", "NDX", "US100"]):
            return 2.0     # Nasdaq
        elif any(i in sym_upper for i in ["SPX", "US500"]):
            return 1.0     # S&P 500
        else:
            return 0.0001  # Standard forex pair (1 pip)

    def is_connected(self) -> bool:
        """Verifies API key is valid by making a lightweight request."""
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api_usage",
                timeout=8
            )
            data = resp.json()
            # If we get a current_usage field, we're connected
            if "current_usage" in data:
                remaining = data.get("plan_limit", 800) - data.get("current_usage", 0)
                logger.info(f"TwelveData: Connected ✓ | API calls remaining today: {remaining}")
                return True
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"TwelveData: Connection check failed: {e}")
            return False

    def shutdown(self):
        """Cleanup — close the HTTP session."""
        self.session.close()
        logger.info("TwelveData: Session closed.")
