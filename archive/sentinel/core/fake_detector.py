"""
Fake / ghost-job detection.

Produces a 0.0-1.0 suspicion score on each job packet using 9 weighted,
deterministic signals. None of this requires an LLM. Each signal returns
a per-signal score in [0,1] with a short reason string, or None when
there's not enough data to evaluate it. The final score is a weighted
average over the signals that fired.

Why deterministic? The user is Platform PM, not an ML team. A simple
explanation ("flagged because salary band is 6x wide and description is
80 chars") is far more defensible than a black-box classifier, and it
keeps the zero-cost guarantee of the whole pipeline.

Signals:
  a. age_stale           — posting older than a threshold (default 60 days)
  b. salary_band_wide    — min/max ratio suggests "throw a wide net" fakery
  c. location_vague      — "Remote" / "Worldwide" with no country or city
  d. seniority_conflict  — title seniority doesn't match YoE language
                           in the description (e.g. "junior" + "10+ years")
  e. buzzword_density    — high ratio of soft-buzzwords to content
  f. missing_fields      — description/technologies/responsibilities missing
  g. duplicate_title     — same title already seen for this company
                           (pass in a {company: {titles seen}} accumulator)
  h. apply_url_missing   — no apply URL on the packet
  i. overloaded_stack    — absurd number of distinct specialisms in one role

Per-signal weights are tuned so that a posting needs a mix of issues to
cross the "suspect" line — any single signal hitting 1.0 on its own is
uncomfortable, not damning.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Signal thresholds & lexicons
# ─────────────────────────────────────────────────────────────────────
#
# Age curve reworked for wider discernment (Apr 2026):
#   - Old linear 30→90 maxed out only at 90 days, which compressed the
#     "obviously stale" signal too gently. A 72-day posting scored 0.70.
#   - New exponential curve saturates earlier and ramps faster:
#       * < 14 days   → 0.00
#       *  30 days    → 0.41
#       *  60 days    → 0.79
#       *  72 days    → 0.86
#       *  90 days    → 0.93
#       * 120 days    → 0.98
#   - Result: a 72-day posting now weighs meaningfully more than a 30-day
#     posting, which matches user intent ("stale should look stale").
#
_STALE_DAYS_MIN = 14         # no penalty below this floor
_STALE_DAYS_TAU = 30         # exponential time constant (days)

_SALARY_RATIO_SOFT = 3.5     # ratio max/min starts scoring above zero.
                              # Staff+ bands at real employers legitimately
                              # span 2.5-3.5x, so we begin suspicion only
                              # beyond that.
_SALARY_RATIO_HARD = 6.0     # saturates at 1.0 here

# "Severely" vague: truly no geography at all.
_VAGUE_LOCATIONS_SEVERE = {
    "", "remote", "worldwide", "anywhere", "global",
    "remote - global", "remote (global)",
}
# "Lightly" vague: country/region only. Often legitimate on genuinely-remote
# roles filed under tax/work-authorisation jurisdiction. Downgraded from the
# severe bucket after QE feedback.
_VAGUE_LOCATIONS_LIGHT = {
    "united states", "usa", "us",
    "uk", "united kingdom",
    "europe", "emea", "apac", "americas",
}

_SENIORITY_YOE_HINTS = {
    "junior":     (0, 3),
    "mid":        (2, 6),
    "senior":     (4, 12),
    "staff":      (7, 20),
    "principal":  (10, 25),
    "director":   (8, 30),
    "vp":         (10, 30),
    "cxo":        (12, 40),
}

_TITLE_SENIORITY_TOKENS = [
    ("staff ", "staff"), ("principal ", "principal"),
    ("director of ", "director"), (" director", "director"),
    ("head of ", "director"), ("vp ", "vp"), ("vp,", "vp"),
    ("senior ", "senior"), ("lead ", "senior"),
    ("sr. ", "senior"), ("sr ", "senior"),
    ("junior", "junior"), ("associate ", "junior"), ("graduate", "junior"),
]

_BUZZWORDS = {
    "rockstar", "ninja", "guru", "wizard", "superstar",
    "wear many hats", "wear multiple hats",
    "fast-paced", "fast paced", "hit the ground running",
    "self-starter", "go-getter", "passionate", "driven",
    "dynamic environment", "thrive in chaos",
    "work hard play hard", "think outside the box",
    # QE-dropped: bare "family" / "tribe" hit legitimate benefits copy
    # ("family leave") and Spotify-style "engineering tribe" org design.
    # We only flag the explicit "we are a family" phrasing.
    "we're a family", "we are a family",
    "unicorn", "disruptive", "synergy",
    "competitive salary", "salary commensurate",
}

# Short, content-bearing tokens used for specificity normalisation in (e)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\.#\-]*")

# Stack categories — if a role demands coverage across too many, it's
# probably a wishlist rather than one job.
_STACK_CATEGORIES = {
    "backend_langs": {
        "python", "java", "go", "golang", "rust", "ruby", "php",
        "c#", ".net", "kotlin", "scala", "elixir", "c++",
    },
    "frontend_langs": {
        "javascript", "typescript", "react", "vue", "angular", "svelte",
    },
    "clouds": {"aws", "gcp", "google cloud", "azure", "digitalocean", "heroku"},
    "data_stores": {
        "postgres", "postgresql", "mysql", "mongodb", "cassandra", "redis",
        "dynamodb", "bigquery", "snowflake", "elasticsearch",
    },
    "infra": {"kubernetes", "docker", "terraform", "ansible", "pulumi", "helm"},
    "ml": {"pytorch", "tensorflow", "jax", "scikit-learn", "hugging face"},
}

_OVERLOAD_CATEGORY_THRESHOLD_SOFT = 3    # >=3 distinct categories starts firing
_OVERLOAD_CATEGORY_THRESHOLD_HARD = 5    # >=5 saturates
_OVERLOAD_TECH_COUNT_SOFT = 10           # raw technology count softer signal
_OVERLOAD_TECH_COUNT_HARD = 18


# ─────────────────────────────────────────────────────────────────────
# Per-signal scorers
# ─────────────────────────────────────────────────────────────────────
def _parse_posted_at(payload: dict) -> Optional[datetime]:
    for key in ("posted_at", "posted_date", "date_posted", "_first_seen"):
        v = payload.get(key)
        if not v:
            continue
        if isinstance(v, (int, float)):
            try:
                return datetime.fromtimestamp(v, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
        if isinstance(v, str):
            s = v.strip()
            if not s:
                continue
            # Try ISO 8601 first.
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                pass
            # Fallback: common date-only formats.
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    return None


def _score_age(payload: dict, now: Optional[datetime] = None) -> Optional[tuple]:
    """(a) Stale posting. Returns (score, reason) or None if no date.

    Uses an exponential saturation curve:
        score = 1 - exp(-(age_days - MIN) / TAU)

    with score clamped to [0, 1]. This puts more weight on the 30-90 day
    range where ghost postings actually live, and less on the "brand new"
    and "ancient" extremes where the signal is already clear from context.
    """
    import math
    posted = _parse_posted_at(payload)
    if posted is None:
        return None
    now = now or datetime.now(tz=timezone.utc)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    age_days = (now - posted).days
    if age_days <= _STALE_DAYS_MIN:
        return 0.0, f"posted {age_days}d ago"
    scaled = 1.0 - math.exp(-(age_days - _STALE_DAYS_MIN) / _STALE_DAYS_TAU)
    scaled = max(0.0, min(1.0, scaled))
    flag = " (stale)" if scaled >= 0.8 else ""
    return round(scaled, 3), f"posted {age_days}d ago{flag}"


def _score_salary_band(payload: dict) -> Optional[tuple]:
    """(b) Unreasonably wide salary band. None if band absent or invalid."""
    try:
        lo = payload.get("salary_min") or payload.get("comp_min") or 0
        hi = payload.get("salary_max") or payload.get("comp_max") or 0
        lo = float(lo or 0)
        hi = float(hi or 0)
    except (TypeError, ValueError):
        return None
    if lo <= 0 or hi <= 0 or hi < lo:
        return None
    ratio = hi / lo
    if ratio <= _SALARY_RATIO_SOFT:
        return 0.0, f"band ratio {ratio:.1f}x"
    if ratio >= _SALARY_RATIO_HARD:
        return 1.0, f"band ratio {ratio:.1f}x (very wide)"
    span = _SALARY_RATIO_HARD - _SALARY_RATIO_SOFT
    scaled = (ratio - _SALARY_RATIO_SOFT) / span
    return round(scaled, 3), f"band ratio {ratio:.1f}x"


def _score_location_vague(payload: dict) -> Optional[tuple]:
    """(c) Vague location. Returns None when location field is absent.

    Two tiers after QE feedback: "no geography at all" is severely suspect
    (score 0.7), "country-only" is only mildly suspect (0.3) because a
    genuinely-remote role often lists a country for tax/work-authorisation
    reasons. When the packet has `remote: True`, country-only is treated
    as benign."""
    loc = payload.get("location")
    if loc is None:
        return None
    loc_norm = str(loc).strip().lower()
    if not loc_norm:
        return 1.0, "no location given"
    if loc_norm in _VAGUE_LOCATIONS_SEVERE:
        return 0.7, f"vague location '{loc}'"
    # "Remote - <region>" with nothing specific after the hyphen is still vague.
    if loc_norm.startswith("remote") and len(loc_norm) <= 12:
        return 0.7, f"vague location '{loc}'"
    if loc_norm in _VAGUE_LOCATIONS_LIGHT:
        # Remote flag makes country-only benign.
        if payload.get("remote") in (True, "true", "True", 1):
            return 0.0, f"location '{loc}' (remote role, country-level is fine)"
        return 0.3, f"country-only location '{loc}'"
    return 0.0, f"location '{loc}'"


def _title_seniority(title: str) -> str:
    t = (title or "").lower()
    for tok, label in _TITLE_SENIORITY_TOKENS:
        if tok in t:
            return label
    return ""


# Capture a lower bound (required) and optionally an upper bound from
# ranges like "3-7 years" or "3 to 7 yrs". Without the upper bound we'd
# over-flag senior roles that say "3+ years" or "3 to 7 years" as conflicts.
_YOE_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|(?:to|-|–|—)\s*(\d{1,2}))?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def _score_seniority_conflict(payload: dict) -> Optional[tuple]:
    """(d) Title seniority disagrees with years-of-experience language.

    Handles ranges: for "3-7 years" we use the upper bound (7) when the
    lower would look conflicting with a senior title. For "10+ years" we
    use the lower bound. This keeps "Senior role asking 3-7y" from being
    flagged as a conflict when 7y is perfectly reasonable."""
    title_sen = _title_seniority(payload.get("title") or "")
    if not title_sen:
        return None
    desc = payload.get("description") or ""
    m = _YOE_RANGE_RE.search(desc)
    if not m:
        return None
    try:
        low = int(m.group(1))
    except (TypeError, ValueError):
        return None
    high = None
    if m.group(2):
        try:
            high = int(m.group(2))
        except (TypeError, ValueError):
            high = None
    lo, hi = _SENIORITY_YOE_HINTS[title_sen]
    # When we have a range, any overlap with the plausible band is fine.
    # Only flag when the range lies entirely outside.
    if high is not None:
        r_lo, r_hi = low, high
        if r_hi < lo:
            gap = lo - r_hi
            scaled = min(1.0, gap / 6.0)
            return round(scaled, 3), (
                f"{title_sen} role asks for {r_lo}-{r_hi}y (conflicts)"
            )
        if r_lo > hi:
            gap = r_lo - hi
            scaled = min(1.0, gap / 6.0)
            return round(scaled, 3), (
                f"{title_sen} role asks for {r_lo}-{r_hi}y (conflicts)"
            )
        return 0.0, f"{title_sen} role asks for {r_lo}-{r_hi}y (consistent)"
    # Open-ended "X+ years" or just "X years": treat X as the required floor.
    if lo <= low <= hi:
        return 0.0, f"{title_sen} role asks for {low}y (consistent)"
    if low < lo:
        gap = lo - low
    else:
        gap = low - hi
    scaled = min(1.0, gap / 6.0)
    return round(scaled, 3), f"{title_sen} role asks for {low}y (conflicts)"


def _tokenise_words(text: str) -> list:
    return _WORD_RE.findall(text or "")


def _score_buzzword_density(payload: dict) -> Optional[tuple]:
    """(e) High ratio of buzzwords to content words. Works on description
    + title. Short descriptions can't meaningfully score here."""
    text = ((payload.get("description") or "") + " " + (payload.get("title") or "")).lower()
    words = _tokenise_words(text)
    if len(words) < 80:
        return None
    hits = 0
    matched: list = []
    for phrase in _BUZZWORDS:
        if phrase in text:
            hits += 1
            if len(matched) < 3:
                matched.append(phrase)
    # Normalise: 0 hits → 0.0, 3 → 0.5, 6+ → 1.0.
    scaled = min(1.0, hits / 6.0)
    if hits == 0:
        return 0.0, "no buzzwords"
    return round(scaled, 3), f"{hits} buzzwords ({', '.join(matched)})"


