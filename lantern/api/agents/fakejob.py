"""
FAKE JOB DETECTOR
Flags suspicious job listings using heuristic rules + LLM verification.
Catches: ghost jobs, scam postings, recruiter spam, and vague listings.
"""

import logging
import re
from core.protocol import SentinelPacket, PayloadType, Priority, Sender

logger = logging.getLogger("lantern.fakejob")

# ─── Heuristic red flags ──────────────────────────────────────────
SCAM_PHRASES = [
    "wire transfer", "western union", "money order", "pay upfront",
    "processing fee", "background check fee", "training fee",
    "send money", "wire funds", "personal bank",
    "guaranteed income", "unlimited earning", "get rich",
    "no experience necessary", "no skills required",
    "work from home $", "earn $ per", "make money fast",
]

GHOST_JOB_SIGNALS = [
    "always hiring", "continuous posting", "evergreen",
    "talent pool", "future opportunities", "pipeline role",
    "general application", "talent community",
]

VAGUE_TITLE_PATTERNS = [
    r"^(job|position|role|opening|opportunity)$",
    r"^various\s",
    r"^multiple\s",
    r"\bTBD\b",
    r"\bvarious\b",
]


class FakeJobDetector:
    """Detects fake, ghost, or suspicious job listings."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.enabled = self.config.get("enable_fake_job_detection", True)

    def check(self, payload: dict) -> dict:
        """
        Run all checks on a job payload.
        Returns: {"is_suspicious": bool, "flags": [...], "risk_level": "low"|"medium"|"high"}
        """
        if not self.enabled:
            return {"is_suspicious": False, "flags": [], "risk_level": "low"}

        flags = []
        # Coerce with str() before .lower(): ingest packets are NOT
        # type-guaranteed. The salary extractor stores a structured dict
        # ({min, max, ...}), and some ATS APIs hand back nested
        # location/salary objects. A fake-job *detector* must never hard-
        # crash the whole pipeline over a field type — it only scans text
        # for scam markers, so a stringified dict is harmless noise.
        title = str(payload.get("title") or "").lower()
        company = str(payload.get("company") or "").lower()
        desc = str(payload.get("description") or "").lower()
        location = str(payload.get("location") or "").lower()
        url = str(payload.get("url") or "").lower()
        salary = str(payload.get("salary") or "").lower()

        # ── Scam detection ──
        full_text = f"{title} {desc} {salary}"
        for phrase in SCAM_PHRASES:
            if phrase in full_text:
                flags.append(f"SCAM_PHRASE: '{phrase}' found in listing")

        # ── Ghost job detection ──
        for signal in GHOST_JOB_SIGNALS:
            if signal in full_text:
                flags.append(f"GHOST_JOB: '{signal}' suggests this may not be a real opening")

        # ── Vague title ──
        for pattern in VAGUE_TITLE_PATTERNS:
            if re.search(pattern, title):
                flags.append(f"VAGUE_TITLE: title '{payload.get('title')}' is too generic")
                break

        # ── Missing company ──
        if not company or company in ("n/a", "none", "unknown", "confidential", ""):
            flags.append("NO_COMPANY: company name is missing or hidden")

        # ── Suspicious salary ──
        if salary:
            # Unrealistically high
            numbers = re.findall(r"[\d,]+", salary.replace(",", ""))
            for n in numbers:
                try:
                    val = int(n)
                    if val > 500000:
                        flags.append(f"SUSPICIOUS_SALARY: ${val:,} is unrealistically high")
                except ValueError:
                    pass

        # ── Description quality ──
        if desc and len(desc) < 50:
            flags.append("SHORT_DESCRIPTION: listing has almost no detail")

        if not desc:
            flags.append("NO_DESCRIPTION: listing has no description at all")

        # ── Suspicious URL patterns ──
        suspicious_domains = ["bit.ly", "tinyurl", "t.co", "goo.gl"]
        for domain in suspicious_domains:
            if domain in url:
                flags.append(f"SHORTENED_URL: listing uses {domain} which may hide the real destination")

        # ── Email in description (common in scams) ──
        email_pattern = r"[a-zA-Z0-9._%+-]+@(gmail|yahoo|hotmail|outlook)\.(com|net|org)"
        if re.search(email_pattern, desc):
            flags.append("PERSONAL_EMAIL: listing contains a personal email address (common in scams)")

        # ── Risk assessment ──
        scam_flags = sum(1 for f in flags if f.startswith("SCAM"))
        ghost_flags = sum(1 for f in flags if f.startswith("GHOST"))
        other_flags = len(flags) - scam_flags - ghost_flags

        if scam_flags >= 1:
            risk = "high"
        elif ghost_flags >= 2 or (ghost_flags >= 1 and other_flags >= 2):
            risk = "high"
        elif len(flags) >= 3:
            risk = "medium"
        elif len(flags) >= 1:
            risk = "medium" if scam_flags else "low"
        else:
            risk = "low"

        return {
            "is_suspicious": len(flags) > 0,
            "flags": flags,
            "risk_level": risk,
            "flag_count": len(flags),
        }

    def filter_packets(self, packets: list[SentinelPacket]) -> tuple[list[SentinelPacket], list[dict]]:
        """
        Check all packets. Returns (clean_packets, flagged_reports).
        High-risk jobs are removed. Medium-risk are kept but flagged.
        """
        clean = []
        flagged = []

        for pkt in packets:
            if pkt.payload_type != PayloadType.JSON_JOB:
                clean.append(pkt)
                continue

            result = self.check(pkt.payload)

            if result["risk_level"] == "high":
                flagged.append({
                    "title": pkt.payload.get("title"),
                    "company": pkt.payload.get("company"),
                    "action": "removed",
                    **result,
                })
                logger.warning(
                    "BLOCKED: %s @ %s [%s] %s",
                    pkt.payload.get("title"), pkt.payload.get("company"),
                    result["risk_level"], "; ".join(result["flags"]),
                )
            elif result["risk_level"] == "medium":
                pkt.payload["_fake_job_flags"] = result["flags"]
                pkt.payload["_fake_job_risk"] = "medium"
                clean.append(pkt)
                flagged.append({
                    "title": pkt.payload.get("title"),
                    "company": pkt.payload.get("company"),
                    "action": "flagged",
                    **result,
                })
                logger.info(
                    "FLAGGED: %s @ %s [medium] %s",
                    pkt.payload.get("title"), pkt.payload.get("company"),
                    "; ".join(result["flags"]),
                )
            else:
                clean.append(pkt)

        blocked = sum(1 for f in flagged if f["action"] == "removed")
        warned = sum(1 for f in flagged if f["action"] == "flagged")
        logger.info(
            "Fake job check: %d clean, %d blocked, %d flagged",
            len(clean), blocked, warned,
        )

        return clean, flagged
