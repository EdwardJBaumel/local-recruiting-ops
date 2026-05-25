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

logger = logging.getLogger("sentinel.ingest")

# How long a slug stays in cooldown after a 404. Keeps us from hammering
# ATS endpoints that have already told us "no such board" while still
# letting slugs recover when a company re-opens their Greenhouse / Lever
# page. Override via config["ingest"]["dead_slug_cooldown_days"] for tests.
DEAD_SLUG_COOLDOWN_DAYS_DEFAULT = 7

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Safari/605.1.15",
]

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
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
        })

        self.role_keywords = config.get("role_keywords", [
            "product manager",
            "senior product manager",
            "product operations",
            "program manager",
            "product excellence",
            "technical program manager",
        ])

        # Allow config to override which companies to check
        self.greenhouse = config.get("greenhouse_companies", GREENHOUSE_COMPANIES)
        self.lever = config.get("lever_companies", LEVER_COMPANIES)
        self.ashby = config.get("ashby_companies", ASHBY_COMPANIES)
        # Big-tech toggles default OFF so a freshly cloned repo does not
        # auto-scrape anyone. The dashboard's Settings -> Companies panel
        # is where users opt in per source.
        self.enable_apple = config.get("enable_apple", False)
        self.enable_amazon = config.get("enable_amazon", False)
        self.enable_google = config.get("enable_google", False)
        self.enable_meta = config.get("enable_meta", False)
        self.enable_microsoft = config.get("enable_microsoft", False)

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

    def _matches(self, title: str) -> bool:
        """Check if job title matches any of the target role keywords."""
        t = title.lower()
        return any(kw in t for kw in self.role_keywords)

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
        time.sleep(random.uniform(*self.delay_range))

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
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]

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
                    "description": desc[:500],
                    "technologies": [],
                    "seniority": self._guess_seniority(title),
                    "job_type": categories.get("commitment", "full-time"),
                    "remote": "remote" if "remote" in location.lower() else "unknown",
                    "url": job.get("hostedUrl", ""),
                    "posted_date": "",
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

                packets.append(self._make_packet({
                    "title": title,
                    "company": display_name,
                    "location": location or "",
                    "salary": None,
                    "description": "",
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

    # -----------------------------------------------------------------------
    # APPLE / META / MICROSOFT -- shared Playwright path
    # -----------------------------------------------------------------------
    # These sites render their job lists via JavaScript, so plain HTTP
    # GETs return empty skeletons. We use a headless Chromium (Playwright)
    # to load, wait, and extract. Selectors live in agents/tenants.py --
    # edit there, not here, when a site's DOM changes.
    # -----------------------------------------------------------------------
    def _fetch_via_playwright(self, tenant_name: str) -> list[SentinelPacket]:
        """Run the Playwright scraper for one tenant and wrap each
        returned job in a SentinelPacket. Returns [] if Playwright is
        not installed or the tenant is not in tenants.py."""
        from agents import tenants
        from agents.playwright_runner import fetch_spa

        tenant = tenants.get_tenant(tenant_name)
        if tenant is None:
            logger.warning("[%s] no tenant config found", tenant_name)
            return []

        # Keep the top 3 keywords so a Playwright cycle doesn't run for
        # 20+ minutes. Fast tier already covers the broader search space
        # via ATS APIs -- this is for companies that ONLY live in SPAs.
        keywords = self.role_keywords[:3]

        try:
            jobs = fetch_spa(tenant, keywords)
        except Exception as e:
            logger.warning("[%s] Playwright scrape failed: %s", tenant_name, e)
            return [self._error_packet(tenant_name, str(e))]

        packets = []
        for job in jobs:
            title = job.get("title", "")
            if not self._matches(title):
                continue
            packets.append(self._make_packet({
                "title": title,
                "company": job.get("company", tenant.get("display_name", tenant_name)),
                "location": job.get("location", ""),
                "salary": None,
                "description": "",
                "technologies": "",
                "seniority": self._guess_seniority(title),
                "job_type": "full-time",
                "remote": "unknown",
                "url": job.get("url", ""),
                "posted_date": "",
                "team": job.get("team", ""),
            }, tenant_name))

        logger.info("[%s] %d matching jobs (from %d scraped)",
                    tenant_name, len(packets), len(jobs))
        return packets

    def fetch_apple(self) -> list[SentinelPacket]:
        return self._fetch_via_playwright("apple")

    # -----------------------------------------------------------------------
    # AMAZON (public jobs API)
    # -----------------------------------------------------------------------
    def fetch_amazon(self) -> list[SentinelPacket]:
        packets = []
        for keyword in self.role_keywords[:3]:  # Top 3 to avoid too many requests
            url = "https://www.amazon.jobs/en/search.json"
            params = {
                "base_query": keyword,
                "country": "USA",
                "result_limit": 25,
                "sort": "recent",
            }
            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                jobs = data.get("jobs", [])

                for job in jobs:
                    title = job.get("title", "")
                    if not self._matches(title):
                        continue

                    desc = job.get("description", "")
                    if desc:
                        desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]

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

                self._sleep()

            except Exception as e:
                logger.warning("[Amazon] %s", e)
                packets.append(self._error_packet("amazon", str(e)))

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
        packets = []
        for keyword in self.role_keywords[:3]:
            url = "https://www.google.com/about/careers/applications/jobs/results"
            params = {
                "q": keyword,
                "location": "United States",
                "employment_type": "FULL_TIME",
                "hl": "en_US",
                "sort_by": "date",
            }
            try:
                headers = {**self.session.headers, "Accept": "text/html"}
                resp = requests.get(url, params=params, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logger.warning("[Google] HTTP %d for '%s'", resp.status_code, keyword)
                    continue

                # Google careers returns HTML with structured job card data
                # Send as RAW_HTML for LLM parsing
                soup = BeautifulSoup(resp.text, "html.parser")

                # Try to find job listing elements
                job_elements = soup.select("li.lLd3Je") or soup.select("[data-id]") or soup.select("a[href*='jobs/results']")

                kept = 0
                dropped = 0
                for i, el in enumerate(job_elements[:20]):
                    if not self._html_card_matches(el):
                        dropped += 1
                        continue
                    kept += 1
                    packets.append(SentinelPacket(
                        sender=Sender.INGEST,
                        payload_type=PayloadType.RAW_HTML,
                        payload={
                            "html": str(el),
                            "source_url": resp.url,
                            "card_index": i,
                            "_company_hint": "Google",
                        },
                        priority=Priority.MED,
                    ))
                if dropped:
                    logger.info("[Google:%s] kept %d cards, dropped %d off-keyword",
                                keyword, kept, dropped)

                self._sleep()

            except Exception as e:
                logger.warning("[Google] %s", e)
                packets.append(self._error_packet("google", str(e)))

        logger.info("[Google] %d cards extracted", len(packets))
        return packets

    # -----------------------------------------------------------------------
    # META (careers page, HTML)
    # -----------------------------------------------------------------------
    def fetch_meta(self) -> list[SentinelPacket]:
        return self._fetch_via_playwright("meta")

    # -----------------------------------------------------------------------
    # MICROSOFT (careers page, HTML)
    # -----------------------------------------------------------------------
    def fetch_microsoft(self) -> list[SentinelPacket]:
        return self._fetch_via_playwright("microsoft")

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
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]

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
                    desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]

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
                }, "jobicy"))

            logger.info("[Jobicy] %d matching jobs", len(packets))
        except Exception as e:
            logger.warning("[Jobicy] %s", e)
        return packets

    # -----------------------------------------------------------------------
    # REMOTIVE (public JSON API, no auth) - broader stop-gap source (#108)
    # Endpoint: https://remotive.com/api/remote-jobs?search={keyword}
    # Mostly remote/hybrid tech roles. Filters per keyword server-side so
    # we don't pay parsing cost on obvious non-matches.
    # -----------------------------------------------------------------------
    def fetch_remotive(self) -> list[SentinelPacket]:
        packets = []
        # Take the first 3 keywords — Remotive's q= only accepts one at a
        # time and further queries just burn quota. 3 keeps us well
        # inside the 50-req/hour courtesy limit.
        for keyword in self.role_keywords[:3]:
            try:
                resp = self.session.get(
                    "https://remotive.com/api/remote-jobs",
                    params={"search": keyword, "limit": 40},
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.warning("[Remotive] HTTP %d for '%s'", resp.status_code, keyword)
                    continue
                jobs = resp.json().get("jobs", [])

                for job in jobs:
                    title = job.get("title", "")
                    if not self._matches(title):
                        continue

                    desc = job.get("description", "")
                    if desc:
                        desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]

                    salary_raw = (job.get("salary") or "").strip()
                    packets.append(self._make_packet({
                        "title": title,
                        "company": job.get("company_name", ""),
                        "location": job.get("candidate_required_location", "Remote"),
                        "salary": salary_raw or None,
                        "description": desc,
                        "technologies": job.get("tags", []) or [],
                        "seniority": self._guess_seniority(title),
                        "job_type": job.get("job_type", "full-time"),
                        "remote": "remote",
                        "url": job.get("url", ""),
                        "posted_date": (job.get("publication_date", "") or "")[:10],
                    }, "remotive"))

                self._sleep()
            except Exception as e:
                logger.warning("[Remotive] %s", e)

        logger.info("[Remotive] %d matching jobs", len(packets))
        return packets

    # -----------------------------------------------------------------------
    # MAIN RUNNER
    # -----------------------------------------------------------------------
    def run(self, tiers: tuple = ("fast", "slow")) -> list[SentinelPacket]:
        """Fetch from configured sources.

        `tiers`:
          - "fast": pure-HTTP sources. Greenhouse/Lever/Ashby ATS APIs,
            Amazon JSON, Google HTML, RemoteOK/Jobicy/Remotive JSON. One
            full cycle runs in under a minute.
          - "slow": Playwright-driven browser scrapers for SPA careers
            pages (Apple, Meta, Microsoft). Minutes per cycle, needs
            Chromium installed. Triggered via the separate "Run Scraper"
            button, not every pipeline cycle.

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

            # 5. Free job board APIs
            logger.info("=== FREE JOB BOARDS ===")
            before = len(all_packets)
            all_packets.extend(self.fetch_remoteok())
            _track("remoteok", before)
            self._sleep()
            before = len(all_packets)
            all_packets.extend(self.fetch_jobicy())
            _track("jobicy", before)
            self._sleep()
            before = len(all_packets)
            all_packets.extend(self.fetch_remotive())
            _track("remotive", before)

        # ── SLOW TIER ────────────────────────────────────────────────
        # Playwright-driven scrapers for SPA careers pages that block
        # plain HTTP. Minutes per run. Triggered by the "Run Scraper"
        # button, not every pipeline cycle. If Playwright isn't
        # installed the fetchers log and return [] cleanly.
        if run_slow:
            if self.enable_apple:
                logger.info("=== APPLE CAREERS (Playwright) ===")
                before = len(all_packets)
                all_packets.extend(self.fetch_apple())
                _track("apple", before)

            if self.enable_meta:
                logger.info("=== META CAREERS (Playwright) ===")
                before = len(all_packets)
                all_packets.extend(self.fetch_meta())
                _track("meta", before)

            if self.enable_microsoft:
                logger.info("=== MICROSOFT CAREERS (Playwright) ===")
                before = len(all_packets)
                all_packets.extend(self.fetch_microsoft())
                _track("microsoft", before)

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
