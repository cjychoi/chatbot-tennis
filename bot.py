"""
Telegram bot – /classes command backed by Playwright scraper.

Run:
    TELEGRAM_BOT_TOKEN=<token> python bot.py
"""

import os
import re
import logging
from datetime import datetime
from typing import List, Dict

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from scraper import fetch_classes, DEFAULT_QUERY

load_dotenv()

log = logging.getLogger(__name__)

TELEGRAM_MSG_LIMIT = 4096
DISPLAY_CAP = 0  # 0 = no cap; show all results

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_classes(items: List[Dict[str, str]]) -> str:
    """
    Render class items grouped by date.
    Returns a Markdown-formatted string.
    """
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for it in items:
        grouped.setdefault(it["date"], []).append(it)

    lines: List[str] = []
    for date, classes in grouped.items():
        lines.append(f"*{_escape_md(date)}*")
        for c in classes:
            title = c.get("title") or ""

            # Derive an availability icon from the "X Registered Y Open" text.
            reg_str = c.get("reg_open") or ""
            open_slots = None
            m = re.search(r"(\d+)\s+Open\b", reg_str, re.IGNORECASE)
            if m:
                try:
                    open_slots = int(m.group(1))
                except ValueError:
                    open_slots = None

            if open_slots is None:
                icon = "⚪️"
            elif open_slots == 0:
                icon = "❌"
            elif open_slots <= 3:
                icon = "⚠️"
            else:
                icon = "🟢"

            # Bullet: bold class title
            lines.append(f"  • *{_escape_md(title)}*")
            lines.append(
                f"     `{_escape_md(c['hours'])}`  —  {_escape_md(c['facility'])}"
            )
            lines.append(f"     {icon} _{_escape_md(reg_str)}_")
        lines.append("")  # blank line between date groups

    return "\n".join(lines).strip()


def _escape_md(text: str) -> str:
    """Escape characters that break Telegram Markdown v1."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _chunk_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> List[str]:
    """Split a long message into chunks that fit Telegram's size limit."""
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split on a blank-line boundary
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey! Use /class to see upcoming tennis classes.\n"
        "You can also pass a custom search term, e.g. `/class 4.0` or `/class today`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Commands*\n"
        "/class — search for classes (default: 3.0 Tennis)\n"
        "/class 4.0 — search with a different level\n"
        "/class today — show only today's classes\n"
        "/help — show this message",
        parse_mode=ParseMode.MARKDOWN,
    )


async def class_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.strip() for a in (context.args or []) if a.strip()]

    # Support "/class today" which filters to today's date.
    filter_today = False
    if args and args[0].lower() == "today":
        filter_today = True
        args = args[1:]

    query = DEFAULT_QUERY
    if args:
        query = " ".join(args).strip()

    msg = await update.message.reply_text(f"Searching for *{_escape_md(query)}* classes…", parse_mode=ParseMode.MARKDOWN)

    try:
        items = await fetch_classes(query=query)
    except Exception as exc:
        log.exception("Scraper failed")
        await msg.edit_text(f"Scrape failed: {exc}")
        return

    if not items:
        await msg.edit_text("No classes found (try different filters or check selectors).")
        return

    # Optional: restrict to classes on today's date.
    if filter_today:
        today = datetime.today().date()
        filtered: List[Dict[str, str]] = []
        for it in items:
            try:
                # Example: "Monday | March 02, 2026"
                dt = datetime.strptime(it["date"], "%A | %B %d, %Y").date()
            except Exception:
                continue
            if dt == today:
                filtered.append(it)
        items = filtered

        if not items:
            await msg.edit_text("No classes found for today.")
            return

    if DISPLAY_CAP and DISPLAY_CAP > 0:
        capped = items[:DISPLAY_CAP]
    else:
        capped = items

    text = _format_classes(capped)

    chunks = _chunk_message(text)
    await msg.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN)
    for extra in chunks[1:]:
        await update.message.reply_text(extra, parse_mode=ParseMode.MARKDOWN)

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Create a bot via @BotFather and export the token."
        )

    # Use separate clients for polling vs outgoing API calls:
    # long-polling can occupy one connection for up to ~10s, so command replies
    # need their own pool to avoid PoolTimeout under load.
    api_request = HTTPXRequest(
        connection_pool_size=8,
        pool_timeout=10.0,
        httpx_kwargs={"trust_env": False},
    )
    polling_request = HTTPXRequest(
        connection_pool_size=2,
        pool_timeout=10.0,
        httpx_kwargs={"trust_env": False},
    )

    app = (
        Application.builder()
        .token(token)
        .request(api_request)
        .get_updates_request(polling_request)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("class", class_cmd))

    log.info("Bot starting (long-polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
