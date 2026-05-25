"""
Resume Tailor
=============

What this file is
-----------------
A single-LLM-pass resume tailoring pipeline. Given

    * the candidate's parsed profile (from core.resume_profile)
    * the full resume text (from core.resume_store)
    * one job payload (title, company, description, technologies)

it produces a tailored HTML resume and writes both HTML and PDF into
`data/resumes/`. The UI surfaces a "Tailor Resume" button on each
match card that POSTs here.

Why this shape?
---------------
Three design choices that keep the blast radius small so a weak
local LLM (and a weak future debugger) can keep this honest:

1. **LLM writes JSON, Python writes HTML.** We never ask the model
   to emit layout. A misbehaving 8B model WILL mangle an HTML
   template with fancy CSS. Instead it returns a small JSON blob
   (summary, bullets, skills) and a deterministic template here
   lays it out. Broken template -> fix ONE function.

2. **Never fabricate.** The prompt is explicit: reorder and
   rephrase existing bullets, never invent metrics or employers.
   Source-of-truth is the parsed profile. The tailor is a
   *reorder + tighten* pass, not a ghostwriter.

3. **Dual PDF path.** We try weasyprint first (pure Python, fast).
   If it's missing or chokes on the HTML, we fall back to Playwright's
   print-to-PDF (already used by the SPA runner, so no new dep).
   Worst case: HTML ships without a PDF -- the UI still links to it.

Output
------
Two files per call, named `tailored_<slug>_<timestamp>.{html,pdf}`:

    data/resumes/tailored_meta_product_manager_growth_20260423_1530.html
    data/resumes/tailored_meta_product_manager_growth_20260423_1530.pdf

The caller gets back a dict:

    {
      "ok": True,
      "html_path": "...",
      "pdf_path": "..." | None,
      "pdf_method": "weasyprint" | "playwright" | None,
      "filename_stem": "tailored_meta_product_manager_...",
      "summary": "<1-sentence tailored summary>",
    }

On failure we return {"ok": False, "error": "..."} -- the server
route turns that into an HTTP 500 with a readable message.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import json
import logging
import re
from pathlib import Path
from typing import Any

from core import llm

logger = logging.getLogger("sentinel.resume_tailor")


# -----------------------------------------------------------------------------
# LLM PROMPT
# -----------------------------------------------------------------------------
# Kept deliberately small. Weak local models handle short prompts with
# tight JSON schemas far better than 2000-token instruction dumps.
# -----------------------------------------------------------------------------
_PROMPT = """You tailor an existing resume for one specific job. You do NOT invent facts.

CANDIDATE PROFILE (parsed from the real resume -- source of truth):
{profile}

RAW RESUME TEXT (use for exact wording of experience bullets):
{resume_text}

TARGET JOB:
Title: {job_title}
Company: {job_company}
Technologies mentioned: {job_tech}
Description:
{job_description}

Rewrite the resume for THIS job. Return ONLY JSON, no prose, no code fences:

{{
  "headline":       "<1 line under the name: seniority + domain tuned to the role>",
  "summary":        "<2-3 sentence candidate summary, third person, tuned to this role>",
  "highlighted_skills": ["<skill>", "..."],
  "experience": [
    {{
      "title":   "<exact role title from resume>",
      "company": "<exact company from resume>",
      "dates":   "<start - end, e.g. 'Jan 2022 - Present'>",
      "bullets": [
        "<rewritten bullet, action-verb-first, keeps any real metric from the source>",
        "..."
      ]
    }}
  ],
  "education":    ["<degree, institution, year>", "..."],
  "keywords_pulled_in": ["<job-description keyword you matched with a real skill>"]
}}

