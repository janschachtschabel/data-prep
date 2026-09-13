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

  function row(column) {
    const saved = declared.get(column) || { list: false, separator: ",", min: 1, guidance: "" };
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

  function invalidatePreview() {
    $("#balance-run-btn").disabled = true;
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

  /* The preview is also the empty state: "nothing is short" has to be said, or an
     empty table reads as a failure. */
  function renderPlan(plan) {
    const out = $("#balance-result");
    const summary = document.createElement("p");
    const short = Object.entries(plan.per_label).filter(([, e]) => e.deficit > 0);
    summary.textContent = plan.rows_to_add
      ? I18n.t("js.refine.balancePlan", {
        count: plan.labels_below_target, rows: plan.rows_to_add,
        calls: plan.batches, target: plan.target,
      })
      : I18n.t("js.refine.balanceNothing", { target: plan.target });
    out.replaceChildren(summary);

    if (short.length) {
      short.sort((a, b) => b[1].deficit - a[1].deficit);
      const table = document.createElement("div");
      Refine.renderTable(table, [
        I18n.t("js.refine.thLabel"), I18n.t("js.refine.thSupport"),
        I18n.t("js.refine.thDeficit"), I18n.t("js.refine.thSynthetic"),
      ], short.slice(0, MAX_ROWS_SHOWN).map(([label, e]) =>
        [label, e.support, e.deficit, percent(e.synthetic_share)]));
      out.appendChild(table);
      if (short.length > MAX_ROWS_SHOWN) {
        const more = document.createElement("p");
        more.className = "muted";
        more.textContent = I18n.t("js.refine.balanceMore",
          { count: short.length - MAX_ROWS_SHOWN });
        out.appendChild(more);
      }
    }
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

  async function withBusy(button, labelKey, work) {
    const restore = button.textContent;
    button.disabled = true;
    button.textContent = I18n.t(labelKey);
    try {
      await work();
    } finally {
      button.disabled = false;
      button.textContent = restore;
    }
  }

  $("#balance-preview-btn").addEventListener("click", () => {
    const button = $("#balance-preview-btn");
    return withBusy(button, "js.refine.balancePreviewing", () =>
      Refine.runOp("balance", balanceBody({ dry_run: true }), (plan) => {
        renderPlan(plan);
        // Only a preview may arm the run: the cost has to have been shown once.
        $("#balance-run-btn").disabled = plan.rows_to_add === 0;
      }, null));
  });

  $("#balance-run-btn").addEventListener("click", () => {
    const button = $("#balance-run-btn");
    const target = $("#balance-name").value.trim();
    if (!target) { Refine.showError(I18n.t("js.refine.balanceNeedsName")); return undefined; }
    return withBusy(button, "js.refine.balanceRunning", () =>
      Refine.runOp("balance", balanceBody({ target }), async (res) => {
        const out = $("#balance-result");
        const done = document.createElement("p");
        done.textContent = I18n.t("js.refine.balanced", {
          count: res.rows_added, labels: res.labels_filled, target: res.target,
          calls: res.usage.calls, tokens: res.usage.tokens_total,
        });
        out.replaceChildren(done);
        out.hidden = false;
        invalidatePreview();          // the plan described the source, not the result
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
