
import requests
import logging

logger = logging.getLogger("PropBot.Notifications")

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, message: str):
        """
        Sends a message to the configured Telegram chat.
        """
        if not self.enabled or not self.token or not self.chat_id:
            logger.debug("Telegram notifications disabled or missing credentials.")
            return

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Failed to send Telegram message: {response.text}")
            else:
                logger.info("Telegram notification sent.")
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
