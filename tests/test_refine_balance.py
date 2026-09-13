"""Refine balancing: lift an unevenly distributed dataset to a minimum number of
rows per label by generating the missing ones from the label's own examples.

The preview (``plan_balance``) is pure: it says what would be generated and what it
would cost, before a single LLM call is paid for.
"""

from __future__ import annotations

import pandas as pd

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"


def _fields():
    from app.refine.fields import TextField

    return [
        TextField(column=TITLE, guidance="Ein kurzer, konkreter Titel."),
        TextField(column=DESC, guidance="Zwei bis drei Sätze zum Inhalt."),
        TextField(column=KEYW, separator=",", min_values=3,
                  guidance="3-6 Schlagwörter, kommagetrennt."),
    ]


def _rows(spec: list[tuple[str, str]]) -> pd.DataFrame:
    """``[(label, title), …]`` as a frame; the other text fields follow the title, so
    an empty title makes a row that carries no text at all."""
    return pd.DataFrame(
        [[title, f"Beschreibung zu {title}." if title else "", "a, b, c" if title else "", label]
         for label, title in spec],
        columns=[TITLE, DESC, KEYW, LABEL],
    )


# ------------------------------------------------------------ plan_balance ----


def test_plan_reports_deficit_per_label_and_the_batches_it_would_cost():
    """The preview is the one thing a user sees before paying for generation, so it
    has to name the cost: how many rows per label, and how many model calls."""
    from app.refine.balance import plan_balance

    df = _rows([("Mathematik", f"Titel {i}") for i in range(12)]
               + [("Physik", f"Optik {i}") for i in range(2)])

    plan = plan_balance(df, fields=_fields(), label_column=LABEL,
                        target_per_label=10, batch_size=10)

    assert plan["target"] == 10
    assert plan["per_label"]["Mathematik"] == {
        "support": 12, "deficit": 0, "batches": 0, "synthetic_share": 0.0}
    assert plan["per_label"]["Physik"] == {
        "support": 2, "deficit": 8, "batches": 1, "synthetic_share": 0.8}
    assert plan["labels_below_target"] == 1
    assert plan["rows_to_add"] == 8
    assert plan["batches"] == 1


def test_a_deficit_larger_than_one_batch_is_split_into_several():
    """Nothing asks a model for 97 items in one answer: the deficit is chunked, and
    the number of chunks is what the estimate is built from."""
    from app.refine.balance import plan_balance

    df = _rows([("Physik", "Optik")])
    plan = plan_balance(df, fields=_fields(), label_column=LABEL,
                        target_per_label=100, batch_size=10)

    assert plan["per_label"]["Physik"]["deficit"] == 99
    assert plan["per_label"]["Physik"]["batches"] == 10  # ceil(99 / 10)
    assert plan["per_label"]["Physik"]["synthetic_share"] == 0.99
    assert plan["batches"] == 10


def test_a_label_without_a_usable_example_is_skipped_not_invented():
    """Generation works from examples. A label whose rows carry no text at all gives
    the model nothing to imitate, and inventing rows from the bare label name would
    produce exactly the generic filler this feature exists to avoid."""
    from app.refine.balance import plan_balance

    df = _rows([("Physik", "Optik"), ("Leerlabel", "")])
    plan = plan_balance(df, fields=_fields(), label_column=LABEL,
                        target_per_label=5, batch_size=10)

    assert plan["skipped_without_examples"] == ["Leerlabel"]
    assert "Leerlabel" not in plan["per_label"]
    assert plan["rows_to_add"] == 4  # only Physik's deficit is counted


def test_nothing_to_do_is_stated_rather_than_implied():
    """An already-balanced dataset must produce an explicit zero, so the UI can say
    "nothing to do" instead of showing an empty table that looks like a failure."""
    from app.refine.balance import plan_balance

    df = _rows([("Mathematik", f"T{i}") for i in range(5)]
               + [("Physik", f"P{i}") for i in range(5)])
    plan = plan_balance(df, fields=_fields(), label_column=LABEL,
                        target_per_label=5, batch_size=10)

    assert plan["rows_to_add"] == 0
    assert plan["batches"] == 0
    assert plan["labels_below_target"] == 0
    assert plan["skipped_without_examples"] == []


