"""Encoder concurrency: the lazy model load must happen exactly once even when
two threads call ``encode`` at the same time. The /refine filter route offloads
semantic dedupe to a worker thread, so concurrent requests can race the load.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np

from app.embeddings import Encoder


def test_encode_loads_model_once_under_concurrent_calls(monkeypatch):
    load_count = 0
    count_lock = threading.Lock()
    first_in = threading.Event()
    proceed = threading.Event()

    class FakeModel:
        def encode(self, texts, use_multiprocessing=False):
            return np.full((len(texts), 3), 3.0, dtype=np.float32)

    class FakeStaticModel:
        @classmethod
        def from_pretrained(cls, name):
            nonlocal load_count
            with count_lock:
                load_count += 1
                n = load_count
            if n == 1:
                first_in.set()
            proceed.wait(timeout=5)  # hold the load open so a 2nd thread can race it
            return FakeModel()

    fake_module = types.ModuleType("model2vec")
    fake_module.StaticModel = FakeStaticModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "model2vec", fake_module)

    encoder = Encoder("fake-model")
    results: list[np.ndarray] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(encoder.encode(["hello"]))
        except Exception as exc:  # surface a thread failure to the test  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    assert first_in.wait(timeout=5), "first encode never entered the model load"
    t2.start()
    time.sleep(0.2)  # give the 2nd thread time to reach the load site or block on the lock
    proceed.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, errors
    assert load_count == 1
    assert len(results) == 2
