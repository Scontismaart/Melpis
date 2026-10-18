const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(path.resolve(__dirname, "../../web/dashboard-settings.js"), "utf8");

function createSettings(role = "owner", apiFetch = async () => ({ ok: true, status: 200, json: async () => ({ timezone: "UTC", timezone_disponibili: ["UTC"] }) }), redirects = []) {
  const dom = new JSDOM(`
    <h1 id="topbar-title"></h1>
    <input id="settings-search-input">
    <p id="settings-search-empty" hidden></p>
    <div class="settings-nav-group">
      <button data-settings-cat-btn="generale">Generale</button>
      <button data-settings-cat-btn="piano" data-keywords="billing">Piano</button>
      <button data-settings-cat-btn="fatturazione">Fatturazione</button>
    </div>
    <section data-settings-panel="generale"></section>
    <section data-settings-panel="piano"></section>
    <section data-settings-panel="fatturazione"></section>
    <select id="settings-timezone"></select><button id="settings-timezone-save"></button>
    <p id="settings-timezone-status"></p><p id="account-status" hidden></p>
    <span id="account-stato"></span><span id="account-plan-nome"></span><span id="account-plan-prezzo"></span>
    <span id="sidebar-account-plan"></span><p id="account-rinnovo"></p><div id="account-plans"></div>
    <button id="account-portal-btn"></button><button id="account-cancel-btn"></button>
    <div id="audit-list"></div><span id="audit-count"></span>
  `, { runScripts: "outside-only", url: "https://melpis.test/app/settings" });
  dom.window.eval(source);
  const context = { userId: "u1", sessionOrganizationId: "o1", selectedOrganizationId: "o1", view: "impostazioni", transition: 1, epoch: 0 };
  const module = dom.window.MelpisDashboardSettings.create({
    API_BASE: "", apiFetch,
    getContext: () => context, getRole: () => role, t: (key) => key,
    _tDash: (key, fallback) => key === "sidebar.menu_piano" ? fallback : key,
    sanitize: String,
    securityStatus: (el, text) => { el.textContent = text; }, localeCorrente: () => "it-IT",
    navigateTo: () => true, normalizeSettingsTab: (tab) => tab, settingsSearchForTab: () => "",
    closeAccountMenu() {}, loadMfaStatus() {}, getIntegrationsApi: () => null,
    refreshSidebarIdentity() { dom.window.document.getElementById("sidebar-account-plan").textContent = `Ruolo: ${role === "staff" ? "Staff" : "Proprietario"}`; },
    redirect: (url) => redirects.push(url),
  });
  return { dom, context, module };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const response = (body, status = 200, json = async () => body) => ({ ok: status >= 200 && status < 300, status, json });

test("settings factory freezes its API and only activates allowlisted panels", () => {
  const settings = createSettings();
  assert.equal(Object.isFrozen(settings.dom.window.MelpisDashboardSettings), true);
  assert.equal(Object.isFrozen(settings.module), true);
  settings.module.onEnter("generale");
  assert.equal(settings.dom.window.document.querySelector('[data-settings-panel="generale"]').hidden, false);
  assert.equal(settings.dom.window.document.querySelector('[data-settings-panel="piano"]').hidden, true);
});

test("staff search cannot reveal billing categories", () => {
  const settings = createSettings("staff");
  settings.module.onEnter("generale");
  const input = settings.dom.window.document.getElementById("settings-search-input");
  input.value = "billing";
  input.dispatchEvent(new settings.dom.window.Event("input", { bubbles: true }));
  assert.equal(settings.dom.window.document.querySelector('[data-settings-cat-btn="piano"]').style.display, "none");
  assert.equal(settings.dom.window.document.querySelector('[data-settings-cat-btn="fatturazione"]').style.display, "none");
});

test("leaving and invalidating settings prevents stale route callbacks from becoming current", () => {
  const settings = createSettings();
  settings.module.onEnter("generale");
  settings.module.onExit();
  settings.context.view = "panoramica";
  settings.module.onEnter("piano");
  assert.equal(settings.dom.window.document.querySelector('[data-settings-panel="piano"]').hidden, true);
  settings.module.invalidate();
  assert.equal(settings.dom.window.document.getElementById("settings-timezone-save").disabled, true);
});

test("timezone loads allowlisted options and coalesces duplicate saves", async () => {
  const pending = deferred();
  let saves = 0;
  const settings = createSettings("owner", async (url, options) => {
    if (options?.method === "PUT") { saves += 1; return pending.promise; }
    return response({ timezone: "Europe/Rome", timezone_disponibili: ["UTC", "Europe/Rome"] });
  });
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));
  const select = settings.dom.window.document.getElementById("settings-timezone");
  assert.deepEqual([...select.options].map((option) => option.value), ["UTC", "Europe/Rome"]);
  assert.equal(select.value, "Europe/Rome");
  const button = settings.dom.window.document.getElementById("settings-timezone-save");
  button.click();
  button.click();
  assert.equal(saves, 1);
  pending.resolve(response({}));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(button.disabled, false);
  settings.dom.window.close();
});