# ------------------------------------------------------ build_balance_prompt ----


def _examples():
    return [
        {TITLE: "Optik Grundlagen", DESC: "Licht und Brechung erklärt.",
         KEYW: "Optik,Licht,  Brechung "},
        {TITLE: "Linsen im Alltag", DESC: "Brille, Lupe, Kamera.", KEYW: "Linse, Auge"},
    ]


def test_the_prompt_shows_the_examples_names_every_field_and_lists_avoided_titles():
    """Everything the model needs is in one prompt: what the label looks like in this
    dataset (the examples), what each field is for (the guidance), and which titles
    already exist — the last one is what keeps a batch from collapsing into variants
    of the same item."""
    from app.refine.balance_prompt import build_balance_prompt

    prompt = build_balance_prompt(
        "Physik", _examples(), _fields(), n=5, avoid_titles=["Optik Grundlagen"])

    assert "Physik" in prompt
    assert "Optik Grundlagen" in prompt
    assert "Licht und Brechung erklärt." in prompt
    assert "Brille, Lupe, Kamera." in prompt
    for field in _fields():
        assert prompt.count(field.column) == 1, f"{field.column} named more than once"
        assert field.guidance in prompt


def test_a_list_field_reaches_the_model_as_a_cell_not_as_a_python_list():
    """The model answers in the format it is shown. Showing it ``['Optik', 'Licht']``
    teaches it to write a Python repr into the column; showing it the cell as the
    dataset actually stores it teaches it the separator."""
    from app.refine.balance_prompt import build_balance_prompt

    prompt = build_balance_prompt("Physik", _examples(), _fields(), n=5, avoid_titles=[])

    assert "Optik, Licht, Brechung" in prompt  # normalised through the field
    assert "['Optik'" not in prompt
    assert "kommagetrennt" in prompt  # the separator is stated, not only implied


def test_without_examples_the_prompt_is_not_built():
    """A prompt with no examples is the generic-filler case the preview already
    refuses; reaching it here means a caller skipped the plan."""
    import pytest

    from app.refine.balance_prompt import build_balance_prompt

    with pytest.raises(ValueError, match="no examples"):
        build_balance_prompt("Physik", [], _fields(), n=5, avoid_titles=[])


# ---------------------------------------------------------- balance_dataset ----


def _scripted(batches: list[list[list[str]]]):
    """A ``complete`` that returns the given item batches in order, then empty ones."""
    calls: list[str] = []

    async def complete(prompt, schema):
        calls.append(prompt)
        index = len(calls) - 1
        values = batches[index] if index < len(batches) else []
        return schema(items=[{"values": item} for item in values])

    return complete, calls


def _item(title: str) -> list[str]:
    return [title, f"Eine Beschreibung zu {title} mit genug Inhalt.", "a, b, c"]


def test_a_short_label_is_filled_to_the_target_and_the_new_rows_are_marked():
    """The core promise: after balancing, the label has as many rows as asked for,
    and every added row says which label it was generated for — without that mark
    the holdout split cannot keep synthetic text out of the evaluation."""
    import asyncio

    from app.refine.balance import balance_dataset

    df = _rows([("Physik", "Optik"), ("Physik", "Mechanik")]
               + [("Mathematik", f"M{i}") for i in range(5)])
    complete, calls = _scripted([[_item("Akustik"), _item("Thermo"), _item("Statik")]])

    new, stats = asyncio.run(balance_dataset(
        df, fields=_fields(), label_column=LABEL, target_per_label=5, complete=complete))

    assert len(calls) == 1, "one batch covers a deficit of three"
    assert len(new) == len(df) + 3
    added = new[new["generated_for"] != ""]
    assert len(added) == 3
    assert set(added[LABEL]) == {"Physik"}
    assert set(added["generated_for"]) == {"Physik"}
    assert set(added["enriched_fields"]) == {""}, "generated is not gap-filled"
    assert added.iloc[0][TITLE] == "Akustik"
    assert stats["rows_added"] == 3
    assert stats["labels_filled"] == 1
    assert stats["per_label"]["Physik"]["added"] == 3


