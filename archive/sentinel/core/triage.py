"""
TRIAGE LEARNING

The Triage tab lets the user rapidly keep/skip matches with keyboard
shortcuts. Those decisions already persist via decision_store as
reactions (action=up/down), but on their own they're just a list - we
want to USE them as a signal that informs future cycles.

This module scans reactions and surfaces tokens that appear much more
often in SKIPPED titles than KEPT titles. It's a simple log-odds
filter, deliberately not LLM-backed - deterministic, near-free to run,
and transparent to the user.

The suggestions are *surfaced, not applied*: the UI shows them so the
user can copy a word into the Settings blocklist if it resonates.
Auto-applying silent blocklists from one click is how feedback loops
turn into over-filtering.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path

from core import decision_store

logger = logging.getLogger("sentinel.triage")

_MIN_SAMPLES = 3   # need at least this many of either up/down before we bother
_MIN_OCC     = 2   # a token must appear in at least this many skipped titles
_TOP_N       = 15  # how many suggestions we return

# Stop-list: common PM/tech words we never want to surface because they
# dominate everyone's titles and would drown real signal.
_STOP = {
    "product", "manager", "senior", "lead", "principal", "staff", "director",
    "head", "of", "the", "a", "an", "and", "or", "&", "for", "to", "at", "in",
    "on", "with", "new", "role", "ii", "iii", "-", ",", "team", "group",
    "engineer", "engineering", "program", "technical",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\+]*")


def _tokenise(title: str) -> set[str]:
    """Unique lowercase tokens from a title, dropping the stop-list."""
    return {t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(title or ""))
            if t not in _STOP and len(t) > 2}


def learned_keywords(data_dir: Path) -> dict:
    """Return {"samples": {...}, "suggestions": [...]}.

    suggestions is newest-first by log-odds; each entry is
    {token, skips, keeps, score}. When there aren't enough samples yet
    we return an empty suggestion list + a readable "needs_more" reason
    so the UI can show a helpful message rather than an empty table."""
    data = decision_store.list_all(data_dir)
    reactions = data.get("reactions", {}) or {}

    up_titles = [r.get("title", "") for r in reactions.values() if r.get("action") == "up"]
    down_titles = [r.get("title", "") for r in reactions.values() if r.get("action") == "down"]

    samples = {"keeps": len(up_titles), "skips": len(down_titles)}
    if len(down_titles) < _MIN_SAMPLES:
        return {
            "samples": samples,
            "suggestions": [],
            "needs_more": f"Mark at least {_MIN_SAMPLES} jobs as Skip to see learned keywords.",
        }

    skip_ctr: Counter = Counter()
    keep_ctr: Counter = Counter()
    for title in down_titles:
        for tok in _tokenise(title):
            skip_ctr[tok] += 1
    for title in up_titles:
        for tok in _tokenise(title):
            keep_ctr[tok] += 1

    # Add-one smoothed log-odds. Positive score = more skip-biased.
    # Totals used for the denominator so a token that's everywhere
    # (in half of all titles) doesn't dominate.
    total_skip = max(1, sum(skip_ctr.values()))
    total_keep = max(1, sum(keep_ctr.values()))
    suggestions = []
    for tok, skip_count in skip_ctr.items():
        if skip_count < _MIN_OCC:
            continue
        keep_count = keep_ctr.get(tok, 0)
        p_skip = (skip_count + 1) / (total_skip + 2)
        p_keep = (keep_count + 1) / (total_keep + 2)
        score = math.log(p_skip / p_keep)
        if score <= 0:
            continue
        suggestions.append({
            "token": tok,
            "skips": skip_count,
            "keeps": keep_count,
            "score": round(score, 3),
        })

    suggestions.sort(key=lambda s: (-s["score"], -s["skips"]))
    return {
        "samples": samples,
        "suggestions": suggestions[:_TOP_N],
    }
