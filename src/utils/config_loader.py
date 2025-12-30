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
    """
    load_dotenv(env_path)
    
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    
    if not all([login, password, server]):
        logging.warning("MT5 credentials not fully set in .env")
        
    return {
        "login": int(login) if login else None,
        "password": password,
        "server": server,
        "telegram_token": os.getenv("TELEGRAM_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID")
    }
