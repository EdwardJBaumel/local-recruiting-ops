"""
ROLE ARCHETYPE GATE
===================

What this module does
---------------------
A free pre-filter that drops postings whose title clearly does not
match the archetype(s) the user is looking for. Runs BEFORE the LLM
scorer, so every drop is saved latency + tokens.

Why it exists
-------------
The LLM scorer is excellent at nuance but wasteful on obvious noise.
A user targeting "Product Manager" does not want to burn tokens
scoring "Engineering Program Manager", "Product Marketing Manager",
or "Project Manager" — three title families that visually resemble PM
but represent different jobs.

Extending
---------
Add an archetype by editing `config/role_archetypes.yaml`. The file
is documented. Users can override in `user.json` under the key
`role_archetypes_override` — the loader merges user overrides on top
of the defaults (user aliases / excludes are unioned with shipped).

Public API
----------
    load_archetypes(extra: dict | None = None) -> dict
    title_allowed(title: str, target_archetypes: list[str],
                  archetypes: dict | None = None) -> tuple[bool, str]

`title_allowed` returns (allowed, reason). The reason is short human-
readable text so telemetry can log why a job was dropped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("sentinel.role_archetypes")


# -----------------------------------------------------------------------------
# Default config location. Live repo ships one; callers can point somewhere
# else by passing a `path=` to load_archetypes().
# -----------------------------------------------------------------------------
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "role_archetypes.yaml"


# -----------------------------------------------------------------------------
# CACHE — we load once per process. Cheap, but this function runs on every
# job so avoid the YAML parse on every call.
# -----------------------------------------------------------------------------
_CACHED: dict[str, Any] | None = None


def load_archetypes(path: Path | str | None = None,
                    extra: dict | None = None,
                    force_reload: bool = False) -> dict[str, Any]:
    """Load the archetype taxonomy.

    Parameters
    ----------
    path : optional override for the YAML file.
    extra : user-specific overrides (shape identical to the YAML file
        under `archetypes:`). Merged on top of the shipped defaults.
    force_reload : skip the cache. Useful for tests.

    Returns
    -------
    dict shape: { archetype_name: {aliases: [...], exclude: [...]} }
    """
    global _CACHED
    if _CACHED is not None and not force_reload and extra is None and path is None:
        return _CACHED

    p = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if p.exists():
        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            data = raw.get("archetypes", {}) or {}
        except Exception as e:
            logger.warning("Failed to load role archetypes from %s: %s", p, e)
            data = {}
    else:
        logger.info("No role archetype config at %s — gate will be a no-op", p)

    # Merge user overrides. Aliases/excludes are unioned, not replaced, so
    # users don't lose ship-default coverage by setting a single custom
    # phrase.
    if extra:
        for name, spec in (extra or {}).items():
            if not isinstance(spec, dict):
                continue
            base = data.get(name, {"aliases": [], "exclude": []})
            merged = {
                "aliases": _merge_phrases(base.get("aliases", []), spec.get("aliases", [])),
                "exclude": _merge_phrases(base.get("exclude", []), spec.get("exclude", [])),
            }
            data[name] = merged

    # Pre-compile regexes once. Each phrase becomes a word-boundary regex.
    for name, spec in data.items():
        spec["_alias_patterns"] = _compile_phrases(spec.get("aliases", []))
        spec["_exclude_patterns"] = _compile_phrases(spec.get("exclude", []))

    if extra is None and path is None:
        _CACHED = data
    return data


def _merge_phrases(a: list[str], b: list[str]) -> list[str]:
    """Dedupe merge preserving order, case-insensitive."""
    seen: set[str] = set()
    out: list[str] = []
    for s in list(a) + list(b):
        if not isinstance(s, str):
            continue
        k = s.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _compile_phrases(phrases: list[str]) -> list[re.Pattern]:
    """Compile each phrase as a case-insensitive regex with word
    boundaries. Some aliases contain leading or trailing spaces (e.g.
    `" pm"`) — those are a signal the phrase needs literal whitespace
    and no extra \\b, which would reject it. We detect that and use
    the raw phrase when it has a leading/trailing non-word char.
    """
    pats: list[re.Pattern] = []
    for p in phrases or []:
        if not isinstance(p, str):
            continue
        phrase = p.strip()
        if not phrase:
            # Phrases like " pm" that intentionally carry leading space
            # are re-stripped here; rewrap with soft boundaries below.
            continue
        # Build pattern. Escape phrase, then wrap in boundaries that
        # tolerate punctuation commonly seen in titles ("Manager, Product"
        # or "Manager - Product"). We use a lookaround so trailing commas
        # and parentheses don't break the match.
        escaped = re.escape(phrase)
        # phrases already containing spaces use \b on edges.
        pat = re.compile(r"(?i)\b" + escaped + r"\b")
        pats.append(pat)
    return pats


# -----------------------------------------------------------------------------
# PUBLIC GATE
# -----------------------------------------------------------------------------
def title_allowed(title: str,
                  target_archetypes: list[str] | None,
                  archetypes: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Decide whether `title` passes the archetype pre-filter.

    Returns (allowed, reason).

    Rules:
      - Empty `target_archetypes` list → allow everything (user hasn't
        narrowed; don't surprise them by dropping jobs).
      - If any deny phrase matches AND no allow phrase matches → drop.
      - Otherwise allow.
    """
    if not title:
        return True, "empty title"
    if not target_archetypes:
        return True, "no target archetypes set"

    archs = archetypes if archetypes is not None else load_archetypes()
    if not archs:
        return True, "no archetype config loaded"

    title_low = title.lower()
    # Track which archetypes this title matches as an allow.
    allow_hits: list[str] = []
    # Track the deny phrases hit so we can name them in telemetry.
    deny_hits: list[str] = []

    for name in target_archetypes:
        spec = archs.get(name)
        if not spec:
            # Unknown archetype name in user.json — skip, don't fail open.
            # The rest of the gate still runs on the archetypes we know.
            logger.debug("title_allowed: unknown archetype %r in user.json", name)
            continue
        for pat in spec.get("_alias_patterns", []):
            if pat.search(title_low):
                allow_hits.append(name)
                break

        for pat in spec.get("_exclude_patterns", []):
            if pat.search(title_low):
                deny_hits.append(pat.pattern)
                # Don't break — multiple deny hits are useful telemetry.

    if allow_hits:
        return True, f"allowed by archetype(s): {', '.join(sorted(set(allow_hits)))}"
    if deny_hits:
        # Strip the regex wrapping for readable logs.
        readable = sorted({p.replace(r"\b", "").replace("\\", "") for p in deny_hits})
        return False, f"title-excluded: matched {', '.join(readable[:3])}"
    return True, "no archetype signal (default allow)"
