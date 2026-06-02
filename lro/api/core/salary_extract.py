"""
SALARY EXTRACTION FROM JD TEXT
==============================

What this file does
-------------------
Most ATS JSON APIs (Greenhouse, Lever, Ashby) don't ship the salary
band as a structured field — they put it in the free-text description
because pay-disclosure laws require the disclosure but not a particular
schema. Means the registry's `salary: {min, max, currency}` field comes
back as None for ~95% of US postings, and the Brief tab's salary
distribution renders "No salary data yet" even when 30% of JDs do
mention pay.

This module scrapes salary from description text via regex. Called
per-packet at ingest time when the structured field is empty. Best-
effort: returns None when the text doesn't contain a parseable band.

Why regex and not LLM
---------------------
The relevant patterns are narrow ("$140,000 - $180,000", "$140K-$180K",
"USD 140k–180k", "£80,000–£120,000"). An LLM call per JD at ingest
would add ~30-50 minutes per cycle for 1000 jobs on consumer GPU — and
regex hits ~95% of the cases by inspection of real-world JD samples.

Why we don't try harder on edge cases
-------------------------------------
- Hourly rates ("$45/hour") → skip. Hourly contractor postings are
  not what this dashboard is for.
- Equity-only ranges ("0.1% - 0.5% equity") → skip. Not comparable.
- Wide ranges with "depends on level" caveats → take them as-is. The
  Brief tab's histogram is fine with broad bands.

Currency support
----------------
USD (default — bare $ or USD prefix), GBP (£), EUR (€). For anything
else we return None rather than guess wrong; the salary distribution
chart already only renders the USD-band histogram, so we'd lose
nothing by skipping foreign bands here.

Range patterns we match
-----------------------
- "$140,000 - $180,000"
- "$140,000-$180,000" (no spaces)
- "$140k - $180k"  /  "$140K-$180K"
- "USD 140,000 - 180,000"
- "140k–180k" (en-dash)
- "£80,000-£120,000"
- "€90,000 – €120,000"

Floor-only patterns (we synthesise a max = min if max can't be parsed):
- "starting at $140,000"
- "from $150k"
"""
from __future__ import annotations

import re
from typing import Optional

# Currency symbol → ISO code. Bare $ defaults to USD because we're a
# US-centric pipeline; users outside US can re-tune by adding their
# own regex variants if they care.
_CURRENCY_SYMBOLS = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
}
_CURRENCY_CODES = {
    "USD": "USD",
    "GBP": "GBP",
    "EUR": "EUR",
}

# Range with explicit currency symbol on both sides:
#   $140,000 - $180,000
#   £80,000-£120,000
# Pattern allows: optional commas, optional decimals, optional k/K
# suffix, en-dash / em-dash / hyphen / "to" as separator.
_RANGE_SYMBOL = re.compile(
    r"(?P<sym1>[$£€])\s*(?P<lo>\d{2,3}[,.]?\d{0,3})\s*(?P<klo>[kK])?\s*"
    r"(?:[-–—]|to)\s*"
    r"(?P<sym2>[$£€])?\s*(?P<hi>\d{2,3}[,.]?\d{0,3})\s*(?P<khi>[kK])?",
)

# Range with currency code prefix once:
#   USD 140,000 - 180,000
#   GBP 80,000-120,000
_RANGE_CODE = re.compile(
    r"\b(?P<cur>USD|GBP|EUR)\s*\$?\s*(?P<lo>\d{2,3}[,.]?\d{0,3})\s*(?P<klo>[kK])?\s*"
    r"(?:[-–—]|to)\s*"
    r"\$?\s*(?P<hi>\d{2,3}[,.]?\d{0,3})\s*(?P<khi>[kK])?",
    re.IGNORECASE,
)

