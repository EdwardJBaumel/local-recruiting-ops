"""
ORCHESTRATOR: Full pipeline with market intel, fit-gap, funnel tracking,
decision logging, and digest delivery.
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.protocol import PayloadType
from agents.ingest import IngestAgent
from agents.parse import ParseAgent
from agents.qa import QAAgent
from agents.match import MatchAgent
from agents.analyzer import FitGapAnalyzer
from agents.fakejob import FakeJobDetector
from agents.resume import ResumeGenerator
from core import resume_store, resume_profile as resume_profile_module, user_store
from core.io_safe import write_text_atomic
from tracker import ApplicationTracker
from digest import DigestGenerator

logger = logging.getLogger("lantern.orchestrator")


def _resolve_manual_mode(config: dict, env=None) -> bool:
    """Pure helper so the manual-vs-auto decision is unit-testable without
    spinning up the full Orchestrator (agents, embeddings, data dir).
    Resolution order:
      a. env LANTERN_MANUAL_MODE ("1"/"true"/"yes"/"on" → manual);
         SENTINEL_MANUAL_MODE is honored as a legacy alias
      b. config.pipeline.auto_start == false → manual
      c. default: auto-schedule on
    """
    if env is None:
        env = os.environ
    raw = (env.get("LANTERN_MANUAL_MODE") or env.get("SENTINEL_MANUAL_MODE") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    pipeline_cfg = (config or {}).get("pipeline", {}) or {}
    if "auto_start" in pipeline_cfg:
        return not bool(pipeline_cfg.get("auto_start"))
    return False


class Orchestrator:
    def __init__(self, config: dict):
        self.config = config
        self.cycle_interval = config.get("cycle_interval_minutes", 60)
        self.max_cycles = config.get("max_cycles", 0)
        self.data_dir = Path(config.get("data_dir", "data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Resolve the effective profile text: an uploaded resume
        # (data/resume/) always wins over config.match.profile_text so the
        # Settings UI upload is authoritative.
        match_config = dict(config.get("match", {}))
        # Preferences (location filter, salary weight) are stored at the
        # top level of config so the UI can edit them independently of
        # match-specific fields. Forward them into the match agent.
        match_config["preferences"] = config.get("preferences", {})
        # Ghost-job aggressiveness lives at the config root so the UI can
        # edit it without reaching into match-specific fields.
        match_config["fake_detection"] = config.get("fake_detection", {}) or {}
        # Data dir forwarded so the MatchAgent can instantiate the
        # FeedbackLearner and persist its embedding cache alongside the
        # match registry.
        match_config["data_dir"] = self.data_dir
        self._config_profile_fallback = match_config.get("profile_text", "")
        effective_profile = self._effective_profile_text()
        if effective_profile:
            match_config["profile_text"] = effective_profile
            logger.info("Using candidate profile (%d chars).", len(effective_profile))
        elif self._config_profile_fallback:
            logger.info("No resume uploaded; using config.match.profile_text fallback.")
        else:
            logger.warning("No candidate profile available - matching will score everything 0.")

        # Pass the structured profile (if one has been parsed) so the match
        # agent can compute dimensional sub-scores alongside the base score.
        struct = resume_profile_module.get_cached_profile(self.data_dir)
        if struct and not struct.get("error"):
            match_config["profile_struct"] = struct
        # Forward role_keywords into match so the TitleScorer can boost
        # roles whose title matches a keyword the user explicitly listed.
        # We read from ingest because that's where the human-curated list
        # lives; matching and ingest share the same intent here.
        match_config["role_keywords"] = list(
            (config.get("ingest") or {}).get("role_keywords") or []
        )
        # Blocked title keywords (wrong-discipline markers) live under
        # preferences so the user edits them next to the location
        # block-list. Forward them so the TitleScorer can penalise a
        # title the user excluded — the same set the ingest stage skips
        # at scrape time.
        match_config["blocked_title_keywords"] = list(
            (config.get("preferences") or {}).get("blocked_title_keywords") or []
        )

        # Core agents
        # Forward preferences into the ingest config so the title-
        # trapdoor pre-filter (in agents.ingest._matches) can read
        # `years_experience` + `trapdoor_enabled`. Without this,
        # senior-title roles that the match-time trapdoor will
        # reject still get scraped + parsed, costing one LLM call
        # apiece for nothing.
        ingest_config = dict(config.get("ingest", {}))
        ingest_config["preferences"] = config.get("preferences", {})
        self.ingest = IngestAgent(ingest_config, data_dir=self.data_dir)
        self.parse = ParseAgent(config.get("parse", {}))
        self.qa = QAAgent(config.get("qa", {}))
        self.match = MatchAgent(match_config)

        # New features
        # data_dir is passed so the analyzer can append STAR+R bullets
        # to <data_dir>/story_bank.md after every match analysis.
        self.analyzer = FitGapAnalyzer(match_config, data_dir=self.data_dir)
        self.fake_detector = FakeJobDetector(config.get("qa", {}))
        self.resume_gen = ResumeGenerator({
            **config.get("resume", {}),
            "output_dir": str(self.data_dir / "resumes"),
        })
        self.tracker = ApplicationTracker(str(self.data_dir))
        self.digest = DigestGenerator({
            # Fallback follows the consolidated-model plan: qwen3:14b is
            # the resident "everything not parse" model. Previous code
            # fell back to "gemma4:e4b" which is a hallucinated tag
            # (gemma4 doesn't ship that variant in the Ollama library) —
            # only surfaced on a config with no digest.model AND no
            # parse.model, but worth fixing so a wiped config still
            # boots cleanly.
            "model": config.get("digest", {}).get("model") or config.get("parse", {}).get("model", "qwen3:14b"),
            "discord_webhook": config.get("discord_webhook", ""),
            "email": config.get("email", {}),
            "data_dir": str(self.data_dir),
        })

        # Digest frequency: every N cycles (default every 6 = ~6 hours at 60min intervals)
        self.digest_every = config.get("digest_every_n_cycles", 6)

        # Dedup
        self.seen_jobs_file = self.data_dir / "seen_jobs.json"
        self.seen_jobs = self._load_seen()
        # URL-level dedupe. Cross-cycle set of normalised URLs we have
        # already pulled through the pipeline. Filtering at ingest means
        # we never pay parse/match/analyze cost on a URL we've already
        # processed - the single biggest lever for local GPU time.
        self.seen_urls_file = self.data_dir / "seen_urls.json"
        self.seen_urls = self._load_seen_urls()
        # Per-cycle URL dedupe stats, surfaced on the dashboard so the
        # user can see "skipped N already-seen URLs" after each cycle.
        self.last_url_dedupe_stats: dict = {"input": 0, "skipped": 0, "new": 0}

        # Market intel accumulator
        self.market_data_file = self.data_dir / "market_intel.json"

        self.cycle_count = 0

        # Live progress snapshot for /api/status. Updated at each phase
        # boundary so the dashboard can render "Stage: Matching 12/80"
        # and rolling counts while a cycle is in flight.
        # Pre-resolve the model used at each stage so the UI can render
        # "Parsing · qwen3:8b" next to the live stage label. Falls back
        # to a sensible default when the config doesn't pin a model. If
        # sentence-transformers is installed, match uses embeddings and
        # the model field shows that fact; the LLM model stays in
        # `match_llm_fallback` so the UI can also say "will fall back to
        # qwen3:8b if embeddings unavailable".
        parse_model = (config.get("parse", {}) or {}).get("model") or "qwen3:8b"
        match_llm = (config.get("match", {}) or {}).get("model") or "qwen3:14b"
        # Defaults aligned with core/llm.DEFAULT_MODELS + the model-swap
        # commit (analyze/digest/cover_letter all consolidated onto
        # qwen3:14b so only two distinct models are resident: qwen3:8b
        # for parse + always-on, qwen3:14b for everything else).
        analyze_model = config.get("analyze_model") or (config.get("analyze", {}) or {}).get("model") or "qwen3:14b"
        digest_model = config.get("digest_model") or (config.get("digest", {}) or {}).get("model") or "qwen3:14b"
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
            match_model_label = f"embeddings ({(config.get('match', {}) or {}).get('embed_model') or 'BAAI/bge-m3'})"
        except ImportError:
            match_model_label = match_llm

        self.progress = {
            "stage": "idle",
            "stage_index": 0,
            "stage_count": 8,
            "stage_label": "Waiting",
            "cycle": 0,
            "started_at": None,
            # Keyed by stage so the UI can pull the right model per phase.
            # `match_llm_fallback` is separate so the UI can surface the
            # fallback clearly ("embeddings, will fall back to qwen3:8b").
            "models": {
                "ingest": None,  # deterministic HTTP, no model
                "parse": parse_model,
                "qa": parse_model,  # QA reuses the parse model
                "fake_detect": None,  # deterministic ghost signals
                "match": match_model_label,
                "match_llm_fallback": match_llm,
                "analyze": analyze_model,
                "tracking": None,
                "digest": digest_model,
            },
            "counts": {
                "ingested": 0, "parsed": 0, "qa_pass": 0, "qa_fail": 0,
                "fake_blocked": 0, "new_jobs": 0, "matches": 0,
                "maybes": 0,
                "fit_gaps": 0, "resumes": 0,
                # Scoring counter - ticks once per posting inside the match
                # phase so the UI can render "Scoring X/Y" live rather than
                # waiting for the whole phase to finish.
                "scored": 0, "scored_total": 0,
            },
        }

    def _set_stage(self, key: str, label: str, index: int):
        """Update the live progress dict. Cheap enough to call per phase."""
        self.progress["stage"] = key
        self.progress["stage_label"] = label
        self.progress["stage_index"] = index

    def _load_seen(self) -> set:
        if self.seen_jobs_file.exists():
            try:
                return set(json.loads(self.seen_jobs_file.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen(self):
        write_text_atomic(self.seen_jobs_file, json.dumps(list(self.seen_jobs)))

    def _load_seen_urls(self) -> set:
        if self.seen_urls_file.exists():
            try:
                return set(json.loads(self.seen_urls_file.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen_urls(self):
        # Cap on disk so this file can't grow without bound over months.
        # A set-of-strings JSON with ~50k entries is still only ~3-5 MB
        # but the parse cost starts to matter; we drop oldest entries
        # when we cross the cap. Simple LRU isn't worth the complexity -
        # "drop 20% when over 50k" is good enough for this scale.
        urls = self.seen_urls
        cap = 50000
        if len(urls) > cap:
            keep = list(urls)[-int(cap * 0.8):]
            urls = set(keep)
            self.seen_urls = urls
            logger.info("seen_urls: trimmed to %d entries", len(urls))
        write_text_atomic(self.seen_urls_file, json.dumps(list(urls)))

    @staticmethod
    def _normalise_url(raw: str) -> str:
        """Canonicalise a URL for cross-cycle dedupe. We strip:
          - tracking query params (utm_, gh_src, source, ref, etc.)
          - fragments (#apply)
          - trailing slashes and default ports
        Two URLs that point at the same job posting on the same board
        should collapse to the same key regardless of the clickpath."""
        if not raw:
            return ""
        s = raw.strip().lower()
        if not s:
            return ""
        try:
            from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        except Exception:
            return s
        try:
            parts = urlsplit(s)
        except Exception:
            return s
        # Drop tracking params; keep functional ones (the ATS job id is
        # typically in the path, not the query, so this is safe for all
        # the boards we ingest).
        drop_prefixes = ("utm_", "gh_", "mc_", "fbclid", "gclid", "ref", "source", "s_")
        qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
              if not any(k.startswith(p) for p in drop_prefixes)]
        query = urlencode(sorted(qs))
        netloc = parts.netloc
        if netloc.endswith(":80") or netloc.endswith(":443"):
            netloc = netloc.rsplit(":", 1)[0]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme or "https", netloc, path, query, ""))

    @staticmethod
    def _packet_url(pkt) -> str:
        """Pull whatever URL-like field the ingester attached. HTML packets
        carry `_source_url` (the page we scraped); JSON packets put the
        job link on the payload's `url` field."""
        payload = getattr(pkt, "payload", None) or {}
        if isinstance(payload, dict):
            return payload.get("url") or payload.get("_source_url") or ""
        return ""

    def _job_key(self, p):
        return f"{(p.get('title') or '').strip().lower()}||{(p.get('company') or '').strip().lower()}"

    def _save_matches(self, matches, cycle):
        out = self.data_dir / "matches" / f"cycle_{cycle:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        data = sorted(
            [m.payload for m in matches],
            key=lambda j: (j.get("_match_score", 0), j.get("posted_date", "") or ""),
            reverse=True,
        )
        write_text_atomic(out, json.dumps(data, indent=2))
        logger.info("Saved %d matches to %s", len(data), out)

    def _save_fit_gaps(self, reports, cycle):
        out = self.data_dir / "fit_gaps" / f"cycle_{cycle:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(out, json.dumps(reports, indent=2))
        logger.info("Saved %d fit-gap reports to %s", len(reports), out)

    def _save_market_intel(self, all_jobs, cycle):
        """Accumulate market intelligence data."""
        intel = {"timestamp": datetime.now(timezone.utc).isoformat(), "cycle": cycle}

        # Company volume
        companies = {}
        for pkt in all_jobs:
            p = pkt.payload
            co = p.get("company", "Unknown")
            companies[co] = companies.get(co, 0) + 1
        intel["company_volume"] = dict(sorted(companies.items(), key=lambda x: -x[1])[:20])

        # Source breakdown
        sources = {}
        for pkt in all_jobs:
            src = pkt.payload.get("_source", "unknown").split(":")[0]
            sources[src] = sources.get(src, 0) + 1
        intel["source_breakdown"] = sources

        # Salary data. The regex extractor stores recovered bands as a
        # structured dict ({min, max, ...}); the string adapters
        # (RemoteOK, Jobicy) store a "$lo-$hi" string. Normalise a dict
        # band to a "$"-string here so it isn't silently dropped from
        # the market histogram — the consumer only keeps "$"-strings.
        salaries = []
        for pkt in all_jobs:
            sal = pkt.payload.get("salary")
            if isinstance(sal, dict):
                bounds = [str(v) for v in (sal.get("min"), sal.get("max")) if v]
                sal = "$" + "-$".join(bounds) if bounds else None
            if sal and "$" in str(sal):
                salaries.append(sal)
        intel["salary_samples"] = salaries[:50]

        # Remote/location
        remote_counts = {"remote": 0, "hybrid": 0, "onsite": 0, "unknown": 0}
        for pkt in all_jobs:
            r = pkt.payload.get("remote", "unknown")
            remote_counts[r] = remote_counts.get(r, 0) + 1
        intel["work_model"] = remote_counts

        # Seniority
        seniority = {}
        for pkt in all_jobs:
            s = pkt.payload.get("seniority", "unknown")
            seniority[s] = seniority.get(s, 0) + 1
        intel["seniority_distribution"] = seniority

        # Append to history
        history = []
        if self.market_data_file.exists():
            try:
                history = json.loads(self.market_data_file.read_text())
            except Exception:
                pass
        history.append(intel)
        # Keep last 50 cycles
        history = history[-50:]
        write_text_atomic(self.market_data_file, json.dumps(history, indent=2))

    def _save_all_parsed(self, jobs, cycle):
        out = self.data_dir / "parsed" / f"cycle_{cycle:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(out, json.dumps([j.payload for j in jobs], indent=2))

    def _effective_profile_text(self) -> str:
        """Decide what profile_text to feed the match agent.

        Preference order:
          1. Structured profile rendered by resume_profile (best signal -
             it is the LLM-parsed summary, skills, targets).
          2. Raw parsed resume text + notes from resume_store.
          3. config.match.profile_text fallback (manually written).
        Returns empty string when nothing is available."""
        cached = resume_profile_module.get_cached_profile(self.data_dir)
        if cached and not cached.get("error"):
            rendered = resume_profile_module.profile_to_text(cached)
            if rendered:
                return rendered
        raw = resume_store.get_profile_text(self.data_dir)
        if raw:
            return raw
        return self._config_profile_fallback or ""

    def _refresh_profile(self):
        """Pick up resume and preference changes made via the Settings UI
        without restarting the server. Called once per cycle."""
        effective = self._effective_profile_text()
        # MatchAgent.set_profile short-circuits when the text is unchanged,
        # so this is cheap to call every cycle even with big resumes.
        self.match.set_profile(effective)
        # Also refresh the structured profile. Missing cache -> None, which
        # tells MatchAgent to skip dimensional scoring.
        struct = resume_profile_module.get_cached_profile(self.data_dir)
        if struct and struct.get("error"):
            struct = None
        if hasattr(self.match, "set_profile_struct"):
            self.match.set_profile_struct(struct)
        self.analyzer.set_profile(effective)

        # Preferences may have been edited via POST /api/config. Re-read
        # config.json rather than trusting the in-memory copy because the
        # server writes the canonical version to disk.
        try:
            cfg_path = Path("config.json")
            if cfg_path.exists():
                on_disk = json.loads(cfg_path.read_text())
                self.match.set_preferences(on_disk.get("preferences", {}) or {})
                self.config["preferences"] = on_disk.get("preferences", {})
                # Cycle interval hot-reload: picked up on the NEXT sleep
                # (the current sleep is already scheduled). Clamp to a
                # sane range matching server.py.
                try:
                    mins = int(on_disk.get("cycle_interval_minutes", self.cycle_interval))
                    new_interval = max(5, min(240, mins))
                    if new_interval != self.cycle_interval:
                        logger.info("Cycle interval updated: %d -> %d minutes", self.cycle_interval, new_interval)
                        self.cycle_interval = new_interval
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug("Could not refresh preferences: %s", e)

    def run_cycle(self, tiers: tuple = ("fast",)) -> dict:
        """Run one cycle. `tiers` selects which ingest sources fire:

          - ("fast",): ATS + direct HTTP APIs. Default for Run Pipeline.

        Match / registry / analysis stages always run against whatever
        packets the ingest tier produced."""
        try:
            return self._run_cycle_inner(tiers)
        except Exception:
            # Always force the live stage back to idle on the way out,
            # even when an inner stage raised. Without this finally,
            # a crashed cycle leaves progress.stage_label stuck on
            # "MATCHING" / "ANALYZING" / etc. forever, which used to
            # lock the reset endpoint AND made the dashboard's Header
            # badge show a perpetual "Running" state. The
            # _pipeline_state flag in server.py is reset by its own
            # finally block, but progress.stage_label belongs to the
            # orchestrator and only it can clean it up.
            self._set_stage("idle", "Waiting", 0)
            raise

    def _run_cycle_inner(self, tiers: tuple) -> dict:
        """Actual cycle body — the try/finally above guarantees stage
        is reset to idle even if any of these stages raise."""
        self.cycle_count += 1
        cycle = self.cycle_count
        cycle_started = time.time()
        run_mode = "scraper" if tiers == ("slow",) else ("full" if "slow" in tiers else "pipeline")
        logger.info("=" * 60)
        logger.info("CYCLE %d STARTING (mode=%s tiers=%s) at %s",
                    cycle, run_mode, ",".join(tiers), datetime.now(timezone.utc).isoformat())
        logger.info("=" * 60)

        # Pick up any resume/notes changes the user made via the UI.
        self._refresh_profile()

        stats = {"cycle": cycle, "mode": run_mode, "tiers": list(tiers),
                 "ingested": 0, "parsed": 0, "qa_pass": 0,
                 "qa_fail": 0, "fake_blocked": 0, "new_jobs": 0, "matches": 0,
                 "fit_gaps": 0, "resumes": 0, "duration_seconds": 0.0}

        # Reset the live progress snapshot for this cycle. The API reads
        # this from /api/status so the dashboard can paint progress while
        # we're still running rather than only at the end.
        self.progress["cycle"] = cycle
        self.progress["mode"] = run_mode
        self.progress["started_at"] = datetime.now(timezone.utc).isoformat()
        self.progress["counts"] = {k: 0 for k in self.progress["counts"]}

        # 1. INGEST
        ingest_label = "Scraping SPA careers pages" if run_mode == "scraper" else "Scraping job boards"
        self._set_stage("ingest", ingest_label, 1)
        logger.info("[1/8] INGEST (tiers=%s)", ",".join(tiers))
        # Tracking the scrape/pipeline split separately so the Brief tab
        # can answer "is ingest slow or is the LLM slow?" at a glance.
        # ingest_started is the scrape-only bookend; the pipeline
        # (parse+qa+match+analyze+resume) runs from here until the end
        # of this cycle. We stash both on stats so the history JSON
        # preserves them forever.
        ingest_started = time.time()
        raw_packets = self.ingest.run(tiers=tiers)
        raw_total = sum(1 for p in raw_packets if p.payload_type in (PayloadType.RAW_HTML, PayloadType.JSON_JOB))

        # Surface this cycle's ATS 404s in the log. IngestAgent itself
        # owns `data/dead_slugs.json` now — it persists the rolling
        # history (with per-slug cooldown timestamps) and clears a slug
        # from it as soon as the endpoint comes back alive. We only read
        # the per-cycle snapshot here for the banner.
        try:
            dead = list(getattr(self.ingest, "dead_slugs", []) or [])
            if dead:
                logger.warning("INGEST: %d ATS slug(s) returned 404: %s",
                               len(dead),
                               ", ".join(f"{d['source']}:{d['slug']}" for d in dead[:10]))
            stats["dead_slugs"] = dead
            self.ingest.dead_slugs = []
        except Exception as e:
            logger.debug("dead_slugs summary failed: %s", e)

        # Persist per-source ingest tally (#89) so the Brief tab can
        # show "greenhouse:stripe → 3 jobs, meta → 0 jobs (5 errors)"
        # without reaching into the live orchestrator. Rewrites fully
        # each cycle so the snapshot always reflects the latest run.
        try:
            sources = dict(getattr(self.ingest, "last_source_stats", {}) or {})
            (self.data_dir / "ingest_sources.json").write_text(
                json.dumps({
                    "cycle": cycle,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "sources": sources,
                }, indent=2),
                encoding="utf-8",
            )
            stats["ingest_sources"] = sources
        except Exception as e:
            logger.debug("ingest_sources persist failed: %s", e)

        # 1b. URL DEDUPE (cross-cycle). Skip anything whose URL we have
        # already pushed through the pipeline. Cheapest possible filter,
        # and it shortcuts the entire parse+match+analyze chain for
        # re-encountered postings.
        #
        # IMPORTANT: we only block RAW_HTML packets here. JSON_JOB packets
        # from the ATS APIs are pre-parsed and re-scoring them each cycle
        # costs ~2 ms per row in embed mode. Blocking them was why a
        # healthy ingest (411 ATS rows) produced an empty match registry:
        # every previously-seen ATS URL was nuked before match ran.
        # Re-flowing ATS JSON lets the registry upsert idempotently (keyed
        # on company||title||location) and refreshes match state against
        # the current profile/threshold.
        url_in = len(raw_packets)
        url_skipped = 0
        filtered_packets = []
        for pkt in raw_packets:
            if pkt.payload_type == PayloadType.JSON_JOB:
                # Record the URL so the HTML-dedupe side stays consistent,
                # but always keep the packet.
                url = self._normalise_url(self._packet_url(pkt))
                if url:
                    self.seen_urls.add(url)
                filtered_packets.append(pkt)
                continue
            url = self._normalise_url(self._packet_url(pkt))
            # No URL means we can't dedupe on this axis; pass through and
            # let the title||company seen_jobs set handle it downstream.
            if not url:
                filtered_packets.append(pkt)
                continue
            if url in self.seen_urls:
                url_skipped += 1
                continue
            self.seen_urls.add(url)
            filtered_packets.append(pkt)
        raw_packets = filtered_packets
        self.last_url_dedupe_stats = {
            "input": url_in,
            "skipped": url_skipped,
            "new": url_in - url_skipped,
            "registry_size": len(self.seen_urls),
        }
        stats["url_dedupe"] = dict(self.last_url_dedupe_stats)
        if url_skipped:
            logger.info("URL dedupe: skipped %d already-seen URL(s); %d remain.",
                        url_skipped, len(raw_packets))
        # Persist immediately so a crash mid-cycle doesn't re-pay the
        # scrape cost for these URLs on the next run.
        try:
            self._save_seen_urls()
        except Exception as e:
            logger.warning("Could not persist seen_urls: %s", e)

        stats["ingested"] = sum(1 for p in raw_packets if p.payload_type in (PayloadType.RAW_HTML, PayloadType.JSON_JOB))
        self.progress["counts"]["ingested"] = stats["ingested"]

        # Capture scrape duration before we branch on empty-cycle. Even
        # when the pipeline exits early (nothing new to parse) we still
        # want the ingest tally so cycle_times.json reflects reality.
        stats["ingest_seconds"] = round(time.time() - ingest_started, 2)

        if stats["ingested"] == 0:
            if raw_total > 0:
                logger.info("All %d ingested URL(s) already processed; nothing new this cycle.", raw_total)
            else:
                logger.warning("No jobs ingested. Skipping pipeline.")
            self._set_stage("idle", "Waiting", 0)
            return stats

        # Pipeline bookend. Everything from here on is parse+qa+match+
        # analyze+resume+tracker — the LLM-heavy side of the cycle.
        pipeline_started = time.time()

        # =====================================================================
        # TWO-PASS PIPELINE: fast pass (pre-parsed JSON) → PARSE → slow pass
        # =====================================================================
        # The old structure ran PARSE → QA → FAKE → DEDUPE → MATCH on the
        # union of every packet, which meant the user waited 8+ minutes for
        # 53 HTML cards to be LLM-parsed before the FIRST match scored. That
        # was 95% of cycle latency burned on 5% of the data.
        #
        # The fast pass scores the ~1000 already-structured JSON_JOB packets
        # (Greenhouse / Lever / Ashby / Workday / Amazon / RemoteOK / Jobicy)
        # IMMEDIATELY — no LLM-parse cost — so the registry starts filling
        # within ~30s of INGEST finishing. The slow pass runs PARSE on the
        # ~50 RAW_HTML cards (Google) and matches them after, appending to
        # the registry.
        #
        # Trade-offs vs the old union flow:
        #   - Cross-ATS dedupe now runs per-pass instead of across the union.
        #     Misses the rare case of a Google HTML card being a duplicate
        #     of a Greenhouse JSON listing for the same role. <5% of merges
        #     historically; acceptable.
        #   - QA / FAKE / DEDUPE / seen-jobs / market_intel all need to
        #     accumulate across both passes. The helper below keeps each
        #     pass self-contained and the merge-at-end logic explicit.
        # =====================================================================
        from core import dedupe as dedupe_mod

        html_packets = [p for p in raw_packets if p.payload_type == PayloadType.RAW_HTML]
        json_packets = [p for p in raw_packets if p.payload_type == PayloadType.JSON_JOB]

        # Per-pass accumulators that merge into stats / persisted files at
        # the end. Keeping them as locals (instead of mutating stats inline)
        # makes both passes parallel-shaped and easier to reason about.
        all_clean_packets: list = []      # for market_intel + save_all_parsed
        all_new_packets: list = []        # union of new_packets across passes
        all_fake_reports: list = []       # appended to fake_jobs.json once
        total_qa_pass = 0
        total_qa_fail = 0
        total_fake_blocked = 0
        total_newly_seen = 0
        merged_dedupe_stats: dict = {"merged": 0, "unique": 0, "cross_listed_pct": 0.0}

        def _qa_fake_dedupe_pass(batch_packets: list, batch_label: str) -> tuple[list, list]:
            """Run QA → FAKE → DEDUPE → seen-key tracking on one batch.

            Returns (clean_packets, new_packets). Mutates the outer
            accumulators (total_qa_pass / total_fake_blocked / etc.) in
            place — easier than threading a stats dict through.
            """
            nonlocal total_qa_pass, total_qa_fail, total_fake_blocked, total_newly_seen
            if not batch_packets:
                return [], []
            # QA
            valid_pkts, err_pkts = self.qa.run(batch_packets)
            total_qa_pass += len(valid_pkts)
            total_qa_fail += len(err_pkts)
            # FAKE
            clean_pkts, reports = self.fake_detector.filter_packets(valid_pkts)
            total_fake_blocked += sum(1 for f in reports if f["action"] == "removed")
            all_fake_reports.extend(reports)
            # DEDUPE (within this batch only — see trade-off note above)
            clean_pkts, ddstats = dedupe_mod.dedupe_packets(clean_pkts)
            # Merge dedupe counters across passes
            merged_dedupe_stats["merged"] += ddstats.get("merged", 0)
            merged_dedupe_stats["unique"] += ddstats.get("unique", 0)
            if ddstats.get("merged"):
                logger.info(
                    "[%s] Cross-ATS dedupe merged %d into %d unique (%.1f%% cross-listed).",
                    batch_label, ddstats["merged"], ddstats["unique"],
                    ddstats.get("cross_listed_pct", 0.0),
                )
            # Seen-key accounting (cross-cycle "new vs refresh" stat)
            new_pkts = list(clean_pkts)
            for pkt in clean_pkts:
                key = self._job_key(pkt.payload)
                if key and key != "||" and key not in self.seen_jobs:
                    self.seen_jobs.add(key)
                    total_newly_seen += 1
            return clean_pkts, new_pkts

        # =====================================================================
        # MATCH agent + registry setup (shared across both passes)
        # =====================================================================
        # Done once before pass 1 so the embedding model, company signals,
        # and feedback learner are warm. Both pass-1 and pass-2 calls to
        # match.run() reuse the same agent state.
        #
        # The match agent already does on_scored callbacks per-row → live
        # progress + per-row registry flush. We keep the same callback
        # plumbing across passes so the UI sees a continuous stream of
        # match rows tick in (pass 1 fills ~95% of the registry; pass 2
        # tops up with HTML-parsed cards).
        # =====================================================================
        try:
            signals = self.tracker.company_signals()
            self.match.set_company_signals(signals)
            if signals:
                logger.info(
                    "MatchAgent primed with %d company signals (range %.3f..%.3f)",
                    len(signals), min(signals.values()), max(signals.values()),
                )
        except Exception as e:
            logger.debug("set_company_signals skipped: %s", e)

        _reg = None
        _profile_version = None
        try:
            from core.match_registry import get_registry
            import hashlib
            profile_text = self._effective_profile_text() or ""
            _profile_version = hashlib.sha1(profile_text.encode("utf-8")).hexdigest()[:12] if profile_text else None
            _reg = get_registry(self.data_dir)
            _reg.reload()
        except Exception as e:
            logger.warning("registry prep for incremental flush failed: %s", e)

        if _reg is not None:
            try:
                registry_entries = _reg.entries_by_key()
                fb_stats = self.match.refresh_feedback(registry_entries)
                if fb_stats:
                    logger.info(
                        "Feedback learner: +%d embeddings, -%d stale, %d cached",
                        fb_stats.get("added", 0),
                        fb_stats.get("dropped", 0),
                        fb_stats.get("kept", 0),
                    )
            except Exception as e:
                logger.warning("feedback learner refresh failed: %s", e)

        # Shared on_scored callback. Per-row registry flush so the UI
        # paints match rows live as they're scored — same UX as before,
        # now spans BOTH passes (rows from the fast pass arrive first,
        # then pass 2 appends after PARSE finishes).
        FLUSH_EVERY = 1
        _pending: list = []
        _matches_so_far = 0
        _maybes_so_far = 0
        _scored_running_total = 0
        # scored_total grows as we discover the second batch's size after
        # PARSE. We seed with json_packets length up front so the Brief
        # tab's "Scoring 0/N" counter has a meaningful denominator before
        # the slow pass starts.
        self.progress["counts"]["scored"] = 0
        self.progress["counts"]["scored_total"] = len(json_packets)

        def _flush_pending():
            nonlocal _pending
            if not _pending or _reg is None:
                _pending = []
                return
            try:
                _reg.upsert_matches(_pending, cycle, profile_version=_profile_version)
            except Exception as e:
                logger.warning("incremental registry upsert failed: %s", e)
            _pending = []

        def _on_scored(scored, total, result, is_match):
            nonlocal _matches_so_far, _maybes_so_far, _scored_running_total
            # `scored`/`total` are pass-local. Convert to running totals
            # so the dashboard counter monotonically increases across
            # the two passes instead of resetting at pass 2.
            _scored_running_total = max(_scored_running_total, _pass1_scored_count + scored)
            self.progress["counts"]["scored"] = _scored_running_total
            tier = (result.payload or {}).get("_match_tier")
            if tier == "match":
                _matches_so_far += 1
                self.progress["counts"]["matches"] = _matches_so_far
                _pending.append(result)
                if len(_pending) >= FLUSH_EVERY:
                    _flush_pending()
            elif tier == "maybe":
                _maybes_so_far += 1
                self.progress["counts"]["maybes"] = _maybes_so_far
                _pending.append(result)
                if len(_pending) >= FLUSH_EVERY:
                    _flush_pending()

        # =====================================================================
        # PASS 1: pre-parsed JSON packets — fast path to first matches
        # =====================================================================
        _pass1_scored_count = 0  # consumed by _on_scored running total math
        scored_pass_1: list = []
        if json_packets:
            self._set_stage("match", "Scoring pre-parsed jobs", 5)
            logger.info("[FAST PASS] QA + FAKE + DEDUPE + MATCH on %d JSON packets", len(json_packets))
            clean_a, new_a = _qa_fake_dedupe_pass(json_packets, "fast pass")
            all_clean_packets.extend(clean_a)
            all_new_packets.extend(new_a)
            scored_pass_1 = self.match.run(new_a, on_scored=_on_scored) if new_a else []
            _flush_pending()
            _pass1_scored_count = len(new_a)
            logger.info(
                "[FAST PASS] Done. %d matches, %d maybes scored. Registry now live.",
                _matches_so_far, _maybes_so_far,
            )

        # =====================================================================
        # PARSE: only the ~50 HTML cards from Google. The fast pass above
        # already filled most of the match registry, so the user is no
        # longer staring at an empty Matches tab while we burn LLM cycles.
        # =====================================================================
        self._set_stage("parse", "Extracting role details from HTML cards", 2)
        logger.info("[2/8] PARSE")
        parsed_from_html = self.parse.run(html_packets) if html_packets else []
        parsed_jobs = [p for p in parsed_from_html if p.payload_type == PayloadType.JSON_JOB]

        # =====================================================================
        # PASS 2: HTML-parsed packets — append to registry after PARSE
        # =====================================================================
        scored_pass_2: list = []
        if parsed_jobs:
            self._set_stage("match", "Scoring HTML-parsed jobs", 5)
            logger.info("[SLOW PASS] QA + FAKE + DEDUPE + MATCH on %d parsed HTML packets",
                        len(parsed_jobs))
            clean_b, new_b = _qa_fake_dedupe_pass(parsed_jobs, "slow pass")
            all_clean_packets.extend(clean_b)
            all_new_packets.extend(new_b)
            # Bump the running denominator so "Scoring X/Y" reflects the
            # combined workload now that pass 2 has packets to score.
            self.progress["counts"]["scored_total"] = _pass1_scored_count + len(new_b)
            scored_pass_2 = self.match.run(new_b, on_scored=_on_scored) if new_b else []
            _flush_pending()
            logger.info("[SLOW PASS] Done.")

        scored_packets = scored_pass_1 + scored_pass_2

        # =====================================================================
        # Stats roll-up + persistence (was inline-per-stage before)
        # =====================================================================
        stats["parsed"] = len(json_packets) + len(parsed_jobs)
        self.progress["counts"]["parsed"] = stats["parsed"]
        stats["qa_pass"] = total_qa_pass
        stats["qa_fail"] = total_qa_fail
        self.progress["counts"]["qa_pass"] = stats["qa_pass"]
        self.progress["counts"]["qa_fail"] = stats["qa_fail"]
        stats["fake_blocked"] = total_fake_blocked
        self.progress["counts"]["fake_blocked"] = stats["fake_blocked"]
        # Compute final cross_listed_pct from merged counters
        if merged_dedupe_stats["unique"] > 0:
            merged_dedupe_stats["cross_listed_pct"] = round(
                100.0 * merged_dedupe_stats["merged"] / merged_dedupe_stats["unique"],
                1,
            )
        stats["dedupe"] = merged_dedupe_stats
        stats["new_jobs"] = total_newly_seen
        self.progress["counts"]["new_jobs"] = total_newly_seen

        # Persist fake-job audit trail (last 200 reports kept on disk).
        if all_fake_reports:
            fake_file = self.data_dir / "fake_jobs.json"
            existing = []
            if fake_file.exists():
                try:
                    existing = json.loads(fake_file.read_text())
                except Exception:
                    pass
            existing.extend(all_fake_reports)
            write_text_atomic(fake_file, json.dumps(existing[-200:], indent=2))

        self._save_seen()

        # Save market intel (on the union of clean packets across both passes)
        self._save_market_intel(all_clean_packets, cycle)

        # Aliases used by the rest of the cycle (fit-gap analyze, tracking,
        # save-all-parsed). The downstream code already expects these names.
        clean_packets = all_clean_packets
        new_packets = all_new_packets

        if not new_packets:
            logger.info("No new unique jobs this cycle.")
            self._set_stage("idle", "Waiting", 0)
            self._log_stats(stats)
            return stats

        self._save_all_parsed(new_packets, cycle)

        # =====================================================================
        # POST-MATCH PERSISTENCE (was inline in the old single-pass MATCH
        # block; both passes have already finished by this point).
        # =====================================================================
        # `scored_packets` was assembled from pass 1 + pass 2 above. The
        # registry has already been upserted live via the on_scored
        # callback. The bulk-upsert below is the idempotent safety net
        # for the (rare) case where registry prep crashed at the top.
        matches = [p for p in scored_packets if p.payload.get("_is_match")]
        stats["matches"] = len(matches)
        self.progress["counts"]["matches"] = stats["matches"]

        if matches:
            self._save_matches(matches, cycle)
            try:
                if _reg is None:
                    from core.match_registry import get_registry
                    _reg = get_registry(self.data_dir)
                    _reg.reload()
                reg_stats = _reg.upsert_matches(matches, cycle, profile_version=_profile_version)
                stats["match_registry"] = reg_stats
            except Exception as e:
                logger.warning("match_registry upsert failed: %s", e)
            # Per-match dump at DEBUG. The dashboard reads from the
            # registry, not logs, so this is purely log-readability.
            for m in matches:
                logger.debug("MATCH: %s @ %s (%.3f)",
                             m.payload.get("title"), m.payload.get("company"),
                             m.payload.get("_match_score", 0))

        # Persist match-agent diagnostics (median latency, mode, sample
        # count) so /api/status can show a sensible Brief metric after
        # a backend restart. The MatchAgent's latency ring buffer is
        # in-memory only.
        try:
            stats_snapshot = self.match.get_status()
            stats_snapshot["_saved_at"] = datetime.utcnow().isoformat() + "Z"
            stats_snapshot["_cycle"] = cycle
            stats_path = self.data_dir / "match_stats.json"
            stats_path.write_text(json.dumps(stats_snapshot, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("match_stats.json write failed: %s", e)

        # 6. FIT-GAP ANALYSIS (only on top-N matches by score)
        # Analysing every match burns an LLM call per job. On a 30-match
        # cycle that's 30 sequential calls to qwen3:8b, which dominates
        # runtime even on a mid-range 16 GB card. Gating to the top N
        # by score keeps the sharpest jobs analysed while cutting total
        # LLM time by 60-80 percent. Set analyze.top_n <= 0 (or null) to
        # restore the old 'analyse everything' behaviour.
        self._set_stage("fit_gap", "Explaining fit and gaps for top matches", 6)
        logger.info("[6/8] FIT-GAP ANALYSIS")
        fit_reports = []
        if matches:
            analyze_cfg = self.config.get("analyze", {}) or {}
            top_n = int(analyze_cfg.get("top_n", 10) or 0)
            to_analyze = matches
            if top_n > 0 and len(matches) > top_n:
                # Sort by _match_score descending; stable tiebreak on
                # title so re-running the same cycle picks the same set.
                to_analyze = sorted(
                    matches,
                    key=lambda p: (
                        -(p.payload.get("_match_score") or 0.0),
                        p.payload.get("title", ""),
                    ),
                )[:top_n]
                logger.info(
                    "Analysing top %d of %d matches (analyze.top_n=%d).",
                    len(to_analyze), len(matches), top_n,
                )
            fit_reports = self.analyzer.run(to_analyze)
            self._save_fit_gaps(fit_reports, cycle)
            stats["fit_gaps"] = len(fit_reports)
            self.progress["counts"]["fit_gaps"] = stats["fit_gaps"]

            # =====================================================================
            # Merge fit-gap reports back onto the match registry payloads.
            # =====================================================================
            # Before this fix, fit-gap data lived only in data/fit_gaps/
            # cycle_X.json — the match registry payloads never received
            # it. That broke the SkillGap card on the Brief tab: it
            # reads `m._fit_gap.matched` / `m._fit_gap.gaps` from each
            # match in the FE registry, found `undefined` on every row,
            # and rendered "Not enough analysed matches yet" even when
            # the registry had 1000+ matches. The fit_gaps/*.json files
            # were a write-only sink.
            #
            # Now: every fit report gets folded onto its source packet's
            # `_fit_gap` field (matching the FE's MatchPayload._fit_gap
            # shape), then we upsert those packets into the registry
            # so the FE's next poll picks them up.
            #
            # Wire shape mapping (analyzer output → FE consumer):
            #   matched_skills       → _fit_gap.matched     (list[str])
            #   missing_skills/gaps  → _fit_gap.gaps        (list[str])
            #   fit_summary          → _fit_gap.summary     (str)
            #   talking_points       → _fit_gap.rationale   (str, joined)
            if fit_reports:
                by_url = {r.get("url"): r for r in fit_reports if r.get("url")}
                updated_packets = []
                for pkt in to_analyze:
                    url = pkt.payload.get("url")
                    report = by_url.get(url)
                    if not report or report.get("error"):
                        continue
                    # `gaps` in the analyzer output is sometimes a list
                    # of dicts ({skill, severity, mitigation}), sometimes
                    # a list of strings. Coerce both to plain strings.
                    raw_gaps = report.get("gaps") or report.get("missing_skills") or []
                    gap_strings = []
                    for g in raw_gaps:
                        if isinstance(g, dict):
                            s = g.get("skill") or g.get("name")
                            if s:
                                gap_strings.append(str(s))
                        elif g:
                            gap_strings.append(str(g))
                    matched = [str(s) for s in (report.get("matched_skills") or []) if s]
                    tps = report.get("talking_points") or []
                    rationale = " ".join(str(t) for t in tps if t)[:1000]
                    pkt.payload["_fit_gap"] = {
                        "summary": str(report.get("fit_summary") or "")[:500],
                        "matched": matched,
                        "gaps": gap_strings,
                        "rationale": rationale,
                    }
                    updated_packets.append(pkt)
                # Idempotent re-upsert into the registry. The match
                # registry keys on the dedupe-key so this updates the
                # existing rows in place rather than appending duplicates.
                if updated_packets and _reg is not None:
                    try:
                        _reg.upsert_matches(updated_packets, cycle,
                                            profile_version=_profile_version)
                        logger.info(
                            "Fit-gap data merged onto %d registry rows (now visible to the SkillGap card).",
                            len(updated_packets),
                        )
                    except Exception as e:
                        logger.warning("fit-gap registry upsert failed: %s", e)

        # 7. AUTO-RESUME GENERATION
        # Off by default. Resume tailoring is the slowest stage in the
        # cycle (one LLM call per top match) and most cycles don't need
        # it — the user triggers it per-match on demand via
        # POST /api/tailor-resume (the "Tailor resume" button in the
        # Matches detail panel). Set config.resume.auto_generate=true to
        # restore the old every-cycle behaviour for headless / overnight
        # runs.
        self._set_stage("resume", "Tailoring resume bullets", 7)
        auto_resume = bool((self.config.get("resume") or {}).get("auto_generate", False))
        if auto_resume and fit_reports:
            logger.info("[7/8] RESUME GENERATION (auto)")
            resume_results = self.resume_gen.run(fit_reports)
            stats["resumes"] = len(resume_results)
            self.progress["counts"]["resumes"] = stats["resumes"]
            logger.info("Generated %d tailored resumes", stats["resumes"])
        else:
            logger.info("[7/8] RESUME GENERATION skipped (on-demand mode)")

        # 8. TRACKING
        self._set_stage("tracking", "Saving matches and writing the brief", 8)
        logger.info("[8/8] FUNNEL TRACKING")
        # Discover both match and maybe tiers so the funnel reflects the
        # full consideration set, not just hard matches. Without this the
        # funnel reads total_discovered=0 whenever the match bar is tight
        # or a cycle fails upstream, hiding that the pipeline actually
        # found candidates worth surfacing.
        tracked = list(matches)
        maybe_packets = [p for p in scored_packets
                         if p.payload.get("_match_tier") == "maybe"
                         and not p.payload.get("_is_match")]
        tracked.extend(maybe_packets)
        self.tracker.bulk_discover(tracked, fit_reports)
        funnel = self.tracker.funnel_metrics()
        logger.info("Funnel: %s (match=%d, maybe=%d tracked this cycle)",
                    json.dumps(funnel), len(matches), len(maybe_packets))

        # DIGEST (every N cycles) — only when there's somewhere to
        # send the output. The whole point of the digest is delivery
        # via Discord webhook or email; without either configured, the
        # generated prose just gets logged and tossed. The LLM call
        # itself takes ~10-15s on gemma3:12b, so skipping it when the
        # output has no consumer is a free speed win on every Nth
        # cycle.
        has_webhook = bool((self.config.get("discord_webhook") or "").strip())
        email_cfg = self.config.get("email") or {}
        has_email = bool((email_cfg.get("smtp_user") or "").strip()
                         and (email_cfg.get("smtp_pass") or "").strip())
        if cycle % self.digest_every == 0 and (has_webhook or has_email):
            logger.info("GENERATING DIGEST (every %d cycles)", self.digest_every)
            tracker_export = self.tracker.export_for_dashboard()
            self.digest.run(stats, tracker_export, fit_reports, matches)
        elif cycle % self.digest_every == 0:
            logger.info(
                "Skipping digest stage: no discord_webhook or email "
                "configured. Configure either in config.json to enable.",
            )

        # Record wall-clock duration for the Brief tab metrics.
        stats["duration_seconds"] = round(time.time() - cycle_started, 2)
        # Pipeline-only duration: everything after ingest. Defaults to
        # 0 when the empty-cycle branch above returned early (we never
        # set pipeline_started). Subtracting is more robust than
        # `time.time() - pipeline_started` because it survives clock
        # adjustments mid-cycle.
        try:
            stats["pipeline_seconds"] = round(time.time() - pipeline_started, 2)
        except NameError:
            stats["pipeline_seconds"] = 0.0
        # Append to cycle_times.json for the rolling-average calculations
        # in /api/status (avg_pipeline_seconds, avg_scrape_seconds).
        # Stats keys live on the same entry; the per-cycle History tab
        # was removed but the file structure stays compatible.
        self._record_cycle_duration(cycle, stats["duration_seconds"], stats=stats)

        # NOTE: dashboard.json export was removed in the dead-code audit.
        # The /api/dashboard endpoint that read it is gone, no UI surface
        # consumes it, and writing it on every cycle was wasted I/O on
        # the hot path. If you bring back a dashboard endpoint, the
        # _export_dashboard_data method history is in git.

        # Cycle complete: reset to idle so the dashboard stops painting
        # "in progress" until the next run_cycle tick.
        self._set_stage("idle", "Waiting", 0)

        self._log_stats(stats)
        return stats

    def _record_cycle_duration(self, cycle: int, seconds: float, stats: dict | None = None):
        """Append cycle duration + summary counts to a ring buffer on disk
        so the UI can compute an average across recent runs (Brief tab)
        AND render a History tab with per-cycle breakdown.

        Keeps the top-level keys `cycle`, `seconds`, `ts` stable for the
        existing /api/cycle-times consumers. Extra fields live under the
        same entry so no second file is needed.
        """
        path = self.data_dir / "cycle_times.json"
        history = []
        if path.exists():
            try:
                history = json.loads(path.read_text())
            except Exception:
                history = []
        entry = {
            "cycle": cycle,
            "seconds": seconds,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if isinstance(stats, dict):
            # Lift the safe, small numeric fields onto the entry. We
            # deliberately avoid copying the full stats dict (may contain
            # nested structures that balloon file size over many runs).
            for key in ("ingested", "parsed", "qa_pass", "qa_fail",
                        "fake_blocked", "new_jobs", "matches",
                        "fit_gaps", "resumes",
                        "ingest_seconds", "pipeline_seconds"):
                if key in stats:
                    entry[key] = stats[key]
        history.append(entry)
        history = history[-100:]
        write_text_atomic(path, json.dumps(history, indent=2))

    def _log_stats(self, stats):
        logger.info("-" * 40)
        logger.info("CYCLE %d SUMMARY:", stats["cycle"])
        logger.info("  Ingested:   %d", stats["ingested"])
        logger.info("  Parsed:     %d", stats["parsed"])
        logger.info("  QA Pass:    %d | Fail: %d", stats["qa_pass"], stats["qa_fail"])
        logger.info("  Fake Blocked: %d", stats.get("fake_blocked", 0))
        logger.info("  New Jobs:   %d (deduped)", stats["new_jobs"])
        logger.info("  Matches:    %d", stats["matches"])
        logger.info("  Fit-Gaps:   %d", stats["fit_gaps"])
        logger.info("  Resumes:    %d", stats.get("resumes", 0))
        logger.info("-" * 40)

    def _wait_for_setup(self):
        """Block (with polling) until the user finishes the first-run
        wizard. Keeps the server + dashboard responsive (they run on
        their own thread) while preventing any scraping from happening
        on an unconfigured profile. Logs once on entry so the terminal
        shows the app isn't crashed, just waiting."""
        if user_store.is_setup_complete(self.data_dir):
            return
        logger.info("Waiting for first-run setup to complete via the dashboard wizard.")
        self.progress["stage"] = "awaiting_setup"
        self.progress["stage_label"] = "Awaiting setup"
        self.progress["stage_index"] = 0
        printed_reminder = False
        while not user_store.is_setup_complete(self.data_dir):
            # Poll every 3s. Cheap: user_store.load re-reads a tiny JSON.
            # Print a gentle reminder every ~2 minutes so long idles
            # don't look like the process has hung.
            for _ in range(40):
                time.sleep(3)
                if user_store.is_setup_complete(self.data_dir):
                    break
            else:
                if not printed_reminder:
                    logger.info("Still waiting for setup. Open the dashboard and finish the wizard.")
                    printed_reminder = True
        logger.info("Setup complete. Starting cycle loop.")
        self.progress["stage"] = "idle"
        self.progress["stage_label"] = "Waiting"

    def is_manual_mode(self) -> bool:
        """True when cycles only fire on demand (/api/run-cycle from the
        dashboard) rather than on the scheduled interval. See
        `_resolve_manual_mode` for resolution order."""
        return _resolve_manual_mode(self.config)

    def run(self):
        logger.info("LANTERN ORCHESTRATOR STARTING")
        logger.info("Cycle interval: %d minutes", self.cycle_interval)
        logger.info("Max cycles: %s", self.max_cycles or "unlimited")
        logger.info("Sources: %d Greenhouse, %d Lever, %d Ashby + big-tech tenants + job boards",
                     len(self.ingest.greenhouse), len(self.ingest.lever), len(self.ingest.ashby))

        # First-run gate. The dashboard's wizard calls /api/setup to flip
        # the user_store flag; until then we idle rather than scraping
        # against an empty profile.
        self._wait_for_setup()

        manual = self.is_manual_mode()
        if manual:
            logger.info("Manual mode active. Cycles run only when the dashboard "
                        "triggers /api/run-cycle. Close the process to stop.")
            self.progress["stage"] = "idle"
            self.progress["stage_label"] = "Idle - press Run Pipeline"
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                logger.info("Shutdown requested.")
            finally:
                self._save_seen()
                logger.info("LANTERN STOPPED (manual mode) after %d cycles.",
                            self.cycle_count)
            return

        try:
            while True:
                self.run_cycle()
                if self.max_cycles and self.cycle_count >= self.max_cycles:
                    logger.info("Max cycles (%d) reached.", self.max_cycles)
                    break
                logger.info("Sleeping %d minutes...", self.cycle_interval)
                time.sleep(self.cycle_interval * 60)
        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
        except Exception as e:
            logger.critical("Unhandled error: %s", e, exc_info=True)
        finally:
            self._save_seen()
            logger.info("LANTERN STOPPED after %d cycles.", self.cycle_count)
