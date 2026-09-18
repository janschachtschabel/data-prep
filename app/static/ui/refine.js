/* Refine tab: dataset store + non-destructive analysis + training preflight.
   Later refine operations (filter/combine/prep/label-audit/enrich) extend the
   operations panel here. textContent / table rendering only. */
"use strict";

const Refine = (() => {
  const $ = (sel) => document.querySelector(sel);

  function showError(message) {
    const box = $("#refine-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#refine-error").hidden = true;
  }

  function columnConfig() {
    const text = $("#refine-text-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
    return {
      text_columns: text.length ? text : undefined,
      label_column: $("#refine-label-col").value.trim() || undefined,
      label_filter: $("#refine-label-filter").value.trim() || undefined,
    };
  }

  // What each dataset actually contains, so the column fields can offer facts
  // instead of the WLO defaults every file is assumed to follow.
  const columnsByDataset = new Map();

  function offerColumns(datasetName) {
    const columns = columnsByDataset.get(datasetName) || [];
    // The label field takes one value, so the native datalist fits it exactly.
    $("#refine-col-options").replaceChildren(
      ...columns.map((col) => new Option(col, col)));
    // The text field takes a comma-separated list, which a datalist cannot serve:
    // each column becomes a button that appends itself to what is already there.
    const chips = $("#refine-col-chips");
    chips.replaceChildren();
    for (const col of columns) {
      const chip = document.createElement("button");
      chip.type = "button";            // inside a form: must not submit it
      chip.className = "ghost";
      chip.textContent = col;
      chip.addEventListener("click", () => appendTextColumn(col));
      chips.appendChild(chip);
    }
    RefineFields.render();
  }

  function appendTextColumn(column) {
    const field = $("#refine-text-cols");
    const present = field.value.split(",").map((part) => part.trim()).filter(Boolean);
    if (!present.includes(column)) present.push(column);
    field.value = present.join(", ");
    field.focus();                     // the caret lands where the change happened
    RefineFields.render();             // setting .value fires no change event
  }

  async function refreshDatasets(selectName) {
    const select = $("#refine-dataset");
    const previous = selectName || select.value;
    select.replaceChildren(new Option(I18n.t("js.refine.phDataset"), ""));
    const list = $("#refine-list");
    const combineSources = $("#combine-sources");
    list.replaceChildren();
    combineSources.replaceChildren();
    try {
      const res = await Api.get("/refine/datasets");
      $("#refine-empty").hidden = res.datasets.length > 0;
      for (const ds of res.datasets) {
        select.add(new Option(ds.name, ds.name));
        columnsByDataset.set(ds.name, ds.columns);
        list.appendChild(datasetRow(ds));
        combineSources.appendChild(combineCheckbox(ds));
      }
      if (previous && [...select.options].some((o) => o.value === previous)) {
        select.value = previous;
      }
      loadOps(select.value);
      offerColumns(select.value);
    } catch (err) {
      showError(err.message || I18n.t("js.err.datasets"));
    }
  }

  /* Operation history — shows the chain of filters/steps applied to a dataset,
     making it clear that operations can be stacked (each Apply → new dataset). */
  async function loadOps(name) {
    const box = $("#refine-ops");
    if (!name) { box.hidden = true; return; }
    try {
      const res = await Api.get(`/refine/${encodeURIComponent(name)}/ops`);
      if (!res.ops.length) {
        box.textContent = I18n.t("js.refine.noOps");
      } else {
        const steps = res.ops.map((o, i) =>
          `${i + 1}. ${o.op || o.filter}${o.source ? I18n.t("js.refine.fromSource", { source: o.source }) : ""}`);
        box.textContent = I18n.t("js.refine.appliedSteps") + steps.join("  →  ");
      }
      box.hidden = false;
    } catch {
      box.hidden = true;
    }
  }

  function combineCheckbox(ds) {
    const label = document.createElement("label");
    label.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = ds.name;
    box.className = "combine-source";
    label.append(box, document.createTextNode(I18n.t("js.refine.sourceCheckbox", { name: ds.name, rows: ds.rows })));
    return label;
  }

  function checkedSources() {
    return [...document.querySelectorAll(".combine-source:checked")].map((b) => b.value);
  }

  function datasetRow(ds) {
    const li = document.createElement("li");
    li.className = "list-row";
    const text = document.createElement("span");
    text.textContent = I18n.t("js.refine.row", { name: ds.name, rows: ds.rows, cols: ds.columns.length });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost";
    del.textContent = I18n.t("js.btn.delete");
    del.addEventListener("click", async () => {
      if (!window.confirm(I18n.t("js.refine.confirmDelete", { name: ds.name }))) return;
      clearError();
      try {
        await Api.del(`/refine/${encodeURIComponent(ds.name)}`);
        refreshDatasets();
      } catch (err) {
        showError(err.message || I18n.t("js.err.deleteFailed"));
      }
    });
    li.append(text, del);
    return li;
  }

  function renderTable(container, headers, rows) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    for (const h of headers) {
      const th = document.createElement("th");
      th.textContent = h;
      htr.appendChild(th);
    }
    thead.appendChild(htr);
    const tbody = document.createElement("tbody");
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const cell of row) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.append(thead, tbody);
    wrap.appendChild(table);
    container.replaceChildren(wrap);
  }

  /* `shown` names what a write would replace, for the overwrite question. */
  async function runOp(path, body, render, shown = body.target) {
    const name = $("#refine-dataset").value;
    if (!name) { showError(I18n.t("js.refine.chooseDataset")); return; }
    clearError();
    try {
      const res = await Api.postGuarded(`/refine/${encodeURIComponent(name)}/${path}`, body, shown);
      await render(res);  // an async render's failure belongs in the error box too
    } catch (err) {
      showError(err.message || I18n.t("js.refine.opFailed"));
    }
  }

  $("#refine-upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearError();
    const btn = $("#refine-upload-btn");
    btn.disabled = true;
    try {
      const form = new FormData();
      const file = $("#refine-file").files[0];
      form.append("file", file);
      const name = $("#refine-name").value.trim();
      if (name) form.append("name", name);
      await Api.postGuarded("/refine/datasets/import", form, name || Api.stem(file && file.name));
      $("#refine-upload-form").reset();
      await refreshDatasets();
    } catch (err) {
      showError(err.message || I18n.t("js.err.import"));
    } finally {
      btn.disabled = false;
    }
  });

  $("#refine-analyze-btn").addEventListener("click", () =>
    runOp("analyze", columnConfig(), (res) => {
      const out = $("#refine-result");
      const summary = document.createElement("p");
      summary.textContent = I18n.t("js.refine.analyze", {
        rows: res.total_rows, labels: res.unique_labels, dupes: res.exact_duplicate_rows,
        empty: res.empty_text_rows, nolabel: res.rows_without_label,
        lmin: res.text_length.min, lmax: res.text_length.max, lmean: res.text_length.mean,
        pii: res.pii.rows_affected, counts: JSON.stringify(res.pii.counts),
      });
      out.replaceChildren(summary);
      const top = Object.entries(res.label_support).slice(0, 20).map(([k, v]) => [k, v]);
      const table = document.createElement("div");
      renderTable(table, [I18n.t("js.refine.thLabel"), I18n.t("js.refine.thSupport")], top);
      out.appendChild(table);
      out.hidden = false;
    }));

  /* ---- filter params show/hide + build ---- */
  function syncFilterParams() {
    const selected = $("#refine-filter").value;
    document.querySelectorAll(".filter-param").forEach((el) => {
      el.hidden = el.dataset.for !== selected;
    });
  }
  $("#refine-filter").addEventListener("change", syncFilterParams);

  function filterParams() {
    const kind = $("#refine-filter").value;
    if (kind === "dedupe_semantic") return { threshold: Number($("#refine-threshold").value) || 0.95 };
    if (kind === "length") {
      return { min: Number($("#refine-len-min").value) || 0, max: Number($("#refine-len-max").value) || 1e9 };
    }
    if (kind === "label_filter") return { substring: $("#refine-substr").value.trim() };
    if (kind === "cap_per_label") return { cap: Number($("#refine-cap").value) || 1 };
    if (kind === "pii") return { mode: $("#refine-pii-mode").value };
    return {};
  }

  function renderFilterResult(res) {
    const out = $("#refine-result");
    const summary = document.createElement("p");
    const mode = res.preview ? I18n.t("js.refine.preview") : I18n.t("js.refine.applied", { target: res.target });
    summary.textContent = I18n.t("js.refine.filterResult", {
      mode, filter: res.filter, before: res.before, after: res.after,
      removed: res.removed, changed: res.changed,
    });
    out.replaceChildren(summary);
    if (!res.preview) {
      const hint = document.createElement("p");
      hint.className = "muted";
      hint.textContent = I18n.t("js.refine.chainHint", { target: res.target });
      out.appendChild(hint);
    }
    if (res.examples && res.examples.length) {
      const rows = res.examples.map((ex) =>
        ex.before !== undefined ? [ex.before, ex.after] : [ex.removed, I18n.t("js.refine.removedMark")]);
      const table = document.createElement("div");
      renderTable(table, [I18n.t("js.refine.thBefore"), I18n.t("js.refine.thAfter")], rows);
      out.appendChild(table);
    }
    out.hidden = false;
  }

  async function runFilter(withTarget) {
    const body = { filter: $("#refine-filter").value, params: filterParams(), ...columnConfig() };
    if (withTarget) {
      const target = $("#refine-target").value.trim();
      if (!target) { showError(I18n.t("js.refine.enterApplyName")); return; }
      body.target = target;
    }
    await runOp("filter", body, async (res) => {
      renderFilterResult(res);
      if (!res.preview) await refreshDatasets(res.target);  // auto-advance for chaining
    });
  }

  $("#refine-dataset").addEventListener("change", (ev) => {
    loadOps(ev.target.value);
    offerColumns(ev.target.value);
  });
  $("#refine-filter-preview").addEventListener("click", () => runFilter(false));
  $("#refine-filter-apply").addEventListener("click", () => runFilter(true));

  $("#refine-preflight-btn").addEventListener("click", () => {
    const body = columnConfig();
    const min = $("#refine-min-samples").value.trim();
    if (min) body.min_samples = Number(min);
    runOp("preflight", body, (res) => {
      const out = $("#refine-result");
      const summary = document.createElement("p");
      summary.textContent = I18n.t("js.refine.preflight", {
        eff: res.effective_rows, raw: res.raw_rows, labels: res.learnable_labels,
        min: res.min_samples, kept: res.kept_after_cleaning,
        short: res.dropped.too_short, nolabel: res.dropped.no_label, dup: res.dropped.duplicate,
      });
      out.replaceChildren(summary);
      const rows = Object.entries(res.per_label_effective).map(([k, v]) => [k, v]);
      const table = document.createElement("div");
      renderTable(table, [I18n.t("js.refine.thLabel"), I18n.t("js.refine.thEffective")], rows);
      out.appendChild(table);
      out.hidden = false;
    });
  });

  /* ---- combine: suggest mappings, then merge ---- */
  const TARGETS = [
    "properties.cclom:title", "properties.cclom:general_description",
    "properties.cclom:general_keyword", "properties.ccm:taxonid",
  ];

  $("#combine-suggest-btn").addEventListener("click", async () => {
    const sources = checkedSources();
    if (!sources.length) { showError(I18n.t("js.refine.selectSource")); return; }
    clearError();
    try {
      const suggestions = await Api.post("/refine/combine/suggest", { sources });
      renderMappings(sources, suggestions);
    } catch (err) {
      showError(err.message || I18n.t("js.refine.errSuggest"));
    }
  });

  function renderMappings(sources, suggestions) {
    const container = $("#combine-mappings");
    container.replaceChildren();
    for (const name of sources) {
      const info = suggestions[name];
      const block = document.createElement("div");
      block.className = "card";
      block.dataset.source = name;
      const head = document.createElement("strong");
      head.textContent = I18n.t("js.refine.mapHead", { name });
      block.appendChild(head);
      for (const target of TARGETS) {
        const label = document.createElement("label");
        label.textContent = target.split(":").pop();
        const sel = document.createElement("select");
        sel.className = "map-select";
        sel.dataset.target = target;
        sel.add(new Option(I18n.t("js.refine.mapNone"), ""));
        for (const col of info.columns) sel.add(new Option(col, col));
        sel.value = info.mapping[target] || "";
        label.appendChild(sel);
        block.appendChild(label);
      }
      container.appendChild(block);
    }
  }

  $("#combine-btn").addEventListener("click", async () => {
    const target = $("#combine-target").value.trim();
    if (!target) { showError(I18n.t("js.refine.enterCombineName")); return; }
    const blocks = [...document.querySelectorAll("#combine-mappings [data-source]")];
    if (!blocks.length) { showError(I18n.t("js.refine.suggestFirst")); return; }
    const sources = blocks.map((block) => {
      const mapping = {};
      block.querySelectorAll(".map-select").forEach((sel) => {
        mapping[sel.dataset.target] = sel.value || null;
      });
      return { name: block.dataset.source, label: block.dataset.source, mapping };
    });
    clearError();
    const btn = $("#combine-btn");
    btn.disabled = true;
    try {
      const res = await Api.postGuarded("/refine/combine", { sources, target }, target);
      const out = $("#refine-result");
      const p = document.createElement("p");
      p.textContent = I18n.t("js.refine.combined", {
        target: res.target, rowsIn: res.total_in, rowsOut: res.total_out,
        conflicts: res.conflicts_resolved,
      });
      out.replaceChildren(p);
      const table = document.createElement("div");
      renderTable(table,
        [I18n.t("js.refine.thSource"), I18n.t("js.refine.thRowsIn"), I18n.t("js.refine.thRowsKept")],
        res.per_source.map((s) => [s.label, s.rows_in, s.rows_kept]));
      out.appendChild(table);
      out.hidden = false;
      await refreshDatasets(res.target);  // continue from the merged result
    } catch (err) {
      showError(err.message || I18n.t("js.refine.errCombine"));
    } finally {
      btn.disabled = false;
    }
  });

  /* ---- prepare: holdout split + api_v3 push ---- */
  $("#prep-split-btn").addEventListener("click", () => {
    const target = $("#prep-target").value.trim();
    if (!target) { showError(I18n.t("js.refine.enterSplitName")); return; }
    const body = {
      ...columnConfig(),
      holdout_fraction: Number($("#prep-fraction").value) || 0.15,
      seed: Number($("#prep-seed").value) || 0,
      target,
    };
    runOp("split", body, async (res) => {
      const out = $("#refine-result");
      const p = document.createElement("p");
      p.textContent = I18n.t("js.refine.split", {
        target: res.target, train: res.train_rows, holdout: res.holdout_rows,
        pct: (res.holdout_fraction_actual * 100).toFixed(0),
        rec: res.balance.recommended_min_samples,
      });
      out.replaceChildren(p);
      // What the marks of a balance run kept out of the holdout — said, not implied.
      if (res.generated_excluded || res.real_kept_in_train) {
        const marks = document.createElement("p");
        marks.className = "muted";
        marks.textContent = I18n.t("js.refine.splitMarks", {
          generated: res.generated_excluded, kept: res.real_kept_in_train,
        });
        out.appendChild(marks);
      }
      const lost = res.labels_without_holdout || [];
      if (lost.length) {
        const line = document.createElement("p");
        line.className = "muted";
        line.textContent = I18n.t("js.refine.splitNoHoldout", {
          count: lost.length, labels: lost.slice(0, 5).join(", "),
        });
        out.appendChild(line);
      }
      const rows = Object.entries(res.balance.label_support).slice(0, 20).map(([k, v]) => [k, v]);
      const table = document.createElement("div");
      renderTable(table, [I18n.t("js.refine.thLabel"), I18n.t("js.refine.thTrainSupport")], rows);
      out.appendChild(table);
      out.hidden = false;
      await refreshDatasets(`${res.target}_train`);  // continue from the training split
    }, `${target}_train / ${target}_holdout`);
  });

  $("#prep-push-btn").addEventListener("click", async () => {
    const name = $("#refine-dataset").value;
    if (!name) { showError(I18n.t("js.refine.choosePush")); return; }
    if (!window.confirm(I18n.t("js.refine.confirmPush", { name }))) return;
    clearError();
    const btn = $("#prep-push-btn");
    btn.disabled = true;
    try {
      const res = await Api.post(`/refine/${encodeURIComponent(name)}/push`);
      const out = $("#refine-result");
      out.replaceChildren(Object.assign(document.createElement("p"), {
        textContent: I18n.t("js.refine.pushed",
          { pushed: res.pushed, rows: res.rows, api: JSON.stringify(res.api_v3) }),
      }));
      out.hidden = false;
    } catch (err) {
      showError(err.message || I18n.t("js.refine.errPush"));
    } finally {
      btn.disabled = false;
    }
  });

  /* ---- label audit (second opinion via api_v3) ---- */
  $("#audit-btn").addEventListener("click", () => {
    const model = $("#audit-model").value.trim();
    if (!model) { showError(I18n.t("js.refine.enterModel")); return; }
    const body = {
      ...columnConfig(),
      model_name: model,
      confidence_threshold: Number($("#audit-conf").value) || 0.8,
      top_k: Number($("#audit-topk").value) || 3,
    };
    runOp("label-audit", body, (res) => {
      const out = $("#refine-result");
      const p = document.createElement("p");
      p.textContent = I18n.t("js.refine.audit", { rows: res.rows_audited, flagged: res.flagged_count });
      out.replaceChildren(p);
      const rows = res.checklist.slice(0, 50).map((c) =>
        [c.gold.join(", "), c.top_prediction, c.top_confidence, c.model_top.join(", ")]);
      const table = document.createElement("div");
      renderTable(table,
        [I18n.t("js.refine.thGold"), I18n.t("js.refine.thModelTop"),
          I18n.t("js.refine.thConfidence"), I18n.t("js.refine.thRanking")], rows);
      out.appendChild(table);
      out.hidden = false;
    });
  });

  /* ---- enrich (additive LLM completion) ---- */
  $("#enrich-btn").addEventListener("click", async () => {
    const target = $("#enrich-target").value.trim();
    if (!target) { showError(I18n.t("js.refine.enterEnrichName")); return; }
    const fields = RefineFields.spec();
    const targetField = $("#enrich-field").value;
    if (!targetField) { showError(I18n.t("js.refine.enrichNeedsField")); return; }
    const body = {
      fields, target_field: targetField, target,
      label_column: $("#refine-label-col").value.trim() || undefined,
    };
    const btn = $("#enrich-btn");
    btn.disabled = true;
    btn.textContent = I18n.t("js.refine.enriching");
    try {
      await runOp("enrich", body, async (res) => {
        const out = $("#refine-result");
        out.replaceChildren(Object.assign(document.createElement("p"), {
          textContent: I18n.t("js.refine.enriched", {
            enriched: res.enriched, rows: res.rows, field: res.field, target: res.target,
            calls: res.usage.calls, tokens: res.usage.tokens_total,
          }),
        }));
        out.hidden = false;
        await refreshDatasets(res.target);  // continue from the enriched result
      });
    } finally {
      btn.disabled = false;
      btn.textContent = I18n.t("enrich.btn");  // restore the static label
    }
  });

  window.addEventListener("dataprep-app-ready", () => {
    syncFilterParams();
    refreshDatasets();
  });
  // Re-render the dataset list (row text + buttons) on language change.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshDatasets();
  });
  // Refresh datasets when this tab is shown (datasets created elsewhere appear).
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "refine") refreshDatasets();
  });

  // Shared with refine-fields.js: the dataset picker, the overwrite-guarded POST and
  // the error box belong to this panel, and a second copy of them would drift.
  return { runOp, refreshDatasets, renderTable, showError, clearError };
})();