test("timezone save remains single-flight through a delayed response body after route re-entry", async () => {
  const body = deferred();
  let saves = 0;
  const settings = createSettings("owner", async (url, options) => {
    if (options?.method === "PUT") {
      saves += 1;
      return response(null, 200, () => body.promise);
    }
    return response({ timezone: "UTC", timezone_disponibili: ["UTC"] });
  });
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));
  const button = settings.dom.window.document.getElementById("settings-timezone-save");
  button.dispatchEvent(new settings.dom.window.Event("click", { bubbles: true }));
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "impostazioni", transition: 3 });
  settings.module.onEnter("generale");
  button.dispatchEvent(new settings.dom.window.Event("click", { bubbles: true }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(saves, 1);
  body.resolve({});
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.close();
});

test("timezone re-entry does not reuse a stale pending load and keeps the new error visible", async () => {
  const firstLoad = deferred();
  let loads = 0;
  const settings = createSettings("owner", async () => {
    loads += 1;
    return loads === 1 ? firstLoad.promise : response({}, 500);
  });
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "impostazioni", transition: 3 });
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));

  const status = settings.dom.window.document.getElementById("settings-timezone-status");
  assert.equal(loads, 2);
  assert.equal(status.textContent, "Impossibile caricare il fuso orario. Riprova.");
  firstLoad.resolve(response({ timezone: "Europe/Rome", timezone_disponibili: ["Europe/Rome"] }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(status.textContent, "Impossibile caricare il fuso orario. Riprova.");
  assert.equal(settings.dom.window.document.getElementById("settings-timezone").options.length, 0);
  settings.dom.window.close();
});

test("timezone load/save failures report failure and never claim success", async () => {
  const settings = createSettings("owner", async (url, options) => options?.method === "PUT"
    ? response({}, 503)
    : response({}, 503));
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));
  const status = settings.dom.window.document.getElementById("settings-timezone-status");
  assert.equal(status.textContent, "Impossibile caricare il fuso orario. Riprova.");
  assert.doesNotMatch(status.textContent, /^settings:/);
  settings.dom.window.close();
});

test("timezone timeout failure restores controls without claiming a save", async () => {
  const settings = createSettings("owner", async (url, options) => {
    if (options?.method === "PUT") throw new Error("network timeout");
    return response({ timezone: "UTC", timezone_disponibili: ["UTC"] });
  });
  settings.module.onEnter("generale");
  await new Promise((resolve) => setImmediate(resolve));
  const button = settings.dom.window.document.getElementById("settings-timezone-save");
  button.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(button.disabled, false);
  assert.equal(settings.dom.window.document.getElementById("settings-timezone-status").textContent, "Errore di connessione.");
  settings.dom.window.close();
});

test("billing redirects only after a current successful response; staff and 403 are denied", async () => {
  let createCalls = 0;
  const redirects = [];
  const settings = createSettings("owner", async (url, options) => {
    if (url.endsWith("/api/billing/subscription")) return response({ plan: "starter", subscription_status: "active" });
    if (url.endsWith("/api/billing/create-checkout-session")) {
      createCalls += 1;
      return response({ url: "https://billing.example/checkout" });
    }
    return response({}, 403);
  }, redirects);
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  const planButton = [...settings.dom.window.document.querySelectorAll("#account-plans button")]
    .find((button) => button.textContent.includes("Crescita"));
  planButton.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(createCalls, 1);
  assert.deepEqual(redirects, ["https://billing.example/checkout"]);
  settings.dom.window.close();

  const staff = createSettings("staff", async () => response({ plan: "starter", subscription_status: "active" }));
  staff.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(staff.dom.window.document.querySelectorAll("#account-plans button").length, 0);
  staff.dom.window.close();
});

test("billing keeps an active application trial card-free when Stripe checkout is deferred", async () => {
  const redirects = [];
  let checkoutCalls = 0;
  const settings = createSettings("owner", async (url) => {
    if (url.endsWith("/api/billing/subscription")) {
      return response({ plan: "starter", subscription_status: "trialing", trial_end: "2026-10-08T12:00:00Z" });
    }
    if (url.endsWith("/api/billing/create-checkout-session")) {
      checkoutCalls += 1;
      return response({ detail: {
        code: "trial_active_checkout_deferred",
        trial_end: "2026-10-08T12:00:00Z",
      } }, 409);
    }
    return response({});
  }, redirects);
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  const planButton = [...settings.dom.window.document.querySelectorAll("#account-plans button")]
    .find((button) => button.textContent.includes("Crescita"));
  planButton.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(checkoutCalls, 1);
  assert.deepEqual(redirects, []);
  assert.match(settings.dom.window.document.getElementById("account-status").textContent, /Non è richiesta una carta/);
  settings.dom.window.close();
});

