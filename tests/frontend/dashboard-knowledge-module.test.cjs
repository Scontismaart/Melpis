const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const shared = fs.readFileSync(path.join(root, "web/dashboard-shared.js"), "utf8");
const dialogFocus = fs.readFileSync(path.join(root, "web/dialog-focus.js"), "utf8");
const source = fs.readFileSync(path.join(root, "web/dashboard-knowledge.js"), "utf8");

function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  return { promise, resolve };
}
function response(status, body) {
  return { status, ok: status >= 200 && status < 300, async json() { return body; } };
}
function controlledTimers(window) {
  let nextId = 0;
  const callbacks = new Map();
  window.setTimeout = (callback) => { const id = ++nextId; callbacks.set(id, callback); return id; };
  window.clearTimeout = (id) => callbacks.delete(id);
  return async function runNext() {
    const entry = callbacks.entries().next().value;
    if (!entry) return false;
    const [id, callback] = entry;
    callbacks.delete(id);
    await callback();
    await settle();
    return true;
  };
}
const emptySummary = { faq: {}, documenti: {}, web: {}, dati_struttura: {}, conflitti_totali: 0 };
const faq = { id: "faq-1", nome: "Orari", metadata: { domanda: "Orari?", risposta: "9–18" }, is_active: true };
const availableLimits = (overrides = {}) => ({
  rag_enabled: true, plan: "starter",
  technical_limits: { upload_bytes: 20 * 1024 * 1024, pages_per_document: 200, chunks_per_document: 500 },
  usage: { documents: 0, pages: 0, chunks: 0 },
  file_quota: { status: "available", limit_bytes: 5_000_000, committed_bytes: 0, reserved_bytes: 0,
    occupied_bytes: 0, remaining_bytes: 5_000_000, unknown_documents: 0, scope: "paid",
    originals_retained: false, new_uploads_allowed: true, replacements_allowed: true },
  ...overrides,
});

function fixture(transport = async (url) => {
  if (url.endsWith("/summary")) return response(200, emptySummary);
  if (url.includes("/dati-struttura")) return response(200, { servizi: [], orari: "" });
  return response(200, { documenti: [] });
}, confirm = async () => true, configureWindow = null) {
  const dom = new JSDOM(html, { url: "https://melpis.test/app/knowledge", runScripts: "outside-only" });
  const { window } = dom;
  configureWindow?.(window);
  // Dashboard source partials are intentionally not composed into web/index.html in this task.
  const documentsPanel = window.document.getElementById("kb-panel-documenti");
  if (documentsPanel && !window.document.getElementById("kb-doc-limits")) {
    const limits = window.document.createElement("div");
    limits.id = "kb-doc-limits";
    documentsPanel.appendChild(limits);
  }
  if (documentsPanel && !window.document.getElementById("kb-doc-upload-retry")) {
    const retry = window.document.createElement("button");
    retry.id = "kb-doc-upload-retry";
    retry.hidden = true;
    window.document.getElementById("doc-carica-status")?.after(retry);
  }
  const knowledgePanel = window.document.querySelector('[data-view-panel="conoscenza"]');
  if (knowledgePanel && !window.document.getElementById("kb-conflitti-parziali")) {
    const partialWarning = window.document.createElement("div");
    partialWarning.id = "kb-conflitti-parziali";
    partialWarning.hidden = true;
    knowledgePanel.prepend(partialWarning);
  }
  const context = { userId: "user-1", sessionOrganizationId: "org-1",
    selectedOrganizationId: "org-1", view: "conoscenza", transition: 1, epoch: 1 };
  const calls = [];
  window.eval(dialogFocus);
  window.eval(shared);
  window.eval(source);
  const module = window.MelpisDashboardKnowledge.create({
    API_BASE: "", apiFetch(url, options) {
      calls.push({ url, options });
      return transport(url, options);
    },
    _escapeHtml: window.MelpisDashboardShared.escapeHtml,
    _sanitize: window.MelpisDashboardShared.sanitize,
    _tDash: (_key, fallback, values = {}) => fallback.replace(/{{(\w+)}}/g, (_, name) => values[name] ?? `{{${name}}}`),
    localeCorrente: () => "it-IT",
    confermaDestructiva: confirm,
    getContext: () => ({ ...context }),
  });
  module.onEnter();
  return { dom, window, document: window.document, context, module, calls };
}
async function settle() {
  for (let i = 0; i < 100; i += 1) await Promise.resolve();
}

