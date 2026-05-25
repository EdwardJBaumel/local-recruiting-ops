"""
Multi-dimensional match scoring.

The primary `_match_score` on a packet is still the embedding-or-LLM
similarity adjusted by the salary weight. That's good at capturing the
full-text vibe of "does this role look like the candidate?", but it's
opaque - a 72% score is hard to reason about.

This module adds deterministic sub-scores a human can audit:
  seniority_fit: ordinal distance on the candidate's seniority vs the job's
  tech_fit:      fraction of the candidate's listed technologies present
                 in the JD's technologies list + description
  domain_fit:    fraction of the candidate's domains mentioned in the JD
  years_fit:     how well the candidate's years of experience fit the
                 expected band for the job's seniority
  requirements_hit: a combined "how many must-haves are covered" view

Each sub-score is in [0.0, 1.0]. Missing profile data for a dimension
returns None for that dimension so the UI can render "—" instead of
pretending it scored zero.

Used for transparency (Brief tab + match detail) and as an optional
tiebreaker when the base score is close to threshold.
"""
from __future__ import annotations

import re
from typing import Optional

_SENIORITY_ORDER = {
    "intern": 0, "entry": 1, "junior": 1, "mid": 2, "senior": 3,
    "staff": 4, "principal": 5, "director": 6, "vp": 7, "cxo": 8,
}
# Rough expected years-of-experience floors per seniority level.
_SENIORITY_YEARS_FLOOR = {
    "intern": 0, "entry": 0, "junior": 0, "mid": 2, "senior": 5,
    "staff": 8, "principal": 10, "director": 10, "vp": 12, "cxo": 15,
}
# Seniority tokens we'll pull from a job title when the job doesn't have
# a tagged seniority field.
_TITLE_SENIORITY_TOKENS = [
    ("intern", "intern"), ("internship", "intern"),
    ("staff", "staff"), ("principal", "principal"),
    ("director of", "director"), (" director", "director"),
    ("head of", "director"), ("vp ", "vp"), ("vp,", "vp"),
    ("senior ", "senior"), ("lead ", "senior"), ("sr. ", "senior"), ("sr ", "senior"),
    ("entry-level", "entry"), ("entry level", "entry"),
    ("junior", "junior"), ("associate", "junior"), ("graduate", "entry"), ("new grad", "entry"),
]


