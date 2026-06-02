#!/usr/bin/env python3
"""
One-off utility: re-score every row in match_registry.json using the
current MatchAgent logic (calibration, title/seniority weights, ghost
fold, feedback learner). Preserves seen / starred / dismissed state.

Run from lro/api (same cwd as main.py):

    python rescore_registry.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from agents.match import MatchAgent
from core import app_paths, resume_profile as resume_profile_module
from core import resume_store
from core.match_registry import get_registry, _summary
from core.protocol import PayloadType, Sender, SentinelPacket

logger = logging.getLogger("lro.rescore_registry")
CHUNK_SIZE = 32


def _load_config() -> dict:
    path = app_paths.runtime_dir() / "config.json"
    if not path.is_file():
        raise SystemExit(f"config.json not found at {path} — run from lro/api")
    return json.loads(path.read_text(encoding="utf-8"))


def _effective_profile_text(data_dir: Path, config: dict) -> str:
    cached = resume_profile_module.get_cached_profile(data_dir)
    if cached and not cached.get("error"):
        rendered = resume_profile_module.profile_to_text(cached)
        if rendered:
            return rendered
    raw = resume_store.get_profile_text(data_dir)
    if raw:
        return raw
    return (config.get("match") or {}).get("profile_text") or ""


def _build_match_agent(config: dict, data_dir: Path) -> MatchAgent:
    match_config = dict(config.get("match") or {})
    match_config["preferences"] = config.get("preferences") or {}
    match_config["fake_detection"] = config.get("fake_detection") or {}
    match_config["data_dir"] = data_dir
    match_config["profile_text"] = _effective_profile_text(data_dir, config)
    struct = resume_profile_module.get_cached_profile(data_dir)
    if struct and not struct.get("error"):
        match_config["profile_struct"] = struct
    match_config["role_keywords"] = list(
        (config.get("ingest") or {}).get("role_keywords") or []
    )
    match_config["blocked_title_keywords"] = list(
        (config.get("preferences") or {}).get("blocked_title_keywords") or []
    )
    return MatchAgent(match_config)


def rescore_registry(data_dir: Path | None = None) -> dict:
    data_dir = data_dir or (app_paths.runtime_dir() / "data")
    registry_path = data_dir / "match_registry.json"
    if not registry_path.is_file():
        raise SystemExit(f"No registry at {registry_path}")

    config = _load_config()
    agent = _build_match_agent(config, data_dir)
    if not agent.profile_text.strip() and agent.embed_model is None:
        raise SystemExit("No profile and no embeddings — nothing to score against.")

    reg = get_registry(data_dir)
    reg.reload()
    entries = dict(reg._load()["entries"])
    keys = list(entries.keys())
    total = len(keys)
    if total == 0:
        return {"updated": 0, "total": 0}

    logger.info("Re-scoring %d registry rows…", total)
    if agent.feedback_learner is not None:
        try:
            agent.refresh_feedback(entries)
        except Exception as e:
            logger.warning("feedback refresh skipped: %s", e)

    packets: list[tuple[str, SentinelPacket]] = []
    for key in keys:
        payload = dict((entries[key].get("payload") or {}))
        pkt = SentinelPacket(
            sender=Sender.INGEST,
            payload_type=PayloadType.JSON_JOB,
            payload=payload,
        )
        packets.append((key, pkt))

    embed_active = agent.embed_model is not None and agent.profile_embedding is not None
    title_index: dict[str, set[str]] = {}
    cache = agent.embedding_cache if embed_active else None
    if cache is not None:
        cache.reset_counters()

    from core.embedding_cache import text_key

    all_texts = [agent._job_to_text(p.payload) for _, p in packets] if embed_active else []
    all_keys = [text_key(t) for t in all_texts] if embed_active else []

    t0 = time.time()
    updated_payloads: dict[str, dict] = {}

    for chunk_start in range(0, total, CHUNK_SIZE):
        chunk = packets[chunk_start: chunk_start + CHUNK_SIZE]
        chunk_embeddings: dict[int, object] = {}
        if embed_active:
            miss_packets: list[SentinelPacket] = []
            miss_texts: list[str] = []
            miss_keys: list[str] = []
            for i_in_chunk, (_, pkt) in enumerate(chunk):
                idx = chunk_start + i_in_chunk
                key = all_keys[idx]
                cached = cache.get(key) if cache is not None else None
                if cached is not None:
                    chunk_embeddings[id(pkt)] = cached
                else:
                    miss_packets.append(pkt)
                    miss_texts.append(all_texts[idx])
                    miss_keys.append(key)
            if miss_packets:
                batch = agent.embed_model.encode(
                    miss_texts,
                    batch_size=CHUNK_SIZE,
                    convert_to_tensor=True,
                    show_progress_bar=False,
                )
                for pkt, emb, ck in zip(miss_packets, batch, miss_keys):
                    chunk_embeddings[id(pkt)] = emb
                    if cache is not None:
                        cache.set(ck, emb)

        for i_in_chunk, (entry_key, pkt) in enumerate(chunk):
            i = chunk_start + i_in_chunk
            if (i + 1) % 50 == 0 or i + 1 == total:
                logger.info("  %d / %d", i + 1, total)
            result = agent.match(
                pkt,
                title_index=title_index,
                precomputed_embedding=chunk_embeddings.get(id(pkt)),
            )
            updated_payloads[entry_key] = result.payload

    registry = reg._load()
    for key, payload in updated_payloads.items():
        entry = registry["entries"].get(key)
        if not entry:
            continue
        entry["payload"] = payload
        entry["score"] = float(payload.get("_match_score", 0) or 0)
        entry["summary"] = _summary(payload)
    reg._flush()

    elapsed = time.time() - t0
    scores = [float(p.get("_match_score_display", p.get("_match_score", 0)) or 0)
                for p in updated_payloads.values()]
    scores.sort(reverse=True)
    sample = scores[:5]
    logger.info(
        "Done in %.1fs. Updated %d rows. Top display scores: %s",
        elapsed,
        len(updated_payloads),
        ", ".join(f"{s * 100:.0f}%" for s in sample),
    )
    return {"updated": len(updated_payloads), "total": total, "seconds": round(elapsed, 1)}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    stats = rescore_registry()
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
