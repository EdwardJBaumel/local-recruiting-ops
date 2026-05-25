"""
User preference filters and scoring weights for the match pipeline.

Split by behaviour:
  - LocationFilter: HARD filter. Drops jobs whose work-mode (remote / hybrid
    / onsite) or specific location violates the user's rule. Work mode and
    location are now independent: a user who allows all three modes with no
    allowed_locations effectively says "any location whatsoever", while a
    user who wants only hybrid and onsite within London can set work_modes
    = {hybrid, onsite} plus allowed_locations = [london]. Runs before the
    scorer so we don't waste LLM or embedding calls on roles we'd never
    consider.
  - ExperienceFilter: HARD filter. Drops jobs whose seniority level is
    more than `max_level_gap` bands away from the user's current level,
    OR whose required years exceed the user's by `max_years_gap` or more,
    OR that are Director/VP when the user has fewer than 10 years. Catches
    the "Platform PM sees Director of Product at Google scored 63%" case
    the cosine-only path would otherwise let through.
  - ExperienceScorer: SOFT weight. Gently lowers the score when the years
    gap is in the 3 to 7 range (hard drop kicks in at 8+). Calibrated so
    a default weight of 0.04 takes roughly 0.20 off a cosine score at the
    upper end, enough to bump an overreaching role below threshold.
  - SalaryScorer: SOFT weight. Adjusts the match score after the fact.
    Jobs with no salary data get a neutral penalty rather than a drop
    because ~60% of listings omit salary and we still want to surface them.

All are cheap pure-Python; they do not call Ollama or the embedder.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger("sentinel.preferences")

# Rough USD-equivalent conversions for the salary parser. Good enough for
# "is this a ballpark we care about" - we do not need FX precision.
_CURRENCY_TO_USD = {
    "$": 1.0, "USD": 1.0,
    "£": 1.27, "GBP": 1.27,
    "€": 1.08, "EUR": 1.08,
    "C$": 0.73, "CAD": 0.73,
    "A$": 0.66, "AUD": 0.66,
}

# Word tokens used to classify a job's work mode from its remote field
# and/or location text. Order matters: remote is checked first because
# "remote hybrid" shows up occasionally and we treat that as remote.
_WORK_MODE_TOKENS = {
    "remote": ("remote", "anywhere", "distributed", "worldwide", "work from home", "wfh"),
    "hybrid": ("hybrid", "flex arrangement", "flexible schedule", "partially remote"),
    "onsite": ("onsite", "on-site", "on site", "in office", "in-office", "in person", "in-person"),
}
# Legacy token set kept so the blocked-list check can skip remote words
# when matching freeform location text.
_REMOTE_TOKENS = set(_WORK_MODE_TOKENS["remote"])
_VALID_MODES = ("remote", "hybrid", "onsite")


# ──────────────────────────────────────────────────────────────────
# Country detection (hard filter gate)
# ──────────────────────────────────────────────────────────────────
# Map ISO-2 country code to the set of lowercase substrings that,
# when found in a location string, mean the job is in that country.
# Order matters: a later entry overrides an earlier one only if we
# change the iteration (we don't). The "US" bucket includes state
# codes as whole tokens (" ca", " ny") to avoid false hits on
# "Canada", "Pinterest" etc.
_US_STATE_CODES = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "dc",
)

# Full-name / synonym hits. Matched as substrings of the lowercased
# location string. A single hit is enough — most listings say e.g.
# "Bangalore, India" or "Dublin, Ireland" so the country name is
# nearly always explicit. City hits exist as a fallback for "London"
# without a country suffix.
_COUNTRY_TOKENS: dict[str, tuple[str, ...]] = {
    "US": (
        "united states", "u.s.a", " usa", "usa,", "u.s.",
        " us,", ", us", "/us", "-us", "remote - us", "remote, us",
        "remote (us", "us only", "us-only", "us remote",
        # Full state names (listed BEFORE city list so they catch first)
        "alabama", "alaska", "arizona", "arkansas", "california",
        "colorado", "connecticut", "delaware", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
        "kentucky", "louisiana", "maine", "maryland", "massachusetts",
        "michigan", "minnesota", "mississippi", "missouri", "montana",
        "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
        "new york", "north carolina", "north dakota", "ohio", "oklahoma",
        "oregon", "pennsylvania", "rhode island", "south carolina",
        "south dakota", "tennessee", "texas", "utah", "vermont",
        "virginia", "washington state", "west virginia", "wisconsin",
        "wyoming",
        # Top US metros (don't overlap with state names)
        "san francisco", "los angeles", "seattle", "boston", "chicago",
        "austin", "denver", "atlanta", "portland", "san jose",
        "palo alto", "mountain view", "menlo park", "sunnyvale", "irvine",
        "san diego", "washington dc", "washington, d.c", "miami",
        "brooklyn", "manhattan", "bellevue", "redmond", "cambridge ma",
        "cupertino", "santa clara", "livingston, nj", "houston", "phoenix",
        "philadelphia", "dallas", "fort worth", "jacksonville", "charlotte",
        "indianapolis", "columbus", "detroit", "nashville", "memphis",
        "baltimore", "milwaukee", "oakland", "tulsa", "minneapolis",
        "cleveland", "tampa", "orlando", "sacramento", "honolulu",
        "pittsburgh", "cincinnati", "raleigh", "st. louis", "saint louis",
        "new orleans", "kansas city", "las vegas", "salt lake",
        "albuquerque", "omaha", "newark", "arlington", "st. paul",
        "saint paul", "hoboken", "jersey city", "stamford", "hartford",
        "providence", "richmond", "durham", "charleston", "columbus oh",
        "buffalo", "rochester", "syracuse", "albany",
    ),
    "GB": (
        "united kingdom", " uk,", ", uk", "/uk", "-uk", "great britain",
        "england", "scotland", "wales", "london", "manchester",
        "edinburgh", "birmingham", "leeds", "bristol", "cambridge uk",
        "oxford",
    ),
    "IE": ("ireland", "dublin", " cork", "galway", "limerick"),
    "CA": (
        "canada", "toronto", "vancouver", "montreal", "ottawa",
        "calgary", "edmonton", "waterloo",
    ),
    "IN": (
        "india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad",
        "chennai", "pune", "noida", "gurgaon", "gurugram", "kolkata",
    ),
    "MX": (
        "mexico", "ciudad de m", "guadalajara", "monterrey", "cdmx",
    ),
    "BR": ("brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro"),
    "DE": ("germany", "berlin", "munich", "hamburg", "frankfurt"),
    "FR": ("france", "paris", "lyon", "toulouse"),
    "NL": ("netherlands", "amsterdam", "rotterdam", "the hague"),
    "PL": ("poland", "warsaw", "krakow", "kraków", "gdansk"),
    "AU": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "NZ": ("new zealand", "auckland", "wellington"),
    "JP": ("japan", "tokyo", "osaka", "kyoto"),
    "SG": ("singapore",),
    "PH": ("philippines", "manila", "cebu"),
    "ID": ("indonesia", "jakarta"),
    "AR": ("argentina", "buenos aires"),
    "CL": ("chile", "santiago"),
    "CO": ("colombia", "bogot"),
    "ES": ("spain", "madrid", "barcelona"),
    "PT": ("portugal", "lisbon", "porto"),
    "IT": ("italy", "milan", "rome"),
    "IL": ("israel", "tel aviv", "jerusalem"),
    "AE": ("united arab emirates", "dubai", "abu dhabi"),
    "ZA": ("south africa", "cape town", "johannesburg"),
    "KR": ("south korea", "seoul"),
    "CN": ("china", "shanghai", "beijing", "shenzhen"),
    "HK": ("hong kong",),
    "TW": ("taiwan", "taipei"),
    "VN": ("vietnam", "ho chi minh"),
    "TH": ("thailand", "bangkok"),
    "EG": ("egypt", "cairo"),
    "NG": ("nigeria", "lagos"),
    "KE": ("kenya", "nairobi"),
    "CH": ("switzerland", "zurich", "geneva"),
    "SE": ("sweden", "stockholm"),
    "DK": ("denmark", "copenhagen"),
    "NO": ("norway", "oslo"),
    "FI": ("finland", "helsinki"),
    "BE": ("belgium", "brussels"),
    "AT": ("austria", "vienna"),
    "CZ": ("czech", "prague"),
    "RO": ("romania", "bucharest"),
    "GR": ("greece", "athens"),
    "TR": ("turkey", "istanbul", "ankara"),
    "RU": ("russia", "moscow"),
    "UA": ("ukraine", "kyiv", "kiev"),
}

# Aliases the user might type in Settings. Normalised to ISO-2.
_COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a": "US",
    "united states": "US", "america": "US",
    "uk": "GB", "u.k.": "GB", "united kingdom": "GB",
    "great britain": "GB", "britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "gb": "GB",
    "ireland": "IE", "eire": "IE", "ie": "IE",
    "canada": "CA", "ca": "CA",
    "germany": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "netherlands": "NL", "holland": "NL", "nl": "NL",
    "australia": "AU", "au": "AU",
    "india": "IN", "in": "IN",
    "spain": "ES", "es": "ES",
    "italy": "IT", "it": "IT",
    "japan": "JP", "jp": "JP",
    "singapore": "SG", "sg": "SG",
}


def _normalise_country(raw) -> str:
    """Map a user-supplied country string to ISO-2. Returns '' if unknown."""
    if not raw:
        return ""
    s = str(raw).strip().lower()
    if not s:
        return ""
    # Already an ISO-2?
    if len(s) == 2 and s.upper() in _COUNTRY_TOKENS:
        return s.upper()
    return _COUNTRY_ALIASES.get(s, "")


def detect_country(location: Optional[str], remote_hint: Optional[str] = None) -> str:
    """Best-effort country detection. Returns ISO-2 code, or '' if unknown.

    We scan in a stable order: country-name tokens first, then US state
    codes only if the string still looks US-shaped (has ", XX" or " XX "
    where XX is a state code). The "remote - us" convention is honoured
    via the `remote_hint` argument.
    """
    if not location and not remote_hint:
        return ""
    text = f"{location or ''} {remote_hint or ''}".lower()
    if not text.strip():
        return ""

    # Pure remote, no country suffix: let caller treat as unknown.
    stripped = text.strip(" -,/|")
    if stripped in _REMOTE_TOKENS:
        return ""

    # Country-name / city-token pass.
    for iso, tokens in _COUNTRY_TOKENS.items():
        for tok in tokens:
            if tok in text:
                return iso

    # State-code fallback, only for US. Looks for ", CA" / " CA " / "(CA)"
    # shapes so we don't false-hit "Canada" (ca) or "Pinterest" (in).
    for code in _US_STATE_CODES:
        needle_a = f", {code}"
        needle_b = f" {code} "
        needle_c = f"({code})"
        if needle_a in text or needle_b in text or needle_c in text:
            return "US"

    return ""


class CountryFilter:
    """Hard country gate. Unlike LocationFilter this is always hard by
    default because the user was seeing Bangalore/Mexico/Brazil leaking
    through the soft location penalty.

    Preference keys consumed:
      - allowed_countries (list[str]): ISO-2 codes or aliases. Empty
        list = no filtering (permissive).
      - country_mode ("hard" | "soft", default "hard"): in soft mode
        the class becomes a no-op and `active` returns False.
      - allow_remote_any_country (bool, default True): when True, jobs
        classified as remote pass even if their country is elsewhere.
        Turn off to also gate remote roles.
    """

    def __init__(self, prefs: dict):
        raw = prefs.get("allowed_countries") or []
        if isinstance(raw, str):
            raw = [raw]
        allowed: set[str] = set()
        for entry in raw:
            iso = _normalise_country(entry)
            if iso:
                allowed.add(iso)
        self.allowed = allowed
        mode = str(prefs.get("country_mode") or "hard").strip().lower()
        self.mode = mode if mode in ("hard", "soft") else "hard"
        self.allow_remote_any_country = bool(
            prefs.get("allow_remote_any_country", True)
        )
        # strict_unknown_country: if True (default), jobs we can't
        # classify by country are DROPPED rather than passed through.
        # The old permissive behaviour was letting vague-location
        # listings slip past the hard filter.
        self.strict_unknown = bool(
            prefs.get("strict_unknown_country", True)
        )

    @property
    def active(self) -> bool:
        return self.mode == "hard" and bool(self.allowed)

    def classify(self, payload: dict) -> str:
        """Expose the detected country so the UI and scorer can reuse it
        without doing the work twice. Returns ISO-2 or ''."""
        return detect_country(payload.get("location"), payload.get("remote"))

    def evaluate(self, payload: dict) -> tuple[bool, str, str]:
        """Returns (keep, reason, detected_country). detected_country is
        emitted even when the filter is inactive so the match pipeline
        can surface it in the job card."""
        country = self.classify(payload)
        if not self.active:
            return True, "", country
        # Remote-any-country escape hatch.
        if self.allow_remote_any_country:
            mode = _classify_work_mode(payload)
            if mode == "remote":
                return True, "", country
        # Unknown-country handling. Default (strict_unknown=True):
        # drop, because the user wants a hard gate and the permissive
        # path was leaking Bangalore/Mexico roles whose country tokens
        # weren't recognised. Opt-in permissive mode is available via
        # strict_unknown_country=false in config.
        if not country:
            if self.strict_unknown:
                return False, (
                    "country unknown; strict_unknown_country is on"
                ), country
            return True, "", country
        if country not in self.allowed:
            return False, (
                f"country {country} not in allowed set "
                f"({', '.join(sorted(self.allowed))})"
            ), country
        return True, "", country


def _classify_work_mode(payload: dict) -> str:
    """Return one of "remote" | "hybrid" | "onsite" | "unknown".

    Priority order:
      1. The parser's own `remote` field if it's a known mode keyword.
      2. Token hits in the location string.
      3. "unknown" otherwise.
    Remote wins over hybrid wins over onsite when multiple tokens match
    so borderline listings are treated permissively (they don't get
    dropped as onsite-only).
    """
    raw_mode = str(payload.get("remote") or "").strip().lower()
    if raw_mode in _VALID_MODES:
        return raw_mode
    location = str(payload.get("location") or "").lower()
    text = f"{raw_mode} {location}"
    for mode in _VALID_MODES:
        for tok in _WORK_MODE_TOKENS[mode]:
            if tok in text:
                return mode
    return "unknown"


class LocationFilter:
    """Hard filter. Returns (keep: bool, reason: str).

    Preference keys consumed:
      - work_modes (list[str]): any combination of "remote", "hybrid",
        "onsite". Default = all three (i.e. no work-mode filtering).
        Unknown-mode jobs pass through the work-mode check because we
        don't want to punish listings whose HTML didn't specify.
      - allowed_locations (list[str]): when non-empty, gates hybrid /
        onsite jobs. Remote jobs always bypass this list because they
        are location-agnostic. Unknown-mode jobs with no location text
        also bypass it (no signal to test against).
      - blocked_locations (list[str]): any match kills the job.
      - allow_remote / remote_only (legacy bool fields): read only when
        work_modes is not set, and translated into an equivalent
        work_modes set so existing configs keep working.
    """

    def __init__(self, prefs: dict):
        raw_modes = prefs.get("work_modes")
        if raw_modes is None:
            # Legacy migration: derive from allow_remote + remote_only.
            allow_remote = bool(prefs.get("allow_remote", True))
            remote_only = bool(prefs.get("remote_only", False))
            if remote_only and allow_remote:
                modes = {"remote"}
            elif not allow_remote:
                modes = {"hybrid", "onsite"}
            else:
                modes = set(_VALID_MODES)
        else:
            modes = {str(m).strip().lower() for m in raw_modes if str(m).strip()}
            modes = {m for m in modes if m in _VALID_MODES}
            if not modes:
                # Empty selection = block everything, and signal via the
                # reason so the user notices in the log.
                modes = set()
        self.work_modes = modes
        self.allowed = [s.strip().lower() for s in prefs.get("allowed_locations", []) if s and s.strip()]
        self.blocked = [s.strip().lower() for s in prefs.get("blocked_locations", []) if s and s.strip()]
        # Geographic pin filter. Each pin is {lat, lon}; a job passes
        # if it's within `pin_radius_km` of ANY pin (union, not
        # intersection). Hard filter — same semantics as
        # allowed_locations but expressed as radii instead of
        # substrings. Ungeocodable locations get the benefit of the
        # doubt (see core/geocode.within_any_pin).
        raw_pins = prefs.get("location_pin_areas") or []
        self.pins: list[tuple[float, float]] = []
        for area in raw_pins:
            if isinstance(area, dict):
                lat, lon = area.get("lat"), area.get("lon")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    self.pins.append((float(lat), float(lon)))
        try:
            self.pin_radius_km = float(prefs.get("location_pin_radius_km") or 50.0)
        except (TypeError, ValueError):
            self.pin_radius_km = 50.0
        # "hard" keeps the legacy behaviour (drop the packet). "soft" makes
        # the filter inert and delegates the penalty to LocationScorer so
        # the user sees ranked-down roles rather than a silent 50% cull.
        mode_pref = str(prefs.get("location_mode") or "soft").strip().lower()
        self.mode = mode_pref if mode_pref in ("hard", "soft") else "soft"

    @property
    def active(self) -> bool:
        """True if any rule would actually do something. Soft mode is a
        no-op here - the matching scorer handles the penalty instead.
        Pin filter is always treated as hard (it's geographic intent,
        not a soft preference) so it activates regardless of mode."""
        if bool(self.pins):
            return True
        if self.mode == "soft":
            return False
        return (
            bool(self.allowed)
            or bool(self.blocked)
            or self.work_modes != set(_VALID_MODES)
        )

    def would_drop(self, payload: dict) -> tuple[bool, str]:
        """Same rule evaluation as `evaluate`, but callable even when
        `mode == 'soft'`. LocationScorer uses it to decide whether to
        apply the soft penalty without forcing the filter to be active."""
        return self._eval(payload)

    def evaluate(self, payload: dict) -> tuple[bool, str]:
        """Public entry used by MatchAgent. Honours the hard/soft mode:
        in soft mode we never drop on the substring rules — but the
        pin filter is always hard, so we still apply it even in soft
        mode (geographic pins are explicit user intent)."""
        if self.mode == "soft":
            keep, reason = self._eval_pins(payload)
            return keep, reason
        return self._eval(payload)

    def _eval(self, payload: dict) -> tuple[bool, str]:
        # Empty work_modes means user deselected everything. Honour it
        # explicitly so the log explains the zero-results state.
        if not self.work_modes:
            return False, "no work modes selected (check Settings)"

        location = (payload.get("location") or "").lower()
        mode = _classify_work_mode(payload)

        # 1. Work-mode gate. Unknown mode passes through because we don't
        # want to drop listings whose HTML didn't spell out remote/hybrid.
        if mode != "unknown" and mode not in self.work_modes:
            return False, f"work mode '{mode}' not allowed (settings allow: {', '.join(sorted(self.work_modes))})"

        # 2. Blocklist always wins, regardless of mode. Skip remote
        # tokens because they are handled by the mode gate above.
        non_remote_blocks = [b for b in self.blocked if b not in _REMOTE_TOKENS]
        if non_remote_blocks and any(b in location for b in non_remote_blocks):
            return False, "location matches blocked term"

        # 3. Inclusion list — UNION of geographic pins and substring
        # allowlist. A job passes if it's in any pin's radius OR
        # substring-matches any allowed_locations entry. Both empty =
        # no inclusion filter. Remote bypasses entirely because remote
        # is location-agnostic. Rationale: pins and text are two ways
        # to express the same intent ("I'm OK with this place"); they
        # should add coverage, not stack as conjunctions.
        if mode != "remote" and (self.pins or self.allowed):
            pin_passes = False
            if self.pins:
                keep, _ = self._eval_pins(payload)
                pin_passes = keep

            text_passes = False
            if self.allowed:
                if not location:
                    # No location text to test against. Same benefit-of-
                    # doubt convention as before — passes the text check.
                    text_passes = True
                else:
                    text_passes = any(a in location for a in self.allowed)

            if not pin_passes and not text_passes:
                bits = []
                if self.pins:
                    bits.append(f"outside all {len(self.pins)} pin radii")
                if self.allowed:
                    bits.append("no allowed_locations match")
                return False, "location " + " and ".join(bits)

        return True, ""

    def _eval_pins(self, payload: dict) -> tuple[bool, str]:
        """Stand-alone pin check, used by `evaluate` even in soft mode
        because geographic pins represent explicit user intent. Remote
        bypasses (location-agnostic). Ungeocodable strings bypass
        (benefit of the doubt — same convention as allowed_locations
        when the location field is empty)."""
        if not self.pins:
            return True, ""
        from core.geocode import within_any_pin  # local import: tests may not have the geocode module
        mode = _classify_work_mode(payload)
        if mode == "remote":
            return True, ""
        return within_any_pin(payload.get("location"), self.pins, self.pin_radius_km)


class LocationScorer:
    """Soft weight counterpart to LocationFilter. Applies a single-shot
    penalty when a role would have been dropped by the hard filter. Only
    active when `location_mode == 'soft'`; inert otherwise.

    Rationale: users told us "I got 22 matches because 195/392 dropped
    on location". A soft penalty (-0.06 by default) lets the roles
    through so they can be ranked alongside everything else, but keeps
    out-of-region roles comfortably below in-region ones with the same
    base score.
    """

    def __init__(self, prefs: dict):
        self._filter = LocationFilter(prefs)
        try:
            self.weight = float(prefs.get("location_weight", 0.06) or 0)
        except (TypeError, ValueError):
            self.weight = 0.06
        self.mode = self._filter.mode

    @property
    def active(self) -> bool:
        # Only contributes when the filter is in soft mode and the
        # underlying rules would actually do something. In hard mode
        # the filter drops outright, so no score adjustment is needed.
        if self.mode != "soft" or self.weight <= 0:
            return False
        return (
            bool(self._filter.allowed)
            or bool(self._filter.blocked)
            or self._filter.work_modes != set(_VALID_MODES)
        )

    def adjust(self, base_score: float, payload: dict) -> tuple[float, float, str]:
        if not self.active:
            return base_score, 0.0, ""
        keep, reason = self._filter.would_drop(payload)
        if keep:
            return base_score, 0.0, ""
        delta = -self.weight
        return _clamp(base_score + delta), delta, f"location soft penalty: {reason}"


# Level taxonomy shared with core.dimensions. Kept in sync by convention
# (both files import the same string keys). The numeric values here start
# at 1 for junior so a user who picks "new grad / APM" gets a non-zero
# band, matching dimensions.py.
_LEVEL_ORDER = {
    "junior": 1, "mid": 2, "senior": 3, "staff": 4,
    "principal": 5, "director": 6, "vp": 7, "cxo": 8,
}
# Labels we accept from the UI (wizard dropdown) and map down to the
# canonical keys above. We deliberately lump principal under staff for
# the dropdown so non-technical users aren't parsing five synonyms.
_LEVEL_ALIASES = {
    "new grad": "junior", "new-grad": "junior", "apm": "junior",
    "associate": "junior", "graduate": "junior", "intern": "junior",
    "entry": "junior", "entry-level": "junior", "junior": "junior",
    "mid": "mid", "mid-level": "mid", "pm": "mid", "product manager": "mid",
    "senior": "senior", "sr": "senior", "lead": "senior",
    "staff": "staff", "principal": "staff", "staff/principal": "staff",
    "director": "director", "head of": "director", "head-of": "director",
    "vp": "vp", "vice president": "vp",
    "cxo": "cxo", "cpo": "cxo", "chief product officer": "cxo",
}
# Titles that categorically require org-level scope. When the user has
# fewer than _TRAPDOOR_YEARS years, these always drop regardless of cosine.
_TRAPDOOR_SENIORITY = {"director", "vp", "cxo"}
_TRAPDOOR_YEARS = 10

# Rough YoE floor we assume per seniority band when the JD doesn't spell
# it out. Kept in step with dimensions._SENIORITY_YEARS_FLOOR.
_SENIORITY_YEARS_FLOOR = {
    "junior": 0, "mid": 2, "senior": 5, "staff": 8,
    "principal": 10, "director": 12, "vp": 15, "cxo": 18,
}

# Extract "N years" / "N+ years" / "N-M years" phrases from job descriptions.
# We take the LOWER bound of a range because that's what gates the applicant:
# "5 to 10 years" still lets someone with 5 apply.
_YEARS_RE = re.compile(
    r"(?<![\w.])(\d{1,2})\s*(?:\+|\s*-\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
    flags=re.IGNORECASE,
)

# Context-aware variant: phrase must name a requirement ("experience",
# "exp", "yoe", "in product management", "of PM", "of engineering", etc.)
# within ~40 chars of the number. Used for the hard "obvious senior role"
# drop — catches "15+ years of product management experience" without
# misfiring on "the company has 15 years of history".
_YEARS_EXP_RE = re.compile(
    r"(?<![\w.])"
    r"(\d{1,2})\s*\+?\s*(?:to\s*\d{1,2}\s*)?"
    r"(?:years?|yrs?|yoe)\s*"
    r"(?:of\s+)?"
    r"(?:[a-z][a-z /&+,.\-]{0,40}?\s*)?"
    r"(?:experience|exp\b|\bin\s+(?:product|pm|program|engineering|design|marketing|sales|ops|operations|data|software|eng\b|ml|ai|platform|security|research|strategy|finance|leadership|management)|leading|managing|building)",
    flags=re.IGNORECASE,
)

# Payload fields `_extract_required_years` scans. Ordered by how likely
# each is to actually carry requirements text. Ingest stages drop data
# into some subset of these, so we cast a wide net.
_YEARS_SCAN_FIELDS = (
    "description", "title", "requirements", "qualifications",
    "basic_qualifications", "preferred_qualifications",
    "responsibilities", "summary", "technologies", "team",
)


def _normalise_level(value: str) -> str:
    """Map any freeform level label to the canonical key, or '' if unknown."""
    if not value:
        return ""
    v = str(value).strip().lower()
    if v in _LEVEL_ORDER:
        return v
    return _LEVEL_ALIASES.get(v, "")


def _infer_job_level(payload: dict) -> str:
    """Mirror of dimensions._infer_job_seniority but returns '' (not None)
    so downstream code can treat it like a string."""
    tagged = _normalise_level(payload.get("seniority") or "")
    if tagged:
        return tagged
    title = (payload.get("title") or "").lower()
    # Same ordered tokens dimensions.py uses, inlined here to avoid a
    # circular import (dimensions imports from preferences in tests).
    for tok, label in [
        ("staff", "staff"), ("principal", "staff"),
        ("director of", "director"), (" director", "director"),
        ("head of", "director"), ("vp ", "vp"), ("vp,", "vp"),
        ("senior ", "senior"), ("lead ", "senior"), ("sr. ", "senior"), ("sr ", "senior"),
        ("junior", "junior"), ("associate", "junior"), ("graduate", "junior"),
    ]:
        if tok in title:
            return label
    return ""


def _extract_required_years(payload: dict) -> Optional[int]:
    """Pull the years-of-experience requirement from a JD.

    We scan many payload fields because different scrapers drop text in
    different places. Two passes:

      1. Context-aware pass (`_YEARS_EXP_RE`): phrases that name a
         requirement (e.g. "15+ years of product management experience").
         If this fires, we return the MAX of its hits — because when a JD
         explicitly says "15+ years of PM experience", the user being 10
         under that is the gate, not a 3-year hit from somewhere else.

      2. Fallback pass (`_YEARS_RE`): plain "N years" mentions anywhere.
         We return the MIN here so a "5 to 10 years" range lets a 5-year
         candidate apply.

    Returns an int or None."""
    parts = []
    for field in _YEARS_SCAN_FIELDS:
        val = payload.get(field)
        if not val:
            continue
        if isinstance(val, list):
            parts.append(" ".join(str(x) for x in val))
        else:
            parts.append(str(val))
    text = " ".join(parts)
    if not text:
        return None

    # Pass 1 — explicit-requirement phrases. Prefer the highest number,
    # because the senior-gating requirement is what locks the candidate
    # out. 15+ years > 3+ years when "15+ years of experience" is real.
    exp_hits: list[int] = []
    for m in _YEARS_EXP_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 30:
            exp_hits.append(n)
    if exp_hits:
        return max(exp_hits)

    # Pass 2 — fallback, plain "N years". MIN is the safe gate.
    hits: list[int] = []
    for m in _YEARS_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 30:
            hits.append(n)
    if not hits:
        return None
    return min(hits)


class ExperienceFilter:
    """Hard filter based on the user's seniority band and years of experience.

    Preference keys consumed:
      - years_experience (int, default 0): user's years in their field.
      - current_level (str, default ""): one of the canonical level keys
        (junior/mid/senior/staff/principal/director/vp/cxo) or an alias
        that normalises to one. Empty string means "don't filter on level".
      - max_level_gap (int, default 3): drop when the job's band is this
        many or more bands above the user's.
      - max_years_gap (int, default 8): drop when required_years - user_years
        exceeds this.
      - trapdoor_enabled (bool, default True): always drop Director/VP/CXO
        roles when user has fewer than 10 years.

    Returns (keep, reason) identical to LocationFilter. Inactive when the
    user has set neither current_level nor a non-zero years_experience.
    """

    def __init__(self, prefs: dict):
        try:
            self.years = int(prefs.get("years_experience") or 0)
        except (TypeError, ValueError):
            self.years = 0
        self.level = _normalise_level(prefs.get("current_level") or "")
        try:
            self.max_level_gap = int(prefs.get("max_level_gap") or 3)
        except (TypeError, ValueError):
            self.max_level_gap = 3
        try:
            self.max_years_gap = int(prefs.get("max_years_gap") or 8)
        except (TypeError, ValueError):
            self.max_years_gap = 8
        self.trapdoor = bool(prefs.get("trapdoor_enabled", True))

    @property
    def active(self) -> bool:
        # If the user hasn't told us anything, don't filter. Years==0 and
        # level=="" means "not configured". Years==0 with level=="junior"
        # is a legitimate new-grad setup and should still be active.
        return bool(self.level) or self.years > 0

    def evaluate(self, payload: dict) -> tuple[bool, str]:
        if not self.active:
            return True, ""

        job_level = _infer_job_level(payload)
        required_years = _extract_required_years(payload)

        # 1. Director/VP trap-door. Applies even if user level is unset,
        # because a 0-years user should never see Director roles.
        if self.trapdoor and job_level in _TRAPDOOR_SENIORITY and self.years < _TRAPDOOR_YEARS:
            return False, f"role is {job_level}; requires ~{_TRAPDOOR_YEARS}+ years (you have {self.years})"

        # 2. Seniority band gap (either direction).
        if self.level and job_level:
            u = _LEVEL_ORDER.get(self.level)
            j = _LEVEL_ORDER.get(job_level)
            if u and j and abs(j - u) >= self.max_level_gap:
                direction = "above" if j > u else "below"
                return False, f"role seniority ({job_level}) is {abs(j - u)} bands {direction} yours ({self.level})"

        # 3. Explicit years requirement from the JD.
        if required_years is not None and required_years - self.years >= self.max_years_gap:
            return False, f"role requires {required_years}+ years; you have {self.years} (gap {required_years - self.years})"

        return True, ""


class ExperienceScorer:
    """Soft weight. Gently lowers the score when the user is under the
    required years but not by enough to hit the hard filter above.

    Penalty = min(max(gap - soft_start + 1, 0), soft_span) * weight.
    Default weight 0.04, soft_start 3, soft_span 5 gives:
        gap 3 → 0.04 off
        gap 5 → 0.12 off
        gap 7 → 0.20 off
        gap 8+ → (already dropped by the filter)

    Missing required_years: no adjustment. Overshoot (user_years > required):
    no bonus - we don't want to over-reward senior folks for filtering
    entry-level roles we were willing to surface.
    """

    def __init__(self, prefs: dict):
        try:
            self.years = int(prefs.get("years_experience") or 0)
        except (TypeError, ValueError):
            self.years = 0
        try:
            self.weight = float(prefs.get("years_weight", 0.04) or 0)
        except (TypeError, ValueError):
            self.weight = 0.04
        self.soft_start = 3  # gap below this = no penalty
        self.soft_span = 5   # number of gap steps over which penalty scales

    @property
    def active(self) -> bool:
        return self.weight > 0 and self.years > 0

    def adjust(self, base_score: float, payload: dict) -> tuple[float, float, str]:
        if not self.active:
            return base_score, 0.0, ""
        required_years = _extract_required_years(payload)
        if required_years is None:
            return base_score, 0.0, ""
        gap = required_years - self.years
        if gap < self.soft_start:
            return base_score, 0.0, ""
        steps = min(gap - self.soft_start + 1, self.soft_span)
        delta = -(steps * self.weight)
        return _clamp(base_score + delta), delta, (
            f"role wants {required_years}+ years, you have {self.years} (gap {gap})"
        )


class TitleScorer:
    """Soft weight. Boosts the match score when the job title contains one
    of the user's role_keywords. The embedding model treats titles and
    descriptions as equal word-bags, so a title hit can get washed out by
    a long, domain-specific JD. This scorer puts a thumb on the scale for
    titles the user *explicitly* asked for.

    Config keys:
      - role_keywords (list[str]): same list used by the ingest stage to
        filter postings. Passed through orchestrator.
      - title_weight (float, default 0.08): max boost applied when the
        longest keyword in the title hits. Scaled down for shorter hits.

    Scoring:
      - Longest matching keyword (by char count) wins to reward precise
        matches like "product operations" over generic "product".
      - Boost = weight * min(1.0, len(hit) / 18), so "product operations"
        (19 chars) gets the full weight, while "pm" (2) gets ~0.009.
      - Never applies if no keyword matches. Never applies a penalty.
    """

    def __init__(self, prefs: dict):
        raw = prefs.get("role_keywords") or []
        if isinstance(raw, str):
            raw = [raw]
        # Lowercase + dedupe + sort by length desc so longest-first scan
        # makes the "longest match" semantics a free side effect.
        seen: set[str] = set()
        kws: list[str] = []
        for k in raw:
            s = str(k).strip().lower()
            if s and s not in seen:
                seen.add(s)
                kws.append(s)
        kws.sort(key=len, reverse=True)
        self.keywords = kws
        try:
            self.weight = float(prefs.get("title_weight", 0.08) or 0)
        except (TypeError, ValueError):
            self.weight = 0.08

    @property
    def active(self) -> bool:
        return self.weight > 0 and bool(self.keywords)

    def adjust(self, base_score: float, payload: dict) -> tuple[float, float, str]:
        if not self.active:
            return base_score, 0.0, ""
        title = str(payload.get("title") or "").lower()
        if not title:
            return base_score, 0.0, ""
        for kw in self.keywords:
            # Whole-word-ish match: require the keyword to appear with a
            # non-word boundary on either side. Avoids "product" hitting
            # "counterproductive", "pm" hitting "completed", etc.
            pattern = r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])"
            if re.search(pattern, title):
                scale = min(1.0, len(kw) / 18.0)
                delta = self.weight * scale
                return _clamp(base_score + delta), delta, (
                    f"title contains '{kw}' (+{delta:.3f})"
                )
        return base_score, 0.0, ""


class SalaryScorer:
    """Soft weight. Nudges the embedding/LLM score by up to `weight`.

    Behaviour:
      - Salary clearly above floor: +weight * scale (up to full weight)
      - Salary clearly below floor: -weight
      - Salary missing: -weight * 0.3 (mild penalty; most listings lack it)
      - No floor configured: no-op regardless of salary data
    """

    def __init__(self, prefs: dict):
        self.floor = float(prefs.get("salary_floor_usd", 0) or 0)
        self.weight = float(prefs.get("salary_weight", 0.15) or 0)
        self._missing_penalty = 0.3  # fraction of weight

    @property
    def active(self) -> bool:
        return self.floor > 0 and self.weight > 0

    def adjust(self, base_score: float, payload: dict) -> tuple[float, float, str]:
        """Return (new_score, delta, reason). delta is signed."""
        if not self.active:
            return base_score, 0.0, ""

        salary_usd = extract_salary_usd(payload.get("salary"))

        if salary_usd is None:
            delta = -self.weight * self._missing_penalty
            return _clamp(base_score + delta), delta, "salary missing"

        if salary_usd >= self.floor:
            # Scale how far above floor we are, capped at 2x floor => full weight.
            over = min((salary_usd - self.floor) / max(self.floor, 1), 1.0)
            delta = self.weight * (0.5 + 0.5 * over)
            return _clamp(base_score + delta), delta, f"salary ${salary_usd:,.0f} >= floor"

        # Below floor.
        delta = -self.weight
        return _clamp(base_score + delta), delta, f"salary ${salary_usd:,.0f} < floor"


# ──────────────────────────────────────────────────────────────────
# Salary parsing (best-effort; never raises)
# ──────────────────────────────────────────────────────────────────
# Number followed by optional K/M suffix. We normalise whitespace before
# matching so "€140 000" and "£140,000" both parse as 140000.
_NUM_RE = re.compile(r"(\d+(?:[,\.]\d+)*)\s*([kKmM]?)", flags=re.UNICODE)


def extract_salary_usd(raw) -> float | None:
    """Best-effort parse of a salary string into an approximate USD number.

    Handles: "$160K-200K", "$180,000", "£120k", "€140 000", "USD 175000",
    "$1.5M". Returns the MIDPOINT of a range, or the single value. None if
    no plausible salary number found (values under 10k are rejected as
    noise - years, counts, etc).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Figure out currency multiplier; default to USD.
    mult = 1.0
    for token, rate in _CURRENCY_TO_USD.items():
        if token in s:
            mult = rate
            break

    # Collapse thin spaces / NBSP inside numbers so "140 000" stays together.
    normalised = s.replace("\u00a0", " ").replace("\u202f", " ")
    normalised = re.sub(r"(\d)\s+(\d{3})\b", r"\1\2", normalised)

    nums: list[float] = []
    for m in _NUM_RE.finditer(normalised):
        token = m.group(1)
        suffix = m.group(2).lower()
        # Drop commas / dots used as thousands separators. If someone wrote
        # "1.5M" we need to preserve the decimal first.
        if suffix in ("k", "m") and token.count(".") == 1 and len(token.split(".")[-1]) <= 2:
            val = float(token)
        else:
            val = float(token.replace(",", "").replace(".", ""))
        if suffix == "k":
            val *= 1_000
        elif suffix == "m":
            val *= 1_000_000
        # Guard against silly small numbers (e.g. year "2026") - require at least 10k.
        if val < 10_000:
            continue
        nums.append(val)

    if not nums:
        return None

    midpoint = (min(nums) + max(nums)) / 2 if len(nums) > 1 else nums[0]
    return midpoint * mult


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def describe(prefs: dict) -> str:
    """One-line human-readable summary for logs."""
    loc = LocationFilter(prefs)
    sal = SalaryScorer(prefs)
    exp_f = ExperienceFilter(prefs)
    exp_s = ExperienceScorer(prefs)
    parts: list[str] = []
    if loc.active:
        bits: list[str] = []
        if not loc.work_modes:
            bits.append("no work modes")
        elif loc.work_modes != set(_VALID_MODES):
            bits.append("modes: " + ", ".join(sorted(loc.work_modes)))
        if loc.allowed:
            bits.append(f"allow: {', '.join(loc.allowed)}")
        if loc.blocked:
            bits.append(f"block: {', '.join(loc.blocked)}")
        parts.append("location[" + "; ".join(bits) + "]")
    if exp_f.active:
        bits = [f"level={exp_f.level or '?'}", f"years={exp_f.years}"]
        if exp_f.trapdoor:
            bits.append("trapdoor")
        parts.append("experience[" + ", ".join(bits) + "]")
    if exp_s.active:
        parts.append(f"years_weight={exp_s.weight:.2f}")
    if sal.active:
        parts.append(f"salary[floor=${sal.floor:,.0f}, weight={sal.weight:.2f}]")
    return ", ".join(parts) or "none"
