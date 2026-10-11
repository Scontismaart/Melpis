const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(path.resolve(__dirname, "../../web/dashboard-ai-settings.js"), "utf8");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function response(body, status = 200, json = async () => body) {
  return { ok: status >= 200 && status < 300, status, json };
}

function createSettings({ apiFetch = async () => response({ profilo: {} }), initialContext = {}, role = "owner", translate = (key) => key } = {}) {
  const dom = new JSDOM(`
    <form id="ai-config-form">
      <input id="ai-cfg-nome" required>
      <select id="ai-cfg-verticale"><option value="ristorante">Ristorante</option></select>
      <textarea id="ai-cfg-descrizione"></textarea><textarea id="ai-cfg-orari"></textarea>
      <select id="ai-cfg-tono-select"><option value="professionale_caloroso">Caldo</option></select>
      <textarea id="ai-cfg-tono-custom"></textarea>
      <input class="ai-cfg-lang-opt" value="en" type="checkbox">
      <select id="ai-cfg-lingua-default"></select>
      <input id="ai-cfg-new-rule-input"><button id="ai-cfg-add-rule-btn" type="button">Add</button>
      <div id="ai-cfg-rules-list"></div>
      <button id="ai-cfg-save-btn" type="submit">Save</button>
      <span id="ai-cfg-save-status"></span>
    </form>
    <span id="business-name"></span><span id="chat-business-name"></span><textarea id="kb-orari-input"></textarea>
  `, { runScripts: "outside-only", url: "https://melpis.test/app/ai-settings" });
  dom.window.console.error = () => {};
  dom.window.eval(source);
  const context = { userId: "user-1", sessionOrganizationId: "org-1", selectedOrganizationId: "org-1", view: "configurazione-ai", transition: 1, epoch: 0, ...initialContext };
  let profile = null;
  const toasts = [];
  const module = dom.window.MelpisDashboardAiSettings.create({
    API_BASE: "", apiFetch, getContext: () => context, getRole: () => role,
    getProfile: () => profile, setProfile: (value) => { profile = value; },
    sanitize: (value) => String(value), escapeHtml: (value) => String(value),
    toast: (...args) => toasts.push(args), translate,
    securityStatus: (element, text) => { element.textContent = text; },
  });
  return { dom, context, module, toasts, get profile() { return profile; } };
}

