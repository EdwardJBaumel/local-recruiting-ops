"""
TEXT CLEANING FOR PARSE

The ingest layer hands the parse agent raw HTML strings taken straight
from BeautifulSoup (`str(el)` in ingest.py). That means every parse
call burns tokens on script tags, cookie banners, `data-pm-slice`
attributes, and recruiter tracking codes like "RDQ226R484" - the LLM
occasionally surfaces that garbage as the job title or in description.

This module centralises cleanup. Two pipelines:

  a. `clean_for_llm(html)` - run BEFORE the extraction prompt. Strips
     tags, nukes script/style/nav/header/footer/aside, drops tracking
     codes, collapses whitespace, truncates to a budget.
  b. `sanitise_job(job)` - run AFTER the LLM returns its JSON. Unescapes
     HTML entities in string fields, strips stray tags, drops titles
     that are obvious tracking-code noise.

Pure string functions so tests don't need BeautifulSoup to validate
anything but the strip_html helper (which does).
"""
from __future__ import annotations

import html as _html
import re
from typing import Any


DEFAULT_MAX_CHARS = 8000

# Tracking-code shape: 2-5 uppercase letters followed by digits and
# mixed alphanumerics, total length 6-14. Matches "RDQ226R484",
# "REQ12345", "JOB-2025-0042" etc. Kept intentionally tight so normal
# words like "LOVED" or "FOCUS" never trigger.
_TRACKING_CODE_RE = re.compile(
    r"^[A-Z]{2,5}[-_]?\d{2,}[A-Z0-9\-_]{0,10}$"
)

# Tags whose entire subtree is noise for job parsing. These nodes get
# removed before we serialise to text.
_DROP_TAGS = (
    "script", "style", "noscript",
    "nav", "header", "footer", "aside",
    "form", "button", "iframe", "svg",
)

# Whitespace collapse: any run of spaces/tabs -> single space; any run
# of 3+ newlines -> exactly 2 (paragraph break).
_WS_SPACE_RE = re.compile(r"[ \t\u00a0]+")
_WS_NEWLINES_RE = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """Turn an HTML fragment into clean text. Uses BeautifulSoup for
    parsing. Returns the input unchanged if BS4 isn't available or
    parsing fails - we'd rather pass raw HTML to the LLM than lose
    the packet entirely."""
    if not html or not isinstance(html, str):
        return ""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return html
    for tag_name in _DROP_TAGS:
        for node in soup.find_all(tag_name):
            node.decompose()
    # Preserve block-level breaks: replace <br>, <p>, <li>, <div>
    # boundaries with newlines so we don't mash the job description
    # into a single paragraph.
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "li", "div", "tr", "h1", "h2", "h3", "h4"]):
        block.append("\n")
    text = soup.get_text()
    return text


def drop_tracking_codes(text: str) -> str:
    """Remove lines that are pure recruiter/req tracking codes."""
    if not text:
        return ""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _TRACKING_CODE_RE.match(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def collapse_whitespace(text: str) -> str:
    """Flatten repeated spaces and capped newlines to a readable shape."""
    if not text:
        return ""
    # Normalise line endings first so \r\n doesn't produce double blanks.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_SPACE_RE.sub(" ", text)
    # Trim trailing spaces on each line.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _WS_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def clean_for_llm(html: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Full pre-LLM pipeline: strip tags, drop tracking codes, collapse
    whitespace, truncate. Cheap - O(length)."""
    text = strip_html(html or "")
    text = drop_tracking_codes(text)
    text = collapse_whitespace(text)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


_TAG_RE = re.compile(r"<[^>]+>")


def clean_field(value: Any) -> Any:
    """Clean one extracted string field. Unescapes HTML entities,
    strips surviving tags, trims. Non-strings pass through untouched."""
    if not isinstance(value, str):
        return value
    s = _html.unescape(value)
    s = _TAG_RE.sub("", s)
    s = s.strip()
    return s or None


def is_tracking_code(value: Any) -> bool:
    """True if the value is JUST a recruiter tracking code with no other
    content. Used to null out title/company fields the LLM slurped off
    the top of a poorly-formatted page."""
    if not isinstance(value, str):
        return False
    return bool(_TRACKING_CODE_RE.match(value.strip()))


_STRING_FIELDS = ("title", "company", "location", "salary",
                  "description", "url")


def sanitise_job(job: dict) -> dict:
    """Run clean_field over the string fields the LLM returned, null
    out any title/company that's pure tracking code, de-duplicate
    technologies, collapse seniority to lowercase. Returns a new dict;
    does not mutate the input."""
    if not isinstance(job, dict):
        return job
    out = dict(job)
    for k in _STRING_FIELDS:
        if k in out:
            out[k] = clean_field(out[k])
    # Title / company should never be a bare tracking code.
    if is_tracking_code(out.get("title")):
        out["title"] = None
    if is_tracking_code(out.get("company")):
        out["company"] = None
    # Normalise the short enum-y fields.
    for k in ("seniority", "job_type", "remote"):
        v = out.get(k)
        if isinstance(v, str):
            out[k] = v.strip().lower() or None
    # Deduplicate tech list, preserve order, drop blanks.
    techs = out.get("technologies")
    if isinstance(techs, list):
        seen = set()
        deduped = []
        for t in techs:
            if not isinstance(t, str):
                continue
            t2 = clean_field(t)
            if not t2:
                continue
            key = t2.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(t2)
        out["technologies"] = deduped
    return out
