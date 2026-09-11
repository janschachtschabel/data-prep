"""A refine write is all or nothing.

Datasets and their operation history are two files, and both were written in
place: a crash or a full disk mid-write left a half file behind, readable as
garbage or as a shorter table. run_store.py and review.py already write through
a sibling .tmp and os.replace; the refine store did not.

The failures below are simulated at the filesystem boundary (a write that gets
part-way and then raises), which is what a full disk or a killed process looks
like from inside the process.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest

from app.refine.apply import preview_or_apply
from app.refine.store import (
    dataset_path,
    load_dataset,
    read_ops,
    refine_dir,
    save_dataset,
    write_ops,
)
from app.settings import Settings

V1 = pd.DataFrame({"a": ["1", "2", "3"]})
V2 = pd.DataFrame({"a": ["9", "8", "7", "6"]})


def _settings(tmp_path) -> Settings:
    return Settings(auth_key=None, data_dir=tmp_path / "data")


def _fail_partway_to_csv(monkeypatch):
    """pandas writes the dataset itself; make it write the header, then die."""
    def broken(self, path_or_buf, *args, **kwargs):
        pathlib.Path(path_or_buf).write_text("a\n9\n", encoding="utf-8")
        raise OSError("disk full")
    monkeypatch.setattr(pd.DataFrame, "to_csv", broken)


def _fail_partway_write_text(monkeypatch, when):
    """Path.write_text is how the ops file is written; fail on the Nth ops write."""
    real = pathlib.Path.write_text
    calls = {"ops": 0}

    def broken(self, data, *args, **kwargs):
        if ".ops.json" in self.name:
            calls["ops"] += 1
            if calls["ops"] == when:
                real(self, data[: len(data) // 2], *args, **kwargs)
                raise OSError("disk full")
        return real(self, data, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, "write_text", broken)


class TestDatasetWrite:
    def test_a_failed_write_leaves_the_previous_dataset_intact(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        save_dataset(settings, "d", V1)
        _fail_partway_to_csv(monkeypatch)
        with pytest.raises(OSError):
            save_dataset(settings, "d", V2)
        assert list(load_dataset(settings, "d")["a"]) == ["1", "2", "3"]

    def test_a_failed_write_leaves_no_temp_file_behind(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        _fail_partway_to_csv(monkeypatch)
        with pytest.raises(OSError):
            save_dataset(settings, "d", V2)
        assert not list(refine_dir(settings).glob("*.tmp"))

    def test_a_successful_write_leaves_no_temp_file_either(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "d", V1)
        assert dataset_path(settings, "d").exists()
        assert not list(refine_dir(settings).glob("*.tmp"))


class TestOpsWrite:
    def test_a_failed_write_leaves_the_previous_history_intact(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        write_ops(settings, "d", [{"filter": "a"}])
        _fail_partway_write_text(monkeypatch, when=1)
        with pytest.raises(OSError):
            write_ops(settings, "d", [{"filter": "a"}, {"filter": "b"}])
        assert read_ops(settings, "d") == [{"filter": "a"}]
        assert not list(refine_dir(settings).glob("*.tmp"))


class TestApplyIsConsistent:
    """preview_or_apply writes the dataset, then the history. The residual
    window -- new dataset, stale history -- is a VALID state: the dataset lists
    and loads, only its provenance is behind. The reverse (history describing a
    step that never landed) is the state to rule out, and so is a history that
    carries the source's steps but not the new one."""

    STATS = {"filter": "rules", "before": 3, "after": 4, "removed": 0, "changed": 0}

    def test_the_dataset_lands_before_the_history(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        save_dataset(settings, "src", V1)
        write_ops(settings, "src", [{"filter": "a"}])
        _fail_partway_write_text(monkeypatch, when=1)
        with pytest.raises(OSError):
            preview_or_apply(settings, "src", "out", "rules", {}, V2, self.STATS)
        assert load_dataset(settings, "out") is not None, "the dataset must land first"
        assert read_ops(settings, "out") == [], "a half-written history is worse than none"

    def test_the_history_is_never_the_source_steps_without_the_new_one(self, tmp_path, monkeypatch):
        """Written as history THEN appended, a failure between the two left
        exactly the source's steps under the target's name -- a plausible-looking
        history that lies about how the dataset was made."""
        settings = _settings(tmp_path)
        save_dataset(settings, "src", V1)
        write_ops(settings, "src", [{"filter": "a"}])
        _fail_partway_write_text(monkeypatch, when=2)
        try:
            preview_or_apply(settings, "src", "out", "rules", {}, V2, self.STATS)
        except OSError:
            pass
        ops = read_ops(settings, "out")
        assert ops != [{"filter": "a"}], "the source's history without the new step"
        assert ops == [] or [o["filter"] for o in ops] == ["a", "rules"]
