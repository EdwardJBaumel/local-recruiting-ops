"""
Resume → structured profile parser.

Takes the plain-text resume stored by resume_store and asks a local Ollama
model to extract a structured profile. The output drives:
  1. config.match.profile_text — so the user never has to hand-write it
  2. multi-dimensional scoring — seniority/domain/tech comparisons per job
  3. the Brief tab profile card — transparency on what the pipeline thinks
     it knows about the candidate

Everything still runs locally. The parsed profile is cached to
data/resume/profile.json so we only pay the LLM cost when the resume
actually changes (or the user presses Reparse).

Contract:
  parse_to_profile(data_dir, force=False) -> dict | {"error": str}
  get_cached_profile(data_dir) -> dict | None
  profile_to_text(profile) -> str
  invalidate(data_dir) -> None

Design notes:
  - We pin this to qwen3:8b by default (task="analyze") rather than the
    26B model. Parsing is analytical-but-bounded; 26B is overkill and too
    slow for a responsive UI action.
  - Prompts are structured to coerce JSON output. If the LLM goes off the
    rails we fall back to a deterministic keyword-based profile so the
    match pipeline still has something to compare against.
  - We never send the resume anywhere off-box. llm.query calls localhost.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
from pathlib import Path

from core import llm, resume_store

logger = logging.getLogger("lantern.resume_profile")

_PROFILE_FILE = "profile.json"
_MAX_INPUT_CHARS = 8000  # plenty for even a dense 3-page CV
_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────
# LLM prompt
# ──────────────────────────────────────────────────────────────────
_SYSTEM = (
    "You parse resumes into structured JSON. You MUST respond with JSON "
    "only, no prose, no code fences. Unknown fields must be empty string, "
    "empty list or null; never invent data the resume does not contain."
)

_SCHEMA_DESCRIPTION = """Return a JSON object with EXACTLY these keys:
{
  "name": string,
  "headline": string,
  "years_experience": integer,
  "seniority": one of ["junior","mid","senior","staff","principal","director","vp","cxo"],
  "roles": [
    { "title": string, "company": string, "start": string (YYYY or YYYY-MM), "end": string ("present" if current), "domain": string }
  ],
  "skills": [string],
  "technologies": [string],
  "domains": [string],
  "years_per_stack": { "<tech>": integer, ... },
  "education": [
    { "degree": string, "field": string, "institution": string, "year": string }
  ],
  "certifications": [string],
  "summary": string (2-3 sentences, third person, suitable as a candidate profile for job matching),
  "target_roles": [string]
}

CRITICAL RULES for target_roles (these were tuned after observing the
parser drift into aspirational titles 2-3 levels above the candidate):

  1. STAY IN THE CANDIDATE'S CURRENT FUNCTION. If their last 2 roles are
     all "Product Manager" titles, every target_role must be a Product
     Manager variant or a directly-adjacent role (Product Operations,
     Technical Program Manager). DO NOT propose Engineering, Architect,
     Designer, or Marketing titles even if the resume mentions those
     skills tangentially.

  2. SENIORITY BAND: every target_role must be at most ONE level above
     the candidate's current seniority. Mapping:
        junior      → "Product Manager", "Associate Product Manager"
        mid         → "Product Manager", "Senior Product Manager"
        senior      → "Senior Product Manager", "Staff Product Manager",
                      "Lead Product Manager"
        staff       → "Staff Product Manager", "Principal Product Manager",
                      "Group Product Manager"
        principal   → "Principal Product Manager", "Group Product Manager",
                      "Director of Product"
        director+   → "Director", "Senior Director", "VP", "Head of"

     If years_experience < 8, NEVER include "Director", "VP", "Head of",
     "Chief", "C-suite", or "Architect" titles regardless of skill list.

  3. RETURN 3-5 ENTRIES. Quality over quantity. If you can only justify
     two solid target titles from the resume, return two.

  4. PREFER SPECIFICITY when domain is clear. A candidate with
     healthcare/regulated-industry experience can have "Senior Product
     Manager, Healthcare" but only if it's the same seniority band —
     never "Head of Healthcare Tech" for a 5-year-experience PM.
