/* Seeds tab: build seed sets (distilled or empty), inspect/edit per concept,
   LLM-bootstrap single concepts. textContent rendering only. */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  let currentSet = null; // full payload of the opened seed set
  let currentUri = null;
  let draftSeeds = [];   // editor working copy for the selected concept

  function showError(message) {
    const box = $("#seed-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#seed-error").hidden = true;
  }

  async function fillSelect(select, path, listKey, placeholder) {
    const previous = select.value;
    select.replaceChildren(new Option(placeholder, ""));
    try {
      const res = await Api.get(path);
      for (const item of res[listKey]) select.add(new Option(item.name, item.name));
      if (previous) select.value = previous;  // keep the choice across refreshes
    } catch (err) {
      showError(err.message || I18n.t("js.err.loadList"));
    }
  }

  async function refreshInputs() {
    await fillSelect($("#seed-vocab"), "/vocabs", "vocabularies", I18n.t("js.seeds.phVocab"));
    await fillSelect($("#seed-reference"), "/references", "references", I18n.t("js.seeds.phRef"));
  }

  async function refreshList() {
    const list = $("#seed-list");
    const empty = $("#seed-empty");
    try {
      const res = await Api.get("/seeds");
      list.replaceChildren();
      empty.hidden = res.seed_sets.length > 0;
      for (const set of res.seed_sets) {
        const li = document.createElement("li");
        li.className = "list-row";
        const text = document.createElement("span");
        text.textContent = I18n.t("js.seeds.row", {
          name: set.name, vocab: set.vocab, seeded: set.concepts_with_seeds,
          total: set.concept_count, seeds: set.seed_count, withTerms: set.concepts_with_terms,
        });
        const openBtn = document.createElement("button");
        openBtn.type = "button";
        openBtn.className = "ghost";
        openBtn.textContent = I18n.t("js.btn.open");
        openBtn.addEventListener("click", () => openSet(set.name));
        const delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.className = "ghost";
        delBtn.textContent = I18n.t("js.btn.delete");
        delBtn.addEventListener("click", async () => {
          if (!window.confirm(I18n.t("js.seeds.confirmDelete", { name: set.name }))) return;
          clearError();
          try {
            await Api.del(`/seeds/${encodeURIComponent(set.name)}`);
            $("#seed-editor").hidden = true;
            refreshList();
          } catch (err) {
            showError(err.message || I18n.t("js.err.deleteFailed"));
          }
        });
        li.append(text, openBtn, delBtn);
        list.appendChild(li);
      }
    } catch (err) {
      showError(err.message || I18n.t("js.err.seeds"));
    }
  }

  async function openSet(name) {
    clearError();
    try {
      currentSet = await Api.get(`/seeds/${encodeURIComponent(name)}`);
    } catch (err) {
      showError(err.message || I18n.t("js.seeds.errOpen"));
      return;
    }
    $("#seed-editor-title").textContent =
      I18n.t("js.seeds.editTitle", { name: currentSet.name, vocab: currentSet.vocab });
    const select = $("#seed-concept");
    select.replaceChildren();
    for (const [uri, entry] of Object.entries(currentSet.concepts)) {
      const label = uri.split("/").pop();
      select.add(new Option(I18n.t("js.seeds.conceptOption", { label, n: entry.seeds.length }), uri));
    }
    $("#seed-editor").hidden = false;
    selectConcept(select.value);
  }

  function selectConcept(uri) {
    currentUri = uri;
    draftSeeds = (currentSet.concepts[uri]?.seeds || []).map((s) => ({ ...s }));
    renderSeedTable();
    renderTerms();
  }

  function renderTerms() {
    const terms = currentSet.concepts[currentUri]?.terms || [];
    $("#seed-terms").textContent = terms.length ? terms.join(", ") : I18n.t("js.seeds.noTerms");
  }

  function renderSeedTable() {
    const tbody = $("#seed-rows");
    tbody.replaceChildren();
    $("#seed-none").hidden = draftSeeds.length > 0;
    draftSeeds.forEach((seed, index) => {
      const tr = document.createElement("tr");
      for (const key of ["title", "description", "keywords"]) {
        const td = document.createElement("td");
        td.textContent = seed[key] || "";
        tr.appendChild(td);
      }
      const src = document.createElement("td");
      src.textContent = seed.source || "";
      tr.appendChild(src);
      const actions = document.createElement("td");
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "ghost";
      rm.textContent = I18n.t("js.btn.remove");
      rm.addEventListener("click", () => {
        draftSeeds.splice(index, 1);
        renderSeedTable();
      });
      actions.appendChild(rm);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
  }

  /* ---- forms ---- */

  $("#seed-build-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearError();
    const btn = $("#seed-build-btn");
    btn.disabled = true;
    try {
      const reference = $("#seed-reference").value;
      const kwCols = $("#seed-keyword-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
      const termCols = $("#seed-term-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
      const name = $("#seed-name").value.trim();
      // A rebuild discards the set's hand-edited and LLM-generated seeds.
      await Api.postGuarded("/seeds/build", {
        name,
        vocab: $("#seed-vocab").value,
        ...(reference ? { reference } : {}),
        per_concept: Number($("#seed-per-concept").value) || 6,
        ...(kwCols.length ? { keyword_columns: kwCols } : {}),
        ...(termCols.length ? { term_columns: termCols } : {}),
        terms_max_rows: Number($("#seed-terms-rows").value) || 0,
        terms_top_n: Number($("#seed-terms-topn").value) || 60,
      }, name);
      $("#seed-build-form").reset();
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.seeds.errBuild"));
    } finally {
      btn.disabled = false;
    }
  });

  // Pre-fill the term-bank column fields with the chosen reference's actual
  // columns (editable). The convention is text_columns = [title, description,
  // keyword]; empty reference (vocab-only) clears them.
  async function syncColumnDefaults() {
    const name = $("#seed-reference").value;
    const kw = $("#seed-keyword-cols");
    const tx = $("#seed-term-cols");
    if (!name) { kw.value = ""; tx.value = ""; return; }
    try {
      const ref = await Api.get(`/references/${encodeURIComponent(name)}`);
      const cols = ref.text_columns || [];
      kw.value = cols[2] || "";
      tx.value = cols.slice(0, 2).join(", ");
    } catch { /* keep the static placeholder hints */ }
  }
  $("#seed-reference").addEventListener("change", syncColumnDefaults);

  $("#seed-concept").addEventListener("change", (ev) => selectConcept(ev.target.value));

  $("#seed-add-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const title = $("#seed-add-title").value.trim();
    if (!title) return;
    draftSeeds.push({
      title,
      description: $("#seed-add-description").value.trim(),
      keywords: $("#seed-add-keywords").value.trim(),
      source: "manual",
    });
    $("#seed-add-form").reset();
    renderSeedTable();
  });

  $("#seed-save-btn").addEventListener("click", async () => {
    clearError();
    const btn = $("#seed-save-btn");
    btn.disabled = true;
    try {
      await Api.put(`/seeds/${encodeURIComponent(currentSet.name)}/concepts`, {
        concept_uri: currentUri,
        seeds: draftSeeds.map(({ title, description, keywords }) => ({ title, description, keywords })),
      });
      await openSet(currentSet.name);
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.seeds.errSave"));
    } finally {
      btn.disabled = false;
    }
  });

  $("#seed-bootstrap-btn").addEventListener("click", async () => {
    clearError();
    const btn = $("#seed-bootstrap-btn");
    btn.disabled = true;
    btn.textContent = I18n.t("js.seeds.generating");
    try {
      const res = await Api.post(
        `/seeds/${encodeURIComponent(currentSet.name)}/bootstrap`,
        { concept_uri: currentUri, n: 4 },
      );
      $("#seed-usage").textContent = I18n.t("js.seeds.added",
        { n: res.added, calls: res.usage.calls, tokens: res.usage.tokens_total });
      await openSet(currentSet.name);
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.seeds.errBootstrap"));
    } finally {
      btn.disabled = false;
      btn.textContent = I18n.t("seed.bootstrap.btn");  // restore the static label
    }
  });

  $("#seed-terms-btn").addEventListener("click", async () => {
    clearError();
    const btn = $("#seed-terms-btn");
    btn.disabled = true;
    btn.textContent = I18n.t("js.seeds.refining");
    try {
      const res = await Api.post(`/seeds/${encodeURIComponent(currentSet.name)}/terms`,
        { concept_uri: currentUri, context: $("#seed-terms-context").value.trim() });
      currentSet.concepts[currentUri].terms = res.terms;
      renderTerms();
      $("#seed-usage").textContent = I18n.t("js.seeds.termsRefined",
        { n: res.terms.length, calls: res.usage.calls, tokens: res.usage.tokens_total });
      await refreshList();
    } catch (err) {
      showError(err.message || I18n.t("js.seeds.errTerms"));
    } finally {
      btn.disabled = false;
      btn.textContent = I18n.t("seed.terms.btn");  // restore the static label
    }
  });

  window.addEventListener("dataprep-app-ready", () => {
    refreshInputs();
    refreshList();
  });
  // Re-render the seed-set list (row text + buttons) on language change.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshList();
  });
  // Refresh when this tab is shown: pick up vocabularies / references added elsewhere.
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "seeds") { refreshInputs(); refreshList(); }
  });
})();
