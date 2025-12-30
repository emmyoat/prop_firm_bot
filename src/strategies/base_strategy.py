from abc import ABC, abstractmethod
import pandas as pd
from src.models import Signal

class Strategy(ABC):
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame, symbol: str) -> Signal:
        """
        Analyzes the provided dataframe and returns a Signal.
        """
        pass
