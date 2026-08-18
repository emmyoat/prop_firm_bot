# Deploying the Prop Firm Signal Bot to a Cloud VPS

This guide outlines how to deploy your **Signal-Only Bot** (TwelveData + Telegram) to run 24/7 on a cloud server.

Since **no MetaTrader 5** is required, the bot runs lightweight on **Linux (Ubuntu/Debian)** or **Windows VPS**.

---

## 📋 Prerequisites
1. **Cloud VPS** ($4–$6/mo on DigitalOcean, Hetzner, Vultr, AWS, or Linode).
2. **TwelveData API Key** in `.env` (`TWELVEDATA_API_KEY=...`).
3. **Telegram Credentials** in `.env` (`TELEGRAM_TOKEN=...`, `TELEGRAM_CHAT_ID=...`).
4. **GitHub Repo Access** (`https://github.com/emmyoat/prop_firm_bot.git`).

---

## 🚀 Quick Setup on Ubuntu / Debian Linux VPS

### Step 1: Connect to your VPS via SSH
```bash
ssh root@YOUR_SERVER_IP
```

### Step 2: Install Python & Git
```bash
sudo apt update && sudo apt install -y python3 python3-pip git
```

### Step 3: Clone your repository
```bash
git clone https://github.com/emmyoat/prop_firm_bot.git
cd prop_firm_bot
```

### Step 4: Install Python dependencies
```bash
pip3 install -r requirements.txt
```

### Step 5: Configure `.env`
Create your `.env` file on the server:
```bash
nano .env
```
Paste your credentials:
```env
TWELVEDATA_API_KEY=5d8ec10ccd1f4bac9387cea7c5077c5e
TELEGRAM_TOKEN=8538740560:AAEQeNkZCMLAwNehbY56QvuPFvDNB8DPD18
TELEGRAM_CHAT_ID=-1003741948082
```
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## 🔄 Run 24/7 in Background (Using Systemd)

Create a background service so the bot starts automatically and restarts if server reboots:

```bash
sudo nano /etc/systemd/system/propbot.service
```

Paste the following:
```ini
[Unit]
Description=Prop Firm Signal Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/prop_firm_bot
ExecStart=/usr/bin/python3 /root/prop_firm_bot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save and enable the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable propbot
sudo systemctl start propbot
```

### Check Bot Status & Logs:
```bash
# View live logs:
sudo journalctl -u propbot -f

# Check status:
sudo systemctl status propbot
```

---

## 🖥️ Alternative: Running on a Windows VPS
1. Connect via Remote Desktop (RDP).
2. Open PowerShell and clone your repo:
   ```powershell
   git clone https://github.com/emmyoat/prop_firm_bot.git
   cd prop_firm_bot
   ```
3. Create your `.env` file.
4. Run `start_bot.bat`.
