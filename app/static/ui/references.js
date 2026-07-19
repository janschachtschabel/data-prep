/* References tab: CSV upload (input PII scrub happens server-side), list with
   PII summary + per-concept group count, delete. textContent rendering only. */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);

  function showError(message) {
    const box = $("#ref-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#ref-error").hidden = true;
  }

  async function refreshList() {
    const list = $("#ref-list");
    const empty = $("#ref-empty");
    try {
      const res = await Api.get("/references");
      list.replaceChildren();
      empty.hidden = res.references.length > 0;
      for (const ref of res.references) {
        list.appendChild(renderItem(ref));
      }
    } catch (err) {
      showError(err.message || I18n.t("js.err.refs"));
    }
  }

  function renderItem(ref) {
    const li = document.createElement("li");
    li.className = "list-row";

    const text = document.createElement("span");
    const leaf = (col) => (col || "").split(":").pop();
    text.textContent = I18n.t("js.ref.row", {
      name: ref.name, rows: ref.row_count, groups: ref.group_count, pii: ref.pii_rows_affected,
    }) + I18n.t("js.ref.fields", {
      cols: (ref.text_columns || []).map(leaf).join(", "), label: leaf(ref.label_column),
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "ghost";
    delBtn.textContent = I18n.t("js.btn.delete");
    delBtn.addEventListener("click", async () => {
      if (!window.confirm(I18n.t("js.ref.confirmDelete", { name: ref.name }))) return;
      clearError();
      try {
        await Api.del(`/references/${encodeURIComponent(ref.name)}`);
        refreshList();
      } catch (err) {
        showError(err.message || I18n.t("js.err.deleteFailed"));
      }
    });

    li.append(text, delBtn);
    return li;
  }

  $("#ref-upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearError();
    const btn = $("#ref-upload-btn");
    btn.disabled = true;
    try {
      const form = new FormData();
      form.append("file", $("#ref-file").files[0]);
      const name = $("#ref-name").value.trim();
      if (name) form.append("name", name);
      const cols = $("#ref-text-columns").value.trim();
      if (cols) form.append("text_columns", cols);
      const label = $("#ref-label-column").value.trim();
      if (label) form.append("label_column", label);
      const meta = await Api.postForm("/references/import", form);
      $("#ref-upload-form").reset();
      const pii = meta.pii || { rows_affected: 0 };
      $("#ref-result").textContent = I18n.t("js.ref.imported", {
        name: meta.name, rows: meta.row_count,
        groups: Object.keys(meta.group_counts).length, pii: pii.rows_affected,
      });
      $("#ref-result").hidden = false;
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.err.import"));
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener("dataprep-app-ready", refreshList);
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshList();
  });
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "references") refreshList();
  });
})();
