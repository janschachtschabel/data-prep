/* Tables tab, part 2: changing a table — rules, columns, duplicates, join.

   Part 1 (tables.js) looks at a table; this rewrites it. Every operation goes
   through the same endpoint pair, so they share one submit path: preview when
   no target is named, apply when one is.

   Column names come from imported files, so every select option is built with
   new Option(...) / textContent — never innerHTML. */
"use strict";

const TablesOps = (() => {
  const $ = (sel) => document.querySelector(sel);

  // Operators the API accepts, in the order that reads best in a dropdown.
  const OPERATORS = [
    "eq", "ne", "contains", "starts_with", "ends_with", "regex",
    "gt", "gte", "lt", "lte", "between", "in", "not_in", "is_empty", "not_empty",
  ];
  const NEEDS_NO_VALUE = new Set(["is_empty", "not_empty"]);
  const NEEDS_SECOND = new Set(["between"]);
  const ORDERED = new Set(["lt", "lte", "gt", "gte", "between"]);

  let columns = [];
  let datasets = [];

  function status(message) {
    $("#tops-status").textContent = message;
  }

  function targetName() {
    return $("#tops-target").value.trim();
  }

  /* For the ORDERED operators only, a value that looks like a number is sent as
     one -- that is what makes the API compare numerically ("9" > "10"). For
     eq/ne it must stay text: an id of "007" is not the number 7, and sending
     7 would match "7", "07" and "7.0" as well. The backend documents exactly
     that trap; the UI must not reintroduce it. */
  function coerce(raw) {
    const text = raw.trim();
    if (text === "") return "";
    return /^-?\d+(\.\d+)?$/.test(text) ? Number(text) : text;
  }

  function ruleRow() {
    const row = document.createElement("div");
    row.className = "row rule-row";

    const column = document.createElement("select");
    column.className = "rule-column";
    for (const name of columns) column.add(new Option(name, name));
    column.setAttribute("aria-label", I18n.t("js.tops.aria.column"));

    const operator = document.createElement("select");
    operator.className = "rule-op";
    for (const op of OPERATORS) operator.add(new Option(I18n.t(`js.tops.op.${op}`), op));
    operator.setAttribute("aria-label", I18n.t("js.tops.aria.operator"));

    const value = document.createElement("input");
    value.type = "text";
    value.className = "rule-value";
    value.maxLength = 300;
    value.setAttribute("aria-label", I18n.t("js.tops.aria.value"));

    const second = document.createElement("input");
    second.type = "text";
    second.className = "rule-value2";
    second.maxLength = 300;
    second.hidden = true;
    second.setAttribute("aria-label", I18n.t("js.tops.aria.value2"));

    // The value fields follow the operator: "is empty" takes none, "between"
    // takes two. Showing three boxes for every operator would invite nonsense.
    const sync = () => {
      value.hidden = NEEDS_NO_VALUE.has(operator.value);
      second.hidden = !NEEDS_SECOND.has(operator.value);
    };
    operator.addEventListener("change", sync);
    sync();

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost";
    remove.textContent = I18n.t("js.tops.removeRule");
    remove.addEventListener("click", () => row.remove());

    row.append(column, operator, value, second, remove);
    return row;
  }

  function collectRules() {
    return [...document.querySelectorAll("#tops-rules .rule-row")].map((row) => {
      const op = row.querySelector(".rule-op").value;
      const rule = { column: row.querySelector(".rule-column").value, op };
      if (!NEEDS_NO_VALUE.has(op)) {
        const raw = row.querySelector(".rule-value").value;
        rule.value = (op === "in" || op === "not_in")
          ? raw.split(",").map((s) => s.trim()).filter(Boolean)
          : (ORDERED.has(op) ? coerce(raw) : raw.trim());
      }
      if (NEEDS_SECOND.has(op)) rule.value2 = coerce(row.querySelector(".rule-value2").value);
      return rule;
    });
  }

  function splitList(value) {
    return value.split(",").map((s) => s.trim()).filter(Boolean);
  }

  /* "a=b=c" is left "a", right "b=c": everything after the first "=" belongs to
     the right-hand side rather than being dropped. */
  function splitPair(pair) {
    const at = pair.indexOf("=");
    if (at < 0) return [pair.trim(), ""];
    return [pair.slice(0, at).trim(), pair.slice(at + 1).trim()];
  }

  /* One submit path for every operation: no target means preview. */
  async function run(op, params, button, labelKey, apply) {
    const source = Tables.current();
    if (!source) { status(I18n.t("js.tops.pickDataset")); return; }
    const target = targetName();
    if (apply && !target) { status(I18n.t("js.tops.needTarget")); return; }

    Tables.clearError();
    Tables.busy(button, true, labelKey);
    status(I18n.t("js.tables.working"));
    try {
      const body = { op, params };
      if (apply) body.target = target;
      const res = await Api.post(`/refine/${encodeURIComponent(source)}/op`, body);
      // Refresh FIRST, then report. Applying selects the result so the next step
      // can chain onto it, and that selection change clears the status -- writing
      // the message beforehand meant it vanished the instant it was earned.
      if (apply) await Tables.refreshDatasets(target);
      status(describe(res, apply));
    } catch (err) {
      status("");
      Tables.showError(err.message || I18n.t("js.tops.errRun"));
    } finally {
      Tables.busy(button, false, labelKey);
    }
  }

  function describe(res, applied) {
    const parts = [];
    if (res.columns_after) {
      parts.push(I18n.t("js.tops.resultCols")
        .replace("{before}", res.columns_before.length)
        .replace("{after}", res.columns_after.length));
    } else {
      parts.push(I18n.t("js.tops.resultRows")
        .replace("{before}", res.before.toLocaleString(I18n.current()))
        .replace("{after}", res.after.toLocaleString(I18n.current()))
        .replace("{removed}", res.removed.toLocaleString(I18n.current())));
    }
    // A rule aimed at the wrong column shows up here and nowhere else.
    if (res.not_comparable) {
      parts.push(I18n.t("js.tops.notComparable")
        .replace("{n}", res.not_comparable.toLocaleString(I18n.current())));
    }
    parts.push(applied ? I18n.t("js.tops.written").replace("{target}", res.target)
                       : I18n.t("js.tops.previewOnly"));
    return parts.join(" ");
  }

  async function duplicateReport() {
    const source = Tables.current();
    const keys = splitList($("#tops-dupe-keys").value);
    if (!source || !keys.length) { status(I18n.t("js.tops.needKeys")); return; }
    const button = $("#tops-dupe-report");
    Tables.clearError();
    Tables.busy(button, true, "tops.dupes.report");
    try {
      const params = new URLSearchParams();
      for (const key of keys) params.append("keys", key);
      const res = await Api.get(`/refine/${encodeURIComponent(source)}/duplicates?${params}`);
      status(I18n.t("js.tops.dupeReport")
        .replace("{groups}", res.duplicate_groups.toLocaleString(I18n.current()))
        .replace("{removable}", res.removable_rows.toLocaleString(I18n.current()))
        .replace("{empty}", res.empty_key_rows.toLocaleString(I18n.current())));
    } catch (err) {
      status("");
      Tables.showError(err.message || I18n.t("js.tops.errRun"));
    } finally {
      Tables.busy(button, false, "tops.dupes.report");
    }
  }

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
      const res = await Api.post(`/refine/${encodeURIComponent(source)}/join`, body);
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

  function columnParams() {
    const mode = $("#tops-cols-mode").value;
    const raw = $("#tops-cols-value").value;
    if (mode === "rename_columns") {
      const mapping = {};
      for (const pair of splitList(raw)) {
        const [from, to] = splitPair(pair);
        if (from && to) mapping[from] = to;
      }
      return [mode, { mapping }];
    }
    return [mode, mode === "select_columns" ? { keep: splitList(raw) } : { drop: splitList(raw) }];
  }

  function selectionChanged() {
    // Clearing here is safe because run()/join() report AFTER refreshing, so an
    // apply's message is written once this has already run.
    status("");
    const ds = datasets.find((d) => d.name === Tables.current());
    columns = ds ? ds.columns : [];
    $("#tops-cols-available").textContent = columns.length
      ? I18n.t("js.tops.available").replace("{cols}", columns.join(", "))
      : "";
    // Existing rule rows point at the previous dataset's columns.
    $("#tops-rules").replaceChildren();
    if (columns.length) $("#tops-rules").appendChild(ruleRow());
  }

  function datasetsChanged(list) {
    datasets = list;
    const select = $("#tops-join-right");
    const previous = select.value;
    select.replaceChildren(new Option(I18n.t("js.tables.phDataset"), ""));
    for (const ds of list) select.add(new Option(ds.name, ds.name));
    if (previous && list.some((d) => d.name === previous)) select.value = previous;
    selectionChanged();
  }

  function init() {
    $("#tops-rule-add").addEventListener("click", () => {
      if (columns.length) $("#tops-rules").appendChild(ruleRow());
    });
    $("#tops-rules-preview").addEventListener("click", () => run(
      "rules", { rules: collectRules(), combine: $("#tops-combine").value },
      $("#tops-rules-preview"), "tops.preview", false));
    $("#tops-rules-apply").addEventListener("click", () => run(
      "rules", { rules: collectRules(), combine: $("#tops-combine").value },
      $("#tops-rules-apply"), "tops.apply", true));

    $("#tops-cols-preview").addEventListener("click", () => {
      const [op, params] = columnParams();
      run(op, params, $("#tops-cols-preview"), "tops.preview", false);
    });
    $("#tops-cols-apply").addEventListener("click", () => {
      const [op, params] = columnParams();
      run(op, params, $("#tops-cols-apply"), "tops.apply", true);
    });

    $("#tops-dupe-report").addEventListener("click", duplicateReport);
    $("#tops-dupe-apply").addEventListener("click", () => run(
      "dedupe_keys",
      { keys: splitList($("#tops-dupe-keys").value), keep: $("#tops-dupe-keep").value },
      $("#tops-dupe-apply"), "tops.dupes.remove", true));

    $("#tops-join-preview").addEventListener("click", () => join(false));
    $("#tops-join-apply").addEventListener("click", () => join(true));
  }

  init();
  return { datasetsChanged, selectionChanged };
})();
