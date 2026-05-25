"""
SCRAPER SESSION HELPERS

Realistic browser fingerprints + polite retry for any scraper that
talks HTTP. The goal isn't to defeat sophisticated anti-bot (Cloudflare
Turnstile, Akamai BotManager — those need proxies + actual browser
automation) but to look enough like a real Firefox/Chrome user that:

  - Public ATS APIs (Greenhouse, Lever, Ashby, Workday) don't drop us
    when we ramp up keyword breadth.
  - Light rate-limited career pages (Amazon, Google, Microsoft non-
    Playwright fallbacks) don't 429 us off the cliff.
  - We back off cleanly when a server DOES throttle, instead of
    hammering and looking even more like a bot.

What this module gives every caller:
  - `realistic_headers()` → a fresh full header dict mimicking a recent
    Firefox or Chrome on macOS/Windows, with rotated User-Agent.
  - `polite_get()` / `polite_post()` → drop-in for `requests.get`
    that handles 429/503 with jittered exponential backoff.
  - `jittered_sleep(min_s, max_s)` → uniform-random pause for the
    politeness gap BETWEEN requests.

Why not use `cloudscraper` / `undetected-chromedriver`: those are
heavier, license-restricted, or break on every Chrome update. For
public ATS endpoints, plain requests + good headers is plenty.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

import requests

logger = logging.getLogger("lantern.scraper_session")


# Pool of real, recent browser User-Agents. Sampled from
# whatismybrowser.com's "common UA strings" 2026-04 update. Mix of
# Chrome / Firefox / Safari on Windows / macOS / Linux to avoid an
# obvious "always the same OS" fingerprint across requests.
USER_AGENTS: tuple[str, ...] = (
    # Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
    # Slightly older variants — avoids the "everyone is on the absolute
    # latest browser" tell that some bot-detectors look for.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)


# Accept-Language values weighted toward English-speaking but mixed —
# some German, French, Spanish to look like a real population.
_ACCEPT_LANGUAGES = (
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.5",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
)


def realistic_headers(
    *,
    referer: Optional[str] = None,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    json_request: bool = False,
) -> dict[str, str]:
    """Build a plausible browser-style header dict.

    Args:
        referer: optional Referer URL (helps look like a real navigation).
        accept: Accept header — defaults to the typical browser HTML mix.
        json_request: when True, override Accept to 'application/json'
                       and add 'Content-Type'.

    Note: we intentionally DO NOT set Cookie here. Cookies should come
    from the requests.Session (session.cookies persists across calls
    on the same host, which is what real browsers do).
    """
    ua = random.choice(USER_AGENTS)
    is_chromium = "Chrome" in ua and "Edg" not in ua  # plain Chrome path
    headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "application/json" if json_request else accept,
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        # Real browsers always send Accept-Encoding. requests handles
        # decompression automatically when this is set.
        "Accept-Encoding": "gzip, deflate, br",
        # DNT (Do Not Track) — not all browsers send it, but ~30% do.
        # Including it 30% of the time looks more authentic than always
        # or never. Random per-request.
        **({"DNT": "1"} if random.random() < 0.3 else {}),
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if json_request:
        headers["Content-Type"] = "application/json"

    # Sec-Fetch-* headers — sent by Chrome/Edge but NOT Firefox or
    # Safari. Sending them when the UA is Chrome/Edge looks right;
    # sending them with a Firefox UA would actually be a giveaway.
    if is_chromium or "Edg" in ua:
        headers.update({
            "Sec-Fetch-Dest": "document" if not json_request else "empty",
            "Sec-Fetch-Mode": "navigate" if not json_request else "cors",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
        })
    if referer:
        headers["Referer"] = referer
    return headers


# --- polite-retry wrapper -----------------------------------------------
# Servers sometimes 429 (rate limit) or 503 (service unavailable) under
# load. Hammering them when they're already saying "back off" gets you
# IP-banned. polite_get retries with exponential backoff: 1s, 3s, 9s,
# 27s. After that we give up and propagate the failure.

_RETRY_STATUSES = {429, 502, 503, 504}
_BACKOFF_BASE = 1.0
_BACKOFF_FACTOR = 3.0
_MAX_RETRIES = 4


def _do_request(
    method: str,
    session: requests.Session,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    """Internal: shared retry loop for GET/POST."""
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            # Network-level error (DNS, connection refused, timeout).
            # Retry the same way as a transient HTTP error.
            last_exc = e
            wait = _BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt)
            wait *= 1 + random.uniform(-0.1, 0.1)  # jitter
            logger.debug("[polite] network error %s; sleep %.1fs", e, wait)
            time.sleep(wait)
            continue

        if resp.status_code not in _RETRY_STATUSES:
            return resp

        # Retry-After header trumps our default if the server gave one.
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = _BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt)
        else:
            wait = _BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt)
        wait *= 1 + random.uniform(-0.1, 0.1)
        logger.info(
            "[polite] %s %s → %d; backing off %.1fs (attempt %d/%d)",
            method, url[:80], resp.status_code, wait, attempt + 1, _MAX_RETRIES,
        )
        time.sleep(wait)

    # Exhausted retries.
    if last_exc:
        raise last_exc
    return resp  # last response, even if it was a retry-able error


def polite_get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    """`session.get` with auto-retry on 429/503 and exp backoff."""
    return _do_request("GET", session, url, **kwargs)


def polite_post(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    """`session.post` with auto-retry on 429/503 and exp backoff."""
    return _do_request("POST", session, url, **kwargs)


def jittered_sleep(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Random uniform pause between two values. Use BETWEEN scraper
    requests to look less mechanical than a fixed `time.sleep(2)`.

    Why a range instead of a fixed value: real users' click-throughs
    aren't on a metronome. A predictable 2.0s gap is one of the
    cheapest tells a basic bot-detector watches for.
    """
    time.sleep(random.uniform(min_s, max_s))
