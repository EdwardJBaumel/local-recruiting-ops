"""
Cross-encoder reranking for the match pipeline.

Architecture: embed wide (bi-encoder over all jobs), cross-encode narrow
(top-N by embedding score). No Ollama — runs on CPU or GPU via
sentence-transformers CrossEncoder.

Default model pairs with bge-m3: BAAI/bge-reranker-v2-m3
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("lantern.cross_encoder")

CROSS_ENCODER_AVAILABLE = False
CrossEncoder = None  # type: ignore

try:
    from sentence_transformers import CrossEncoder as _CrossEncoder
    CrossEncoder = _CrossEncoder
    CROSS_ENCODER_AVAILABLE = True
except ImportError:
    pass

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_TOP_N = 60
DEFAULT_BLEND = 0.55


def _sigmoid(x: float) -> float:
    if x >= 20:
        return 1.0
    if x <= -20:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def normalize_rerank_scores(raw_scores: list[float]) -> list[float]:
    """Map cross-encoder logits to [0, 1] within a batch."""
    if not raw_scores:
        return []
    lo, hi = min(raw_scores), max(raw_scores)
    if hi - lo < 1e-6:
        return [_sigmoid(s) for s in raw_scores]
    return [(s - lo) / (hi - lo) for s in raw_scores]


class CrossEncoderReranker:
    """Lazy-loaded pairwise reranker."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        top_n: int = DEFAULT_TOP_N,
        blend_weight: float = DEFAULT_BLEND,
        enabled: bool = True,
    ):
        self.model_name = model_name or DEFAULT_MODEL
        self.top_n = max(1, int(top_n or DEFAULT_TOP_N))
        try:
            self.blend_weight = float(blend_weight)
        except (TypeError, ValueError):
            self.blend_weight = DEFAULT_BLEND
        self.blend_weight = max(0.0, min(1.0, self.blend_weight))
        self.enabled = bool(enabled) and CROSS_ENCODER_AVAILABLE
        self._model = None

    @property
    def active(self) -> bool:
        return self.enabled and CROSS_ENCODER_AVAILABLE

    def _load(self):
        if self._model is not None or not self.active:
            return
        logger.info("Loading cross-encoder %s for rerank", self.model_name)
        self._model = CrossEncoder(self.model_name)

    def rerank_pairs(self, query: str, documents: list[str]) -> list[float]:
        """Score (query, doc) pairs. Returns raw logits."""
        if not documents or not query.strip():
            return []
        self._load()
        if self._model is None:
            return [0.0] * len(documents)
        pairs = [[query, doc] for doc in documents]
        try:
            scores = self._model.predict(pairs, show_progress_bar=False)
            return [float(s) for s in scores]
        except Exception as e:
            logger.warning("Cross-encoder predict failed: %s", e)
            return [0.0] * len(documents)

    def blend_score(self, embed_score: float, rerank_norm: float) -> float:
        w = self.blend_weight
        return round((1.0 - w) * embed_score + w * rerank_norm, 4)

    def select_top_indices(self, scores: list[float], n: int | None = None) -> list[int]:
        """Return indices of top-N jobs by descending score."""
        n = n if n is not None else self.top_n
        indexed = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return indexed[:n]

    @staticmethod
    def document_from_payload(payload: dict[str, Any]) -> str:
        sig = str(payload.get("job_signature") or "").strip()
        if sig:
            return sig
        from core.job_signature import build_job_signature
        return build_job_signature(payload)
