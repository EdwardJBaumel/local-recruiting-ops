"""
[MOD-INGEST]: The Ingestion Engine
Pulls jobs directly from big tech career page APIs and ATS platforms.
No scraping proxies or stealth needed -- these are the same endpoints
their own career pages use to render job listings.

ATS APIs (public JSON, zero auth):
  - Greenhouse: Stripe, Airbnb, Figma, Notion, DoorDash, Databricks, Square,
                Coinbase, Plaid, Cloudflare, Discord, Instacart, GitLab, etc.
  - Lever:      Netflix, Spotify, etc.
  - Ashby:      OpenAI, Ramp, Vercel, etc.

Direct career APIs (company-hosted, public):
  - Apple:      jobs.apple.com/api
  - Amazon:     amazon.jobs/en/search.json
"""

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

import requests
from bs4 import BeautifulSoup

from core.io_safe import write_json_atomic
from core.protocol import SentinelPacket, Sender, PayloadType, Priority
from core.preferences import title_has_blocked_keyword
from core.scraper_session import (
    realistic_headers,
    polite_get,
    polite_post,
    jittered_sleep,
)

logger = logging.getLogger("lro.ingest")

# How long a slug stays in cooldown after a 404. Keeps us from hammering
# ATS endpoints that have already told us "no such board" while still
# letting slugs recover when a company re-opens their Greenhouse / Lever
# page. Override via config["ingest"]["dead_slug_cooldown_days"] for tests.
DEAD_SLUG_COOLDOWN_DAYS_DEFAULT = 7


def _epoch_ms_to_iso(value) -> str:
    """Convert a Unix-millis epoch (Lever / some Workday endpoints) to
    an ISO 8601 string. Returns "" on any parse failure so callers can
    drop into the "no date" branch cleanly without a crash.

    The fake_detector and the UI both speak ISO 8601 — by normalising
    here we don't need format-aware parsing downstream.
    """
    if value in (None, "", 0):
        return ""
    try:
        seconds = float(value) / 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""

# NOTE: a `USER_AGENTS` list used to live here for per-request UA
# rotation. It's gone — `core/scraper_session.realistic_headers()` is
# the single source of truth for User-Agent rotation now, and was
# already used everywhere this list might have been. Removed in the
# dead-code audit; this comment is a paper trail in case someone reads
# git blame later and wonders.

# ---------------------------------------------------------------------------
# Default company -> ATS mapping
# ---------------------------------------------------------------------------
# These ship empty on purpose: a cloned public repo should NOT auto-scrape
# any company. Users add their own targets through Settings -> Companies in
# the dashboard, which writes into config["ingest"]["greenhouse_companies"]
# etc. and those config values override these defaults.
#
# If you want a starter list to paste into the UI, here's what Sentinel
# was seeded with originally:
#   Greenhouse: stripe, airbnb, figma, notion, doordash, databricks,
#               square, coinbase, plaid, cloudflare, discord, gitlab,
#               instacart, lyft, twitch, airtable, hashicorp, snyk,
#               cockroachlabs, gusto, brex, nerdwallet, robinhood,
#               duolingo, pinterest, coreweave, wiz-inc
#   Lever:      netflix, spotify
#   Ashby:      OpenAI (openai), Ramp (ramp), Vercel (vercel)
# ---------------------------------------------------------------------------
GREENHOUSE_COMPANIES: list[str] = []
LEVER_COMPANIES: list[str] = []
ASHBY_COMPANIES: list[tuple[str, str]] = []  # (display_name, ashby_slug)

# Known Workday-hosted companies. Each tuple is
#   (config_flag_key, tenant, region, board, display_name)
# The flag key is what the UI checkbox writes to config (so we can
# enable/disable each independently). Tenant + region + board are
# the three parts of a Workday URL — there's no central registry,
# so this list is hand-curated. To add another company:
#   1. Find their careers page (https://CO.wdN.myworkdayjobs.com/...)
#   2. Note the wdN region and the path slug after .com/
#   3. Add a tuple here + a UI checkbox + a config-key reader below
WORKDAY_TENANTS: list[tuple[str, str, str, str, str]] = [
    ("enable_nvidia", "nvidia", "wd5", "NVIDIAExternalCareerSite", "Nvidia"),
    ("enable_adobe",  "adobe",  "wd5", "external_experienced",     "Adobe"),
    # Removed Apr 2026: intel + salesforce + ibm + cisco.
    #   intel:      tenant works but PM yield is near-zero. Last cycle:
    #               3 PM hits across 5 pages of fetching = pure waste.
    #   salesforce: HTTP 422 (request schema drifted underneath us)
    #   ibm:        HTTP 422 (same)
    #   cisco:      tenant slug renamed (404)
    # Listed here as comments so a future maintainer doesn't think
    # the deletion was accidental and can restore any of them if
    # the underlying endpoint stabilises again. Old config rows:
    #   ("enable_intel",      "intel",      "wd1", "External",             "Intel"),
    #   ("enable_salesforce", "salesforce", "wd1", "External_Career_Site", "Salesforce"),
    #   ("enable_ibm",        "ibm",        "wd1", "IBM",                  "IBM"),
    #   ("enable_cisco",      "cisco",      "wd5", "external_career_site", "Cisco"),
]


