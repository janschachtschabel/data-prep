/* Tables tab, part 3: joining two datasets.

   Split out of tables-ops.js along a real seam rather than a line count: the
   join has its own request shape (JoinRequest, not OperationRequest), its own
   endpoint, and its own preview semantics -- a preview reports the CARDINALITY
   of the result rather than the result itself, because the size is the
   dangerous number and computing it costs nothing while materialising it is
   the very thing worth avoiding.

   Loads after tables-ops.js and borrows its status line, target field and
   list parsers, so both cards on the "Change the table" panel behave alike. */
"use strict";

const TablesJoin = (() => {
  const $ = (sel) => document.querySelector(sel);
  // A hard load-order dependency, stated rather than hidden: the join lives on
  // the same card as the other operations and shares their status line.
  const { status, targetName, splitList, splitPair } = TablesOps;

  function joinKeys() {
    // "id=uid, jahr=year" -> the pairs the API expects.
    return splitList($("#tops-join-keys").value).map((pair) => {
      const [left, right] = splitPair(pair);
      return { left, right: right || left };
    });
  }

  async function join(apply) {
    const source = Tables.current();
    const right = $("#tops-join-right").value;
    if (!source || !right) { status(I18n.t("js.tops.needSecond")); return; }
    const target = targetName();
    if (apply && !target) { status(I18n.t("js.tops.needTarget")); return; }

    const button = apply ? $("#tops-join-apply") : $("#tops-join-preview");
    const labelKey = apply ? "tops.apply" : "tops.join.check";
    Tables.clearError();
    Tables.busy(button, true, labelKey);
    status(I18n.t("js.tables.working"));
    try {
      const body = {
        right, keys: joinKeys(), how: $("#tops-join-how").value,
        coalesce: $("#tops-join-coalesce").checked,
      };
      if (apply) body.target = target;
      const res = await Api.postGuarded(`/refine/${encodeURIComponent(source)}/join`, body, target);
      if (res.preview) {
        // The size BEFORE the join is the number that matters: a key repeating
        // on both sides multiplies rows, and that is the trap worth naming.
        status(I18n.t("js.tops.joinPreview")
          .replace("{relation}", res.relation)
          .replace("{matching}", res.matching_keys.toLocaleString(I18n.current()))
          .replace("{rows}", res.estimated_rows.toLocaleString(I18n.current()))
          + (res.explodes ? " " + I18n.t("js.tops.joinExplodes") : ""));
      } else {
        await Tables.refreshDatasets(target);
        status(I18n.t("js.tops.joinDone")
          .replace("{after}", res.after.toLocaleString(I18n.current()))
          .replace("{matched}", res.matched_rows.toLocaleString(I18n.current()))
          .replace("{unmatched}", res.unmatched_left.toLocaleString(I18n.current()))
          .replace("{target}", res.target));
      }
    } catch (err) {
      status("");
      Tables.showError(err.message || I18n.t("js.tops.errRun"));
    } finally {
      Tables.busy(button, false, labelKey);
    }
  }

  /* The second-dataset dropdown follows the dataset list; the current choice
     survives a refresh when it still exists. */
  function datasetsChanged(list) {
    const select = $("#tops-join-right");
    const previous = select.value;
    select.replaceChildren(new Option(I18n.t("js.tables.phDataset"), ""));
    for (const ds of list) select.add(new Option(ds.name, ds.name));
    if (previous && list.some((d) => d.name === previous)) select.value = previous;
  }

  $("#tops-join-preview").addEventListener("click", () => join(false));
  $("#tops-join-apply").addEventListener("click", () => join(true));

  return { datasetsChanged };
})();
