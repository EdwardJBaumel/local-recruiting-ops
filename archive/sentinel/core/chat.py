"""
Context-aware chat over local job-pipeline data.

Design constraints:
  - Local LLMs have small context windows (gemma4:e4b ~8K, qwen3:8b ~32K
    in practice). We do not pipe raw match/fit-gap JSON into the prompt.
  - Instead we build a compact, summarised "situational briefing" from
    the dashboard data and let the LLM reason over that.
  - Retrieval is keyword-based, not embedded. The corpus is tiny (matches
    + decisions + fit-gaps + market) so grep-style scoring is plenty.

The chat endpoint in server.py handles the HTTP layer. This module owns
the retrieval + prompt construction.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from core import llm, market_intel, resume_store

logger = logging.getLogger("sentinel.chat")

_MAX_BRIEFING_CHARS = 6000  # leave headroom for the user question + response


def _safe_json_load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _load_recent_matches(data_dir: Path, limit: int = 20) -> list[dict]:
    d = data_dir / "matches"
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.json"), reverse=True)[:5]:
        try:
            out.extend(json.loads(f.read_text()))
        except Exception:
            pass
        if len(out) >= limit:
            break
    # Rank by score desc.
    out.sort(key=lambda j: j.get("_match_score", 0) or 0, reverse=True)
    return out[:limit]


def _load_recent_fit_gaps(data_dir: Path, limit: int = 10) -> list[dict]:
    d = data_dir / "fit_gaps"
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.json"), reverse=True)[:5]:
        try:
            out.extend(json.loads(f.read_text()))
        except Exception:
            pass
        if len(out) >= limit:
            break
    return out[:limit]


def _tokenize(q: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9+\-\.]{1,}", q) if len(t) > 2]


def _retrieve(query: str, matches: list[dict], fit_gaps: list[dict],
              reactions: list[dict]) -> dict:
    """Keyword-scored retrieval. Returns top-N items per collection."""
    tokens = _tokenize(query)

    def score(blob: str) -> int:
        blob = blob.lower()
        return sum(1 for t in tokens if t in blob)

    scored_matches = []
    for m in matches:
        blob = " ".join(str(m.get(k, "")) for k in ("title", "company", "description", "location", "technologies"))
        s = score(blob)
        if s or not tokens:
            scored_matches.append((s, m))
    scored_matches.sort(key=lambda x: x[0], reverse=True)
    top_matches = [m for _, m in scored_matches[:6]]

    scored_gaps = []
    for fg in fit_gaps:
        blob = json.dumps(fg)
        s = score(blob)
        if s or not tokens:
            scored_gaps.append((s, fg))
    scored_gaps.sort(key=lambda x: x[0], reverse=True)
    top_gaps = [fg for _, fg in scored_gaps[:3]]

    scored_reactions = []
    for r in reactions:
        blob = " ".join(str(r.get(k, "")) for k in ("title", "company", "notes"))
        s = score(blob)
        scored_reactions.append((s, r))
    scored_reactions.sort(key=lambda x: x[0], reverse=True)
    top_reactions = [r for _, r in scored_reactions[:5]]

    return {"matches": top_matches, "fit_gaps": top_gaps, "reactions": top_reactions}


def _summarise_match(m: dict) -> str:
    parts = [
        f"{m.get('title','?')} @ {m.get('company','?')}",
        f"score={((m.get('_match_score') or 0) * 100):.0f}%",
        m.get("location") or "",
        m.get("remote") or "",
    ]
    if m.get("salary"):
        parts.append(f"pay: {m.get('salary')}")
    techs = m.get("technologies") or []
    if isinstance(techs, list) and techs:
        parts.append("tech: " + ", ".join(techs[:6]))
    desc = (m.get("description") or "").strip().replace("\n", " ")
    if desc:
        parts.append(desc[:200])
    return " | ".join(p for p in parts if p)


def _summarise_fit_gap(fg: dict) -> str:
    title = f"{fg.get('title','?')} @ {fg.get('company','?')}"
    matched = ", ".join((fg.get("matched_skills") or [])[:6])
    gaps = ", ".join(g.get("skill", "") for g in (fg.get("gaps") or [])[:4])
    return f"{title} | matched: {matched} | gaps: {gaps}"


def build_briefing(data_dir: Path, question: str) -> str:
    """Construct the compact context pack that gets prepended to the user turn."""
    matches = _load_recent_matches(data_dir)
    fit_gaps = _load_recent_fit_gaps(data_dir)

    from core import decision_store
    reactions = decision_store.list_reactions(data_dir)

    tier1 = market_intel.tier1_bundle(data_dir)
    resume_state = resume_store.read_current(data_dir)

    retrieved = _retrieve(question, matches, fit_gaps, reactions)

    profile_summary = ""
    if resume_state.get("has_resume"):
        parsed = (resume_state.get("parsed_text") or "")[:600]
        notes = (resume_state.get("additional_notes") or "")[:300]
        profile_summary = f"Resume (first 600 chars):\n{parsed}\n\nNotes:\n{notes}"

    top_gaps = tier1.get("skill_gap_frequency", [])[:5]
    velocity = tier1.get("hiring_velocity_wow", {})
    matched_metrics = tier1.get("matched_job_metrics", {})

    parts: list[str] = []

    if profile_summary:
        parts.append("=== CANDIDATE ===\n" + profile_summary)

    if retrieved["matches"]:
        parts.append("=== TOP MATCHES (relevant to your question) ===\n" +
                     "\n".join("- " + _summarise_match(m) for m in retrieved["matches"]))

    if retrieved["fit_gaps"]:
        parts.append("=== FIT-GAP REPORTS ===\n" +
                     "\n".join("- " + _summarise_fit_gap(f) for f in retrieved["fit_gaps"]))

    if retrieved["reactions"]:
        parts.append("=== USER REACTIONS (thumbs up/down) ===\n" +
                     "\n".join(f"- {r.get('action','?')}: {r.get('title')} @ {r.get('company')} "
                               f"({((r.get('score') or 0)*100):.0f}%)"
                               for r in retrieved["reactions"]))

    if top_gaps:
        parts.append("=== MOST COMMON SKILL GAPS ACROSS REPORTS ===\n" +
                     "\n".join(f"- {g['skill']} ({g['count']} reports, {g.get('pct_of_reports',0)}%)"
                               for g in top_gaps))

    if velocity.get("this_week") or velocity.get("last_week"):
        parts.append(f"=== HIRING VELOCITY ===\nThis week: {velocity.get('this_week')}, "
                     f"last week: {velocity.get('last_week')}, delta: {velocity.get('delta_pct')}%")

    if matched_metrics.get("total_matched_jobs"):
        wm = matched_metrics.get("work_model") or {}
        top_skills = matched_metrics.get("skill_frequency") or []
        parts.append("=== MATCHED JOB MIX ===\n"
                     f"Total matched: {matched_metrics['total_matched_jobs']}. "
                     f"Work model: {wm}. "
                     f"Top skills: {', '.join(s['skill'] for s in top_skills[:8])}")

    briefing = "\n\n".join(parts) if parts else "(no pipeline data yet - run a cycle first)"

    # Trim defensively. We prefer to keep the top of the briefing (profile,
    # top matches) over the tail (market rollups).
    if len(briefing) > _MAX_BRIEFING_CHARS:
        briefing = briefing[:_MAX_BRIEFING_CHARS] + "\n\n[briefing truncated]"
    return briefing


SYSTEM_PROMPT = """You are SENTINEL's assistant. You help the user (a Product Manager) reason about their job search.
You have access to a compact briefing pulled from the user's local job-match pipeline: recent matched jobs, fit-gap reports, thumbs up/down reactions, market intelligence, and a resume summary.

