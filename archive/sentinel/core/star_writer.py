"""
STAR+R Bullet Rewriter
======================

What this file is
-----------------
A tiny LLM pass that takes the raw "talking points" produced by the
fit-gap analyzer (things like "Built LLM eval harness that cut
regression time 4x") and reshapes each one into a 4-slot STAR+R
structure:

    S/T:  Situation + Task (one sentence of context)
    A:    Action you took
    R:    Result with a metric where possible
    R:    Reflection -- what you'd do again or differently

Why bother?
-----------
Talking points on their own are one-liners. Interview answers need
context and reflection. This pass takes 3-8s of LLM time and saves
the user 10-15 minutes of rewriting per story per role.

Why not do this inside the analyzer?
------------------------------------
The analyzer already has one big prompt (FIT_GAP_PROMPT). Bolting a
second schema onto it makes the prompt longer, slows parsing, and
couples two concerns. Keeping this standalone means:
  - Easier to swap the model (smaller/faster model for bullet
    rewriting, bigger model for fit-gap).
  - Easier to disable if the LLM goes flaky without losing fit-gap.
  - Easier to improve -- just edit ONE prompt.

Fallback
--------
If the LLM call fails or returns non-JSON, we return the input
points as-is (each becomes just an "A:" slot). The caller passes
whatever we return into story_bank.append_stories, which handles
partial bullets cleanly.
"""

from __future__ import annotations

import logging
from typing import Any

from core import llm

logger = logging.getLogger("sentinel.star_writer")


# The schema we want back. Keep it tight: the smaller the JSON
# schema, the more reliably small local models produce it.
_PROMPT = """You turn raw PM interview talking points into STAR+R bullets for a specific role.

JOB CONTEXT:
  Title: {title}
  Company: {company}
  Archetype: {archetype}

RAW TALKING POINTS (one per line):
{raw_points}

Rewrite EACH raw point as a single bullet with four slots. Return JSON:

{{
  "bullets": [
    {{
      "st": "<one sentence: the situation and what you were asked to do>",
      "a":  "<one sentence: what you actually did>",
      "r":  "<one sentence with a measurable outcome if possible>",
      "reflection": "<one sentence: what worked, what you'd change next time, or how it applies to the target role>"
    }}
  ]
}}

Rules:
- Keep each slot under 35 words. Interview bullets are scannable, not essays.
- NEVER fabricate metrics. If the raw point has no number, leave R qualitative.
- The reflection should tie back to the target role when possible (e.g. "this kind of ambiguous launch maps directly to the 0->1 work at {company}").
- Produce the same number of bullets as raw points -- do not merge or split.
- If a raw point is too thin to rewrite, emit the bullet with the raw text in `a` and empty strings elsewhere.
"""


def rewrite_to_star(
    talking_points: list[str],
    *,
    title: str = "",
    company: str = "",
    archetype: str = "",
    model: str | None = None,
    config_models: dict | None = None,
) -> list[dict[str, str]]:
    """Rewrite a list of talking points into STAR+R bullets.

    Returns a list of dicts with keys st / a / r / reflection. On any
    failure, returns minimal fallback bullets where the raw text lives
    in the `a` slot. Never raises.

    The story_bank module accepts exactly this shape via its `bullets`
    parameter, so the typical call sequence is:

        star = rewrite_to_star(points, title=..., company=...)
        story_bank.append_stories(
            data_dir=data_dir, job=job, analysis=result, bullets=star,
        )
    """
    # Guard: nothing to rewrite.
    clean_points = [p.strip() for p in (talking_points or []) if p and p.strip()]
    if not clean_points:
        return []

    prompt = _PROMPT.format(
        title=title or "the target role",
        company=company or "the company",
        archetype=archetype or "product manager",
        raw_points="\n".join(f"- {p}" for p in clean_points),
    )

    try:
        resp = llm.query_json(
            prompt,
            task="star_writer",
            model=model,
            config_models=config_models,
        )
    except Exception as e:
        logger.warning("[star_writer] LLM call failed: %s", e)
        return _fallback(clean_points)

    if not isinstance(resp, dict) or resp.get("_parse_error"):
        logger.info("[star_writer] non-JSON response, using fallback")
        return _fallback(clean_points)

    raw_bullets = resp.get("bullets", [])
    if not isinstance(raw_bullets, list) or not raw_bullets:
        return _fallback(clean_points)

    out: list[dict[str, str]] = []
    for b in raw_bullets:
        if not isinstance(b, dict):
            continue
        # Coerce every field to a safe string; trim whitespace.
        out.append({
            "st": _clean(b.get("st") or b.get("situation_task") or ""),
            "a":  _clean(b.get("a") or b.get("action") or ""),
            "r":  _clean(b.get("r") or b.get("result") or ""),
            "reflection": _clean(
                b.get("reflection") or b.get("rr") or b.get("r2") or ""
            ),
        })

    # If the model returned fewer bullets than points, pad with
    # fallbacks so the user still sees every raw point in the bank.
    if len(out) < len(clean_points):
        missing = clean_points[len(out):]
        out.extend(_fallback(missing))

    return out


# -----------------------------------------------------------------------------
# INTERNALS
# -----------------------------------------------------------------------------

def _clean(val: Any) -> str:
    """Coerce a model field to a trimmed string. Caps length so a
    hallucinated paragraph doesn't blow up the markdown file."""
    return str(val or "").strip()[:500]


def _fallback(points: list[str]) -> list[dict[str, str]]:
    """If the LLM can't help, at least stash every raw point in the
    A slot so the user sees their input reflected in the bank."""
    return [{"st": "", "a": p, "r": "", "reflection": ""} for p in points]
