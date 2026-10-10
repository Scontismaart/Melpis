const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");

const canonicalRoutes = {
  panoramica: "/app/overview",
  inbox: "/app/inbox",
  prenotazioni: "/app/bookings",
  recensioni: "/app/reviews",
  team: "/app/team",
  assistente: "/app/ai-simulator",
  conoscenza: "/app/knowledge",
  "configurazione-ai": "/app/ai-settings",
  impostazioni: "/app/settings",
};

function activePanel(document) {
  return [...document.querySelectorAll("[data-view-panel]")]
    .filter((panel) => !panel.classList.contains("view-hidden"));
}

function listenerCount(harness, target, type) {
  return harness.listenerRegistrations.filter((item) => item.target === target && item.type === type).length;
}

function intervalDelays(harness) {
  return [...harness.intervals.values()].map(({ delay }) => delay).sort((a, b) => a - b);
}

function assertNoRendererErrors(harness) {
  assert.deepEqual(harness.errors, [], "dashboard emitted no console errors or warnings");
  assert.deepEqual(harness.windowErrors, [], "dashboard emitted no uncaught window errors");
}

test("full dashboard script order and authenticated startup are single-instance", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();

  const index = (path) => harness.scriptOrder.indexOf(path);
  assert.ok(index("/app/dialog-focus.js") < index("/app/dashboard-router.js"));
  assert.ok(index("/app/dashboard-router.js") < index("/app/dashboard-shared.js"));
  assert.ok(index("/app/dashboard-shared.js") < index("/app/dashboard-overview.js"));
  assert.ok(index("/app/dashboard-shared.js") < index("/app/dashboard-inbox.js"));
  assert.ok(index("/app/dashboard-inbox.js") < index("/app/app.js"));
  assert.ok(index("/app/dashboard-overview.js") < index("/app/dashboard-reviews.js"));
  assert.ok(index("/app/dashboard-reviews.js") < index("/app/app.js"));
  const extracted = ["bookings", "knowledge", "team", "ai-simulator"];
  for (const name of extracted) {
    const file = `/app/dashboard-${name}.js`;
    assert.ok(index("/app/dashboard-reviews.js") < index(file));
    assert.ok(index(file) < index("/app/app.js"));
    assert.equal(harness.scriptOrder.filter((path) => path === file).length, 1);
  }
  assert.ok(index("/app/dashboard-router.js") < index("/app/app.js"));
  assert.ok(index("/app/app.js") < index("/app/mfa.js"));
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/dashboard-router.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/dashboard-shared.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/dashboard-inbox.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/dashboard-overview.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/dashboard-reviews.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/app.js").length, 1);
  assert.equal(harness.scriptOrder.filter((path) => path === "/app/mfa.js").length, 1);
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/auth/me").length, 1);
  assert.equal(harness.routerConstructions, 1);
  assert.equal(harness.domContentLoadedEvents, 1, "JSDOM dispatches its native DOMContentLoaded event once");
  assert.equal(activePanel(harness.document)[0].dataset.viewPanel, "panoramica");
  assert.equal(listenerCount(harness, harness.window, "popstate"), 1);
  assert.equal(listenerCount(harness, harness.document, "click"), 4, "shell-level click handlers attach once");
  assert.deepEqual(intervalDelays(harness), [5000, 30000]);
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("real shell routes stay canonical across nine destinations and deep links", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();

  assert.deepEqual(JSON.parse(JSON.stringify(harness.window.MelpisDashboardRouter.ROUTES)), canonicalRoutes);
  const routeHrefs = [...new Set([...harness.document.querySelectorAll("[data-app-view]")]
    .map((anchor) => new URL(anchor.href).pathname))].sort();
  assert.deepEqual(routeHrefs, Object.values(canonicalRoutes).sort());
  assert.equal(harness.document.querySelector('[data-app-view="report"]'), null);
  assert.equal(harness.document.querySelector('[data-view-panel="report"]'), null);

  for (const [view, href] of Object.entries(canonicalRoutes)) {
    harness.window.history.pushState(null, "", href);
    harness.window.dispatchEvent(new harness.window.PopStateEvent("popstate"));
    await harness.settle();
    assert.equal(harness.window.location.pathname, href, `${view} URL remains canonical`);
    assert.equal(activePanel(harness.document).length, 1, `${view} has exactly one active panel`);
    assert.equal(activePanel(harness.document)[0].dataset.viewPanel, view);
    const activeSidebarItems = harness.document.querySelectorAll(".sidebar-nav .nav-item.active");
    if (view === "impostazioni") {
      assert.equal(activeSidebarItems.length, 0, "Settings is an account-menu route, not a primary sidebar item");
    } else {
      assert.equal(activeSidebarItems.length, 1, `${view} has one active sidebar item`);
      assert.equal(activeSidebarItems[0].dataset.view, view);
    }
  }
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("direct Settings deep link selects one allowlisted panel and synchronizes title and sidebar", async (t) => {
  const harness = createDashboardHarness({ url: "https://melpis.test/app/settings?tab=calendar&source=contract#oauth" });
  t.after(harness.dispose);
  await harness.ready();

  assert.equal(harness.window.location.pathname, "/app/settings");
  assert.equal(harness.window.location.search, "?source=contract&tab=calendar");
  assert.equal(harness.window.location.hash, "#oauth");
  assert.equal(activePanel(harness.document)[0].dataset.viewPanel, "impostazioni");
  const settings = harness.document.querySelector('[data-view-panel="impostazioni"]');
  const selected = settings.querySelectorAll('[data-settings-cat-btn][aria-selected="true"]');
  const visiblePanels = [...settings.querySelectorAll("[data-settings-panel]")].filter((panel) => !panel.hidden);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].dataset.settingsCatBtn, "calendar");
  assert.equal(visiblePanels.length, 1);
  assert.equal(visiblePanels[0].dataset.settingsPanel, "calendar");
  assert.equal(harness.document.getElementById("topbar-title").dataset.i18n, "dashboard:topbar.title_calendar");
  assert.equal(harness.document.querySelectorAll(".sidebar-nav .nav-item.active").length, 0);
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("repeated real navigation does not add shell listeners or duplicate route actions", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();
  const baseline = {
    windowPopstate: listenerCount(harness, harness.window, "popstate"),
    documentClick: listenerCount(harness, harness.document, "click"),
    documentDomReady: listenerCount(harness, harness.document, "DOMContentLoaded"),
  };

  await harness.navigate("inbox");
  await harness.navigate("impostazioni", "?tab=sicurezza");
  await harness.navigate("panoramica");
  await harness.navigate("inbox");
  harness.router.start();
  harness.router.start();
  await harness.navigate("panoramica");
  await harness.navigate("inbox");
  assert.deepEqual({
    windowPopstate: listenerCount(harness, harness.window, "popstate"),
    documentClick: listenerCount(harness, harness.document, "click"),
    documentDomReady: listenerCount(harness, harness.document, "DOMContentLoaded"),
  }, baseline);

  const inboxCalls = harness.requests.filter((request) => request.pathname === "/api/inbox/tickets").length;
  assert.equal(inboxCalls, 3, "one list load per actual entry to Inbox");
  assert.equal(activePanel(harness.document)[0].dataset.viewPanel, "inbox");
  assert.equal(harness.routerConstructions, 1);
  assert.deepEqual(intervalDelays(harness), [3000, 30000]);
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("overview and Inbox swaps retain exactly one view poller and one notification poller", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();
  assert.deepEqual(intervalDelays(harness), [5000, 30000]);

  await harness.navigate("inbox");
  assert.deepEqual(intervalDelays(harness), [3000, 30000]);
  await harness.navigate("panoramica");
  assert.deepEqual(intervalDelays(harness), [5000, 30000]);
  await harness.navigate("inbox");
  await harness.navigate("panoramica");
  await harness.navigate("inbox");
  assert.deepEqual(intervalDelays(harness), [3000, 30000]);
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("booking form state survives leaving and re-entering its route", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();
  await harness.navigate("prenotazioni");
  const name = harness.document.getElementById("booking-name");
  assert.ok(name);
  name.value = "Draft customer";
  harness.document.getElementById("booking-note").value = "Keep this draft";

  await harness.navigate("inbox");
  await harness.navigate("prenotazioni");
  assert.equal(name.value, "Draft customer");
  assert.equal(harness.document.getElementById("booking-note").value, "Keep this draft");
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("API requests keep selected organization, cookie credentials, and CSRF; an org denial stays denied", async (t) => {
  const denied = { __response: { status: 403 }, body: { detail: "Organization access denied" } };
  const harness = createDashboardHarness({ overrides: { "POST /api/test/org-denied": denied } });
  t.after(harness.dispose);
  await harness.ready();

  const result = await harness.window.eval('apiFetch("/api/test/org-denied", { method: "POST", body: JSON.stringify({ organization_id: "org-other" }) })');
  const request = harness.requests.find((item) => item.pathname === "/api/test/org-denied");
  assert.equal(result.status, 403);
  assert.equal(result.mfaRequired, undefined);
  assert.equal(request.options.credentials, "include");
  assert.equal(request.options.headers["X-Organization-Id"], "org-1");
  assert.equal(request.options.headers["X-CSRF-Token"], "csrf-test-token");
  assert.equal(harness.document.querySelector(".toast-action-btn"), null, "ordinary authorization denial does not become an MFA action");
  assert.deepEqual(harness.unexpectedRequests, []);
});

test("MFA-required API response reaches the real MFA bridge through the toast action", async (t) => {
  const required = {
    __response: { status: 403, headers: { "X-MFA-Required": "true" } },
    body: { detail: "step-up required" },
  };
  const harness = createDashboardHarness({ overrides: { "POST /api/test/mfa-required": required } });
  t.after(harness.dispose);
  await harness.ready();

  const result = await harness.window.eval('apiFetch("/api/test/mfa-required", { method: "POST" })');
  assert.equal(result.status, 403);
  assert.equal(result.mfaRequired, true);
  const action = harness.document.querySelector(".toast-action-btn");
  assert.ok(action);
  action.click();
  await harness.settle();
  assert.equal(activePanel(harness.document)[0].dataset.viewPanel, "impostazioni");
  assert.equal(harness.window.location.search, "?tab=sicurezza");
  assert.equal(harness.document.querySelector('[data-settings-cat-btn="sicurezza"]').getAttribute("aria-selected"), "true");
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/auth/mfa").length, 1);
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/test/mfa-required").length, 1,
    "clicking the MFA prompt does not repeat the denied privileged mutation");
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("auth-me retries once after successful refresh and only then reveals the private shell", async (t) => {
  let meCalls = 0;
  const harness = createDashboardHarness({
    overrides: {
      "GET /api/auth/me": (_request, respond) => {
        meCalls += 1;
        return meCalls === 1
          ? respond({ detail: "expired" }, { status: 401 })
          : respond({ user_id: "user-1", organization_id: "org-1", email: "owner@example.test", ruolo: "owner" });
      },
      "POST /api/auth/refresh": { __response: { status: 200 }, body: {} },
    },
  });
  t.after(harness.dispose);
  await harness.ready();

  assert.equal(harness.requests.filter((request) => request.pathname === "/api/auth/me").length, 2);
  const refresh = harness.requests.filter((request) => request.pathname === "/api/auth/refresh");
  assert.equal(refresh.length, 1);
  assert.equal(refresh[0].method, "POST");
  assert.equal(refresh[0].options.credentials, "include");
  assert.equal(refresh[0].options.headers["X-CSRF-Token"], "csrf-test-token");
  assert.equal(harness.document.body.classList.contains("authenticated"), true);
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("failed auth refresh leaves private UI hidden and redirects once", async (t) => {
  const harness = createDashboardHarness({
    overrides: {
      "GET /api/auth/me": { __response: { status: 401 }, body: { detail: "expired" } },
      "POST /api/auth/refresh": { __response: { status: 401 }, body: { detail: "refresh denied" } },
    },
  });
  t.after(harness.dispose);
  await harness.domReady;
  await harness.waitFor(() => harness.requests.some((request) => request.pathname === "/api/auth/refresh"));
  await harness.settle();

  assert.equal(harness.requests.filter((request) => request.pathname === "/api/auth/me").length, 1);
  assert.equal(harness.requests.filter((request) => request.pathname === "/api/auth/refresh").length, 1);
  assert.equal(harness.document.body.classList.contains("authenticated"), false);
  assert.equal(harness.document.getElementById("accesso-btn").hidden, false);
  assert.equal(harness.requests.some((request) => request.pathname === "/api/ui/summary"), false,
    "authenticated dashboard loaders do not start after failed authentication");
  assert.deepEqual(harness.jsdomErrors.map((error) => error.message), [
    "Not implemented: navigation (except hash changes)",
  ]);
  assert.deepEqual(harness.unexpectedRequests, []);
});

test("an auth-me organization denial selects the returned organization for subsequent client requests", async (t) => {
  let meCalls = 0;
  const harness = createDashboardHarness({
    selectedOrganization: "org-stale",
    overrides: {
      "GET /api/auth/me": (_request, respond) => {
        meCalls += 1;
        if (meCalls === 1) return respond({ detail: "organization not selected" }, { status: 403 });
        return respond({ user_id: "user-2", organization_id: "org-B", email: "member@example.test", ruolo: "owner" });
      },
      "GET /api/team/organizations": { organizations: [{ id: "org-B", name: "Org B" }] },
    },
  });
  t.after(harness.dispose);
  await harness.ready();

  const meRequests = harness.requests.filter((request) => request.pathname === "/api/auth/me");
  assert.equal(meRequests.length, 2);
  assert.equal(meRequests[0].options.headers["X-Organization-Id"], "org-stale");
  assert.equal(meRequests[1].options.headers["X-Organization-Id"], "org-B");
  assert.equal(harness.window.localStorage.getItem("melpis_selected_organization"), "org-B");
  const successfulMeIndex = harness.requests.indexOf(meRequests[1]);
  const followingApiRequests = harness.requests.slice(successfulMeIndex + 1)
    .filter((request) => request.pathname.startsWith("/api/"));
  assert.ok(followingApiRequests.length > 0);
  assert.ok(followingApiRequests.every((request) => request.options.headers["X-Organization-Id"] === "org-B"));
  assert.deepEqual(harness.unexpectedRequests, []);
  assertNoRendererErrors(harness);
});

test("full shell preserves private-page lifecycle and cross-tab logout contracts", async (t) => {
  const harness = createDashboardHarness();
  t.after(harness.dispose);
  await harness.ready();
  const { window, document } = harness;
  window.dispatchEvent(new window.PageTransitionEvent("pageshow", { persisted: false }));
  assert.equal(document.body.classList.contains("authenticated"), true);
  window.dispatchEvent(new window.PageTransitionEvent("pagehide", { persisted: true }));
  assert.equal(document.body.classList.contains("authenticated"), false);
  window.dispatchEvent(new window.PageTransitionEvent("pageshow", { persisted: true }));
  assert.equal(document.body.dataset.authPageshowPersisted, "true");
  assert.equal(document.body.classList.contains("authenticated"), false);
  assert.equal(harness.jsdomErrors.length, 1, "one reload requested for persisted restore");
  window.dispatchEvent(new window.StorageEvent("storage", {
    key: "melpis_auth_logout", newValue: "synthetic-logout",
  }));
  assert.equal(document.body.classList.contains("authenticated"), false);
  assert.equal(document.getElementById("accesso-btn").hidden, false);
  assert.equal(harness.jsdomErrors.length, 2, "logout requests one login navigation");
});

test("limited staff shell keeps billing controls hidden while owner controls remain visible", async (t) => {
  for (const ruolo of ["owner", "staff"]) {
    const harness = createDashboardHarness({ overrides: {
      "GET /api/auth/me": { user_id: "user-1", organization_id: "org-1", email: "qa@example.test", ruolo },
    } });
    t.after(harness.dispose);
    await harness.ready();
    assert.equal(harness.document.getElementById("sidebar-menu-piano").hidden, ruolo === "staff");
    for (const button of harness.document.querySelectorAll('[data-settings-cat-btn="piano"], [data-settings-cat-btn="fatturazione"]')) {
      assert.equal(button.style.display, ruolo === "staff" ? "none" : "");
    }
    assert.deepEqual(harness.unexpectedRequests, []);
    assertNoRendererErrors(harness);
  }
});
