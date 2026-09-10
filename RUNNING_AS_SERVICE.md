# Running bot.py in the Background (systemd)

`bot.py` is **not** started manually with `nohup`/`tmux` — it runs as a
systemd service, so it starts on boot and restarts automatically if it
crashes.

## Service file

Location: `/etc/systemd/system/tennis-bot.service`

```ini
[Unit]
Description=Tennis Class Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=andrewpi
WorkingDirectory=/home/andrewpi/documents/chatbot-tennis
ExecStart=/home/andrewpi/documents/chatbot-tennis/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Initial setup (already done, for reference)

```bash
# 1. Create the unit file above at /etc/systemd/system/tennis-bot.service
sudo nano /etc/systemd/system/tennis-bot.service

# 2. Reload systemd so it picks up the new unit
sudo systemctl daemon-reload

# 3. Enable it to start automatically on boot
sudo systemctl enable tennis-bot.service

# 4. Start it now
sudo systemctl start tennis-bot.service
```

## Checking status

```bash
# Quick status (state, uptime, PID, last log lines)
systemctl status tennis-bot.service

# Is it enabled to start on boot?
systemctl is-enabled tennis-bot.service

# Follow logs live
journalctl -u tennis-bot.service -f

# Last 50 log lines
journalctl -u tennis-bot.service -n 50

# Confirm the process itself is alive
pgrep -af bot.py
```

## Stopping / killing it

```bash
# Normal stop (preferred — systemd manages the process cleanly)
sudo systemctl stop tennis-bot.service

# Restart (e.g. after editing bot.py or .env)
sudo systemctl restart tennis-bot.service

# Temporarily disable auto-start on boot (does not stop a running instance)
sudo systemctl disable tennis-bot.service
```

Because `Restart=on-failure` is set, killing the process directly with
`kill <PID>` will make systemd restart it a few seconds later. To actually
stop the bot, use `systemctl stop`, not a raw `kill`.

If systemd itself is somehow not managing it (e.g. it was started manually
outside the unit), fall back to:

```bash
pgrep -af bot.py     # find the PID
kill <PID>           # graceful stop
kill -9 <PID>        # force kill if it won't stop
```

## Notes

- Working directory: `/home/andrewpi/documents/chatbot-tennis`
- Runs under the project's own `.venv` interpreter, not system Python.
- Secrets (Telegram bot token) live in `.env` in this folder and are loaded
  via `python-dotenv` — never hardcode the token in the unit file or in logs.
