"""
[MOD-MATCH]: The Vector Matcher
Computes cosine similarity between user profile and parsed jobs.
Uses sentence-transformers if available, falls back to LLM-based scoring.

Preferences pipeline (per cycle):
  1. LocationFilter (hard): drop jobs whose location violates user rules.
  2. Scorer (embedding or LLM): produce base match score.
  3. SalaryScorer (soft): adjust score by salary_weight given salary_floor.
  4. Threshold: mark _is_match if final score >= threshold.
"""

import logging
import time
from core.protocol import SentinelPacket, Sender, PayloadType, Priority
from core import llm
from core.preferences import (
    LocationFilter, LocationScorer, SalaryScorer,
    ExperienceFilter, ExperienceScorer, CountryFilter, TitleScorer,
    describe as describe_prefs,
)
from core import dimensions as dim_scorer
from core import fake_detector
from core import feedback_learner as _feedback

logger = logging.getLogger("sentinel.match")


# ─── Piecewise-linear score calibration ──────────────────────────────
# bge-m3 cosine similarity for related tech-PM documents almost never
# spans the full 0-1 range. Real-world observed spread is ~0.35-0.70,
# which means every job scores around 50% on the UI and the human has
# no signal. The anchors below stretch the observed working window to
# a perceptually useful 5-98% display range. Monotonic, so sort order
# is preserved. Raw score is still kept on the payload for downstream
# tier logic and debugging.
#
# v2 tuning (2026-04): user feedback was "everything sits around 50%,
# I can't tell the good ones apart". The clustering zone is 0.45-0.58
# raw, so we spent more anchor budget there to stretch that slice
# across ~35-80% of the display range. The previous table mapped that
# same slice to ~15-75% which sounded wide on paper but bunched all
# the actual results into a tight ~30-50% display window because the
# real mass sits at 0.48-0.54. Also added a 0.45 anchor specifically
# so a "decent but not strong" PM fit lands near 35% display (matches
# the user's intuition that "a 34% match is pretty solid").
_CALIBRATION_ANCHORS = [
    (0.00, 0.00),
    (0.30, 0.06),
    (0.38, 0.15),
    (0.42, 0.24),
    (0.45, 0.35),   # "a 34% match is pretty solid"
    (0.48, 0.46),
    (0.50, 0.55),
    (0.52, 0.64),
    (0.55, 0.75),
    (0.58, 0.83),
    (0.62, 0.90),
    (0.68, 0.96),
    (0.75, 0.99),
    (1.00, 1.00),
]


def calibrate_score(raw: float) -> float:
    """Map raw cosine similarity to a calibrated display score via
    piecewise-linear interpolation between the anchors above. Tolerates
    inputs outside [0, 1] by clamping."""
    if raw is None:
        return 0.0
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if x <= _CALIBRATION_ANCHORS[0][0]:
        return _CALIBRATION_ANCHORS[0][1]
    if x >= _CALIBRATION_ANCHORS[-1][0]:
        return _CALIBRATION_ANCHORS[-1][1]
    for i in range(len(_CALIBRATION_ANCHORS) - 1):
        x0, y0 = _CALIBRATION_ANCHORS[i]
        x1, y1 = _CALIBRATION_ANCHORS[i + 1]
        if x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return _CALIBRATION_ANCHORS[-1][1]

try:
    from sentence_transformers import SentenceTransformer, util as st_util
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    # The dev launchers (start.ps1 / start.sh) install this automatically
    # via requirements.txt. The packaged EXE deliberately excludes it to
    # keep the bundle under 1 GB and falls back to LLM-based scoring.
    logger.warning(
        "sentence-transformers not available. Using LLM-based matching fallback. "
        "For the fast embedding path, run the dev launcher (start.ps1 / start.sh) "
        "or `pip install sentence-transformers` into your active venv."
    )


MATCH_PROMPT = """You are a job matching evaluator. Score how well this job matches the candidate profile.

CANDIDATE PROFILE:
{profile}

JOB LISTING:
Title: {title}
Company: {company}
Location: {location}
Description: {description}
Technologies: {technologies}
Seniority: {seniority}
Remote: {remote}

Respond with ONLY a JSON object:
{{"score": <float 0.0 to 1.0>, "reasoning": "<one sentence>"}}
"""


