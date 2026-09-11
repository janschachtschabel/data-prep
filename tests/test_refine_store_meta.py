"""Listing datasets does not re-read them (audit 2026-09-11, P2).

list_datasets parsed the header and then column 0 of EVERY stored CSV to
count rows -- on every GET /refine/datasets, which the UI issues after each
import and each apply from two tabs. O(total rows in the store) per click.
save_dataset already holds the shape it writes; it records it beside the
table, and the listing reads that. Files that predate the sidecar are still
counted the slow way, so nothing already stored goes missing.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.refine.store import (
    dataset_path,
    delete_dataset,
    list_datasets,
    refine_dir,
    save_dataset,
)
from app.settings import Settings

# A quoted cell spanning two physical lines: the row count that a line count
# gets wrong, and the reason the slow path parses instead of counting lines.
MULTILINE = pd.DataFrame({"a": ["1", "two\nlines", "3"], "b": ["x", "y", "z"]})


def _settings(tmp_path) -> Settings:
    return Settings(auth_key=None, data_dir=tmp_path / "data")


def _forbid_reading_csv(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the listing opened a dataset")

    monkeypatch.setattr(pd, "read_csv", refuse)


class TestSavedDatasetsListWithoutBeingRead:
    def test_rows_and_columns_come_from_the_sidecar(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        save_dataset(settings, "d", MULTILINE)
        _forbid_reading_csv(monkeypatch)
        assert list_datasets(settings) == [{"name": "d", "rows": 3, "columns": ["a", "b"]}]

    def test_a_resave_updates_the_shape(self, tmp_path, monkeypatch):
        settings = _settings(tmp_path)
        save_dataset(settings, "d", MULTILINE)
        save_dataset(settings, "d", pd.DataFrame({"only": ["1"]}))
        _forbid_reading_csv(monkeypatch)
        assert list_datasets(settings) == [{"name": "d", "rows": 1, "columns": ["only"]}]

    def test_delete_removes_the_sidecar_too(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "d", MULTILINE)
        assert delete_dataset(settings, "d")
        assert not list(refine_dir(settings).iterdir())


class TestLegacyFilesStillList:
    def test_a_csv_without_a_sidecar_is_counted_by_parsing(self, tmp_path):
        settings = _settings(tmp_path)
        refine_dir(settings)
        MULTILINE.to_csv(dataset_path(settings, "old"), sep=";", index=False, encoding="utf-8")
        assert list_datasets(settings) == [{"name": "old", "rows": 3, "columns": ["a", "b"]}]

    @pytest.mark.parametrize("empty", ["", "a;b\n"])
    def test_an_empty_legacy_file_lists_with_zero_rows(self, tmp_path, empty):
        settings = _settings(tmp_path)
        refine_dir(settings)
        dataset_path(settings, "old").write_text(empty, encoding="utf-8")
        listed = list_datasets(settings)
        assert len(listed) == 1 and listed[0]["rows"] == 0
