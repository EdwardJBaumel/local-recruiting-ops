"""
CROSS-ATS DEDUPE

Inside a single cycle the same role can appear on multiple ATS boards -
the engineering leadership role on Netflix shows up on Lever, their
careers-page JSON blob, and sometimes a third source. Before this
module each source produced its own packet and they all fell into
`seen_jobs` one at a time, which meant:

  a. We double-counted job volume in market intel.
  b. Only one source survived to the UI; users lost the signal that the
     role is cross-listed (useful when one board is broken).
  c. Occasionally the *less complete* payload won the dedupe race because
     ingest order isn't stable.

This module aggregates by a normalised key (company, title, location)
and returns one merged packet per unique role with a new
`_provenance` list that lists every source that posted the role.

Called from the orchestrator right after fake-detection and before the
cross-cycle seen_jobs dedupe, so downstream stages see one packet per
role and `seen_jobs` gets a stable key.
"""
from __future__ import annotations

import re
import logging
from collections import OrderedDict

logger = logging.getLogger("lro.dedupe")

# Strip punctuation + collapse whitespace so "Sr. Product Manager, AI"
# and "Senior Product Manager AI" key to the same bucket where
# reasonable. We don't try too hard - too-aggressive normalisation
# accidentally merges "Product Manager, Growth" with "Product Manager,
# Core" which is worse than a rare dupe.
_TITLE_STRIP_RE = re.compile(r"[\(\)\[\],\./]+")
_WS_RE = re.compile(r"\s+")
_TITLE_ALIAS = {
    "sr.": "senior", "sr": "senior", "jr.": "junior", "jr": "junior",
    "tpm": "technical program manager",
    "pm": "product manager",
}


def _normalise_title(s: str) -> str:
    s = (s or "").lower()
    s = _TITLE_STRIP_RE.sub(" ", s)
    tokens = _WS_RE.sub(" ", s).strip().split()
    tokens = [_TITLE_ALIAS.get(tok, tok) for tok in tokens]
    return " ".join(tokens)


def _normalise_location(s: str) -> str:
    """Location strings are the most scrambled field - every ATS has its
    own format ("Remote - US", "United States (Remote)", "Remote/Global").
    Collapse to a coarse shape so two packets for the same role don't
    lose to trivial formatting differences."""
    s = (s or "").lower()
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""
    if "remote" in s:
        return "remote"
    # Drop trailing country names that some boards append and others don't.
    for suffix in (", united states", ", usa", ", us", ", uk", ", ca"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def _dedupe_key(payload: dict) -> str:
    co = (payload.get("company") or "").strip().lower()
    title = _normalise_title(payload.get("title") or "")
    loc = _normalise_location(payload.get("location") or "")
    return f"{co}||{title}||{loc}"


def _score_completeness(payload: dict) -> int:
    """Rough 'how useful is this payload?' score. Higher = keep. Used to
    pick which of several cross-listed packets becomes the primary."""
    score = 0
    if payload.get("description"): score += len((payload["description"]) or "") // 200
    if payload.get("technologies"): score += len(payload["technologies"])
    if payload.get("seniority") and payload["seniority"] != "unknown": score += 2
    if payload.get("remote") and payload["remote"] != "unknown": score += 1
    if payload.get("posted_date"): score += 1
    if payload.get("salary_min") or payload.get("salary_max"): score += 3
    return score


def _merge(primary: dict, secondary: dict) -> dict:
    """Fold any fields `primary` is missing from `secondary`. Non-empty
    values on `primary` always win; only its blanks get filled in. This
    keeps downstream code's expectations intact (everyone reads from the
    primary payload) while recovering data that would otherwise be lost."""
    out = dict(primary)
    for key, val in secondary.items():
        if key.startswith("_"):
            continue
        if val in (None, "", [], {}):
            continue
        if out.get(key) in (None, "", [], {}):
            out[key] = val
    return out


def dedupe_packets(packets):
    """Merge cross-ATS duplicates within a list of SentinelPacket objects.

    Returns (merged_packets, stats) where stats is a small summary dict
    the orchestrator can log + surface on the dashboard:
        {
          "input": int,
          "unique": int,
          "merged": int,       # count of packets folded into a primary
          "cross_listed_pct": float,
        }
    Packet identity is preserved for the primary - we copy its object
    and only mutate `.payload._provenance`. Never modifies the inputs.
    """
    groups: OrderedDict[str, list] = OrderedDict()
    for pkt in packets:
        key = _dedupe_key(pkt.payload)
        groups.setdefault(key, []).append(pkt)

    merged = []
    folded = 0
    cross_listed = 0
    for key, group in groups.items():
        if len(group) == 1:
            # Still set _provenance so downstream code can always rely on
            # the field being present. One-source packets get a single-
            # element list.
            pkt = group[0]
            src = pkt.payload.get("_source") or ""
            if src:
                pkt.payload["_provenance"] = [src]
            merged.append(pkt)
            continue

        cross_listed += 1
        folded += len(group) - 1

        # Pick the most complete packet as primary.
        group_sorted = sorted(group, key=lambda p: _score_completeness(p.payload), reverse=True)
        primary = group_sorted[0]
        primary_payload = dict(primary.payload)
        provenance = []
        for pkt in group_sorted:
            src = (pkt.payload.get("_source") or "").strip()
            if src and src not in provenance:
                provenance.append(src)
            # Fold in any missing fields.
            primary_payload = _merge(primary_payload, pkt.payload)

        primary_payload["_provenance"] = provenance
        # Keep the original packet object (preserves trace_id etc) but
        # swap in the merged payload.
        primary.payload = primary_payload
        merged.append(primary)
        logger.debug("Dedup merge: %s -> %d sources: %s",
                     primary_payload.get("title"), len(provenance), provenance)

    total = len(packets) or 1
    stats = {
        "input": len(packets),
        "unique": len(merged),
        "merged": folded,
        "cross_listed_pct": round((cross_listed / total) * 100, 1) if cross_listed else 0.0,
    }
    return merged, stats
