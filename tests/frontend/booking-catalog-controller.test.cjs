const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const source = fs.readFileSync(path.join(__dirname, "../../web/dashboard-booking-catalog.js"), "utf8");
const fixture = require("../core/bookings/fixtures/catalogs_v2.json").ristorante;
const copy = (x) => JSON.parse(JSON.stringify(x));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const state = (extra = {}) => ({ revision: 1, draft: copy(fixture), published: null, published_revision: null, runtime_enabled: false, organization_sector: "ristorante", capabilities: { can_save: true, can_publish: true }, ...extra });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function setup(fetch, config = {}) {
  const dom = new JSDOM("<main></main>", { runScripts: "outside-only", url: "https://example.test" });
  let context = { view: "prenotazioni", userId: "u1", sessionOrganizationId: "o1", selectedOrganizationId: null }, session = { ruolo: "owner", id: "session-1" }, model = null;
  const calls = [];
  dom.window.MelpisBookingCatalogForm = { create(container, opts) { return { load(c) { model = c && copy(c); container.textContent = model ? model.services[0].name : "empty"; }, read(revision) { if (!model) throw Error("empty"); return { ...copy(model), revision }; }, clear() { model = null; container.replaceChildren(); }, setEditable(v) { container.dataset.editable = String(v); }, opts }; } };
  dom.window.eval(source);
  const container = dom.window.document.querySelector("main");
  const controller = dom.window.MelpisBookingCatalog.create({ container, API_BASE: "", apiFetch: (url, options) => { calls.push({ url, options }); return fetch(url, options, calls.length); }, getContext: () => context, getSession: () => session, confirm: config.confirm || (async () => true), onOpenSecurity: config.onOpenSecurity || (() => {}) });
  return { dom, container, controller, calls, setContext: (value) => { context = { ...context, ...value }; }, setSession: (value) => { session = { ...session, ...value }; }, changeModel: (name) => { model.services[0].name = name; } };
}
function button(h, text) { return [...h.container.querySelectorAll("button")].find((n) => n.textContent === text); }

test("manager is read-only and staff cannot fetch or see controls", async () => {
  const h = setup(async () => response(state({ capabilities: { can_save: true, can_publish: true } })));
  h.setSession({ ruolo: "manager" }); h.controller.enter(); await tick();
  assert.equal(button(h, "Salva bozza").hidden, true); assert.equal(button(h, "Pubblica").hidden, true);
  assert.equal(h.container.querySelector('[data-editable]').dataset.editable, "false");
  h.setSession({ ruolo: "staff" }); h.controller.enter(); await tick();
  assert.equal(h.calls.length, 1); assert.equal(h.container.textContent, ""); h.dom.window.close();
});

test("stale GET is fenced for organization, role and session changes", async () => {
  for (const mutate of [(h) => h.setContext({ sessionOrganizationId: "o2" }), (h) => h.setSession({ ruolo: "manager" }), (h) => h.setSession({ id: "session-2" })]) {
    const wait = deferred(), h = setup(() => wait.promise); h.controller.enter(); mutate(h); wait.resolve(response(state())); await tick();
    assert.equal(h.container.textContent, ""); h.dom.window.close();
  }
});

test("late save after tenant change cannot restore previous data", async () => {
  const wait = deferred(), h = setup(async (url) => url.endsWith("/draft") ? wait.promise : response(state()));
  h.controller.enter(); await tick(); button(h, "Salva bozza").click(); await tick();
  h.setContext({ sessionOrganizationId: "o2" }); h.controller.invalidate(); wait.resolve(response({ ...state(), applied_revision: 2 })); await tick();
  assert.equal(h.container.textContent, ""); assert.equal(h.calls.length, 2); h.dom.window.close();
});

test("uncertain retry retains UUID for identical body, changed payload gets a new UUID", async () => {
  const h = setup(async (url) => { if (url.endsWith("/draft")) throw Error("offline"); return response(state()); });
  h.controller.enter(); await tick(); button(h, "Salva bozza").click(); await tick(); button(h, "Salva bozza").click(); await tick();
  const first = JSON.parse(h.calls[1].options.body), second = JSON.parse(h.calls[2].options.body);
  assert.equal(first.request_id, second.request_id); assert.equal(h.calls[1].options.body, h.calls[2].options.body);
  h.changeModel("changed"); button(h, "Salva bozza").click(); await tick();
  assert.notEqual(JSON.parse(h.calls[3].options.body).request_id, first.request_id); h.dom.window.close();
});

test("409 requires reload and preserves unsaved changes for explicit restore", async () => {
  const h = setup(async (url) => url.endsWith("/draft") ? response({}, 409) : response(state()));
  h.controller.enter(); await tick(); h.changeModel("Unsaved"); button(h, "Salva bozza").click(); await tick();
  assert.equal(button(h, "Salva bozza").hidden, true); assert.match(h.container.textContent, /cambiato/);
  button(h, "Ricarica catalogo").click(); await tick(); assert.equal(button(h, "Ripristina modifiche non salvate").hidden, false);
  button(h, "Ripristina modifiche non salvate").click(); assert.match(h.container.textContent, /Unsaved/); h.dom.window.close();
});

