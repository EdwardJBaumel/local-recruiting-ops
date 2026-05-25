"""
SPA Tenant Configurations
=========================

What this file is
-----------------
A plain dictionary per career site we scrape with a headless browser.
Each dict tells the Playwright runner:

    * where to go           -> url_template
    * how to wait           -> wait_for_selector  (DOM node that signals "jobs loaded")
    * how to load more      -> scroll_strategy    ("infinite" | "button" | "paginate" | "none")
    * what to pull out      -> card_selector + field_selectors
    * what to keep          -> keyword_filter     (titles that don't match are skipped)

Why is this separate from playwright_runner.py?
-----------------------------------------------
Career pages change their CSS classes all the time. When Meta renames
`div.careers-job-card` to `div.job-listing-v2`, only THIS file needs an
edit. The runner stays untouched. If you're debugging a broken tenant,
start here -- 90% of the time the fix is a selector string.

How to add a new tenant
-----------------------
1. Open the career page in Chrome, inspect the job-card element.
2. Copy the CSS selector from DevTools -> right-click -> Copy -> Copy selector.
3. Add a new entry below. Start from an existing one (Apple is the simplest).
4. Test with:  python -m sentinel.tools.test_tenant <name>

Field selectors: each is a CSS selector that points to a node INSIDE a card.
`.text` means "take innerText". `@href` means "take the href attribute".
Missing fields are OK -- they'll just be empty strings on the packet.

Shipping note
-------------
This file ships with `TENANTS = {}` in the public repo so forks don't
automatically hammer anyone's career page. Users opt in by enabling
toggles in the Settings UI, which populates this dict from config.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# TENANT REGISTRY
# -----------------------------------------------------------------------------
# Key is the lowercase internal name. Value is a plain dict (no classes --
# weak LLMs get confused by classes). Read top-to-bottom like a recipe.
# -----------------------------------------------------------------------------

TENANTS: dict[str, dict] = {

    # -------------------------------------------------------------------------
    # APPLE -- jobs.apple.com
    # -------------------------------------------------------------------------
    # Apple uses a client-side accordion list. Each job is an <li class=
    # "rc-accordion-item"> containing an <a href="/en-us/details/..."> for
    # the title, a <span.team-name> for the org, a <span.job-posted-date>
    # for posted date, and location text further down. Confirmed live on
    # 2026-04-23: 60 job anchors on page 1 for "product manager".
    # -------------------------------------------------------------------------
    "apple": {
        "display_name": "Apple",
        "enabled_by_default": False,

        # {keyword} gets replaced with the search term (URL-encoded by runner).
        # {page} starts at 1. Apple's pagination uses 1-based pages.
        "url_template": (
            "https://jobs.apple.com/en-us/search?search={keyword}"
            "&sort=newest&page={page}"
        ),

        # Wait for the first job-detail anchor -- confirmed working
        # selector as of 2026-04-23 probe.
        "wait_for_selector": "a[href*='/details/']",

        "scroll_strategy": "paginate",
        "max_pages": 3,

        # CRITICAL: scope to "ul#search-job-list" because Apple ALSO uses
        # "li.rc-accordion-item" for the filter sidebar (Location /
        # Teams / etc.). A bare "li.rc-accordion-item" selector grabs
        # both and pollutes results with empty filter rows. Confirmed
        # on 2026-04-23 probe.
        "card_selector": "ul#search-job-list li.rc-accordion-item",

        "field_selectors": {
            # Title anchor: "link-inline t-intro" class inside an <h3>.
            # Simplified to the href pattern since that's more stable.
            "title":    "a[href*='/details/'].text",
            "url":      "a[href*='/details/']@href",
            # Team sits right under the title in "span.team-name".
            "team":     "span.team-name.text",
            # Posted date -- Apple surfaces this as its own span.
            "posted":   "span.job-posted-date.text",
            # Location -- real selector from 2026-04-23 probe. Apple
            # puts the city/country inside a span whose class is a
            # mouthful ("table--advanced-search__location-sub") and
            # whose id is unstable ("search-store-name-N"). The class
            # has been stable for 2+ years; match on it.
            "location": "span.table--advanced-search__location-sub.text",
        },

        # Apple hrefs are site-relative ("/en-us/details/..."). Prefix.
        "url_prefix": "https://jobs.apple.com",
    },

    # -------------------------------------------------------------------------
    # META -- metacareers.com
    # -------------------------------------------------------------------------
    # STATUS AS OF 2026-04-23: BLOCKED. metacareers.com now returns
    # "Not Logged In. Please log in to see this page." even for the
    # public careers listing. A Facebook session cookie is required.
    # We keep the config present so the toggle UI still works and the
    # selectors are ready IF Meta re-opens the page or the user wires
    # a cookie path later. The runner will simply return zero jobs
    # until that happens -- no error is raised.
    # -------------------------------------------------------------------------
    "meta": {
        "display_name": "Meta",
        "enabled_by_default": False,

        "url_template": (
            "https://www.metacareers.com/jobs?q={keyword}"
            "&offices[0]=United+States"
        ),

        # Wait for a real job anchor -- if/when Meta re-opens the site
        # publicly, the numeric /jobs/<id> pattern is the stable part.
        "wait_for_selector": "a[href*='/jobs/']",

        "scroll_strategy": "infinite",
        "scroll_steps": 6,
        "scroll_pause_ms": 1200,

        "card_selector": "div._8sel, a[href*='/jobs/'][role='link']",

        "field_selectors": {
            "title":    "div._army, span._army.text",
            "url":      "a@href",
            "location": "div._8sen, span._8sen.text",
            "team":     "div._8seo.text",
        },

        "url_prefix": "https://www.metacareers.com",
    },

    # -------------------------------------------------------------------------
    # MICROSOFT -- jobs.careers.microsoft.com
    # -------------------------------------------------------------------------
    # STATUS AS OF 2026-04-23: BLOCKED BY reCAPTCHA. The search URL
    # redirects headless browsers to apply.careers.microsoft.com with
    # a reCAPTCHA iframe injected. Runner detects the iframe via
    # captcha_selector and bails cleanly -- no garbage rows get
    # scraped. Config stays so the toggle UI still works.
    #
    # Workarounds if the user wants MS scraping later:
    #   (a) feed in a logged-in session cookie via context.storage_state
    #   (b) use playwright-stealth to hide the automation fingerprint
    # Both are out of scope for this pass. Keep the hooks.
    # -------------------------------------------------------------------------
    "microsoft": {
        "display_name": "Microsoft",
        "enabled_by_default": False,

        "url_template": (
            "https://jobs.careers.microsoft.com/global/en/search"
            "?q={keyword}&lc=United%20States&pg={page}"
        ),

        "wait_for_selector": "div[role='listitem'], div.ms-List-cell",

        "scroll_strategy": "paginate",
        "max_pages": 3,

        "card_selector": "div[role='listitem']",

        "field_selectors": {
            "title":    "h2.text, span.ms-Link.text",
            "url":      "a@href",
            "location": "span.ms-Persona-secondaryText.text, div[aria-label*='location'].text",
            "team":     "span.ms-Persona-tertiaryText.text",
        },

        "url_prefix": "https://jobs.careers.microsoft.com",

        # Verified present on 2026-04-23: reCAPTCHA iframe loads before
        # the job list does. Runner sees this and skips the page.
        "captcha_selector": "iframe[src*='recaptcha'], div#px-captcha",
    },
}


def get_tenant(name: str) -> dict | None:
    """Look up a tenant config by name. Returns None if not found.

    This is the ONLY public function other code should use to reach
    into TENANTS -- it gives us one place to add validation later
    (e.g. checking required keys) without touching callers.
    """
    return TENANTS.get(name.lower())


def list_tenant_names() -> list[str]:
    """Return all known tenant keys. Used by the orchestrator when
    deciding which slow-tier scrapers to dispatch."""
    return sorted(TENANTS.keys())