class MatchAgent:
    """Matches jobs against a user profile using embeddings or LLM fallback."""

    def __init__(self, config: dict):
        # Keep the original config on the instance so run() / score() can
        # read values (target_archetypes, ghost_weight, etc.) without
        # re-plumbing them through every method. Hot-swap setters below
        # also update the cached dict so subsequent reads see fresh values.
        self.config = dict(config) if isinstance(config, dict) else {}
        # Per-company score nudges from the tracker's "how did I do with
        # this company last time?" signal. Populated by the orchestrator
        # via set_company_signals() once per cycle (before run()).
        # Shape: {"stripe": 0.04, "some-bad-co": -0.03}. Applied after
        # the ghost fold as a bounded multiplicative bump. Empty dict =
        # feature disabled.
        self.company_signals: dict = {}
        self.profile_text = config.get("profile_text", "")
        # Structured profile (from core/resume_profile.py). Optional -
        # when set, we emit per-job dimensional sub-scores on top of the
        # primary embedding/LLM score.
        self.profile_struct = config.get("profile_struct") or None
        # Primary `match` threshold. Kept for backwards compatibility and
        # shown in Settings as the headline number. Scores at or above
        # this land in the Matches tab.
        self.threshold = config.get("threshold", 0.55)
        # Two-tier provenance-aware scoring. A raw similarity score means
        # different things depending on whether it came from MiniLM
        # embeddings or an LLM judge, so each has its own pair of cutoffs.
        # Precedence:
        #   1. config.match.tiers.{embed,llm}.{match,maybe} if present.
        #   2. Otherwise, derive from self.threshold so the user's
        #      headline threshold still drives behaviour.
        # The Maybe tier is *below* the Match tier, never above. It lets
        # borderline roles surface in a separate tab without polluting
        # Matches. Users can turn it off by setting maybe == match.
        tiers_cfg = (config.get("tiers") or {}) if isinstance(config.get("tiers"), dict) else {}
        embed_cfg = tiers_cfg.get("embed") or {}
        llm_cfg = tiers_cfg.get("llm") or {}
        # Derive defaults from the headline threshold. Embeddings cluster
        # tight (0.4-0.75 realistic window), so Maybe is set 0.10 below
        # Match. LLMs give higher absolute scores for loose matches, so
        # Match is +0.10 above headline and Maybe is at headline.
        embed_match_default = max(0.0, min(1.0, self.threshold + 0.05))
        embed_maybe_default = max(0.0, min(1.0, self.threshold - 0.10))
        llm_match_default   = max(0.0, min(1.0, self.threshold + 0.10))
        llm_maybe_default   = max(0.0, min(1.0, self.threshold))
        self.tier_cutoffs = {
            "embed": {
                "match": float(embed_cfg.get("match", embed_match_default)),
                "maybe": float(embed_cfg.get("maybe", embed_maybe_default)),
            },
            "llm": {
                "match": float(llm_cfg.get("match",   llm_match_default)),
                "maybe": float(llm_cfg.get("maybe",   llm_maybe_default)),
            },
        }
        # Guarantee maybe <= match even if config specified otherwise.
        for k in ("embed", "llm"):
            if self.tier_cutoffs[k]["maybe"] > self.tier_cutoffs[k]["match"]:
                self.tier_cutoffs[k]["maybe"] = self.tier_cutoffs[k]["match"]
        self.model_name = config.get("model", "qwen3:8b")
        self.embed_model = None
        self.profile_embedding = None

        # Ghost-job aggressiveness. Accept either a named preset
        # ("low"/"balanced"/"strict") or a raw float. Falls back to the
        # module default when the config is missing/invalid.
        fake_cfg = config.get("fake_detection") or {}
        self.fake_threshold = fake_detector.resolve_threshold(
            fake_cfg.get("aggressiveness", fake_cfg.get("threshold"))
        )

        # Ghost-score fold controls. The fold penalises the match score by
        # the detector's ghost probability so a high-fit posting that looks
        # like a ghost ranks below a lower-fit posting that looks real.
        #
        #   ghost_weight             — 0..1 multiplier. 0 disables the fold
        #                              (legacy behaviour). Default 0.35 means
        #                              "at worst, a 100% ghost loses 35% of
        #                              its match score". Tunable in Pipeline UI.
        #   ghost_flag_threshold     — ghost score at/above which the job gets
        #                              badged as "suspicious" in the list view.
        #                              Mirrors self.fake_threshold but kept
        #                              separately so we can eventually split
        #                              "flag in UI" from "count against score".
        #   ghost_warn_threshold     — below flag, above this = show a softer
        #                              "age-stale" warning. Gives users a
        #                              middle band instead of a binary flag.
        # All three are hot-swappable via set_ghost_weight / set_ghost_thresholds
        # so the Pipeline-tab sliders can apply without restart.
        try:
            self.ghost_weight = float(fake_cfg.get("ghost_weight", 0.35) or 0.0)
        except (TypeError, ValueError):
            self.ghost_weight = 0.35
        self.ghost_weight = max(0.0, min(1.0, self.ghost_weight))
        try:
            self.ghost_flag_threshold = float(
                fake_cfg.get("flag_threshold", self.fake_threshold))
        except (TypeError, ValueError):
            self.ghost_flag_threshold = self.fake_threshold
        try:
            self.ghost_warn_threshold = float(
                fake_cfg.get("warn_threshold", 0.30))
        except (TypeError, ValueError):
            self.ghost_warn_threshold = 0.30
        # warn must sit strictly below flag, else the warn band is empty.
        if self.ghost_warn_threshold >= self.ghost_flag_threshold:
            self.ghost_warn_threshold = max(0.0, self.ghost_flag_threshold - 0.10)

        # Preferences (location + salary). Kept on the agent so
        # orchestrator can refresh via set_preferences() after the user
        # edits them in the UI without bouncing the process.
        prefs = config.get("preferences", {}) or {}
        self.location_filter = LocationFilter(prefs)
        self.location_scorer = LocationScorer(prefs)
        self.salary_scorer = SalaryScorer(prefs)
        self.experience_filter = ExperienceFilter(prefs)
        self.experience_scorer = ExperienceScorer(prefs)
        self.country_filter = CountryFilter(prefs)
        # TitleScorer reads role_keywords off the match config (plumbed
        # from config.ingest.role_keywords by the orchestrator) so the
        # boost list stays in one place.
        self._title_weight = float(config.get("title_weight", 0.08) or 0.08)
        self._role_keywords = list(config.get("role_keywords") or [])
        self.title_scorer = TitleScorer({
            "role_keywords": self._role_keywords,
            "title_weight": self._title_weight,
        })

        # Per-match latency ring buffer (ms). Kept small so a long-running
        # process doesn't grow memory unbounded.
        self._latencies_ms: list[float] = []
        self._latency_cap = 500

        # Feedback learner: caches embeddings for starred + dismissed jobs
        # so the matcher can boost/penalise semantically similar roles on
        # future cycles. Orchestrator calls refresh_feedback(registry) at
        # the start of each cycle. When data_dir isn't provided (tests,
        # legacy callers) the learner is None and scoring works as before.
        data_dir = config.get("data_dir")
        self.feedback_learner = _feedback.FeedbackLearner(data_dir) if data_dir else None

        if EMBEDDINGS_AVAILABLE:
            from core import embed_presets
            raw_embed = config.get("embed_model")
            embed_model_name = embed_presets.resolve(raw_embed)
            if raw_embed and raw_embed != embed_model_name:
                logger.info("Embedding preset %r -> %s", raw_embed, embed_model_name)
            try:
                logger.info("Loading embedding model: %s", embed_model_name)
                self.embed_model = SentenceTransformer(embed_model_name)
                self._encode_profile()
                logger.info("Embedding model loaded.")
            except Exception as e:
                logger.error("Failed to load embedding model: %s. Using LLM fallback.", e)
                self.embed_model = None

        # Seed the feedback learner from the user's profile text the first
        # time we have both an embedding model and a learner. Subsequent
        # reseeds (on resume upload or keyword edit) are driven by
        # reseed_feedback(). This is the cold-start fix: without seeds,
        # the learner contributes nothing until the user stars ≥3 jobs.
        self._reseed_feedback_from_current_state(config.get("role_keywords"))

        logger.info("MatchAgent preferences: %s", describe_prefs(prefs))

    # ──────────────────────────────────────────────────────────────
    # Profile management
    # ──────────────────────────────────────────────────────────────
    def _encode_profile(self):
        """(Re)compute the profile embedding. Safe to call with empty text -
        we skip encoding so a later set_profile() call can populate it."""
        if self.embed_model is None or not self.profile_text.strip():
            self.profile_embedding = None
            return
        self.profile_embedding = self.embed_model.encode(self.profile_text, convert_to_tensor=True)

    def set_profile(self, text: str):
        """Swap the candidate profile between cycles (resume upload/clear).
        Re-encodes the embedding if embeddings are in use. No-op if the
        text is unchanged, so poll-driven refreshes are cheap."""
        text = text or ""
        if text == self.profile_text:
            return
        self.profile_text = text
        self._encode_profile()
        # Reseed the feedback learner against the new profile so cold
        # start stays aligned with what the user just uploaded.
        self._reseed_feedback_from_current_state()

    def set_preferences(self, prefs: dict):
        """Swap the location/salary/experience preferences without restarting."""
        prefs = prefs or {}
        self.location_filter = LocationFilter(prefs)
        self.location_scorer = LocationScorer(prefs)
        self.salary_scorer = SalaryScorer(prefs)
        self.experience_filter = ExperienceFilter(prefs)
        self.experience_scorer = ExperienceScorer(prefs)
        self.country_filter = CountryFilter(prefs)
        # Preserve the most recent title-scorer settings on hot-apply.
        # (Role keywords come from config.ingest, not preferences, so they
        # don't change on a preferences save — only a full config reload.)
        self.title_scorer = TitleScorer({
            "role_keywords": self._role_keywords,
            "title_weight": self._title_weight,
        })
        logger.info("MatchAgent preferences updated: %s", describe_prefs(prefs))

    def set_profile_struct(self, profile: dict | None):
        """Swap the structured profile between cycles. Safe to call with
        None to disable dimensional scoring."""
        self.profile_struct = profile or None

    def set_fake_threshold(self, preset_or_value):
        """Hot-swap the ghost-job suspicion threshold. Accepts a preset
        name or a raw float; falls back to the module default if the
        input is unusable."""
        self.fake_threshold = fake_detector.resolve_threshold(preset_or_value)
        logger.info("MatchAgent ghost threshold set to %.3f", self.fake_threshold)

    def set_ghost_weight(self, value):
        """Hot-swap the ghost-penalty weight (0..1). 0 disables the fold;
        higher values punish suspicious postings more. Sliders in the
        Pipeline tab call this via /api/config."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        self.ghost_weight = max(0.0, min(1.0, v))
        # Mirror into cached config so anything else that reads
        # self.config.fake_detection.ghost_weight stays in sync.
        fd = self.config.setdefault("fake_detection", {})
        fd["ghost_weight"] = self.ghost_weight
        logger.info("MatchAgent ghost_weight set to %.3f", self.ghost_weight)

    def set_ghost_thresholds(self, flag=None, warn=None):
        """Hot-swap the ghost flag / warn thresholds. Both are optional;
        pass just the one that changed. Enforces warn < flag so the
        middle band is always meaningful."""
        if flag is not None:
            try:
                self.ghost_flag_threshold = max(0.0, min(1.0, float(flag)))
            except (TypeError, ValueError):
                pass
        if warn is not None:
            try:
                self.ghost_warn_threshold = max(0.0, min(1.0, float(warn)))
            except (TypeError, ValueError):
                pass
        if self.ghost_warn_threshold >= self.ghost_flag_threshold:
            self.ghost_warn_threshold = max(0.0, self.ghost_flag_threshold - 0.10)
        fd = self.config.setdefault("fake_detection", {})
        fd["flag_threshold"] = self.ghost_flag_threshold
        fd["warn_threshold"] = self.ghost_warn_threshold
        logger.info("MatchAgent ghost thresholds: flag=%.3f warn=%.3f",
                    self.ghost_flag_threshold, self.ghost_warn_threshold)

    def set_company_signals(self, signals: dict):
        """Hot-swap the per-company score nudges. Orchestrator calls
        this at the top of each cycle with the tracker's latest
        company_signals() output. Values outside [-0.08, 0.08] are
        clamped to keep the feature from ever dominating the primary
        fit score."""
        if not isinstance(signals, dict):
            self.company_signals = {}
            return
        cleaned = {}
        for k, v in signals.items():
            if not isinstance(k, str):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            cleaned[k.lower().strip()] = max(-0.08, min(0.08, fv))
        self.company_signals = cleaned
        if cleaned:
            logger.info("MatchAgent learned signals loaded for %d companies", len(cleaned))

    # ──────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────
    def get_status(self) -> dict:
        """Snapshot for /api/status so the UI can show whether the fast
        embedding path is live or if we're falling back to the LLM."""
        mode = "embeddings" if (self.embed_model is not None and self.profile_embedding is not None) else (
            "llm" if self.profile_text.strip() else "idle"
        )
        latencies = list(self._latencies_ms)
        median = sorted(latencies)[len(latencies) // 2] if latencies else None
        return {
            "mode": mode,
            "embeddings_installed": EMBEDDINGS_AVAILABLE,
            "embeddings_active": mode == "embeddings",
            "threshold": self.threshold,
            "profile_chars": len(self.profile_text),
            "median_latency_ms": median,
            "sample_count": len(latencies),
            "preferences_active": {
                "location": self.location_filter.active or self.location_scorer.active,
                "location_mode": self.location_filter.mode,
                "salary": self.salary_scorer.active,
            },
        }

    # ──────────────────────────────────────────────────────────────
    # Scoring
    # ──────────────────────────────────────────────────────────────
    def _job_to_text(self, payload: dict) -> str:
        parts = []
        for key in ["title", "company", "location", "description", "seniority", "remote"]:
            val = payload.get(key)
            if val:
                parts.append(f"{key}: {val}")
        techs = payload.get("technologies", [])
        if techs:
            parts.append(f"technologies: {', '.join(techs)}")
        return "\n".join(parts)

    def score_with_embeddings(self, job_text: str):
        """Returns (score, job_embedding). The embedding is returned so
        callers can reuse it for the feedback learner without paying the
        encode() cost twice."""
        job_embedding = self.embed_model.encode(job_text, convert_to_tensor=True)
        score = st_util.cos_sim(self.profile_embedding, job_embedding).item()
        return round(score, 4), job_embedding

    def _reseed_feedback_from_current_state(self, role_keywords=None):
        """Idempotent. Seeds the feedback learner from the current
        profile_text (and any passed role keywords) so cold-start users
        get a useful similarity signal before they've starred anything.
        No-op when the learner is disabled or embeddings are unavailable.
        """
        if self.feedback_learner is None or self.embed_model is None:
            return None
        try:
            return self.feedback_learner.seed_from_profile(
                self.profile_text,
                self.embed_model,
                role_keywords=role_keywords or [],
            )
        except Exception as e:
            logger.warning("feedback seed failed: %s", e)
            return None

    def refresh_feedback(self, registry_entries: dict):
        """Orchestrator hook: refresh the feedback learner's embedding
        cache at the start of each cycle so recent saves/dismisses take
        effect. No-op when the learner is disabled."""
        if self.feedback_learner is None or self.embed_model is None:
            return None
        try:
            return self.feedback_learner.refresh(registry_entries, self.embed_model)
        except Exception as e:
            logger.warning("feedback refresh failed: %s", e)
            return None

    def score_with_llm(self, payload: dict) -> tuple[float, str]:
        prompt = MATCH_PROMPT.format(
            profile=self.profile_text,
            title=payload.get("title", "N/A"),
            company=payload.get("company", "N/A"),
            location=payload.get("location", "N/A"),
            description=payload.get("description", "N/A"),
            technologies=", ".join(payload.get("technologies", [])),
            seniority=payload.get("seniority", "unknown"),
            remote=payload.get("remote", "unknown"),
        )
        result = llm.query_json(prompt, task="match")
        score = float(result.get("score", 0))
        reasoning = result.get("reasoning", "")
        return score, reasoning

    def match(self, packet: SentinelPacket, title_index: dict | None = None) -> SentinelPacket:
        """Score a single job packet. Location filter applied upstream in run().
        `title_index` is the per-cycle accumulator for the duplicate-title
        fake-detector signal; callers pass it from run()."""
        job_text = self._job_to_text(packet.payload)
        start = time.time()

        # Track which scorer produced the raw score. Tier cutoffs differ
        # between embeddings and LLM (see tier_cutoffs in __init__) because
        # the two distributions are not comparable.
        provenance = "none"
        job_embedding = None
        try:
            if self.embed_model is not None and self.profile_embedding is not None:
                score, job_embedding = self.score_with_embeddings(job_text)
                reasoning = "embedding similarity"
                provenance = "embed"
            elif not self.profile_text.strip():
                # No profile at all - either no resume uploaded and
                # config.match.profile_text empty. Score 0 so nothing matches.
                score = 0.0
                reasoning = "no candidate profile configured"
            else:
                score, reasoning = self.score_with_llm(packet.payload)
                provenance = "llm"
        except Exception as e:
            logger.error("Matching error: %s", e)
            score = 0.0
            reasoning = f"error: {e}"

        # Feedback adjustment: bias toward jobs similar to saved ones and
        # away from jobs similar to dismissed ones. Only runs on the
        # embedding path (needs a job embedding to compare against). Gated
        # internally on MIN_SET_SIZE so it is a no-op until the user has
        # hearted / X-ed enough roles.
        feedback_telemetry: dict | None = None
        if (self.feedback_learner is not None
                and job_embedding is not None
                and provenance == "embed"):
            try:
                score, feedback_telemetry = self.feedback_learner.adjust(score, job_embedding)
            except Exception as e:
                logger.warning("feedback adjust failed: %s", e)
                feedback_telemetry = None

        # Soft weights compose in a deterministic order so score math is
        # reproducible: title -> location -> salary -> years. Title runs
        # FIRST so the keyword boost stacks cleanly onto the base embedding
        # score before other deltas are layered on. Each returns the
        # running score plus a signed delta for telemetry / UI hover.
        adjusted, title_delta, title_reason = self.title_scorer.adjust(score, packet.payload)
        adjusted, location_delta, location_reason = self.location_scorer.adjust(adjusted, packet.payload)
        adjusted, salary_delta, salary_reason = self.salary_scorer.adjust(adjusted, packet.payload)
        adjusted, years_delta, years_reason = self.experience_scorer.adjust(adjusted, packet.payload)

        self._latencies_ms.append((time.time() - start) * 1000)
        if len(self._latencies_ms) > self._latency_cap:
            self._latencies_ms = self._latencies_ms[-self._latency_cap:]

        # Two-tier provenance-aware classification.
        #   match : adjusted score >= cutoffs[provenance].match
        #   maybe : adjusted score >= cutoffs[provenance].maybe (but below match)
        #   none  : below both
        # Keep `_is_match` true only for the top tier so the Matches tab
        # remains high signal. `_match_tier` carries the full label.
        tier = "none"
        if provenance in self.tier_cutoffs:
            cutoffs = self.tier_cutoffs[provenance]
            if adjusted >= cutoffs["match"]:
                tier = "match"
            elif adjusted >= cutoffs["maybe"]:
                tier = "maybe"

        # Calibrated display score: stretches the bunched 0.40-0.70 cosine
        # window to a perceptually useful 5-98% band. Sort-order preserving,
        # so the existing tier logic is untouched - we keep the raw adjusted
        # score for thresholding and surface the calibrated number only for
        # UI display.
        display_score = calibrate_score(adjusted) if provenance == "embed" else adjusted

        payload = {
            **packet.payload,
            "_match_score_raw": score,
            "_match_score": round(adjusted, 4),
            "_match_score_display": round(display_score, 4),
            "_match_reasoning": reasoning,
            "_match_provenance": provenance,
            "_match_tier": tier,
            "_match_tier_cutoffs": self.tier_cutoffs.get(provenance, None),
            "_is_match": tier == "match",
        }
        if feedback_telemetry:
            payload["_feedback_adjustment"] = feedback_telemetry
        if title_delta:
            payload["_title_adjustment"] = round(title_delta, 4)
            payload["_title_reason"] = title_reason
        if location_delta:
            payload["_location_adjustment"] = round(location_delta, 4)
            payload["_location_reason"] = location_reason
        if salary_delta:
            payload["_salary_adjustment"] = round(salary_delta, 4)
            payload["_salary_reason"] = salary_reason
        if years_delta:
            payload["_years_adjustment"] = round(years_delta, 4)
            payload["_years_reason"] = years_reason

        # Dimensional sub-scores for transparency - independent of the base
        # score, purely derived from the structured profile. Skipped when
        # no structured profile is loaded.
        if self.profile_struct:
            try:
                dims = dim_scorer.score_dimensions(self.profile_struct, packet.payload)
                if dims:
                    payload["_dimensions"] = dims
            except Exception as e:
                logger.warning("Dimensional scoring failed for %s: %s",
                               packet.payload.get("title", "?"), e)

        # Ghost-job suspicion signals. Pure-Python, deterministic, always
        # emitted (no profile needed). title_index is threaded from run()
        # so the duplicate-title signal can fire across the cycle.
        try:
            fake = fake_detector.score_fake(
                packet.payload,
                title_index=title_index,
                threshold=self.fake_threshold,
            )
            payload["_fake"] = fake
            if fake.get("is_suspect"):
                payload["_is_suspect"] = True

            # Fold ghost-score into match score.
            #
            # `adjusted` so far is the fit score after title/location/salary/
            # years deltas. Now we penalise by ghost probability so a "92%
            # fit / 85% ghost" posting ranks below an "80% fit / 15% ghost"
            # one. Formula:
            #
            #     final = adjusted * (1 - ghost_weight * ghost_score)
            #
            # ghost_weight is configurable in config.match.ghost_weight,
            # default 0.35. A weight of 0 disables the fold entirely
            # (legacy behaviour, for users who want raw fit scoring).
            #
            # Display score is recomputed AFTER the fold so the calibrated
            # number the UI shows reflects the penalty. Both raw fit and
            # the ghost-adjusted value are kept in the payload so the
            # breakdown chip can show "Fit 92 × Ghost 85% → Score 65".
            ghost_weight = float(getattr(self, "ghost_weight", 0.35) or 0.0)
            ghost_weight = max(0.0, min(1.0, ghost_weight))
            ghost_score = float(fake.get("score") or 0.0)
            # Band label lets the UI show "suspicious" vs "aging" vs clean
            # without re-computing thresholds client-side. Deterministic,
            # cheap — attach regardless of whether the fold fires.
            flag_t = float(getattr(self, "ghost_flag_threshold",
                                    self.fake_threshold))
            warn_t = float(getattr(self, "ghost_warn_threshold", 0.30))
            if ghost_score >= flag_t:
                payload["_ghost_band"] = "flag"
            elif ghost_score >= warn_t:
                payload["_ghost_band"] = "warn"
            else:
                payload["_ghost_band"] = "clear"
            if ghost_weight > 0 and ghost_score > 0:
                penalty = 1.0 - (ghost_weight * ghost_score)
                penalty = max(0.0, min(1.0, penalty))
                pre_ghost = payload["_match_score"]
                folded = round(adjusted * penalty, 4)
                payload["_match_score_pre_ghost"] = pre_ghost
                payload["_match_score"] = folded
                payload["_ghost_penalty"] = round(1.0 - penalty, 4)
                payload["_ghost_weight"] = ghost_weight
                # Recompute display score off the folded value.
                payload["_match_score_display"] = round(
                    calibrate_score(folded) if provenance == "embed" else folded,
                    4,
                )
                # Re-tier: a post-ghost score may drop out of match/maybe.
                if provenance in self.tier_cutoffs:
                    cutoffs = self.tier_cutoffs[provenance]
                    if folded >= cutoffs["match"]:
                        payload["_match_tier"] = "match"
                        payload["_is_match"] = True
                    elif folded >= cutoffs["maybe"]:
                        payload["_match_tier"] = "maybe"
                        payload["_is_match"] = False
                    else:
                        payload["_match_tier"] = "none"
                        payload["_is_match"] = False

            # Learned-bonus pass: nudge the score up or down based on
            # how the user has historically engaged with this company
            # (applied / interviewed / rejected / passed). Bounded to
            # ±8% by set_company_signals so this layer never dominates
            # the ghost fold or the fit score.
            if self.company_signals:
                company_key = (packet.payload.get("company") or "").lower().strip()
                bonus = self.company_signals.get(company_key, 0.0)
                if bonus != 0.0:
                    base = payload["_match_score"]
                    boosted = round(max(0.0, min(1.0, base * (1.0 + bonus))), 4)
                    payload["_match_score_pre_learned"] = base
                    payload["_match_score"] = boosted
                    payload["_learned_bonus"] = round(bonus, 4)
                    payload["_match_score_display"] = round(
                        calibrate_score(boosted) if provenance == "embed" else boosted,
                        4,
                    )
        except Exception as e:
            logger.warning("Fake-detection failed for %s: %s",
                           packet.payload.get("title", "?"), e)

        return SentinelPacket(
            sender=Sender.MATCH,
            payload_type=PayloadType.VECTOR_SCORE,
            payload=payload,
            priority=Priority.HIGH if adjusted >= self.threshold else Priority.LOW,
            trace_id=packet.trace_id,
        )

    def run(self, valid_packets: list[SentinelPacket],
            on_scored=None) -> list[SentinelPacket]:
        """Score all valid job packets. Runs the hard location filter first.

        `on_scored(index, total, result, is_match)` is invoked after every
        scored packet so the orchestrator can flush matches to the registry
        incrementally (and update progress counters). The UI polls the
        registry, so this is what makes matches appear mid-cycle instead
        of only once scoring finishes."""
        results: list[SentinelPacket] = []
        dropped_location = 0
        dropped_details: list[str] = []
        dropped_country = 0
        country_details: list[str] = []

        logger.info("Matching %d jobs against profile", len(valid_packets))

        # Pre-pass: attach detected country to every payload so the UI
        # can surface it even for jobs that don't pass the filter. We
        # also run the hard country gate here so Bangalore/Mexico/Brazil
        # never reach the scoring stage.
        pre_filtered: list[SentinelPacket] = []
        for pkt in valid_packets:
            keep, reason, country = self.country_filter.evaluate(pkt.payload)
            # Attach on the payload in place; the match() call preserves
            # original payload fields into the scored packet.
            pkt.payload["_country"] = country or ""
            if not keep:
                dropped_country += 1
                if len(country_details) < 5:
                    country_details.append(
                        f"{pkt.payload.get('title','?')} @ {pkt.payload.get('company','?')} "
                        f"[{pkt.payload.get('location','?')}]: {reason}"
                    )
                continue
            pre_filtered.append(pkt)

        if dropped_country:
            logger.info("Country filter dropped %d/%d jobs. Examples: %s",
                        dropped_country, len(valid_packets),
                        " | ".join(country_details))

        to_score: list[SentinelPacket] = []
        if self.location_filter.active:
            for pkt in pre_filtered:
                keep, reason = self.location_filter.evaluate(pkt.payload)
                if keep:
                    to_score.append(pkt)
                else:
                    dropped_location += 1
                    # Record the first few for a log line without drowning the log.
                    if len(dropped_details) < 5:
                        dropped_details.append(
                            f"{pkt.payload.get('title','?')} @ {pkt.payload.get('company','?')} "
                            f"[{pkt.payload.get('location','?')}]: {reason}"
                        )
        else:
            to_score = list(pre_filtered)

        if dropped_location:
            logger.info("Location filter dropped %d/%d jobs. Examples: %s",
                        dropped_location, len(pre_filtered), " | ".join(dropped_details))

        # Experience hard filter runs after location so its reasons show up
        # in the log even when location would also have excluded the role.
        dropped_experience = 0
        experience_details: list[str] = []
        if self.experience_filter.active:
            next_round: list[SentinelPacket] = []
            for pkt in to_score:
                keep, reason = self.experience_filter.evaluate(pkt.payload)
                if keep:
                    next_round.append(pkt)
                else:
                    dropped_experience += 1
                    if len(experience_details) < 5:
                        experience_details.append(
                            f"{pkt.payload.get('title','?')} @ {pkt.payload.get('company','?')}: {reason}"
                        )
            to_score = next_round
            if dropped_experience:
                logger.info(
                    "Experience filter dropped %d jobs. Examples: %s",
                    dropped_experience, " | ".join(experience_details),
                )

        # Role-archetype gate. Drops titles that clearly don't match the
        # user's target archetype(s) BEFORE the LLM scorer runs. This is
        # a free pre-filter:
        #   - user targeting "Product Manager" → drop "Engineering Program Manager"
        #   - user targeting "Product Designer" → drop "Graphic Designer"
        # Gate is a no-op if the user hasn't set `target_archetypes` in
        # user.json. Config lives in `config/role_archetypes.yaml`.
        dropped_archetype = 0
        archetype_details: list[str] = []
        try:
            from core import role_archetypes as _archetypes
            target_arch_list = self.config.get("target_archetypes") or []
            arch_overrides = self.config.get("role_archetypes_override") or None
            archetypes_map = _archetypes.load_archetypes(extra=arch_overrides) if arch_overrides else _archetypes.load_archetypes()
            if target_arch_list and archetypes_map:
                next_round: list[SentinelPacket] = []
                for pkt in to_score:
                    allowed, reason = _archetypes.title_allowed(
                        pkt.payload.get("title", ""),
                        target_arch_list,
                        archetypes=archetypes_map,
                    )
                    if allowed:
                        next_round.append(pkt)
                    else:
                        dropped_archetype += 1
                        if len(archetype_details) < 5:
                            archetype_details.append(
                                f"{pkt.payload.get('title','?')} @ {pkt.payload.get('company','?')}: {reason}"
                            )
                to_score = next_round
                if dropped_archetype:
                    logger.info(
                        "Archetype filter dropped %d jobs. Examples: %s",
                        dropped_archetype, " | ".join(archetype_details),
                    )
        except Exception as e:
            # Fail open — never block a cycle because the gate errored.
            logger.warning("Archetype gate failed, allowing all: %s", e)

        # Shared per-cycle accumulator for the duplicate-title fake-job
        # signal. {company_lower: set[title_lower]}. Reset every cycle.
        title_index: dict[str, set[str]] = {}

        # Archetype classifier is imported lazily so a broken import
        # (missing Ollama, bad model config) never stops the main cycle.
        from agents.archetype import classify_archetype

        total = len(to_score)
        for i, pkt in enumerate(to_score):
            logger.info("Matching %d/%d: %s", i + 1, total, pkt.payload.get("title", "?"))
            result = self.match(pkt, title_index=title_index)

            # Attach an archetype bucket (PM / TPM / AI PM / ...) only on
            # matches and maybes. Classifying every scanned job would
            # double our per-job LLM cost for bucketing results we're
            # about to throw away. If classification fails, the call
            # returns archetype="unclassified" -- it never raises.
            tier = result.payload.get("_match_tier")
            if result.payload.get("_is_match") or tier == "maybe":
                arch = classify_archetype(
                    result.payload.get("title", ""),
                    result.payload.get("description", ""),
                    config_models=self.config.get("models") if hasattr(self, "config") else None,
                )
                result.payload["archetype"] = arch.get("archetype")
                result.payload["archetype_confidence"] = arch.get("confidence")
                result.payload["archetype_rationale"] = arch.get("rationale")

            results.append(result)
            if on_scored is not None:
                try:
                    on_scored(i + 1, total, result,
                              bool(result.payload.get("_is_match")))
                except Exception:
                    # Never let a progress callback kill a cycle.
                    logger.exception("on_scored callback raised; continuing")

        matches = [r for r in results if r.payload.get("_is_match")]
        maybes = [r for r in results if r.payload.get("_match_tier") == "maybe"]
        suspects = [r for r in results if r.payload.get("_is_suspect")]
        # Break down by provenance so the cutoffs shown in the log line up
        # with the decision actually made. embed and llm have different
        # score distributions and different cutoffs.
        embed_cut = self.tier_cutoffs["embed"]
        llm_cut = self.tier_cutoffs["llm"]
        logger.info(
            "Matching complete: %d match, %d maybe, %d scored. "
            "Cutoffs embed=%.2f/%.2f llm=%.2f/%.2f. "
            "Location dropped: %d. Experience dropped: %d. Ghost-job suspects: %d.",
            len(matches), len(maybes), len(results),
            embed_cut["match"], embed_cut["maybe"],
            llm_cut["match"], llm_cut["maybe"],
            dropped_location, dropped_experience, len(suspects),
        )
        return results
