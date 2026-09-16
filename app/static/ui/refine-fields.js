/* What the chosen text columns hold, and the balancing that needs to know.

   A CSV says nothing about whether a cell is one value or a list: a keyword cell
   separated by commas is three values, a title containing a comma is one title.
   The LLM operations need that distinction, so it is asked once — here — and both
   enrich and balance read it from `RefineFields.spec()`.

   Its own module rather than more of refine.js, which is already the size this
   project splits at. textContent / table rendering only. */
"use strict";

const RefineFields = (() => {
  const $ = (sel) => document.querySelector(sel);

  // Remembered per column, so retyping the text-column list does not discard what
  // was said about a field that is still in it.
  const declared = new Map();

  function chosenColumns() {
    return $("#refine-text-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
  }

  // What the WLO export's own columns hold, so its keyword column starts as the list
  // it is. The hints are PROMPT text: they reach a German prompt whatever language
  // the UI shows, so they are not translated. They repeat the guidance the enrich
  // route gives requests in the old shape (routes/refine_prep.py).
  const DEFAULTS = {
    "properties.cclom:general_keyword": {
      list: true, separator: ",", min: 3,
      guidance: "Nenne 3-6 treffende deutsche Schlagwörter (kommagetrennt), die den Inhalt erschließen.",
    },
    "properties.cclom:general_description": {
      list: false, separator: ",", min: 1,
      guidance: "Schreibe eine sachliche Beschreibung (100-400 Zeichen), was dieses Material bietet.",
    },
  };
  const BLANK = { list: false, separator: ",", min: 1, guidance: "" };

  function row(column) {
    const saved = declared.get(column) || DEFAULTS[column] || BLANK;
    const wrap = document.createElement("div");
    wrap.className = "field-spec";
    wrap.dataset.column = column;

    const name = document.createElement("code");
    name.className = "field-spec-name";
    name.textContent = column;

    const listLabel = document.createElement("label");
    // `check` is the app's checkbox label: it is the real click target, and it
    // carries the 24px minimum the bare 13px box cannot meet (WCAG 2.2 SC 2.5.8).
    listLabel.className = "check";
    const list = document.createElement("input");
    list.type = "checkbox";
    list.className = "field-spec-list";
    list.checked = saved.list;
    listLabel.append(list, document.createTextNode(" " + I18n.t("refine.fields.islist")));

    const separator = document.createElement("input");
    separator.type = "text";
    separator.className = "field-spec-sep";
    separator.maxLength = 3;
    separator.size = 2;
    separator.value = saved.separator;
    separator.setAttribute("aria-label", I18n.t("refine.fields.separator"));

    const min = document.createElement("input");
    min.type = "number";
    min.className = "field-spec-min";
    min.min = "1";
    min.max = "50";
    min.value = String(saved.min);
    min.setAttribute("aria-label", I18n.t("refine.fields.minvalues"));

    const guidance = document.createElement("input");
    guidance.type = "text";
    guidance.className = "field-spec-guidance";
    guidance.maxLength = 1000;
    guidance.value = saved.guidance;
    guidance.placeholder = I18n.t("refine.fields.guidance.ph");
    guidance.setAttribute("aria-label", I18n.t("refine.fields.guidance"));

    // A separator and a minimum only mean something for a list; disabled rather
    // than hidden, so the row keeps its shape and nothing jumps when it is ticked.
    const syncEnabled = () => {
      separator.disabled = !list.checked;
      min.disabled = !list.checked;
    };
    syncEnabled();
    list.addEventListener("change", syncEnabled);
    for (const control of [list, separator, min, guidance]) {
      control.addEventListener("change", remember);
    }

    wrap.append(name, listLabel, separator, min, guidance);
    return wrap;
  }

  function remember() {
    for (const el of document.querySelectorAll("#refine-fields .field-spec")) {
      declared.set(el.dataset.column, {
        list: el.querySelector(".field-spec-list").checked,
        separator: el.querySelector(".field-spec-sep").value || ",",
        min: Number(el.querySelector(".field-spec-min").value) || 1,
        guidance: el.querySelector(".field-spec-guidance").value.trim(),
      });
    }
  }

  /* The rows, and the field pickers that depend on them. Called whenever the
     chosen columns change — retyping the list must not leave a stale picker. */
  function render() {
    const columns = chosenColumns();
    $("#refine-fields").replaceChildren(...columns.map(row));
    $("#refine-fields-empty").hidden = columns.length > 0;

    const picker = $("#enrich-field");
    const previous = picker.value;
    picker.replaceChildren(...columns.map((col) => new Option(col, col)));
    if (columns.includes(previous)) picker.value = previous;
    invalidatePreview();   // the fields changed, or the dataset they came from did
  }

  /* What the API calls send: one entry per chosen column. A column not ticked as a
     list has no separator, which is what makes `read_values` treat its cell as one
     value however many commas it contains. */
  function spec() {
    return [...document.querySelectorAll("#refine-fields .field-spec")].map((el) => {
      const isList = el.querySelector(".field-spec-list").checked;
      const field = { column: el.dataset.column };
      if (isList) {
        field.separator = el.querySelector(".field-spec-sep").value || ",";
        field.min_values = Number(el.querySelector(".field-spec-min").value) || 1;
      }
      const guidance = el.querySelector(".field-spec-guidance").value.trim();
      if (guidance) field.guidance = guidance;
      return field;
    });
  }

  /* ---- balancing: preview what it would cost, then generate ---- */

  // The request the plan on screen was made for. The run button follows it, and a
  // run is sent only for exactly that request: dataset, fields, label, target and
  // limit can all change without an event this module sees — refine.js switches
  // the dataset after every write — so the click compares against what would be
  // sent NOW.
  let armedFor = null;

  function currentRequest() {
    return JSON.stringify([$("#refine-dataset").value, balanceBody({})]);
  }

  function syncRunButton() {
    $("#balance-run-btn").disabled = armedFor === null;
  }

  function invalidatePreview() {
    armedFor = null;
    syncRunButton();
  }

  function hasFields() {
    if (spec().length) return true;
    Refine.showError(I18n.t("js.refine.needsTextColumns"));
    return false;
  }

  function balanceBody(extra) {
    return {
      fields: spec(),
      label_column: $("#refine-label-col").value.trim() || undefined,
      target_per_label: Number($("#balance-target").value) || 1,
      limit: Number($("#balance-limit").value) || 500,
      ...extra,
    };
  }

  // A 300-label dataset would otherwise render 300 rows nobody reads; the ones that
  // matter are the shortest, and they are sorted to the top.
  const MAX_ROWS_SHOWN = 30;

  function percent(share) {
    return new Intl.NumberFormat(I18n.current(), { style: "percent" }).format(share);
  }

  // "100.000.000", not "100000000": a budget has to be readable at a glance.
  function number(value) {
    return new Intl.NumberFormat(I18n.current()).format(value);
  }

  /* The preview is also the empty state: "nothing is short" has to be said, or an
     empty table reads as a failure. */
  function renderPlan(plan) {
    const out = $("#balance-result");
    const summary = document.createElement("p");
    const short = Object.entries(plan.per_label).filter(([, e]) => e.deficit > 0);
    summary.textContent = plan.rows_to_add
      ? I18n.t("js.refine.balancePlan", {
        count: plan.labels_below_target, rows: number(plan.rows_to_add),
        calls: number(plan.batches), maxCalls: number(plan.max_calls),
        budget: number(plan.call_budget), target: number(plan.target_per_label),
      })
      : I18n.t("js.refine.balanceNothing", { target: number(plan.target_per_label) });
    out.replaceChildren(summary);

    if (short.length) {
      short.sort((a, b) => b[1].deficit - a[1].deficit);
      const table = document.createElement("div");
      Refine.renderTable(table, [
        I18n.t("js.refine.thLabel"), I18n.t("js.refine.thSupport"),
        I18n.t("js.refine.thDeficit"), I18n.t("js.refine.thPlanned"),
        I18n.t("js.refine.thSynthetic"),
      ], short.slice(0, MAX_ROWS_SHOWN).map(([label, e]) =>
        [label, number(e.support), number(e.deficit), number(e.planned),
          percent(e.synthetic_share)]));
      out.appendChild(table);
      if (short.length > MAX_ROWS_SHOWN) {
        const more = document.createElement("p");
        more.className = "muted";
        more.textContent = I18n.t("js.refine.balanceMore",
          { count: short.length - MAX_ROWS_SHOWN });
        out.appendChild(more);
      }
    }
    note(out, "js.refine.balanceCut", plan.labels_cut_by_limit);
    if (plan.skipped_without_examples.length) {
      const skipped = document.createElement("p");
      skipped.className = "muted";
      skipped.textContent = I18n.t("js.refine.balanceSkipped", {
        count: plan.skipped_without_examples.length,
        labels: plan.skipped_without_examples.slice(0, 5).join(", "),
      });
      out.appendChild(skipped);
    }
    out.hidden = false;
  }

  /* A muted line naming up to five labels, when there are any. */
  function note(out, key, labels) {
    if (!labels || !labels.length) return;
    const line = document.createElement("p");
    line.className = "muted";
    line.textContent = I18n.t(key, { count: labels.length, labels: labels.slice(0, 5).join(", ") });
    out.appendChild(line);
  }

  /* Busy while `work` runs. Afterwards the label comes from its key — a language
     switch meanwhile would otherwise bring the old language back — and the
     disabled state from `settle`, so a finished run cannot re-arm the run button. */
  async function withBusy(button, busyKey, idleKey, settle, work) {
    button.disabled = true;
    button.textContent = I18n.t(busyKey);
    try {
      await work();
    } finally {
      button.textContent = I18n.t(idleKey);
      settle();
    }
  }

  $("#balance-preview-btn").addEventListener("click", () => {
    const button = $("#balance-preview-btn");
    invalidatePreview();
    if (!hasFields()) return undefined;
    const request = currentRequest();
    return withBusy(button, "js.refine.balancePreviewing", "refine.balance.preview",
      () => { button.disabled = false; },
      () => Refine.runOp("balance", balanceBody({ dry_run: true }), (plan) => {
        renderPlan(plan);
        // Only a preview arms the run, and only for the request it was made for.
        armedFor = plan.rows_to_add > 0 ? request : null;
        syncRunButton();
      }, null));
  });

  $("#balance-run-btn").addEventListener("click", () => {
    const button = $("#balance-run-btn");
    if (armedFor === null || armedFor !== currentRequest()) {
      // No preview, or the form changed since: the cost has to be shown again first.
      invalidatePreview();
      Refine.showError(I18n.t("js.refine.balanceStale"));
      return undefined;
    }
    const target = $("#balance-name").value.trim();
    if (!target) { Refine.showError(I18n.t("js.refine.balanceNeedsName")); return undefined; }
    return withBusy(button, "js.refine.balanceRunning", "refine.balance.run", syncRunButton,
      () => Refine.runOp("balance", balanceBody({ target }), async (res) => {
        invalidatePreview();          // the plan described the source, not the result
        const out = $("#balance-result");
        const done = document.createElement("p");
        done.textContent = I18n.t("js.refine.balanced", {
          count: res.rows_added, labels: number(res.labels_filled), target: res.target,
          calls: number(res.usage.calls), tokens: number(res.usage.tokens_total),
        });
        out.replaceChildren(done);
        note(out, "js.refine.balanceCut", res.labels_cut_by_limit);
        out.hidden = false;
        await Refine.refreshDatasets(res.target);
      }, target));
  });

  // Any change to what would be generated makes the shown plan stale.
  for (const sel of ["#refine-text-cols", "#refine-label-col", "#balance-target",
    "#balance-limit", "#refine-dataset"]) {
    $(sel).addEventListener("change", invalidatePreview);
  }
  $("#refine-fields").addEventListener("change", invalidatePreview);

  $("#refine-text-cols").addEventListener("change", render);
  window.addEventListener("dataprep-lang-changed", render);

  return { render, spec };
})();
