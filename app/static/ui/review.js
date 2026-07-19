/* Review tab: browse a run's samples, approve/discard (mouse or keyboard a/d),
   regenerate discarded via resume. textContent rendering only. */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);

  function showError(message) {
    const box = $("#review-error");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() {
    $("#review-error").hidden = true;
  }
  function announce(message) {
    $("#review-status").textContent = message;
  }
  // Compose the status badge (rebuilt on status change so it stays translated).
  function badgeText(status, concept, sim) {
    return I18n.t("js.review.badge", { status: I18n.st(status), concept, sim });
  }

  async function refreshRuns() {
    const select = $("#review-run");
    const previous = select.value;
    select.replaceChildren(new Option(I18n.t("js.review.phRun"), ""));
    try {
      const res = await Api.get("/runs");
      for (const run of res.runs) {
        select.add(new Option(
          I18n.t("js.review.runOption", { id: run.id, status: I18n.st(run.status), gen: run.generated }),
          run.id));
      }
      if (previous) select.value = previous;
    } catch (err) {
      showError(err.message || I18n.t("js.err.runs"));
    }
  }

  function query() {
    const params = new URLSearchParams();
    const concept = $("#review-concept").value.trim();
    const status = $("#review-status-filter").value;
    const minSim = $("#review-min-sim").value.trim();
    if (concept) params.set("concept", concept);
    if (status) params.set("status", status);
    if (minSim) params.set("min_sim", minSim);
    params.set("limit", "200");
    return params.toString();
  }

  async function loadSamples() {
    const runId = $("#review-run").value;
    const list = $("#review-list");
    const empty = $("#review-empty");
    list.replaceChildren();
    if (!runId) {
      empty.textContent = I18n.t("review.empty");
      empty.hidden = false;
      return;
    }
    clearError();
    try {
      const res = await Api.get(`/runs/${encodeURIComponent(runId)}/samples?${query()}`);
      empty.hidden = res.samples.length > 0;
      if (!res.samples.length) empty.textContent = I18n.t("js.review.noMatch");
      announce(I18n.t("js.review.count", { total: res.total, shown: res.samples.length }));
      res.samples.forEach((sample, index) => list.appendChild(renderCard(runId, sample, index)));
    } catch (err) {
      showError(err.message || I18n.t("js.review.errSamples"));
    }
  }

  function renderCard(runId, sample, index) {
    const card = document.createElement("li");
    card.className = "sample-card";
    card.tabIndex = 0;
    card.dataset.status = sample.status;
    card.dataset.concept = sample.concept.split("/").pop();
    card.dataset.sim = (sample.meta && sample.meta.sim_max != null) ? sample.meta.sim_max : "—";

    const head = document.createElement("div");
    head.className = "sample-head";
    const title = document.createElement("strong");
    title.textContent = sample.title;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = badgeText(sample.status, card.dataset.concept, card.dataset.sim);
    head.append(title, badge);

    const desc = document.createElement("p");
    desc.textContent = sample.description;
    const keyw = document.createElement("p");
    keyw.className = "muted";
    keyw.textContent = sample.keywords;

    const actions = document.createElement("div");
    actions.className = "row";
    actions.append(
      statusButton(runId, sample.id, card, "approved", I18n.t("js.review.approve")),
      statusButton(runId, sample.id, card, "discarded", I18n.t("js.review.discard")),
    );

    card.append(head, desc, keyw, actions);
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "a") { ev.preventDefault(); setStatus(runId, sample.id, card, "approved"); }
      if (ev.key === "d") { ev.preventDefault(); setStatus(runId, sample.id, card, "discarded"); }
    });
    return card;
  }

  function statusButton(runId, sampleId, card, status, label) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost";
    btn.textContent = label;
    btn.addEventListener("click", () => setStatus(runId, sampleId, card, status));
    return btn;
  }

  async function setStatus(runId, sampleId, card, status) {
    clearError();
    try {
      await Api.post(`/runs/${encodeURIComponent(runId)}/samples/${encodeURIComponent(sampleId)}/status`,
        { status });
      card.dataset.status = status;
      card.querySelector(".badge").textContent =
        badgeText(status, card.dataset.concept, card.dataset.sim);
      announce(I18n.t("js.review.sampleStatus", { status: I18n.st(status) }));
      const next = card.nextElementSibling;
      if (next) next.focus();
    } catch (err) {
      showError(err.message || I18n.t("js.review.errUpdate"));
    }
  }

  $("#review-run").addEventListener("change", loadSamples);
  $("#review-apply").addEventListener("click", loadSamples);
  $("#review-refresh").addEventListener("click", refreshRuns);

  $("#review-regenerate").addEventListener("click", async () => {
    const runId = $("#review-run").value;
    if (!runId) return;
    if (!window.confirm(I18n.t("js.review.confirmRegen"))) {
      return;
    }
    clearError();
    const btn = $("#review-regenerate");
    btn.disabled = true;
    try {
      await Api.post(`/runs/${encodeURIComponent(runId)}/resume`);
      announce(I18n.t("js.review.regenStarted"));
    } catch (err) {
      showError(err.message || I18n.t("js.review.errRegen"));
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener("dataprep-app-ready", refreshRuns);
  // Re-render the run dropdown + visible sample cards on language change.
  // Await refreshRuns so the selection is restored before loadSamples reads it.
  window.addEventListener("dataprep-lang-changed", async () => {
    if (document.querySelector("#view-app").hidden) return;
    await refreshRuns();
    loadSamples();
  });
  // Refresh the run list when this tab is shown (runs started elsewhere appear).
  window.addEventListener("dataprep-tab-shown", (e) => {
    if (e.detail === "review") refreshRuns();
  });
})();
