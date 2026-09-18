"""Run a table operation, then preview or record its result.

Two halves that belong together. :data:`TABLE_OPS` dispatches the operations of
the table layer, mirroring ``filters.FILTERS`` for the label layer.
:func:`preview_or_apply` then decides what becomes of the result.

The second half is shared with the label filters, which dispatch through their
own registry -- they need an embedding model in their context, these know
nothing about labels -- but what happens AFTERWARDS is identical, and it is the
part that matters most: the operation history is what makes a finished dataset
reconstructable. Written twice, the two routes would have drifted.

Routes therefore only translate HTTP; neither registry nor history handling
lives in them.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from ..security import refuse_existing
from ..settings import Settings
from .columns import drop_columns, rename_columns, select_columns
from .duplicates import dedupe_keys
from .rules import filter_rows
from .store import dataset_path, save_dataset, write_ops

# The table layer's operations, mirroring filters.FILTERS for the label layer.
# Kept beside preview_or_apply so one module answers "run a step, then record
# it" -- the routes only translate HTTP.
TABLE_OPS: dict[str, Callable[..., tuple[pd.DataFrame, dict]]] = {
    "rules": filter_rows,
    "select_columns": select_columns,
    "drop_columns": drop_columns,
    "rename_columns": rename_columns,
    "dedupe_keys": dedupe_keys,
}


def run_table_op(name: str, df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Dispatch to one table operation; raises ``ValueError`` for an unknown name."""
    operation = TABLE_OPS.get(name)
    if operation is None:
        raise ValueError(
            f"Unknown operation {name!r}. Available: {', '.join(sorted(TABLE_OPS))}."
        )
    return operation(df, params, ctx)


def replaces_another(settings: Settings, source: str, target: str) -> bool:
    """True when writing ``target`` would replace a dataset OTHER than ``source``.

    Naming the source itself is working in place -- the history is carried and
    nothing unrelated is lost -- so only a different, existing name counts."""
    return target != source and dataset_path(settings, target).exists()


def preview_or_apply(
    settings: Settings,
    source: str,
    target: str | None,
    op_name: str,
    params: dict,
    new_df: pd.DataFrame,
    stats: dict,
    *,
    history: list[dict],
    overwrite: bool = False,
) -> dict:
    """Return ``stats`` alone (preview) or write ``new_df`` to ``target``.

    ``target`` is ``None`` for a preview, which writes nothing at all -- a wrong
    filter must never cost the input. When a target IS named, ``history`` is
    carried forward and this operation appended, so three steps read as three
    steps on the final dataset. ``history`` is the source's, read together with
    the table the step ran on (``load_with_history_or_404``): read here, at the
    write, it could already describe a newer version of the source.

    Applying refuses to replace a dataset other than the source unless
    ``overwrite``. That is checked here, at the write: the step itself ran in a
    worker thread, and the name may have been taken meanwhile. Routes run an apply
    through :func:`app.refine.store.in_store`, so nothing comes between the check
    and the write; a preview touches no file and runs where it is called.

    ``target`` must already have passed :func:`safe_name` at the route boundary.
    """
    if target is None:
        return {**stats, "preview": True}

    refuse_existing(replaces_another(settings, source, target), "Dataset", target, overwrite)
    # Dataset first, history second, and the history in ONE write. Each write is
    # atomic on its own, so the only window a crash can leave is "new dataset,
    # stale history" -- a valid state whose provenance is merely behind. Written
    # as history-then-append, a failure between the two left the SOURCE's steps
    # under the target's name: a history that lied about how the dataset was made.
    save_dataset(settings, target, new_df)
    write_ops(settings, target, [*history, {
        "filter": op_name,
        "params": params,
        "source": source,
        "before": stats["before"],
        "after": stats["after"],
        "removed": stats["removed"],
        "changed": stats["changed"],
    }])
    return {**stats, "preview": False, "target": target}