class IngestAgent:
    """Pulls PM/PgM/ProdOps jobs from big tech career APIs."""

    def __init__(self, config: dict, data_dir: Optional[Union[str, Path]] = None):
        self.config = config
        self.delay_range = config.get("delay_range", (1, 3))
        self.data_dir = Path(data_dir) if data_dir else None
        try:
            self.cooldown_days = int(config.get("dead_slug_cooldown_days",
                                                DEAD_SLUG_COOLDOWN_DAYS_DEFAULT))
        except (TypeError, ValueError):
            self.cooldown_days = DEAD_SLUG_COOLDOWN_DAYS_DEFAULT
        # Session = connection pool + cookie jar across all requests
        # in a cycle. Default headers are a realistic browser baseline;
        # individual scrapers override per-request via `realistic_headers()`
        # to randomize the User-Agent each call.
        self.session = requests.Session()
        self.session.headers.update(realistic_headers(json_request=True))

        self.role_keywords = config.get("role_keywords", [
            "product manager",
            "senior product manager",
            "product operations",
            "program manager",
            "product excellence",
            "technical program manager",
        ])
        # Blocked title keywords — wrong-discipline markers (engineer,
        # designer, counsel, ...) the user never wants in the funnel.
        # Forwarded from config.preferences by the orchestrator. Same
        # list + word-boundary matching the TitleScorer penalises with,
        # so a title dropped here is exactly one the scorer would sink.
        _prefs = config.get("preferences") or {}
        self.blocked_title_keywords = list(
            _prefs.get("blocked_title_keywords") or []
        )

        # Allow config to override which companies to check
        self.greenhouse = config.get("greenhouse_companies", GREENHOUSE_COMPANIES)
        self.lever = config.get("lever_companies", LEVER_COMPANIES)
        self.ashby = config.get("ashby_companies", ASHBY_COMPANIES)
        # Big-tech toggles default OFF so a freshly cloned repo does not
        # auto-scrape anyone. The dashboard's Settings -> Companies panel
        # is where users opt in per source.
        self.enable_amazon = config.get("enable_amazon", False)
        self.enable_google = config.get("enable_google", False)
        # Workday tenants — one toggle per company. Defaults all off so
        # a fresh clone of the repo doesn't auto-scrape anyone the user
        # didn't ask for.
        self.workday_enabled = {
            cfg_key: bool(config.get(cfg_key, False))
            for cfg_key, *_ in WORKDAY_TENANTS
        }

        # Dead-slug tracker. `self.dead_slugs` is the per-cycle snapshot
        # used for the Brief-tab banner (refreshed/new failures this run).
        # `self._dead_history` is the authoritative rolling history keyed
        # by (source, slug) with an ISO timestamp of the last failure.
        # Both are persisted to `data/dead_slugs.json` via io_safe. On
        # subsequent runs, slugs still inside the cooldown window are
        # skipped before we make the HTTP call, so we stop burning RTTs
        # on endpoints that already told us "no such board".
        self.dead_slugs: list[dict] = []
        self._dead_history: dict[tuple, str] = self._load_dead_history()

        # Per-source packet tallies. Populated at the end of every run()
        # so /api/status can surface "greenhouse:stripe → 4 jobs" style
        # breakdown on the Brief tab (#89/#90).
        self.last_source_stats: dict[str, dict[str, int]] = {}

    # ---------------- dead-slug cooldown helpers ----------------

    def _dead_file(self) -> Optional[Path]:
        return (self.data_dir / "dead_slugs.json") if self.data_dir else None

    def _load_dead_history(self) -> dict[tuple, str]:
        """Read the persisted dead-slug history into memory. The on-disk
        format is a list of {source, slug, ts} for backwards compatibility
        with the Brief-tab banner (server.py reads the same file)."""
        f = self._dead_file()
        if not f or not f.exists():
            return {}
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("dead_slugs load failed: %s", e)
            return {}
        out: dict[tuple, str] = {}
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                src, slug = entry.get("source"), entry.get("slug")
                ts = entry.get("ts") or ""
                if src and slug:
                    out[(src, slug)] = ts
        return out

    def _persist_dead_history(self) -> None:
        f = self._dead_file()
        if not f:
            return
        payload = [
            {"source": src, "slug": slug, "ts": ts}
            for (src, slug), ts in sorted(self._dead_history.items())
        ]
        try:
            write_json_atomic(f, payload)
        except Exception as e:
            logger.debug("dead_slugs persist failed: %s", e)

    def _is_in_cooldown(self, source: str, slug: str) -> bool:
        """True if this (source, slug) was last recorded dead within the
        cooldown window. Unparseable timestamps fail open so a corrupt
        entry can't permanently hide a slug."""
        ts = self._dead_history.get((source, slug))
        if not ts:
            return False
        try:
            last = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last
        return age < timedelta(days=self.cooldown_days)

    def _clear_dead_slug(self, source: str, slug: str) -> None:
        """Drop a slug from the history when it comes back alive. Called
        from fetchers on any non-404 response so a recovered slug stops
        being suppressed on the next cycle."""
        if self._dead_history.pop((source, slug), None) is not None:
            self._persist_dead_history()

    def _record_dead_slug(self, source: str, slug: str):
        """Track a 404'd ATS slug for the cycle banner and update the
        rolling history on disk so the next cycle can skip it during the
        cooldown window. Dedupes per (source, slug) within the cycle."""
        ts = datetime.now(timezone.utc).isoformat()
        self._dead_history[(source, slug)] = ts
        self._persist_dead_history()
        key = (source, slug)
        if any((d.get("source"), d.get("slug")) == key for d in self.dead_slugs):
            return
        self.dead_slugs.append({"source": source, "slug": slug, "ts": ts})

    # Title-keyword trapdoor — runs at ingest so doomed roles are
    # never parsed or scored. Mirrors core/preferences.py's match-time
    # trapdoor (Director / VP / Head-of titles get rejected when the
    # candidate has fewer than ~8 yoe). Pushing it upstream saves a
    # parse + match pass on roles that can't pass the experience
    # filter anyway. Conservative — only matches the specific senior-
    # title prefixes we know the match-time trapdoor will reject.
    _TRAPDOOR_TITLE_PATTERNS: tuple[str, ...] = (
        "director of",
        " director,",
        ", director ",
        "head of ",
        "vp ",
        "vp,",
        "vp of ",
        "vice president",
        "chief ",
        "cto", "cpo", "ceo", "coo", "cfo",
    )

    # Non-PM specialties that contain the word "product". When the
    # "product"-permissive branch in `_matches` fires, we use this
    # block-list to keep Product Marketing / Product Designer /
    # Product Engineer etc. out of the funnel. They share the word
    # but not the job. Order doesn't matter — substring match.
    _NON_PM_PRODUCT_TITLES: tuple[str, ...] = (
        "product marketing",
        "product designer",
        "product design ",     # trailing space avoids dropping "Product Design Manager" — borderline, see comment
        "product engineer",
        "product analyst",
        "product researcher",
        "product research ",
        "product specialist",
        "product sales",
        "product support",
        "product associate",
        "product counsel",     # legal
        "product writer",
        "product copywriter",
        "product editor",
    )

    def _matches(self, title: str) -> bool:
        """True if `title` should be parsed + scored.

        Permissive PM filter
        --------------------
        The historical filter required a full substring like
        "product manager" to be present. That missed real PM titles
        with non-standard naming — "Senior Product, Cloud Platform",
        "Product Lead, Identity", "Product, Growth" — and also missed
        Google's preference for `Program Manager` titles since
        "program manager" is a different substring entirely.

        New rule:
          1. If the title contains "product" AND none of the known
             non-PM specialties (Product Marketing / Designer /
             Engineer / Analyst / etc.), KEEP. This is the loose
             "everything product-y" sweep the user asked for.
          2. Otherwise fall back to the explicit role_keywords list
             (catches "Technical Program Manager", "TPM, Cloud",
             "AI PM, Foundation Models", etc. — titles with no
             "product" in them).
          3. Then the trapdoor drops senior titles for sub-8-yoe
             profiles, same as before.

        Fails open on any unexpected exception so an obscure title
        format never silently blocks the entire ingest.
        """
        t = title.lower()

        # Blocked-title skip: wrong-discipline markers the user
        # excluded. Dropped here so they're never parsed, scored, or
        # stored — same keyword set + word-boundary match the
        # TitleScorer penalises with, kept in lockstep via the shared
        # title_has_blocked_keyword helper.
        if self.blocked_title_keywords and title_has_blocked_keyword(
            t, self.blocked_title_keywords
        ):
            return False

        # Branch 1: "product" is in the title. Keep unless the title
        # falls into a known non-PM specialty (marketing, design, etc.).
        if "product" in t:
            if any(spec in t for spec in self._NON_PM_PRODUCT_TITLES):
                return False
            # Fall through — title is product-y enough to look at.
        else:
            # Branch 2: no "product" — must match an explicit keyword
            # (TPM, AI PM, program manager) to be relevant at all.
            if not any(kw in t for kw in self.role_keywords):
                return False

        # Title-trapdoor pre-filter. Only fires when the user's
        # profile has trapdoor enabled AND their yoe is below the
        # threshold the match-time trapdoor uses (8 years).
        try:
            prefs = self.config.get("preferences") or {}
            if prefs.get("trapdoor_enabled", True):
                yoe = float(prefs.get("years_experience") or 0)
                if 0 < yoe < 8 and any(p in t for p in self._TRAPDOOR_TITLE_PATTERNS):
                    return False
        except Exception:
            pass
        return True

    def _html_card_matches(self, el) -> bool:
        """Pre-filter HTML scrape cards (Google, Meta, Microsoft) by role
        keyword before emitting a RAW_HTML packet. The LLM parse step
        downstream is expensive (one Ollama call per card) so dropping
        clearly off-target cards here cuts match-phase load by 60-80% on
        broad keyword searches. We check the card's visible text so a
        title rendered inside a span, h3, or aria-label all count."""
        if not self.role_keywords:
            return True
        try:
            text = el.get_text(" ", strip=True).lower() if hasattr(el, "get_text") else str(el).lower()
        except Exception:
            return True  # Fail open so a parse glitch doesn't silently drop everything.
        return any(kw in text for kw in self.role_keywords)

    def _make_packet(self, job: dict, source: str) -> SentinelPacket:
        # If the ATS API didn't return a structured salary band, scrape
        # one out of the description text. Most Greenhouse/Lever/Ashby
        # JDs put the band in the description (pay-disclosure laws
        # require the disclosure but not a schema), so before this
        # ~95% of postings hit the registry with `salary: None` and
        # the Brief tab's salary histogram rendered "No salary data
        # yet" despite ~30% of JDs explicitly listing pay. The regex
        # extractor lives in core/salary_extract.py — best-effort,
        # returns None when the text doesn't have a parseable band.
        existing = job.get("salary")
        has_structured = (
            isinstance(existing, dict)
            and (existing.get("min") or existing.get("max"))
        )
        if not has_structured:
            desc = job.get("description") or ""
            if desc:
                try:
                    from core.salary_extract import extract_salary
                    # JSON-ATS descriptions (Greenhouse/Ashby/...) ship
                    # as raw HTML — the pay band is wrapped in markup and
                    # uses &mdash; / &#36; entities the plain-text regex
                    # can't see. Strip tags + decode entities for the
                    # salary scan only; the stored `description` stays
                    # HTML for the UI to render.
                    salary_text = BeautifulSoup(
                        desc, "html.parser"
                    ).get_text(" ", strip=True)
                    extracted = extract_salary(salary_text)
                    if extracted:
                        # Overwrite even if `existing` was a non-empty
                        # but malformed value (e.g. a raw string from
                        # Remotive). The extractor's output matches the
                        # match-payload schema exactly.
                        job = {**job, "salary": extracted}
                except Exception:
                    # Never let salary extraction kill a packet.
                    pass
        from core.job_signature import attach_job_signature
        attach_job_signature(job)
        return SentinelPacket(
            sender=Sender.INGEST,
            payload_type=PayloadType.JSON_JOB,
            payload={**job, "_source": source},
            priority=Priority.MED,
        )

    def _error_packet(self, source: str, error: str) -> SentinelPacket:
        return SentinelPacket(
            sender=Sender.INGEST,
            payload_type=PayloadType.ERROR_LOG,
            payload={"error": error, "source": source},
            priority=Priority.HIGH,
        )

    def _sleep(self):
        # Delegates to scraper_session.jittered_sleep so all "wait
        # politely between requests" calls go through one place. The
        # pause lower/upper bounds come from config so tests can shrink
        # them and the user can widen them if they're getting throttled.
        lo, hi = self.delay_range
        jittered_sleep(lo, hi)

    def _guess_seniority(self, title: str) -> str:
        t = title.lower()
        if any(w in t for w in ["senior", "sr.", "sr ", "staff", "principal"]):
            return "senior"
        if any(w in t for w in ["lead", "head of"]):
            return "lead"
        if any(w in t for w in ["director", "vp", "vice president"]):
            return "director"
        if any(w in t for w in ["junior", "jr", "associate", "entry"]):
            return "junior"
        return "mid"

    # -----------------------------------------------------------------------
    # GREENHOUSE (public JSON API, no auth)
    # Endpoint: https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    # -----------------------------------------------------------------------
    def fetch_greenhouse(self, company: str) -> list[SentinelPacket]:
        packets = []
        if self._is_in_cooldown("greenhouse", company):
            logger.debug("[Greenhouse:%s] in dead-slug cooldown, skipping", company)
            return packets
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 404:
                logger.warning(
                    "[Greenhouse:%s] slug not found (404). Run `python scripts/probe_slugs.py --extras` "
                    "or remove from config.json.", company)
                self._record_dead_slug("greenhouse", company)
                return packets
            resp.raise_for_status()
            self._clear_dead_slug("greenhouse", company)
            jobs = resp.json().get("jobs", [])

            for job in jobs:
                title = job.get("title", "")
                if not self._matches(title):
                    continue

                location = job.get("location", {}).get("name", "")
                desc = job.get("content", "")
                if desc:
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:12000]

                packets.append(self._make_packet({
                    "title": title,
                    "company": company.replace("-", " ").title(),
                    "location": location,
                    "salary": None,
                    "description": desc,
                    "technologies": [],
                    "seniority": self._guess_seniority(title),
                    "job_type": "full-time",
                    "remote": "remote" if "remote" in location.lower() else "unknown",
                    "url": job.get("absolute_url", ""),
                    "posted_date": job.get("updated_at", ""),
                }, f"greenhouse:{company}"))

            logger.info("[Greenhouse:%s] %d matching jobs", company, len(packets))

        except Exception as e:
            logger.warning("[Greenhouse:%s] %s", company, e)
            packets.append(self._error_packet(f"greenhouse:{company}", str(e)))

        return packets

    # -----------------------------------------------------------------------
    # LEVER (public JSON API, no auth)
    # Endpoint: https://api.lever.co/v0/postings/{company}
    # -----------------------------------------------------------------------
    def fetch_lever(self, company: str) -> list[SentinelPacket]:
        packets = []
        if self._is_in_cooldown("lever", company):
            logger.debug("[Lever:%s] in dead-slug cooldown, skipping", company)
            return packets
        url = f"https://api.lever.co/v0/postings/{company}"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 404:
                logger.warning(
                    "[Lever:%s] slug not found (404). Run `python scripts/probe_slugs.py --extras` "
                    "or remove from config.json.", company)
                self._record_dead_slug("lever", company)
                return packets
            resp.raise_for_status()
            self._clear_dead_slug("lever", company)
            jobs = resp.json()

            for job in jobs:
                title = job.get("text", "")
                if not self._matches(title):
                    continue

                desc = job.get("descriptionPlain", "") or ""
                categories = job.get("categories", {})
                location = categories.get("location", "")
                team = categories.get("team", "")

                # Lever sometimes has salary in additionalPlain
                additional = job.get("additionalPlain", "") or ""
                salary = None
                if "$" in additional:
                    for line in additional.split("\n"):
                        if "$" in line:
                            salary = line.strip()[:100]
                            break

                packets.append(self._make_packet({
                    "title": title,
                    "company": company.replace("-", " ").title(),
                    "location": location,
                    "salary": salary,
                    # 500-char cap was a v0 hack to avoid bloating the
                    # parser prompt. With qwen3:14b's context budget and
                    # the UI rendering full HTML JDs, the cap mid-truncates
                    # listings and shows "see..." in the middle of a
                    # sentence. 12k chars covers ~95% of real JDs, with
                    # a hard cap so a malformed feed can't blow up the
                    # downstream embedding step.
                    "description": desc[:12000],
                    "technologies": [],
                    "seniority": self._guess_seniority(title),
                    "job_type": categories.get("commitment", "full-time"),
                    "remote": "remote" if "remote" in location.lower() else "unknown",
                    "url": job.get("hostedUrl", ""),
                    # Lever ships `createdAt` as Unix MILLIS. Convert to
                    # ISO 8601 here so downstream parsers see a single
                    # canonical format. Was set to "" — every Lever
                    # posting registered as undated, which after the
                    # missing-posted-date safety cap meant they all
                    # showed Clear regardless of how stale they were.
                    "posted_date": _epoch_ms_to_iso(job.get("createdAt")),
                    "team": team,
                }, f"lever:{company}"))

            logger.info("[Lever:%s] %d matching jobs", company, len(packets))

        except Exception as e:
            logger.warning("[Lever:%s] %s", company, e)
            packets.append(self._error_packet(f"lever:{company}", str(e)))

        return packets

    # -----------------------------------------------------------------------
    # ASHBY (public JSON API, POST)
    # Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}
    # -----------------------------------------------------------------------
    def fetch_ashby(self, display_name: str, slug: str) -> list[SentinelPacket]:
        packets = []
        if self._is_in_cooldown("ashby", slug):
            logger.debug("[Ashby:%s] in dead-slug cooldown, skipping", display_name)
            return packets
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 404:
                logger.warning(
                    "[Ashby:%s] slug '%s' not found (404). Run `python scripts/probe_slugs.py --extras` "
                    "or remove from config.json.", display_name, slug)
                self._record_dead_slug("ashby", slug)
                return packets
            resp.raise_for_status()
            self._clear_dead_slug("ashby", slug)
            data = resp.json()
            jobs = data.get("jobs", [])

            for job in jobs:
                title = job.get("title", "")
                if not self._matches(title):
                    continue

                location = job.get("location", "")
                if isinstance(location, dict):
                    location = location.get("name", "")

                dept = job.get("department", "")
                if isinstance(dept, dict):
                    dept = dept.get("name", "")

                # Ashby's job-board API already ships the full JD in the
                # list response — prefer the HTML body (the UI renders it
                # through DOMPurify), fall back to plain text. This used
                # to be hardcoded to "" which left every Ashby job
                # (OpenAI, Harvey, Ramp, Cursor...) description-less and
                # effectively unmatchable.
                desc = (job.get("descriptionHtml")
                        or job.get("descriptionPlain") or "").strip()

                packets.append(self._make_packet({
                    "title": title,
                    "company": display_name,
                    "location": location or "",
                    "salary": None,
                    "description": desc[:12000],
                    "technologies": [],
                    "seniority": self._guess_seniority(title),
                    "job_type": job.get("employmentType", "full-time"),
                    "remote": "remote" if "remote" in str(location).lower() else "unknown",
                    "url": job.get("jobUrl", "") or f"https://jobs.ashbyhq.com/{slug}/{job.get('id', '')}",
                    "posted_date": job.get("publishedAt", ""),
                    "department": dept,
                }, f"ashby:{slug}"))

            logger.info("[Ashby:%s] %d matching jobs", display_name, len(packets))

        except Exception as e:
            logger.warning("[Ashby:%s] %s", display_name, e)
            packets.append(self._error_packet(f"ashby:{slug}", str(e)))

        return packets

    # NOTE: Apple / Meta / Microsoft scrapers were removed before public
    # release. Each company's TOS or robots.txt explicitly prohibits
    # automated access:
    #   - Meta: robots.txt blocks ClaudeBot + every named AI bot
    #   - Apple: site TOS bans "robot, spider or other automatic device"
    #   - Microsoft: service agreement prohibits scraping
    # The Playwright runner that backed those scrapers was also removed
    # — there are no remaining callers and shipping unused anti-bot
    # tooling sends the wrong signal in a public repo. If a future
    # company needs SPA scraping AND has an open TOS, port that
    # runner back from the git history at that point.
    # NOTE (Apr 2026): the Netflix scraper used to live here. Their
    # old jobs.netflix.com/api/search endpoint died when they moved
    # to a Phenom SPA at explore.jobs.netflix.net (JS-only, no clean
    # public REST API). The Lever public board they migrated through
    # exists at api.lever.co/v0/postings/netflix but has zero active
    # postings. The scraper + its toggle / call site / config key
    # were all removed in the dead-companies cleanup. If Netflix
    # ever ships a working public feed again, restore from git.

    # -----------------------------------------------------------------------
    # WORKDAY (generic — works for any tenant on Workday's public job
    # search API). One scraper unlocks Nvidia, Adobe, Salesforce, IBM,
    # Cisco, Intel, plus any other Workday-hosted company we add later.
    # -----------------------------------------------------------------------
    def fetch_workday(
        self,
        tenant: str,
        region: str,       # "wd1" / "wd3" / "wd5" — pinned per company
        board: str,        # the URL slug Workday uses for the public board
        display_name: str,
    ) -> list[SentinelPacket]:
        """Fetch jobs from a Workday-hosted tenant's public board.

        Workday's job-search URL pattern:
            POST https://{tenant}.{region}.myworkdayjobs.com
                  /wday/cxs/{tenant}/{board}/jobs
            body: { appliedFacets: {}, limit: 20, offset: N, searchText: KW }

        Response: {"total": N, "jobPostings": [{title, externalPath,
        locationsText, postedOn, ...}, ...]}

        Why this works for so many companies: every Workday tenant
        exposes the same JSON contract. We just need (tenant, region,
        board) to point at the right one. The boards are named by the
        company — there's no central registry — so we hand-curate the
        list of well-known ones in WORKDAY_TENANTS below.

        Pagination: walk pages of 20 until we exhaust results or hit
        MAX_PAGES (default 5 = 100 jobs per keyword per tenant).

        Keyword strategy: Workday's searchText is loose enough that
        one broad keyword ("product") returns nearly the same set as
        seven specific ones. Originally we iterated every keyword
        from config.role_keywords; the per-cycle log showed identical
        page sequences (p1: 18/20, p2: 17/20, p3: 2/20...) repeated
        7× per tenant, with the registry dedupe collapsing them at
        the end. Wasted ~30 requests per tenant (Apr 2026 cycle log
        analysis). Now we iterate ONLY the first keyword — coverage
        is unchanged in practice, total Workday traffic is 7× lower.
        """
        # 3-page cap (was 5). Last cycle's logs showed Adobe page 4
        # returning 15/20 and page 5 returning 2/20 — mostly tail-end
        # repeats and stale postings. Pages 1-3 carry the freshness
        # signal; capping there saves ~30s/tenant with negligible
        # coverage loss.
        MAX_PAGES = 3
        url = f"https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
        packets = []

        # Workday keyword filter is fuzzy — one broad sweep catches
        # the same roles as seven narrow sweeps, since each keyword
        # returns the same fuzzy match set. Iterate just the first.
        keywords_to_run = self.role_keywords[:1] if self.role_keywords else [""]
        for keyword in keywords_to_run:
            offset = 0
            page = 0
            while page < MAX_PAGES:
                page += 1
                payload = {
                    "appliedFacets": {},
                    "limit": 20,
                    "offset": offset,
                    "searchText": keyword,
                }
                try:
                    # polite_post: auto-retries on 429/503 with exp
                    # backoff. Realistic headers add a fresh per-request
                    # User-Agent so we don't fingerprint as
                    # "always-same-UA Python script."
                    resp = polite_post(
                        self.session,
                        url,
                        json=payload,
                        headers=realistic_headers(json_request=True),
                        timeout=15,
                    )
                    if resp.status_code == 404:
                        # Tenant or board name is wrong. Mark dead so we
                        # stop hammering on subsequent cycles.
                        logger.warning("[Workday:%s] 404 — board '%s' likely renamed", tenant, board)
                        self._record_dead_slug("workday", tenant)
                        return packets
                    if resp.status_code != 200:
                        logger.warning("[Workday:%s p%d] HTTP %d", tenant, page, resp.status_code)
                        break

                    data = resp.json()
                    jobs = data.get("jobPostings") or []
                    if not jobs:
                        break  # past the last page

                    kept = 0
                    for j in jobs:
                        title = (j.get("title") or "").strip()
                        if not self._matches(title):
                            continue
                        kept += 1
                        external = (j.get("externalPath") or "").lstrip("/")
                        full_url = f"https://{tenant}.{region}.myworkdayjobs.com/{board}/{external}"
                        # Workday's list endpoint returns cards only — no
                        # JD body. Pre-parsed JSON packets skip the LLM
                        # parse stage (the fast pass goes straight to
                        # match), so the description has to be backfilled
                        # here via a per-posting detail call or it stays
                        # empty forever. Best-effort: polite_get handles
                        # 429/503 backoff; on failure the card is kept
                        # JD-less rather than dropped.
                        description = self._fetch_workday_detail(
                            tenant, region, board, external
                        )
                        packets.append(self._make_packet({
                            "title": title,
                            "company": display_name,
                            "location": (j.get("locationsText") or "").strip(),
                            "salary": "",
                            "description": description,
                            "technologies": [],
                            "seniority": self._guess_seniority(title),
                            "job_type": "full-time",
                            "remote": "remote" if "remote" in (j.get("locationsText") or "").lower() else "unknown",
                            "url": full_url,
                            "posted_date": (j.get("postedOn") or "").strip(),
                            "team": "",
                        }, f"workday:{tenant}"))

                    logger.info("[Workday:%s p%d] kept %d/%d", tenant, page, kept, len(jobs))

                    if len(jobs) < 20:
                        break  # last page (Workday returns < limit when exhausted)
                    offset += 20
                    self._sleep()

                except requests.exceptions.RequestException as e:
                    logger.warning("[Workday:%s] %s", tenant, e)
                    break
                except (ValueError, KeyError) as e:
                    logger.warning("[Workday:%s] bad response: %s", tenant, e)
                    break

        return packets

    def _fetch_workday_detail(
        self, tenant: str, region: str, board: str, external_path: str
    ) -> str:
        """Best-effort fetch of a single Workday posting's JD body.

        Workday's list endpoint only returns cards (title, location,
        path). The description lives behind a per-posting detail call at
        /wday/cxs/{tenant}/{board}/{externalPath}, which responds with
        {"jobPostingInfo": {"jobDescription": "<html>", ...}}.

        Returns "" on any failure — the caller keeps the card, just
        without JD text — so a slow or flaky detail endpoint can never
        break the ingest cycle.
        """
        external_path = (external_path or "").lstrip("/")
        if not external_path:
            return ""
        detail_url = (
            f"https://{tenant}.{region}.myworkdayjobs.com"
            f"/wday/cxs/{tenant}/{board}/{external_path}"
        )
        try:
            resp = polite_get(
                self.session,
                detail_url,
                headers=realistic_headers(json_request=True),
                timeout=12,
            )
            if resp.status_code != 200:
                return ""
            info = (resp.json() or {}).get("jobPostingInfo") or {}
            return (info.get("jobDescription") or "").strip()[:12000]
        except Exception:
            return ""

    # -----------------------------------------------------------------------
    # AMAZON (public jobs API)
    # -----------------------------------------------------------------------
    def fetch_amazon(self) -> list[SentinelPacket]:
        """Amazon jobs API — paginated, all keywords.

        Coverage fixes vs. the v0 scraper that was capping at 75 jobs
        total (3 keywords × 25 result_limit × 1 page):

          1. Iterate ALL keywords. The original `[:3]` was too
             conservative for a tool the user runs once a day.
          2. Paginate via the API's `offset` param. Amazon caps
             `result_limit` at 100 per call; we walk pages of 100
             until we hit a page with <100 results (= last page) or
             MAX_PAGES (safety belt).
          3. Use polite_get + realistic_headers so the API doesn't
             rate-limit us as aggressively.

        Two-stage keyword pruning
        -------------------------
        Amazon was the single biggest source by volume — pre-fix it
        returned ~430 of 1056 ingested jobs in a single cycle. We
        prune the keyword list TWICE before iterating:

          1. **Trapdoor skip** — drop senior-leaning queries ("head
             of product", "director of product", "vp product", "group
             product manager") when the user's profile is sub-8-yoe
             with trapdoor on. Those queries return pages of senior
             titles that `_matches()` would drop anyway.

          2. **Near-duplicate collapse** — Amazon's `base_query` is a
             fuzzy full-text matcher; "product manager" and "product
             management" return ~95% the same job set, with the dedupe
             at the end of fetch_amazon collapsing duplicates. So the
             second query was buying ~5% extra coverage at the cost of
             a full pagination pass. We keep the more specific phrase
             ("product manager") and drop the management variant.

        Pagination cap
        --------------
        MAX_PAGES = 3 (was 5). Amazon sorts by recency, so pages 1-3
        carry the freshness signal that matters for a daily run; pages
        4-5 are mostly older stale postings already in seen_urls.json
        from earlier cycles. Cuts ~12s per keyword.

        Worst case (trapdoor off, all keywords kept): 5 keywords × 3
        pages × 100 results = 1,500 cards examined per cycle — well
        inside the polite-traffic budget.
        """
        MAX_PAGES = 3
        # Stage 1: skip senior-leaning queries when trapdoor is on for
        # this profile. Mirrors _TRAPDOOR_TITLE_PATTERNS so "what counts
        # as senior" has a single source of truth across the codebase.
        keywords = list(self.role_keywords)
        try:
            prefs = self.config.get("preferences") or {}
            if prefs.get("trapdoor_enabled", True):
                yoe = float(prefs.get("years_experience") or 0)
                if 0 < yoe < 8:
                    senior_query_patterns = (
                        "director of", "head of ", "vp ", "vp of",
                        "vice president", "chief ", "group product manager",
                    )
                    before = len(keywords)
                    keywords = [
                        k for k in keywords
                        if not any(p in k.lower() for p in senior_query_patterns)
                    ]
                    skipped = before - len(keywords)
                    if skipped:
                        logger.info(
                            "[Amazon] trapdoor on (yoe=%g), skipping %d senior "
                            "keyword(s)", yoe, skipped,
                        )
        except Exception:
            # Fail open — keyword list stays at the configured set.
            pass

        # Stage 2: collapse "product manager" + "product management"
        # to just "product manager". Amazon's fuzzy match means these
        # two queries returned the same set with ~5% drift — the second
        # one wasn't earning its pagination budget. The end-of-method
        # dedup (by title + url) was already collapsing the overlap.
        if "product manager" in keywords and "product management" in keywords:
            keywords = [k for k in keywords if k != "product management"]
            logger.info("[Amazon] collapsing 'product management' into "
                        "'product manager' (Amazon search is fuzzy enough)")
        logger.info("[Amazon] querying %d keyword(s) over up to %d page(s)",
                    len(keywords), MAX_PAGES)

        packets = []
        for keyword in keywords:
            offset = 0
            for page in range(MAX_PAGES):
                url = "https://www.amazon.jobs/en/search.json"
                params = {
                    "base_query": keyword,
                    "country": "USA",
                    "result_limit": 100,
                    "offset": offset,
                    "sort": "recent",
                }
                try:
                    resp = polite_get(
                        self.session,
                        url,
                        params=params,
                        headers=realistic_headers(json_request=True),
                        timeout=15,
                    )
                    if resp.status_code != 200:
                        logger.warning("[Amazon:%s p%d] HTTP %d", keyword, page + 1, resp.status_code)
                        break
                    data = resp.json()
                    jobs = data.get("jobs", [])
                    if not jobs:
                        break  # past last page

                    for job in jobs:
                        title = job.get("title", "")
                        if not self._matches(title):
                            continue
                        desc = job.get("description", "")
                        if desc:
                            desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:12000]
                        packets.append(self._make_packet({
                            "title": title,
                            "company": "Amazon",
                            "location": job.get("normalized_location", "") or job.get("location", ""),
                            "salary": None,
                            "description": desc,
                            "technologies": job.get("basic_qualifications", "")[:200] if job.get("basic_qualifications") else "",
                            "seniority": self._guess_seniority(title),
                            "job_type": "full-time",
                            "remote": "unknown",
                            "url": f"https://www.amazon.jobs{job.get('job_path', '')}",
                            "posted_date": job.get("posted_date", ""),
                            "team": job.get("company_name", ""),
                        }, "amazon"))

                    # Stop paging when we got fewer than the requested
                    # batch — Amazon returned its tail page.
                    if len(jobs) < 100:
                        break
                    offset += 100
                    self._sleep()

                except Exception as e:
                    logger.warning("[Amazon:%s p%d] %s", keyword, page + 1, e)
                    packets.append(self._error_packet("amazon", str(e)))
                    break

        # Dedup
        seen = set()
        unique = []
        for p in packets:
            key = (p.payload.get("title", ""), p.payload.get("url", ""))
            if key not in seen:
                seen.add(key)
                unique.append(p)

        logger.info("[Amazon] %d matching jobs", sum(1 for p in unique if p.payload_type == PayloadType.JSON_JOB))
        return unique

    # -----------------------------------------------------------------------
    # GOOGLE (careers page, HTML -- lightweight parse)
    # -----------------------------------------------------------------------
    def fetch_google(self) -> list[SentinelPacket]:
        """Google Careers — single page only, all keywords.

        IMPORTANT (TOS / robots.txt compliance):
        Google's robots.txt explicitly Disallows the paginated
        results URL pattern:
            Disallow: /about/careers/applications/jobs/results?page=
            Disallow: /about/careers/applications/jobs/results/?*&page=
        We respect that. The first page is fair game (no `?page=` param);
        every subsequent page is gated behind robots.txt. So this
        scraper queries page 1 only, across all keywords, and accepts
        we'll see fewer roles per cycle as the trade for not violating
        their stated policy. If you want exhaustive coverage, the right
        path is to email the careers team for an opt-in feed, not to
        bypass robots.txt.

        Coverage approximation: ~20 cards/keyword × N keywords ≈ 20N
        cards examined per cycle. Daily Google ad-PM-role volume is
        well under 100, so even with N=3-5 keywords most days we
        catch everything that's actually new.

        Trapdoor + dedup pruning
        ------------------------
        Same logic as fetch_amazon: we drop senior-leaning queries
        when trapdoor is on (those would all be `_matches()`-trapdoored
        anyway), and we collapse "product manager" / "product
        management" because Google's job-search fuzzy-matches both to
        the same posting set. EVERY Google card becomes a PARSE-stage
        LLM call, so cutting the keyword count is the single biggest
        lever we have on cycle wall time — last cycle's PARSE was
        8 minutes for 53 cards (entirely from Google).
        """
        keywords = list(self.role_keywords)
        try:
            prefs = self.config.get("preferences") or {}
            if prefs.get("trapdoor_enabled", True):
                yoe = float(prefs.get("years_experience") or 0)
                if 0 < yoe < 8:
                    senior_query_patterns = (
                        "director of", "head of ", "vp ", "vp of",
                        "vice president", "chief ", "group product manager",
                    )
                    before = len(keywords)
                    keywords = [
                        k for k in keywords
                        if not any(p in k.lower() for p in senior_query_patterns)
                    ]
                    skipped = before - len(keywords)
                    if skipped:
                        logger.info(
                            "[Google] trapdoor on (yoe=%g), skipping %d senior "
                            "keyword(s)", yoe, skipped,
                        )
        except Exception:
            # Fail open — keyword list stays at the configured set.
            pass

        # Google's job search fuzzy-matches "product manager" and
        # "product management" to nearly identical posting sets, so
        # the second query is mostly buying duplicate parse work.
        # Every duplicate that survives card-extraction becomes a
        # full LLM parse call (~5-10s each on qwen3:8b). Drop one.
        if "product manager" in keywords and "product management" in keywords:
            keywords = [k for k in keywords if k != "product management"]
            logger.info("[Google] collapsing 'product management' into "
                        "'product manager' (Google search is fuzzy enough)")
        logger.info("[Google] querying %d keyword(s)", len(keywords))

        packets = []
        for keyword in keywords:
            kept_for_keyword = 0
            url = "https://www.google.com/about/careers/applications/jobs/results"
            params = {
                "q": keyword,
                "location": "United States",
                "employment_type": "FULL_TIME",
                "hl": "en_US",
                "sort_by": "date",
                # Deliberately NO `page` param — see docstring.
            }
            # The pagination loop is gone but the rest of the keyword
            # loop body stays — wrap the single-shot in a try/except
            # the original code structure used. The `while page <=
            # MAX_PAGES` got replaced with a single iteration; the
            # `page = 1; page += 1` housekeeping is no longer needed.
            page = 1
            while page <= 1:
                try:
                    headers = {**self.session.headers, "Accept": "text/html"}
                    resp = requests.get(url, params=params, headers=headers, timeout=15)
                    if resp.status_code != 200:
                        logger.warning("[Google] HTTP %d for '%s' page %d",
                                       resp.status_code, keyword, page)
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    job_elements = (
                        soup.select("li.lLd3Je")
                        or soup.select("[data-id]")
                        or soup.select("a[href*='jobs/results']")
                    )
                    if not job_elements:
                        # Empty page = past the last page of results.
                        break

                    kept = 0
                    dropped = 0
                    for i, el in enumerate(job_elements):
                        if not self._html_card_matches(el):
                            dropped += 1
                            continue
                        kept += 1
                        # Extract the per-job URL deterministically
                        # before sending the card to the LLM. Why: our
                        # text_clean.clean_for_llm strips <a href>
                        # before the LLM sees the input (we don't
                        # want anchor noise in the parse prompt). Net
                        # effect: the LLM returns "url": null for
                        # every Google card, the registry stores
                        # url=None, and the UI's row-selection /
                        # Apply button breaks because every Google
                        # row shares the same null id. Pulling it
                        # here gives the parser an authoritative
                        # hint to fall back to.
                        url_hint = ""
                        try:
                            anchor = el if el.name == "a" else el.find("a", href=True)
                            href = (anchor.get("href") if anchor else "") or ""
                            if href:
                                if href.startswith("/"):
                                    href = "https://www.google.com" + href
                                url_hint = href
                        except Exception:
                            url_hint = ""
                        packets.append(SentinelPacket(
                            sender=Sender.INGEST,
                            payload_type=PayloadType.RAW_HTML,
                            payload={
                                "html": str(el),
                                "source_url": resp.url,
                                "card_index": i,
                                "_company_hint": "Google",
                                "_url_hint": url_hint,
                            },
                            priority=Priority.MED,
                        ))
                    kept_for_keyword += kept
                    logger.info("[Google:%s p%d] kept %d, dropped %d off-keyword",
                                keyword, page, kept, dropped)

                    # If a whole page returned 0 keepers AND 0 dropped
                    # cards, the search-result set is exhausted. Bail.
                    if kept == 0 and dropped == 0:
                        break

                    self._sleep()
                    page += 1

                except Exception as e:
                    logger.warning("[Google] %s (page %d)", e, page)
                    packets.append(self._error_packet("google", str(e)))
                    break

            logger.info("[Google:%s] %d cards across %d page%s",
                        keyword, kept_for_keyword, page - 1, "" if page == 2 else "s")

        logger.info("[Google] %d total cards extracted", len(packets))
        return packets

    # -----------------------------------------------------------------------
    # FREE JOB BOARD APIs (reliable fallbacks)
    # -----------------------------------------------------------------------
    def fetch_remoteok(self) -> list[SentinelPacket]:
        packets = []
        try:
            resp = self.session.get("https://remoteok.com/api", timeout=15)
            resp.raise_for_status()
            jobs = resp.json()
            if jobs and isinstance(jobs[0], dict) and "legal" in jobs[0]:
                jobs = jobs[1:]

            for job in jobs:
                title = job.get("position", "")
                if not self._matches(title):
                    continue
                desc = job.get("description", "")
                if desc:
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:12000]

                packets.append(self._make_packet({
                    "title": title,
                    "company": job.get("company", ""),
                    "location": job.get("location", "Remote"),
                    "salary": f"${job['salary_min']:,}-${job['salary_max']:,}" if job.get("salary_min") and job.get("salary_max") else None,
                    "description": desc,
                    "technologies": job.get("tags", []),
                    "seniority": self._guess_seniority(title),
                    "job_type": "full-time",
                    "remote": "remote",
                    "url": job.get("url", ""),
                    # RemoteOK ships `date` as ISO 8601 with timezone
                    # (e.g. "2026-04-25T08:00:26+00:00"). Pass it
                    # through verbatim — the fake_detector and the UI
                    # both parse ISO 8601 directly. Without this, every
                    # RemoteOK posting registered as "no date" → ghost
                    # score capped at Clear, which hid genuinely stale
                    # postings AND made fresh ones look identical.
                    "posted_date": job.get("date", ""),
                }, "remoteok"))

            logger.info("[RemoteOK] %d matching jobs", len(packets))
        except Exception as e:
            logger.warning("[RemoteOK] %s", e)
        return packets

    def fetch_jobicy(self) -> list[SentinelPacket]:
        packets = []
        try:
            resp = self.session.get(
                "https://jobicy.com/api/v2/remote-jobs?count=50&tag=product+manager",
                timeout=15,
            )
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])

            for job in jobs:
                title = job.get("jobTitle", "")
                if not self._matches(title):
                    continue

                desc = job.get("jobDescription", "")
                if desc:
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:12000]

                packets.append(self._make_packet({
                    "title": title,
                    "company": job.get("companyName", ""),
                    "location": job.get("jobGeo", "Remote"),
                    "salary": f"${int(job['annualSalaryMin']):,}-${int(job['annualSalaryMax']):,}" if job.get("annualSalaryMin") and job.get("annualSalaryMax") else None,
                    "description": desc,
                    "technologies": [],
                    "seniority": self._guess_seniority(title),
                    "job_type": job.get("jobType", "full-time"),
                    "remote": "remote",
                    "url": job.get("url", ""),
                    # Jobicy ships `pubDate` as ISO 8601 with TZ.
                    "posted_date": job.get("pubDate", ""),
                }, "jobicy"))

            logger.info("[Jobicy] %d matching jobs", len(packets))
        except Exception as e:
            logger.warning("[Jobicy] %s", e)
        return packets

    # -----------------------------------------------------------------------
    # REMOTIVE (REMOVED — Apr 2026)
    # -----------------------------------------------------------------------
    # Endpoint was https://remotive.com/api/remote-jobs?search={keyword}.
    # Removed because the source consistently returned 0 matches: their
    # server-side `search` does fuzzy expansion (a query for "product
    # manager" returns "Head of Engineering", "AI Video Artist", ...) and
    # the resulting titles never survived our title-keyword filter. Last
    # cycle: 63 jobs retrieved, 0 kept after filter — pure waste of an
    # API call + sleep.
    # Listed here as a comment so a future maintainer doesn't think the
    # deletion was accidental. If Remotive's PM inventory ever picks up
    # again, the recipe was: iterate first 3 role_keywords, GET the API
    # with `search=<kw>` and `limit=40`, stuff each `jobs[]` row into a
    # remotive packet via `_make_packet`. Keep `_sleep()` between calls.
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # MAIN RUNNER
    # -----------------------------------------------------------------------
    def run(self, tiers: tuple = ("fast", "slow")) -> list[SentinelPacket]:
        """Fetch from configured sources.

        `tiers`:
          - "fast": pure-HTTP sources only. Greenhouse / Lever / Ashby
            ATS APIs, Amazon JSON, Google HTML (single page, robots-
            respectful), Workday tenants (Nvidia + Adobe), RemoteOK and
            Jobicy JSON. One full cycle takes a couple of minutes
            depending on tenant count.
          - "slow": historical Playwright SPA-scraping tier. Removed
            before public release because every target (Apple, Meta,
            Microsoft, etc.) explicitly prohibits automated access in
            its TOS / robots.txt. The `tiers` parameter still accepts
            "slow" for API stability but it's a documented no-op —
            `del run_slow` at the end of run() acknowledges this.

        Default is both tiers for backward compat, but callers should
        pass an explicit tuple. Unknown tier names are ignored.
        """
        all_packets = []
        run_fast = "fast" in tiers
        run_slow = "slow" in tiers

        # Per-source packet tally so the Brief tab can show where jobs
        # actually came from, and so a silent scraper regression (e.g.
        # Meta's DOM changed and now returns zero cards) is visible
        # instead of hidden behind the aggregated total. Populated as we
        # extend all_packets below. Also tracks ERROR_LOG packets so a
        # source that only emits errors surfaces as "0 hits, 3 errors".
        source_stats: dict[str, dict[str, int]] = {}

        def _track(label: str, before: int):
            after = len(all_packets)
            added = all_packets[before:after]
            jobs = sum(1 for p in added if p.payload_type in (PayloadType.JSON_JOB, PayloadType.RAW_HTML))
            errs = sum(1 for p in added if p.payload_type == PayloadType.ERROR_LOG)
            if label in source_stats:
                source_stats[label]["jobs"] += jobs
                source_stats[label]["errors"] += errs
            else:
                source_stats[label] = {"jobs": jobs, "errors": errs}

        # ── FAST TIER ────────────────────────────────────────────────
        # Pure HTTP, runs on every pipeline cycle. Cheap and broadly
        # reliable. Total cost: ~30-60s wall-clock across all tenants.
        if run_fast:
            # 1. Greenhouse companies (fastest, pure JSON)
            logger.info("=== GREENHOUSE APIs (%d companies) ===", len(self.greenhouse))
            for company in self.greenhouse:
                before = len(all_packets)
                pkts = self.fetch_greenhouse(company)
                all_packets.extend(pkts)
                _track(f"greenhouse:{company}", before)
                self._sleep()

            # 2. Lever companies
            logger.info("=== LEVER APIs (%d companies) ===", len(self.lever))
            for company in self.lever:
                before = len(all_packets)
                pkts = self.fetch_lever(company)
                all_packets.extend(pkts)
                _track(f"lever:{company}", before)
                self._sleep()

            # 3. Ashby companies
            logger.info("=== ASHBY APIs (%d companies) ===", len(self.ashby))
            for display_name, slug in self.ashby:
                before = len(all_packets)
                pkts = self.fetch_ashby(display_name, slug)
                all_packets.extend(pkts)
                _track(f"ashby:{slug}", before)
                self._sleep()

            # 4. Direct JSON/HTML APIs (Amazon JSON, Google HTML).
            if self.enable_amazon:
                logger.info("=== AMAZON JOBS API ===")
                before = len(all_packets)
                all_packets.extend(self.fetch_amazon())
                _track("amazon", before)

            if self.enable_google:
                logger.info("=== GOOGLE CAREERS ===")
                before = len(all_packets)
                all_packets.extend(self.fetch_google())
                _track("google", before)

            # 4a. Workday tenants (Nvidia, Adobe, Intel). One generic
            # JSON-API scraper handles all three via the standardized
            # /wday/cxs/ endpoint. Runs in the FAST tier — these are
            # plain HTTPS POSTs, no Playwright. Salesforce + IBM +
            # Cisco used to live here too but their endpoints drifted
            # (see the WORKDAY_TENANTS comment for details).
            workday_runs = [
                (cfg, tenant, region, board, display)
                for cfg, tenant, region, board, display in WORKDAY_TENANTS
                if self.workday_enabled.get(cfg)
            ]
            if workday_runs:
                logger.info("=== WORKDAY (%d tenants) ===", len(workday_runs))
                for cfg, tenant, region, board, display in workday_runs:
                    if self._is_in_cooldown("workday", tenant):
                        logger.info("[Workday:%s] skipped (dead-slug cooldown)", tenant)
                        continue
                    before = len(all_packets)
                    try:
                        all_packets.extend(self.fetch_workday(tenant, region, board, display))
                    except Exception as e:
                        logger.warning("[Workday:%s] crashed: %s", tenant, e)
                    _track(f"workday:{tenant}", before)
                    self._sleep()

            # 5. Free job board APIs. Remotive removed Apr 2026 — see
            # the comment on the deleted fetch_remotive() block; PM
            # inventory was 0% match-rate so the call was pure cost.
            logger.info("=== FREE JOB BOARDS ===")
            before = len(all_packets)
            all_packets.extend(self.fetch_remoteok())
            _track("remoteok", before)
            self._sleep()
            before = len(all_packets)
            all_packets.extend(self.fetch_jobicy())
            _track("jobicy", before)

        # NOTE: SLOW TIER (Playwright SPA scrapers for Apple/Meta/Microsoft)
        # was removed before public release. Each company's TOS or
        # robots.txt prohibits automated access; shipping the code in
        # a public repo would imply we'd built an evasion path. The
        # `run_slow` flag is still on the run() signature so the
        # /api/run-scraper endpoint stays working — it just becomes a
        # no-op tier that returns no new packets.
        del run_slow  # unused — kept on signature for API stability

        # Expose for orchestrator -> /api/status. Sort descending by job
        # count so the Brief tab highlights the productive sources first.
        self.last_source_stats = dict(sorted(
            source_stats.items(), key=lambda kv: -kv[1]["jobs"]))

        # Summary
        jobs = sum(1 for p in all_packets if p.payload_type == PayloadType.JSON_JOB)
        html = sum(1 for p in all_packets if p.payload_type == PayloadType.RAW_HTML)
        errs = sum(1 for p in all_packets if p.payload_type == PayloadType.ERROR_LOG)
        logger.info(
            "INGEST COMPLETE: %d pre-parsed jobs, %d HTML cards for LLM, %d errors",
            jobs, html, errs,
        )
        return all_packets
