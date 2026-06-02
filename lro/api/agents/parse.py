"""
[MOD-PARSE]: The LLM-Parser
Takes RAW_HTML packets, sends them to the local LLM, outputs JSON_JOB packets.
"""

import logging
import requests
from core.protocol import SentinelPacket, Sender, PayloadType, Priority
from core import llm
from core import text_clean

logger = logging.getLogger("lro.parse")

# Circuit breaker. If this many Ollama calls in a row raise Timeout /
# ConnectionError, abort the parse stage instead of grinding through
# 60 more cards at 120s each (i.e. a 2 hour hang). The remaining
# packets fall through as ERROR_LOG so downstream stages keep running
# on whatever parsed cleanly. The streak resets on any successful call.
CONSECUTIVE_FAILURE_LIMIT = 4

# Max chars of cleaned HTML we send to the LLM. 8000 was the original
# budget but parse is pure extraction, not reasoning - a shorter prompt
# cuts generation time significantly and is still plenty of context
# for a single job card. qwen2.5:3b runs this in ~5s on CPU.
PARSE_PROMPT_BUDGET = 4000

EXTRACTION_PROMPT = """You are a job listing data extractor. Given the following text extracted from a job board, extract structured data.

Respond with ONLY a JSON object (no markdown, no explanation) with these keys:
- "title": job title (string or null). Must be a human-readable role name, never a recruiter tracking code like "RDQ226R484".
- "company": company name (string or null)
- "location": job location (string or null)
- "salary": salary info if present (string or null)
- "description": brief description/summary, max 200 words (string or null)
- "technologies": list of technologies/skills mentioned (array of strings)
- "seniority": one of "junior", "mid", "senior", "lead", "manager", "director", "unknown"
- "job_type": one of "full-time", "part-time", "contract", "intern", "unknown"
- "remote": one of "remote", "hybrid", "onsite", "unknown"
- "url": direct link to the job if present (string or null)

If a field cannot be determined, use null. Do not invent data. Do not include any HTML tags or entities in string values.

Text:
{html}
"""