Rules:
- NEVER invent employers, titles, dates, or metrics. Reorder and rephrase what exists.
- Prefer 4-6 bullets per role. Cut weakest bullets rather than adding filler.
- Highlight skills that overlap with the job's technologies first.
- If a job keyword has NO match in the resume, omit it -- don't fake coverage.
- Keep each bullet under 30 words.
"""


# -----------------------------------------------------------------------------
# PUBLIC ENTRY
# -----------------------------------------------------------------------------
def tailor_resume(
    *,
    data_dir: Path,
    profile: dict,
    resume_text: str,
    job: dict,
    model: str | None = None,
) -> dict:
    """Tailor one resume for one job. Writes HTML + (best-effort) PDF.

    Parameters
    ----------
    data_dir :
        Sentinel data directory. We write into `data_dir/resumes/`.
    profile :
        The dict from core.resume_profile.get_cached_profile(). Must at
        least contain `name`. Missing fields degrade gracefully.
    resume_text :
        Raw resume text (parsed + additional notes). Gets truncated
        inside the prompt to stay inside the context window.
    job :
        Job payload dict. We read title, company, description,
        technologies, url.
    model :
        Optional override. Default routes via llm.query_json(task=...).

    Returns
    -------
    dict with at least `ok` (bool). On success also `html_path`,
    `pdf_path`, `pdf_method`, `filename_stem`, `summary`. On failure
    only `error`.
    """
    # Guard: no profile = nothing to tailor. Surface a readable error
    # so the UI can say "upload a resume first".
    if not profile or not isinstance(profile, dict):
        return {"ok": False, "error": "No parsed profile available. Upload a resume first."}
    if not (resume_text or "").strip():
        return {"ok": False, "error": "Resume text is empty. Re-upload a resume."}

    job_title = str(job.get("title", "") or "").strip() or "Role"
    job_company = str(job.get("company", "") or "").strip() or "Company"

    # Ask the LLM for a structured tailoring.
    prompt = _PROMPT.format(
        profile=_compact_json(profile),
        resume_text=_truncate(resume_text, 6000),
        job_title=job_title,
        job_company=job_company,
        job_tech=", ".join(job.get("technologies") or []) or "not listed",
        job_description=_truncate(job.get("description", "") or "", 3000),
    )

    try:
        tailored = llm.query_json(prompt, task="analyze", model=model)
    except Exception as e:
        logger.warning("tailor LLM call failed: %s", e)
        return {"ok": False, "error": f"LLM call failed: {e}"}

    if not isinstance(tailored, dict) or tailored.get("_parse_error"):
        logger.warning("tailor LLM returned unparseable JSON")
        return {"ok": False, "error": "LLM returned unparseable response. Try again."}

    # Merge tailored output over baseline profile. The tailored output
    # is authoritative for the fields it provides; profile fills gaps
    # (name, contact, etc.) the LLM was never asked about.
    rendered_html = _render_html(
        profile=profile,
        tailored=tailored,
        job_title=job_title,
        job_company=job_company,
    )

    # Write files.
    out_dir = Path(data_dir) / "resumes"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = _filename_stem(profile=profile, job_title=job_title, job_company=job_company)
    html_path = out_dir / f"{stem}.html"
    pdf_path = out_dir / f"{stem}.pdf"

    try:
        html_path.write_text(rendered_html, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Failed to write HTML: {e}"}

    pdf_method = _write_pdf(rendered_html, pdf_path)

    return {
        "ok": True,
        "html_path": str(html_path),
        "pdf_path": str(pdf_path) if pdf_method else None,
        "pdf_method": pdf_method,
        "filename_stem": stem,
        "summary": tailored.get("summary", "")[:500],
    }


# -----------------------------------------------------------------------------
# HTML RENDERING
# -----------------------------------------------------------------------------
# Everything below is deterministic: given the same profile + tailored
# dict, produces the same HTML. No LLM in the loop here on purpose --
# layout bugs are fixed in ONE place (this function) and a weak model
# can't break them.
# -----------------------------------------------------------------------------

_CSS = """
@page { margin: 0.5in; }
body {
  font-family: 'Helvetica', 'Arial', sans-serif;
  font-size: 10.5pt;
  line-height: 1.35;
  color: #222;
  max-width: 7.5in;
  margin: 0 auto;
}
h1 { font-size: 20pt; margin: 0 0 2pt 0; }
h2 { font-size: 11pt; margin: 14pt 0 4pt 0; border-bottom: 1px solid #999;
     text-transform: uppercase; letter-spacing: 0.5pt; }