def _score_missing_fields(payload: dict) -> Optional[tuple]:
    """(f) Key fields missing or implausibly thin."""
    description = (payload.get("description") or "").strip()
    techs = payload.get("technologies") or []
    issues: list = []
    score_parts: list = []
    if len(description) < 200:
        issues.append(f"description {len(description)} chars")
        score_parts.append(0.6)
    if not techs:
        issues.append("no technologies listed")
        score_parts.append(0.3)
    if not payload.get("company"):
        issues.append("no company")
        score_parts.append(0.4)
    if not payload.get("title"):
        issues.append("no title")
        score_parts.append(0.6)
    if not score_parts:
        return 0.0, "fields present"
    score = min(1.0, sum(score_parts))
    return round(score, 3), "; ".join(issues)


def _score_duplicate_title(payload: dict, title_index: Optional[dict]) -> Optional[tuple]:
    """(g) Same title already seen at this company in this cycle. The
    caller passes a {company_lower: set[(title_lower, location_lower)]}
    accumulator; the first time a (company, title, location) triple is
    seen it's recorded and scores 0. Re-appearances score 1.0.

    Location is included in the key so legitimate multi-city postings
    ("Software Engineer" in SF + NYC + Austin) don't all flag the 2nd
    and 3rd copies."""
    if title_index is None:
        return None
    title = (payload.get("title") or "").strip().lower()
    company = (payload.get("company") or "").strip().lower()
    if not title or not company:
        return None
    location = (payload.get("location") or "").strip().lower()
    key = (title, location)
    seen = title_index.setdefault(company, set())
    if key in seen:
        return 1.0, "duplicate (title, location) at same company"
    seen.add(key)
    return 0.0, "unique title"


