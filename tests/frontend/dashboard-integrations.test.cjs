const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");

function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  return { promise, resolve };
}

test("integration status does not commit JSON resolved after route exit", async (t) => {
  const body = deferred();
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=whatsapp",
    overrides: {
      "/api/whatsapp/settings": (_request, jsonResponse) => ({
        status: 200, ok: true, async json() { return body.promise; },
        async text() { return "{}"; }, clone() { return jsonResponse({}); },
      }),
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  await new Promise((resolve) => setImmediate(resolve));
  const heading = harness.document.getElementById("integ-wa-connected-title");
  const original = heading?.textContent;
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  body.resolve({ connesso: true, verified_name: "Stale Org", display_phone_number: "+100" });
  await harness.settle();
  assert.equal(heading?.textContent, original);
});

test("integration destructive confirmation is canceled when organization changes before confirmation", async (t) => {
  const harness = createDashboardHarness({ url: "https://melpis.test/app/settings?tab=whatsapp" });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  harness.document.getElementById("integ-wa-disconnect-btn").click();
  harness.window.localStorage.setItem("melpis_selected_organization", "org-2");
  harness.document.getElementById("confirm-ok-btn").click();
  await harness.settle();
  assert.equal(harness.requests.some((request) => request.pathname === "/api/whatsapp/disconnect"), false);
});

test("integration destructive confirmation is canceled when logout invalidates the origin session", async (t) => {
  const harness = createDashboardHarness({ url: "https://melpis.test/app/settings?tab=whatsapp" });
  t.after(harness.dispose);
  await harness.ready();
  harness.document.getElementById("integ-wa-disconnect-btn").click();
  harness.window.dispatchEvent(new harness.window.StorageEvent("storage", { key: "melpis_auth_logout", newValue: "logout" }));
  harness.document.getElementById("confirm-ok-btn").click();
  await harness.settle();
  assert.equal(harness.requests.some((request) => request.pathname === "/api/whatsapp/disconnect"), false);
});

test("provider drafts survive same-session route exit but secrets clear on organization invalidation", async (t) => {
  const harness = createDashboardHarness({ url: "https://melpis.test/app/settings?tab=whatsapp" });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  const token = harness.document.getElementById("wa-access-token");
  const testPhone = harness.document.getElementById("wa-wz-test-phone");
  token.value = "draft-token";
  testPhone.value = "+393331234567";
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  assert.equal(token.value, "draft-token");
  harness.window.localStorage.setItem("melpis_selected_organization", "org-2");
  harness.window.history.pushState(null, "", "/app/settings?tab=whatsapp");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  assert.equal(token.value, "");
  assert.equal(testPhone.value, "");
});

test("WhatsApp connect is single-flight and ignores a response after the original route exits", async (t) => {
  const response = deferred();
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=whatsapp",
    overrides: {
      "POST /api/whatsapp/connect": () => ({
        status: 200, ok: true, async json() { return response.promise; },
        async text() { return "{}"; }, clone() { return this; },
      }),
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  const form = harness.document.getElementById("wa-connect-form");
  harness.document.getElementById("wa-phone-number-id").value = "phone-id";
  harness.document.getElementById("wa-waba-id").value = "waba-id";
  harness.document.getElementById("wa-access-token").value = "secret-token";
  form.dispatchEvent(new harness.window.Event("submit", { bubbles: true, cancelable: true }));
  form.dispatchEvent(new harness.window.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/whatsapp/connect").length, 1);
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  harness.window.history.pushState(null, "", "/app/settings?tab=whatsapp");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  form.dispatchEvent(new harness.window.Event("submit", { bubbles: true, cancelable: true }));
  await harness.settle();
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/whatsapp/connect").length, 1);
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  response.resolve({ message: "stale success" });
  await harness.settle();
  assert.equal(harness.document.getElementById("wa-connect-status").textContent, "");
});

test("OAuth authorization does not redirect after its original integration scope exits", async (t) => {
  const pending = deferred();
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=calendar",
    overrides: {
      "/api/calendar/auth": () => pending.promise,
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  harness.document.getElementById("integ-calendar-connect").click();
  await harness.settle();
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  pending.resolve({ status: 200, ok: true, async json() { return {}; }, async text() { return ""; } });
  await harness.settle();
  assert.equal(harness.window.location.pathname, "/app/overview");
});

test("calendar settings display the server readiness state and honest mode without implying bidirectional sync", async (t) => {
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=calendar",
    overrides: {
      "/api/calendar/status": {
        enabled: true,
        connected: true,
        mode: "google_authoritative",
        integration_status: "authoritative_unavailable",
        error_code: "availability_unverified",
        calendar_id: "primary-team",
        resource_calendar_ids: ["room-a"],
        sync_enabled: true,
        last_sync_at: null,
        pending_count: 2,
        failed_count: 1,
        uncertain_count: 3,
      },
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  assert.equal(harness.document.querySelector("#integ-calendar-stato .badge-status-label").textContent, "Google non disponibile");
  assert.match(harness.document.getElementById("integ-calendar-sub").textContent, /Google autorevole/);
  assert.equal(harness.document.getElementById("integ-calendar-mode").value, "google_authoritative");
  assert.equal(harness.document.getElementById("integ-calendar-primary-id").value, "primary-team");
  assert.equal(harness.document.getElementById("integ-calendar-resource-ids").value, "room-a");
  assert.equal(harness.document.getElementById("integ-calendar-sync-enabled").checked, true);
  assert.match(harness.document.getElementById("integ-calendar-counts").textContent, /In attesa: 2 · Non riuscite: 1 · Esito incerto: 3/);
  assert.match(harness.document.getElementById("integ-calendar-help").textContent, /Non è stato possibile verificare la disponibilità/);
  assert.equal(harness.document.body.textContent.includes("Bidirezionale automatica"), false);
  assert.equal(harness.requests.some((request) => request.pathname === "/api/calendar/settings"), false);
});

test("calendar mode defaults to internal for a legacy status without a mode", async (t) => {
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=calendar",
    overrides: { "/api/calendar/status": { enabled: true, connected: false, integration_status: "not_connected" } },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  assert.equal(harness.document.getElementById("integ-calendar-mode").value, "internal");
  assert.equal(harness.document.getElementById("integ-calendar-connect").hidden, false);
});

test("calendar settings save is explicit, sends the fixed contract, and does not auto-switch mode", async (t) => {
  let savedMode = "google_authoritative";
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=calendar",
    overrides: {
      "/api/calendar/status": (_request, jsonResponse) => jsonResponse({
        enabled: true, connected: true, mode: savedMode, integration_status: "sync_failed",
        error_code: "provider_unavailable", calendar_id: "primary", resource_calendar_ids: [], sync_enabled: false,
      }),
      "PATCH /api/calendar/settings": (request, jsonResponse) => {
        savedMode = JSON.parse(request.options.body).mode;
        return jsonResponse({ saved: true });
      },
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  assert.equal(harness.requests.some((request) => request.method === "PATCH" && request.pathname === "/api/calendar/settings"), false);
  harness.document.getElementById("integ-calendar-mode").value = "internal_calendar_sync";
  harness.document.getElementById("integ-calendar-primary-id").value = "main-calendar";
  harness.document.getElementById("integ-calendar-resource-ids").value = "room-a\n room-b \nroom-a";
  harness.document.getElementById("integ-calendar-sync-enabled").checked = true;
  harness.document.getElementById("integ-calendar-save").click();
  await harness.settle();
  const request = harness.requests.find((item) => item.method === "PATCH" && item.pathname === "/api/calendar/settings");
  assert.ok(request);
  assert.deepEqual(JSON.parse(request.options.body), {
    mode: "internal_calendar_sync",
    calendar_id: "main-calendar",
    resource_calendar_ids: ["room-a", "room-b"],
    sync_enabled: true,
  });
  assert.equal(harness.document.getElementById("integ-calendar-mode").value, "internal_calendar_sync");
});

test("calendar check uses the readiness endpoint and ignores a response after organization switch", async (t) => {
  let resolveCheck;
  const pending = new Promise((resolve) => { resolveCheck = resolve; });
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=calendar",
    overrides: {
      "/api/calendar/status": {
        enabled: true, connected: true, mode: "google_authoritative", integration_status: "connected",
        error_code: null, calendar_id: "primary", resource_calendar_ids: [], sync_enabled: true,
      },
      "POST /api/calendar/check": () => pending,
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  harness.document.getElementById("integ-calendar-check").click();
  await harness.settle();
  assert.equal(harness.document.getElementById("integ-calendar-stato").classList.contains("connecting"), true);
  harness.window.localStorage.setItem("melpis_selected_organization", "org-2");
  harness.window.dispatchEvent(new harness.window.StorageEvent("storage", { key: "melpis_selected_organization", newValue: "org-2" }));
  resolveCheck({
    status: 200, ok: true, async json() { return {
      enabled: true, connected: true, mode: "google_authoritative", integration_status: "connected",
      calendar_id: "other-org-calendar", resource_calendar_ids: [], sync_enabled: true,
    }; }, async text() { return "{}"; }, clone() { return this; },
  });
  await harness.settle();
  assert.notEqual(harness.document.getElementById("integ-calendar-id").textContent, "other-org-calendar");
  assert.equal(harness.requests.some((request) => request.pathname === "/api/integrazioni/test/calendar"), false);
});

test("WhatsApp connect discards a response after an organization switch", async (t) => {
  const response = deferred();
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=whatsapp",
    overrides: {
      "POST /api/whatsapp/connect": () => ({
        status: 200, ok: true, async json() { return response.promise; },
        async text() { return "{}"; }, clone() { return this; },
      }),
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  const form = harness.document.getElementById("wa-connect-form");
  harness.document.getElementById("wa-phone-number-id").value = "phone-id";
  harness.document.getElementById("wa-waba-id").value = "waba-id";
  harness.document.getElementById("wa-access-token").value = "secret-token";
  form.dispatchEvent(new harness.window.Event("submit", { bubbles: true, cancelable: true }));
  await harness.settle();
  harness.window.localStorage.setItem("melpis_selected_organization", "org-2");
  harness.window.dispatchEvent(new harness.window.StorageEvent("storage", { key: "melpis_selected_organization", newValue: "org-2" }));
  response.resolve({ message: "stale success" });
  await harness.settle();
  assert.equal(harness.document.getElementById("wa-connect-status").textContent.includes("stale success"), false);
});

test("WhatsApp connect failure never reports success and restores the submit control", async (t) => {
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=whatsapp",
    overrides: { "POST /api/whatsapp/connect": { __response: { status: 500 }, body: { message: "provider unavailable" } } },
  });
  t.after(harness.dispose);
  await harness.ready();
  const form = harness.document.getElementById("wa-connect-form");
  const button = form.querySelector('[type="submit"]');
  harness.document.getElementById("wa-phone-number-id").value = "phone-id";
  harness.document.getElementById("wa-waba-id").value = "waba-id";
  harness.document.getElementById("wa-access-token").value = "secret-token";
  form.dispatchEvent(new harness.window.Event("submit", { bubbles: true, cancelable: true }));
  await harness.settle();
  assert.equal(/success/i.test(harness.document.getElementById("wa-connect-status").textContent), false);
  assert.equal(button.disabled, false);
  assert.equal(harness.errors.some((args) => args.some((value) => String(value).includes("success"))), false);
});

test("integration scope invalidation clears organization A credentials before organization B is denied", async (t) => {
  let loads = 0;
  const harness = createDashboardHarness({
    url: "https://melpis.test/app/settings?tab=whatsapp",
    overrides: {
      "/api/whatsapp/settings": (_request, jsonResponse) => {
        loads += 1;
        return loads === 1
          ? jsonResponse({ connesso: true, verified_name: "Organization A", display_phone_number: "+100000000" })
          : jsonResponse({ detail: "forbidden" }, { status: 403 });
      },
    },
  });
  t.after(harness.dispose);
  await harness.ready();
  await harness.settle();
  assert.equal(harness.document.getElementById("integ-wa-phone-number-display").textContent, "+100000000");
  harness.window.history.pushState(null, "", "/app/overview");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  harness.window.localStorage.setItem("melpis_selected_organization", "org-2");
  harness.window.history.pushState(null, "", "/app/settings?tab=whatsapp");
  harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
  await harness.settle();
  assert.equal(harness.document.getElementById("integ-wa-phone-number-display").textContent, "—");
  assert.equal(harness.document.getElementById("integ-wa-connected-title").textContent, "WhatsApp Business");
  assert.equal(harness.document.body.textContent.includes("Organization A"), false);
});