test("publish requires capability, confirmation and unchanged session; 403 is safe", async () => {
  const confirmation = deferred(); const h = setup(async (url) => url.endsWith("/publish") ? response({}, 403) : response(state()), { confirm: () => confirmation.promise });
  h.controller.enter(); await tick(); button(h, "Pubblica").click(); h.setSession({ id: "new" }); confirmation.resolve(true); await tick();
  assert.equal(h.calls.length, 1); assert.equal(h.container.textContent, ""); h.dom.window.close();
  const h2 = setup(async (url) => url.endsWith("/publish") ? response({}, 403) : response(state()));
  h2.controller.enter(); await tick(); button(h2, "Pubblica").click(); await tick();
  assert.match(h2.container.textContent, /due fattori/); assert.equal(button(h2, "Apri impostazioni di sicurezza").hidden, false); assert.equal(button(h2, "Pubblica").hidden, true); h2.dom.window.close();
});

test("503 is visible, successful write reloads and applied revision is not faked", async () => {
  let count = 0; const h = setup(async (url) => { if (url.endsWith("/draft")) { count++; return count === 1 ? response({}, 503) : response({ ...state({ revision: 2 }), applied_revision: 2 }); } return response(state({ revision: count > 1 ? 2 : 1 })); });
  h.controller.enter(); await tick(); button(h, "Salva bozza").click(); await tick(); assert.match(h.container.textContent, /temporaneamente/); assert.match(h.container.textContent, /corrente: 1/);
  button(h, "Salva bozza").click(); await tick(); assert.match(h.container.textContent, /corrente: 2/); assert.equal(h.calls.length, 4); h.dom.window.close();
});

test("duplicate clicks during a pending save issue one request and invalidate aborts it", async () => {
  const pending = deferred(); const h = setup(async (url) => url.endsWith("/draft") ? pending.promise : response(state()));
  h.controller.enter(); await tick(); button(h, "Salva bozza").click(); button(h, "Salva bozza").click(); await tick();
  assert.equal(h.calls.length, 2); const signal = h.calls[1].options.signal; h.controller.invalidate(); assert.equal(signal.aborted, true);
  pending.resolve(response({ ...state(), applied_revision: 2 })); await tick(); assert.equal(h.container.textContent, ""); h.dom.window.close();
});

test("owner without publish capability cannot publish", async () => {
  const h = setup(async () => response(state({ capabilities: { can_save: true, can_publish: false } })));
  h.controller.enter(); await tick(); assert.equal(button(h, "Pubblica").hidden, true); button(h, "Pubblica").click(); await tick(); assert.equal(h.calls.length, 1); h.dom.window.close();
});


test("an old response cannot invalidate a newer scope", async () => {
  const old = deferred(); let calls = 0;
  const h = setup(async () => ++calls === 1 ? old.promise : response(state()));
  h.controller.enter(); h.setContext({ sessionOrganizationId: "o2" }); h.controller.enter(); await tick();
  assert.match(h.container.textContent, /Catalogo caricato/);
  old.resolve(response(state())); await tick();
  assert.match(h.container.textContent, /Catalogo caricato/); assert.ok(button(h, "Salva bozza")); h.dom.window.close();
});

test("MFA hint offers a scope-fenced security action", async () => {
  let opened = 0; const h = setup(async () => response(state({ capabilities: { can_save: true, can_publish: false } })), { onOpenSecurity: () => { opened++; } });
  h.controller.enter(); await tick(); assert.match(h.container.textContent, /due fattori/);
  button(h, "Apri impostazioni di sicurezza").click(); assert.equal(opened, 1);
  h.setSession({ id: "new" }); button(h, "Apri impostazioni di sicurezza").click(); assert.equal(opened, 1); assert.equal(h.container.textContent, ""); h.dom.window.close();
});

test("successful write with failed refresh disables edits until reload", async () => {
  let reads = 0; const h = setup(async (url) => {
    if (url.endsWith("/draft")) return response({ ...state({ revision: 2 }), applied_revision: 2 });
    reads++; if (reads === 2) throw Error("offline"); return response(state({ revision: reads > 1 ? 2 : 1 }));
  });
  h.controller.enter(); await tick(); button(h, "Salva bozza").click(); await tick();
  assert.match(h.container.textContent, /Scrittura riuscita/); assert.equal(button(h, "Salva bozza").hidden, true);
  button(h, "Ricarica catalogo").click(); await tick(); assert.equal(button(h, "Salva bozza").hidden, false); assert.match(h.container.textContent, /corrente: 2/); h.dom.window.close();
});

test("unknown organization sector cannot edit or publish", async () => {
  const h = setup(async () => response(state({ organization_sector: null })));
  h.controller.enter(); await tick(); assert.equal(button(h, "Salva bozza").hidden, true); assert.equal(button(h, "Pubblica").hidden, true); assert.match(h.container.textContent, /Settore non disponibile/); h.dom.window.close();
});

test("audit displays only validated safe metadata", async () => {
  const h = setup(async (url) => url.endsWith("/audit") ? response({ events: [{ action: "draft_saved", revision: 2, created_at: "2027-01-01T10:00:00Z", request_id: "12345678-1234-1234-1234-123456789012", body: "secret" }, { action: "<img>", revision: 1, created_at: "bad", request_id: "bad" }] }) : response(state()));
  h.controller.enter(); await tick(); button(h, "Mostra cronologia").click(); await tick();
  assert.match(h.container.textContent, /draft_saved/); assert.doesNotMatch(h.container.textContent, /secret|<img>/); assert.equal(h.container.querySelector("img"), null); h.dom.window.close();
});
