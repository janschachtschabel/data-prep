// The table export controls, driven through the REAL UI scripts in a minimal DOM
// stub. Exit code 1 on any failed check; tests/test_ui_tables_export.py runs it.
//
// Pins what a markup test cannot see: `spreadsheet_safe` is a CSV property, so the
// checkbox must not stay live for JSON and JSONL, where the download ignores it
// (review of the merged branches, 2026-09-20). The tick itself survives a detour
// through JSON — clearing it would hand back an undefused CSV to someone who had
// asked for the defused one.
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
  focus() {} reset() {} click() {}
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

// The anchors Api.download builds, so the offered filename can be read back.
const anchors = [];
const winListeners = {};
Object.assign(globalThis, {
  document: {
    querySelector: $,
    querySelectorAll: () => [],
    createElement: (t) => {
      const el = new FakeElement(t);
      if (t === "a") anchors.push(el);
      return el;
    },
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
  Blob: class { constructor(parts) { this.parts = parts; } },
  Response: class {},
  URL: { createObjectURL: () => "blob:stub", revokeObjectURL() {} },
  Event: class { constructor(type) { this.type = type; } },
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
});

const sent = [];
globalThis.fetch = async (url) => {
  sent.push(url);
  return { status: 200, ok: true, json: async () => ({}),
           headers: { get: () => "application/json" } };
};

// As index.html serves it: CSV is the preselected format, the box starts unticked.
$("#tables-export-format").value = "csv";
$("#tables-export-separator").value = ";";
$("#tables-dataset").value = "quelle";

for (const file of ["i18n.js", "api.js", "tables.js"]) {
  vm.runInThisContext(fs.readFileSync(path.join(UI, file), "utf8"), { filename: file });
}
const settle = () => new Promise((r) => setTimeout(r, 20));
let failures = 0;
const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) failures += 1; };

const format = $("#tables-export-format");
const box = $("#tables-export-spreadsheet");
const downloads = () => sent.filter((u) => u.includes("/download?"));

(async () => {
  // A. CSV is chosen at load, so the checkbox is live from the start
  check(box.disabled === false, "A: the checkbox is live for CSV");

  // B. JSON ignores the flag, so the checkbox says so instead of staying live
  box.checked = true;
  format.value = "json";
  await format.fire("change");
  check(box.disabled === true, "B: JSON disables the checkbox");

  // C. ...and a download made there is neither defused nor named as if it were
  await $("#tables-download-btn").fire("click"); await settle();
  const url = downloads().at(-1);
  check(url !== undefined && !url.includes("spreadsheet_safe"),
        `C: no spreadsheet_safe for JSON (${url})`);
  check(anchors.at(-1).download === "quelle.json",
        `C: the file is not named .spreadsheet (${anchors.at(-1).download})`);

  // D. back on CSV the control is live again AND the tick survived the detour
  format.value = "csv";
  await format.fire("change");
  check(box.disabled === false, "D: CSV makes the checkbox live again");
  check(box.checked === true, "D: the detour through JSON did not clear the tick");
  await $("#tables-download-btn").fire("click"); await settle();
  check(downloads().at(-1).includes("spreadsheet_safe=true"),
        `D: the CSV download is defused again (${downloads().at(-1)})`);
  check(anchors.at(-1).download === "quelle.spreadsheet.csv",
        `D: and named for it (${anchors.at(-1).download})`);

  // E. the gzip variant is a CSV too
  format.value = "csv.gz";
  await format.fire("change");
  check(box.disabled === false, "E: csv.gz keeps the checkbox live");

  // F. JSONL ignores the flag as JSON does
  format.value = "jsonl";
  await format.fire("change");
  check(box.disabled === true, "F: JSONL disables the checkbox");

  console.log(failures ? `${failures} check(s) failed` : "all checks passed");
  process.exit(failures ? 1 : 0);
})();