test("billing portal 403 and stale checkout JSON never redirect", async () => {
  const staleBody = deferred();
  const redirects = [];
  let checkoutCalls = 0;
  const settings = createSettings("owner", async (url) => {
    if (url.endsWith("/api/billing/subscription")) return response({ plan: "starter", subscription_status: "active" });
    if (url.endsWith("/api/billing/create-portal-session")) return response({}, 403);
    if (url.endsWith("/api/billing/create-checkout-session")) { checkoutCalls += 1; return response(null, 200, () => staleBody.promise); }
    return response(null, 200, () => staleBody.promise);
  }, redirects);
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.document.getElementById("account-portal-btn").click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(redirects, []);
  const pro = [...settings.dom.window.document.querySelectorAll("#account-plans button")]
    .find((button) => button.textContent.includes("Crescita"));
  pro.click();
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "impostazioni", transition: 3 });
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  const secondPro = [...settings.dom.window.document.querySelectorAll("#account-plans button")]
    .find((button) => button.textContent.includes("Crescita"));
  secondPro.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(checkoutCalls, 1);
  settings.module.onExit();
  settings.context.view = "panoramica";
  staleBody.resolve({ url: "https://billing.example/stale" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(redirects, []);
  settings.dom.window.close();
});

test("unknown billing role fails closed without loading or mutating billing", async () => {
  const calls = [];
  const unknown = createSettings("unknown", async (url) => {
    calls.push(url);
    return response({ plan: "starter", subscription_status: "active" });
  });
  unknown.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
  unknown.dom.window.document.getElementById("account-portal-btn").click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
  unknown.dom.window.close();
});

test("billing portal remains single-flight through delayed JSON after route re-entry", async () => {
  const body = deferred();
  const redirects = [];
  let portalCalls = 0;
  const settings = createSettings("owner", async (url) => {
    if (url.endsWith("/api/billing/subscription")) return response({ plan: "starter", subscription_status: "active" });
    if (url.endsWith("/api/billing/create-portal-session")) {
      portalCalls += 1;
      return response(null, 200, () => body.promise);
    }
    return response({});
  }, redirects);
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  const portal = settings.dom.window.document.getElementById("account-portal-btn");
  portal.click();
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "impostazioni", transition: 3 });
  settings.module.onEnter("piano");
  portal.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(portalCalls, 1);
  body.resolve({ url: "https://billing.example/stale-portal" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(redirects, []);
  settings.dom.window.close();
});

test("billing display is cleared on organization invalidation before the next load fails", async () => {
  let calls = 0;
  const settings = createSettings("owner", async () => {
    calls += 1;
    return calls === 1
      ? response({ plan: "pro", subscription_status: "active", current_period_end: "2027-01-01T00:00:00Z" })
      : response({ detail: "denied" }, 403);
  });
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("account-plan-nome").textContent, "Crescita");
  settings.module.onExit();
  settings.context.selectedOrganizationId = "org-2";
  settings.module.onEnter("piano");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("account-plan-nome").textContent, "…");
  assert.equal(settings.dom.window.document.getElementById("account-plan-prezzo").textContent, "");
  assert.equal(settings.dom.window.document.getElementById("sidebar-account-plan").textContent, "Ruolo: Proprietario");
  assert.equal(settings.dom.window.document.getElementById("account-plans").children.length, 0);
  settings.dom.window.close();
});

test("settings clears sidebar identity to the translated fallback only after session invalidation", () => {
  const settings = createSettings();
  settings.context.userId = null;
  settings.module.invalidate();
  assert.equal(settings.dom.window.document.getElementById("sidebar-account-plan").textContent, "Piano e abbonamento");
  assert.doesNotMatch(settings.dom.window.document.body.textContent, /dashboard:sidebar\.menu_piano/);
  settings.dom.window.close();
});

test("audit pagination appends only current-scope results", async () => {
  const pending = deferred();
  let calls = 0;
  const settings = createSettings("owner", async () => {
    calls += 1;
    if (calls === 1) return response({ eventi: [{ action: "profile", created_at: "2026-01-01", details: {} }], has_more: true });
    return response(null, 200, () => pending.promise);
  });
  settings.module.onEnter("audit");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.querySelectorAll("#audit-list .audit-item").length, 1);
  settings.dom.window.document.getElementById("audit-load-more").click();
  await new Promise((resolve) => setImmediate(resolve));
  settings.context.selectedOrganizationId = "org-2";
  settings.module.onExit();
  pending.resolve({ eventi: [{ action: "stale", created_at: "2026-01-02", details: {} }], has_more: false });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.querySelectorAll("#audit-list .audit-item").length, 1);
  settings.dom.window.close();
});
