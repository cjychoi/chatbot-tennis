"""
Telegram bot – /classes command backed by Playwright scraper.

Run:
    TELEGRAM_BOT_TOKEN=<token> python bot.py
"""

import os
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

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

# Location abbreviations accepted as "/class" arguments, e.g. "/class lbh oc".
LOCATION_ALIASES: Dict[str, str] = {
    "lbh": "LB Houston",
    "oc": "Oak Creek",
    "fretz": "Fretz",
    "mc": "McKinney",
    "mk": "McKinney",
}

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
    text = str(text)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _filter_by_day_window(
    items: List[Dict[str, str]], day_window: Optional[int]
) -> List[Dict[str, str]]:
    """
    Keep only items whose date falls within [today, today + day_window - 1].
    `day_window=None` means no filtering (return items unchanged).
    """
    if day_window is None:
        return items

    today = datetime.today().date()
    cutoff = today + timedelta(days=day_window - 1)

    filtered: List[Dict[str, str]] = []
    for it in items:
        try:
            # Example: "Monday | March 02, 2026"
            dt = datetime.strptime(it["date"], "%A | %B %d, %Y").date()
        except Exception:
            continue
        if today <= dt <= cutoff:
            filtered.append(it)
    return filtered


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
        "You can also pass a custom search term, e.g. `/class 4.0`, "
        "`/class today`, or `/class lbh oc` — see /help for details.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Commands*\n"
        "/class — all available classes (default: 3.0 / Intermediate, default locations)\n"
        "/class today — same, but only today's classes\n"
        "/class lbh oc fretz mc — classes for the next 2 days at the given locations "
        "(default query)\n"
        "/class 4.0 — search with a different level/term (default locations, all dates)\n"
        "\n"
        "*Location abbreviations*\n"
        "lbh = LB Houston, oc = Oak Creek, fretz = Fretz, mc/mk = McKinney\n"
        "\n"
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

    # If every remaining word is a recognized location abbreviation
    # (e.g. "/class lbh oc fretz mc"), use those locations with the default
    # query and default to a 2-day (today + tomorrow) window. Otherwise,
    # treat the remaining words as custom OR'd search terms (e.g.
    # "/class 3.0 Intermediate" matches classes containing either term).
    locations: Optional[List[str]] = None
    query_terms: List[str]
    used_location_aliases = False

    if args and all(a.lower() in LOCATION_ALIASES for a in args):
        used_location_aliases = True
        seen = set()
        locations = []
        for a in args:
            loc = LOCATION_ALIASES[a.lower()]
            if loc not in seen:
                seen.add(loc)
                locations.append(loc)
        query_terms = list(DEFAULT_QUERY)
    else:
        query_terms = args if args else list(DEFAULT_QUERY)

    # Determine the date window: "today" always wins if given; otherwise
    # location-alias mode defaults to a 2-day window; plain "/class" shows
    # everything with no date filtering.
    if filter_today:
        day_window: Optional[int] = 1
    elif used_location_aliases:
        day_window = 2
    else:
        day_window = None

    query_display = " or ".join(_escape_md(t) for t in query_terms)
    if locations:
        loc_display = " & ".join(_escape_md(l) for l in locations)
        where_display = f" at *{loc_display}*"
    else:
        where_display = ""
    if day_window == 1:
        when_display = " for today"
    elif day_window == 2:
        when_display = " for the next 2 days"
    else:
        when_display = ""

    msg = await update.message.reply_text(
        f"Searching for *{query_display}* classes{where_display}{when_display}…",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        items = await fetch_classes(query=query_terms, locations=locations)
    except Exception as exc:
        log.exception("Scraper failed")
        await msg.edit_text(f"Scrape failed: {exc}")
        return

    if not items:
        await msg.edit_text("No classes found (try different filters or check selectors).")
        return

    # Optional: restrict to the requested date window.
    if day_window is not None:
        items = _filter_by_day_window(items, day_window)

        if not items:
            await msg.edit_text("No classes found for that date range.")
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
