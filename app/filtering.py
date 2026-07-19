"""Stage 6 — semantic gates: leakage filter (hybrid only) + internal dedupe.

Vectors are L2-normalized, so cosine similarity is a plain dot product. The
leakage index is FROZEN reference material; the dedupe index grows with every
accepted sample. Encoders are injected callables — tests use a fake, production
uses :mod:`app.embeddings`.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .embeddings import VectorIndex

Encode = Callable[[list[str]], np.ndarray]


class SemanticGate:
    """Per-run gate combining the (optional) leakage index and the dedupe index."""

    def __init__(
        self,
        encode: Encode,
        *,
        reference_texts: list[str] | None,
        leakage_threshold: float,
        dedupe_threshold: float,
    ) -> None:
        self._encode = encode
        self._leakage_threshold = leakage_threshold
        self._dedupe_threshold = dedupe_threshold
        self._reference: np.ndarray | None = None
        if reference_texts:
            self._reference = self._encode_batched(reference_texts)
        self._accepted = VectorIndex()

    def _encode_batched(self, texts: list[str], batch: int = 512) -> np.ndarray:
        parts = [self._encode(texts[i:i + batch]) for i in range(0, len(texts), batch)]
        return np.vstack(parts)

    def prime(self, texts: list[str]) -> None:
        """Seed the dedupe index with already accepted samples (resume path)."""
        if texts:
            self._accepted.extend(self._encode_batched(texts))

    def check(self, text: str) -> tuple[bool, str | None, float]:
        """Gate one candidate; returns ``(accepted, reject_reason, sim_max)``.

        ``sim_max`` is the highest similarity seen against either index — it
        doubles as the diversity signal stored in the sample meta.
        """
        vector = self._encode([text])[0]
        sim_max = 0.0
        if self._reference is not None:
            sim = float(np.max(self._reference @ vector))
            sim_max = max(sim_max, sim)
            if sim >= self._leakage_threshold:
                return False, "leakage", sim
        if len(self._accepted):
            sim = self._accepted.max_similarity(vector)
            sim_max = max(sim_max, sim)
            if sim >= self._dedupe_threshold:
                return False, "semantic_duplicate", sim
        self._accepted.add(vector)
        return True, None, sim_max
