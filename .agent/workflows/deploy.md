---
description: How to deploy and run the Prop Firm Bot on a Windows VPS
---

# Deploying the Bot to a Windows VPS

This guide outlines the steps to get your bot running 24/7 on a Virtual Private Server (VPS).

## 1. Prerequisites
- **Windows VPS** (2GB RAM minimum, Windows Server 2019+).
- **MetaTrader 5** installed and logged into your trading account.
- **Python 3.12** installed on the VPS.

## 2. Setup Steps
1. **Transfer Files**: Copy the bot folder to your VPS desktop.
2. **Install Dependencies**:
   Open PowerShell in the bot folder and run:
   ```powershell
   pip install -r requirements-local.txt
   ```
3. **Configure Environment**:
   - Rename `.env.example` to `.env`.
   - Add your Telegram credentials to `.env`.
4. **Settings Check**:
   - Open `config.yaml`.
   - Ensure `mt5_path` points to the correct `terminal64.exe` location on your VPS.
   - Verify `dry_run: false` if ready for challenges.

## 3. Running the Bot (Background)
To ensure the bot keeps running even if you close the RDS window:
1. Open PowerShell.
2. Run the bot using the provided batch file:
   ```powershell
   .\start_bot.bat
   ```
3. **Important**: Minimize the terminal window, do NOT close it.

## 4. Monitoring
- **Telegram**: The bot will notify you of every entry, TP, and SL hit.
- **Logs**: Monitor `bot_123456_new.log` for real-time heartbeat.
- **Web Dashboard**: If configured, run `python main.py --dashboard` to view stats via ngrok.

## 5. Maintenance
- **Weekly Check**: Refresh the VPS once a week on Saturday to clear memory.
- **MT5 Updates**: Restart the terminal if MT5 prompts for an update.

---
// turbo
*Run the bot now?*: `.\start_bot.bat`