test("AI settings factory is frozen and entering twice starts one profile load", async () => {
  const pending = deferred();
  let requests = 0;
  const settings = createSettings({ apiFetch: () => { requests += 1; return pending.promise; } });
  assert.equal(Object.isFrozen(settings.dom.window.MelpisDashboardAiSettings), true);
  assert.equal(Object.isFrozen(settings.module), true);
  settings.module.onEnter();
  settings.module.onEnter();
  assert.equal(requests, 1);
  pending.resolve(response({ profilo: { nome_attivita: "Org One" } }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("ai-cfg-nome").value, "Org One");
  assert.equal(settings.profile.nome_attivita, "Org One");
  settings.dom.window.close();
});

test("AI rule removal button uses translated visible and accessible labels", async () => {
  const settings = createSettings({
    apiFetch: async () => response({ profilo: { regole_escalation: ["Ask for human help"] } }),
    translate: (key) => key === "settings:ai_configuration.remove_rule" ? "Rimuovi regola" : key,
  });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const button = settings.dom.window.document.querySelector(".settings-rule-remove-btn");
  assert.ok(button);
  assert.equal(button.getAttribute("aria-label"), "Rimuovi regola");
  assert.equal(button.title, "Rimuovi regola");
  assert.doesNotMatch(button.textContent, /\$\{|settings:ai_configuration\.remove_rule/);
  settings.dom.window.close();
});

test("AI settings preserves a same-organization draft across route exit and return", async () => {
  let requests = 0;
  const settings = createSettings({ apiFetch: async () => { requests += 1; return response({ profilo: { nome_attivita: "Loaded" } }); } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const name = settings.dom.window.document.getElementById("ai-cfg-nome");
  name.value = "Unsaved draft";
  name.dispatchEvent(new settings.dom.window.Event("input", { bubbles: true }));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  settings.module.onEnter();
  Object.assign(settings.context, { view: "configurazione-ai", transition: 3 });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(name.value, "Unsaved draft");
  assert.equal(requests, 1);
  settings.dom.window.close();
});

test("AI settings ignores a stale organization response that resolves after the new scope", async () => {
  const oldJson = deferred();
  let calls = 0;
  const settings = createSettings({ apiFetch: async () => {
    calls += 1;
    if (calls === 1) return response(null, 200, () => oldJson.promise);
    return response({ profilo: { nome_attivita: "Org Two" } });
  } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { selectedOrganizationId: "org-2", view: "configurazione-ai", transition: 2 });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("ai-cfg-nome").value, "Org Two");
  oldJson.resolve({ profilo: { nome_attivita: "Stale Org One" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("ai-cfg-nome").value, "Org Two");
  assert.equal(settings.profile.nome_attivita, "Org Two");
  assert.equal(calls, 2);
  settings.dom.window.close();
});

test("AI settings coalesces double submit and reports failed saves without success feedback", async () => {
  const pendingSave = deferred();
  let posts = 0;
  const settings = createSettings({ apiFetch: async (url, options) => {
    if (options?.method === "POST") { posts += 1; return pendingSave.promise; }
    return response({ profilo: { nome_attivita: "Loaded" } });
  } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const form = settings.dom.window.document.getElementById("ai-config-form");
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(posts, 1);
  pendingSave.resolve(response({ detail: "Synthetic failure" }, 500));
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(settings.dom.window.document.getElementById("ai-cfg-save-status").textContent, /Synthetic failure/);
  assert.equal(settings.toasts.some(([, kind]) => kind === "success"), false);
  settings.dom.window.close();
});

test("AI settings keeps edits made while a successful save is pending", async () => {
  const pendingSave = deferred();
  const settings = createSettings({ apiFetch: async (url, options) => options?.method === "POST"
    ? pendingSave.promise
    : response({ profilo: { nome_attivita: "Loaded" } }) });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const form = settings.dom.window.document.getElementById("ai-config-form");
  const name = settings.dom.window.document.getElementById("ai-cfg-nome");
  name.value = "Submitted value";
  name.dispatchEvent(new settings.dom.window.Event("input", { bubbles: true }));
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  name.value = "Newer unsaved edit";
  name.dispatchEvent(new settings.dom.window.Event("input", { bubbles: true }));
  pendingSave.resolve(response({ profilo: { nome_attivita: "Submitted value" } }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(name.value, "Newer unsaved edit");
  assert.equal(settings.profile.nome_attivita, "Submitted value");
  settings.dom.window.close();
});

test("AI save remains single-flight through delayed JSON after route exit and re-entry", async () => {
  const body = deferred();
  let posts = 0;
  const settings = createSettings({ apiFetch: async (url, options) => {
    if (options?.method === "POST") {
      posts += 1;
      return response(null, 200, () => body.promise);
    }
    return response({ profilo: { nome_attivita: "Loaded" } });
  } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const form = settings.dom.window.document.getElementById("ai-config-form");
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((resolve) => setImmediate(resolve));
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "configurazione-ai", transition: 3 });
  settings.module.onEnter();
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(posts, 1);
  body.resolve({ profilo: { nome_attivita: "Stale saved" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.profile.nome_attivita, "Loaded");
  assert.equal(settings.toasts.some(([, kind]) => kind === "success"), false);
  settings.dom.window.close();
});

test("AI settings restores submit availability after a save settles across route exit", async () => {
  const pendingSave = deferred();
  const settings = createSettings({ apiFetch: async (url, options) => options?.method === "POST"
    ? pendingSave.promise
    : response({ profilo: { nome_attivita: "Loaded" } }) });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const form = settings.dom.window.document.getElementById("ai-config-form");
  const saveButton = settings.dom.window.document.getElementById("ai-cfg-save-btn");
  form.dispatchEvent(new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(saveButton.disabled, true);
  settings.module.onExit();
  Object.assign(settings.context, { view: "panoramica", transition: 2 });
  Object.assign(settings.context, { view: "configurazione-ai", transition: 3 });
  settings.module.onEnter();
  assert.equal(saveButton.disabled, true, "the existing save remains single-flight while pending");
  pendingSave.resolve(response({ profilo: { nome_attivita: "Loaded" } }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(saveButton.disabled, false);
  settings.dom.window.close();
});

test("AI settings invalidation clears the tenant draft and does not retain the shared profile", async () => {
  const settings = createSettings();
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  const name = settings.dom.window.document.getElementById("ai-cfg-nome");
  name.value = "Private Org One";
  settings.module.invalidate();
  assert.equal(name.value, "");
  assert.equal(settings.profile, null);
  settings.dom.window.close();
});

test("AI settings denies staff saves before issuing a profile mutation", async () => {
  let posts = 0;
  const settings = createSettings({ role: "staff", apiFetch: async (url, options) => {
    if (options?.method === "POST") posts += 1;
    return response({ profilo: { nome_attivita: "Loaded" } });
  } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.document.getElementById("ai-config-form").dispatchEvent(
    new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }),
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(posts, 0);
  settings.dom.window.close();
});

test("AI settings denies saves for unknown roles before issuing a profile mutation", async () => {
  let posts = 0;
  const settings = createSettings({ role: "unknown", apiFetch: async (url, options) => {
    if (options?.method === "POST") posts += 1;
    return response({ profilo: { nome_attivita: "Loaded" } });
  } });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.document.getElementById("ai-config-form").dispatchEvent(
    new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }),
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(posts, 0);
  settings.dom.window.close();
});

test("AI settings keeps a rule added during a pending save as a newer draft", async () => {
  const pendingSave = deferred();
  const settings = createSettings({ apiFetch: async (url, options) => options?.method === "POST"
    ? pendingSave.promise
    : response({ profilo: { nome_attivita: "Loaded", regole_escalation: [] } }) });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.document.getElementById("ai-config-form").dispatchEvent(
    new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }),
  );
  const input = settings.dom.window.document.getElementById("ai-cfg-new-rule-input");
  input.value = "Keep this newer rule";
  settings.dom.window.document.getElementById("ai-cfg-add-rule-btn").click();
  pendingSave.resolve(response({ profilo: { nome_attivita: "Loaded", regole_escalation: [] } }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.dom.window.document.getElementById("ai-cfg-rules-list").textContent.includes("Keep this newer rule"), true);
  settings.dom.window.close();
});

test("AI settings discards a pending save after logout invalidates its context", async () => {
  const pendingSave = deferred();
  const settings = createSettings({ apiFetch: async (url, options) => options?.method === "POST"
    ? pendingSave.promise
    : response({ profilo: { nome_attivita: "Loaded" } }) });
  settings.module.onEnter();
  await new Promise((resolve) => setImmediate(resolve));
  settings.dom.window.document.getElementById("ai-config-form").dispatchEvent(
    new settings.dom.window.Event("submit", { bubbles: true, cancelable: true }),
  );
  settings.context.epoch += 1;
  settings.context.userId = null;
  settings.module.invalidate();
  pendingSave.resolve(response({ profilo: { nome_attivita: "Stale" } }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settings.profile, null);
  assert.deepEqual(settings.toasts, []);
  settings.dom.window.close();
});