def _score_apply_url(payload: dict) -> Optional[tuple]:
    """(h) Apply URL missing. Doesn't penalise third-party ATS URLs — those
    are the norm for real postings."""
    url = payload.get("apply_url") or payload.get("url") or ""
    url = str(url).strip()
    if not url:
        return 0.8, "no apply URL"
    return 0.0, "has apply URL"


def _score_overloaded_stack(payload: dict) -> Optional[tuple]:
    """(i) Role demands an implausible breadth of technology. Scores on
    both "how many distinct categories touched" and "raw count", takes max."""
    techs = [str(t).lower() for t in (payload.get("technologies") or []) if t]
    if not techs:
        return None
    cats_hit: set = set()
    for cat, members in _STACK_CATEGORIES.items():
        if any(t in members for t in techs):
            cats_hit.add(cat)
    # Category-count sub-score
    if len(cats_hit) <= _OVERLOAD_CATEGORY_THRESHOLD_SOFT:
        cat_score = 0.0
    elif len(cats_hit) >= _OVERLOAD_CATEGORY_THRESHOLD_HARD:
        cat_score = 1.0
    else:
        span = _OVERLOAD_CATEGORY_THRESHOLD_HARD - _OVERLOAD_CATEGORY_THRESHOLD_SOFT
        cat_score = (len(cats_hit) - _OVERLOAD_CATEGORY_THRESHOLD_SOFT) / span
    # Raw-count sub-score
    n = len(set(techs))
    if n <= _OVERLOAD_TECH_COUNT_SOFT:
        count_score = 0.0
    elif n >= _OVERLOAD_TECH_COUNT_HARD:
        count_score = 1.0
    else:
        span = _OVERLOAD_TECH_COUNT_HARD - _OVERLOAD_TECH_COUNT_SOFT
        count_score = (n - _OVERLOAD_TECH_COUNT_SOFT) / span
    final = max(cat_score, count_score)
    if final <= 0:
        return 0.0, f"{n} techs across {len(cats_hit)} categories"
    return round(final, 3), f"{n} techs across {len(cats_hit)} categories"