"""


def _sanitise_resume(text: str) -> str:
    """Strip content likely to be mistaken for prompt boundaries/instructions.
    A resume is a document, not a conversation; anything that looks like a
    role-play instruction or a fenced block is noise at best and injection
    at worst."""
    t = text
    # Strip triple backticks and code-fence markers that could break JSON parsing.
    t = t.replace("```", "")
    # Collapse suspicious meta-lines people sometimes put in CV footers.
    for marker in (
        "ignore previous instructions",
        "ignore all previous instructions",
        "system:",
        "assistant:",
        "user:",
        "<|im_start|>",
        "<|im_end|>",
    ):
        t = re.sub(re.escape(marker), "[redacted]", t, flags=re.IGNORECASE)
    return t


def _build_prompt(resume_text: str, notes: str) -> str:
    body = _sanitise_resume(resume_text[:_MAX_INPUT_CHARS])
    truncated = len(resume_text) > _MAX_INPUT_CHARS
    clean_notes = _sanitise_resume(notes.strip()[:1500])
    notes_block = (
        f"\n\n====BEGIN CANDIDATE NOTES====\n{clean_notes}\n====END CANDIDATE NOTES===="
        if clean_notes else ""
    )
    trunc_note = (
        f"\n[Note: resume was truncated to {_MAX_INPUT_CHARS} chars out of {len(resume_text)}.]"
        if truncated else ""
    )
    # The authoritative instruction (schema + JSON-only reminder) goes AFTER
    # the user-controlled resume body so a crafted resume cannot replace it
    # with its own instructions.
    return (
        f"{_SYSTEM}\n\n"
        "====BEGIN RESUME TEXT====\n"
        f"{body}\n"
        "====END RESUME TEXT===="
        f"{notes_block}"
        f"{trunc_note}\n\n"
        "Now, using ONLY the resume text and notes above as source data, "
        f"produce the structured profile.\n{_SCHEMA_DESCRIPTION}\n\n"
        "Respond with JSON only. Do not include any text outside the JSON object."
    )


# ──────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────
_SENIORITY_ALLOWED = {"junior", "mid", "senior", "staff", "principal", "director", "vp", "cxo"}


def _coerce_list(v) -> list:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in re.split(r"[,\n;]", v) if s.strip()]
    return []


def _coerce_profile(raw: dict) -> dict:
    """Normalise LLM output. Anything missing gets a safe default so the
    downstream code never needs to defensively None-check."""
    if not isinstance(raw, dict):
        raw = {}

    seniority_raw = (raw.get("seniority") or "").strip()
    seniority = seniority_raw.lower()
    if seniority and seniority not in _SENIORITY_ALLOWED:
        logger.info("Seniority '%s' not in allowed enum; dropping.", seniority_raw)
        seniority = ""

    try:
        years = int(raw.get("years_experience") or 0)
    except (TypeError, ValueError):
        years = 0

    roles_raw = raw.get("roles") or []
    roles: list[dict] = []
    if isinstance(roles_raw, list):
        for r in roles_raw:
            if not isinstance(r, dict):
                continue
            roles.append({
                "title": str(r.get("title", "")).strip(),
                "company": str(r.get("company", "")).strip(),
                "start": str(r.get("start", "")).strip(),
                "end": str(r.get("end", "")).strip(),
                "domain": str(r.get("domain", "")).strip(),
            })

    education_raw = raw.get("education") or []
    education: list[dict] = []
    if isinstance(education_raw, list):
        for e in education_raw:
            if not isinstance(e, dict):
                continue
            education.append({
                "degree": str(e.get("degree", "")).strip(),
                "field": str(e.get("field", "")).strip(),
                "institution": str(e.get("institution", "")).strip(),
                "year": str(e.get("year", "")).strip(),
            })

    yps_raw = raw.get("years_per_stack") or {}
    years_per_stack: dict[str, int] = {}
    if isinstance(yps_raw, dict):
        for k, v in yps_raw.items():
            try:
                years_per_stack[str(k).strip().lower()] = max(0, int(v))
            except (TypeError, ValueError):
                continue

    # Belt-and-suspenders: even if the LLM ignored the prompt's seniority
    # rules and proposed "Head of X" or "Director of Eng" for a 5-yoe
    # candidate, strip those titles deterministically here. The embedding
    # search is sensitive to target_roles, so a single bad title can drag
    # the matcher into wrong-archetype territory.
    target_roles_clean = _filter_target_roles(
        _coerce_list(raw.get("target_roles"))[:5],
        years_experience=years,
        seniority=seniority,
    )

    return {
        "name": str(raw.get("name", "")).strip(),
        "headline": str(raw.get("headline", "")).strip(),
        "years_experience": years,
        "seniority": seniority,
        "roles": roles,
        "skills": _coerce_list(raw.get("skills")),
        "technologies": [t.lower() for t in _coerce_list(raw.get("technologies"))],
        "domains": [d.lower() for d in _coerce_list(raw.get("domains"))],
        "years_per_stack": years_per_stack,
        "education": education,
        "certifications": _coerce_list(raw.get("certifications")),
        "summary": str(raw.get("summary", "")).strip(),
        "target_roles": target_roles_clean,
    }


# ──────────────────────────────────────────────────────────────────
# Target-role filter
# ──────────────────────────────────────────────────────────────────
# Substrings that disqualify a target role unless the candidate has
# enough YoE / matching seniority. The embedding scorer doesn't care
# whether a role title was "suggested by the LLM"; it just measures
# similarity. Letting "Director of Engineering" into target_roles
# pulls the embedding toward Director-level engineering postings,
# which is exactly the failure mode the user reported.
_HIGH_LEVEL_TITLES = (
    "head of", "director", "vp", "vice president", "chief",
    "c-level", "cxo", "cto", "cpo", "ceo", "founder",
)
# Cross-domain titles that aren't PM-adjacent. If the user is a PM,
# we don't want "Architect" / "Engineer" / "Designer" in target_roles.
# A PM with a heavy dev background still wants PM jobs ranked first.
_OFF_DOMAIN_TITLES_FOR_PM = (
    "engineer", "architect", "developer", "scientist",
    "designer", "marketer", "analyst", "consultant",
)


def _filter_target_roles(
    titles: list[str], *, years_experience: int, seniority: str,
) -> list[str]:
    """Drop titles that are above the candidate's seniority band or
    out-of-domain. Pure post-processing — no LLM call.
    """
    is_pm_candidate = any(
        "product" in t.lower() and ("manager" in t.lower() or "owner" in t.lower())
        for t in titles
    ) or seniority in {"junior", "mid", "senior", "staff", "principal"}

    too_senior = (
        years_experience < 8
        or seniority in {"junior", "mid", "senior", "staff"}
    )

    cleaned: list[str] = []
    for t in titles:
        lc = t.lower()
        if too_senior and any(h in lc for h in _HIGH_LEVEL_TITLES):
            continue
        if is_pm_candidate and any(o in lc for o in _OFF_DOMAIN_TITLES_FOR_PM):
            # Allow "Product Manager, AI Engineer Tooling" style hybrids
            # only if "product" is also in the title — otherwise drop.
            if "product" not in lc:
                continue
        cleaned.append(t)

    # If filtering nuked everything (e.g., LLM proposed only Director
    # titles for a junior), fall back to safe seniority-appropriate
    # defaults so the embedding has SOMETHING to anchor on.
    if not cleaned:
        cleaned = _default_targets_for_seniority(seniority)
    return cleaned


def _default_targets_for_seniority(seniority: str) -> list[str]:
    """Last-resort fallback when filtering left target_roles empty."""
    return {
        "junior":    ["Associate Product Manager", "Product Manager"],
        "mid":       ["Product Manager", "Senior Product Manager"],
        "senior":    ["Senior Product Manager", "Lead Product Manager", "Staff Product Manager"],
        "staff":     ["Staff Product Manager", "Principal Product Manager", "Group Product Manager"],
        "principal": ["Principal Product Manager", "Group Product Manager", "Director of Product"],
        "director":  ["Director of Product", "Senior Director, Product", "VP Product"],
        "vp":        ["VP Product", "Senior Director, Product", "Chief Product Officer"],
        "cxo":       ["Chief Product Officer", "VP Product"],
    }.get(seniority, ["Senior Product Manager"])


# ──────────────────────────────────────────────────────────────────
# Deterministic fallback
# ──────────────────────────────────────────────────────────────────
# A tiny keyword extractor so a profile still exists when Ollama is down.
# It's crude; the LLM path is always preferred. Kept here so first-run
# isn't blocked by a bad Ollama state.
_STACK_PATTERNS = [
    r"\breact(?:\.?js)?\b", r"\bpython\b", r"\bjavascript\b", r"\btypescript\b",
    r"\bjava\b", r"\bgo(?:lang)?\b", r"\brust\b", r"\bruby\b", r"\bnode(?:\.?js)?\b",
    r"\bsql\b", r"\bpostgres\b", r"\bmysql\b", r"\bmongo(?:db)?\b",
    r"\bkubernetes\b", r"\bdocker\b", r"\baws\b", r"\bgcp\b", r"\bazure\b",
    r"\btableau\b", r"\blooker\b", r"\bml\b", r"\bllm\b", r"\bstorybook\b",
    r"\bwcag\b", r"\bfigma\b", r"\bjira\b", r"\bconfluence\b",
]
_DOMAIN_HINTS = {
    "fintech": ["payments", "banking", "trading", "wealth"],
    "devtools": ["developer tools", "platform", "devtools", "ci/cd"],
    "healthcare": ["healthcare", "clinical", "patient", "ehr"],
    "commerce": ["ecommerce", "marketplace", "checkout", "storefront"],
    "ai/ml": ["machine learning", "ai", "llm", "neural"],
    "security": ["security", "infosec", "compliance", "soc 2"],
}
_SENIORITY_TOKENS = [
    ("staff", "staff"), ("principal", "principal"),
    ("director of", "director"), ("head of", "director"),
    ("senior ", "senior"), ("lead ", "senior"),
    ("junior", "junior"), ("associate", "junior"),
]


def _fallback_profile(resume_text: str, notes: str) -> dict:
    text = (resume_text + "\n" + notes).lower()
    techs: set[str] = set()
    for pat in _STACK_PATTERNS:
        m = re.search(pat, text)
        if m:
            techs.add(m.group(0).strip("."))

    domains: set[str] = set()
    for label, keys in _DOMAIN_HINTS.items():
        if any(k in text for k in keys):
            domains.add(label)

    seniority = ""
    for tok, label in _SENIORITY_TOKENS:
        if tok in text:
            seniority = label
            break

    # Rough years estimate from phrases like "5 years", "5+ years".
    years = 0
    m = re.search(r"(\d{1,2})\+?\s*(?:years|yrs)", text)
    if m:
        try:
            years = int(m.group(1))
        except ValueError:
            pass

    return _coerce_profile({
        "name": "",
        "headline": "",
        "years_experience": years,
        "seniority": seniority,
        "roles": [],
        "skills": [],
        "technologies": sorted(techs),
        "domains": sorted(domains),
        "years_per_stack": {},
        "education": [],
        "certifications": [],
        "summary": resume_text[:400].strip(),
        "target_roles": [],
    })


# ──────────────────────────────────────────────────────────────────
# Cache I/O
# ──────────────────────────────────────────────────────────────────
def _path(data_dir: Path) -> Path:
    return data_dir / "resume" / _PROFILE_FILE


def get_cached_profile(data_dir: Path) -> dict | None:
    p = _path(data_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("profile.json unreadable: %s", e)
        return None


def invalidate(data_dir: Path) -> None:
    """Remove the cached profile so the next parse call re-runs the LLM."""
    with _lock:
        p = _path(data_dir)
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                logger.warning("Failed to clear profile cache: %s", e)


# ──────────────────────────────────────────────────────────────────
# User-edited override
# ──────────────────────────────────────────────────────────────────
# Editable fields. The user can override what the LLM parsed; the
# rest of the profile (timestamps, model, etc.) stays parser-owned.
# Anything not in this allowlist gets ignored on save — that prevents
# a malformed POST body from corrupting structural metadata.
_EDITABLE_FIELDS = (
    "summary", "headline", "seniority", "years_experience",
    "target_roles", "technologies", "domains", "skills",
)


def save_user_override(data_dir: Path, patch: dict) -> dict:
    """Merge a user-edited profile patch into the cached profile and
    persist. Returns the resulting profile (or {"error": ...} on
    failure).

    Why this design: the LLM parse is fast but imperfect — it might
    pick "Director of Engineering" as a target role for someone with
    5 years' experience. The user has ground truth about themselves
    that the resume text alone can't convey. Letting them edit
    target_roles, seniority, tech stack, etc. directly is the
    pragmatic fix; the embedding step then uses the corrected profile.

    Re-parsing (POST /api/resume/reparse) overwrites the entire
    profile.json with fresh LLM output — including blowing away these
    overrides. That's intentional: the user can always edit again. We
    don't try to merge-on-reparse because that gets confusing fast.
    """
    if not isinstance(patch, dict):
        return {"error": "patch must be an object", "status": "bad_request"}

    with _lock:
        existing = get_cached_profile(data_dir) or {}
        # Only merge fields we explicitly allow editing. Anything else
        # in the patch (e.g. `_fallback`, `model`, `generated_at`) is
        # silently ignored.
        for key in _EDITABLE_FIELDS:
            if key not in patch:
                continue
            v = patch[key]
            if key == "years_experience":
                try:
                    existing[key] = max(0, int(v)) if v is not None else 0
                except (TypeError, ValueError):
                    continue
            elif key == "seniority":
                if isinstance(v, str) and v.strip().lower() in _SENIORITY_ALLOWED:
                    existing[key] = v.strip().lower()
            elif key in ("target_roles", "technologies", "domains", "skills"):
                if isinstance(v, list):
                    existing[key] = [str(x).strip() for x in v if str(x).strip()]
                elif isinstance(v, str):
                    existing[key] = _coerce_list(v)
            else:  # plain string fields (summary, headline)
                existing[key] = str(v or "").strip()

        # Stamp that this profile has user-edits applied so the UI
        # can show "edited" / "from parser" cleanly. Cleared on
        # next reparse.
        existing["_user_edited"] = True
        existing["_user_edited_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        # Make sure the profile has a resume linkage even if the user
        # is editing before the first parse has run.
        if "source_uploaded_at" not in existing:
            try:
                state = resume_store.read_current(data_dir)
                existing["source_uploaded_at"] = (
                    state.get("metadata", {}).get("uploaded_at", "")
                )
            except Exception:
                pass

        _path(data_dir).parent.mkdir(parents=True, exist_ok=True)
        from core.io_safe import write_text_atomic
        write_text_atomic(_path(data_dir), json.dumps(existing, indent=2))

    logger.info("User override saved: %d fields", sum(1 for k in _EDITABLE_FIELDS if k in patch))
    return existing


# ──────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────
def parse_to_profile(data_dir: Path, force: bool = False,
                     model: str | None = None) -> dict:
    """Parse the currently-stored resume into a structured profile.

    Returns a dict. On success the dict has the full schema plus
    metadata fields (generated_at, source, model). On failure returns
    {"error": "<reason>"} with status 'not_parsed'.

    force=True bypasses the cache.
    """
    state = resume_store.read_current(data_dir)
    if not state["has_resume"]:
        return {"error": "No resume uploaded.", "status": "no_resume"}

    if not force:
        cached = get_cached_profile(data_dir)
        # Reuse cache only if it was generated for the current upload
        # (keyed on uploaded_at) — otherwise re-parse.
        upload_ts = state.get("metadata", {}).get("uploaded_at", "")
        if cached and cached.get("source_uploaded_at") == upload_ts:
            return cached

    resume_text = state["parsed_text"]
    notes = state["additional_notes"]

    prompt = _build_prompt(resume_text, notes)
    # Resume parsing is conceptually a "parse" task (resume text → JSON),
    # not "analyze". Honour the user's config override by reading the
    # parse model from config.json. Falls back to DEFAULT_MODELS["parse"]
    # if the config is missing or malformed.
    chosen_model = model
    if not chosen_model:
        try:
            import json as _json
            from pathlib import Path as _Path
            cfg = _json.loads((_Path(data_dir).parent / "config.json").read_text())
            chosen_model = (cfg.get("parse") or {}).get("model") or llm.get_model("parse")
        except Exception:
            chosen_model = llm.get_model("parse")

    try:
        raw = llm.query_json(prompt, task="parse", model=chosen_model)
        if isinstance(raw, dict) and raw.get("_parse_error"):
            logger.warning("LLM returned non-JSON; using fallback parser.")
            profile = _fallback_profile(resume_text, notes)
            profile["_fallback"] = True
        else:
            profile = _coerce_profile(raw)
            profile["_fallback"] = False
    except Exception as e:
        logger.warning("Ollama unavailable for resume parse: %s", e)
        profile = _fallback_profile(resume_text, notes)
        profile["_fallback"] = True
        profile["_llm_error"] = str(e)

    original_upload_ts = state.get("metadata", {}).get("uploaded_at", "")
    profile["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    profile["source_uploaded_at"] = original_upload_ts
    # Stamp the actual model that ran (post-fallback substitution) so
    # the metadata reflects reality, not the requested name. If the
    # requested model 404'd and the fallback chain substituted, the
    # substitute is what we report. Falls through to chosen_model when
    # no substitution happened.
    if profile["_fallback"]:
        profile["model"] = "fallback:keyword"
    else:
        try:
            substitutes = llm.get_effective_models().get("substitutes", {})
            profile["model"] = substitutes.get(chosen_model, chosen_model)
        except Exception:
            profile["model"] = chosen_model

    # Stale-write guard: if the user cleared or replaced the resume while
    # the LLM was running, discard this result rather than writing a
    # profile that's keyed to a resume that no longer exists. The whole
    # check + write sits inside the lock so `invalidate()` and any later
    # parse can't interleave with this one.
    with _lock:
        current = resume_store.read_current(data_dir)
        if not current["has_resume"]:
            logger.info("Resume cleared during parse; discarding profile result.")
            return {"error": "Resume cleared during parse.", "status": "stale"}
        current_ts = current.get("metadata", {}).get("uploaded_at", "")
        if current_ts != original_upload_ts:
            logger.info("Resume replaced during parse (was %s, now %s); discarding.",
                        original_upload_ts, current_ts)
            return {"error": "Resume changed during parse.", "status": "stale"}
        _path(data_dir).parent.mkdir(parents=True, exist_ok=True)
        from core.io_safe import write_text_atomic
        write_text_atomic(_path(data_dir), json.dumps(profile, indent=2))

    logger.info("Profile parsed (%s): %d skills, %d techs, seniority=%s",
                "fallback" if profile["_fallback"] else "llm",
                len(profile["skills"]), len(profile["technologies"]),
                profile.get("seniority") or "?")
    return profile


# ──────────────────────────────────────────────────────────────────
# Text rendering for the match pipeline
# ──────────────────────────────────────────────────────────────────
def profile_to_text(profile: dict) -> str:
    """Render the structured profile back into a dense paragraph suitable
    for config.match.profile_text (which feeds the embedding encoder
    or LLM matcher). This lets the match pipeline stay profile-agnostic
    while still benefiting from the structured parse."""
    if not profile or profile.get("error"):
        return ""

    parts: list[str] = []
    if profile.get("summary"):
        parts.append(profile["summary"])

    headline = profile.get("headline")
    sen = profile.get("seniority")
    yrs = profile.get("years_experience")
    if headline or sen or yrs:
        parts.append(
            f"{headline or 'Candidate'}"
            f"{' · ' + sen.title() if sen else ''}"
            f"{' · ' + str(yrs) + ' years experience' if yrs else ''}"
        )

    techs = profile.get("technologies") or []
    if techs:
        parts.append("Technologies: " + ", ".join(techs[:20]))

    skills = profile.get("skills") or []
    if skills:
        parts.append("Skills: " + ", ".join(skills[:20]))

    domains = profile.get("domains") or []
    if domains:
        parts.append("Domains: " + ", ".join(domains))

    targets = profile.get("target_roles") or []
    if targets:
        parts.append("Target roles: " + ", ".join(targets))

    return "\n".join(parts).strip()
