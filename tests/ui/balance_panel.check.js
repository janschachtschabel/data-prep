// The balance panel, driven through the REAL UI scripts in a minimal DOM stub —
// plus the enrich result, which reports a stopped run the same way (H).
// Exit code 1 on any failed check; tests/test_ui_balance_panel.py runs it.
//
// Pins the review findings a markup test cannot see: a finished run must not
// re-arm the run button, a plan made for other inputs must not be run, nothing
// is sent without text columns, and a validation error is readable (#9, #18).
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const UI = path.join(__dirname, "..", "..", "app", "static", "ui");

class FakeElement {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase(); this.id = id;
    this.children = []; this.listeners = {}; this.dataset = {}; this.attributes = {};
    this.value = ""; this.disabled = false; this.hidden = false; this.textContent = "";
    this.className = ""; this.options = []; this.style = {}; this.files = []; this.checked = false;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async fire(type) {
    return Promise.all((this.listeners[type] || []).map(
      (fn) => fn({ type, target: this, preventDefault() {} })));
  }
  append(...n) { this.children.push(...n); }
  appendChild(n) { this.children.push(n); return n; }
  replaceChildren(...n) { this.children = n; this.options = n.filter((x) => x.tagName === "OPTION"); }
  add(o) { this.options.push(o); this.children.push(o); }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  focus() {} reset() {}
  querySelector() { return new FakeElement(); }
  querySelectorAll() { return []; }
}
class FakeOption extends FakeElement {
  constructor(text, value) { super("option"); this.text = text; this.value = value; }
}

const registry = new Map();
const $ = (sel) => {
  if (!registry.has(sel)) registry.set(sel, new FakeElement("div", sel));
  return registry.get(sel);
};

// Field rows as refine-fields.js builds them, reduced to what spec() reads.
function fieldRow(column, { list = false, sep = ",", min = 1, guidance = "" } = {}) {
  const controls = {
    ".field-spec-list": { checked: list }, ".field-spec-sep": { value: sep },
    ".field-spec-min": { value: String(min) }, ".field-spec-guidance": { value: guidance },
  };
  return { dataset: { column }, querySelector: (s) => controls[s] };
}
let rows = [];
const winListeners = {};
Object.assign(globalThis, {
  document: {
    querySelector: $,
    querySelectorAll: (sel) => (sel === "#refine-fields .field-spec" ? rows : []),
    createElement: (t) => new FakeElement(t),
    createTextNode: (t) => ({ textContent: t }),
    addEventListener() {},
    documentElement: {},
  },
  window: {
    addEventListener: (t, fn) => (winListeners[t] ||= []).push(fn),
    dispatchEvent: (e) => (winListeners[e.type] || []).forEach((fn) => fn(e)),
    confirm: () => true,
  },
  Option: FakeOption,
  Event: class { constructor(type) { this.type = type; } },
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
});

let respond = () => ({ status: 200, body: {} });
const sent = [];
globalThis.fetch = async (url, opts = {}) => {
  const body = opts.body ? JSON.parse(opts.body) : undefined;
  sent.push({ url, body });
  const { status, body: out, delay } = respond(url, body);
  if (delay) await new Promise((r) => setTimeout(r, delay));   // a request still in flight
  return { status, ok: status < 400, json: async () => out,
           headers: { get: () => "application/json" } };
};

for (const file of ["i18n.js", "api.js", "refine.js", "refine-fields.js"]) {
  vm.runInThisContext(fs.readFileSync(path.join(UI, file), "utf8"), { filename: file });
}
const settle = () => new Promise((r) => setTimeout(r, 20));
let failures = 0;
const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) failures += 1; };
const balanceCalls = () => sent.filter((s) => s.url.endsWith("/balance"));

