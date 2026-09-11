/* Transport layer: API-key handling + fetch wrapper. Same-origin, no CORS.
   The key lives in sessionStorage only (gone when the tab closes) and is sent
   as the X-API-Key header on every request — exactly the API's auth model. */
"use strict";

const Api = (() => {
  const KEY = "dataprep-key";
  // Per-request LLM credentials (open-instance mode): the user's OWN OpenAI key
  // + model, kept in this tab only and sent as X-LLM-* headers so the server
  // needs no AI key of its own. Same sessionStorage posture as the app key.
  const LLM_KEY = "dataprep-llm-key";
  const LLM_MODEL = "dataprep-llm-model";

  const getKey = () => sessionStorage.getItem(KEY) || "";
  const setKey = (k) => sessionStorage.setItem(KEY, k);
  const clearKey = () => sessionStorage.removeItem(KEY);

  const getLlmKey = () => sessionStorage.getItem(LLM_KEY) || "";
  const getLlmModel = () => sessionStorage.getItem(LLM_MODEL) || "";
  const setLlm = (key, model) => {
    key ? sessionStorage.setItem(LLM_KEY, key) : sessionStorage.removeItem(LLM_KEY);
    model ? sessionStorage.setItem(LLM_MODEL, model) : sessionStorage.removeItem(LLM_MODEL);
  };
  const clearLlm = () => { sessionStorage.removeItem(LLM_KEY); sessionStorage.removeItem(LLM_MODEL); };

  class ApiError extends Error {
    constructor(status, detail) {
      super(detail || I18n.t("js.err.requestFailed", { status }));
      this.status = status;
    }
  }

  async function request(path, { method = "GET", json, form } = {}) {
    const headers = {};
    const key = getKey();
    if (key) headers["X-API-Key"] = key; // keyless mode: rely on the server's auth setting
    // Per-request LLM credentials (when the user provided them). Harmless on
    // non-LLM routes; the server ignores them there.
    const llmKey = getLlmKey();
    if (llmKey) headers["X-LLM-Key"] = llmKey;
    const llmModel = getLlmModel();
    if (llmModel) headers["X-LLM-Model"] = llmModel;
    let body;
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(json);
    } else if (form !== undefined) {
      body = form; // browser sets the multipart boundary itself
    }
    const res = await fetch(path, { method, headers, body });
    if (res.status === 401) {
      // Only broadcast when a key was actually in use: a first visit without a
      // key must show a neutral login, not an "expired" error.
      const hadKey = Boolean(key);
      clearKey();
      if (hadKey) window.dispatchEvent(new Event("dataprep-unauthorized"));
      throw new ApiError(401, I18n.t("js.err.keyInvalid"));
    }
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch { /* non-JSON error body */ }
      throw new ApiError(res.status, detail);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res;
  }

  /* POST something that is stored under a name. The server answers 409 when
     the name is taken and `overwrite` was not sent -- nothing is replaced
     silently. Ask in the UI language, then resend with overwrite; a "no"
     leaves everything unchanged and surfaces as an ordinary error message.
     `name` is only for the question (null when the server derives it). */
  async function postGuarded(path, payload, name) {
    const isForm = payload instanceof FormData;
    const send = (overwrite) => {
      if (isForm) {
        if (overwrite) payload.set("overwrite", "true");
        return request(path, { method: "POST", form: payload });
      }
      return request(path, { method: "POST", json: overwrite ? { ...payload, overwrite: true } : payload });
    };
    try {
      return await send(false);
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 409) throw err;
      const question = name
        ? I18n.t("js.overwrite.confirm", { name })
        : I18n.t("js.overwrite.confirmUnnamed");
      if (!window.confirm(question)) throw new ApiError(409, I18n.t("js.overwrite.kept"));
      return send(true);
    }
  }

  /* The name the server derives from an upload when none is typed (Path.stem). */
  const stem = (filename) => (filename || "").replace(/\.[^.]*$/, "");

  /* Authenticated file download: fetch as blob, hand to the browser. */
  async function download(path, filename) {
    const res = await request(path);
    const blob = res instanceof Response ? await res.blob() : new Blob([JSON.stringify(res)]);
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    a.click();
    URL.revokeObjectURL(url);
  }

  return {
    getKey, setKey, clearKey, getLlmKey, getLlmModel, setLlm, clearLlm, ApiError, download,
    postGuarded, stem,
    get: (p) => request(p),
    post: (p, json) => request(p, { method: "POST", json }),
    put: (p, json) => request(p, { method: "PUT", json }),
    postForm: (p, form) => request(p, { method: "POST", form }),
    del: (p) => request(p, { method: "DELETE" }),
  };
})();
