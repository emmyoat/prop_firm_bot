import requests
import logging

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
    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token    = token
        self.chat_id  = chat_id
        self.enabled  = enabled
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.last_update_id = 0
        self.session = requests.Session()

    # ── Inbound ───────────────────────────────────────────────────────────────

    def get_updates(self) -> list[str]:
        """Polls for new commands from the authorised user or channel."""
        if not self.enabled or not self.token or not self.chat_id:
            return []

        url    = f"{self.base_url}/getUpdates"
        offset = self.last_update_id + 1 if self.last_update_id > 0 else 0
        params = {"offset": offset, "timeout": 1}

        try:
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data    = response.json()
                updates = data.get("result", [])
                commands = []

                for update in updates:
                    self.last_update_id = update["update_id"]
                    msg = update.get("message") or update.get("channel_post") or {}
                    text = msg.get("text", "").strip()

                    if text and text.startswith("/"):
                        # Handle handle suffixes like /status@MyBot
                        clean_cmd = text.split()[0].split("@")[0].lower()
                        logger.info(f"Telegram command received: '{clean_cmd}' (raw: '{text}')")
                        commands.append(clean_cmd)

                return commands

        except Exception as e:
            logger.error(f"Error polling Telegram: {self._redact(str(e))}")
        return []

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

        try:
            response = self.session.post(url, json=payload, timeout=15)
            if response.status_code != 200:
                logger.error(f"Telegram send failed: {self._redact(response.text)}")
                return False
            logger.info("Telegram notification sent.")
            return True
        except Exception as e:
            logger.error(f"Error sending Telegram message: {self._redact(str(e))}")
            return False

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
            f"📊 R:R:    `{rr:.2f}R`  {rr_label}\n"
            f"💼 Lot:    `{lot_size}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"_{comment}_\n"
            f"[📈 View Chart on TradingView]({chart_url})"
        )

        return self.send_message(message, parse_mode="Markdown")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _redact(self, text: str) -> str:
        if self.token and text:
            return str(text).replace(self.token, "******")
        return str(text)
