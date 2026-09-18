"""The step both kinds of operation share: preview, or apply and record.

Label filters and table operations dispatch differently -- one needs an
embedding model in its context, the other knows nothing about labels -- but
what happens to the RESULT is identical, and the operation history is what
makes a pipeline reconstructable. Pinning it once here keeps the two routes
from drifting apart.
"""

from __future__ import annotations

import pandas as pd

from app.refine.apply import preview_or_apply
from app.refine.store import load_dataset, read_ops, save_dataset
from app.settings import Settings

DF = pd.DataFrame({"a": ["1", "2", "3"]})
RESULT = pd.DataFrame({"a": ["1", "2"]})
STATS = {"filter": "rules", "before": 3, "after": 2, "removed": 1, "changed": 0}


def _settings(tmp_path) -> Settings:
    return Settings(auth_key=None, data_dir=tmp_path / "data")


class TestPreview:
    def test_it_reports_the_stats_and_writes_nothing(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        out = preview_or_apply(settings, "src", None, "rules", {}, RESULT, STATS, history=[])
        assert out["preview"] is True
        assert out["removed"] == 1
        assert "target" not in out

    def test_the_source_is_left_alone(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        preview_or_apply(settings, "src", None, "rules", {}, RESULT, STATS, history=[])
        assert len(load_dataset(settings, "src")) == 3


class TestApply:
    def test_it_writes_the_target(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        out = preview_or_apply(settings, "src", "kept", "rules", {}, RESULT, STATS,
            history=read_ops(settings, "src"))
        assert out["preview"] is False
        assert out["target"] == "kept"
        assert len(load_dataset(settings, "kept")) == 2

    def test_the_source_survives_unchanged(self, tmp_path):
        """Operations are non-destructive: a wrong filter must never cost the
        input, which is the whole reason a target is named."""
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        preview_or_apply(settings, "src", "kept", "rules", {}, RESULT, STATS,
            history=read_ops(settings, "src"))
        assert len(load_dataset(settings, "src")) == 3

    def test_the_operation_is_recorded_with_its_params(self, tmp_path):
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        params = {"rules": [{"column": "a", "op": "ne", "value": "3"}]}
        preview_or_apply(settings, "src", "kept", "rules", params, RESULT, STATS,
            history=read_ops(settings, "src"))
        ops = read_ops(settings, "kept")
        assert len(ops) == 1
        assert ops[0]["filter"] == "rules"
        assert ops[0]["params"] == params
        assert ops[0]["source"] == "src"
        assert ops[0]["removed"] == 1

    def test_a_chain_carries_the_whole_history_forward(self, tmp_path):
        """Three steps must read as three steps on the final dataset, or the
        pipeline cannot be reconstructed from what was kept."""
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        preview_or_apply(settings, "src", "step1", "drop_columns", {}, RESULT, STATS,
            history=read_ops(settings, "src"))
        preview_or_apply(settings, "step1", "step2", "rules", {}, RESULT, STATS,
            history=read_ops(settings, "step1"))
        preview_or_apply(settings, "step2", "step3", "dedupe_exact", {}, RESULT, STATS,
            history=read_ops(settings, "step2"))
        assert [op["filter"] for op in read_ops(settings, "step3")] == [
            "drop_columns", "rules", "dedupe_exact"]

    def test_writing_onto_the_source_replaces_it_and_keeps_one_history(self, tmp_path):
        """Naming the source as the target is how an operator works in place;
        it must not duplicate the history it just carried forward."""
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        preview_or_apply(settings, "src", "src", "rules", {}, RESULT, STATS,
            history=read_ops(settings, "src"))
        assert len(load_dataset(settings, "src")) == 2
        assert [op["filter"] for op in read_ops(settings, "src")] == ["rules"]

    def test_column_stats_are_recorded_too(self, tmp_path):
        """A column operation reports columns_before/after; the history entry
        keeps the row counters so one list can render both kinds of step."""
        settings = _settings(tmp_path)
        save_dataset(settings, "src", DF)
        stats = {"filter": "drop_columns", "before": 3, "after": 3, "removed": 0,
                 "changed": 1, "columns_before": ["a", "b"], "columns_after": ["a"]}
        out = preview_or_apply(settings, "src", "slim", "drop_columns", {}, RESULT, stats,
            history=read_ops(settings, "src"))
        assert out["columns_after"] == ["a"]
        assert read_ops(settings, "slim")[0]["changed"] == 1