test("Knowledge renders FAQ, empty, error, and hostile names as inert text", async (t) => {
  let list = [faq];
  const h = fixture(async (url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.includes("tipo=faq")) return response(200, { documenti: list });
    return response(200, { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  assert.match(h.document.getElementById("kb-faq-list").textContent, /Orari\?/);
  list = [{ ...faq, nome: '<img src=x onerror=alert(1)>', metadata: {} }];
  await h.module.aggiornaConoscenzaCompleta();
  assert.equal(h.document.querySelector("#kb-faq-list img"), null);
  assert.match(h.document.getElementById("kb-faq-list").textContent, /<img/);
  list = [];
  await h.module.aggiornaConoscenzaCompleta();
  assert.match(h.document.getElementById("kb-faq-list").textContent, /Nessuna FAQ/);
  h.module.onExit();
  h.context.transition += 1;
  h.module.onEnter();
  const broken = fixture(async (url) => response(url.endsWith("/summary") ? 200 : 500, emptySummary));
  t.after(() => broken.dom.window.close());
  await broken.module.aggiornaConoscenzaCompleta();
  assert.match(broken.document.getElementById("kb-faq-list").textContent, /Errore nel caricamento/);
});

test("stale list and summary cannot render after organization or view change", async (t) => {
  const delayed = deferred();
  const h = fixture((url) => url.endsWith("/summary") ? delayed.promise : response(200, { documenti: [faq] }));
  t.after(() => h.dom.window.close());
  const pending = h.module.aggiornaConoscenzaCompleta();
  await settle();
  h.context.selectedOrganizationId = "org-2";
  h.module.onEnter();
  delayed.resolve(response(200, { ...emptySummary, faq: { totale: 9 } }));
  await pending;
  assert.equal(h.document.getElementById("kb-badge-faq").textContent, "");
  assert.equal(h.calls.length, 1);
  h.module.onExit();
  h.context.view = "panoramica";
  h.context.transition += 1;
  await h.module.aggiornaConoscenzaCompleta();
  assert.equal(h.calls.length, 1);
});

test("incomplete conflict scan warns that zero conflicts are not certified", async (t) => {
  const summary = { ...emptySummary, conflitti_totali: 0, conflitti_completi: false };
  const h = fixture((url) => response(200, url.endsWith("/summary") ? summary : { documenti: [] }));
  t.after(() => h.dom.window.close());

  await h.module.aggiornaConoscenzaCompleta();

  const warning = h.document.getElementById("kb-conflitti-parziali");
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /Verifica conflitti parziale/);
  assert.match(warning.textContent, /assenza di conflitti non è confermata/);
  assert.equal(h.calls.some((call) => call.url.endsWith("/api/conoscenza/conflitti")), false);
});

test("logout while a FAQ read is pending leaves no private content", async (t) => {
  const delayed = deferred();
  const h = fixture((url) => url.endsWith("/summary")
    ? response(200, emptySummary) : delayed.promise);
  t.after(() => h.dom.window.close());
  const pending = h.module.aggiornaConoscenzaCompleta();
  await settle();
  h.context.userId = null;
  h.context.epoch += 1;
  h.module.invalidate();
  delayed.resolve(response(200, { documenti: [faq] }));
  await pending;
  assert.equal(h.document.querySelector("#kb-faq-list .kb-item-card"), null);
});

test("a pending tab read cannot render after switching tabs", async (t) => {
  const delayed = deferred();
  const h = fixture((url) => {
    if (url.includes("tipo=faq")) return delayed.promise;
    return response(200, url.endsWith("/summary") ? emptySummary : { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="faq"]').click();
  await settle();
  h.document.querySelector('[data-kb-tab="web"]').click();
  await settle();
  delayed.resolve(response(200, { documenti: [faq] }));
  await settle();
  assert.equal(h.document.querySelector("#kb-faq-list .kb-item-card"), null);
  assert.equal(h.document.getElementById("kb-panel-web").hidden, false);
});

test("reentering the panel does not register another tab listener", async (t) => {
  const h = fixture();
  t.after(() => h.dom.window.close());
  h.module.onExit();
  h.context.transition += 1;
  h.module.onEnter();
  h.module.onExit();
  h.context.transition += 1;
  h.module.onEnter();
  h.document.querySelector('[data-kb-tab="web"]').click();
  await settle();
  assert.equal(h.calls.filter((c) => c.url.includes("tipo=web")).length, 1);
});

test("FAQ save stays single-flight across exit and restores the initial control", async (t) => {
  const delayed = deferred();
  const h = fixture((url, options) => options?.method === "POST" ? delayed.promise
    : response(200, url.endsWith("/summary") ? emptySummary : { documenti: [] }));
  t.after(() => h.dom.window.close());
  const button = h.document.getElementById("kb-faq-save-btn");
  const initial = button.innerHTML;
  h.document.getElementById("kb-faq-domanda").value = "Question";
  h.document.getElementById("kb-faq-risposta").value = "Answer";
  button.click();
  button.dispatchEvent(new h.window.Event("click"));
  assert.equal(h.calls.filter((c) => c.options?.method === "POST").length, 1);
  h.module.onExit();
  assert.equal(button.disabled, false);
  assert.equal(button.innerHTML, initial);
  h.context.transition += 1;
  h.module.onEnter();
  button.click();
  assert.equal(h.calls.filter((c) => c.options?.method === "POST").length, 1);
  delayed.resolve(response(200, {}));
  await settle();
  assert.equal(h.document.getElementById("kb-faq-domanda").value, "Question");
  button.click();
  await settle();
  assert.equal(h.calls.filter((c) => c.options?.method === "POST").length, 2);
});

test("file upload rejection and retry retain the original input listener", async (t) => {
  let uploads = 0;
  const h = fixture(async (url, options) => {
    if (options?.method === "POST") return response(++uploads === 1 ? 400 : 200, { detail: "File" });
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    return response(200, url.endsWith("/summary") ? emptySummary : { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  const input = h.document.getElementById("doc-file");
  const zone = h.document.getElementById("kb-doc-dropzone");
  const file = new h.window.File(["hello"], "menu.txt", { type: "text/plain" });
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  input.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.equal(uploads, 1);
  h.module.onExit();
  h.context.transition += 1;
  h.module.onEnter();
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  assert.equal(zone.querySelector("#doc-file"), input);
  input.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.equal(uploads, 2);
});

test("file upload double dispatch makes one request while pending", async (t) => {
  const delayed = deferred();
  let uploads = 0;
  let uploaded = false;
  let uploadedKey;
  const h = fixture((url, options) => {
    if (options?.method === "POST") { uploads += 1; uploadedKey = options.headers["Idempotency-Key"]; return delayed.promise; }
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/elenco") && uploaded) return response(200, { documenti: [
      { id: "uploaded-1", nome: "menu.txt", tipo: "documento", stato: "indicizzata", index_verified: true,
        index_status: "idle", index_request_id: uploadedKey, published_request_id: uploadedKey, is_active: true },
    ] });
    return response(200, { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const input = h.document.getElementById("doc-file");
  Object.defineProperty(input, "files", { configurable: true,
    value: [new h.window.File(["hello"], "menu.txt", { type: "text/plain" })] });
  input.dispatchEvent(new h.window.Event("change"));
  input.dispatchEvent(new h.window.Event("change"));
  assert.equal(uploads, 1);
  uploaded = true;
  delayed.resolve(response(200, { detail: "Accepted", id: "uploaded-1", index_request_id: uploadedKey }));
  await settle();
  assert.match(h.document.getElementById("doc-carica-status").textContent, /indicizzato con successo/);
});

test("document limits render backend values and keep missing values unavailable", async (t) => {
  const h = fixture(async (url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, {
      rag_enabled: true, plan: "starter",
      technical_limits: { upload_bytes: 8 * 1024 * 1024, pages_per_document: 42, chunks_per_document: 90 },
      usage: { documents: 2, pages: 9, chunks: 12 },
      file_quota: { status: "available", limit_bytes: 12_500_000, committed_bytes: 2_000_000, reserved_bytes: 500_000,
        occupied_bytes: 2_500_000, remaining_bytes: 10_000_000, unknown_documents: 0, scope: "paid",
        originals_retained: false, new_uploads_allowed: true, replacements_allowed: true },
    });
    return response(200, { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const limits = h.document.getElementById("kb-doc-limits");
  assert.match(limits.textContent, /8 MiB, 42 pagine, 90 segmenti/);
  assert.match(limits.textContent, /2 documenti, 9 pagine, 12 segmenti/);
  assert.match(limits.textContent, /2,5 MB occupati su 12,5 MB/);
  assert.equal(limits.textContent.includes("100 documenti"), false);

  const unknown = fixture(async (url) => url.endsWith("/limiti")
    ? response(200, { rag_enabled: true, plan: "starter", technical_limits: {}, usage: {}, file_quota: { status: "not_configured", new_uploads_allowed: false, replacements_allowed: false } })
    : response(200, url.endsWith("/summary") ? emptySummary : { documenti: [] }));
  t.after(() => unknown.dom.window.close());
  unknown.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  assert.match(unknown.document.getElementById("kb-doc-limits").textContent, /Limiti tecnici non disponibili/);
  assert.match(unknown.document.getElementById("kb-doc-limits").textContent, /Utilizzo non disponibile/);
});

test("RAG access and file quota control separate upload actions", async (t) => {
  const h = fixture(async (url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits({
      plan: "pro", file_quota: { ...availableLimits().file_quota, status: "full", limit_bytes: 5_000_000,
        occupied_bytes: 5_000_000, remaining_bytes: 0, new_uploads_allowed: false, replacements_allowed: true },
    }));
    return response(200, { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  assert.equal(h.document.getElementById("doc-file").disabled, true);
  assert.equal(h.document.getElementById("doc-carica-btn").disabled, false, "pasted text is not part of the original-file quota");
  assert.match(h.document.getElementById("kb-doc-limits").textContent, /Quota file piena/);
});

test("the technical per-file limit blocks oversized files before upload", async (t) => {
  let uploads = 0;
  const h = fixture(async (url, options) => {
    if (options?.method === "POST") { uploads += 1; return response(200, {}); }
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    return response(200, { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const input = h.document.getElementById("doc-file");
  const file = new h.window.File(["tiny"], "large.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "size", { configurable: true, value: 21 * 1024 * 1024 });
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  input.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.equal(uploads, 0);
  assert.match(h.document.getElementById("doc-carica-status").textContent, /20 MiB/);
});

test("a document list response from a previous organization cannot replace the selected organization's list", async (t) => {
  const oldList = deferred();
  const h = fixture((url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    return oldList.promise;
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.context.selectedOrganizationId = "org-2";
  h.module.onEnter();
  oldList.resolve(response(200, { documenti: [{ id: "private", nome: "Private.pdf", tipo: "documento", stato: "indicizzata", index_verified: true, is_active: true }] }));
  await settle();
  assert.equal(h.document.getElementById("doc-library").textContent.includes("Private.pdf"), false);
});

test("document lifecycle does not imply readiness for pending, failed, or unverified rows", async (t) => {
  const rows = [
    { id: "pending", nome: "Pending.pdf", tipo: "documento", stato: "caricato", is_active: false },
    { id: "working", nome: "Working.pdf", tipo: "documento", stato: "in_elaborazione", is_active: false },
    { id: "failed", nome: "Failed.pdf", tipo: "documento", stato: "errore", errore: "SECRET RAW DETAILS", is_active: false },
    { id: "old", nome: "Old.pdf", tipo: "documento", stato: "indicizzata", is_active: true },
    { id: "unverified", nome: "Unverified.pdf", tipo: "documento", stato: "indicizzata", index_verified: false, is_active: true },
  ];
  const h = fixture(async (url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    return response(200, { documenti: rows });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const list = h.document.getElementById("doc-library");
  assert.match(list.textContent, /Caricato/);
  assert.match(list.textContent, /In elaborazione/);
  assert.match(list.textContent, /Da reindicizzare/);
  assert.match(list.textContent, /Stato non disponibile/);
  assert.equal(list.textContent.includes("Indicizzato"), false);
  assert.equal(list.textContent.includes("SECRET RAW DETAILS"), false);
  assert.equal(list.querySelectorAll(".kb-item-card .kb-switch").length, 0);
  assert.equal(list.querySelectorAll(".kb-item-card button").length, 7, "errored/unverified rows offer reindex and every row can be deleted");
});

test("network retry reuses the upload idempotency key", async (t) => {
  const keys = [];
  let uploads = 0;
  let limitReads = 0;
  const h = fixture(async (url, options) => {
    if (options?.method === "POST") {
      keys.push(options.headers["Idempotency-Key"]);
      return response(++uploads === 1 ? 503 : 200, { detail: "Retry" });
    }
    if (url.endsWith("/limiti")) return response(200, limitReads++ === 0 ? availableLimits() : availableLimits({
      file_quota: { ...availableLimits().file_quota, status: "full", occupied_bytes: 5_000_000,
        remaining_bytes: 0, new_uploads_allowed: false },
    }));
    return response(200, url.endsWith("/summary") ? emptySummary : { documenti: [] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const input = h.document.getElementById("doc-file");
  Object.defineProperty(input, "files", { configurable: true,
    value: [new h.window.File(["hello"], "retry.pdf", { type: "application/pdf" })] });
  input.dispatchEvent(new h.window.Event("change"));
  await settle();
  const retry = h.document.getElementById("kb-doc-upload-retry");
  assert.equal(retry.hidden, false);
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  assert.equal(h.document.getElementById("doc-file").disabled, true, "the updated quota blocks new file keys");
  retry.click();
  await settle();
  assert.equal(keys.length, 2);
  assert.ok(keys[0]);
  assert.equal(keys[0], keys[1]);
});

test("reindex uploads the selected original file with a fresh idempotency key", async (t) => {
  const rows = [{ id: "old-doc", nome: "Old.pdf", tipo: "documento", stato: "indicizzata", index_verified: false, is_active: true }];
  let submitted;
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits({ file_quota: {
      ...availableLimits().file_quota, status: "full", occupied_bytes: 5_000_000, remaining_bytes: 0,
      new_uploads_allowed: false, replacements_allowed: true,
    } }));
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      submitted = { url, options };
      return response(200, { detail: "Accepted" });
    }
    return response(200, { documenti: rows });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  const file = new h.window.File(["original"], "Old.pdf", { type: "application/pdf" });
  Object.defineProperty(picker, "files", { configurable: true, value: [file] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.match(submitted.url, /\/api\/documenti\/old-doc\/reindicizza$/);
  assert.ok(submitted.options.headers["Idempotency-Key"]);
  assert.equal(submitted.options.body.get("file").name, "Old.pdf");
});

test("replacement polling never treats the previous verified publication as the new index", async (t) => {
  let requestKey;
  let replacementStarted = false;
  let runNextTimer;
  const row = () => ({ id: "old-doc", nome: "Old.pdf", tipo: "documento", stato: "indicizzata",
    index_verified: true, is_active: true, index_status: replacementStarted ? "in_progress" : "failed",
    index_request_id: replacementStarted ? requestKey : "old-request",
    published_request_id: "old-published" });
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      requestKey = options.headers["Idempotency-Key"];
      replacementStarted = true;
      return response(202, { id: "old-doc" });
    }
    return response(200, { documenti: [row()] });
  }, async () => true, (window) => { runNextTimer = controlledTimers(window); });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Old.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();

  assert.match(h.document.getElementById("doc-carica-status").textContent, /Aggiornamento dell'indice in corso/);
  assert.equal(h.document.querySelector("#doc-library .kb-switch input").checked, true,
    "the previously published version remains active during replacement");
  assert.match(h.document.querySelector("#doc-library .kb-document-index-update-notice").textContent,
    /versione precedente resta disponibile/);
  assert.equal(await runNextTimer(), true, "the pending replacement is polled");
  assert.match(h.document.getElementById("doc-carica-status").textContent, /Aggiornamento dell'indice in corso/);
  assert.doesNotMatch(h.document.getElementById("doc-carica-status").textContent, /indicizzato con successo/);
});

test("failed replacement keeps the published version, offers retry, and reuses its idempotency key", async (t) => {
  let requestKey;
  let requestCount = 0;
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits({ file_quota: {
      ...availableLimits().file_quota, replacements_allowed: requestCount === 0,
    } }));
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      requestCount += 1;
      requestKey ||= options.headers["Idempotency-Key"];
      assert.equal(options.headers["Idempotency-Key"], requestKey);
      return response(202, { id: "old-doc" });
    }
    return response(200, { documenti: [{ id: "old-doc", nome: "Old.pdf", tipo: "documento",
      stato: "indicizzata", index_verified: true, is_active: true, index_status: "failed",
      index_request_id: requestKey, published_request_id: "old-published", error: "indexing_failed" }] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Old.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();

  const status = h.document.getElementById("doc-carica-status");
  assert.match(status.textContent, /Aggiornamento dell'indice non riuscito/);
  assert.doesNotMatch(status.textContent, /indexing_failed/);
  assert.equal(h.document.querySelector("#doc-library .kb-switch input").checked, true);
  assert.match(h.document.querySelector("#doc-library .kb-document-index-update-notice").textContent,
    /versione precedente resta disponibile/);
  const reindexButton = h.document.querySelector("#doc-library button[data-document-reindex]");
  assert.ok(reindexButton, "a failed replacement with an existing publication keeps the reindex action visible");
  assert.equal(reindexButton.disabled, true, "a fresh replacement follows the updated quota policy");
  const retry = h.document.getElementById("kb-doc-upload-retry");
  assert.equal(retry.hidden, false);
  retry.click();
  await settle();
  assert.equal(requestCount, 2);
  assert.equal(requestKey.length > 0, true);
  assert.equal(h.document.getElementById("doc-file").disabled, false,
    "a failed replacement must not disable text upload or revoke normal quota controls");
});

test("replacement succeeds only after the matching request is published", async (t) => {
  let requestKey;
  let submitted = false;
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      requestKey = options.headers["Idempotency-Key"];
      submitted = true;
      return response(202, { id: "old-doc" });
    }
    return response(200, { documenti: [{ id: "old-doc", nome: "Old.pdf", tipo: "documento",
      stato: "indicizzata", index_verified: true, is_active: true, index_status: submitted ? "idle" : "failed",
      index_request_id: submitted ? requestKey : "old-request",
      published_request_id: submitted ? requestKey : "old-published" }] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Old.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.match(h.document.getElementById("doc-carica-status").textContent, /indicizzato con successo/);
});

test("unknown replacement response stays unconfirmed and retries with the same key", async (t) => {
  let requestCount = 0;
  const requestKeys = [];
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      requestCount += 1;
      requestKeys.push(options.headers["Idempotency-Key"]);
      if (requestCount === 1) throw new Error("connection interrupted");
      return response(202, { id: "old-doc" });
    }
    return response(200, { documenti: [{ id: "old-doc", nome: "Old.pdf", tipo: "documento",
      stato: "indicizzata", index_verified: true, is_active: true, index_status: "failed",
      index_request_id: "old-request", published_request_id: "old-published" }] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Old.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();
  const status = h.document.getElementById("doc-carica-status");
  assert.match(status.textContent, /Richiesta di aggiornamento non confermata/);
  assert.doesNotMatch(status.textContent, /indicizzato con successo/);
  assert.equal(h.document.getElementById("kb-doc-upload-retry").hidden, false);
  h.document.getElementById("kb-doc-upload-retry").click();
  await settle();
  assert.equal(requestCount, 2);
  assert.ok(requestKeys[0]);
  assert.equal(requestKeys[1], requestKeys[0]);
  assert.doesNotMatch(status.textContent, /indicizzato con successo/);
});

test("replacement response from a previous tenant cannot update the current dashboard", async (t) => {
  const delayed = deferred();
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/reindicizza") && options?.method === "POST") return delayed.promise;
    return response(200, { documenti: [{ id: "old-doc", nome: "Old.pdf", tipo: "documento",
      stato: "indicizzata", index_verified: true, is_active: true, index_status: "failed",
      index_request_id: "old-request", published_request_id: "old-published" }] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  h.document.querySelector("#doc-library button.report-refresh").click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Old.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();
  h.context.selectedOrganizationId = "org-2";
  h.module.onEnter();
  delayed.resolve(response(202, { id: "old-doc" }));
  await settle();
  assert.doesNotMatch(h.document.getElementById("doc-carica-status").textContent, /Old\.pdf|indicizzato con successo/);
});

test("failed reindex never claims an inactive publication is available or reactivates it", async (t) => {
  let requestKey = "old-request";
  const h = fixture(async (url, options) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    if (url.endsWith("/limiti")) return response(200, availableLimits());
    if (url.endsWith("/reindicizza") && options?.method === "POST") {
      requestKey = options.headers["Idempotency-Key"];
      return response(202, { id: "inactive-doc" });
    }
    return response(200, { documenti: [{ id: "inactive-doc", nome: "Inactive.pdf", tipo: "documento",
      stato: "indicizzata", index_verified: true, is_active: false, index_status: "failed",
      index_request_id: requestKey, published_request_id: "old-published" }] });
  });
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="documenti"]').click();
  await settle();
  const card = h.document.querySelector("#doc-library .kb-item-card");
  assert.equal(card.querySelector(".kb-switch input").checked, false);
  assert.equal(card.querySelector(".kb-document-index-update-notice"), null,
    "an inactive publication must not be described as currently available");
  const reindex = card.querySelector("button[data-document-reindex]");
  assert.ok(reindex);
  reindex.click();
  const picker = h.document.querySelector("input[data-reindex-file-picker]");
  Object.defineProperty(picker, "files", { configurable: true,
    value: [new h.window.File(["replacement"], "Inactive.pdf", { type: "application/pdf" })] });
  picker.dispatchEvent(new h.window.Event("change"));
  await settle();
  assert.equal(h.document.querySelector("#doc-library .kb-switch input").checked, false,
    "retry must preserve the source activation state");
  assert.equal(h.document.querySelector("#doc-library .kb-document-index-update-notice"), null);
});

test("detached FAQ action cannot mutate a newly selected organization", async (t) => {
  const h = fixture(async (url) => response(200, url.endsWith("/summary") ? emptySummary : { documenti: [faq] }));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  const oldDelete = h.document.querySelector("#kb-faq-list .kb-btn-delete");
  h.context.selectedOrganizationId = "org-2";
  h.module.onEnter();
  oldDelete.click();
  await settle();
  assert.equal(h.calls.some((c) => c.options?.method === "DELETE"), false);
});

test("exit cancels only the Knowledge confirmation and clears its description", async (t) => {
  const h = fixture(async (url) => response(200, url.endsWith("/summary") ? emptySummary : { documenti: [faq] }),
    (options) => h.window.MelpisDashboardShared.confirmDestructive(options));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  h.document.querySelector("#kb-faq-list .kb-btn-delete").click();
  assert.equal(h.document.getElementById("confirm-modal").hidden, false);
  assert.match(h.document.getElementById("confirm-desc").textContent, /Orari/);
  h.module.onExit();
  await settle();
  assert.equal(h.document.getElementById("confirm-modal").hidden, true);
  assert.equal(h.calls.some((c) => c.options?.method === "DELETE"), false);
});

test("Knowledge Escape restores delete focus without mutating, while exit preserves another focus target", async (t) => {
  const h = fixture(async (url) => response(200, url.endsWith("/summary") ? emptySummary : { documenti: [faq] }),
    (options) => h.window.MelpisDashboardShared.confirmDestructive(options));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  const button = h.document.querySelector("#kb-faq-list .kb-btn-delete");
  button.focus();
  button.click();
  const modal = h.document.getElementById("confirm-modal");
  assert.equal(modal.hidden, false);
  modal.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await settle();
  assert.equal(modal.hidden, true);
  assert.equal(h.document.activeElement, button);
  assert.equal(h.calls.some((call) => call.options?.method === "DELETE"), false);

  button.click();
  assert.equal(modal.hidden, false);
  const otherTarget = h.document.getElementById("kb-faq-domanda");
  otherTarget.focus();
  h.module.onExit();
  await settle();
  assert.equal(modal.hidden, true);
  assert.equal(h.document.activeElement, otherTarget, "leaving the view must not steal focus from its new target");
  assert.equal(h.calls.some((call) => call.options?.method === "DELETE"), false);
});

test("auth denial clears private rows and prevents follow-up reads", async (t) => {
  let listCalls = 0;
  const h = fixture(async (url) => {
    if (url.endsWith("/summary")) return response(200, emptySummary);
    return response(++listCalls === 1 ? 200 : 403, { documenti: [faq] });
  });
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  assert.equal(h.document.querySelectorAll("#kb-faq-list .kb-item-card").length, 1);
  await h.module.aggiornaConoscenzaCompleta();
  assert.equal(h.document.querySelectorAll("#kb-faq-list .kb-item-card").length, 0);
  assert.equal(h.calls.filter((c) => c.url.endsWith("/summary")).length, 2);
});

test("two destructive row clicks cannot share one confirmation", async (t) => {
  const pending = deferred();
  let confirmations = 0;
  const h = fixture(async (url) => response(200, url.endsWith("/summary") ? emptySummary
    : { documenti: [faq, { ...faq, id: "faq-2" }] }), () => {
    confirmations += 1;
    return pending.promise;
  });
  t.after(() => h.dom.window.close());
  await h.module.aggiornaConoscenzaCompleta();
  h.document.querySelectorAll("#kb-faq-list .kb-btn-delete").forEach((button) => button.click());
  await settle();
  assert.equal(confirmations, 1);
  pending.resolve(false);
  await settle();
  assert.equal(h.calls.some((call) => call.options?.method === "DELETE"), false);
});

test("repeated clicks on the current tab share one pending list request", async (t) => {
  const pending = deferred();
  const h = fixture(() => pending.promise);
  t.after(() => h.dom.window.close());
  const tab = h.document.querySelector('[data-kb-tab="faq"]');
  tab.click(); tab.click();
  await settle();
  assert.equal(h.calls.length, 1);
  pending.resolve(response(200, { documenti: [faq] }));
  await settle();
  assert.equal(h.document.querySelectorAll("#kb-faq-list .kb-item-card").length, 1);
});

test("web source with script scheme renders without a clickable link", async (t) => {
  const h = fixture(async (url) => response(200, url.endsWith("/summary") ? emptySummary
    : { documenti: [{ id: "web-1", tipo: "web", nome: "Site", fonte: "javascript:alert(1)", is_active: true }] }));
  t.after(() => h.dom.window.close());
  h.document.querySelector('[data-kb-tab="web"]').click();
  await settle();
  assert.equal(h.document.querySelector("#kb-web-list a"), null);
  assert.match(h.document.getElementById("kb-web-list").textContent, /javascript:alert/);
});
