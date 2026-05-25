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

logger = logging.getLogger("sentinel.resume_profile")

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
  "target_roles": [string] (3-5 realistic target titles given seniority + skills)
}"""


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
        "target_roles": _coerce_list(raw.get("target_roles"))[:5],
    }


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