# ─────────────────────────────────────────────────────────────────────
# Composer
# ─────────────────────────────────────────────────────────────────────
# Weights reworked Apr 2026 for wider discernment range.
#
# Prior weights treated every signal as small-and-equal so the final score
# rarely left the 0.25-0.50 band. Users reported the "ghost %" column was
# "hard to read" — everything looked borderline. New weights bias heavily
# toward the strongest signals (age, missing fields, duplicate title,
# seniority conflict) so a posting that fires several of them lands
# decisively above 0.7, while a clean posting lands decisively below 0.2.
_SIGNAL_WEIGHTS = {
    "age_stale":          0.20,   # bumped from 0.10 — age is the clearest tell
    "salary_band_wide":   0.06,
    "location_vague":     0.06,
    "seniority_conflict": 0.14,
    "buzzword_density":   0.06,
    "missing_fields":     0.20,
    "duplicate_title":    0.14,
    "apply_url_missing":  0.06,
    "overloaded_stack":   0.08,
}

# Score above which a posting is flagged as "suspect". A mix of issues is
# needed to cross this line; a single signal can't do it alone. This is
# the default; callers can override via the `threshold` arg or use the
# named presets below.
GHOST_SUSPECT_THRESHOLD = 0.45

# Named aggressiveness presets surfaced in the Settings UI. The map lets
# the server translate "low" / "balanced" / "strict" into a numeric
# threshold without spreading config lookups through the codebase.
AGGRESSIVENESS_PRESETS = {
    "low":      0.60,   # only obvious ghosts cross the line
    "balanced": 0.45,   # default
    "strict":   0.30,   # flags borderline roles more readily
}