def _normalise_seniority(value: str) -> str:
    """Map a freeform seniority string ("Senior II", "Lead") to the enum."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if v in _SENIORITY_ORDER:
        return v
    for tok, label in _TITLE_SENIORITY_TOKENS:
        if tok in v:
            return label
    return ""


def _infer_job_seniority(payload: dict) -> str:
    """Prefer a tagged seniority field; else peek at the title."""
    tagged = _normalise_seniority(payload.get("seniority") or "")
    if tagged:
        return tagged
    title = (payload.get("title") or "").lower()
    for tok, label in _TITLE_SENIORITY_TOKENS:
        if tok in title:
            return label
    return ""


def _score_seniority(profile_seniority: str, job_seniority: str) -> Optional[float]:
    """Ordinal match: 1.0 same, 0.75 off by 1, 0.4 off by 2, 0.15 beyond.
    None when either side is unknown - we don't want a missing field
    reading as a zero."""
    p = _SENIORITY_ORDER.get(profile_seniority or "")
    j = _SENIORITY_ORDER.get(job_seniority or "")
    if not p or not j:
        return None
    gap = abs(p - j)
    if gap == 0:
        return 1.0
    if gap == 1:
        return 0.75
    if gap == 2:
        return 0.40
    return 0.15


def _tech_in_text(tech: str, text: str) -> bool:
    """Match a tech name in free text without tripping over embedded
    substrings. `\\b` doesn't work for names like `c++`, `c#`, `.net`,
    `node.js` because `+`, `#`, `.` are non-word characters. We use
    explicit alphanumeric boundaries that tolerate punctuation inside
    the token."""
    if not tech:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(tech)}(?![A-Za-z0-9])"
    return re.search(pattern, text) is not None


def _score_tech_overlap(profile_techs: list, payload: dict) -> Optional[float]:
    """Fraction of the candidate's listed technologies that appear in the
    JD. Looks in both the structured technologies field and the free-text
    description, because JDs often only list half their stack up front."""
    if not profile_techs:
        return None
    techs = {t.lower() for t in profile_techs if t}
    if not techs:
        return None
    jd_techs = {str(t).lower() for t in (payload.get("technologies") or []) if t}
    description = (payload.get("description") or "") + " " + (payload.get("title") or "")
    description = description.lower()
    hits = 0
    for t in techs:
        if t in jd_techs:
            hits += 1
        elif _tech_in_text(t, description):
            hits += 1
    return round(hits / len(techs), 3)


def _score_domain(profile_domains: list, payload: dict) -> Optional[float]:
    """Fraction of the candidate's domains that are mentioned in the JD.
    We match on substring (domains are short: 'fintech', 'devtools') so
    no tokenisation worries."""
    if not profile_domains:
        return None
    doms = [d.lower() for d in profile_domains if d]
    if not doms:
        return None
    text = " ".join([
        payload.get("description") or "",
        payload.get("title") or "",
        payload.get("company") or "",
    ]).lower()
    hits = sum(1 for d in doms if d in text)
    return round(hits / len(doms), 3)


def _score_years(profile_years: int, job_seniority: str) -> Optional[float]:
    """Compare the candidate's years of experience against the seniority's
    expected floor. Overshooting is fine (capped at 1.0); undershooting
    scales down to 0.0 at zero years."""
    # Clamp: treat negative/None as unknown, but zero is a legitimate value
    # for a junior/new-grad profile so it mustn't short-circuit to None.
    try:
        py = int(profile_years)
    except (TypeError, ValueError):
        return None
    if py < 0:
        return None
    if not job_seniority:
        return None
    floor = _SENIORITY_YEARS_FLOOR.get(job_seniority)
    if floor is None:
        return None
    if floor <= 0:
        # Junior role - any non-negative years passes.
        return 1.0
    if py >= floor:
        return 1.0
    return round(py / floor, 3)


def _combine_requirements(tech_fit: Optional[float], domain_fit: Optional[float],
                          seniority_fit: Optional[float]) -> Optional[float]:
    """A simple weighted blend of the three transparency signals, treating
    None as "not scored" (average over what we have). Used as a single
    headline number for badges where space is tight."""
    parts = [(0.5, tech_fit), (0.3, domain_fit), (0.2, seniority_fit)]
    total_weight = 0.0
    value = 0.0
    for w, s in parts:
        if s is None:
            continue
        value += w * s
        total_weight += w
    if total_weight <= 0:
        return None
    return round(value / total_weight, 3)


def score_dimensions(profile: dict, payload: dict) -> dict:
    """Produce all sub-scores for a single job. Profile is the structured
    dict from resume_profile; payload is the job packet payload."""
    if not profile or not isinstance(profile, dict):
        return {}
    profile_seniority = (profile.get("seniority") or "").strip().lower()
    job_seniority = _infer_job_seniority(payload)

    seniority_fit = _score_seniority(profile_seniority, job_seniority)
    tech_fit = _score_tech_overlap(profile.get("technologies") or [], payload)
    domain_fit = _score_domain(profile.get("domains") or [], payload)
    years_fit = _score_years(int(profile.get("years_experience") or 0), job_seniority)
    headline = _combine_requirements(tech_fit, domain_fit, seniority_fit)

    return {
        "seniority_fit": seniority_fit,
        "tech_fit": tech_fit,
        "domain_fit": domain_fit,
        "years_fit": years_fit,
        "requirements_fit": headline,
        "profile_seniority": profile_seniority or None,
        "job_seniority": job_seniority or None,
    }