def test_the_untouched_label_keeps_its_rows_and_its_empty_mark():
    """Balancing must not touch a label that is already at target — and the real rows
    must end up with an EMPTY generated_for, not a missing one."""
    import asyncio

    from app.refine.balance import balance_dataset

    df = _rows([("Mathematik", f"M{i}") for i in range(5)])
    complete, calls = _scripted([])

    new, stats = asyncio.run(balance_dataset(
        df, fields=_fields(), label_column=LABEL, target_per_label=5, complete=complete))

    assert calls == [], "nothing was short, so nothing was paid for"
    assert len(new) == 5
    assert list(new["generated_for"]) == [""] * 5
    assert stats["rows_added"] == 0


def test_an_item_the_model_repeats_is_dropped_and_retried():
    """Prototype collapse is the known failure of batch generation. A repeat of an
    existing row is not a new row, so it is discarded and the shortfall is asked for
    again rather than silently accepted."""
    import asyncio

    from app.refine.balance import balance_dataset

    df = _rows([("Physik", "Optik")])
    complete, calls = _scripted([
        [_item("Akustik"), _item("Akustik")],   # the second is its own duplicate
        [_item("Thermo")],
    ])

    new, stats = asyncio.run(balance_dataset(
        df, fields=_fields(), label_column=LABEL, target_per_label=3, complete=complete))

    assert len(calls) == 2, "the shortfall triggered a second batch"
    titles = list(new[new["generated_for"] != ""][TITLE])
    assert titles == ["Akustik", "Thermo"]
    assert stats["per_label"]["Physik"]["discarded_duplicate"] == 1


def test_an_incomplete_item_is_discarded_with_the_reason():
    """A field below its min_values is a gap by the dataset's own definition; writing
    it would produce exactly the rows the enrichment step exists to repair."""
    import asyncio

    from app.refine.balance import balance_dataset

    df = _rows([("Physik", "Optik")])
    complete, _ = _scripted([[["Akustik", "Text dazu.", "nur-eins"]]])  # keywords < 3

    new, stats = asyncio.run(balance_dataset(
        df, fields=_fields(), label_column=LABEL, target_per_label=2, complete=complete))

    assert len(new) == 1, "nothing acceptable was produced"
    assert stats["per_label"]["Physik"]["discarded_incomplete"] == 1
    assert stats["per_label"]["Physik"]["missing"] == 1


def test_limit_caps_what_one_call_may_generate():
    """The cost ceiling. Two labels each short by 3, limit 2: the run stops at two
    rows instead of spending the whole budget of a 300-label dataset."""
    import asyncio

    from app.refine.balance import balance_dataset

    df = _rows([("Physik", "Optik"), ("Chemie", "Säuren")])
    complete, _ = _scripted([[_item("A"), _item("B"), _item("C")],
                             [_item("D"), _item("E"), _item("F")]])

    new, stats = asyncio.run(balance_dataset(
        df, fields=_fields(), label_column=LABEL, target_per_label=4,
        complete=complete, limit=2))

    assert stats["rows_added"] == 2
    assert len(new) == 4


def test_a_foreign_generated_for_column_stops_the_run():
    """If a dataset already has a column of that name holding something else, writing
    provenance into it would make holdout_split drop REAL rows from the evaluation.
    That is silent, so the run refuses instead."""
    import asyncio

    import pytest

    from app.refine.balance import balance_dataset

    df = _rows([("Physik", "Optik")])
    df["generated_for"] = "Notiz der Redaktion"
    complete, _ = _scripted([])

    with pytest.raises(ValueError, match="generated_for"):
        asyncio.run(balance_dataset(df, fields=_fields(), label_column=LABEL,
                                    target_per_label=3, complete=complete))