def resolve_threshold(preset_or_value) -> float:
    """Accept a preset name ("low"/"balanced"/"strict") or a float and
    return the effective suspicion threshold. Unknown input falls back
    to the default so a broken config can't hide every posting."""
    if isinstance(preset_or_value, (int, float)):
        v = float(preset_or_value)
        if 0.0 <= v <= 1.0:
            return v
    if isinstance(preset_or_value, str):
        return AGGRESSIVENESS_PRESETS.get(preset_or_value.strip().lower(), GHOST_SUSPECT_THRESHOLD)
    return GHOST_SUSPECT_THRESHOLD


def score_fake(payload: dict, title_index: Optional[dict] = None,
               now: Optional[datetime] = None,
               threshold: Optional[float] = None) -> dict:
    """Return fake-suspicion signals for a single job packet.

    Shape:
      {
        "score": 0.0-1.0,
        "is_suspect": bool,
        "signals": {
            "<signal_name>": {"score": float, "reason": str},
            ...
        },
      }

    `title_index` is an optional {company_lower: set[title_lower]} accumulator
    threaded through a cycle's packets so the duplicate-title signal can fire.
    """
    if not payload or not isinstance(payload, dict):
        return {"score": 0.0, "is_suspect": False, "signals": {}}

    results: dict = {
        "age_stale":          _score_age(payload, now=now),
        "salary_band_wide":   _score_salary_band(payload),
        "location_vague":     _score_location_vague(payload),
        "seniority_conflict": _score_seniority_conflict(payload),
        "buzzword_density":   _score_buzzword_density(payload),
        "missing_fields":     _score_missing_fields(payload),
        "duplicate_title":    _score_duplicate_title(payload, title_index),
        "apply_url_missing":  _score_apply_url(payload),
        "overloaded_stack":   _score_overloaded_stack(payload),
    }

    signals: dict = {}
    weighted_sum = 0.0
    for name, outcome in results.items():
        if outcome is None:
            continue
        score, reason = outcome
        signals[name] = {"score": score, "reason": reason}
        weighted_sum += _SIGNAL_WEIGHTS[name] * score

    # Normalise over the FULL weight table, not just fired signals.
    # Dividing by fired-weight-only was the calibration bug QE flagged:
    # a handful of weak signals on an otherwise-clean job could tip over
    # threshold just because most signals couldn't be evaluated. Treating
    # "can't evaluate" as 0 keeps unknowns conservative.
    total_weight = sum(_SIGNAL_WEIGHTS.values())
    weighted_avg = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Severity floor. The weighted average dilutes a strong single
    # signal — a 72-day-old posting scores age=0.85 but the weighted
    # average is only 0.17 after everything else scores 0. That reads
    # as "mostly fine" even though the age signal alone is damning.
    #
    # We blend the weighted average with a fraction of the worst
    # individual signal, taking the max. Coefficient tuned so:
    #   - One signal at 1.0 alone → 0.70 raw
    #   - One signal at 0.85 alone → 0.60 raw (72-day age case)
    #   - Mixed moderate signals stay governed by the weighted average
    max_fired = max((s["score"] for s in signals.values()), default=0.0)
    raw = max(weighted_avg, max_fired * 0.70)

    # Calibration curve. Pulls mid-range scores further apart so the
    # rendered percentage has meaningful resolution between "probably
    # fine" and "almost certainly stale". Using pow(x, 0.65) as a soft
    # easing: identity at endpoints, lifts the mid-band.
    #   0.00 → 0.00        0.60 → 0.72
    #   0.20 → 0.36        0.80 → 0.87
    #   0.40 → 0.56        1.00 → 1.00
    calibrated = round(raw ** 0.65, 3) if raw > 0 else 0.0
    final = calibrated
    effective_threshold = GHOST_SUSPECT_THRESHOLD if threshold is None else float(threshold)
    return {
        "score": final,
        "score_raw": round(raw, 3),
        "is_suspect": final >= effective_threshold,
        "threshold": effective_threshold,
        "signals": signals,
    }