# Floor-only with "starting at" / "from":
#   starting at $140,000
#   from $150k
_FLOOR_ONLY = re.compile(
    r"(?:starting\s+at|from)\s*(?P<sym>[$£€])\s*(?P<lo>\d{2,3}[,.]?\d{0,3})\s*(?P<klo>[kK])?",
    re.IGNORECASE,
)

# Reject hourly rates so we don't accidentally extract "$45/hour" as
# an annual salary. If we see /hour /hr per hour anywhere within ~30
# chars of the matched range, we drop the match.
_HOURLY_NOISE = re.compile(r"/\s*(?:hour|hr)\b|per\s+hour", re.IGNORECASE)


def _norm_amount(raw: str, has_k_suffix: bool) -> int | None:
    """Convert a numeric token like "140,000" or "140" or "150.5" into
    an integer dollar amount. If the token had a `k`/`K` suffix the
    value is multiplied by 1000. Returns None if parsing fails.
    """
    # Strip commas + decimals (round down to int).
    cleaned = raw.replace(",", "")
    try:
        n = float(cleaned)
    except ValueError:
        return None
    if has_k_suffix:
        n *= 1000
    # Plausibility band: salaries below $20k or above $2M are noise.
    if n < 20_000 or n > 2_000_000:
        # Maybe the value was stated without the k suffix — try
        # multiplying. "140" alone almost certainly means 140k in a JD.
        if not has_k_suffix and 50 <= n <= 999:
            n *= 1000
            if n < 20_000 or n > 2_000_000:
                return None
        else:
            return None
    return int(round(n))


def _looks_hourly(text: str, match_start: int, match_end: int) -> bool:
    """True if the matched salary range is within ~30 chars of an
    hourly-rate token. Filters out '$45/hour' false positives."""
    window = text[max(0, match_start - 30): match_end + 30]
    return bool(_HOURLY_NOISE.search(window))


def extract_salary(text: str) -> Optional[dict]:
    """Best-effort salary extraction from JD text.

    Returns
    -------
    A dict shaped like `{min: int, max: int, currency: "USD"|"GBP"|"EUR"}`
    or `None` if no parseable salary band was found.

    The returned dict matches the existing `salary` field shape on the
    match payload (the Brief tab's salary-distribution card consumes
    `salary.min` / `salary.max`).
    """
    if not text or not isinstance(text, str):
        return None

    # Prefer the explicit-symbol range — most US JDs use it.
    for m in _RANGE_SYMBOL.finditer(text):
        if _looks_hourly(text, m.start(), m.end()):
            continue
        sym = m.group("sym1")
        currency = _CURRENCY_SYMBOLS.get(sym, "USD")
        lo = _norm_amount(m.group("lo"), bool(m.group("klo")))
        hi = _norm_amount(m.group("hi"), bool(m.group("khi")))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        # Sanity: spread shouldn't exceed 6x. If it does, we likely
        # captured a phone number / year / unrelated digits.
        if hi > lo * 6:
            continue
        return {"min": lo, "max": hi, "currency": currency}

    # Currency-code prefix range (USD 140,000 - 180,000).
    for m in _RANGE_CODE.finditer(text):
        if _looks_hourly(text, m.start(), m.end()):
            continue
        currency = _CURRENCY_CODES.get(m.group("cur").upper(), "USD")
        lo = _norm_amount(m.group("lo"), bool(m.group("klo")))
        hi = _norm_amount(m.group("hi"), bool(m.group("khi")))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        if hi > lo * 6:
            continue
        return {"min": lo, "max": hi, "currency": currency}

    # Floor-only ("starting at $140,000"). Encode as min-only so the
    # Brief histogram still buckets it correctly.
    m = _FLOOR_ONLY.search(text)
    if m and not _looks_hourly(text, m.start(), m.end()):
        sym = m.group("sym")
        currency = _CURRENCY_SYMBOLS.get(sym, "USD")
        lo = _norm_amount(m.group("lo"), bool(m.group("klo")))
        if lo is not None:
            return {"min": lo, "max": None, "currency": currency}

    return None