(async () => {
  const PLAN = { target_per_label: 100, rows_to_add: 3, batches: 1, max_batches: 3,
                 max_calls: 3, call_budget: 2000, labels_below_target: 1,
                 labels_cut_by_limit: [], skipped_without_examples: [],
                 per_label: { Physik: { support: 1, deficit: 3, planned: 3, batches: 1,
                                        synthetic_share: 0.75 } } };
  const RUN = { target: "out", target_per_label: 100, rows_added: 3, labels_filled: 1,
                labels_cut_by_limit: [], per_label: {}, skipped_without_examples: [],
                usage: { calls: 1, tokens_total: 10 } };
  respond = (url, body) => {
    if (url.endsWith("/balance")) return { status: 200, body: body.dry_run ? PLAN : RUN };
    if (url === "/refine/datasets") return { status: 200, body: { datasets: [
      { name: "quelle", rows: 5, columns: ["t"] }, { name: "out", rows: 8, columns: ["t"] }] } };
    if (url.endsWith("/ops")) return { status: 200, body: { ops: [] } };
    return { status: 200, body: {} };
  };
  const run = $("#balance-run-btn");
  const preview = $("#balance-preview-btn");
  run.disabled = true;                          // as served by index.html
  $("#refine-dataset").value = "quelle";
  $("#balance-target").value = "100";
  $("#balance-limit").value = "500";
  $("#balance-name").value = "out";

  // A. no text columns chosen: nothing is sent, the user is told why
  rows = [];
  await preview.fire("click"); await settle();
  check(balanceCalls().length === 0, "A: no request without text columns");
  check(!$("#refine-error").hidden && $("#refine-error").textContent.length > 0,
        "A: the error box says what is missing");

  // B. a preview arms the run; a run disarms it again
  rows = [fieldRow("t")];
  $("#refine-error").hidden = true;
  await preview.fire("click"); await settle();
  check(balanceCalls().length === 1 && run.disabled === false, "B: a preview arms the run");
  check(preview.textContent === I18n.t("refine.balance.preview"),
        "B: the preview button gets its own label back");
  await run.fire("click"); await settle();
  check(balanceCalls().length === 2, "B: the run was sent");
  check(run.disabled === true, "B: after a run the button is disabled again");
  check(run.textContent === I18n.t("refine.balance.run"), "B: the run button gets its label back");
  await run.fire("click"); await settle();
  check(balanceCalls().length === 2, "B: a second click without a new preview sends nothing");

  // C. a programmatic dataset switch makes the shown plan stale
  $("#refine-dataset").value = "quelle";
  await preview.fire("click"); await settle();
  check(run.disabled === false, "C: armed again after a new preview");
  await Refine.refreshDatasets("out"); await settle();   // what filter/enrich/split do
  const before = balanceCalls().length;
  await run.fire("click"); await settle();
  check(balanceCalls().length === before, "C: no run for a plan made for another dataset");
  check(run.disabled === true, "C: the stale plan disarmed the button");

  // D. a changed field specification makes the plan stale too, even without an event
  $("#refine-dataset").value = "quelle";
  await preview.fire("click"); await settle();
  rows = [fieldRow("t", { list: true, sep: ";", min: 2 })];
  const before2 = balanceCalls().length;
  await run.fire("click"); await settle();
  check(balanceCalls().length === before2, "D: no run for a plan made with other fields");

  // F. a preview clicked while a run is in flight must not re-arm the run button
  //    (review round 2, finding 4: it did, and a second paid run went out)
  rows = [fieldRow("t")];
  $("#refine-dataset").value = "quelle";
  respond = (url, body) => {
    if (url.endsWith("/balance")) {
      return body.dry_run ? { status: 200, body: PLAN } : { status: 200, body: RUN, delay: 120 };
    }
    if (url === "/refine/datasets") return { status: 200, body: { datasets: [
      { name: "quelle", rows: 5, columns: ["t"] }, { name: "out", rows: 8, columns: ["t"] }] } };
    return { status: 200, body: { ops: [] } };
  };
  await preview.fire("click"); await settle();
  const runsBefore = balanceCalls().filter((s) => !s.body.dry_run).length;
  const inFlight = run.fire("click");
  await settle();
  const previewsBefore = balanceCalls().filter((s) => s.body.dry_run).length;
  await preview.fire("click"); await settle();
  check(run.disabled === true, "F: the run button stays disabled while a run is in flight");
  check(balanceCalls().filter((s) => s.body.dry_run).length === previewsBefore,
        "F: no preview is sent while a run is in flight");
  await run.fire("click"); await settle();
  await inFlight; await settle();
  check(balanceCalls().filter((s) => !s.body.dry_run).length === runsBefore + 1,
        "F: exactly one run went out");

  // G. the result names what the gates discarded
  const DISCARDING = { ...RUN, per_label: { Physik: { support: 1, added: 1, missing: 2,
    synthetic_share: 0.5, discarded_duplicate: 1, discarded_short: 2, discarded_long: 3,
    discarded_incomplete: 0 } } };
  respond = (url, body) => (url.endsWith("/balance")
    ? { status: 200, body: body.dry_run ? PLAN : DISCARDING }
    : { status: 200, body: { datasets: [{ name: "quelle", rows: 5, columns: ["t"] }], ops: [] } });
  $("#refine-dataset").value = "quelle";
  await preview.fire("click"); await settle();
  await run.fire("click"); await settle();
  const said = $("#balance-result").children.map((c) => c.textContent).join(" | ");
  check(said.includes(I18n.t("js.refine.balanceDiscarded",
    { duplicate: 1, short: 2, long: 3, incomplete: 0 })), `G: the discards are named (${said})`);

  // H. an enrichment a cap ended early says so; what it enriched until then is saved
  const REASON = "Budget reached: 14 tokens (cap 10).";
  respond = (url) => (url.endsWith("/enrich")
    ? { status: 200, body: { field: "t", rows: 3, enriched: 1, target: "angereichert",
                             stopped: REASON, usage: { calls: 1, tokens_total: 14 } } }
    : { status: 200, body: { datasets: [{ name: "quelle", rows: 3, columns: ["t"] }], ops: [] } });
  rows = [fieldRow("t")];
  $("#refine-dataset").value = "quelle";
  $("#enrich-target").value = "angereichert";
  $("#enrich-field").value = "t";
  await $("#enrich-btn").fire("click"); await settle();
  const told = $("#refine-result").children.map((c) => c.textContent).join(" | ");
  check(told.includes(I18n.t("js.refine.enrichStopped", { reason: REASON })),
        `H: an enrichment stopped by a cap says so (${told})`);

  // E. a 422 reads as text, not "[object Object]"
  respond = (url) => url.endsWith("/balance")
    ? { status: 422, body: { detail: [{ type: "value_error", loc: ["body", "fields"],
                                        msg: "Value error, Field 't' is named more than once." }] } }
    : { status: 200, body: { datasets: [], ops: [] } };
  await preview.fire("click"); await settle();
  const text = $("#refine-error").textContent;
  check(!text.includes("[object Object]") && text.includes("named more than once"),
        `E: a validation error is readable (${JSON.stringify(text)})`);

  console.log(failures ? `${failures} check(s) failed` : "all checks passed");
  process.exit(failures ? 1 : 0);
})();
