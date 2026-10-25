const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");

const root = path.resolve(__dirname, "../..");
const draftKey = (userId, organizationId) => `melpis_onboarding_bozza:${userId}:${organizationId}`;
const legacyOrganizationDraftKey = (organizationId) => `melpis_onboarding_bozza:${organizationId}`;
const profileDraft = (name, step = 1) => JSON.stringify({
  step,
  salvata_at: "2026-10-25T10:00:00.000Z",
  profilo: {
    verticale: "ristorante", nome_attivita: name, orari: "", tono: "", servizi: [],
    regole_escalation: [], lingue_supportate: ["it"], lingua_default: "it",
  },
  extraRules: [],
});

function onboardingOverrides(organizationId, userId = `user-${organizationId}`) {
  return {
    "/api/auth/me": { user_id: userId, organization_id: organizationId, email: `${userId}@example.test`, ruolo: "owner" },
    "/api/onboarding/verticali": {
      verticali: [{ id: "ristorante", label: "Ristorante", tono: "Professionale", servizi: ["Menu"], esempio: "Siete aperti?", escalation: [] }],
      lingue_disponibili: ["it", "en", "fr", "de", "es"],
    },
    "/api/onboarding/profilo": { profilo: null },
    "POST /api/auth/logout": {},
  };
}

async function openWizard(t, organizationId, { storageSeed = {}, userId, overrides = {} } = {}) {
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/onboarding",
    selectedOrganization: organizationId,
    storageSeed,
    overrides: { ...onboardingOverrides(organizationId, userId), ...overrides },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.waitFor(
    () => harness.document.querySelector("#vertical-grid")?.children.length === 1
      || /permessi/i.test(harness.document.getElementById("onboarding-status")?.textContent || ""),
    "onboarding templates did not load",
  );
  return harness;
}

test("the seven-step wizard resolves directly and from the overview guide", async (t) => {
  const h = await openWizard(t, "org-A");
  assert.equal(h.window.location.pathname, "/app/onboarding");
  assert.equal(h.document.querySelector('[data-view-panel="onboarding"]').classList.contains("view-hidden"), false);
  assert.equal(h.document.querySelectorAll("[data-onboarding-page]").length, 7);
  assert.equal(h.document.querySelectorAll("[data-onboarding-step]").length, 7);

  await h.navigate("panoramica");
  await h.waitFor(() => h.document.querySelector('[data-action="setup-wizard"]'), "overview setup guide did not render");
  h.document.querySelector('[data-action="setup-wizard"]').click();
  await h.waitFor(() => h.window.location.pathname === "/app/onboarding", "setup guide did not open the wizard route");
  assert.equal(h.document.querySelector('[data-view-panel="onboarding"]').classList.contains("view-hidden"), false);
});

test("onboarding drafts stay scoped to their organization across A to B to A and reload", async (t) => {
  const a1 = await openWizard(t, "org-A");
  const nameA = a1.document.getElementById("onboarding-name");
  nameA.value = "Org A private draft";
  nameA.dispatchEvent(new a1.window.Event("input", { bubbles: true }));
  await a1.waitFor(() => a1.window.localStorage.getItem(draftKey("user-org-A", "org-A"))?.includes("Org A private draft"));
  const draftA = a1.window.localStorage.getItem(draftKey("user-org-A", "org-A"));

  const b = await openWizard(t, "org-B", {
    storageSeed: {
      [draftKey("user-org-A", "org-A")]: draftA,
      [legacyOrganizationDraftKey("org-A")]: profileDraft("Unknown owner legacy draft"),
      melpis_onboarding_bozza: profileDraft("Unscoped legacy data"),
    },
  });
  assert.equal(b.document.getElementById("onboarding-name").value, "", "Org B must not load Org A or unscoped draft data");
  assert.equal(b.window.localStorage.getItem(draftKey("user-org-A", "org-A")), draftA, "Org B must preserve Org A's own draft");
  assert.equal(b.document.getElementById("onboarding-name").value, "", "organization-only legacy drafts have no account provenance and stay ignored");
  assert.equal(b.window.localStorage.getItem("melpis_onboarding_bozza"), profileDraft("Unscoped legacy data"), "legacy unscoped drafts are ignored, not migrated");

  const nameB = b.document.getElementById("onboarding-name");
  nameB.value = "Org B draft";
  nameB.dispatchEvent(new b.window.Event("input", { bubbles: true }));
  await b.waitFor(() => b.window.localStorage.getItem(draftKey("user-org-B", "org-B"))?.includes("Org B draft"));
  const draftB = b.window.localStorage.getItem(draftKey("user-org-B", "org-B"));

  const a2 = await openWizard(t, "org-A", {
    storageSeed: { [draftKey("user-org-A", "org-A")]: draftA, [draftKey("user-org-B", "org-B")]: draftB },
  });
  assert.equal(a2.document.getElementById("onboarding-name").value, "Org A private draft", "a same-org reload restores its own draft");
  assert.equal(a2.window.localStorage.getItem(draftKey("user-org-B", "org-B")), draftB, "Org A must preserve Org B's own draft");
});

