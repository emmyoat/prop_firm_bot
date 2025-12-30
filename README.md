# Prop-Firm & Personal Trading Bot (MT5)

A professional-grade, python-based automated trading bot designed for **Prop Firm Compliance** and **Personal Account Growth**. It connects to MetaTrader 5 (MT5) and executes a "Liquidity Wick" strategy with strict risk management.

## 🚀 Key Features

### 🧠 Strategy: Liquidity Wick
- **Logic**: Identifies liquidity sweeps on H4/D1 timeframes and enters on reversals.
- **Modes**:
    - **SWING**: H4/D1 Analysis (1-2 trades/week)
    - **DAY**: H1/H4 Analysis (1 trade/day)
- **Filters**: News Filter (skips high-impact USD events), Friday Exit (closes trades before weekend).

### 🛡️ Risk Management (Prop Firm Ready)
- **Hard Limits**: Enforces Daily Drawdown and Overall Drawdown limits.
- **Dynamic Risk**: Calculates lot sizes based on strict % equity risk (Default: 2%).
- **Breakeven**: Auto-moves Stop Loss to entry after +20 pips profit.
- **Trailing Stop**: Activates at +45 pips, trails price by 15 pips.
- **Trade Duration**: Enforces minimum trade duration (4 mins) to avoid "scalping" classifications.

### 📊 Tech Stack
- **Language**: Python 3.12+
- **Platform**: MetaTrader 5 (terminal64.exe)
- **Libraries**: `MetaTrader5`, `pandas`, `numpy`, `pyyaml`
- **Notifications**: Telegram Bot integration for live trade updates.

## 🛠️ Installation

1.  **Prerequisites**:
    *   Windows OS (MT5 requirement)
    *   Python 3.10+
    *   MetaTrader 5 Terminal installed and logged in.

2.  **Clone the Repository**:
    ```bash
    git clone https://github.com/yourusername/prop-firm-bot.git
    cd prop-firm-bot
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configuration**:
    *   Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    *   Edit `.env` and add your **Telegram Token** and **Chat ID** (optional but recommended). *Note: MT5 login details are optional in .env if you are already logged into the terminal.*

5.  **Adjust Config**:
    *   Edit `config.yaml` to set your Risk % (default 2.0) and other parameters.

## 📉 Backtesting

Run the built-in backtester (uses live MT5 data):
```bash
python backtest.py
```

## ▶️ Live Trading

1.  **Dry Run (Simulation)**:
    Set `dry_run: true` in `config.yaml`.
    Run: `start_bot.bat`

2.  **Prop Challenge**:
    Set `dry_run: false` in `config.yaml`.
    Run: `start_bot.bat`

## ⚠️ Disclaimer
This software is for educational purposes only. Trading Forex involves substantial risk of loss. Past performance is not indicative of future results. Use at your own risk.
