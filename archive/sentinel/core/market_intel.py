"""
Tier 1 + Tier 2 market intelligence metrics.

Computed on demand from on-disk data rather than the in-memory orchestrator
state so the API survives restarts. All functions take a data_dir and
return JSON-serialisable dicts/lists.

Tier 1:
  - skill_gap_frequency: how often each skill appears as a gap across all
    fit-gap reports. Highlights what Eddie should upskill in.
  - hiring_velocity_wow: week-over-week change in total postings by source.
  - skill_frequency_matched: technology frequencies across MATCHED jobs
    only. Tells us what the target segment actually wants.
  - work_model_matched: remote/hybrid/onsite split for matched jobs only.
    The existing market_intel.json tracks ALL ingested jobs; that mixes
    signal with noise when the user cares about their funnel.

Tier 2:
  - new_companies: companies appearing in the most recent cycle but not in
    any prior cycle. Signals a new hiring start.
  - posting_age_distribution: histogram of how stale the MATCHED jobs are
    (0-7d / 8-30d / 31-60d / 61+d / unknown). Old postings correlate with
    ghost jobs; useful sanity check alongside the fake-detector.
  - source_effectiveness: match rate per ATS source (matches / ingested).
    Tells us which ATS we're actually finding viable roles on.
  - ghost_job_rate_by_company: share of a company's matched jobs flagged
    as ghost-suspect. Requires fake_detector output on each match payload.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger("sentinel.market_intel")


def _iter_json_files(d: Path) -> list[Path]:
    return sorted(d.glob("*.json"), reverse=True) if d.exists() else []


def _load_many(d: Path, limit: int | None = None) -> list:
    out: list = []
    for f in _iter_json_files(d)[:limit] if limit else _iter_json_files(d):
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            logger.debug("Skipping unreadable %s: %s", f, e)
            continue
        if isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return out


# ──────────────────────────────────────────────────────────────────
# Skill gap frequency
# ──────────────────────────────────────────────────────────────────
def skill_gap_frequency(data_dir: Path, top_n: int = 15) -> list[dict]:
    reports = _load_many(data_dir / "fit_gaps")
    counter: Counter[str] = Counter()
    severity_tally: dict[str, Counter[str]] = defaultdict(Counter)
    total_reports = 0

    for r in reports:
        if not isinstance(r, dict):
            continue
        total_reports += 1
        for g in r.get("gaps") or []:
            if not isinstance(g, dict):
                continue
            skill = (g.get("skill") or "").strip().lower()
            if not skill:
                continue
            counter[skill] += 1
            severity_tally[skill][(g.get("severity") or "unknown").lower()] += 1
        for s in r.get("missing_skills") or []:
            skill = (s or "").strip().lower()
            if skill:
                counter[skill] += 1

    out: list[dict] = []
    for skill, count in counter.most_common(top_n):
        sev = dict(severity_tally.get(skill, {}))
        out.append({
            "skill": skill,
            "count": count,
            "pct_of_reports": round(100 * count / total_reports, 1) if total_reports else 0.0,
            "severities": sev,
        })
    return out


# ──────────────────────────────────────────────────────────────────
# Hiring velocity WoW
# ──────────────────────────────────────────────────────────────────
def hiring_velocity_wow(data_dir: Path) -> dict:
    """Compare this week's ingested job count vs last week's, per source."""
    intel_file = data_dir / "market_intel.json"
    if not intel_file.exists():
        return {"this_week": 0, "last_week": 0, "delta_pct": 0.0, "by_source": {}}

    try:
        history = json.loads(intel_file.read_text())
    except Exception:
        return {"this_week": 0, "last_week": 0, "delta_pct": 0.0, "by_source": {}}

    now = _dt.datetime.now(_dt.timezone.utc)
    week_ago = now - _dt.timedelta(days=7)
    two_weeks_ago = now - _dt.timedelta(days=14)

    def _in_range(entry: dict, start, end) -> bool:
        ts = entry.get("timestamp", "")
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return False
        return start <= dt < end

    def _sum(entries: list[dict]) -> tuple[int, dict]:
        total = 0
        by_src: Counter[str] = Counter()
        for e in entries:
            for src, n in (e.get("source_breakdown") or {}).items():
                by_src[src] += n
                total += n
        return total, dict(by_src)

    this_entries = [e for e in history if _in_range(e, week_ago, now)]
    last_entries = [e for e in history if _in_range(e, two_weeks_ago, week_ago)]

    this_total, this_by = _sum(this_entries)
    last_total, last_by = _sum(last_entries)

    delta_pct = 0.0
    if last_total:
        delta_pct = round(100 * (this_total - last_total) / last_total, 1)

    by_source = {}
    for src in set(this_by) | set(last_by):
        t = this_by.get(src, 0)
        l = last_by.get(src, 0)
        by_source[src] = {
            "this_week": t, "last_week": l,
            "delta_pct": round(100 * (t - l) / l, 1) if l else (100.0 if t else 0.0),
        }

    return {
        "this_week": this_total,
        "last_week": last_total,
        "delta_pct": delta_pct,
        "by_source": by_source,
    }


# ──────────────────────────────────────────────────────────────────
# Matched-only: skill frequency, work model
# ──────────────────────────────────────────────────────────────────
_NON_SKILL_TOKENS = {
    "the", "and", "with", "for", "from", "that", "this", "have", "will",
    "team", "work", "role", "product", "engineer", "manager", "senior",
}


def _extract_skills_from_job(job: dict) -> list[str]:
    tech = job.get("technologies") or []
    if isinstance(tech, list) and tech:
        return [str(t).strip().lower() for t in tech if t]
    # Fallback: crude token extraction from description.
    desc = (job.get("description") or "").lower()
    tokens = re.findall(r"\b[a-z][a-z+\-.]{2,}\b", desc)
    return [t for t in tokens if t not in _NON_SKILL_TOKENS]


def matched_job_metrics(data_dir: Path, top_n_skills: int = 12) -> dict:
    """Skill frequency and work-model split across MATCHED jobs only."""
    matches = _load_many(data_dir / "matches")
    skill_counter: Counter[str] = Counter()
    work_model: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()

    for job in matches:
        if not isinstance(job, dict):
            continue
        for s in _extract_skills_from_job(job):
            skill_counter[s] += 1
        wm = (job.get("remote") or "unknown").lower()
        work_model[wm if wm in ("remote", "hybrid", "onsite") else "unknown"] += 1
        co = job.get("company") or "Unknown"
        company_counter[co] += 1

    return {
        "total_matched_jobs": len(matches),
        "skill_frequency": [{"skill": s, "count": n}
                            for s, n in skill_counter.most_common(top_n_skills)],
        "work_model": dict(work_model),
        "top_companies": dict(company_counter.most_common(10)),
    }


# ──────────────────────────────────────────────────────────────────
# Composite Tier 1 bundle
# ──────────────────────────────────────────────────────────────────
def tier1_bundle(data_dir: Path) -> dict:
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "skill_gap_frequency": skill_gap_frequency(data_dir),
        "hiring_velocity_wow": hiring_velocity_wow(data_dir),
        "matched_job_metrics": matched_job_metrics(data_dir),
    }


# ──────────────────────────────────────────────────────────────────
# Tier 2: new company detection
# ──────────────────────────────────────────────────────────────────
def _load_market_history(data_dir: Path) -> list[dict]:
    f = data_dir / "market_intel.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text())
    except Exception:
        return []
    return [e for e in data if isinstance(e, dict)]


def new_companies(data_dir: Path, lookback_cycles: int = 10) -> dict:
    """Companies whose first appearance is the most recent cycle.

    Uses the market_intel.json cycle history. Compares the newest cycle's
    company_volume against the union of the previous `lookback_cycles` to
    decide novelty. Keeping the lookback finite (rather than the full
    history) lets returning-after-a-gap still register as interesting.
    """
    history = _load_market_history(data_dir)
    if len(history) < 2:
        return {"cycle": None, "new": [], "total_active_companies": 0}

    # Sort newest last so [-1] is always the latest cycle.
    history.sort(key=lambda e: e.get("timestamp", ""))
    latest = history[-1]
    prior = history[-(lookback_cycles + 1):-1] if lookback_cycles else history[:-1]

    prior_companies: set[str] = set()
    for c in prior:
        for name in (c.get("company_volume") or {}):
            prior_companies.add(name.lower())

    latest_vol = latest.get("company_volume") or {}
    novel = []
    for name, n in latest_vol.items():
        if name.lower() not in prior_companies:
            novel.append({"company": name, "count": int(n)})

    # Rank by count so the most interesting newcomers show first.
    novel.sort(key=lambda x: x["count"], reverse=True)
    return {
        "cycle": latest.get("cycle"),
        "timestamp": latest.get("timestamp"),
        "new": novel,
        "total_active_companies": len(latest_vol),
        "compared_against_cycles": len(prior),
    }


# ──────────────────────────────────────────────────────────────────
# Tier 2: posting age distribution
# ──────────────────────────────────────────────────────────────────
_DATE_FIELDS = ("posted_at", "posted_date", "published_at", "publishedAt",
                "updated_at", "updatedAt", "created_at", "createdAt", "date")


def _parse_job_date(job: dict) -> _dt.datetime | None:
    for key in _DATE_FIELDS:
        raw = job.get(key)
        if not raw:
            continue
        s = str(raw).strip()
        if not s:
            continue
        # Handle unix timestamps (seconds or milliseconds) that a few ATS feeds
        # hand back as integers or strings.
        try:
            n = float(s)
            if n > 1e12:  # ms
                n /= 1000
            if n > 10_000_000:  # post-1970 epoch, loose sanity guard
                return _dt.datetime.fromtimestamp(n, tz=_dt.timezone.utc)
        except ValueError:
            pass
        # ISO / RFC3339.
        try:
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def posting_age_distribution(data_dir: Path) -> dict:
    """Histogram of posting age across matched jobs.

    Bins: 0-7d, 8-30d, 31-60d, 61+d, unknown. Older listings correlate
    with ghost-job risk, so this complements the fake_detector output
    without duplicating its signal.
    """
    jobs = _load_many(data_dir / "matches")
    now = _dt.datetime.now(_dt.timezone.utc)
    bins = {"0-7d": 0, "8-30d": 0, "31-60d": 0, "61+d": 0, "unknown": 0}

    for job in jobs:
        if not isinstance(job, dict):
            continue
        dt = _parse_job_date(job)
        if dt is None:
            bins["unknown"] += 1
            continue
        age_days = (now - dt).days
        if age_days < 0:
            # Future-dated posting - treat as fresh rather than unknown.
            bins["0-7d"] += 1
        elif age_days <= 7:
            bins["0-7d"] += 1
        elif age_days <= 30:
            bins["8-30d"] += 1
        elif age_days <= 60:
            bins["31-60d"] += 1
        else:
            bins["61+d"] += 1

    total = sum(bins.values())
    pct = {k: (round(100 * v / total, 1) if total else 0.0) for k, v in bins.items()}
    return {"bins": bins, "percent": pct, "total": total}


# ──────────────────────────────────────────────────────────────────
# Tier 2: source effectiveness (match rate per ATS)
# ──────────────────────────────────────────────────────────────────
def source_effectiveness(data_dir: Path) -> list[dict]:
    """Per-source: how many ingested vs how many matched.

    Ingested counts come from the cumulative source_breakdown across all
    market_intel.json cycles. Matched counts come from reading
    data/matches/*.json and grouping on the `_source` field the ingest
    agent stamps on each payload.
    """
    history = _load_market_history(data_dir)
    ingested: Counter[str] = Counter()
    for cyc in history:
        for src, n in (cyc.get("source_breakdown") or {}).items():
            ingested[src] += int(n or 0)

    matches = _load_many(data_dir / "matches")
    matched: Counter[str] = Counter()
    for job in matches:
        if not isinstance(job, dict):
            continue
        src = (job.get("_source") or "unknown").lower()
        matched[src] += 1

    out: list[dict] = []
    for src in sorted(set(ingested) | set(matched)):
        ing = ingested.get(src, 0)
        mat = matched.get(src, 0)
        rate = round(100 * mat / ing, 2) if ing else 0.0
        out.append({"source": src, "ingested": ing, "matched": mat, "match_rate_pct": rate})
    # Rank by match rate desc, with ties broken by ingested volume so a
    # 2/3 one-off doesn't leapfrog a stable 180/600.
    out.sort(key=lambda x: (x["match_rate_pct"], x["ingested"]), reverse=True)
    return out


# ──────────────────────────────────────────────────────────────────
# Tier 2: ghost-job rate by company
# ──────────────────────────────────────────────────────────────────
def ghost_job_rate_by_company(data_dir: Path, min_matches: int = 3) -> list[dict]:
    """Share of each company's matched jobs flagged as ghost-suspect.

    Depends on fake_detector having tagged each match with `_fake` and
    `_is_suspect`. Silently returns an empty list if no matches are
    present yet. We filter to companies with at least `min_matches` so
    one-hit flukes don't dominate the leaderboard.
    """
    jobs = _load_many(data_dir / "matches")
    per_co: dict[str, dict] = {}

    for job in jobs:
        if not isinstance(job, dict):
            continue
        co = (job.get("company") or "Unknown").strip() or "Unknown"
        entry = per_co.setdefault(co, {"company": co, "total": 0, "suspect": 0, "score_sum": 0.0})
        entry["total"] += 1
        if job.get("_is_suspect"):
            entry["suspect"] += 1
        fake = job.get("_fake") or {}
        if isinstance(fake, dict):
            entry["score_sum"] += float(fake.get("score") or 0)

    out: list[dict] = []
    for entry in per_co.values():
        if entry["total"] < min_matches:
            continue
        out.append({
            "company": entry["company"],
            "matches": entry["total"],
            "suspects": entry["suspect"],
            "suspect_rate_pct": round(100 * entry["suspect"] / entry["total"], 1),
            "avg_fake_score": round(entry["score_sum"] / entry["total"], 3),
        })
    # Leaderboard by suspect rate first, then raw count as a tiebreaker.
    out.sort(key=lambda x: (x["suspect_rate_pct"], x["suspects"]), reverse=True)
    return out


# ──────────────────────────────────────────────────────────────────
# Composite Tier 2 bundle
# ──────────────────────────────────────────────────────────────────
def tier2_bundle(data_dir: Path) -> dict:
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "new_companies": new_companies(data_dir),
        "posting_age_distribution": posting_age_distribution(data_dir),
        "source_effectiveness": source_effectiveness(data_dir),
        "ghost_job_rate_by_company": ghost_job_rate_by_company(data_dir),
    }