test("a different account in the same organization cannot read the prior account draft", async (t) => {
  const first = await openWizard(t, "shared-org", { userId: "account-A" });
  const input = first.document.getElementById("onboarding-name");
  input.value = "Account A draft";
  input.dispatchEvent(new first.window.Event("input", { bubbles: true }));
  const keyA = draftKey("account-A", "shared-org");
  await first.waitFor(() => first.window.localStorage.getItem(keyA)?.includes("Account A draft"));

  const second = await openWizard(t, "shared-org", {
    userId: "account-B",
    storageSeed: { [keyA]: first.window.localStorage.getItem(keyA) },
  });
  assert.equal(second.document.getElementById("onboarding-name").value, "");
  assert.doesNotMatch(second.window.localStorage.getItem(draftKey("account-B", "shared-org")) || "", /Account A draft/);
});

test("logout clears onboarding form state but preserves organization-scoped drafts", async (t) => {
  const h = await openWizard(t, "org-A");
  const input = h.document.getElementById("onboarding-name");
  input.value = "Org A draft";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  const key = draftKey("user-org-A", "org-A");
  await h.waitFor(() => h.window.localStorage.getItem(key)?.includes("Org A draft"));
  const saved = h.window.localStorage.getItem(key);

  h.document.getElementById("sidebar-logout-btn").click();
  await h.waitFor(() => h.window.localStorage.getItem("melpis_auth_logout") !== null, "logout did not complete");
  assert.equal(input.value, "", "private form data is cleared from memory on logout");
  assert.equal(h.window.localStorage.getItem(key), saved, "logout preserves the draft for that account and organization");
});

test("a pending onboarding response is ignored after logout and login to the same account", async (t) => {
  let resolveFirst;
  let verticalCalls = 0;
  const h = createDashboardHarness({
    url: "https://melpis.test/app/panoramica",
    selectedOrganization: "org-A",
    overrides: {
      ...onboardingOverrides("org-A"),
      "/api/onboarding/verticali": (_request, jsonResponse) => {
        verticalCalls += 1;
        if (verticalCalls === 1) return new Promise((resolve) => { resolveFirst = resolve; });
        return jsonResponse({ verticali: [{ id: "ristorante", label: "Fresh", tono: "Fresh tone", servizi: ["Fresh service"], esempio: "Fresh example", escalation: [] }], lingue_disponibili: ["it"] });
      },
    },
  });
  t.after(h.dispose);
  await h.ready();
  h.window.eval("inizializzaOnboarding()");
  await h.waitFor(() => verticalCalls === 1, "first onboarding request did not start");
  h.window.eval("invalidaSessione()");
  await h.window.eval("caricaSessione()");
  h.window.eval("inizializzaOnboarding()");
  await h.settle();
  assert.equal(verticalCalls, 2, `new-session request missing; errors=${JSON.stringify(h.errors)}; calls=${h.requests.filter((request) => request.pathname === "/api/onboarding/verticali").length}`);
  await h.waitFor(() => h.document.getElementById("onboarding-tone").value === "Fresh tone", "new session did not initialize cleanly");
  resolveFirst({ ok: true, status: 200, async json() { return { verticali: [{ id: "ristorante", label: "Stale", tono: "Stale tone", servizi: ["Stale service"], esempio: "Stale example", escalation: [] }], lingue_disponibili: ["it"] }; } });
  await h.settle();
  assert.equal(h.document.getElementById("onboarding-name").value, "");
  assert.equal(h.document.getElementById("onboarding-tone").value, "Fresh tone");
});

