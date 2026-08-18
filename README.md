# Prop Firm Signal Bot

A signal-only Python bot for market analysis, risk-aware paper-account tracking, and Telegram alerts. It does not place trades and does not require MetaTrader 5.

## Runtime

- Market data: TwelveData REST API
- Strategy: liquidity-wick signals with optional SMC confluence filtering
- Risk: virtual account, daily loss limit, overall drawdown limit, profit target, and lot-size recommendations
- Notifications: Telegram with authorized-chat filtering and durable polling offsets
- Persistence: SQLite runtime state
- Platform: Windows is supported by [`start_bot.bat`](start_bot.bat)

## Installation

1. Install Python 3.10 or newer.
2. Install runtime dependencies:

   ```text
   python -m pip install -r requirements.txt
   ```

3. Copy [`.env.example`](.env.example) to `.env` and set the TwelveData key. Telegram credentials are required for alerts and optional for data-only operation.
4. Review [`config.yaml`](config.yaml), especially `system.symbol_list`, `data_source`, `runtime`, `health`, and `telegram`.

## Running

For a supervised Windows process:

```text
start_bot.bat
```

For direct execution:

```text
python main.py
```


## Durable State and Recovery

Runtime state is stored in [`runtime_state.db`](runtime_state.db). The database contains:

- Risk state, cumulative paper P&L, daily counters, and high-water mark
- Signal deduplication claims by symbol, label, and candle
- Telegram update offsets per authorized chat
- Health component status and metadata
- Process startup, heartbeat, and clean-shutdown metadata

The database is created automatically and checked with SQLite integrity validation at startup. Back it up before manual recovery or migration. Do not delete it during normal restarts: doing so resets deduplication, risk history, and Telegram polling offsets.

Daily counters roll over using UTC dates after a restart or downtime. Cumulative paper P&L and drawdown history remain persistent.


## Testing and Verification

Install local development dependencies:

```text
python -m pip install -r requirements-local.txt
```

Run the maintained test suite:

```text
python -m pytest -q
```

The repository includes deterministic tests for SQLite restart state, UTC rollover, TwelveData retries and cache behavior, Telegram offsets and authorization, and health transitions. Pytest discovery is scoped by [`pytest.ini`](pytest.ini) to the `tests` directory so manual scripts are not collected.

Run syntax validation:

```text
python -m py_compile main.py src\\utils\\state_store.py src\\utils\\health.py src\\risk\\risk_manager.py src\\data\\twelvedata_loader.py src\\utils\\notifications.py
```

## Operational Assumptions

- The bot is signal-only; no order execution or broker credentials are used by the runtime.
- TwelveData timestamps and request budgets are interpreted in UTC.
- Telegram chat IDs are treated as strings to avoid numeric formatting mismatches.
- Stale market data is acceptable only inside the explicit configured bound; beyond that bound the bot fails closed for signal generation.
- SQLite is local durable state, not a multi-process database. Run one bot instance per state database.
- The Windows supervisor is intended for unattended operation and uses bounded restart delays; persistent configuration or credential failures still require operator intervention.

## Disclaimer

This software is for educational and research purposes. Trading and trading signals involve substantial risk of loss. Validate the strategy, provider limits, and operational controls before relying on the output.
