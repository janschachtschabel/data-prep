"""model2vec wrapper: lazy, cached encoders producing L2-normalized float32
vectors (cosine similarity = dot product downstream).

Torch-free static embeddings; the model name comes from config.yaml. Windows
note (project lesson): ``use_multiprocessing=False`` avoids WinError 1450 on
large batches.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

logger = logging.getLogger("data_prep.embeddings")


class Encoder:
    """Lazy-loading encoder for one model name."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None
        # Guards the one-time lazy load: /refine offloads encode() to worker
        # threads, so concurrent first calls must not each load the model.
        self._load_lock = threading.Lock()

    def encode(self, texts: list[str]) -> np.ndarray:
        model = self._model
        if model is None:
            model = self._ensure_model()
        vectors = np.asarray(
            model.encode(texts, use_multiprocessing=False), dtype=np.float32
        )
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0  # all-zero vectors (empty text) stay zero
        return vectors / norms

    def _ensure_model(self) -> Any:
        """Load the model once (double-checked locking, thread-safe)."""
        with self._load_lock:
            if self._model is None:
                # Import here so the app starts fast and tests never touch model2vec.
                from model2vec import StaticModel

                logger.info("Loading embedding model %s", self.model_name)
                self._model = StaticModel.from_pretrained(self.model_name)
            return self._model


_cache: dict[str, Encoder] = {}


def get_encoder(model_name: str) -> Encoder:
    """Process-wide encoder cache — the table is memory-heavy, load it once."""
    if model_name not in _cache:
        _cache[model_name] = Encoder(model_name)
    return _cache[model_name]


class VectorIndex:
    """Append-only matrix of L2-normalized vectors with amortized-O(1) growth.

    Keeps the running max-cosine check to a single BLAS matmul against one
    contiguous array, instead of re-stacking a growing Python list on every
    candidate — the earlier ``np.vstack(list) @ vector`` pattern was O(N^2) time
    and allocation, which thrashed on the ~30k-row datasets this app targets.
    """

    _INITIAL_CAPACITY = 8

    def __init__(self) -> None:
        self._buf: np.ndarray | None = None
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def add(self, vector: np.ndarray) -> None:
        if self._buf is None:
            self._buf = np.empty((self._INITIAL_CAPACITY, vector.shape[0]), dtype=np.float32)
        elif self._n == self._buf.shape[0]:  # full -> double the capacity (amortized O(1))
            grown = np.empty((self._n * 2, self._buf.shape[1]), dtype=np.float32)
            grown[: self._n] = self._buf[: self._n]
            self._buf = grown
        self._buf[self._n] = vector
        self._n += 1

    def extend(self, vectors: np.ndarray) -> None:
        """Add every row of a 2-D vector array (seeds the index on resume)."""
        for vector in vectors:
            self.add(vector)

    def max_similarity(self, vector: np.ndarray) -> float:
        """Highest dot product (= cosine; vectors are L2-normalized) against the
        index; ``0.0`` when the index is empty."""
        if self._n == 0 or self._buf is None:
            return 0.0
        return float(np.max(self._buf[: self._n] @ vector))
