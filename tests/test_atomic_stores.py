"""Every store write is all or nothing (audit 2026-09-11, L1).

13b80d1 made refine datasets atomic; the seed, vocabulary, reference and
run stores still wrote in place, and the two that already used a sibling
.tmp left it behind on failure. One truncated seed-set file took the whole
Seeds listing down (list_seed_sets parses every file). Failures are simulated
at the filesystem boundary, as a full disk or a killed process looks from
inside the process.
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

from app import review, run_store
from app.seeds import list_seed_sets, load_seed_set, save_seed_set
from app.settings import Settings

PAYLOAD_1 = {"name": "s", "vocab": "v", "concepts": {"u": {"seeds": [], "terms": []}}}
PAYLOAD_2 = {"name": "s", "vocab": "v", "concepts": {"u": {"seeds": [{"title": "t"}], "terms": []}}}


def _settings(tmp_path) -> Settings:
    return Settings(auth_key=None, data_dir=tmp_path / "data", runs_dir=tmp_path / "runs")


def _fail_partway_write_text(monkeypatch, marker: str):
    """Path.write_text writes half of the FIRST file whose name carries the
    marker, then dies; every other write is untouched."""
    real = pathlib.Path.write_text
    state = {"done": False}

    def broken(self, data, *args, **kwargs):
        if marker in self.name and not state["done"]:
            state["done"] = True
            real(self, data[: len(data) // 2], *args, **kwargs)
            raise OSError("disk full")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", broken)


def _fail_partway_to_csv(monkeypatch):
    def broken(self, path_or_buf, *args, **kwargs):
        pathlib.Path(path_or_buf).write_text("a\n9\n", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", broken)


def _no_tmp_left(directory: pathlib.Path) -> bool:
    return not list(directory.rglob("*.tmp"))


class TestSeedStore:
    def test_a_failed_write_leaves_the_previous_set_and_the_listing_intact(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        save_seed_set(settings, "s", PAYLOAD_1)
        _fail_partway_write_text(monkeypatch, "s.json")
        with pytest.raises(OSError):
            save_seed_set(settings, "s", PAYLOAD_2)
        assert load_seed_set(settings, "s") == PAYLOAD_1
        assert [s["name"] for s in list_seed_sets(settings)] == ["s"]
        assert _no_tmp_left(settings.data_dir)


class TestRunStore:
    def test_a_failed_state_write_leaves_the_previous_state_and_no_temp_file(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        run_store.run_dir(settings, "r1").mkdir(parents=True)
        state = {"id": "r1", "status": "running", "counters": {"generated": 1}}
        run_store.write_state(settings, state)
        _fail_partway_write_text(monkeypatch, "state.json")
        with pytest.raises(OSError):
            run_store.write_state(settings, {**state, "counters": {"generated": 2}})
        assert run_store.read_state(settings, "r1") == state
        assert _no_tmp_left(settings.runs_dir)


class TestReviewStore:
    def test_a_failed_status_write_leaves_the_samples_and_no_temp_file(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        samples = [{"id": "a", "status": "passed"}, {"id": "b", "status": "passed"}]
        (run_dir / "samples.jsonl").write_text(
            "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
        )
        _fail_partway_write_text(monkeypatch, "samples.jsonl")
        with pytest.raises(OSError):
            review.update_status(run_dir, "a", "discarded")
        assert review.list_samples(run_dir)["samples"] == samples
        assert _no_tmp_left(run_dir)


class TestVocabularyStore:
    def test_a_failed_write_leaves_the_previous_vocabulary_intact(self, make_client, monkeypatch, tmp_path):
        client = make_client(auth_key=None)
        first = client.post("/vocabs/manual", json={"name": "v", "text": "Alpha\nBeta"})
        assert first.status_code == 200, first.text
        _fail_partway_write_text(monkeypatch, "v.json")
        with pytest.raises(OSError):  # an explicit replace that dies mid-write
            client.post("/vocabs/manual", json={"name": "v", "text": "Gamma", "overwrite": True})
        detail = client.get("/vocabs/v").json()
        assert detail["concept_count"] == 2
        assert _no_tmp_left(tmp_path / "data")


class TestReferenceStore:
    def test_a_failed_import_leaves_the_previous_reference_intact(self, make_client, monkeypatch, tmp_path):
        client = make_client(auth_key=None)
        csv = (
            "properties.cclom:title;properties.cclom:general_description;"
            "properties.cclom:general_keyword;properties.ccm:taxonid\n"
            "T1;D1;K1;http://x/1\nT2;D2;K2;http://x/1\n"
        )
        first = client.post("/references/import", files={"file": ("r.csv", csv.encode(), "text/csv")})
        assert first.status_code == 200, first.text
        assert first.json()["row_count"] == 2
        _fail_partway_to_csv(monkeypatch)
        longer = (csv + "T3;D3;K3;http://x/2\n").encode()
        with pytest.raises(OSError):  # an explicit replace that dies mid-write
            client.post("/references/import", files={"file": ("r.csv", longer, "text/csv")},
                        data={"overwrite": "true"})
        # The CSV itself, not the meta file: written in place, the failed
        # write truncated the table while the meta beside it still said 2 rows.
        stored = (tmp_path / "data" / "references" / "r.csv").read_text(encoding="utf-8")
        assert stored.count("\n") == 3, stored
        assert client.get("/references/r").json()["row_count"] == 2
        assert _no_tmp_left(tmp_path / "data")
