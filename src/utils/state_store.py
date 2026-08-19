"""
Durable runtime state for the signal-only bot.

SQLite is used so related updates can be committed transactionally and survive
process restarts without relying on several independently written JSON files.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("PropBot.State")

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    """Small SQLite repository for process, risk, deduplication, and health state."""

    def __init__(self, path: str = "runtime_state.db"):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS risk_state (
                    magic_number INTEGER PRIMARY KEY,
                    initial_balance REAL NOT NULL,
                    daily_starting_equity REAL NOT NULL,
                    high_water_mark REAL NOT NULL,
                    paper_pnl REAL NOT NULL DEFAULT 0,
                    daily_pnl REAL NOT NULL DEFAULT 0,
                    signals_today INTEGER NOT NULL DEFAULT 0,
                    wins_today INTEGER NOT NULL DEFAULT 0,
                    losses_today INTEGER NOT NULL DEFAULT 0,
                    trading_date TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signal_dedup (
                    dedup_key TEXT PRIMARY KEY,
                    candle_time TEXT NOT NULL,
                    emitted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_state (
                    state_key TEXT PRIMARY KEY,
                    last_update_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_state (
                    component TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT,
                    last_failure_at TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_state (
                    state_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS active_trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    label TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry REAL NOT NULL,
                    sl REAL NOT NULL,
                    tp REAL NOT NULL,
                    initial_sl REAL NOT NULL,
                    current_sl REAL NOT NULL,
                    is_stop_order INTEGER NOT NULL DEFAULT 1,
                    triggered INTEGER NOT NULL DEFAULT 0,
                    be_alerted INTEGER NOT NULL DEFAULT 0,
                    last_trail_sl REAL NOT NULL DEFAULT 0,
                    highest_price REAL NOT NULL,
                    lowest_price REAL NOT NULL,
                    lot_size REAL NOT NULL DEFAULT 0.01,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        """Compatibility hook; connections are intentionally short-lived."""

    def get_risk_state(self, magic_number: int) -> Optional[dict[str, Any]]:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM risk_state WHERE magic_number = ?", (magic_number,)
            ).fetchone()
        return dict(row) if row else None

    def save_risk_state(self, state: dict[str, Any]) -> None:
        now = state.get("updated_at") or utc_now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO risk_state(
                    magic_number, initial_balance, daily_starting_equity,
                    high_water_mark, paper_pnl, daily_pnl, signals_today,
                    wins_today, losses_today, trading_date, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(magic_number) DO UPDATE SET
                    initial_balance=excluded.initial_balance,
                    daily_starting_equity=excluded.daily_starting_equity,
                    high_water_mark=excluded.high_water_mark,
                    paper_pnl=excluded.paper_pnl,
                    daily_pnl=excluded.daily_pnl,
                    signals_today=excluded.signals_today,
                    wins_today=excluded.wins_today,
                    losses_today=excluded.losses_today,
                    trading_date=excluded.trading_date,
                    updated_at=excluded.updated_at
                """,
                (
                    state["magic_number"],
                    state["initial_balance"],
                    state["daily_starting_equity"],
                    state["high_water_mark"],
                    state["paper_pnl"],
                    state["daily_pnl"],
                    state["signals_today"],
                    state["wins_today"],
                    state["losses_today"],
                    state.get("trading_date"),
                    now,
                ),
            )

    def claim_signal(self, dedup_key: str, candle_time: str) -> bool:
        """Atomically claim a signal candle; False means it was already emitted."""
        with self._connection() as db:
            existing = db.execute(
                "SELECT candle_time FROM signal_dedup WHERE dedup_key = ?",
                (dedup_key,),
            ).fetchone()
            if existing and existing["candle_time"] == candle_time:
                return False
            db.execute(
                """
                INSERT INTO signal_dedup(dedup_key, candle_time, emitted_at)
                VALUES(?, ?, ?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                    candle_time=excluded.candle_time,
                    emitted_at=excluded.emitted_at
                """,
                (dedup_key, candle_time, utc_now()),
            )
            return True

    def release_signal(self, dedup_key: str, candle_time: str) -> None:
        """Release a claim only when it still refers to the failed delivery."""
        with self._connection() as db:
            db.execute(
                "DELETE FROM signal_dedup WHERE dedup_key = ? AND candle_time = ?",
                (dedup_key, candle_time),
            )

    def get_telegram_offset(self, state_key: str = "default") -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT last_update_id FROM telegram_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        return int(row["last_update_id"]) if row else 0

    def save_telegram_offset(self, update_id: int, state_key: str = "default") -> None:
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO telegram_state(state_key, last_update_id, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    last_update_id=excluded.last_update_id,
                    updated_at=excluded.updated_at
                """,
                (state_key, int(update_id), utc_now()),
            )

    def set_runtime_value(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, sort_keys=True)
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO runtime_state(state_key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, serialized, utc_now()),
            )

    def get_runtime_value(self, key: str, default: Any = None) -> Any:
        with self._connection() as db:
            row = db.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def record_health(
        self,
        component: str,
        status: str,
        reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        now = utc_now()
        with self._connection() as db:
            previous = db.execute(
                "SELECT last_success_at, last_failure_at, consecutive_failures FROM health_state WHERE component = ?",
                (component,),
            ).fetchone()
            failures = int(previous["consecutive_failures"]) if previous else 0
            if status == "healthy":
                failures = 0
                last_success = now
                last_failure = previous["last_failure_at"] if previous else None
            else:
                failures += 1
                last_success = previous["last_success_at"] if previous else None
                last_failure = now
            db.execute(
                """
                INSERT INTO health_state(
                    component, status, reason, last_success_at, last_failure_at,
                    consecutive_failures, metadata_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    status=excluded.status,
                    reason=excluded.reason,
                    last_success_at=excluded.last_success_at,
                    last_failure_at=excluded.last_failure_at,
                    consecutive_failures=excluded.consecutive_failures,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    component,
                    status,
                    reason,
                    last_success,
                    last_failure,
                    failures,
                    json.dumps(metadata or {}, sort_keys=True),
                    now,
                ),
            )

    def get_health(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM health_state ORDER BY component"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except json.JSONDecodeError:
                item["metadata"] = {}
            result.append(item)
        return result

    def backup(self, destination: str) -> None:
        """Create a consistent SQLite backup for operator recovery."""
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source:
            target = sqlite3.connect(str(destination_path))
            try:
                source.backup(target)
            finally:
                target.close()

    def integrity_check(self) -> bool:
        with self._connection() as db:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok"

    # ── Active Trade Tracker ──────────────────────────────────────────────────

    def save_active_trade(self, trade: dict[str, Any]) -> None:
        now = trade.get("updated_at") or utc_now()
        created = trade.get("created_at") or now
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO active_trades(
                    trade_id, symbol, label, direction, entry, sl, tp,
                    initial_sl, current_sl, is_stop_order, triggered,
                    be_alerted, last_trail_sl, highest_price, lowest_price,
                    lot_size, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    entry=excluded.entry,
                    sl=excluded.sl,
                    tp=excluded.tp,
                    current_sl=excluded.current_sl,
                    triggered=excluded.triggered,
                    be_alerted=excluded.be_alerted,
                    last_trail_sl=excluded.last_trail_sl,
                    highest_price=excluded.highest_price,
                    lowest_price=excluded.lowest_price,
                    lot_size=excluded.lot_size,
                    updated_at=excluded.updated_at
                """,
                (
                    trade["trade_id"],
                    trade["symbol"],
                    trade["label"],
                    trade["direction"],
                    float(trade["entry"]),
                    float(trade["sl"]),
                    float(trade["tp"]),
                    float(trade.get("initial_sl", trade["sl"])),
                    float(trade.get("current_sl", trade["sl"])),
                    1 if trade.get("is_stop_order", True) else 0,
                    1 if trade.get("triggered", False) else 0,
                    1 if trade.get("be_alerted", False) else 0,
                    float(trade.get("last_trail_sl", 0.0)),
                    float(trade.get("highest_price", trade["entry"])),
                    float(trade.get("lowest_price", trade["entry"])),
                    float(trade.get("lot_size", 0.01)),
                    created,
                    now,
                ),
            )

    def get_active_trades(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            if symbol:
                rows = db.execute(
                    "SELECT * FROM active_trades WHERE symbol = ? ORDER BY created_at ASC",
                    (symbol,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM active_trades ORDER BY created_at ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def remove_active_trade(self, trade_id: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM active_trades WHERE trade_id = ?", (trade_id,))
