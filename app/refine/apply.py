"""What happens to an operation's result: preview it, or apply and record it.

Label filters and table operations dispatch differently -- one needs an
embedding model in its context, the other knows nothing about labels -- but
what happens afterwards is identical, and it is the part that matters most: the
operation history is what makes a finished dataset reconstructable.

Kept here rather than in either route so the two cannot drift apart. The caller
runs its own operation and hands over the result, which keeps this module free
of any operation registry.
"""

from __future__ import annotations

import pandas as pd

from ..security import safe_name
from ..settings import Settings
from .store import append_op, read_ops, save_dataset, write_ops


def preview_or_apply(
    settings: Settings,
    source: str,
    target: str | None,
    op_name: str,
    params: dict,
    new_df: pd.DataFrame,
    stats: dict,
) -> dict:
    """Return ``stats`` alone (preview) or write ``new_df`` to ``target``.

    ``target`` is ``None`` for a preview, which writes nothing at all -- a wrong
    filter must never cost the input. When a target IS named, the source's
    history is carried forward and this operation appended, so three steps read
    as three steps on the final dataset.

    ``target`` must already have passed :func:`safe_name` at the route boundary.
    """
    if target is None:
        return {**stats, "preview": True}

    # Read the source's history BEFORE writing, so that naming the source as the
    # target (working in place) keeps one history instead of duplicating it.
    history = read_ops(settings, safe_name(source, "dataset name"))
    save_dataset(settings, target, new_df)
    write_ops(settings, target, history)
    append_op(settings, target, {
        "filter": op_name,
        "params": params,
        "source": source,
        "before": stats["before"],
        "after": stats["after"],
        "removed": stats["removed"],
        "changed": stats["changed"],
    })
    return {**stats, "preview": False, "target": target}
