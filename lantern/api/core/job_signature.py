"""
Compact job signatures for cross-encoder reranking.

Bi-encoder embeddings (bge-m3) ingest ~1,500 chars of JD boilerplate.
Cross-encoders are pairwise — cost scales with query × document length.
A ~150-token signature (~600 chars) keeps rerank latency bounded while
preserving title, stack, seniority and the responsibilities slice a
candidate actually reads.

Built deterministically from parsed fields (no extra LLM call).
"""
from __future__ import annotations

import re
from typing import Any

# Roughly 150 tokens at ~4 chars/token.
DEFAULT_MAX_CHARS = 600

_KEEP_SECTION_HEADINGS = (
    "what you'll do",
    "what you will do",
    "responsibilities",
    "the role",
    "your role",
    "what you'll bring",
    "what you will bring",
    "requirements",
    "qualifications",
    "minimum qualifications",
    "basic qualifications",
    "what we're looking for",
    "you have",
    "you will",
    "in this role",
)

_BOILERPLATE_HEADINGS = (
    "about us",
    "about the company",
    "our mission",
    "our values",
    "equal opportunity",
    "benefits",
    "perks",
    "why join",
    "total rewards",
    "eeo",
)

_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)


def _extract_role_slice(description: str, max_chars: int) -> str:
    """Keep responsibilities/requirements sections; drop company boilerplate."""
    plain = _strip_html(description or "")
    if not plain:
        return ""
    lower = plain.lower()
    start = len(plain)
    for heading in _KEEP_SECTION_HEADINGS:
        idx = lower.find(heading)
        if idx >= 0:
            start = min(start, idx)
    if start < len(plain):
        plain = plain[start:]
    else:
        # No recognised headings — first paragraph only.
        parts = [p.strip() for p in re.split(r"\n{2,}", plain) if p.strip()]
        plain = parts[0] if parts else plain[:max_chars]
    for heading in _BOILERPLATE_HEADINGS:
        idx = plain.lower().find(heading)
        if idx > 80:
            plain = plain[:idx]
            break
    plain = _WS_RE.sub(" ", plain).strip()
    if len(plain) > max_chars:
        plain = plain[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return plain


def build_job_signature(payload: dict[str, Any] | None, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return a compact single-line signature for cross-encoder input."""
    if not payload:
        return ""
    title = str(payload.get("title") or "").strip()
    company = str(payload.get("company") or "").strip()
    location = str(payload.get("location") or "").strip()
    seniority = str(payload.get("seniority") or "").strip()
    remote = str(payload.get("remote") or "").strip()
    techs = payload.get("technologies") or []
    if isinstance(techs, str):
        techs = [techs]
    tech_str = ", ".join(str(t).strip() for t in techs[:12] if str(t).strip())

    desc_budget = max(120, max_chars - 180)
    role_slice = _extract_role_slice(str(payload.get("description") or ""), desc_budget)

    parts: list[str] = []
    if title:
        parts.append(title)
    if company:
        parts.append(f"@ {company}")
    meta: list[str] = []
    if seniority and seniority.lower() not in ("unknown", ""):
        meta.append(seniority)
    if remote and remote.lower() not in ("unknown", ""):
        meta.append(remote)
    if location:
        meta.append(location)
    if meta:
        parts.append(f"({'; '.join(meta)})")
    if tech_str:
        parts.append(f"Stack: {tech_str}")
    if role_slice:
        parts.append(role_slice)

    sig = " | ".join(p for p in parts if p)
    sig = _WS_RE.sub(" ", sig).strip()
    if len(sig) > max_chars:
        sig = sig[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return sig


def attach_job_signature(payload: dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """Mutate payload in place with ``job_signature``; return payload."""
    payload["job_signature"] = build_job_signature(payload, max_chars=max_chars)
    return payload