.headline { color: #555; font-size: 11pt; margin-bottom: 6pt; }
.contact { font-size: 9.5pt; color: #666; margin-bottom: 6pt; }
.summary { margin: 0 0 10pt 0; }
.role-head { display: flex; justify-content: space-between; margin-top: 8pt; }
.role-title { font-weight: bold; }
.role-dates { color: #666; font-size: 9.5pt; }
.role-company { color: #444; font-style: italic; margin-bottom: 2pt; }
ul { margin: 2pt 0 4pt 18pt; padding: 0; }
li { margin-bottom: 2pt; }
.skills-list { margin: 0; padding: 0; list-style: none; font-size: 10pt; }
.skills-list li { display: inline-block; margin-right: 10pt; }
.tailored-for { color: #888; font-size: 9pt; margin-top: 12pt;
                border-top: 1px dashed #ccc; padding-top: 4pt; }
"""


def _render_html(
    *,
    profile: dict,
    tailored: dict,
    job_title: str,
    job_company: str,
) -> str:
    """Turn (profile + tailored) into a single-file HTML resume.

    HTML-escapes every user-visible string to keep angle brackets out
    of the markup (some resumes have "<Operations>" or similar). Keeps
    the structure flat so weasyprint and Chromium both handle it.
    """
    name = _esc(profile.get("name") or "Candidate")
    headline = _esc(tailored.get("headline") or profile.get("headline") or "")
    contact = _esc(_build_contact_line(profile))
    summary = _esc(tailored.get("summary") or profile.get("summary") or "")

    # Experience -- prefer tailored, fall back to profile.roles.
    experience = tailored.get("experience")
    if not isinstance(experience, list) or not experience:
        experience = _roles_from_profile(profile)

    exp_html = []
    for role in experience:
        if not isinstance(role, dict):
            continue
        exp_html.append(
            "<div class='role-head'>"
            f"<span class='role-title'>{_esc(role.get('title', ''))}</span>"
            f"<span class='role-dates'>{_esc(role.get('dates', ''))}</span>"
            "</div>"
            f"<div class='role-company'>{_esc(role.get('company', ''))}</div>"
        )
        bullets = role.get("bullets") or []
        if isinstance(bullets, list) and bullets:
            exp_html.append("<ul>")
            for b in bullets:
                if b and str(b).strip():
                    exp_html.append(f"<li>{_esc(str(b))}</li>")
            exp_html.append("</ul>")

    # Skills -- prefer highlighted_skills, then profile.skills.
    skills = tailored.get("highlighted_skills") or profile.get("skills") or []
    skill_html = ""
    if isinstance(skills, list) and skills:
        skill_items = "".join(f"<li>• {_esc(str(s))}</li>" for s in skills if s)
        skill_html = f"<ul class='skills-list'>{skill_items}</ul>"

    # Education.
    education = tailored.get("education") or _education_from_profile(profile)
    edu_html = ""
    if isinstance(education, list) and education:
        edu_items = "".join(f"<li>{_esc(str(e))}</li>" for e in education if e)
        edu_html = f"<ul>{edu_items}</ul>"

    # Generated-for footer -- useful when the user has 12 variants on disk.
    stamp = _dt.datetime.now().strftime("%b %d, %Y %H:%M")
    tailored_for = _esc(f"Tailored for {job_title} at {job_company} on {stamp}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Resume - {name} - {_esc(job_company)}</title>
<style>{_CSS}</style>
</head>
<body>
  <h1>{name}</h1>
  {f'<div class="headline">{headline}</div>' if headline else ''}
  {f'<div class="contact">{contact}</div>' if contact else ''}

  {f'<h2>Summary</h2><p class="summary">{summary}</p>' if summary else ''}

  {f'<h2>Skills</h2>{skill_html}' if skill_html else ''}

  <h2>Experience</h2>
  {''.join(exp_html) if exp_html else '<p><em>No experience parsed.</em></p>'}

  {f'<h2>Education</h2>{edu_html}' if edu_html else ''}

  <div class="tailored-for">{tailored_for}</div>
</body>
</html>"""


# -----------------------------------------------------------------------------
# PDF RENDERING -- weasyprint, then Playwright fallback
# -----------------------------------------------------------------------------

def _write_pdf(html: str, pdf_path: Path) -> str | None:
    """Try weasyprint, fall back to Playwright's print-to-PDF.

    Returns the method name on success ("weasyprint" | "playwright"),
    or None if both paths failed. Never raises.
    """
    # Attempt 1: weasyprint. Pure-Python, fast, no browser launch.
    try:
        from weasyprint import HTML as _WP_HTML
        _WP_HTML(string=html).write_pdf(str(pdf_path))
        return "weasyprint"
    except ImportError:
        logger.info("weasyprint not installed, trying Playwright")
    except Exception as e:
        logger.warning("weasyprint render failed (%s), trying Playwright", e)

    # Attempt 2: Playwright (already a dep for the SPA runner).
    try:
        from agents.playwright_runner import render_html_to_pdf
        if render_html_to_pdf(html, str(pdf_path)):
            return "playwright"
    except Exception as e:
        logger.warning("Playwright PDF render failed: %s", e)

    return None


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def _compact_json(obj: Any) -> str:
    """JSON dump without indentation -- saves prompt tokens on a
    potentially 2KB profile."""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _truncate(text: str, max_chars: int) -> str:
    """Cut long text at `max_chars`, appending an ellipsis note. Keeps
    the LLM from tripping on a 50KB job description."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


def _esc(val: Any) -> str:
    """HTML-escape a value so brackets and quotes in user text don't
    corrupt the rendered resume."""
    return _html.escape(str(val or ""), quote=True)


def _filename_stem(*, profile: dict, job_title: str, job_company: str) -> str:
    """Produce a filesystem-safe filename stem.

    Shape: tailored_<company>_<title>_<YYYYMMDD_HHMM>

    Kept filesystem-safe (lowercase, single underscores, no slashes)
    so Windows is happy. Timestamp disambiguates rapid re-tailoring.
    """
    def slug(s: str, max_len: int = 40) -> str:
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s[:max_len] or "item"

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"tailored_{slug(job_company)}_{slug(job_title)}_{ts}"


def _build_contact_line(profile: dict) -> str:
    """Assemble a contact line from whatever contact-ish fields are in
    the profile. The parser doesn't formally extract these, so we scan
    a few common keys and a summary fallback."""
    bits = []
    for k in ("email", "phone", "location", "linkedin", "website"):
        v = profile.get(k)
        if v and isinstance(v, str):
            bits.append(v.strip())
    return " | ".join(bits)


def _roles_from_profile(profile: dict) -> list[dict]:
    """Convert profile.roles into the tailored experience shape so we
    can render something reasonable when the LLM omits experience."""
    roles = profile.get("roles") or []
    out = []
    if not isinstance(roles, list):
        return out
    for r in roles:
        if not isinstance(r, dict):
            continue
        start = r.get("start", "")
        end = r.get("end", "") or "Present"
        out.append({
            "title": r.get("title", ""),
            "company": r.get("company", ""),
            "dates": f"{start} - {end}".strip(" -"),
            "bullets": [],  # no source bullets in the profile; leave empty
        })
    return out


def _education_from_profile(profile: dict) -> list[str]:
    """Flatten profile.education entries into one-line strings for
    display. Skips entries missing both degree and institution."""
    edu = profile.get("education") or []
    out = []
    if not isinstance(edu, list):
        return out
    for e in edu:
        if not isinstance(e, dict):
            continue
        bits = [str(e.get("degree", "")).strip(), str(e.get("field", "")).strip()]
        tail = [str(e.get("institution", "")).strip(), str(e.get("year", "")).strip()]
        line = " ".join(b for b in bits if b)
        tail_line = ", ".join(t for t in tail if t)
        if line and tail_line:
            out.append(f"{line} - {tail_line}")
        elif line or tail_line:
            out.append(line or tail_line)
    return out
