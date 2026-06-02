#!/usr/bin/env python3
"""Score math report: registry distribution + cross-encoder rerank simulation."""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from core.cross_encoder_rerank import CrossEncoderReranker, normalize_rerank_scores
from core.job_signature import build_job_signature
from core.resume_store import get_profile_text
from agents.match import calibrate_score


def score_of(entry: dict) -> float:
    p = entry.get("payload") or {}
    return float(p.get("_match_score_display") or p.get("_match_score") or 0)


def raw_of(entry: dict) -> float:
    p = entry.get("payload") or {}
    return float(p.get("_match_score") or p.get("_match_score_raw") or 0)


def main():
    data = API_ROOT / "data"
    reg_path = data / "match_registry.json"
    if not reg_path.exists():
        print(f"No registry at {reg_path}")
        sys.exit(1)

    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    entries = list(reg.get("entries", {}).values())
    print("=" * 72)
    print("LRO SCORE REPORT")
    print("=" * 72)
    print(f"Registry entries: {len(entries)}")

    scores = [score_of(e) for e in entries if score_of(e) > 0]
    raw_scores = [raw_of(e) for e in entries if raw_of(e) > 0]
    print(f"Rows with score > 0: {len(scores)}")
    if scores:
        s = sorted(scores)
        print("\nDisplay score percentiles (_match_score_display, calibrated):")
        for pct in (10, 25, 50, 75, 90, 95):
            idx = max(0, min(int(len(s) * pct / 100) - 1, len(s) - 1))
            print(f"  p{pct:>2}: {s[idx]:6.1%}")
        print(f"  mean: {statistics.mean(scores):6.1%}   stdev: {statistics.stdev(scores):.3f}")

    if raw_scores:
        r = sorted(raw_scores)
        print("\nRaw score percentiles (_match_score, pre-calibration):")
        for pct in (10, 25, 50, 75, 90, 95):
            idx = max(0, min(int(len(r) * pct / 100) - 1, len(r) - 1))
            print(f"  p{pct:>2}: {r[idx]:.3f}")
        print(f"  mean: {statistics.mean(raw_scores):.3f}")

    tiers = Counter((e.get("payload") or {}).get("_match_tier", "?") for e in entries)
    print("\nTier counts:", dict(tiers))

    starred = [e for e in entries if e.get("starred")]
    dismissed = [e for e in entries if e.get("dismissed")]
    print(f"\nFeedback labels: starred={len(starred)}  dismissed={len(dismissed)}")
    if starred:
        print("Starred:")
        for e in starred:
            p = e.get("payload") or {}
            print(
                f"  {score_of(e):6.1%} (raw {raw_of(e):.3f})  "
                f"{p.get('title')} @ {p.get('company')}"
            )
    if dismissed:
        print("Dismissed:")
        for e in dismissed[:10]:
            p = e.get("payload") or {}
            print(
                f"  {score_of(e):6.1%} (raw {raw_of(e):.3f})  "
                f"{p.get('title')} @ {p.get('company')}"
            )

    if len(starred) < 1 or len(dismissed) < 1:
        print(
            "\nNote: spike_cross_encoder.py needs both starred AND dismissed jobs "
            "to measure rank separation. Star/dismiss a few roles in the UI first."
        )

    profile = get_profile_text(data) or ""
    if not profile.strip():
        cfg = API_ROOT / "config.json"
        if cfg.exists():
            profile = (json.loads(cfg.read_text(encoding="utf-8")).get("match") or {}).get(
                "profile_text", ""
            )
    print(f"\nProfile text: {len(profile)} chars")

    # Top 60 rerank simulation
    ranked = sorted(entries, key=raw_of, reverse=True)
    top_n = 60
    top = ranked[:top_n]
    print(f"\n{'=' * 72}")
    print(f"CROSS-ENCODER RERANK SIMULATION (top {top_n} by raw _match_score)")
    print("=" * 72)

    reranker = CrossEncoderReranker(enabled=True)
    if not reranker.active:
        print("CrossEncoder not available — install sentence-transformers with CrossEncoder support.")
        return
    if not profile.strip():
        print("No profile text — upload resume first.")
        return

    docs = []
    for e in top:
        p = e.get("payload") or {}
        sig = p.get("job_signature") or build_job_signature(p)
        docs.append(sig or str(p.get("title") or ""))

    print(f"Loading {reranker.model_name} …")
    raw_ce = reranker.rerank_pairs(profile[:4000], docs)
    norm_ce = normalize_rerank_scores(raw_ce)
    blend = reranker.blend_weight

    rows = []
    for i, e in enumerate(top):
        embed_raw = raw_of(e)
        embed_display = score_of(e)
        rerank_norm = norm_ce[i]
        blended_raw = reranker.blend_score(embed_raw, rerank_norm)
        blended_display = calibrate_score(blended_raw)
        delta_display = blended_display - embed_display
        p = e.get("payload") or {}
        rows.append({
            "title": p.get("title", "?"),
            "company": p.get("company", "?"),
            "embed_raw": embed_raw,
            "embed_display": embed_display,
            "ce_raw": raw_ce[i],
            "ce_norm": rerank_norm,
            "blend_raw": blended_raw,
            "blend_display": blended_display,
            "delta_display": delta_display,
        })

    rows.sort(key=lambda r: r["blend_display"], reverse=True)

    print(f"\nBlend formula: final_raw = {1-blend:.2f}*embed + {blend:.2f}*rerank_norm")
    print(f"Display = calibrate(final_raw)\n")

    print(f"{'Rank':>4}  {'Embed':>6}  {'Rerank':>6}  {'Final':>6}  {'Delta':>6}  Role")
    print("-" * 72)
    for rank, r in enumerate(rows[:25], 1):
        print(
            f"{rank:4d}  {r['embed_display']:6.1%}  {r['ce_norm']:6.1%}  "
            f"{r['blend_display']:6.1%}  {r['delta_display']:+6.1%}  "
            f"{r['title'][:40]} @ {r['company'][:15]}"
        )

    # Biggest movers
    movers_up = sorted(rows, key=lambda r: r["delta_display"], reverse=True)[:5]
    movers_down = sorted(rows, key=lambda r: r["delta_display"])[:5]
    print("\nBiggest gains after rerank:")
    for r in movers_up:
        print(f"  {r['delta_display']:+.1%}  {r['embed_display']:.1%} -> {r['blend_display']:.1%}  {r['title'][:45]}")
    print("\nBiggest drops after rerank:")
    for r in movers_down:
        print(f"  {r['delta_display']:+.1%}  {r['embed_display']:.1%} -> {r['blend_display']:.1%}  {r['title'][:45]}")

    # Rank changes in top 25
    embed_order = {id(top[i]): i for i in range(len(top))}
    blend_order = sorted(range(len(rows)), key=lambda i: rows[i]["blend_display"], reverse=True)
    rank_changes = []
    for new_rank, idx in enumerate(blend_order[:25]):
        old_rank = embed_order.get(id(top[idx]), idx)
        if old_rank != new_rank:
            rank_changes.append((old_rank + 1, new_rank + 1, rows[idx]["title"][:40]))
    if rank_changes:
        print(f"\nRank changes in top 25 (embed rank → rerank rank):")
        for old, new, title in rank_changes[:12]:
            print(f"  #{old} -> #{new}  {title}")
    else:
        print("\nNo rank changes in top 25 — rerank preserved ordering.")

    # If we have both labels, quick separation on top-60 subset
    if starred and dismissed:
        print(f"\n{'=' * 72}")
        print("LABEL SEPARATION (on all labelled jobs)")
        print("=" * 72)
        labelled = starred + dismissed
        docs_l = [
            build_job_signature(e.get("payload") or {}) or (e.get("payload") or {}).get("title", "")
            for e in labelled
        ]
        ce_l = normalize_rerank_scores(reranker.rerank_pairs(profile[:4000], docs_l))
        star_ce = [ce_l[i] for i, e in enumerate(labelled) if e.get("starred")]
        dis_ce = [ce_l[i] for i, e in enumerate(labelled) if e.get("dismissed")]
        if star_ce and dis_ce:
            gap = statistics.mean(star_ce) - statistics.mean(dis_ce)
            print(f"Cross-encoder mean(starred) - mean(dismissed) = {gap:+.4f}")


if __name__ == "__main__":
    main()
