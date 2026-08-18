"""
Risk Manager — Signal-Only / Paper Account Mode
================================================
Works entirely without MT5. Uses a configured virtual balance for:
  - Lot size recommendations (included in signal output)
  - Daily drawdown tracking (simulated from paper trades)
  - Session / spread / news filters

Virtual account state is persisted in risk_state_<magic>.json
so drawdown tracking survives bot restarts.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple
from math import floor

from src.utils.state_store import StateStore

logger = logging.getLogger("PropBot.Risk")


@dataclass
class RiskConfig:
    account_equity_risk_pct:   float
    max_daily_loss_pct:        float
    max_overall_drawdown_pct:  float
    max_spread_points:         int
    martingale_multiplier:     float
    profit_target_daily_pct:   float
    max_lot_size:              Optional[float] = 5.0
    spread_limit_map:          Optional[dict]  = None
    trailing_stop_enabled:         Optional[bool] = True
    trailing_stop_activation_pips: Optional[int] = 100
    trailing_stop_distance_pips:   Optional[int] = 40
    friday_exit_hour:              Optional[int] = 21
    min_trade_duration_seconds:    Optional[int] = 240
    pending_order_expiry_hours:    Optional[int] = 4
    symbol_risk_map:               Optional[dict] = None


class RiskManager:
    """
    Paper / signal-only risk manager.
    All methods that previously required mt5.account_info() now accept
    a simple numeric `current_equity` float instead.
    """

    def __init__(self, config: dict, state_store: Optional[StateStore] = None):
        self.config = RiskConfig(**config["risk"])

        # Virtual paper account
        virtual_cfg = config.get("virtual_account", {})
        self._initial_balance: float = virtual_cfg.get("balance", 10_000.0)
        state_path = config.get("runtime", {}).get("state_db_path", "runtime_state.db")
        self.state_store = state_store or StateStore(state_path)

        # Runtime state
        self.daily_starting_equity: float = self._initial_balance
        self.initial_balance:       float = self._initial_balance
        self.high_water_mark:       float = self._initial_balance
        self.magic_number:          int   = config["system"]["magic_number"]

        # Paper trade tracking
        self.paper_pnl:      float = 0.0   # Cumulative simulated P&L
        self.daily_pnl:      float = 0.0   # Today's simulated P&L
        self.signals_today:  int   = 0
        self.wins_today:     int   = 0
        self.losses_today:   int   = 0
        self.trading_date:   str   = datetime.now(timezone.utc).date().isoformat()

    # ── Initialisation ────────────────────────────────────────────────────────

    def initialize_state(self):
        """Load the persisted account snapshot and apply any missed UTC rollover."""
        state = self.state_store.get_risk_state(self.magic_number)
        if state:
            self.high_water_mark = float(state["high_water_mark"])
            self.paper_pnl = float(state["paper_pnl"])
            self.daily_starting_equity = float(state["daily_starting_equity"])
            self.daily_pnl = float(state["daily_pnl"])
            self.signals_today = int(state["signals_today"])
            self.wins_today = int(state["wins_today"])
            self.losses_today = int(state["losses_today"])
            self.trading_date = state.get("trading_date") or self.trading_date
            logger.info(
                f"RiskManager: Loaded state — HWM={self.high_water_mark:.2f}, "
                f"PaperPnL={self.paper_pnl:.2f}, Date={self.trading_date}"
            )
        else:
            self._save_state()

        self.ensure_daily_rollover()
        logger.info(
            f"RiskManager initialised | Virtual Balance: ${self._initial_balance:,.2f} | HWM: ${self.high_water_mark:,.2f}"
        )

    def _current_equity(self) -> float:
        """Virtual equity = initial balance + cumulative paper P&L."""
        return self._initial_balance + self.paper_pnl

    def reset_daily(self, trading_date: Optional[str] = None):
        """Reset daily tracking for a new UTC trading date."""
        self.daily_starting_equity = self._current_equity()
        self.daily_pnl      = 0.0
        self.signals_today  = 0
        self.wins_today     = 0
        self.losses_today   = 0
        self.trading_date = trading_date or datetime.now(timezone.utc).date().isoformat()
        self._save_state()
        logger.info(f"RiskManager: Daily stats reset for {self.trading_date}.")

    def ensure_daily_rollover(self, now: Optional[datetime] = None) -> bool:
        """Apply a missed UTC rollover after midnight or process downtime."""
        current_date = (now or datetime.now(timezone.utc)).date().isoformat()
        if current_date != self.trading_date:
            self.reset_daily(current_date)
            return True
        return False

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self):
        self.state_store.save_risk_state({
            "magic_number": self.magic_number,
            "initial_balance": self._initial_balance,
            "high_water_mark": self.high_water_mark,
            "paper_pnl": self.paper_pnl,
            "daily_starting_equity": self.daily_starting_equity,
            "daily_pnl": self.daily_pnl,
            "signals_today": self.signals_today,
            "wins_today": self.wins_today,
            "losses_today": self.losses_today,
            "trading_date": self.trading_date,
        })

    # ── Paper trade recording ─────────────────────────────────────────────────

    def record_signal_sent(self) -> None:
        """Persist a delivered signal without treating it as a closed trade."""
        self.ensure_daily_rollover()
        self.signals_today += 1
        self._save_state()

    def record_paper_trade(self, pnl: float):
        """
        Records a paper trade outcome (TP hit = positive, SL hit = negative).
        Updates virtual equity and drawdown tracking.
        """
        self.ensure_daily_rollover()
        self.paper_pnl  += pnl
        self.daily_pnl  += pnl

        if pnl >= 0:
            self.wins_today += 1
        else:
            self.losses_today += 1

        self.update_high_water_mark(self._current_equity())
        self._save_state()

        logger.info(
            f"Paper trade recorded: PnL=${pnl:+.2f} | Equity=${self._current_equity():,.2f} | HWM=${self.high_water_mark:,.2f}"
        )

    def update_high_water_mark(self, current_equity: float):
        """Updates HWM if equity has risen."""
        if current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
            self._save_state()

    # ── Drawdown / risk checks ────────────────────────────────────────────────

    def get_drawdown_metrics(self, current_equity: Optional[float] = None) -> dict:
        """Returns daily and overall drawdown percentages."""
        equity = current_equity if current_equity is not None else self._current_equity()

        daily_dd_pct = 0.0
        if self.daily_starting_equity > 0 and equity > 0.1:
            daily_loss   = self.daily_starting_equity - equity
            daily_dd_pct = (daily_loss / self.daily_starting_equity) * 100.0

        overall_dd_pct = 0.0
        if self.high_water_mark > 0 and equity > 0.1:
            overall_loss    = self.high_water_mark - equity
            overall_dd_pct  = (overall_loss / self.high_water_mark) * 100.0

        return {
            "daily_dd_pct":   max(0.0, daily_dd_pct),
            "overall_dd_pct": max(0.0, overall_dd_pct),
            "hwm":            self.high_water_mark,
            "equity":         equity,
        }

    def check_emergency_exit(self) -> Tuple[bool, str]:
        """Checks if virtual drawdown limits have been breached."""
        metrics = self.get_drawdown_metrics()

        if metrics["daily_dd_pct"] >= self.config.max_daily_loss_pct:
            return True, f"Daily Drawdown Limit Hit: {metrics['daily_dd_pct']:.2f}% >= {self.config.max_daily_loss_pct}%"

        if metrics["overall_dd_pct"] >= self.config.max_overall_drawdown_pct:
            return True, f"Overall Drawdown Limit Hit: {metrics['overall_dd_pct']:.2f}% >= {self.config.max_overall_drawdown_pct}%"

        return False, ""

    def check_profit_target(self) -> Tuple[bool, str]:
        """Checks if the daily profit target has been hit."""
        if self.daily_starting_equity <= 0:
            return False, ""

        profit     = self._current_equity() - self.daily_starting_equity
        profit_pct = (profit / self.daily_starting_equity) * 100.0

        if profit_pct >= self.config.profit_target_daily_pct:
            return True, f"Daily Profit Target Hit: {profit_pct:.2f}% >= {self.config.profit_target_daily_pct}%"

        return False, ""

    def check_signal_allowed(self, symbol: str, spread_estimate: float = 0.0) -> Tuple[bool, str]:
        """
        Validates whether a new signal should be acted on.
        Replaces the old check_trade_allowed(account_info, symbol_info, spread_points).
        """
        # 1. Drawdown limits
        breached, reason = self.check_emergency_exit()
        if breached:
            return False, reason

        # 2. Profit target
        target_hit, msg = self.check_profit_target()
        if target_hit:
            return False, f"Daily profit target already hit — {msg}"

        # 3. Spread check (if caller provides an estimate)
        if spread_estimate > 0:
            limit_map   = self.config.spread_limit_map or {}
            max_spread  = limit_map.get(symbol, self.config.max_spread_points)
            # Partial key match for indices (e.g. "NAS100" in "NAS100+")
            if symbol not in limit_map:
                for key in limit_map:
                    if key in symbol:
                        max_spread = limit_map[key]
                        break

            if spread_estimate > max_spread:
                return False, f"Spread too high for {symbol}: {spread_estimate:.1f} > {max_spread}"

        return True, ""

    # ── Lot size calculation ──────────────────────────────────────────────────

    def calculate_lot_size(
        self,
        stop_loss_dist:        float,
        tick_value:            float  = 10.0,
        tick_size:             float  = 0.0001,
        loss_per_lot_override: Optional[float] = None,
        symbol:                Optional[str]   = None,
        account_balance:       Optional[float] = None,
    ) -> float:
        """
        Calculates recommended lot size based on virtual risk percentage.
        `account_balance` defaults to current virtual equity if not supplied.
        """
        if stop_loss_dist <= 0:
            return 0.0

        balance = account_balance if account_balance is not None else self._current_equity()

        # Per-symbol risk override
        risk_pct = self.config.account_equity_risk_pct
        if symbol and self.config.symbol_risk_map and symbol in self.config.symbol_risk_map:
            risk_pct = self.config.symbol_risk_map[symbol]
            logger.info(f"Risk: Using override {risk_pct}% for {symbol}")

        risk_amount = balance * (risk_pct / 100.0)

        # Loss per lot
        if loss_per_lot_override is not None and loss_per_lot_override > 0:
            loss_per_lot = loss_per_lot_override
        else:
            loss_per_lot = (stop_loss_dist / tick_size) * tick_value if tick_size > 0 else 0.0

        if loss_per_lot <= 0:
            logger.warning(f"Risk: loss_per_lot <= 0 ({loss_per_lot}). Cannot size.")
            return 0.0

        raw_lot = risk_amount / loss_per_lot
        lot_floored = floor(raw_lot * 100) / 100.0

        if lot_floored < 0.01:
            if raw_lot >= 0.0085:
                return 0.01
            return 0.0

        max_lot = self.config.max_lot_size or 10.0
        lot = min(lot_floored, max_lot)

        logger.info(
            f"Risk calc | Balance=${balance:.2f} | Risk={risk_pct}% (${risk_amount:.2f}) | "
            f"SL_dist={stop_loss_dist:.5f} | Loss/lot=${loss_per_lot:.2f} | Lot={lot}"
        )
        return lot

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def virtual_equity(self) -> float:
        return self._current_equity()

    @property
    def virtual_balance(self) -> float:
        return self._initial_balance

    def get_summary(self) -> dict:
        metrics = self.get_drawdown_metrics()
        return {
            "virtual_balance":   self._initial_balance,
            "virtual_equity":    self._current_equity(),
            "paper_pnl":         self.paper_pnl,
            "daily_pnl":         self.daily_pnl,
            "high_water_mark":   self.high_water_mark,
            "daily_dd_pct":      metrics["daily_dd_pct"],
            "overall_dd_pct":    metrics["overall_dd_pct"],
            "signals_today":     self.signals_today,
            "wins_today":        self.wins_today,
            "losses_today":      self.losses_today,
        }
