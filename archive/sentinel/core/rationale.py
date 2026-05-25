"""
ON-DEMAND MATCH RATIONALE

The match agent's fast path (sentence-transformers cosine) leaves only
the literal reason "embedding similarity" behind. That's enough for
ranking but opaque for a human deciding whether to apply.

This module runs a qwen3:8b pass that produces a human-readable
rationale from the candidate profile + job payload. It's only ever
invoked when the user clicks "Why this match?" on a detail card, so the
cost is paid per-job-you-care-about rather than per-cycle.

Outputs a small structured dict:
  {
    verdict: "strong" | "solid" | "worth-a-look" | "stretch",
    summary: str,                  # 1-2 sentences, ~40 words
    strengths: [str, ...],         # 2-5 bullets, 1 short sentence each
    gaps: [str, ...],              # 1-3 bullets
  }

A module-level cache keyed by (company, title, url) keeps re-clicks
free; cache is in-memory only, lost on restart - fine since the button
only exists on a live job card.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from core import llm

logger = logging.getLogger("sentinel.rationale")

# Small TTL-free cache. The detail panel only has a few dozen live jobs
# at once; we don't need an eviction policy. Bounded at _CACHE_MAX so a
# misbehaving client can't grow memory unbounded.
_CACHE_MAX = 500
_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _key(payload: dict) -> str:
    return "||".join([
        (payload.get("company") or "").strip().lower(),
        (payload.get("title") or "").strip().lower(),
        (payload.get("url") or "").strip().lower(),
    ])


def _clip(text: str, n: int) -> str:
    """Trim long free-text fields before stuffing into the prompt - JDs
    of 10k characters drag qwen3 to a crawl for no extra insight."""
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


_PROMPT = """You evaluate how well a job matches a candidate's profile.

CANDIDATE PROFILE:
{profile}

JOB:
Title: {title}
Company: {company}
Seniority: {seniority}
Location: {location}  (remote: {remote})
Technologies: {technologies}
Description:
{description}

Numeric match so far (0-1): {score:.2f}  (threshold {threshold:.2f})

Write a short rationale the candidate can skim in 10 seconds. Output ONLY a JSON object with:
  "verdict":  one of "strong", "solid", "worth-a-look", "stretch"
  "summary":  one or two sentences, <40 words, no fluff
  "strengths": 2-5 bullets, each one short concrete sentence ("Python + SQL overlap", not "The candidate may have relevant skills")
  "gaps":     1-3 bullets - concrete things the candidate would need to close or explain

No preamble. No trailing text. JSON only.
"""


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from a response blob. qwen3 usually
    returns clean JSON but occasionally wraps with ```json ...```."""
    if not text:
        return {}
    # Prefer the first balanced '{...}' block.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        # Tolerate trailing commas - qwen3 slip-up we've seen in analyse.
        cleaned = re.sub(r",\s*([\]}])", r"\1", m.group(0))
        try:
            return json.loads(cleaned)
        except Exception:
            return {}


def _coerce_list(val, max_items: int) -> list[str]:
    """Accept a list or a single string; clamp length so a runaway model
    can't return 50 bullets."""
    if isinstance(val, list):
        out = [str(x).strip() for x in val if str(x).strip()]
    elif isinstance(val, str):
        out = [val.strip()] if val.strip() else []
    else:
        out = []
    return out[:max_items]


def _normalise(parsed: dict) -> dict:
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in {"strong", "solid", "worth-a-look", "stretch"}:
        verdict = "worth-a-look"
    return {
        "verdict": verdict,
        "summary": _clip(str(parsed.get("summary") or ""), 320),
        "strengths": _coerce_list(parsed.get("strengths"), 5),
        "gaps": _coerce_list(parsed.get("gaps"), 3),
    }


def generate(profile_text: str, payload: dict, *, threshold: float = 0.55,
             model: str | None = None, force: bool = False) -> dict:
    """Produce or return a cached rationale for one job.

    Returns {ok, cached, rationale:{verdict,summary,strengths,gaps},
             error?}.

    The caller (API handler) is responsible for passing the candidate's
    profile text - we don't re-read it here so this stays a pure function
    from the caller's perspective.
    """
    key = _key(payload)
    if not force:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                return {"ok": True, "cached": True, "rationale": hit}

    if not (profile_text or "").strip():
        return {"ok": False, "error": "No candidate profile. Upload a resume in Settings first."}

    prompt = _PROMPT.format(
        profile=_clip(profile_text, 2000),
        title=payload.get("title") or "N/A",
        company=payload.get("company") or "N/A",
        seniority=payload.get("seniority") or "unknown",
        location=payload.get("location") or "N/A",
        remote=payload.get("remote") or "unknown",
        technologies=", ".join(payload.get("technologies") or []) or "N/A",
        description=_clip(payload.get("description") or "", 2500),
        score=float(payload.get("_match_score") or 0.0),
        threshold=float(threshold),
    )

    try:
        raw = llm.query(prompt, task="analyze", model=model, temperature=0.2, timeout=90)
    except Exception as e:
        logger.warning("Rationale LLM call failed for %s @ %s: %s",
                       payload.get("title"), payload.get("company"), e)
        return {"ok": False, "error": f"LLM unavailable: {e}"}

    parsed = _parse_json(raw)
    if not parsed:
        # Return a shape the UI can still render so the button doesn't
        # look broken - include the raw blob truncated for debugging.
        fallback = {
            "verdict": "worth-a-look",
            "summary": _clip(raw, 320) or "Model returned no structured rationale.",
            "strengths": [],
            "gaps": [],
        }
        with _cache_lock:
            if len(_cache) < _CACHE_MAX:
                _cache[key] = fallback
        return {"ok": True, "cached": False, "rationale": fallback, "warning": "Non-JSON output"}

    normalised = _normalise(parsed)
    with _cache_lock:
        if len(_cache) < _CACHE_MAX:
            _cache[key] = normalised
    return {"ok": True, "cached": False, "rationale": normalised}


def clear_cache():
    """Used by tests and by a future 'regenerate' button if we add one."""
    with _cache_lock:
        _cache.clear()
