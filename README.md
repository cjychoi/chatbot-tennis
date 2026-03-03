# Tennis Class Bot

Telegram bot that scrapes the [Club Automation class calendar](https://impact.clubautomation.com/calendar/classes?tab=by-date) and returns upcoming tennis classes.

## Quick Start

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright's Chromium browser

```bash
playwright install chromium
```

> On a fresh Linux server you may also need system deps:
> `playwright install-deps chromium`

### 4. Set your Telegram bot token

Create a bot via [@BotFather](https://t.me/BotFather) on Telegram.  
Copy the token and either:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
```

…or create a `.env` file (see `.env.example`):

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```

### 5. Run the bot

```bash
python bot.py
```

The bot uses **long polling** — no webhook, no open port needed.

## Usage (Telegram)

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/class` | Search for 3.0 Tennis classes at McKinney + Oak Creek |
| `/class 4.0` | Search with a custom level |
| `/class today` | Only show today's classes (date filter) |
| `/help` | Show available commands |

## Smoke-Test the Scraper (no Telegram needed)

```bash
python scraper.py
```

This runs the scraper standalone and prints results to stdout.

## Debug Mode

| Env var | Effect |
|---------|--------|
| `DEBUG_HEADFUL=1` | Launch Chromium with a visible window |
| `DEBUG_SCREENSHOT=1` | Save `/tmp/scrape_failure.png` on error |

Example:

```bash
DEBUG_HEADFUL=1 DEBUG_SCREENSHOT=1 python scraper.py
```

## Project Structure

```
chatbot_tennis/
├── bot.py              # Telegram bot (long-polling)
├── scraper.py          # Playwright scraper
├── requirements.txt    # Python dependencies
├── .env.example        # Template for secrets
└── README.md           # This file
```

## Deployment Notes

- Works on macOS (Apple Silicon & Intel) and Linux (x86_64, arm64).
- For an always-on setup, run inside `tmux`/`screen`, or create a systemd service.
- On a Raspberry Pi, install Chromium via `playwright install chromium` (arm64 builds are supported).
- Memory usage is ~150-250 MB while scraping (Chromium is launched per-request and shut down after).

## Possible Improvements

- **Fast-path API**: inspect the Club Automation network tab for a JSON endpoint; if one exists, bypass Playwright entirely and use `httpx` for sub-second responses.
- **Caching**: cache results for N minutes so repeated `/classes` calls don't re-launch Chromium.
- **Inline buttons**: let users pick level/location interactively via Telegram inline keyboards.
