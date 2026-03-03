"""
Playwright-based scraper for Club Automation class schedules.

Usage:
    # As a module (imported by bot.py):
    results = await fetch_classes(query="3.0", category="Tennis",
                                  locations=["McKinney", "Oak Creek"])

    # Standalone smoke-test:
    python scraper.py
"""

import os
import re
import asyncio
import logging
from typing import List, Dict, Optional, Union

from playwright.async_api import (
    async_playwright,
    Page,
    Frame,
    TimeoutError as PlaywrightTimeoutError,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_URL = "https://impact.clubautomation.com/calendar/classes?tab=by-date"
ALLOWED_DOMAIN = "clubautomation.com"

DEFAULT_QUERY = "3.0"
DEFAULT_CATEGORY = "Tennis"
DEFAULT_LOCATIONS = ["McKinney", "Oak Creek"]

MAX_RESULTS = 30  # hard cap so we never return a massive payload
NAV_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 5_000
RESULTS_WAIT_MS = 2_000

HEADLESS = os.environ.get("DEBUG_HEADFUL", "0") != "1"
SCREENSHOT_ON_FAIL = os.environ.get("DEBUG_SCREENSHOT", "0") == "1"
STRICT_DOMAIN_FILTER = os.environ.get("STRICT_DOMAIN_FILTER", "0") == "1"

PageLike = Union[Page, Frame]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    """Collapse whitespace to single spaces."""
    return re.sub(r"\s+", " ", s).strip()


async def _block_third_party(route, request):
    """Optionally abort requests that leave the target domain."""
    if not STRICT_DOMAIN_FILTER:
        # Default: do not filter; keeps page behavior identical to a real browser.
        await route.continue_()
        return

    url = request.url
    if ALLOWED_DOMAIN in url or url.startswith("data:"):
        await route.continue_()
    else:
        await route.abort()


async def _safe_screenshot(page: Page, name: str = "debug") -> Optional[str]:
    """Best-effort screenshot; returns path or None."""
    try:
        path = f"/tmp/{name}.png"
        await page.screenshot(path=path, full_page=True)
        log.info("Screenshot saved to %s", path)
        return path
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

async def fetch_classes(
    query: str = DEFAULT_QUERY,
    category: str = DEFAULT_CATEGORY,
    locations: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Open the class calendar, fill in filters, and return structured results.
    """
    if locations is None:
        locations = list(DEFAULT_LOCATIONS)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        page = await context.new_page()

        await page.route("**/*", _block_third_party)

        try:
            results = await _scrape(page, query, category, locations)
        except Exception:
            if SCREENSHOT_ON_FAIL:
                await _safe_screenshot(page, "scrape_failure")
            raise
        finally:
            await browser.close()

    return results[:MAX_RESULTS]


async def _scrape(
    page: Page,
    query: str,
    category: str,
    locations: List[str],
) -> List[Dict[str, str]]:
    log.info("Navigating to %s", TARGET_URL)
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

    # Give client-side JS a moment to attach and any iframes to load
    await page.wait_for_timeout(1000)

    # Some Club Automation views render the main content inside an iframe.
    # Find a frame that contains the \"CLASSES\" header and work inside it.
    ctx: PageLike = page
    for frame in page.frames:
        try:
            snippet = await frame.inner_text("body")
        except Exception:
            continue
        if "CLASSES" in snippet and "Search by class name" in snippet:
            ctx = frame
            log.info("Using calendar iframe context: %s", frame.url)
            break
    else:
        log.info("Using top-level page context")

    # --- 1) Search query --------------------------------------------------
    # Try to locate a textbox via ARIA role first (more resilient than raw CSS),
    # then fall back to a generic <input> if needed.
    search_input = None
    try:
        candidate = ctx.get_by_role("textbox").first
        await candidate.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
        search_input = candidate
        log.info("Found search input via role=textbox")
    except PlaywrightTimeoutError:
        log.warning("Role-based textbox lookup timed out; falling back to generic input.")

    if search_input is None:
        try:
            candidate = ctx.locator("input").first
            await candidate.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
            search_input = candidate
            log.info("Found search input via generic input locator")
        except PlaywrightTimeoutError as exc:
            log.error("Could not find any visible input for search: %s", exc)
            raise

    await search_input.click()
    await search_input.fill(query)
    log.info("Filled search query: %s", query)

    # --- 2) Location multi-select ------------------------------------------
    # Open and set locations *before* touching categories so the category
    # dropdown never covers the locations control.
    await _select_locations(ctx, locations)

    # --- 3) Category filter ------------------------------------------------
    # Most UIs here expose an "All Categories" dropdown with options like Tennis.
    # First try clicking that dropdown and selecting via role=checkbox; if that fails,
    # fall back to a simple text click on the word "Tennis".
    try:
        cat_dd = ctx.get_by_text("All Categories", exact=False).first
        await cat_dd.click(timeout=ACTION_TIMEOUT_MS)
        log.info("Opened category dropdown")
        await ctx.wait_for_timeout(300)
        try:
            cat_cb = ctx.get_by_role("checkbox", name=re.compile(category, re.I)).first
            await cat_cb.click(timeout=ACTION_TIMEOUT_MS)
            log.info("Selected category via role=checkbox: %s", category)
        except PlaywrightTimeoutError:
            log.warning("Category '%s' via role=checkbox not found; falling back to text.", category)
            cat_option = ctx.get_by_text(category, exact=False).first
            await cat_option.click(timeout=ACTION_TIMEOUT_MS)
            log.info("Selected category via text: %s", category)
    except (PlaywrightTimeoutError, Exception):
        log.warning("Could not reliably set category '%s'; continuing anyway", category)

    # --- 4) Click Search ---------------------------------------------------
    search_btn = ctx.get_by_role("button", name=re.compile(r"Search\s+Classes", re.I))
    await search_btn.click(timeout=ACTION_TIMEOUT_MS)
    log.info("Clicked Search Classes")

    # Wait for results to render
    await ctx.wait_for_timeout(RESULTS_WAIT_MS)

    # --- 5) Parse results --------------------------------------------------
    body_text = _clean(await ctx.inner_text("body"))
    return _parse_results(body_text)


async def _select_locations(page: PageLike, locations: List[str]) -> None:
    """Try multiple strategies to open the location dropdown and pick items."""
    opened = False

    # Strategy A: click the explicit "All Locations" dropdown header.
    try:
        loc_dd = page.get_by_text("All Locations", exact=False).first
        if await loc_dd.is_visible():
            await loc_dd.click(timeout=ACTION_TIMEOUT_MS)
            opened = True
            log.info("Opened location dropdown via 'All Locations'")
    except (PlaywrightTimeoutError, Exception):
        pass

    # Strategy B: click a visible "Location" / "Locations" label or an existing location.
    if not opened:
        for label in ["Location", "Locations", "Oak Creek", "McKinney"]:
            try:
                el = page.get_by_text(label, exact=False).first
                if await el.is_visible():
                    await el.click(timeout=ACTION_TIMEOUT_MS)
                    opened = True
                    log.info("Opened location dropdown via '%s'", label)
                    break
            except (PlaywrightTimeoutError, Exception):
                continue

    # Strategy C: fall back to the second combobox on the page
    if not opened:
        try:
            await page.get_by_role("combobox").nth(1).click(timeout=ACTION_TIMEOUT_MS)
            opened = True
            log.info("Opened location dropdown via combobox fallback")
        except (PlaywrightTimeoutError, Exception):
            log.warning("Could not open location dropdown")

    # Select each requested location
    for loc in locations:
        try:
            # Prefer explicit checkbox roles – these represent locations.
            loc_cb = page.get_by_role("checkbox", name=re.compile(loc, re.I)).first
            await loc_cb.click(timeout=ACTION_TIMEOUT_MS)
            log.info("Selected location via role=checkbox: %s", loc)
        except PlaywrightTimeoutError:
            # Fallback: click by visible text anywhere in the open menu.
            try:
                opt = page.get_by_text(loc, exact=False).first
                await opt.click(timeout=ACTION_TIMEOUT_MS)
                log.info("Selected location via text: %s", loc)
            except PlaywrightTimeoutError:
                log.warning("Could not select location '%s'", loc)

# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    r"\s*\|\s*"
    r"([A-Za-z]+\s+\d{1,2},\s*\d{4})"
)
_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}(?:am|pm))\s*-\s*(\d{1,2}:\d{2}(?:am|pm))", re.I
)
_TITLE_RE = re.compile(
    # Capture the class title between the time range and the first "Facility:".
    # Use a non-greedy dot pattern to avoid fragile character-class ranges.
    r"\s*([A-Za-z0-9].{2,120}?)\s+Facility:",
    re.I,
)
_FACILITY_RE = re.compile(
    r"Facility:\s*([A-Za-z0-9 \-]+?)(?=\s+(?:Instructor|Department|Location):|$)",
    re.I,
)
_REGOPEN_RE = re.compile(r"(\d+)\s+Registered\s+(\d+)\s+Open", re.I)


def _parse_results(body: str) -> List[Dict[str, str]]:
    date_matches = list(_DATE_RE.finditer(body))
    if not date_matches:
        log.warning("No date headers found in page text")
        return []

    results: List[Dict[str, str]] = []

    for idx, dm in enumerate(date_matches):
        date_str = f"{dm.group(1)} | {dm.group(2)}"
        start = dm.end()
        end = date_matches[idx + 1].start() if idx + 1 < len(date_matches) else len(body)
        section = body[start:end]

        for tm in _TIME_RE.finditer(section):
            hours = f"{tm.group(1).lower()} - {tm.group(2).lower()}"
            lookahead = section[tm.end(): tm.end() + 500]

            # Class title usually appears between the time range and the first
            # \"Facility:\" token on the same row.
            title = "N/A"
            tmatch = _TITLE_RE.search(lookahead)
            if tmatch:
                title = _clean(tmatch.group(1))

            fac = _FACILITY_RE.search(lookahead)
            ro = _REGOPEN_RE.search(lookahead)

            results.append({
                "date": date_str,
                "title": title,
                "hours": hours,
                "facility": fac.group(1).strip() if fac else "N/A",
                "reg_open": (
                    f"{ro.group(1)} Registered {ro.group(2)} Open" if ro else "N/A"
                ),
            })

    log.info("Parsed %d class entries", len(results))
    return results

# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

async def _main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    results = await fetch_classes()
    if not results:
        print("No results found.")

    def _icon(reg_str: str) -> str:
        m = re.search(r"(\d+)\s+Open", reg_str, re.IGNORECASE)
        if not m:
            return "⚪️"
        try:
            open_slots = int(m.group(1))
        except ValueError:
            return "⚪️"
        if open_slots == 0:
            return "❌"
        if open_slots <= 3:
            return "⚠️"
        return "🟢"

    current_date = None
    for r in results:
        if r["date"] != current_date:
            if current_date is not None:
                print()
            current_date = r["date"]
            print(r["date"])

        icon = _icon(r.get("reg_open", ""))
        print(
            f"  - {r.get('title','')}\n"
            f"    {r.get('hours','')}  |  {r.get('facility','')}\n"
            f"    {icon} {r.get('reg_open','')}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