class ParseAgent:
    """Parses raw HTML into structured job JSON using the local LLM."""

    def __init__(self, config: dict):
        self.model = config.get("model", "qwen3:8b")

    def parse(self, packet: SentinelPacket) -> SentinelPacket:
        """Take a RAW_HTML packet, return a JSON_JOB packet."""
        html = packet.payload.get("html", "")
        source_url = packet.payload.get("source_url", "")

        # Convert the raw HTML into plain text before the LLM sees it.
        # Historical bug: we sent `str(el)` straight through, so script
        # tags, `data-pm-slice` attributes, and recruiter tracking codes
        # like "RDQ226R484" burned tokens and occasionally surfaced as
        # the extracted title. text_clean.clean_for_llm strips tags,
        # drops tracking codes, collapses whitespace, then truncates to
        # the same 8000-char budget.
        cleaned = text_clean.clean_for_llm(html, max_chars=PARSE_PROMPT_BUDGET)

        prompt = EXTRACTION_PROMPT.format(html=cleaned)

        try:
            job_data = llm.query_json(prompt, task="parse")

            if job_data.get("_parse_error"):
                logger.warning("LLM returned unparseable response for card from %s", source_url)
                return SentinelPacket(
                    sender=Sender.PARSE,
                    payload_type=PayloadType.ERROR_LOG,
                    payload={
                        "error": "LLM response not valid JSON",
                        "raw_response": job_data.get("_raw", "")[:500],
                        "source_url": source_url,
                    },
                    priority=Priority.HIGH,
                    trace_id=packet.trace_id,
                )

            # Post-parse sanitiser: unescape HTML entities, strip any
            # stray tags the LLM copied in, null out title/company that
            # came back as bare tracking codes, dedupe tech list.
            job_data = text_clean.sanitise_job(job_data)

            from core.job_signature import attach_job_signature
            attach_job_signature(job_data)

            # Attach source metadata
            job_data["_source_url"] = source_url
            job_data["_card_index"] = packet.payload.get("card_index")

            # Per-job URL fallback: when the LLM returned a null URL
            # (which happens whenever the cleaned HTML had its <a href>
            # stripped — e.g. Google careers cards), use the deterministic
            # hint the fetcher attached to the packet. Without this the
            # registry stored url=None for every Google row, which made
            # multiple rows share the same selection key and broke the
            # detail panel + Apply button.
            if not job_data.get("url"):
                hint = packet.payload.get("_url_hint")
                if hint:
                    job_data["url"] = hint

            return SentinelPacket(
                sender=Sender.PARSE,
                payload_type=PayloadType.JSON_JOB,
                payload=job_data,
                priority=Priority.MED,
                trace_id=packet.trace_id,
            )

        except Exception as e:
            logger.error("Parse failed: %s", e)
            return SentinelPacket(
                sender=Sender.PARSE,
                payload_type=PayloadType.ERROR_LOG,
                payload={"error": str(e), "source_url": source_url},
                priority=Priority.CRITICAL,
                trace_id=packet.trace_id,
            )

    def run(self, packets: list[SentinelPacket]) -> list[SentinelPacket]:
        """Parse all RAW_HTML packets.

        Circuit-broken: once CONSECUTIVE_FAILURE_LIMIT Ollama calls in a
        row raise Timeout/ConnectionError, the remaining packets are
        short-circuited into ERROR_LOG so we do not wait another hour
        for an Ollama that is obviously wedged. Any successful parse
        resets the streak.
        """
        results = []
        html_packets = [p for p in packets if p.payload_type == PayloadType.RAW_HTML]
        if not html_packets:
            return results

        if not llm.task_llm_ready("parse", explicit_model=self.model):
            llm.log_skip_once(
                "parse_no_model",
                "Skipping PARSE on %d HTML card(s): parse model '%s' not pulled. "
                "ATS jobs (Greenhouse/Lever/Amazon) are unaffected. "
                "Run `ollama pull qwen3:8b` or disable Google in Settings.",
                len(html_packets),
                self.model or llm.get_model("parse"),
            )
            return results

        logger.info("Parsing %d HTML packets", len(html_packets))

        consecutive_failures = 0
        broke_circuit_at = None

        for i, pkt in enumerate(html_packets):
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                if broke_circuit_at is None:
                    broke_circuit_at = i
                    logger.error(
                        "Parse circuit broken after %d consecutive Ollama failures. "
                        "Short-circuiting remaining %d cards. Check `ollama serve`, "
                        "the configured parse model, and GPU/CPU load.",
                        CONSECUTIVE_FAILURE_LIMIT, len(html_packets) - i,
                    )
                results.append(SentinelPacket(
                    sender=Sender.PARSE,
                    payload_type=PayloadType.ERROR_LOG,
                    payload={
                        "error": "parse_circuit_broken",
                        "source_url": pkt.payload.get("source_url", ""),
                    },
                    priority=Priority.CRITICAL,
                    trace_id=pkt.trace_id,
                ))
                continue

            logger.info("Parsing card %d/%d", i + 1, len(html_packets))
            try:
                result = self.parse(pkt)
            except (requests.Timeout, requests.ConnectionError) as e:
                # parse() normally catches these and returns ERROR_LOG, but
                # keep the belt-and-braces branch so the counter updates
                # even if the contract ever changes.
                consecutive_failures += 1
                logger.warning("Parse %d failed with %s (streak %d/%d)",
                               i + 1, type(e).__name__, consecutive_failures,
                               CONSECUTIVE_FAILURE_LIMIT)
                results.append(SentinelPacket(
                    sender=Sender.PARSE, payload_type=PayloadType.ERROR_LOG,
                    payload={"error": str(e),
                             "source_url": pkt.payload.get("source_url", "")},
                    priority=Priority.HIGH, trace_id=pkt.trace_id,
                ))
                continue

            results.append(result)

            if result.payload_type == PayloadType.JSON_JOB:
                consecutive_failures = 0
            elif result.payload_type == PayloadType.ERROR_LOG:
                err = str(result.payload.get("error", "")).lower()
                # Only count transport-level failures toward the streak.
                # JSON-parse errors or bad LLM output are structural and
                # shouldn't trip the breaker.
                if "timeout" in err or "timed out" in err or "connection" in err:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

        job_count = sum(1 for r in results if r.payload_type == PayloadType.JSON_JOB)
        err_count = sum(1 for r in results if r.payload_type == PayloadType.ERROR_LOG)
        if broke_circuit_at is not None:
            logger.info("Parse complete: %d jobs, %d errors (circuit broke at card %d)",
                        job_count, err_count, broke_circuit_at + 1)
        else:
            logger.info("Parse complete: %d jobs, %d errors", job_count, err_count)
        return results