Rules:
- Ground answers in the briefing. If the briefing doesn't contain what's needed, say so plainly rather than guessing.
- Be concise. Pull specific titles/companies/scores when making a point.
- Use British English. No Oxford commas. No em dashes.
- When the user asks "what should I apply to", prefer high-score matches where the user has not already given a thumbs down.
"""


def _render_context_block(context: dict | None) -> str:
    """Render a compact "=== CURRENT VIEW ===" block from the UI-side
    context payload. Small by design - this is cheap screen-awareness,
    not a second briefing. Safe with None / partial payloads.
    """
    if not context or not isinstance(context, dict):
        return ""
    lines: list[str] = []
    view = context.get("view")
    if view:
        lines.append(f"Current tab: {view}")
    vmc = context.get("visible_match_count")
    if isinstance(vmc, int):
        lines.append(f"Matches visible: {vmc}")
    sel = context.get("selectedJob") or {}
    if isinstance(sel, dict) and (sel.get("title") or sel.get("company")):
        title = sel.get("title") or "(untitled)"
        company = sel.get("company") or "(unknown)"
        loc = sel.get("location") or "?"
        remote = sel.get("remote")
        score = sel.get("match_score")
        extra: list[str] = []
        if score is not None:
            try:
                extra.append(f"score={float(score):.2f}")
            except Exception:
                pass
        if remote is not None:
            extra.append(f"remote={remote}")
        extra.append(f"location={loc}")
        lines.append(f"Expanded role: {title} @ {company} ({', '.join(extra)})")
    filters = context.get("filters") or {}
    if isinstance(filters, dict):
        active = [k for k, v in filters.items() if v not in (False, None, "", 0)]
        if active:
            lines.append("Active filters: " + ", ".join(active))
    if not lines:
        return ""
    return "=== CURRENT VIEW ===\n" + "\n".join(lines) + "\n\n"


def chat_once(data_dir: Path, messages: list[dict], model: str | None = None, context: dict | None = None) -> dict:
    """Single-turn chat. messages is an OpenAI-style list: [{role, content}, ...].
    `context` is the UI screen-state snapshot (view, selectedJob, filters).
    Returns {reply: str, briefing_chars: int}.

    Ollama's /api/generate is stateless per call, so we collapse the turn
    history into one prompt string rather than relying on a chat endpoint.
    """
    if not messages:
        raise ValueError("messages is empty")

    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break
    if not last_user:
        raise ValueError("no user message found")

    briefing = build_briefing(data_dir, last_user)

    # Render the chat history in a minimal, readable form.
    history_lines: list[str] = []
    for m in messages[:-1] if messages and messages[-1].get("role") == "user" else messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if content:
            history_lines.append(f"{role.upper()}: {content}")

    context_block = _render_context_block(context)

    prompt = (
        SYSTEM_PROMPT + "\n\n"
        + context_block
        + "=== BRIEFING ===\n" + briefing + "\n\n"
        "=== CONVERSATION ===\n"
        + ("\n".join(history_lines) + "\n" if history_lines else "")
        + f"USER: {last_user}\nASSISTANT:"
    )

    chosen_model = model or "qwen3:14b"
    try:
        reply = llm.query(prompt, task="default", model=chosen_model, temperature=0.4, timeout=180)
    except Exception as e:
        logger.exception("Chat LLM call failed")
        return {
            "reply": f"(Could not reach Ollama: {e}. Is `ollama serve` running with {chosen_model} pulled?)",
            "briefing_chars": len(briefing),
            "error": True,
        }

    return {"reply": reply.strip(), "briefing_chars": len(briefing), "model": chosen_model}
