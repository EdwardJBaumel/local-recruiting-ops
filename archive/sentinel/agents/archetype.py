"""
Archetype Classifier
====================

What this file is
-----------------
Before the expensive match-scoring step runs, we make a cheap LLM call
that buckets a job into one of a handful of role archetypes. That
bucket becomes a chip on the match card ("AI PM", "Platform PM", etc.)
and it can be used later to filter / sort matches or weight scoring.

Why bother?
-----------
The title alone is often misleading. "Product Manager, Trust &
Safety" is really Ops. "Senior PM, LLM Infrastructure" is really
AI Platform. Bucketing up front means the match card tells you at a
glance what KIND of PM job this is -- separate from how well it
matches your profile.

How to add / change buckets
---------------------------
Edit the ARCHETYPES dict below. That's it. The prompt is generated
from ARCHETYPES at call time, so adding "Growth PM" takes one line.
Keep descriptions short and unambiguous -- small local models follow
the shortest clearest label.

Wiring
------
Call classify_archetype(title, description) from whichever pipeline
stage wants the label. Result is a plain dict:

    {"archetype": "ai_pm", "confidence": 0.82, "rationale": "..."}

If the LLM call fails we return archetype="unclassified" -- never raise.
Downstream code MUST handle "unclassified" as "no bucket known".
"""

from __future__ import annotations

import logging
from typing import Any

from core import llm

logger = logging.getLogger("sentinel.archetype")


# -----------------------------------------------------------------------------
# BUCKETS -- add / rename entries here, everything else picks them up.
# -----------------------------------------------------------------------------
# Key is the machine slug that gets stored on the match packet.
# Value is a short human description used BOTH in the LLM prompt (so
# the model knows what each slug means) AND in the UI chip label.
# -----------------------------------------------------------------------------
ARCHETYPES: dict[str, str] = {
    "pm":          "Core Product Manager — owns a user-facing product area, writes PRDs, works with design + eng.",
    "tpm":         "Technical Program Manager — coordinates cross-team delivery, schedules, dependencies, risk.",
    "platform_pm": "Platform / Infrastructure PM — owns internal APIs, dev tools, or shared services consumed by other teams.",
    "ai_pm":       "AI / ML Product Manager — owns LLM, ML, or data-science-heavy products; defines eval and model behavior.",
    "ops_pm":      "Product Operations — process, tooling, rituals, enablement; supports PMs rather than shipping customer features.",
    "growth_pm":   "Growth PM — experimentation, funnels, activation, retention, monetization.",
    "director":    "Director / Head of Product — people-management role, not an IC PM seat.",
    "other":       "Adjacent but not core PM (design, eng manager, strategy, research, marketing).",
}

# Slug returned when the LLM call fails or the model gives us garbage.
UNCLASSIFIED = "unclassified"


def classify_archetype(
    title: str,
    description: str,
    *,
    model: str | None = None,
    config_models: dict | None = None,
) -> dict[str, Any]:
    """Classify one job into an archetype bucket.

    Parameters
    ----------
    title :
        Job title exactly as the ATS returned it.
    description :
        Plain-text description. We only send the first ~800 chars to
        keep the classifier prompt small -- archetype is usually
        obvious from the opening paragraphs.
    model / config_models :
        Optional overrides, passed through to llm.query_json. Leave
        as None to use whatever model is mapped to task='archetype'
        (which falls back to the default chat model).

    Returns
    -------
    dict with keys:
        archetype   -- one of ARCHETYPES keys, or UNCLASSIFIED
        confidence  -- float in [0, 1], 0.0 if unclassified
        rationale   -- one-sentence reason (may be empty on failure)
    """
    # Guard: missing inputs never go to the LLM. Saves tokens.
    if not title and not description:
        return _unclassified("no title or description provided")

    prompt = _build_prompt(title=title, description=description)

    try:
        raw = llm.query_json(
            prompt,
            task="archetype",
            model=model,
            config_models=config_models,
        )
    except Exception as e:
        logger.warning("[archetype] LLM call failed: %s", e)
        return _unclassified(f"llm error: {e}")

    # query_json returns {_parse_error: True} when the model emitted
    # non-JSON. Treat that the same as an LLM failure.
    if not isinstance(raw, dict) or raw.get("_parse_error"):
        logger.info("[archetype] parse error on response: %s", str(raw)[:200])
        return _unclassified("llm returned non-json")

    # Validate the slug. If the model hallucinated a new bucket, we
    # reject it rather than letting unknown strings leak into the UI.
    slug = str(raw.get("archetype", "")).strip().lower()
    if slug not in ARCHETYPES:
        logger.info("[archetype] unknown slug '%s', falling back", slug)
        return _unclassified(f"unknown slug: {slug}")

    # Clamp confidence into [0, 1]. Models sometimes return 0.95 or
    # "high" or 95 -- we normalize to a float.
    conf = _coerce_confidence(raw.get("confidence"))

    return {
        "archetype": slug,
        "confidence": conf,
        "rationale": str(raw.get("rationale", "")).strip()[:240],
    }


def display_label(archetype_slug: str) -> str:
    """Short human label for a chip in the UI.

    Keeps UI text close to the source of truth. When you rename a
    bucket in ARCHETYPES, the chip text updates automatically.
    """
    if archetype_slug == UNCLASSIFIED:
        return "Unclassified"
    desc = ARCHETYPES.get(archetype_slug)
    if not desc:
        return archetype_slug.replace("_", " ").title()
    # The description starts with "Core Product Manager — ..."; we
    # just want the part before the em-dash for a short chip.
    head = desc.split("—")[0].strip()
    return head or archetype_slug


# -----------------------------------------------------------------------------
# INTERNAL HELPERS
# -----------------------------------------------------------------------------

def _build_prompt(*, title: str, description: str) -> str:
    """Build the classifier prompt from the ARCHETYPES dict.

    Writing the prompt this way means adding a bucket to ARCHETYPES
    automatically includes it in the prompt. No second edit required.
    """
    bucket_lines = "\n".join(
        f"  {slug}: {desc}" for slug, desc in ARCHETYPES.items()
    )
    # Truncate the description aggressively: the opening of a JD is
    # where the role archetype is nearly always stated.
    short_desc = (description or "")[:800]

    return f"""You are classifying a job posting into ONE archetype bucket.

Return ONLY a JSON object of the form:
{{"archetype": "<slug>", "confidence": <0..1>, "rationale": "<one short sentence>"}}

Valid archetype slugs (pick exactly one):
{bucket_lines}

Guidelines:
- Use the TITLE as a strong signal but override it if the description
  clearly indicates a different role type.
- "Director", "Head of", or "VP" titles go to "director" even if the
  scope sounds like an IC PM.
- TPM / Program Manager titles with a technical orientation go to "tpm".
- If it's an engineering, design, research, or marketing role, return "other".
- If genuinely unsure, still pick the closest bucket -- do not invent new slugs.

JOB TITLE: {title}

DESCRIPTION:
{short_desc}

JSON:"""


def _unclassified(reason: str) -> dict[str, Any]:
    """Return the canonical "we couldn't classify" shape so callers
    don't need to special-case each failure mode."""
    return {
        "archetype": UNCLASSIFIED,
        "confidence": 0.0,
        "rationale": reason,
    }


def _coerce_confidence(raw: Any) -> float:
    """Best-effort parse of whatever the model handed back for
    confidence. Returns 0.0 on anything we can't parse."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # Models sometimes emit 85 instead of 0.85. Normalize.
    if val > 1.0:
        val = val / 100.0
    if val < 0.0:
        return 0.0
    if val > 1.0:
        return 1.0
    return val
