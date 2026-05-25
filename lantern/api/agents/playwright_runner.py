"""
Playwright runner — kept for one purpose only: HTML→PDF rendering
for the resume generator.

Historical context: this file used to contain a full SPA scraper
(`fetch_spa` + ~300 lines of selector-driving plumbing) that powered
the Apple/Meta/Microsoft careers-page ingestors. Those scrapers were
removed before public release because each company's TOS or
robots.txt explicitly prohibits automated access:

    Meta:      robots.txt blocks ClaudeBot + every named AI bot
    Apple:     site TOS bans "robot, spider or other automatic device"
    Microsoft: service agreement prohibits scraping

What stays here is the PDF-rendering path. `render_html_to_pdf` is
called by the resume generator when weasyprint isn't available;
Playwright's print-to-PDF handles modern CSS (flexbox/grid/webfonts)
better for resume-style layouts. Single function, no scraping.

Optional dependency: Playwright is heavy (~300MB for Chromium). We
import it lazily inside `render_html_to_pdf()` so users who never
generate a PDF resume don't pay that cost.

Install (once, per machine):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import logging

logger = logging.getLogger("lantern.playwright")


def render_html_to_pdf(html: str, output_path: str) -> bool:
    """Render an HTML string to a PDF file using headless Chromium.

    Used by the resume generator as a fallback when weasyprint is not
    installed. Returns True on success, False on any failure. Never
    raises — callers treat False as "fall back to HTML-only output".

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
            try:
                page = browser.new_page()
                # set_content handles a complete HTML document inline —
                # no temp file needed. wait_until="networkidle" lets
                # web-fonts and any inline images load before snapshot.
                page.set_content(html, wait_until="networkidle")
                page.pdf(
                    path=output_path,
                    format="Letter",
                    print_background=True,
                    margin={"top": "0.5in", "right": "0.5in", "bottom": "0.5in", "left": "0.5in"},
                )
                return True
            finally:
                browser.close()
    except Exception as e:
        logger.warning("[Playwright] PDF render failed: %s", e)
        return False
