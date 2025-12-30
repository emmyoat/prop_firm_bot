import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("PropBot.Risk")

@dataclass
class RiskConfig:
    account_equity_risk_pct: float
    max_daily_loss_pct: float
    max_overall_drawdown_pct: float
    max_spread_points: int
    martingale_multiplier: float
    profit_target_daily_pct: float
    trailing_stop_activation_pips: Optional[int] = 50
    breakeven_activation_pips: Optional[int] = 20
    trailing_step_pips: Optional[int] = 5
    friday_exit_hour: Optional[int] = 21
    min_trade_duration_seconds: Optional[int] = 240

class RiskManager:
    def __init__(self, config: dict):
        self.config = RiskConfig(**config['risk'])
        self.daily_starting_equity = 0.0
        self.daily_loss = 0.0
        self.high_water_mark = 0.0
        self.initial_balance = 0.0

    def initialize_state(self, account_info, magic_number: int):
        """Initializes the manager with current account state and loads saved HWM."""
        self.daily_starting_equity = account_info.equity
        self.initial_balance = account_info.balance
        self.magic_number = magic_number
        
        # Load HWM from file if exists
        import json
        import os
        hwm_file = f"risk_state_{self.magic_number}.json"
        if os.path.exists(hwm_file):
            try:
                with open(hwm_file, "r") as f:
                    state = json.load(f)
                    self.high_water_mark = state.get("high_water_mark", account_info.balance)
            except:
                self.high_water_mark = account_info.balance
        else:
            self.high_water_mark = account_info.balance
            
        logger.info(f"RiskManager Initialized: Daily Start Equity={self.daily_starting_equity}, HWM={self.high_water_mark}")

    def update_high_water_mark(self, current_equity: float):
        """Updates the high-water mark and saves to file."""
        if current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
            try:
                import json
                state_file = f"risk_state_{self.magic_number}.json"
                with open(state_file, "w") as f:
                    json.dump({"high_water_mark": self.high_water_mark}, f)
            except Exception as e:
                logger.error(f"Failed to save HWM: {e}")

    def get_drawdown_metrics(self, current_equity: float) -> dict:
        """Calculates current drawdown percentages."""
        # 1. Daily Drawdown (Relative to start of day)
        daily_dd_pct = 0.0
        if self.daily_starting_equity > 0:
            daily_loss = self.daily_starting_equity - current_equity
            daily_dd_pct = (daily_loss / self.daily_starting_equity) * 100.0

        # 2. Overall Trailing Drawdown (Relative to HWM)
        overall_dd_pct = 0.0
        if self.high_water_mark > 0:
            overall_loss = self.high_water_mark - current_equity
            overall_dd_pct = (overall_loss / self.high_water_mark) * 100.0

        return {
            "daily_dd_pct": max(0.0, daily_dd_pct),
            "overall_dd_pct": max(0.0, overall_dd_pct),
            "hwm": self.high_water_mark
        }

    def check_emergency_exit(self, account_info) -> Tuple[bool, str]:
        """Checks if any drawdown limits have been breached."""
        metrics = self.get_drawdown_metrics(account_info.equity)
        
        # Check Daily Limit
        if metrics['daily_dd_pct'] >= self.config.max_daily_loss_pct:
            return True, f"Daily Drawdown Limit Hit: {metrics['daily_dd_pct']:.2f}%"

        # Check Overall Limit (Trailing)
        if metrics['overall_dd_pct'] >= self.config.max_overall_drawdown_pct:
            return True, f"Overall Trailing Drawdown Limit Hit: {metrics['overall_dd_pct']:.2f}%"

        return False, ""

    def check_trade_allowed(self, account_info, symbol_info, spread_points: float) -> bool:
        """Validates if a new trade is allowed based on Prop Firm rules."""
        # Check emergency exit first
        breached, reason = self.check_emergency_exit(account_info)
        if breached:
            logger.warning(reason)
            return False

        # 3. Check Spread
        if spread_points > self.config.max_spread_points:
            logger.warning(f"Spread too high: {spread_points} > {self.config.max_spread_points}")
            return False

        return True

    def calculate_lot_size(self, account_balance: float, stop_loss_dist: float, tick_value: float, tick_size: float) -> float:
        """
        Calculates dynamic lot size based on risk percentage.
        Formula: RiskAmount / ((StopLossDistance / TickSize) * TickValue)
        """
        if stop_loss_dist <= 0:
            return 0.0
            
        risk_amount = account_balance * (self.config.account_equity_risk_pct / 100.0)
        
        # Loss per 1 lot if SL hit = (Distance / TickSize) * TickValue
        # This is the most accurate formula for MT5 across all assets
        loss_per_lot = (stop_loss_dist / tick_size) * tick_value
        
        if loss_per_lot <= 0:
            return 0.0
            
        lot_size = risk_amount / loss_per_lot
        
        # Round to 2 decimals
        lot_size = floor(lot_size * 100) / 100.0
        
        if lot_size < 0.01: 
            return 0.0
            
        return lot_size

from math import floor
