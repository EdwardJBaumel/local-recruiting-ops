"""
[QA-AGENT]: Validates parsed job packets.
Checks required fields, flags bad data, triggers self-healing on critical errors.
"""

import logging
from core.protocol import SentinelPacket, Sender, PayloadType, Priority

logger = logging.getLogger("lantern.qa")

REQUIRED_FIELDS = ["title", "company"]
DESIRED_FIELDS = ["location", "description", "technologies", "seniority"]


class QAAgent:
    """Validates JSON_JOB packets and flags issues."""

    def __init__(self, config: dict = None):
        self.stats = {"passed": 0, "warned": 0, "failed": 0}

    def validate(self, packet: SentinelPacket) -> tuple[bool, list[str]]:
        """Returns (is_valid, list_of_issues)."""
        if packet.payload_type != PayloadType.JSON_JOB:
            return False, ["Not a JSON_JOB packet"]

        issues = []
        data = packet.payload

        # Check required fields
        for field in REQUIRED_FIELDS:
            val = data.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                issues.append(f"MISSING_REQUIRED: {field}")

        # Check desired fields
        for field in DESIRED_FIELDS:
            val = data.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                issues.append(f"MISSING_DESIRED: {field}")

        # Check for obviously bogus data
        title = data.get("title", "") or ""
        if len(title) < 3:
            issues.append("SUSPECT: title too short")
        if len(title) > 200:
            issues.append("SUSPECT: title too long")

        has_critical = any("MISSING_REQUIRED" in i for i in issues)
        return (not has_critical), issues

    def run(self, packets: list[SentinelPacket]) -> tuple[list[SentinelPacket], list[SentinelPacket]]:
        """Validate all JSON_JOB packets. Returns (valid_packets, error_packets)."""
        valid = []
        errors = []

        job_packets = [p for p in packets if p.payload_type == PayloadType.JSON_JOB]
        logger.info("QA validating %d job packets", len(job_packets))

        # Per-job issues are tallied, not logged line-by-line. A cycle
        # routinely warns on 100+ jobs (several sources ship cards
        # without a JD body), and one WARNING per job buried the
        # console. We emit a single rolled-up summary below; the
        # per-job detail stays at DEBUG for when it's actually needed.
        warn_counts: dict[str, int] = {}

        for pkt in job_packets:
            is_valid, issues = self.validate(pkt)

            if is_valid and not any("MISSING_DESIRED" in i for i in issues):
                self.stats["passed"] += 1
                valid.append(pkt)
                logger.debug("PASS: %s at %s", pkt.payload.get("title"), pkt.payload.get("company"))

            elif is_valid:
                self.stats["warned"] += 1
                valid.append(pkt)
                for issue in issues:
                    warn_counts[issue] = warn_counts.get(issue, 0) + 1
                logger.debug("WARN: %s - %s", pkt.payload.get("title"), "; ".join(issues))

            else:
                self.stats["failed"] += 1
                err = SentinelPacket(
                    sender=Sender.QA,
                    payload_type=PayloadType.ERROR_LOG,
                    payload={
                        "original_payload": pkt.payload,
                        "issues": issues,
                        "source_url": pkt.payload.get("_source_url"),
                    },
                    priority=Priority.CRITICAL,
                    trace_id=pkt.trace_id,
                )
                errors.append(err)
                logger.error("FAIL: %s", "; ".join(issues))

        logger.info(
            "QA results: %d passed, %d warned, %d failed",
            self.stats["passed"],
            self.stats["warned"],
            self.stats["failed"],
        )
        if warn_counts:
            breakdown = ", ".join(
                f"{count}x {issue}"
                for issue, count in sorted(
                    warn_counts.items(), key=lambda kv: kv[1], reverse=True
                )
            )
            logger.info(
                "QA warnings (non-blocking — jobs still pass to matching): %s",
                breakdown,
            )
        return valid, errors
