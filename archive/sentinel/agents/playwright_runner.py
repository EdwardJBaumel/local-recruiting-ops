"""
Playwright Runner -- headless browser scraper for SPA career pages
==================================================================

What this file is
-----------------
The ONLY place that knows how to drive a headless browser. Given a
tenant config from tenants.py and a list of search keywords, it:

    1. Launches Chromium (via Playwright).
    2. Visits the tenant's URL pattern once per keyword per page.
    3. Waits for the jobs to render.
    4. Scrolls / paginates to load more (depending on strategy).
    5. Extracts each job card using the CSS selectors from the config.
    6. Returns a list of plain dicts -- one per job.
    7. Closes the browser cleanly, even on exceptions.

Why this file is small on purpose
---------------------------------
The "what to scrape" lives in tenants.py. The "how to scrape" lives
here. If you're fixing a broken site, 9/10 times the fix is in
tenants.py (a selector). You should only touch this file when the
browser behaviour itself needs to change (e.g. adding a new
scroll_strategy).

Optional dependency
-------------------
Playwright is heavy (~300MB for Chromium). We import it lazily inside
fetch_spa() so users who only run the fast ATS tier never pay that
cost. If it's missing, fetch_spa() returns [] and logs a hint.

Install (once, per machine):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

logger = logging.getLogger("sentinel.playwright")


# -----------------------------------------------------------------------------
# PUBLIC ENTRY POINT -- call this from ingest.py
# -----------------------------------------------------------------------------
def fetch_spa(
    tenant: dict,
    keywords: list[str],
    *,
    headless: bool = True,
    per_request_timeout_s: int = 20,
) -> list[dict]:
    """Scrape one SPA career page. Returns a list of job dicts.

    Each returned dict has the keys a SentinelPacket expects:
        title, url, location, team, company, source

    Parameters
    ----------
    tenant :
        A config dict from sentinel.agents.tenants.TENANTS. See that
        file's docstring for the shape.
    keywords :
        Search terms to run through the tenant's url_template. The
        runner visits one keyword at a time -- we do NOT parallelize
        because career sites throttle aggressive concurrent loads.
    headless :
        If False, pops a visible browser window. Useful for debugging
        selectors: you can see exactly what the page looked like when
        the scrape ran.
    per_request_timeout_s :
        How long to wait for a page to load before giving up on that
        keyword and moving to the next one.
    """
    # Import Playwright lazily. If the user hasn't installed it, we
    # want a clear log message instead of an ImportError at server boot.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning(
            "[Playwright] not installed. Slow-tier scrapers are disabled. "
            "To enable: pip install playwright && playwright install chromium"
        )
        return []

    display_name = tenant.get("display_name", "unknown")
    jobs: list[dict] = []

    # `sync_playwright()` is a context manager that handles launch +
    # teardown. Using `with` means the browser always closes even if
    # an exception bubbles up mid-scrape -- no zombie Chromium processes.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)

        # A fresh context per tenant so cookies / storage don't leak
        # across runs. User agent is a real desktop Chrome to avoid
        # the "headless detector" tripwires some sites ship.
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )

        page = context.new_page()

        try:
            for keyword in keywords:
                keyword_jobs = _scrape_one_keyword(
                    page=page,
                    tenant=tenant,
                    keyword=keyword,
                    timeout_s=per_request_timeout_s,
                )
                logger.info(
                    "[%s] keyword='%s' -> %d jobs",
                    display_name, keyword, len(keyword_jobs),
                )
                jobs.extend(keyword_jobs)

                # Be polite: a short pause between keywords. Career
                # sites rate-limit by IP and we'd rather slow down
                # than get blocked entirely.
                time.sleep(1.5)

        finally:
            # Always close, even on KeyboardInterrupt. Otherwise
            # Chromium processes pile up and eat RAM.
            context.close()
            browser.close()

    # De-dup by URL. Multiple keywords can find the same job.
    unique = _dedup_by_url(jobs)
    logger.info("[%s] total unique jobs: %d", display_name, len(unique))
    return unique


# -----------------------------------------------------------------------------
# INTERNAL HELPERS -- one job each, named so you can guess what they do
# -----------------------------------------------------------------------------

def _scrape_one_keyword(page, tenant: dict, keyword: str, timeout_s: int) -> list[dict]:
    """Scrape a single keyword. Handles pagination internally if the
    tenant uses scroll_strategy='paginate'.

    Errors inside are caught and logged -- we'd rather keep going
    and scrape 2 of 3 pages than hard-fail the whole tenant.
    """
    strategy = tenant.get("scroll_strategy", "none")

    if strategy == "paginate":
        return _scrape_paginated(page, tenant, keyword, timeout_s)

    # Default: single-page load (optionally with infinite scroll).
    url = _build_url(tenant["url_template"], keyword=keyword, page=1)
    return _scrape_single_page(page, tenant, url, timeout_s)


def _scrape_paginated(page, tenant: dict, keyword: str, timeout_s: int) -> list[dict]:
    """Walk pages 1..max_pages. Stops early if a page returns zero cards
    (usually means we've hit the end of results)."""
    max_pages = tenant.get("max_pages", 3)
    all_cards: list[dict] = []

    for page_num in range(1, max_pages + 1):
        url = _build_url(tenant["url_template"], keyword=keyword, page=page_num)
        cards = _scrape_single_page(page, tenant, url, timeout_s)
        if not cards:
            # Empty page -- we're past the last page of results.
            break
        all_cards.extend(cards)

    return all_cards


def _scrape_single_page(page, tenant: dict, url: str, timeout_s: int) -> list[dict]:
    """Navigate to one URL, wait for content, scroll if needed, extract."""
    display_name = tenant.get("display_name", "unknown")

    try:
        # goto() returns when the "load" event fires; SPAs fetch data
        # AFTER that, which is why we also wait for a selector below.
        page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
    except Exception as e:
        logger.warning("[%s] goto failed for %s: %s", display_name, url, e)
        return []

    # Check for CAPTCHA first -- if the site challenges us, bail
    # gracefully instead of scraping garbage.
    captcha_sel = tenant.get("captcha_selector")
    if captcha_sel:
        try:
            if page.query_selector(captcha_sel):
                logger.warning("[%s] CAPTCHA detected, skipping %s",
                               display_name, url)
                return []
        except Exception:
            pass  # If the check itself fails, proceed anyway.

    # Wait for the job list to hydrate. If this times out, the page
    # loaded but no jobs rendered -- likely a selector drift or a
    # search that returned zero results. Either way, return empty.
    try:
        page.wait_for_selector(
            tenant["wait_for_selector"],
            timeout=timeout_s * 1000,
        )
    except Exception:
        logger.info("[%s] no jobs rendered at %s (selector '%s' never appeared)",
                    display_name, url, tenant["wait_for_selector"])
        return []

    # If the tenant uses infinite scroll, scroll now to load more cards.
    if tenant.get("scroll_strategy") == "infinite":
        _do_infinite_scroll(
            page,
            steps=tenant.get("scroll_steps", 5),
            pause_ms=tenant.get("scroll_pause_ms", 1000),
        )

    # Grab all the cards and extract fields from each.
    return _extract_cards(page, tenant)


def _do_infinite_scroll(page, steps: int, pause_ms: int) -> None:
    """Scroll the page down `steps` times, pausing after each to give
    the SPA a chance to fetch + render more job cards."""
    for i in range(steps):
        # window.scrollTo -- simple and works on 99% of sites.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(pause_ms)


def _extract_cards(page, tenant: dict) -> list[dict]:
    """Find every job card on the current page and build a dict from each.

    Uses query_selector_all so we get a Python list of handles we can
    iterate without worrying about stale references.
    """
    display_name = tenant.get("display_name", "unknown")
    card_selector = tenant["card_selector"]
    field_selectors: dict[str, str] = tenant.get("field_selectors", {})
    url_prefix = tenant.get("url_prefix", "")

    try:
        card_handles = page.query_selector_all(card_selector)
    except Exception as e:
        logger.warning("[%s] card_selector failed: %s", display_name, e)
        return []

    out: list[dict] = []
    for card in card_handles:
        fields = {}
        for field_name, selector in field_selectors.items():
            fields[field_name] = _extract_field(card, selector)

        # Normalize the URL. Relative hrefs get prefixed; absolute
        # stay as-is.
        url_val = fields.get("url", "")
        if url_val and url_prefix and not url_val.startswith("http"):
            fields["url"] = url_prefix.rstrip("/") + "/" + url_val.lstrip("/")

        # Drop cards that didn't yield a title -- they're usually
        # "See more" buttons or layout decorations caught by a loose
        # card_selector.
        if not fields.get("title"):
            continue

        # Stamp the source so downstream code knows where this came from.
        fields["company"] = display_name
        fields["source"] = display_name.lower()
        out.append(fields)

    return out


def _extract_field(card, selector: str) -> str:
    """Pull one field out of one card.

    The selector syntax we support is a tiny DSL:
        "a.foo.text"   -> innerText of the first <a class=foo>
        "a.foo@href"   -> href attribute of the first <a class=foo>
        "a.foo"        -> defaults to innerText (same as .text)

    We keep this DSL tiny on purpose: every extra feature is another
    thing a weak LLM has to understand when fixing a broken tenant.
    """
    # Split off the ".text" / "@href" suffix so the CSS selector is clean.
    if selector.endswith(".text"):
        css = selector[:-5]
        mode = "text"
    elif "@" in selector:
        # e.g. "a.foo@href" -> css="a.foo", attr="href"
        css, _, attr = selector.rpartition("@")
        mode = "attr"
    else:
        css = selector
        mode = "text"

    try:
        # Commas in a CSS selector mean "OR" -- "a, b" matches either.
        # query_selector takes the first match.
        node = card.query_selector(css)
        if node is None:
            return ""
        if mode == "text":
            return (node.inner_text() or "").strip()
        else:
            return (node.get_attribute(attr) or "").strip()
    except Exception:
        # A broken selector shouldn't crash the whole scrape.
        return ""


def _build_url(template: str, *, keyword: str, page: int) -> str:
    """Fill {keyword} and {page} placeholders in a URL template.

    We URL-encode the keyword because spaces and '&' in search terms
    would otherwise corrupt the query string.
    """
    return template.format(
        keyword=urllib.parse.quote_plus(keyword),
        page=page,
    )


# -----------------------------------------------------------------------------
# PUBLIC -- HTML to PDF (reused by resume generator)
# -----------------------------------------------------------------------------
def render_html_to_pdf(html: str, output_path: str) -> bool:
    """Render an HTML string to a PDF file using headless Chromium.

    Used by the resume generator as a fallback when weasyprint is not
    installed. Playwright's print-to-PDF handles modern CSS (flexbox,
    grid, web fonts) better than weasyprint for resume-style layouts.

    Returns True on success, False on any failure. Never raises --
    callers treat False as "fall back to HTML-only output".

    Parameters
    ----------
    html :
        Complete HTML document (with <!DOCTYPE>, <html>, etc).
    output_path :
        Absolute path where the PDF should be written.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info(
            "[Playwright] not installed, cannot render PDF. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return False

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            # set_content handles a complete HTML document inline -- no
            # need to create a temp file. wait_until='networkidle' lets
            # web fonts load before we print.
            page.set_content(html, wait_until="networkidle")
            page.pdf(
                path=output_path,
                format="Letter",
                print_background=True,
                margin={"top": "0.5in", "bottom": "0.5in",
                        "left": "0.5in", "right": "0.5in"},
            )
            browser.close()
        return True
    except Exception as e:
        logger.warning("[Playwright] PDF render failed: %s", e)
        return False


def _dedup_by_url(jobs: list[dict]) -> list[dict]:
    """Keep the first occurrence of each URL. Multiple keyword searches
    often surface the same role -- no point analyzing it twice."""
    seen: set[str] = set()
    out: list[dict] = []
    for job in jobs:
        url = job.get("url", "")
        if not url:
            # No URL means we can't tell if it's a dup -- keep it.
            out.append(job)
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(job)
    return out
