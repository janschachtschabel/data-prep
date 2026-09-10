/* Tables tab, part 1: looking at a table — import, browse, profile, export.

   Changing a table (rules, columns, duplicates, join) lives in tables-ops.js.
   The split is by what the user is doing, not by line count: reading a dataset
   and rewriting it are different jobs with different failure modes.

   All cell content comes from an imported file, so it is rendered with
   textContent throughout — never innerHTML. Only i18n.js, whose values are
   developer-authored constants, may use innerHTML. */
"use strict";

const Tables = (() => {
  const $ = (sel) => document.querySelector(sel);
  const PAGE = 50;

  let offset = 0;
  let matched = 0;
  // The last dataset list fetched, so describe() never refetches what
  // refreshDatasets() just loaded.
  let datasets = [];

  function showError(message) {
    const box = $("#tables-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#tables-error").hidden = true;
  }

  function current() {
    return $("#tables-dataset").value;
  }

  function columnList() {
    return $("#tables-columns").value.split(",").map((s) => s.trim()).filter(Boolean);
  }

  /* Busy state, not just disabled: a disabled button with no other signal is
     indistinguishable from one that is simply unavailable. */
  function busy(button, on, labelKey) {
    button.disabled = on;
    button.textContent = on ? I18n.t("js.tables.working") : I18n.t(labelKey);
  }

  // The profile reports null for a NaN, which toLocaleString would throw on.
  function number(value) {
    return value == null ? "" : value.toLocaleString(I18n.current());
  }

  function renderTable(table, headers, rows) {
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const name of headers) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = name;
      headRow.appendChild(th);
    }
    head.appendChild(headRow);

    const body = document.createElement("tbody");
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const cell of row) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    table.replaceChildren(head, body);
  }

  async function refreshDatasets(selectName) {
    const select = $("#tables-dataset");
    const previous = selectName || select.value;
    select.replaceChildren(new Option(I18n.t("js.tables.phDataset"), ""));
    try {
      const res = await Api.get("/refine/datasets");
      datasets = res.datasets;
      $("#tables-empty").hidden = datasets.length > 0;
      for (const ds of datasets) select.add(new Option(ds.name, ds.name));
      if (previous && [...select.options].some((o) => o.value === previous)) {
        select.value = previous;
      }
      await describe();
      if (typeof TablesOps !== "undefined") TablesOps.datasetsChanged(datasets);
    } catch (err) {
      showError(err.message || I18n.t("js.tables.errDatasets"));
    }
  }

  /* Shape and provenance together: the row/column count says what this IS, the
     operation chain says how it got that way. */
  async function describe() {
    const name = current();
    const shape = $("#tables-shape");
    const chain = $("#tables-chain");
    shape.textContent = "";
    chain.hidden = true;
    if (!name) return;
    try {
      const ds = datasets.find((d) => d.name === name);
      if (ds) {
        shape.textContent = I18n.t("js.tables.shape")
          .replace("{rows}", ds.rows.toLocaleString(I18n.current()))
          .replace("{cols}", ds.columns.length);
      }
      const ops = await Api.get(`/refine/${encodeURIComponent(name)}/ops`);
      if (ops.ops && ops.ops.length) {
        chain.textContent = I18n.t("js.tables.chain") + " " +
          ops.ops.map((o) => o.filter).join(" → ");
        chain.hidden = false;
      }
    } catch (err) {
      showError(err.message || I18n.t("js.tables.errDatasets"));
    }
  }

  async function importDataset(event) {
    event.preventDefault();
    clearError();
    const file = $("#tables-file").files[0];
    if (!file) return;
    const button = $("#tables-import-btn");
    const form = new FormData();
    form.append("file", file);
    form.append("format", $("#tables-format").value);
    form.append("separator", $("#tables-separator").value || ";");
    form.append("encoding", $("#tables-encoding").value || "utf-8");
    const name = $("#tables-name").value.trim();
    if (name) form.append("name", name);

    busy(button, true, "tables.import.btn");
    try {
      const res = await Api.postForm("/refine/datasets/import", form);
      await refreshDatasets(res.name);
      // A wide import is the moment to say so: a WLO export flattens to well
      // over a hundred columns, and the next step is usually dropping most.
      $("#tables-view-status").textContent = I18n.t("js.tables.imported")
        .replace("{rows}", res.rows.toLocaleString(I18n.current()))
        .replace("{cols}", res.columns.length);
      offset = 0;
      await loadRows();
    } catch (err) {
      showError(err.message || I18n.t("js.tables.errImport"));
    } finally {
      busy(button, false, "tables.import.btn");
    }
  }

  async function loadRows() {
    const name = current();
    if (!name) return;
    const status = $("#tables-view-status");
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE) });
    const q = $("#tables-search").value.trim();
    if (q) params.set("q", q);
    for (const column of columnList()) params.append("columns", column);

    status.textContent = I18n.t("js.tables.loading");
    try {
      const res = await Api.get(`/refine/${encodeURIComponent(name)}/rows?${params}`);
      matched = res.matched;
      renderTable(
        $("#tables-rows"),
        res.columns,
        res.rows.map((row) => res.columns.map((c) => row[c] ?? "")),
      );
      if (res.matched === 0) {
        status.textContent = q ? I18n.t("js.tables.noMatch") : I18n.t("js.tables.noRows");
      } else {
        const last = Math.min(offset + PAGE, res.matched);
        status.textContent = I18n.t("js.tables.showing")
          .replace("{from}", (offset + 1).toLocaleString(I18n.current()))
          .replace("{to}", last.toLocaleString(I18n.current()))
          .replace("{matched}", res.matched.toLocaleString(I18n.current()))
          .replace("{total}", res.total.toLocaleString(I18n.current()));
      }
      $("#tables-prev").disabled = offset === 0;
      $("#tables-next").disabled = offset + PAGE >= res.matched;
    } catch (err) {
      status.textContent = "";
      showError(err.message || I18n.t("js.tables.errRows"));
    }
  }

  async function loadProfile() {
    const name = current();
    if (!name) return;
    const button = $("#tables-profile-btn");
    const status = $("#tables-profile-status");
    busy(button, true, "tables.profile.btn");
    status.textContent = I18n.t("js.tables.loading");
    try {
      const res = await Api.get(`/refine/${encodeURIComponent(name)}/profile`);
      renderTable(
        $("#tables-profile"),
        [
          I18n.t("js.tables.col.name"), I18n.t("js.tables.col.filled"),
          I18n.t("js.tables.col.distinct"), I18n.t("js.tables.col.kind"),
          I18n.t("js.tables.col.top"), I18n.t("js.tables.col.range"),
        ],
        res.columns.map((c) => [
          c.name,
          `${c.filled.toLocaleString(I18n.current())} (${Math.round(c.fill_rate * 100)}%)`,
          c.distinct.toLocaleString(I18n.current()),
          I18n.t(`js.tables.kind.${c.kind}`),
          c.top_values.slice(0, 3).map((t) => `${t.value} (${t.count})`).join(", "),
          c.numeric ? `${number(c.numeric.min)} … ${number(c.numeric.max)}` : "",
        ]),
      );
      status.textContent = I18n.t("js.tables.profiled")
        .replace("{cols}", res.columns.length)
        .replace("{rows}", res.rows.toLocaleString(I18n.current()));
    } catch (err) {
      status.textContent = "";
      showError(err.message || I18n.t("js.tables.errProfile"));
    } finally {
      busy(button, false, "tables.profile.btn");
    }
  }

  function download() {
    const name = current();
    if (!name) return;
    const params = new URLSearchParams({
      format: $("#tables-export-format").value,
      separator: $("#tables-export-separator").value || ";",
    });
    // Through Api so the key travels: a plain link would hit a 401.
    Api.download(`/refine/${encodeURIComponent(name)}/download?${params}`,
                 `${name}.${$("#tables-export-format").value}`)
       .catch((err) => showError(err.message || I18n.t("js.tables.errDownload")));
  }

  function init() {
    $("#tables-import-form").addEventListener("submit", importDataset);
    $("#tables-dataset").addEventListener("change", () => {
      offset = 0;
      clearError();
      describe();
      $("#tables-rows").replaceChildren();
      $("#tables-profile").replaceChildren();
      $("#tables-view-status").textContent = "";
      $("#tables-profile-status").textContent = "";
      if (typeof TablesOps !== "undefined") TablesOps.selectionChanged(current());
    });
    $("#tables-view-btn").addEventListener("click", () => { offset = 0; loadRows(); });
    $("#tables-search").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); offset = 0; loadRows(); }
    });
    $("#tables-prev").addEventListener("click", () => {
      offset = Math.max(0, offset - PAGE);
      loadRows();
    });
    $("#tables-next").addEventListener("click", () => {
      if (offset + PAGE < matched) { offset += PAGE; loadRows(); }
    });
    $("#tables-profile-btn").addEventListener("click", loadProfile);
    $("#tables-download-btn").addEventListener("click", download);
  }

  init();
  window.addEventListener("dataprep-app-ready", () => refreshDatasets());
  // Datasets created in the Refine tab must appear here without a reload.
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "tables") refreshDatasets();
  });
  // Numbers and the placeholder option are localised, so both are re-rendered.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshDatasets();
  });

  return { refreshDatasets, current, showError, clearError, renderTable, busy };
})();
