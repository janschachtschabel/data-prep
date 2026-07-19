/* Vocabularies tab: fetch-by-URL, file upload, list with detail tree + delete.
   All rendering uses textContent (labels are external data — never innerHTML). */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const errorBox = () => $("#vocab-error");

  function showError(message) {
    errorBox().textContent = message;
    errorBox().hidden = false;
  }
  function clearError() {
    errorBox().hidden = true;
  }

  async function refreshList() {
    const list = $("#vocab-list");
    const empty = $("#vocab-empty");
    try {
      const res = await Api.get("/vocabs");
      list.replaceChildren();
      empty.hidden = res.vocabularies.length > 0;
      for (const vocab of res.vocabularies) {
        list.appendChild(renderItem(vocab));
      }
    } catch (err) {
      showError(err.message || I18n.t("js.err.vocabs"));
    }
  }

  function renderItem(vocab) {
    const li = document.createElement("li");
    li.className = "list-row";

    const text = document.createElement("span");
    text.textContent =
      I18n.t("js.vocab.row", { name: vocab.name, title: vocab.title, count: vocab.concept_count }) +
      (vocab.label_field ? I18n.t("js.vocab.field", { field: vocab.label_field }) : "");

    const viewBtn = document.createElement("button");
    viewBtn.type = "button";
    viewBtn.className = "ghost";
    viewBtn.textContent = I18n.t("js.btn.view");
    viewBtn.addEventListener("click", () => showDetail(vocab.name));

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "ghost";
    delBtn.textContent = I18n.t("js.btn.delete");
    delBtn.addEventListener("click", async () => {
      if (!window.confirm(I18n.t("js.vocab.confirmDelete", { name: vocab.name }))) return;
      clearError();
      try {
        await Api.del(`/vocabs/${encodeURIComponent(vocab.name)}`);
        $("#vocab-detail").hidden = true;
        refreshList();
      } catch (err) {
        showError(err.message || I18n.t("js.err.deleteFailed"));
      }
    });

    li.append(text, viewBtn, delBtn);
    return li;
  }

  async function showDetail(name) {
    clearError();
    try {
      const detail = await Api.get(`/vocabs/${encodeURIComponent(name)}`);
      $("#vocab-detail-title").textContent = I18n.t("js.vocab.detailTitle",
        { title: detail.title, count: detail.concept_count, langs: detail.languages.join(", ") });
      const tree = $("#vocab-tree");
      tree.replaceChildren();
      for (const node of detail.tree) {
        const li = document.createElement("li");
        li.style.paddingLeft = `${node.depth * 16}px`;
        li.textContent = node.label;
        li.title = node.uri;
        tree.appendChild(li);
      }
      $("#vocab-detail").hidden = false;
    } catch (err) {
      showError(err.message || I18n.t("js.err.details"));
    }
  }

  async function busy(button, action) {
    clearError();
    button.disabled = true;
    try {
      await action();
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.err.request"));
    } finally {
      button.disabled = false;
    }
  }

  $("#vocab-fetch-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy($("#vocab-fetch-btn"), async () => {
      const name = $("#vocab-fetch-name").value.trim();
      const field = $("#vocab-field").value.trim();
      await Api.post("/vocabs/fetch", {
        url: $("#vocab-url").value.trim(),
        ...(name ? { name } : {}),
        ...(field ? { label_field: field } : {}),
      });
      $("#vocab-fetch-form").reset();
    });
  });

  $("#vocab-upload-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy($("#vocab-upload-btn"), async () => {
      const form = new FormData();
      form.append("file", $("#vocab-file").files[0]);
      const name = $("#vocab-upload-name").value.trim();
      if (name) form.append("name", name);
      await Api.postForm("/vocabs/import", form);
      $("#vocab-upload-form").reset();
    });
  });

  $("#vocab-manual-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy($("#vocab-manual-btn"), async () => {
      const field = $("#vocab-manual-field").value.trim();
      await Api.post("/vocabs/manual", {
        name: $("#vocab-manual-name").value.trim(),
        text: $("#vocab-manual-text").value,
        lang: $("#vocab-manual-lang").value,
        ...(field ? { label_field: field } : {}),
      });
      $("#vocab-manual-form").reset();
    });
  });

  $("#vocab-reload").addEventListener("click", () => {
    clearError();
    refreshList();
  });

  // Default-vocabulary presets: fill the fetch form (one click, then Fetch).
  document.querySelectorAll("#vocab-presets button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#vocab-url").value = btn.dataset.url;
      $("#vocab-fetch-name").value = btn.dataset.name;
      $("#vocab-field").value = btn.dataset.field || "";
      $("#vocab-fetch-btn").focus();
    });
  });

  window.addEventListener("dataprep-app-ready", refreshList);
  // Re-render the list (row text + buttons) when the language switches.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshList();
  });
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "vocabularies") refreshList();
  });
})();
