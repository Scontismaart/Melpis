const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "web/dashboard-overview.js"), "utf8");
function response(status, body) {
  return { status, ok: status >= 200 && status < 300, async json() { return body; } };
}
function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  return { promise, resolve };
}
function fixture(apiFetch, onboardingLoader = async () => ({ hasDocs: true, hasBookingHours: true, hasWa: true, isFullyConfigured: true })) {
  const dom = new JSDOM(html, { url: "https://melpis.test/app/overview", runScripts: "outside-only" });
  const { window } = dom;
  const context = { userId: "user-1", sessionOrganizationId: "org-1", selectedOrganizationId: "org-1", view: "panoramica", transition: 1, epoch: 1 };
  const intervals = new Map();
  const timers = new Map();
  const opened = [];
  let id = 0;
  window.setInterval = (callback) => { intervals.set(++id, callback); return id; };
  window.clearInterval = (key) => intervals.delete(key);
  window.setTimeout = (callback) => { timers.set(++id, callback); return id; };
  window.clearTimeout = (key) => timers.delete(key);
  window.eval(source);
  const node = (className, text) => { const element = window.document.createElement("div"); element.className = className; element.textContent = text; return element; };
  const module = window.MelpisDashboardOverview.create({
    API_BASE: "", apiFetch, _sanitize: (value) => String(value ?? ""), toast() {},
    _tDash: (_key, fallback) => fallback, t: (_key, fallback) => fallback,
    localeCorrente: () => "it-IT", _toDateKey: (value) => new Date(value).toISOString().slice(0, 10),
    _emptyState: (_icon, title) => node("empty-state", title), _errorState: (message) => node("error-state", message),
    _skeletonList: () => "<div class='skeleton-list'>loading</div>", ICONS: { chat: "", alert: "" },
    getContext: () => ({ ...context }),
    apriView(view) { module.onExit(); context.view = view; context.transition += 1; },
    apriVistaImpostazioni() {}, apriBookingModal() {},
    getReviewsApi: () => ({ apriDettaglioRecensione: (review) => opened.push(review) }),
    loadOnboarding: onboardingLoader,
  });
  module.onEnter();
  return { dom, document: window.document, context, module, intervals, timers, opened };
}
const event = () => ({ id: "review-1", tipo_evento: "recensione", testo_originale: "Attività QA A", timestamp: new Date().toISOString(), gestito_da_ai: true, dettagli: { stelle: 5 } });

test("Overview renders activity and KPI data through the unchanged endpoints", async (t) => {
  const calls = [];
  const h = fixture(async (url) => { calls.push(url); return response(200, url.endsWith("prioritari") ? [] : [event()]); });
  t.after(() => h.dom.window.close());
  await h.module.aggiornaRiepilogo();
  await h.module.aggiornaPrioritari();
  assert.deepEqual(calls, ["/api/dashboard", "/api/dashboard/prioritari"]);
  assert.equal(h.document.getElementById("stat-totale").textContent, "1");
  assert.match(h.document.getElementById("ticket-list").textContent, /Attività QA A/);
  assert.ok(h.document.querySelector("#sparkline-totale svg"));
});

test("Overview loading becomes an empty state without duplicate nodes", async (t) => {
  const pending = deferred();
  const h = fixture(() => pending.promise);
  t.after(() => h.dom.window.close());
  const loading = h.module.aggiornaRiepilogo();
  assert.ok(h.document.querySelector("#ticket-list .skeleton-list"));
  pending.resolve(response(200, []));
  await loading;
  await h.module.aggiornaRiepilogo();
  assert.equal(h.document.querySelectorAll("#ticket-list .empty-state").length, 1);
  assert.equal(h.document.querySelector("#ticket-list .skeleton-list"), null);
});

test("Overview failed endpoints display their existing error states", async (t) => {
  const h = fixture(async () => response(500, {}));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaRiepilogo();
  await h.module.aggiornaPrioritari();
  assert.equal(h.document.querySelectorAll("#ticket-list .error-state, #priority-list .error-state").length, 2);
});

test("setup checks show unavailable on failed or invalid channel responses instead of claiming completion", async (t) => {
  for (const failingResponse of [
    async (url) => url.endsWith("prioritari") ? response(200, []) : Promise.reject(new Error("offline")),
    async (url) => url.endsWith("prioritari")
      ? response(200, [])
      : ({ ok: true, status: 200, async json() { throw new Error("invalid JSON"); } }),
  ]) {
    const h = fixture(failingResponse, null);
    t.after(() => h.dom.window.close());
    await h.module.aggiornaPrioritari();
    const setupArea = h.document.getElementById("priority-list");
    assert.match(setupArea.textContent, /Impossibile verificare la configurazione/);
    assert.equal(setupArea.querySelector("[data-action='setup-wizard']"), null);
    assert.doesNotMatch(setupArea.textContent, /Completato|pronto a rispondere/);
  }
});

test("Overview starts one poller and removes it on exit", async (t) => {
  const h = fixture(async () => response(200, []));
  t.after(() => h.dom.window.close());
  h.module.avviaPanoramicaPolling();
  h.module.avviaPanoramicaPolling();
  assert.equal(h.intervals.size, 1);
  h.module.onExit();
  assert.equal(h.intervals.size, 0);
  h.module.onEnter();
  h.module.avviaPanoramicaPolling();
  assert.equal(h.intervals.size, 1);
  h.module.invalidate();
  assert.equal(h.intervals.size, 0);
});

test("Overview preserves same-org return state but clears sensitive data before another org loads", async (t) => {
  const h = fixture(async () => response(200, [event()]));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaRiepilogo();
  const before = h.document.getElementById("ticket-list").innerHTML;
  h.module.onExit(); h.context.transition += 1; h.module.onEnter();
  assert.equal(h.document.getElementById("ticket-list").innerHTML, before);
  h.module.onExit(); h.context.selectedOrganizationId = "org-2"; h.module.onEnter();
  assert.equal(h.document.getElementById("ticket-list").textContent, "");
  assert.equal(h.document.getElementById("stat-totale").textContent, "0");
  assert.equal(h.document.querySelector("#sparkline-totale svg"), null);
});

test("Overview logout clears existing data and ignores an in-flight response", async (t) => {
  const pending = deferred();
  let calls = 0;
  const h = fixture(() => ++calls === 1 ? Promise.resolve(response(200, [event()])) : pending.promise);
  t.after(() => h.dom.window.close());
  await h.module.aggiornaRiepilogo();
  const loading = h.module.aggiornaRiepilogo();
  h.module.invalidate(); h.context.userId = null;
  pending.resolve(response(200, [event()]));
  await loading;
  await h.module.aggiornaRiepilogo();
  assert.equal(calls, 2);
  assert.equal(h.document.getElementById("ticket-list").textContent, "");
});

test("Overview review navigation targets the new route and cancels stale-org detail opening", async (t) => {
  const h = fixture(async () => response(200, [event()]));
  t.after(() => h.dom.window.close());
  await h.module.aggiornaRiepilogo();
  h.document.querySelector("#ticket-list .ticket-copy-btn").click();
  assert.equal(h.context.view, "recensioni");
  assert.equal(h.timers.size, 1);
  [...h.timers.values()][0]();
  assert.deepEqual(h.opened, ["review-1"]);
  h.context.view = "panoramica"; h.context.transition += 1; h.module.onEnter();
  await h.module.aggiornaRiepilogo();
  h.document.querySelector("#ticket-list .ticket-copy-btn").click();
  h.context.selectedOrganizationId = "org-2";
  [...h.timers.values()][0]();
  assert.deepEqual(h.opened, ["review-1"]);
});
