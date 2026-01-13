
import requests
import logging

logger = logging.getLogger("PropBot.Notifications")

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.last_update_id = 0

    def get_updates(self):
        """
        Polls for new messages from the user.
        """
        if not self.enabled or not self.token or not self.chat_id:
            return []

        url = f"{self.base_url}/getUpdates"
        # Standard polling: If last_update_id is 0, fetch all to skip old queue
        offset = self.last_update_id + 1 if self.last_update_id > 0 else 0
        params = {"offset": offset, "timeout": 1}
        
        try:
            # logger.info(f"Polling Telegram (offset: {offset})...") 
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                updates = data.get("result", [])
                
                # if updates:
                #    logger.info(f"Raw Updates: {len(updates)} found.")
                
                commands = []
                for update in updates:
                    self.last_update_id = update["update_id"]
                    message = update.get("message", {})
                    text = message.get("text", "")
                    sender_id = str(message.get("chat", {}).get("id"))
                    
                    if text:
                        logger.info(f"Telegram received: '{text}' from ID: {sender_id}")
                    
                    # Only process messages from the authorized user
                    if sender_id == str(self.chat_id):
                        if text.startswith("/"):
                            commands.append(text.lower().strip())
                    else:
                        if text.startswith("/"):
                            logger.warning(f"Unauthorized command '{text}' from ID: {sender_id}. Authorized ID is: {self.chat_id}")
                return commands
        except Exception as e:
            logger.error(f"Error polling Telegram updates: {e}")
        return []

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
