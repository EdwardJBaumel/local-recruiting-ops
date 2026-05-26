"""
Multi-dimensional match scoring.

The primary `_match_score` on a packet is still the embedding-or-LLM
similarity adjusted by salary/location weights. Embeddings capture JD
vocabulary overlap well but bunch every PM-ish role into the same
cosine band — so this module derives resume-structured sub-scores for
UI transparency (``_dimensions`` on match payloads).

``ProfileFitScorer`` remains for tests and optional offline rescore;
the live match path uses bi-encoder + cross-encoder rerank instead.

Sub-scores (each in [0.0, 1.0], None when data is missing):
  seniority_fit: ordinal distance on profile seniority vs inferred job band
  tech_fit:      fraction of profile technologies present in the JD
  domain_fit:    fraction of profile domains mentioned in the JD
  years_fit:     profile years vs the expected floor for the job band
  requirements_fit: weighted blend of tech/domain/seniority for badges

All inputs come from the parsed resume profile — not hardcoded title lists.
"""
from __future__ import annotations

import re
from typing import Optional

# Strip seniority/role boilerplate from titles so the remainder is the
# lane signal ("Platform", "Data Governance", "Billing") — compared
# against the candidate fingerprint from their resume, not hardcoded lists.
_PM_TITLE_BOILERPLATE = re.compile(
    r"\b("
    r"senior|staff|principal|lead|sr\.?|group|director|head|vp|vice president|"
    r"product manager|program manager|technical program manager|tpm|"
    r"forward deployed|enterprise|global|remote|intern|internship|"
    r"junior|associate|graduate|new grad|entry[\s-]?level"
    r")\b",
    flags=re.IGNORECASE,
)
_GENERIC_LANE_WORDS = frozenset({
    "product", "manager", "management", "program", "technical", "the",
    "and", "for", "with", "team", "systems", "system", "services", "service",
})

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


def _job_text(payload: dict) -> str:
    return " ".join([
        payload.get("title") or "",
        payload.get("description") or "",
        payload.get("company") or "",
        " ".join(str(t) for t in (payload.get("technologies") or [])),
    ]).lower()


def _profile_fingerprint(profile: dict) -> set[str]:
    """Resume-derived tokens: domains, skills, stack, target-role lanes."""
    tokens: set[str] = set()
    for field in ("domains", "skills", "technologies"):
        for item in profile.get(field) or []:
            for part in re.split(r"[,/&+\-|]", str(item).lower()):
                t = part.strip()
                if len(t) >= 3 and t not in _GENERIC_LANE_WORDS:
                    tokens.add(t)
    for role in profile.get("target_roles") or []:
        tokens.update(_title_lane_tokens(str(role)))
    return tokens


def _title_lane_tokens(title: str) -> set[str]:
    """Non-boilerplate tokens left in a job title after stripping PM cruft."""
    cleaned = _PM_TITLE_BOILERPLATE.sub(" ", (title or "").lower())
    tokens: set[str] = set()
    for segment in re.split(r"[,/|\-–—]+", cleaned):
        for word in segment.split():
            w = word.strip(".,()")
            if len(w) >= 3 and w not in _GENERIC_LANE_WORDS:
                tokens.add(w)
    return tokens


def _fingerprint_token_in_text(token: str, text: str) -> bool:
    if not token or not text:
        return False
    if token in text:
        return True
    return any(
        len(part) >= 3 and (token in part or part in token)
        for part in text.split()
    )


def _fingerprint_token_matches(token: str, fingerprint: set[str]) -> bool:
    if token in fingerprint:
        return True
    return any(
        len(fp) >= 3 and (token in fp or fp in token)
        for fp in fingerprint
    )


def _score_domain(profile: dict, payload: dict) -> Optional[float]:
    """Share of the resume fingerprint (domains/skills/tech) found in the JD."""
    fp = _profile_fingerprint(profile)
    if not fp:
        return None
    text = _job_text(payload)
    hits = sum(1 for t in fp if _fingerprint_token_in_text(t, text))
    return round(hits / len(fp), 3)


def _score_lane_fit(profile: dict, payload: dict) -> Optional[float]:
    """How well the job title's lane tokens match the resume fingerprint."""
    fp = _profile_fingerprint(profile)
    title_tokens = _title_lane_tokens(payload.get("title") or "")
    if not fp or not title_tokens:
        return None
    hits = sum(1 for t in title_tokens if _fingerprint_token_matches(t, fp))
    return round(hits / len(title_tokens), 3)


def _score_target_role_fit(profile: dict, payload: dict) -> Optional[float]:
    """Overlap between parsed target_roles and the posting title."""
    targets = [str(t).lower() for t in (profile.get("target_roles") or []) if t]
    title = (payload.get("title") or "").lower()
    if not targets or not title:
        return None
    best = 0.0
    title_tokens = _title_lane_tokens(title) | set(title.split())
    for target in targets:
        target_tokens = _title_lane_tokens(target) | set(target.split())
        if not target_tokens:
            continue
        if target in title or title in target:
            return 1.0
        overlap = sum(
            1 for t in target_tokens
            if t in title or any(t in tt or tt in t for tt in title_tokens)
        )
        best = max(best, overlap / len(target_tokens))
    return round(best, 3) if best > 0 else 0.0


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


def _combine_requirements(
    tech_fit: Optional[float],
    domain_fit: Optional[float],
    seniority_fit: Optional[float],
    lane_fit: Optional[float] = None,
) -> Optional[float]:
    """Weighted blend for headline badges."""
    parts = [
        (0.30, tech_fit),
        (0.35, domain_fit),
        (0.20, lane_fit),
        (0.15, seniority_fit),
    ]
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


