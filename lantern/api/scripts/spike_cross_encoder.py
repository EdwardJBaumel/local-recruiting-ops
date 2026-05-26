#!/usr/bin/env python3
"""
Spike: measure rank separation of cross-encoder vs embedding-only.

Uses starred vs dismissed jobs from match_registry.json — no Ollama.
Run from lantern/api:

    python scripts/spike_cross_encoder.py [--pairs 50]

Prints mean score gap, overlap, and a simple AUC estimate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from core.cross_encoder_rerank import CrossEncoderReranker, normalize_rerank_scores
from core.job_signature import build_job_signature
from core.match_registry import MatchRegistry
from core.resume_store import get_profile_text


def _load_profile(data: Path, api_root: Path) -> str:
    text = get_profile_text(data) or ""
    if text.strip():
        return text.strip()
    cfg_path = api_root / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return (cfg.get("match") or {}).get("profile_text") or ""
    return ""


def _collect_labels(reg: MatchRegistry) -> tuple[list[dict], list[dict]]:
    starred, dismissed = [], []
    for entry in reg.entries_by_key().values():
        payload = entry.get("payload") or {}
        if not payload.get("title"):
            continue
        if entry.get("starred"):
            starred.append(payload)
        elif entry.get("dismissed"):
            dismissed.append(payload)
    return starred, dismissed


def _embed_scores(profile: str, payloads: list[dict]) -> list[float]:
    try:
        from sentence_transformers import SentenceTransformer, util
    except ImportError:
        print("sentence-transformers not installed")
        sys.exit(1)
    model = SentenceTransformer("BAAI/bge-m3")
    prof_emb = model.encode(profile, convert_to_tensor=True)
    texts = [build_job_signature(p) or p.get("title", "") for p in payloads]
    job_embs = model.encode(texts, convert_to_tensor=True)
    return [float(util.cos_sim(prof_emb, j).item()) for j in job_embs]


def _cross_scores(profile: str, payloads: list[dict], reranker: CrossEncoderReranker) -> list[float]:
    docs = [build_job_signature(p) or str(p.get("title") or "") for p in payloads]
    raw = reranker.rerank_pairs(profile, docs)
    return normalize_rerank_scores(raw)


def _mean_gap(star: list[float], dismiss: list[float]) -> float:
    if not star or not dismiss:
        return float("nan")
    return (sum(star) / len(star)) - (sum(dismiss) / len(dismiss))


def _pairwise_auc(star: list[float], dismiss: list[float]) -> float:
    if not star or not dismiss:
        return float("nan")
    wins = ties = total = 0
    for s in star:
        for d in dismiss:
            total += 1
            if s > d:
                wins += 1
            elif s == d:
                ties += 1
    return (wins + 0.5 * ties) / total if total else float("nan")


def main():
    parser = argparse.ArgumentParser(description="Cross-encoder rank-separation spike")
    parser.add_argument("--pairs", type=int, default=50, help="Max jobs per label to sample")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()

    data = args.data_dir or (API_ROOT / "data")
    reg_path = data / "match_registry.json"
    if not reg_path.exists():
        print(f"No registry at {reg_path}. Star/dismiss some jobs first.")
        sys.exit(1)

    profile = _load_profile(data, API_ROOT)
    if not profile.strip():
        print("No profile text — upload a resume or set match.profile_text.")
        sys.exit(1)

    reg = MatchRegistry(reg_path)
    starred, dismissed = _collect_labels(reg)
    if not starred or not dismissed:
        print(f"Need both starred and dismissed jobs (have {len(starred)} / {len(dismissed)}).")
        sys.exit(1)

    starred = starred[: args.pairs]
    dismissed = dismissed[: args.pairs]
    print(f"Profile: {len(profile)} chars | starred={len(starred)} dismissed={len(dismissed)}")

    embed_star = _embed_scores(profile, starred)
    embed_dismiss = _embed_scores(profile, dismissed)
    embed_gap = _mean_gap(embed_star, embed_dismiss)
    embed_auc = _pairwise_auc(embed_star, embed_dismiss)
    print(f"\nEmbedding-only (bge-m3 cosine on job_signature):")
    print(f"  mean(starred) - mean(dismissed) = {embed_gap:+.4f}")
    print(f"  pairwise AUC                      = {embed_auc:.3f}")

    reranker = CrossEncoderReranker(enabled=True)
    if not reranker.active:
        print("\nCross-encoder unavailable (sentence-transformers CrossEncoder).")
        sys.exit(1)

    ce_star = _cross_scores(profile, starred, reranker)
    ce_dismiss = _cross_scores(profile, dismissed, reranker)
    ce_gap = _mean_gap(ce_star, ce_dismiss)
    ce_auc = _pairwise_auc(ce_star, ce_dismiss)
    print(f"\nCross-encoder ({reranker.model_name}) on job_signature:")
    print(f"  mean(starred) - mean(dismissed) = {ce_gap:+.4f}")
    print(f"  pairwise AUC                      = {ce_auc:.3f}")

    if not (ce_gap != ce_gap or embed_gap != embed_gap):
        delta = ce_gap - embed_gap
        print(f"\nGap improvement: {delta:+.4f} ({'better' if delta > 0 else 'worse'} separation)")


if __name__ == "__main__":
    main()
