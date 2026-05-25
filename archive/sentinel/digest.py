"""
WEEKLY DIGEST GENERATOR
Generates a natural-language summary using the local LLM,
then delivers it via email (SMTP) and Discord webhook.
"""

import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path

import requests

from core import llm

logger = logging.getLogger("sentinel.digest")

DIGEST_PROMPT = """You are a career intelligence analyst. Generate a concise weekly digest from this job search data. Write in a direct, analytical tone. No fluff.

TODAY'S DATE: {today}

DATA:
- Jobs discovered this cycle: {discovered}
- Jobs above match threshold: {matched}
- Top companies by volume: {top_companies}
- Most common skill gaps: {top_gaps}
- Source breakdown: {sources}
- Funnel metrics: {funnel}

Write a 150-word digest covering:
1. Volume and notable hiring velocity changes
2. Top matches worth applying to (company + title)
3. Skill gaps to prioritize closing
4. Source effectiveness
5. One actionable recommendation

Start with the exact line "SENTINEL Weekly Digest -- {today}" (no alteration - use the date exactly as provided). No markdown formatting. Do not invent a date.
"""


class DigestGenerator:
    def __init__(self, config: dict):
        self.model = config.get("model", "gemma4:26b")
        self.email_config = config.get("email", {})
        self.discord_webhook = config.get("discord_webhook", "")
        self.data_dir = Path(config.get("data_dir", "data"))
        self.digest_dir = self.data_dir / "digests"
        self.digest_dir.mkdir(parents=True, exist_ok=True)

    def generate_digest(self, cycle_stats: dict, tracker_data: dict,
                        fit_reports: list, matched_packets: list) -> str:
        """Generate digest text using the local LLM."""

        # Aggregate data for the prompt
        top_companies = {}
        top_gaps = []
        sources = {}

        for pkt in matched_packets:
            p = pkt.payload if hasattr(pkt, "payload") else pkt
            co = p.get("company", "Unknown")
            top_companies[co] = top_companies.get(co, 0) + 1
            src = p.get("_source", "unknown")
            sources[src] = sources.get(src, 0) + 1

        for r in fit_reports:
            top_gaps.extend(r.get("missing_skills", []))

        from collections import Counter
        gap_counts = Counter(g.lower().strip() for g in top_gaps)

        top_co_str = ", ".join(f"{k}({v})" for k, v in sorted(top_companies.items(), key=lambda x: -x[1])[:8])
        top_gaps_str = ", ".join(f"{g}({c})" for g, c in gap_counts.most_common(5))
        src_str = ", ".join(f"{k}: {v}" for k, v in sorted(sources.items(), key=lambda x: -x[1]))
        funnel_str = json.dumps(tracker_data.get("metrics", {}))

        # Pre-compute the date string once so the prompt and the
        # fallback can both reference it without drifting. Use local
        # time intentionally - the user cares about their calendar
        # date, not UTC.
        today_str = datetime.now().strftime("%B %d, %Y")
        prompt = DIGEST_PROMPT.format(
            today=today_str,
            discovered=cycle_stats.get("ingested", 0),
            matched=cycle_stats.get("matches", 0),
            top_companies=top_co_str or "N/A",
            top_gaps=top_gaps_str or "N/A",
            sources=src_str or "N/A",
            funnel=funnel_str,
        )

        try:
            digest_text = llm.query(prompt, task="digest", temperature=0.4)
        except Exception as e:
            logger.error("Failed to generate digest: %s", e)
            digest_text = self._fallback_digest(cycle_stats, top_co_str, top_gaps_str)

        # Belt-and-braces date hardening. Older gemma/qwen builds ignore the
        # "use today's date" instruction and fall back to their 2023 training
        # prior, so we can't trust whatever header the LLM emitted. Strip any
        # first-line date-like content it produced and force-prepend our
        # deterministic header. This is the only place that renders the date
        # the user sees in the UI, so fixing it here fixes every surface.
        digest_text = self._force_correct_header(digest_text, today_str)

        # Hallucination check on the BODY (not just the header). Small
        # local models happily write "Only one job exceeded the match
        # threshold, listed at Google" even when the data we handed them
        # never mentioned Google. Two cheap heuristics, any one fires →
        # fall back to the deterministic fallback digest, which is
        # ugly-but-honest:
        #   1. The body contains a year that isn't the current one. A
        #      stale training-prior year ("2023" in 2026) is the
        #      tell-tale sign of the model ignoring "TODAY'S DATE".
        #   2. The body mentions a company name that wasn't in the
        #      top_companies we actually sent. If the LLM invented
        #      "Google" or "Meta" out of thin air, we'd rather say
        #      nothing than name the wrong employer.
        real_companies = set(top_companies.keys())
        if self._looks_hallucinated(digest_text, today_str, real_companies):
            logger.warning("Digest body failed hallucination check; "
                           "using deterministic fallback")
            digest_text = self._fallback_digest(cycle_stats, top_co_str, top_gaps_str)
            digest_text = self._force_correct_header(digest_text, today_str)

        return digest_text

    @staticmethod
    def _looks_hallucinated(text: str, today_str: str, real_companies: set) -> bool:
        """Best-effort detector for the two ways small models fake this.

        Returns True if the body:
          - mentions a 4-digit year that isn't the current one, OR
          - names a well-known tech company that wasn't in our data.

        We keep the tech-company list short on purpose -- false positives
        cost us a digest; false negatives just mean a bad digest got
        through (which is the status quo). So this is a net improvement
        as long as the check is cheap and tight.
        """
        import re

        this_year = today_str.split()[-1]  # "April 24, 2026" -> "2026"
        # Any 4-digit year in 1990-2099 that isn't this year.
        for m in re.finditer(r"\b(19|20)\d{2}\b", text or ""):
            if m.group(0) != this_year:
                return True

        # Tech-company training-prior tell. Short list of the names
        # small models love to invent when they're guessing.
        famous = {"google", "meta", "facebook", "amazon", "apple",
                  "microsoft", "netflix", "openai", "anthropic", "tesla"}
        real_lower = {c.lower() for c in real_companies}
        body_lower = (text or "").lower()
        for name in famous:
            if name in body_lower and name not in real_lower:
                return True

        return False

    @staticmethod
    def _force_correct_header(text: str, today_str: str) -> str:
        """Replace the opening header + any stray date line with the real
        date. Runs even when the LLM got it right; the no-op case is cheap.
        """
        import re
        lines = (text or "").strip().splitlines()
        # Drop any leading header-ish lines the LLM produced. We're aggressive
        # because LLMs love to emit date variants: "October 26, 2023",
        # "2023-10-26", "Week of Oct 26", etc. Anything within the first two
        # lines that looks like either our header or a bare date gets nuked.
        header_pat = re.compile(r"(?i)^\s*sentinel\s+weekly\s+digest\b.*$")
        date_pat = re.compile(r"(?i)^\s*(week\s+of\s+)?[a-z]+\s+\d{1,2},?\s+\d{4}\s*$|^\s*\d{4}-\d{2}-\d{2}\s*$")
        while lines and (header_pat.match(lines[0]) or date_pat.match(lines[0]) or not lines[0].strip()):
            lines.pop(0)
        return f"SENTINEL Weekly Digest -- {today_str}\n\n" + "\n".join(lines).strip() + "\n"

    def _fallback_digest(self, stats, companies, gaps):
        return (
            f"SENTINEL Weekly Digest -- {datetime.now().strftime('%B %d, %Y')}\n\n"
            f"Pipeline cycle complete. {stats.get('ingested', 0)} cards ingested, "
            f"{stats.get('parsed', 0)} parsed, {stats.get('matches', 0)} matches found.\n\n"
            f"Top companies: {companies}\n"
            f"Top skill gaps: {gaps}\n"
        )

    def save_digest(self, text: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.digest_dir / f"digest_{ts}.txt"
        path.write_text(text)
        logger.info("Digest saved to %s", path)
        return path

    def send_discord(self, text: str):
        """Send digest to Discord webhook."""
        if not self.discord_webhook:
            logger.info("No Discord webhook configured, skipping.")
            return

        try:
            # Discord has 2000 char limit per message
            chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
            for chunk in chunks:
                resp = requests.post(
                    self.discord_webhook,
                    json={"content": f"```\n{chunk}\n```"},
                    timeout=10,
                )
                resp.raise_for_status()
            logger.info("Digest sent to Discord.")
        except Exception as e:
            logger.error("Discord send failed: %s", e)

    def send_email(self, text: str):
        """Send digest via SMTP (Gmail app password)."""
        cfg = self.email_config
        if not cfg.get("smtp_user"):
            logger.info("No email configured, skipping.")
            return

        try:
            msg = MIMEMultipart()
            msg["From"] = cfg["smtp_user"]
            msg["To"] = cfg.get("to", cfg["smtp_user"])
            msg["Subject"] = f"SENTINEL Digest -- {datetime.now().strftime('%b %d, %Y')}"
            msg.attach(MIMEText(text, "plain"))

            with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), cfg.get("smtp_port", 587)) as server:
                server.starttls()
                server.login(cfg["smtp_user"], cfg["smtp_pass"])
                server.send_message(msg)

            logger.info("Digest emailed to %s", msg["To"])
        except Exception as e:
            logger.error("Email send failed: %s", e)

    def run(self, cycle_stats, tracker_data, fit_reports, matched_packets):
        """Generate, save, and deliver the digest."""
        text = self.generate_digest(cycle_stats, tracker_data, fit_reports, matched_packets)
        self.save_digest(text)
        self.send_discord(text)
        self.send_email(text)
        return text
