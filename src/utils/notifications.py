import requests
import logging
import random
import time
from typing import Callable, Optional

from src.utils.state_store import StateStore

logger = logging.getLogger("PropBot.Notifications")

# TradingView deep-link template
TRADINGVIEW_URL = "https://www.tradingview.com/chart/?symbol={symbol}&interval={interval}"

# Timeframe → TradingView interval mapping
TF_TO_TV = {
    "M1":  "1",
    "M5":  "5",
    "M15": "15",
    "M30": "30",
    "H1":  "60",
    "H4":  "240",
    "D1":  "D",
    "W1":  "W",
}

# Internal symbol → TradingView symbol
TV_SYMBOL_MAP = {
    "XAUUSD": "OANDA:XAUUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "EURUSD": "OANDA:EURUSD",
    "USDJPY": "OANDA:USDJPY",
    "GBPJPY": "OANDA:GBPJPY",
    "US30":   "DJ:DJI",
    "NAS100": "NASDAQ:NDX",
    "US100":  "NASDAQ:NDX",
    "US500":  "SP:SPX",
    "GER30":  "XETR:DAX",
    "GER40":  "XETR:DAX",
    "UK100":  "SPREADEX:UK100",
    "BTCUSD": "BITSTAMP:BTCUSD",
}


def get_chart_link(symbol: str, timeframe: str = "H1") -> str:
    """Returns a clickable TradingView chart deep-link for the symbol."""
    tv_sym      = TV_SYMBOL_MAP.get(symbol.upper(), symbol)
    tv_interval = TF_TO_TV.get(timeframe.upper(), "60")
    return TRADINGVIEW_URL.format(symbol=tv_sym, interval=tv_interval)


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        enabled: bool = True,
        config: Optional[dict] = None,
        state_store: Optional[StateStore] = None,
        session: Optional[requests.Session] = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
    ):
        self.token = token
        self.chat_id = str(chat_id or "")
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.session = session or requests.Session()
        self.state_store = state_store
        self.state_key = f"telegram:{self.chat_id or 'default'}"
        self.last_update_id = (
            state_store.get_telegram_offset(self.state_key) if state_store else 0
        )
        retry_cfg = (config or {}).get("telegram", {}).get("retry", {})
        self.max_attempts = max(1, int(retry_cfg.get("max_attempts", 2)))
        self.base_delay = float(retry_cfg.get("base_delay_seconds", 1.0))
        self.max_delay = float(retry_cfg.get("max_delay_seconds", 5.0))
        self.jitter_seconds = float(retry_cfg.get("jitter_seconds", 0.5))
        self.connect_timeout = float(retry_cfg.get("connect_timeout_seconds", 3.0))
        self.read_timeout = float(retry_cfg.get("read_timeout_seconds", 8.0))
        self._sleep = sleeper
        self._random = random_source
        self.metrics = {
            "requests": 0,
            "retries": 0,
            "failures": 0,
            "last_success_at": None,
            "last_error": "",
            "status": "disabled" if not enabled else "unknown",
        }

    # ── Inbound ───────────────────────────────────────────────────────────────

    def get_updates(self) -> list[str]:
        """Polls for new commands from the authorised user or channel."""
        if not self.enabled or not self.token or not self.chat_id:
            return []

        url    = f"{self.base_url}/getUpdates"
        offset = self.last_update_id + 1 if self.last_update_id > 0 else 0
        params = {"offset": offset, "timeout": 1}

        data = self._request("get", url, params=params)
        if not data:
            return []

        updates = data.get("result", [])
        commands = []
        max_update_id = self.last_update_id
        try:
            for update in updates:
                max_update_id = max(max_update_id, int(update["update_id"]))
                msg = update.get("message") or update.get("channel_post") or {}
                if str(msg.get("chat", {}).get("id", "")) != self.chat_id:
                    logger.warning("Ignored Telegram command from unauthorized chat.")
                    continue
                text = msg.get("text", "").strip()
                if text and text.startswith("/"):
                    clean_cmd = text.split()[0].split("@")[0].lower()
                    logger.info(f"Telegram command received: '{clean_cmd}' (raw: '{text}')")
                    commands.append(clean_cmd)
        except (KeyError, TypeError, ValueError) as exc:
            self._record_failure(f"Invalid update payload: {exc}")
            return []

        self.last_update_id = max_update_id
        if self.state_store and max_update_id > 0:
            self.state_store.save_telegram_offset(max_update_id, self.state_key)
        return commands

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Sends a plain text message."""
        if not self.enabled or not self.token or not self.chat_id:
            logger.debug("Telegram disabled or missing credentials.")
            return False

        url     = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id":    self.chat_id,
            "text":       message,
            "parse_mode": parse_mode,
        }

        result = self._request("post", url, json=payload)
        if result is None:
            return False
        logger.info("Telegram notification sent.")
        return True

    def send_signal_alert(
        self,
        symbol:      str,
        direction:   str,
        entry:       float,
        sl:          float,
        tp:          float,
        rr:          float,
        lot_size:    float,
        timeframe:   str,
        label:       str,
        comment:     str = "",
        dd_metrics:  dict | None = None,
    ) -> bool:
        """
        Sends a richly formatted signal alert with a TradingView chart link.
        """
        icon      = "🟢🚀" if direction == "BUY" else "🔴📉"
        dir_emoji = "⬆️" if direction == "BUY" else "⬇️"
        chart_url = get_chart_link(symbol, timeframe)

        # Risk/Reward quality label
        if rr >= 3.0:
            rr_label = "🏆 Excellent"
        elif rr >= 2.0:
            rr_label = "✅ Good"
        elif rr >= 1.5:
            rr_label = "⚠️ Acceptable"
        else:
            rr_label = "❗ Low"

        message = (
            f"{icon} *{direction} SIGNAL — {symbol}*\n"
            f"{dir_emoji} *{label} Setup* | TF: `{timeframe}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Entry:  `{entry:.5f}`\n"
            f"🛑 SL:     `{sl:.5f}`\n"
            f"🎯 TP:     `{tp:.5f}`\n"
            f"💼 Lot:    `{lot_size}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"[📈 View Chart on TradingView]({chart_url})"
        )

        return self.send_message(message, parse_mode="Markdown")

    def send_breakeven_alert(
        self,
        symbol: str,
        label: str,
        direction: str,
        entry: float,
        current_price: float,
        profit_pips: float,
    ) -> bool:
        """
        Sends a clean Telegram alert instructing the user to move SL to entry (Breakeven).
        """
        message = (
            f"🛡️ *MOVE SL TO BREAKEVEN — {symbol}*\n"
            f"*{label} Setup* | `{direction}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Entry Price: `{entry:.2f}`\n"
            f"📈 Live Price:  `{current_price:.2f}` (+{profit_pips:.0f} pips)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔒 *Action:* Move SL to `{entry:.2f}` "
            f"━━━━━━━━━━━━━━━━━━"
        )
        return self.send_message(message, parse_mode="Markdown")

    def send_trailing_stop_alert(
        self,
        symbol: str,
        label: str,
        direction: str,
        new_sl: float,
        current_price: float,
        locked_pips: float,
    ) -> bool:
        """
        Sends a clean Telegram alert instructing the user to trail their Stop Loss.
        """
        message = (
            f"🔄 *TRAIL STOP — {symbol}*\n"
            f"*{label} Setup* | `{direction}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 Live Price:  `{current_price:.2f}`\n"
            f"🔒 New SL:     `{new_sl:.2f}` (+{locked_pips:.0f} pips locked)\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return self.send_message(message, parse_mode="Markdown")

    def send_trade_closed_alert(
        self,
        symbol: str,
        label: str,
        direction: str,
        exit_type: str,
        entry: float,
        exit_price: float,
        pnl_pips: float,
    ) -> bool:
        """
        Sends a clean Telegram alert when a trade hits TP, SL, or Breakeven.
        """
        if "TP" in exit_type:
            icon = "🎯"
            title = "TAKE PROFIT HIT"
            outcome = "WIN"
        elif "BE" in exit_type:
            icon = "🛡️"
            title = "CLOSED AT BREAKEVEN"
            outcome = "NO LOSS (RISK-FREE)"
        elif "TRAIL" in exit_type:
            icon = "💰"
            title = "TRAILING STOP HIT"
            outcome = "PROFIT LOCKED"
        else:
            icon = "🛑"
            title = "STOP LOSS HIT"
            outcome = "LOSS"

        message = (
            f"{icon} *{title} — {symbol}*\n"
            f"*{label} Setup* | `{direction}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Entry Price: `{entry:.2f}`\n"
            f"🏁 Exit Price:  `{exit_price:.2f}`\n"
            f"📊 PnL Result:  `{pnl_pips:+.0f} pips` ({outcome})\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return self.send_message(message, parse_mode="Markdown")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
        for attempt in range(1, self.max_attempts + 1):
            response = None
            try:
                request = getattr(self.session, method)
                response = request(
                    url,
                    timeout=(self.connect_timeout, self.read_timeout),
                    **kwargs,
                )
                self.metrics["requests"] += 1
                retryable = response.status_code == 429 or response.status_code >= 500
                if retryable:
                    raise requests.exceptions.HTTPError(
                        f"retryable HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok", False):
                    self._record_failure(str(data.get("description", "Telegram API error")))
                    return None
                self.metrics["status"] = "healthy"
                self.metrics["last_success_at"] = time.time()
                self.metrics["last_error"] = ""
                return data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as exc:
                if response is not None and response.status_code == 409 and "getUpdates" in url:
                    logger.debug("Telegram getUpdates skipped: another instance is active.")
                    return None
                retryable = not isinstance(exc, requests.exceptions.HTTPError) or (
                    response is not None
                    and (response.status_code == 429 or response.status_code >= 500)
                )
                if not retryable or attempt >= self.max_attempts:
                    self._record_failure(str(exc))
                    return None
                self.metrics["retries"] += 1
                retry_after = response.headers.get("Retry-After") if response is not None else None
                try:
                    delay = float(retry_after) if retry_after else self.base_delay * (2 ** (attempt - 1))
                except (TypeError, ValueError):
                    delay = self.base_delay * (2 ** (attempt - 1))
                delay = min(delay, self.max_delay) + self._random() * self.jitter_seconds
                self._sleep(delay)
            except (requests.exceptions.RequestException, ValueError, TypeError) as exc:
                self._record_failure(str(exc))
                return None
        return None

    def _record_failure(self, message: str) -> None:
        redacted = self._redact(message)
        self.metrics["failures"] += 1
        self.metrics["last_error"] = redacted
        self.metrics["status"] = "unavailable"
        logger.error(f"Telegram request failed: {redacted}")

    def get_metrics(self) -> dict:
        return dict(self.metrics)

    def shutdown(self) -> None:
        self.session.close()

    def _redact(self, text: str) -> str:
        if self.token and isinstance(self.token, str) and text:
            return str(text).replace(self.token, "******")
        return str(text)