def merge_profile_prefs(prefs: dict, profile: dict | None) -> dict:
    """Fill scoring prefs from the parsed resume when the user has not set them.

    Keeps explicit Settings values — only backfills zeros/blanks from the
    resume so hard filters (trapdoor, band gap) use the same signal as the
    embedding profile text."""
    merged = dict(prefs or {})
    if not profile or not isinstance(profile, dict) or profile.get("error"):
        return merged
    try:
        cfg_years = int(merged.get("years_experience") or 0)
    except (TypeError, ValueError):
        cfg_years = 0
    prof_years = profile.get("years_experience")
    if cfg_years <= 0 and prof_years is not None:
        try:
            merged["years_experience"] = int(prof_years)
        except (TypeError, ValueError):
            pass
    if not (merged.get("current_level") or "").strip():
        prof_sen = (profile.get("seniority") or "").strip()
        if prof_sen:
            merged["current_level"] = prof_sen
    return merged


def _infer_job_seniority_from_payload(payload: dict) -> str:
    """Prefer title-aware band inference (Group PM → director, etc.)."""
    from core.preferences import _infer_job_level
    level = _infer_job_level(payload)
    if level:
        return level
    return _infer_job_seniority(payload)


def score_dimensions(profile: dict, payload: dict) -> dict:
    """Produce all sub-scores for a single job. Profile is the structured
    dict from resume_profile; payload is the job packet payload."""
    if not profile or not isinstance(profile, dict):
        return {}
    profile_seniority = (profile.get("seniority") or "").strip().lower()
    job_seniority = _infer_job_seniority_from_payload(payload)

    seniority_fit = _score_seniority(profile_seniority, job_seniority)
    tech_fit = _score_tech_overlap(profile.get("technologies") or [], payload)
    domain_fit = _score_domain(profile, payload)
    lane_fit = _score_lane_fit(profile, payload)
    target_role_fit = _score_target_role_fit(profile, payload)
    years_fit = _score_years(int(profile.get("years_experience") or 0), job_seniority)
    headline = _combine_requirements(tech_fit, domain_fit, seniority_fit, lane_fit)

    return {
        "seniority_fit": seniority_fit,
        "tech_fit": tech_fit,
        "domain_fit": domain_fit,
        "lane_fit": lane_fit,
        "target_role_fit": target_role_fit,
        "years_fit": years_fit,
        "requirements_fit": headline,
        "profile_seniority": profile_seniority or None,
        "job_seniority": job_seniority or None,
        "profile_fingerprint": sorted(_profile_fingerprint(profile))[:12],
    }


class ProfileFitScorer:
    """Resume-driven score adjustment from parsed profile fields.

    Uses seniority/years bands, domain+skill+tech fingerprint overlap with
    the JD, and title-lane alignment — all derived from the uploaded resume.
    """

    def __init__(self, profile: dict | None, weight: float = 1.0):
        self.profile = profile if profile and not profile.get("error") else None
        try:
            self.weight = float(weight or 1.0)
        except (TypeError, ValueError):
            self.weight = 1.0

    @property
    def active(self) -> bool:
        return bool(self.profile) and self.weight > 0

    def adjust(self, base_score: float, payload: dict) -> tuple[float, float, str]:
        if not self.active:
            return base_score, 0.0, ""
        dims = score_dimensions(self.profile, payload)
        if not dims:
            return base_score, 0.0, ""

        delta = 0.0
        parts: list[str] = []

        seniority_fit = dims.get("seniority_fit")
        if seniority_fit is not None:
            delta -= (1.0 - seniority_fit) * 0.22 * self.weight
            if seniority_fit < 0.5:
                parts.append(
                    f"seniority {dims.get('job_seniority')} vs "
                    f"{dims.get('profile_seniority')}"
                )

        years_fit = dims.get("years_fit")
        if years_fit is not None and years_fit < 1.0:
            delta -= (1.0 - years_fit) * 0.18 * self.weight
            if years_fit < 0.75:
                parts.append(
                    f"{self.profile.get('years_experience')} years vs "
                    f"{dims.get('job_seniority')} band"
                )

        lane_fit = dims.get("lane_fit")
        if lane_fit is not None and lane_fit < 0.25:
            delta -= (0.25 - lane_fit) * 0.38 * self.weight
            parts.append(f"title lane {lane_fit:.0%} vs your profile")

        domain_fit = dims.get("domain_fit")
        if domain_fit is not None:
            if domain_fit < 0.20:
                delta -= (0.20 - domain_fit) * 0.32 * self.weight
                parts.append(f"domains/skills {domain_fit:.0%} in JD")
            elif domain_fit >= 0.45:
                delta += (domain_fit - 0.45) * 0.10 * self.weight

        tech_fit = dims.get("tech_fit")
        if tech_fit is not None:
            delta += (tech_fit - 0.40) * 0.12 * self.weight
            if tech_fit < 0.25:
                parts.append(f"stack overlap {tech_fit:.0%}")

        target_role_fit = dims.get("target_role_fit")
        if target_role_fit is not None and target_role_fit >= 0.6:
            delta += (target_role_fit - 0.6) * 0.08 * self.weight

        delta = max(-0.45, min(0.10, delta))
        if abs(delta) < 0.005:
            return base_score, 0.0, ""

        from core.preferences import _clamp
        reason = "profile: " + "; ".join(parts) if parts else "profile aligned"
        return _clamp(base_score + delta), delta, reason
