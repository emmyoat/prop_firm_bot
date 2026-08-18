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
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

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

    def __init__(
        self,
        config: dict,
        api_key: str,
        session: Optional[requests.Session] = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
    ):
        self.config = config
        self.api_key = api_key
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"apikey {api_key}"})
        self._clock = clock
        self._sleep = sleeper
        self._random = random_source

        source_cfg = config.get("data_source", {})
        retry_cfg = source_cfg.get("retry", {})

        # In-memory cache: (symbol, timeframe, n_bars) -> (timestamp, DataFrame)
        self._cache: dict = {}
        self._cache_ttl = float(source_cfg.get("cache_seconds", 60))
        self._max_stale_seconds = float(source_cfg.get("max_stale_seconds", self._cache_ttl))

        self._min_request_gap = float(source_cfg.get("min_request_gap_seconds", 8.0))
        self._requests_per_minute = int(source_cfg.get("requests_per_minute", 8))
        self._daily_request_limit = int(source_cfg.get("daily_request_limit", 800))
        self._daily_request_date = self._utc_date()
        self._daily_request_count = 0
        self._request_times: deque[float] = deque()
        self._last_request_time = 0.0

        self._max_attempts = max(1, int(retry_cfg.get("max_attempts", 3)))
        self._base_delay = float(retry_cfg.get("base_delay_seconds", 2.0))
        self._max_delay = float(retry_cfg.get("max_delay_seconds", 30.0))
        self._jitter_seconds = float(retry_cfg.get("jitter_seconds", 1.0))
        self._connect_timeout = float(retry_cfg.get("connect_timeout_seconds", 5.0))
        self._read_timeout = float(retry_cfg.get("read_timeout_seconds", 15.0))

        self.metrics: dict[str, Any] = {
            "requests": 0,
            "daily_requests": 0,
            "retries": 0,
            "failures": 0,
            "cache_hits": 0,
            "stale_cache_hits": 0,
            "last_success_at": None,
            "last_error": "",
            "last_status_code": None,
            "daily_limit": self._daily_request_limit,
        }
        self.connected = True

    def _to_td_symbol(self, symbol: str) -> str:
        """Converts internal symbol name to TwelveData format."""
        return SYMBOL_MAP.get(symbol.upper(), symbol)

    def _to_td_timeframe(self, tf_str: str) -> str:
        """Converts internal timeframe string to TwelveData interval."""
        return TIMEFRAME_MAP.get(tf_str.upper(), "1h")

    def _throttle(self):
        """Enforce both a minimum gap and a rolling one-minute request limit."""
        now = self._clock()
        while self._request_times and now - self._request_times[0] >= 60.0:
            self._request_times.popleft()

        waits = [max(0.0, self._min_request_gap - (now - self._last_request_time))]
        if len(self._request_times) >= self._requests_per_minute:
            waits.append(max(0.0, 60.0 - (now - self._request_times[0])))
        wait_seconds = max(waits)
        if wait_seconds > 0:
            logger.debug(f"TwelveData: Rate limit - sleeping {wait_seconds:.1f}s")
            self._sleep(wait_seconds)
            now = self._clock()
            while self._request_times and now - self._request_times[0] >= 60.0:
                self._request_times.popleft()

        self._last_request_time = now
        self._request_times.append(now)

    def _retry_delay(self, attempt: int, response=None) -> float:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                return min(float(retry_after), self._max_delay)
            except (TypeError, ValueError):
                pass
        exponential = min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)
        return exponential + (self._random() * self._jitter_seconds)

    def _utc_date(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()

    def _consume_daily_budget(self) -> bool:
        current_date = self._utc_date()
        if current_date != self._daily_request_date:
            self._daily_request_date = current_date
            self._daily_request_count = 0
            self.metrics["daily_requests"] = 0
        if self._daily_request_count >= self._daily_request_limit:
            self._record_failure(
                f"daily request budget exhausted ({self._daily_request_limit})"
            )
            return False
        self._daily_request_count += 1
        self.metrics["daily_requests"] = self._daily_request_count
        return True

    def _request_json(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """Perform a classified, rate-limited request with bounded retries."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        for attempt in range(1, self._max_attempts + 1):
            if not self._consume_daily_budget():
                return None
            self._throttle()
            response = None
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(self._connect_timeout, self._read_timeout),
                )
                self.metrics["requests"] += 1
                self.metrics["last_status_code"] = response.status_code
                retryable = response.status_code == 429 or 500 <= response.status_code < 600
                if retryable:
                    raise requests.exceptions.HTTPError(
                        f"retryable HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "error":
                    code = int(data.get("code", 0) or 0)
                    message = str(data.get("message", "provider error"))
                    if code == 429 or "rate limit" in message.lower():
                        raise requests.exceptions.HTTPError(message, response=response)
                    self._record_failure(message)
                    return None

                self.connected = True
                self.metrics["last_success_at"] = self._clock()
                self.metrics["last_error"] = ""
                return data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as exc:
                retryable = not isinstance(exc, requests.exceptions.HTTPError) or (
                    response is not None
                    and (response.status_code == 429 or response.status_code >= 500)
                )
                if not retryable or attempt >= self._max_attempts:
                    self._record_failure(str(exc))
                    return None
                self.metrics["retries"] += 1
                delay = self._retry_delay(attempt, response)
                logger.warning(
                    f"TwelveData: Request failed (attempt {attempt}/{self._max_attempts}); "
                    f"retrying in {delay:.1f}s: {exc}"
                )
                self._sleep(delay)
            except (ValueError, TypeError) as exc:
                self._record_failure(f"invalid JSON response: {exc}")
                return None
            except requests.exceptions.RequestException as exc:
                self._record_failure(str(exc))
                return None
        return None

    def _record_failure(self, message: str) -> None:
        self.connected = False
        self.metrics["failures"] += 1
        self.metrics["last_error"] = message
        logger.error(f"TwelveData: {message}")

    def fetch_data(self, symbol: str, timeframe: str, n_bars: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetches OHLCV candlestick data.
        Returns a DataFrame with columns: [time, open, high, low, close, volume]
        Identical format to MT5DataLoader.fetch_data().
        """
        cache_key = (symbol, timeframe, n_bars)
        now = self._clock()

        # Return cached data if still fresh
        if cache_key in self._cache:
            cached_at, df = self._cache[cache_key]
            if now - cached_at < self._cache_ttl:
                self.metrics["cache_hits"] += 1
                logger.debug(f"TwelveData: Cache hit for {symbol} {timeframe}")
                return df.copy()

        td_symbol = self._to_td_symbol(symbol)
        td_interval = self._to_td_timeframe(timeframe)

        try:
            params = {
                "symbol": td_symbol,
                "interval": td_interval,
                "outputsize": n_bars,
                "format": "JSON",
                "order": "ASC",
            }

            logger.debug(f"TwelveData: Fetching {symbol} ({td_symbol}) {timeframe} ({td_interval}) x{n_bars}")
            data = self._request_json("time_series", params=params)
            if data is None:
                cached = self._cache.get(cache_key)
                if cached and now - cached[0] <= self._max_stale_seconds:
                    self.metrics["stale_cache_hits"] += 1
                    logger.warning(f"TwelveData: Serving stale cache for {symbol} {timeframe}")
                    return cached[1].copy()
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
            self._cache[cache_key] = (self._clock(), df)

            logger.debug(f"TwelveData: Loaded {len(df)} bars for {symbol} {timeframe}")
            return df

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

        try:
            data = self._request_json("price", params={"symbol": td_symbol})
            if not data or "price" not in data:
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
        """Verify credentials and update usage metrics through the normal request path."""
        data = self._request_json("api_usage")
        if not data:
            return False
        if "current_usage" in data:
            limit = int(data.get("plan_limit", self._daily_request_limit))
            usage = int(data.get("current_usage", 0))
            self.metrics["daily_limit"] = limit
            self.metrics["daily_usage"] = usage
            self.metrics["daily_remaining"] = max(0, limit - usage)
            logger.info(f"TwelveData: Connected | API calls remaining today: {max(0, limit - usage)}")
        return True

    def get_metrics(self) -> dict[str, Any]:
        return dict(self.metrics)

    def shutdown(self):
        """Cleanup — close the HTTP session."""
        self.session.close()
        logger.info("TwelveData: Session closed.")
