/* Runs tab: cockpit (dry run + start), run list with live progress polling,
   cancel/resume. textContent rendering only. */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  let pollTimer = null;

  function showError(message) {
    const box = $("#run-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#run-error").hidden = true;
  }

  function requestBody() {
    const mode = document.querySelector('input[name="run-mode"]:checked').value;
    const selection = { mode };
    if (mode === "subtree") selection.root = $("#run-root").value.trim();
    if (mode === "list") {
      selection.uris = $("#run-uris").value.split("\n").map((s) => s.trim()).filter(Boolean);
    }
    const profileName = $("#run-profile").value;
    const profile = profileName === "custom"
      ? { min: Number($("#run-min").value) || 100, max: Number($("#run-max").value) || 600 }
      : { name: profileName };
    return {
      seed_set: $("#run-seed-set").value,
      selection,
      per_concept: Number($("#run-per-concept").value) || 50,
      batch_size: Number($("#run-batch").value) || 8,
      length_profile: profile,
      guidance: $("#run-guidance").value.trim(),
      concurrency: Number($("#run-concurrency").value) || 20,
    };
  }

  async function refreshSeedSets() {
    const select = $("#run-seed-set");
    const previous = select.value;
    select.replaceChildren(new Option(I18n.t("js.runs.phSeedset"), ""));
    try {
      const res = await Api.get("/seeds");
      for (const set of res.seed_sets) select.add(new Option(set.name, set.name));
      if (previous) select.value = previous;  // keep the choice across refreshes
    } catch (err) {
      showError(err.message || I18n.t("js.err.seeds"));
    }
  }

  async function refreshRuns() {
    const list = $("#run-list");
    const empty = $("#run-empty");
    const note = $("#run-list-note");
    try {
      const res = await Api.get("/runs?limit=10");  // keep the list tidy — 10 newest
      list.replaceChildren();
      empty.hidden = res.runs.length > 0;
      let anyRunning = false;
      for (const run of res.runs) {
        if (run.status === "running") anyRunning = true;
        list.appendChild(renderRun(run));
      }
      if (res.total > res.runs.length) {
        note.textContent = I18n.t("js.runs.showingLast", { shown: res.runs.length, total: res.total });
        note.hidden = false;
      } else {
        note.hidden = true;
      }
      schedulePoll(anyRunning);
    } catch (err) {
      showError(err.message || I18n.t("js.err.runs"));
    }
  }

  function schedulePoll(active) {
    if (pollTimer) clearTimeout(pollTimer);
    if (active) pollTimer = setTimeout(refreshRuns, 2000);
  }

  function renderRun(run) {
    const li = document.createElement("li");
    li.className = "list-row";
    const text = document.createElement("span");
    text.textContent =
      I18n.t("js.runs.row",
        { id: run.id, status: I18n.st(run.status), gen: run.generated, target: run.target }) +
      (run.message ? I18n.t("js.runs.rowMsg", { message: run.message }) : "");
    li.appendChild(text);

    if (run.status === "running") {
      li.appendChild(actionButton(I18n.t("js.btn.cancel"), async () => {
        await Api.post(`/runs/${run.id}/cancel`);
        $("#run-detail").textContent = I18n.t("js.runs.cancelling");
        $("#run-detail").hidden = false;
      }));
    }
    if (["paused", "cancelled", "failed", "interrupted"].includes(run.status)) {
      li.appendChild(actionButton(I18n.t("js.btn.resume"), () => Api.post(`/runs/${run.id}/resume`)));
    }
    if (run.status !== "running" && run.generated > 0) {
      li.appendChild(actionButton("CSV", () =>
        Api.download(`/runs/${run.id}/export.csv`, `${run.id}.csv`)));
      // Its own name: two files called run-1.csv in a downloads folder cannot be
      // told apart, and the defused one must never go to api_v3 (help text above).
      li.appendChild(actionButton(I18n.t("js.btn.csvSpreadsheet"), () =>
        Api.download(`/runs/${run.id}/export.csv?spreadsheet_safe=true`,
                     `${run.id}.spreadsheet.csv`)));
      li.appendChild(actionButton("JSONL", () =>
        Api.download(`/runs/${run.id}/export.jsonl`, `${run.id}.jsonl`)));
      li.appendChild(actionButton(I18n.t("js.btn.audit"), async () => {
        // Go through Api.get so a non-200 (401/404/5xx) is surfaced as an error
        // instead of dumping the raw error body into the detail pane. audit.md is
        // text/markdown, so Api returns the raw Response to read as text.
        const res = await Api.get(`/runs/${run.id}/audit.md`);
        $("#run-detail").textContent = await res.text();
        $("#run-detail").hidden = false;
      }));
      li.appendChild(actionButton(I18n.t("js.runs.pushBtn"), async () => {
        if (!window.confirm(I18n.t("js.runs.confirmPush", { id: run.id }))) return;
        const res = await Api.post(`/runs/${run.id}/push`);
        $("#run-detail").textContent = I18n.t("js.runs.pushed",
          { pushed: res.pushed, rows: res.rows, api: JSON.stringify(res.api_v3) });
        $("#run-detail").hidden = false;
      }));
    }
    li.appendChild(actionButton(I18n.t("js.btn.details"), async () => {
      const state = await Api.get(`/runs/${run.id}`);
      const c = state.counters;
      $("#run-detail").textContent = I18n.t("js.runs.details", {
        id: state.id, status: I18n.st(state.status), gen: c.generated,
        dl: c.discarded_length, dup: c.discarded_duplicate,
        calls: state.usage.calls, tokens: state.usage.tokens_total,
        failed: c.failed_batches || 0,
      }) + (state.message ? I18n.t("js.runs.rowMsg", { message: state.message }) : "");
      $("#run-detail").hidden = false;
    }));
    return li;
  }

  function actionButton(label, action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost";
    btn.textContent = label;
    btn.addEventListener("click", async () => {
      clearError();
      btn.disabled = true;
      try {
        await action();
        await refreshRuns();
      } catch (err) {
        showError(err.message || I18n.t("js.err.actionFailed", { label }));
      } finally {
        btn.disabled = false;
      }
    });
    return btn;
  }

  $("#run-profile").addEventListener("change", (ev) => {
    $("#run-custom-range").hidden = ev.target.value !== "custom";
  });

  $("#run-dry-btn").addEventListener("click", async () => {
    clearError();
    const btn = $("#run-dry-btn");
    btn.disabled = true;
    try {
      const res = await Api.post("/runs/plan", requestBody());
      const est = res.estimate;
      $("#run-estimate").textContent = I18n.t("js.runs.estimate", {
        concepts: res.plan.length, target: est.total_target,
        calls: est.estimated_calls, tokens: est.estimated_tokens.toLocaleString(),
        bcalls: res.budgets.max_llm_calls, btokens: res.budgets.max_tokens_total.toLocaleString(),
        low: res.corridor[0], high: res.corridor[1],
      });
      $("#run-estimate").hidden = false;
    } catch (err) {
      showError(err.message || I18n.t("js.runs.errDry"));
    } finally {
      btn.disabled = false;
    }
  });

  $("#run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearError();
    const btn = $("#run-start-btn");
    btn.disabled = true;
    try {
      await Api.post("/runs", requestBody());
      await refreshRuns();
    } catch (err) {
      showError(err.message || I18n.t("js.runs.errStart"));
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener("dataprep-app-ready", () => {
    refreshSeedSets();
    refreshRuns();
  });
  // Re-render the run list (row text + buttons) on language change.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!document.querySelector("#view-app").hidden) refreshRuns();
  });
  // Refresh when this tab is shown: pick up seed sets built elsewhere + new runs.
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "runs") { refreshSeedSets(); refreshRuns(); }
  });
})();
