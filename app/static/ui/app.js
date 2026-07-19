/* App shell: login flow (with keyless probe), tab switching, config summary. */
"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const viewLogin = $("#view-login");
  const viewApp = $("#view-app");
  const loginError = $("#login-error");
  const keyInput = $("#api-key");

  /* ---- tabs (plain buttons, [hidden] panels — same pattern as api_v3) ---- */
  const tabs = document.querySelectorAll(".tab");
  function switchTab(name) {
    tabs.forEach((btn) => {
      if (btn.dataset.tab === name) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.hidden = panel.id !== `tab-${name}`;
    });
    // Let the shown tab refresh its inputs — e.g. a seed set built in the Seeds
    // tab must appear in the Runs dropdown without reloading the page.
    window.dispatchEvent(new CustomEvent("dataprep-tab-shown", { detail: name }));
  }
  tabs.forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));

  /* ---- views ---- */
  function showLogin(message) {
    viewApp.hidden = true;
    viewLogin.hidden = false;
    if (message) {
      loginError.textContent = message;
      loginError.hidden = false;
    } else {
      loginError.hidden = true;
    }
    keyInput.focus();
  }

  function enterApp(authEnabled) {
    $("#auth-off-badge").hidden = authEnabled;
    viewLogin.hidden = true;
    viewApp.hidden = false;
    loadLlmSummary();
    // Feature modules (vocab.js, ...) initialize their data on this signal.
    window.dispatchEvent(new Event("dataprep-app-ready"));
  }

  /* Reflect the effective AI models in the Runs tab AND drive the AI-access
     panel: prefill the user's stored key/model, seed the default-model
     placeholder, and open the panel when neither the server nor the user has a
     key yet (so an open instance nudges the user to provide one). */
  async function loadLlmSummary() {
    const target = $("#llm-summary");
    let cfg = null;
    try {
      cfg = await Api.get("/config");
      const ownModel = Api.getLlmModel();
      const shown = { seeds: ownModel || cfg.llm.seeds.model, bulk: ownModel || cfg.llm.bulk.model };
      target.textContent = I18n.t("js.app.models", shown);
    } catch {
      target.textContent = I18n.t("js.app.cfgUnavailable");
    }
    syncLlmPanel(cfg);
  }

  /* ---- AI-access panel (per-request OpenAI key + model) ---- */
  function serverHasKey(cfg) {
    return Boolean(cfg && cfg.llm && cfg.llm.bulk && cfg.llm.bulk.key_configured);
  }

  function llmStatusText(cfg) {
    if (Api.getLlmKey()) {
      return I18n.t("js.llm.usingOwn", { model: Api.getLlmModel() || I18n.t("js.llm.defaultModel") });
    }
    return serverHasKey(cfg) ? I18n.t("js.llm.usingServer") : I18n.t("js.llm.none");
  }

  function syncLlmPanel(cfg) {
    const details = $("#llm-access");
    if (!details || !$("#llm-key") || !$("#llm-model")) return;
    $("#llm-key").value = Api.getLlmKey();
    $("#llm-model").value = Api.getLlmModel();
    if (cfg && cfg.llm && cfg.llm.bulk) $("#llm-model").placeholder = cfg.llm.bulk.model;
    $("#llm-status").textContent = llmStatusText(cfg);
    // Open only when there is no usable key at all — otherwise stay collapsed.
    if (!Api.getLlmKey() && !serverHasKey(cfg)) details.open = true;
  }

  $("#llm-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    Api.setLlm($("#llm-key").value.trim(), $("#llm-model").value.trim());
    $("#llm-status").textContent = I18n.t("js.llm.saved");
    loadLlmSummary();
  });
  $("#llm-clear").addEventListener("click", () => {
    Api.clearLlm();
    $("#llm-key").value = "";
    $("#llm-model").value = "";
    $("#llm-status").textContent = I18n.t("js.llm.cleared");
    loadLlmSummary();
  });

  /* ---- login flow ---- */
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const value = keyInput.value.trim();
    if (!value) {
      showLogin(I18n.t("js.login.enterKey"));
      return;
    }
    const btn = $("#login-btn");
    btn.disabled = true; // prevent double submission while the probe runs
    Api.setKey(value);
    try {
      const res = await Api.get("/auth/check");
      enterApp(res.auth_enabled);
    } catch (err) {
      Api.clearKey();
      showLogin(err.message || I18n.t("js.login.failed"));
    } finally {
      btn.disabled = false;
    }
  });

  $("#logout-btn").addEventListener("click", () => {
    Api.clearKey();
    showLogin();
  });

  window.addEventListener("dataprep-unauthorized", () => showLogin(I18n.t("js.err.keyInvalid")));

  // Re-render the (translated) model summary when the language switches.
  window.addEventListener("dataprep-lang-changed", () => {
    if (!viewApp.hidden) loadLlmSummary();
  });

  /* ---- boot: stored key or keyless server -> straight into the app ---- */
  (async () => {
    try {
      const res = await Api.get("/auth/check");
      enterApp(res.auth_enabled);
    } catch {
      showLogin();
    }
  })();
})();
