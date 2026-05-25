"""
FIT-GAP ANALYZER
Maps your resume bullets against each JD's requirements.
Outputs: matched skills, gaps, study recommendations.
"""

import logging
from pathlib import Path
from typing import Optional, Union

from core.protocol import SentinelPacket, PayloadType
from core import llm
from core import story_bank
from core import star_writer

logger = logging.getLogger("sentinel.analyzer")

FIT_GAP_PROMPT = """You are a career fit analyzer. Compare this candidate profile against the job listing and produce a structured fit-gap analysis.

CANDIDATE PROFILE:
{profile}

JOB LISTING:
Title: {title}
Company: {company}
Description: {description}
Technologies: {technologies}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill3", "skill4"],
  "match_percentage": <int 0-100>,
  "fit_summary": "<one sentence overall fit assessment>",
  "gaps": [
    {{"skill": "skill name", "severity": "critical|moderate|minor", "mitigation": "how to close this gap quickly"}}
  ],
  "talking_points": ["<strength to highlight in interview>", "<another strength>"],
  "study_recommendations": ["<specific thing to learn before applying>"]
}}
"""


class FitGapAnalyzer:
    """Generates fit-gap reports for matched jobs."""

    def __init__(self, config: dict, data_dir: Optional[Union[str, Path]] = None):
        self.profile = config.get("profile_text", "")
        self.model = config.get("model", "qwen3:8b")
        # Where to write the running STAR+R story bank. None disables
        # bank writes (tests, preview mode).
        self.data_dir = Path(data_dir) if data_dir else None

    def set_profile(self, text: str):
        """Swap the candidate profile between cycles (resume upload/clear)."""
        self.profile = text or ""

    def analyze(self, packet: SentinelPacket) -> dict:
        """Analyze a single matched job packet. Returns the fit-gap report dict."""
        payload = packet.payload
        title = payload.get("title", "N/A")
        company = payload.get("company", "N/A")

        prompt = FIT_GAP_PROMPT.format(
            profile=self.profile,
            title=title,
            company=company,
            description=payload.get("description", "N/A"),
            technologies=", ".join(payload.get("technologies", [])) or "Not specified",
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

            # Append a proper STAR+R block to the running story bank.
            # We first run the bullet rewriter so each talking point
            # lands as a full 4-slot bullet (S/T, A, R, Reflection)
            # rather than a bare "A:" stub. Failures in either step
            # degrade gracefully -- star_writer falls back to raw
            # points, story_bank catches every exception.
            if self.data_dir is not None:
                star_bullets = star_writer.rewrite_to_star(
                    result.get("talking_points") or [],
                    title=title,
                    company=company,
                    archetype=payload.get("archetype", ""),
                )
                story_bank.append_stories(
                    data_dir=self.data_dir,
                    job=payload,
                    analysis=result,
                    bullets=star_bullets,
                )

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

        logger.info("Running fit-gap analysis on %d matched jobs", len(matches))

        for i, pkt in enumerate(matches):
            logger.info("Analyzing %d/%d: %s @ %s", i + 1, len(matches),
                        pkt.payload.get("title"), pkt.payload.get("company"))
            report = self.analyze(pkt)
            reports.append(report)

        # Summary stats
        avg_match = sum(r.get("match_percentage", 0) for r in reports if "error" not in r) / max(len(reports), 1)
        all_gaps = []
        for r in reports:
            all_gaps.extend(r.get("missing_skills", []))

        # Find most common gaps
        from collections import Counter
        gap_counts = Counter(g.lower().strip() for g in all_gaps)
        top_gaps = gap_counts.most_common(5)

        logger.info("Fit-gap complete. Avg match: %.0f%%. Top gaps: %s",
                     avg_match, ", ".join(f"{g}({c})" for g, c in top_gaps))

        return reports
