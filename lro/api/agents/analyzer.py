"""
FIT-GAP ANALYZER
One-sentence fit verdict + light skill tags for top match-tier jobs.
"""

import logging
from pathlib import Path
from typing import Optional, Union

from core.protocol import SentinelPacket
from core import llm

logger = logging.getLogger("lro.analyzer")

FIT_GAP_PROMPT = """Compare this candidate to the job. Be direct and specific.

PROFILE:
{profile}

JOB:
{signature}

Respond with ONLY JSON (no markdown):
{{
  "fit_summary": "<one sentence: strongest fit reason OR main gap>",
  "matched_skills": ["up to 3 skills they already have"],
  "missing_skills": ["up to 3 gaps worth noting"],
  "match_percentage": <int 0-100>
}}
"""


class FitGapAnalyzer:
    """Generates fit-gap reports for matched jobs."""

    def __init__(self, config: dict, data_dir: Optional[Union[str, Path]] = None):
        self.profile = config.get("profile_text", "")
        self.model = config.get("model", "qwen3:8b")
        self.data_dir = Path(data_dir) if data_dir else None

    def set_profile(self, text: str):
        """Swap the candidate profile between cycles (resume upload/clear)."""
        self.profile = text or ""

    def analyze(self, packet: SentinelPacket) -> dict:
        """Analyze a single matched job packet. Returns the fit-gap report dict."""
        payload = packet.payload
        title = payload.get("title", "N/A")
        company = payload.get("company", "N/A")

        from core.job_signature import build_job_signature
        signature = (
            payload.get("job_signature")
            or build_job_signature(payload)
            or f"{title} @ {company}"
        )

        prompt = FIT_GAP_PROMPT.format(
            profile=self.profile[:4000],
            signature=signature,
        )

        try:
            result = llm.query_json(prompt, task="analyze")
            if result.get("_parse_error"):
                logger.warning("LLM returned unparseable fit-gap for %s @ %s", title, company)
                return {"error": "parse_failed", "title": title, "company": company}

            result["title"] = title
            result["company"] = company
            result["url"] = payload.get("url", "")
            result["match_score"] = payload.get("_match_score", 0)
            return result

        except Exception as e:
            logger.error("Fit-gap analysis failed for %s @ %s: %s", title, company, e)
            return {"error": str(e), "title": title, "company": company}

    def run(self, matched_packets: list[SentinelPacket]) -> list[dict]:
        """Run fit-gap analysis on all matched jobs."""
        reports = []
        matches = [p for p in matched_packets if p.payload.get("_is_match")]

        if not matches:
            logger.info("No matches to analyze for fit-gap.")
            return reports

        if not llm.task_llm_ready("analyze", explicit_model=self.model):
            llm.log_skip_once(
                "analyze_no_model",
                "Skipping fit-gap analysis on %d match(es): analyze model '%s' not pulled. "
                "Run `ollama pull qwen3:8b`.",
                len(matches),
                self.model or llm.get_model("analyze"),
            )
            return reports

        logger.info("Running fit-gap analysis on %d matched jobs", len(matches))

        for i, pkt in enumerate(matches):
            logger.info("Analyzing %d/%d: %s @ %s", i + 1, len(matches),
                        pkt.payload.get("title"), pkt.payload.get("company"))
            report = self.analyze(pkt)
            reports.append(report)

        avg_match = sum(
            r.get("match_percentage", 0) for r in reports if "error" not in r
        ) / max(len(reports), 1)
        logger.info("Fit-gap complete. Avg match: %.0f%%", avg_match)

        return reports
