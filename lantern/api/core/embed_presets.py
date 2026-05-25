"""
EMBEDDING PRESETS

Central registry for the sentence-transformer models SENTINEL can use.
Keeps the config surface small (users pick a named tier) while letting
power users drop in their own Hugging Face ID.

Design calls:

  a. Preset lookup is case-insensitive so `"BALANCED"` in a YAML edit
     still works.
  b. Unknown strings are treated as raw HF IDs so the user can try a
     new model without a code change.
  c. A single canonical default (BALANCED → bge-m3) means match.py
     never needs to hard-code an ID fallback.
  d. Each preset declares its approximate VRAM footprint so the first-
     run wizard can recommend small/balanced/heavy based on the GPU
     preflight. VRAM numbers are float16 + batch=1 and include a fudge
     factor for tokenizer + activations.

Resolution order (higher is more specific, so wins):

  a. Exact preset name (case-insensitive).
  b. Registered Hugging Face ID that matches a preset's `hf_id`.
  c. Pass-through: any other string is returned as-is and assumed to
     be a valid HF repo. The model loader is the authoritative check.

Anything falsy (None, empty string) yields the default preset's HF ID.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EmbedPreset:
    name: str
    hf_id: str
    approx_vram_gb: float
    dim: int
    notes: str


PRESETS: dict[str, EmbedPreset] = {
    # Default. Multilingual, strong on mixed-domain queries. Heavier at
    # ~2.3 GB FP16 resident so it's the first swap target on 8 GB cards.
    "balanced": EmbedPreset(
        name="balanced",
        hf_id="BAAI/bge-m3",
        approx_vram_gb=2.3,
        dim=1024,
        notes="Current default. Multilingual, 1024-dim, strong mixed-domain.",
    ),
    # ~40% of balanced's VRAM. English-only. Sufficient for PM job
    # postings which are overwhelmingly English in this dataset.
    "small": EmbedPreset(
        name="small",
        hf_id="BAAI/bge-base-en-v1.5",
        approx_vram_gb=0.9,
        dim=768,
        notes="English-only, 768-dim. ~40% smaller than balanced.",
    ),
    # For users with abundant VRAM who want the top of the leaderboard.
    "large": EmbedPreset(
        name="large",
        hf_id="BAAI/bge-large-en-v1.5",
        approx_vram_gb=1.7,
        dim=1024,
        notes="English-only, 1024-dim. Higher recall on long postings.",
    ),
    # CPU-only fallback. Ships with many PyInstaller bundles and does
    # not require a GPU at all. Quality drop is real but still beats
    # falling all the way back to LLM-only scoring.
    "cpu": EmbedPreset(
        name="cpu",
        hf_id="sentence-transformers/all-MiniLM-L6-v2",
        approx_vram_gb=0.1,
        dim=384,
        notes="CPU-friendly 384-dim. Last-resort when no GPU available.",
    ),
}

DEFAULT_PRESET = "balanced"


def list_presets() -> list[dict]:
    """Serialisable view for the Settings / wizard UI."""
    return [
        {
            "name": p.name,
            "hf_id": p.hf_id,
            "approx_vram_gb": p.approx_vram_gb,
            "dim": p.dim,
            "notes": p.notes,
        }
        for p in PRESETS.values()
    ]


def resolve(config_value: Optional[str]) -> str:
    """Return the Hugging Face ID for a config-supplied preset name or
    raw HF ID. See module docstring for resolution order.
    """
    if not config_value or not str(config_value).strip():
        return PRESETS[DEFAULT_PRESET].hf_id
    raw = str(config_value).strip()
    lowered = raw.lower()
    if lowered in PRESETS:
        return PRESETS[lowered].hf_id
    # Reverse lookup: exact hf_id match of a known preset. Accepts the
    # HF id even with mixed case since HF is case-insensitive on org
    # prefixes in practice.
    for p in PRESETS.values():
        if p.hf_id.lower() == lowered:
            return p.hf_id
    # Pass-through: unknown string, assume a valid HF id. Loader will
    # fail loudly if it's wrong.
    return raw


def describe(config_value: Optional[str]) -> dict:
    """Full metadata for the resolved preset, or a pass-through stub if
    the config value is a raw HF ID not in the preset table. Useful for
    the wizard recommendation path and for /api/resources enrichment.
    """
    hf_id = resolve(config_value)
    for p in PRESETS.values():
        if p.hf_id == hf_id:
            return {
                "resolved_hf_id": hf_id,
                "preset": p.name,
                "approx_vram_gb": p.approx_vram_gb,
                "dim": p.dim,
                "notes": p.notes,
                "is_preset": True,
            }
    return {
        "resolved_hf_id": hf_id,
        "preset": None,
        "approx_vram_gb": None,
        "dim": None,
        "notes": "Custom model ID, not a SENTINEL preset.",
        "is_preset": False,
    }


def recommend_for_vram(available_vram_gb: float) -> str:
    """Return the best preset name that fits in the given VRAM budget.
    Used by the first-run wizard's Models step. Leaves 2 GB headroom
    for the Ollama LLM that'll share the card.
    """
    headroom_gb = 2.0
    budget = max(0.0, available_vram_gb - headroom_gb)
    # Descending by VRAM so we pick the best that fits.
    for name in ("balanced", "small", "cpu"):
        if PRESETS[name].approx_vram_gb <= budget:
            return name
    return "cpu"
