import yaml
import os
import logging
from dotenv import load_dotenv


def load_config(config_path="config.yaml"):
    """
    Loads configuration from a YAML file.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        try:
            config = yaml.safe_load(f)
            return config
        except yaml.YAMLError as e:
            logging.error(f"Error parsing config file: {e}")
            raise e


def load_credentials(env_path=".env"):
    """
    Loads environment variables from a specific .env file.
    MT5 credentials are optional (bot is signal-only).
    """
    load_dotenv(env_path)

    return {
        # Telegram
        "telegram_token":      os.getenv("TELEGRAM_TOKEN"),
        "telegram_chat_id":    os.getenv("TELEGRAM_CHAT_ID"),
        # TwelveData
        "twelvedata_api_key":  os.getenv("TWELVEDATA_API_KEY"),
    }