test("malformed successful upload responses never mark documents as imported", async (t) => {
  for (const body of [
    { id: "doc-1", nome: "menu.pdf", indicizzati: 0 },
    { id: "doc-1", nome: "menu.pdf", indicizzati: 1.5 },
    { id: "doc-1", nome: "menu.pdf", indicizzati: "2" },
    { id: "doc-1", nome: "menu.pdf" },
    { nome: "menu.pdf", indicizzati: 2 },
    { id: "doc-1", indicizzati: 2 },
  ]) {
    const h = await openWizard(t, "org-A", { overrides: {
      ...onboardingOverrides("org-A"),
      "POST /api/documenti/carica-file": body,
    } });
    const fileInput = h.document.getElementById("onboarding-doc-file");
    Object.defineProperty(fileInput, "files", { configurable: true, value: [{ name: "menu.pdf" }] });
    h.document.getElementById("onboarding-doc-upload").click();
    await h.waitFor(() => h.document.getElementById("onboarding-doc-status").textContent !== "Caricamento e indicizzazione...");
    assert.equal(h.document.getElementById("onboarding-docs").checked, false);
    assert.equal(h.document.getElementById("onboarding-doc-status").style.color, "var(--red)");
  }
});

test("a forbidden profile response is visible and does not fall back to a local draft", async (t) => {
  const key = draftKey("user-org-A", "org-A");
  const h = await openWizard(t, "org-A", {
    storageSeed: { [key]: profileDraft("Must not mask a forbidden response") },
    overrides: {
      ...onboardingOverrides("org-A"),
      "/api/onboarding/profilo": { __response: { status: 403 }, body: { detail: "forbidden" } },
    },
  });
  assert.equal(h.document.getElementById("onboarding-name").value, "");
  assert.match(h.document.getElementById("onboarding-status").textContent, /permessi/i);
});

test("wizard copy updates in Italian and English and never claims provider connectivity", async (t) => {
  const markup = fs.readFileSync(path.join(root, "web/dashboard-src/partials/pane-onboarding.html"), "utf8");
  const dom = new JSDOM(`<!doctype html><html><body>${markup}</body></html>`, {
    url: "https://melpis.test/app/onboarding?lang=it",
    runScripts: "outside-only",
  });
  t.after(() => dom.window.close());
  dom.window.fetch = async (input) => {
    const match = String(input).match(/\/locales\/([^/]+)\/([^/?]+)\.json/);
    if (!match) return { ok: true, async json() { return {}; } };
    const file = path.join(root, "web/locales", match[1], `${match[2]}.json`);
    return { ok: true, async json() { return JSON.parse(fs.readFileSync(file, "utf8")); } };
  };
  dom.window.eval(fs.readFileSync(path.join(root, "web/i18n-client.js"), "utf8"));
  await dom.window.MelpisI18n.init({ namespaces: ["dashboard"] });
  const document = dom.window.document;
  assert.equal(document.querySelector('[data-onboarding-step="0"] span:nth-child(2)').textContent, "Attività");
  assert.equal(document.getElementById("onboarding-name").placeholder, "Es. Studio Capelli Nora");
  assert.match(document.querySelector('[data-onboarding-page="4"]').textContent, /Non verificato qui/);
  assert.doesNotMatch(document.querySelector('[data-onboarding-page="4"]').textContent, /Webhook\s*Pronto/);

  await dom.window.MelpisI18n.setLanguage("en");
  assert.equal(document.querySelector('[data-onboarding-step="0"] span:nth-child(2)').textContent, "Business");
  assert.equal(document.getElementById("onboarding-name").placeholder, "e.g. Nora's Hair Studio");
  assert.match(document.querySelector('[data-onboarding-page="4"]').textContent, /Not checked here/);
  assert.match(document.querySelector('[data-onboarding-page="4"]').textContent, /Connect a number and check its status/);
});
