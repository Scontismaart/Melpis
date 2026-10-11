const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const app = fs.readFileSync(path.resolve(__dirname, "../../web/app.js"), "utf8").replace(/\r\n/g, "\n");
const transport = fs.readFileSync(path.resolve(__dirname, "../../web/dashboard-auth-transport.js"), "utf8");

function appFunctionSource(name) {
  const start = app.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.notEqual(start, -1, `missing ${name}`);
  const end = app.indexOf("\n}", start);
  assert.notEqual(end, -1, `missing ${name} end`);
  return app.slice(start, end + 2);
}

function setup(fetchImpl) {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "https://melpis.test/app/overview",
    runScripts: "outside-only",
  });
  const win = dom.window;
  win.segnalaErroreRete = () => {};
  win.segnaReteOk = () => {};
  win.aggiornaBottoneAccesso = () => {};
  win.authRedirects = 0;
  win.vaiAdAccesso = () => { win.authRedirects += 1; };
  win.document.body.classList.add("authenticated");
  win.toast = () => {};
  win.authInvalidations = 0;
  win.eval(`
    const API_BASE = "";
    let sessione = { user_id: "disposable-user" };
    window.sessionForTest = () => sessione;
    let dashboardSessionEpoch = 0;
    const dashboardOverviewModule = { invalidate() { window.overviewInvalidations = (window.overviewInvalidations || 0) + 1; } };
    const dashboardReviewsModule = { invalidate() { window.reviewsInvalidations = (window.reviewsInvalidations || 0) + 1; } };
    const dashboardKnowledgeModule = { invalidate() {} };
    const dashboardTeamModule = { invalidate() {} };
    const dashboardAiSimulatorModule = { invalidate() {} };
    const dashboardBookingsModule = { invalidate() {} };
    const dashboardInboxModule = { invalidate() {} };
    const dashboardAiSettingsModule = { invalidate() {} };
    const dashboardSettingsModule = { invalidate() {} };
    ${appFunctionSource("invalidaSessione")}
    ${appFunctionSource("faiLogout")}
  `);
  win.eval(transport);
  const api = win.MelpisDashboardAuthTransport.create({
    API_BASE: "",
    fetch: fetchImpl,
    document: win.document,
    storage: win.localStorage,
    onNetworkError: () => {},
    onNetworkOk: () => {},
    invalidateSession: () => {
      win.authInvalidations += 1;
      win.eval("invalidaSessione()");
    },
    redirectToLogin: () => win.vaiAdAccesso(),
    showMfaRequired: (openSecurity) => { win.pendingMfaAction = openSecurity; },
    openSecurity: () => { win.securityOpens = (win.securityOpens || 0) + 1; },
  });
  win.apiFetch = api.apiFetch;
  win.tentaRefresh = api.tentaRefresh;
  return { dom, win, api };
}

function jsonResponse(status, body = {}, responseHeaders = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => responseHeaders[name] || null },
    clone() { return jsonResponse(status, body, responseHeaders); },
    json: async () => body,
  };
}

test("apiFetch uses the rotated CSRF cookie on its single retry after refresh", async (t) => {
  const calls = [];
  let protectedMutationApplied = false;
  let protectedAttempts = 0;
  const { dom, win } = setup(async (url, options) => {
    calls.push({ url, headers: { ...options.headers } });
    if (url === "/api/auth/refresh") {
      win.document.cookie = "wa_csrf=new-csrf-token; Path=/; SameSite=Lax";
      return jsonResponse(200);
    }
    protectedAttempts += 1;
    if (protectedAttempts === 1) return jsonResponse(401);
    assert.equal(options.headers["X-CSRF-Token"], "new-csrf-token");
    protectedMutationApplied = true;
    return jsonResponse(200);
  });
  t.after(() => dom.window.close());
  win.document.cookie = "wa_csrf=old-csrf-token; Path=/; SameSite=Lax";

  const response = await win.eval('apiFetch("/api/bookings/booking-id/cancel", { method: "POST" })');

  assert.equal(response.status, 200);
  assert.equal(protectedMutationApplied, true);
  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/bookings/booking-id/cancel",
    "/api/auth/refresh",
    "/api/bookings/booking-id/cancel",
  ]);
});

test("apiFetch sends a mutation once with the current CSRF cookie when the first request succeeds", async (t) => {
  const calls = [];
  const { dom, win } = setup(async (url, options) => {
    calls.push({ url, headers: { ...options.headers } });
    return jsonResponse(200);
  });
  t.after(() => dom.window.close());
  win.document.cookie = "wa_csrf=current-csrf-token; Path=/; SameSite=Lax";

  const response = await win.eval('apiFetch("/api/bookings/booking-id/cancel", { method: "POST" })');

  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].headers["X-CSRF-Token"], "current-csrf-token");
});

test("apiFetch does not retry a mutation when refresh fails", async (t) => {
  const calls = [];
  const { dom, win } = setup(async (url, options) => {
    calls.push({ url, headers: { ...options.headers } });
    return jsonResponse(401);
  });
  t.after(() => dom.window.close());

  const response = await win.eval('apiFetch("/api/bookings/booking-id/cancel", { method: "POST" })');

  assert.equal(response.status, 401);
  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/bookings/booking-id/cancel",
    "/api/auth/refresh",
  ]);
});

test("apiFetch removes a stale caller CSRF header when the cookie is gone on retry", async (t) => {
  const calls = [];
  let protectedAttempts = 0;
  const { dom, win } = setup(async (url, options) => {
    calls.push({ url, headers: { ...options.headers } });
    if (url === "/api/auth/refresh") {
      win.document.cookie = "wa_csrf=; Path=/; Max-Age=0; SameSite=Lax";
      return jsonResponse(200);
    }
    protectedAttempts += 1;
    return jsonResponse(protectedAttempts === 1 ? 401 : 200);
  });
  t.after(() => dom.window.close());
  win.document.cookie = "wa_csrf=current-csrf-token; Path=/; SameSite=Lax";

  const response = await win.eval(`apiFetch("/api/bookings/booking-id/cancel", {
    method: "POST", headers: { "X-CSRF-Token": "caller-stale-token" }
  })`);

  assert.equal(response.status, 200);
  assert.equal(calls[0].headers["X-CSRF-Token"], "current-csrf-token");
  assert.equal(calls[2].headers["X-CSRF-Token"], undefined);
});

test("apiFetch leaves safe requests without a CSRF header", async (t) => {
  let request;
  const { dom, win } = setup(async (_url, options) => {
    request = options;
    return jsonResponse(200);
  });
  t.after(() => dom.window.close());
  win.document.cookie = "wa_csrf=csrf-value; Path=/; SameSite=Lax";

  await win.eval('apiFetch("/api/bookings")');

  assert.equal(request.headers["X-CSRF-Token"], undefined);
});

test("auth transport factory exposes only frozen request functions and no cookie token helper", async (t) => {
  const { dom, win, api } = setup(async () => jsonResponse(200));
  t.after(() => dom.window.close());

  assert.equal(Object.isFrozen(win.MelpisDashboardAuthTransport), true);
  assert.equal(Object.isFrozen(api), true);
  assert.deepEqual(Object.keys(api).sort(), ["apiFetch", "tentaRefresh"]);
  assert.equal(api.csrfToken, undefined);
  assert.equal(api.leggiCookie, undefined);
});

test("apiFetch snapshots the selected organization header and always includes cookies", async (t) => {
  let request;
  const { dom, win } = setup(async (_url, options) => { request = options; return jsonResponse(200); });
  t.after(() => dom.window.close());
  win.localStorage.setItem("melpis_selected_organization", "qa-org-a");

  await win.eval('apiFetch("/api/team/members")');

  assert.equal(request.headers["X-Organization-Id"], "qa-org-a");
  assert.equal(request.credentials, "include");
});

test("apiFetch does not refresh or redirect a 401 from an auth endpoint", async (t) => {
  const calls = [];
  const { dom, win } = setup(async (url) => { calls.push(url); return jsonResponse(401); });
  t.after(() => dom.window.close());

  const response = await win.eval('apiFetch("/api/auth/me")');

  assert.equal(response.status, 401);
  assert.deepEqual(calls, ["/api/auth/me"]);
  assert.equal(win.authInvalidations, 0);
  assert.equal(win.authRedirects, 0);
});

test("a plain owner/staff permission 403 is returned without MFA or auth redirects", async (t) => {
  const { dom, win } = setup(async () => jsonResponse(403, { detail: "Not allowed" }));
  t.after(() => dom.window.close());

  const response = await win.eval('apiFetch("/api/billing/portal")');

  assert.equal(response.status, 403);
  assert.equal(response.mfaRequired, undefined);
  assert.equal(win.pendingMfaAction, undefined);
  assert.equal(win.authInvalidations, 0);
  assert.equal(win.authRedirects, 0);
});

test("MFA-marked 403 keeps the existing step-up action behind the toast callback", async (t) => {
  const { dom, win } = setup(async () => jsonResponse(403, {}, { "X-MFA-Required": "true" }));
  t.after(() => dom.window.close());

  const response = await win.eval('apiFetch("/api/settings/billing")');

  assert.equal(response.mfaRequired, true);
  assert.equal(win.securityOpens || 0, 0);
  assert.equal(typeof win.pendingMfaAction, "function");
  win.pendingMfaAction();
  assert.equal(win.securityOpens, 1);
});

test("apiFetch fails closed before sending a mutation with malformed CSRF cookie data", async (t) => {
  let calls = 0;
  const { dom, win } = setup(async () => { calls += 1; return jsonResponse(200); });
  t.after(() => dom.window.close());
  win.document.cookie = "wa_csrf=%E0%A4%A; Path=/; SameSite=Lax";

  await assert.rejects(win.eval('apiFetch("/api/bookings", { method: "POST" })'), /URI malformed/);
  assert.equal(calls, 0);
});

test("apiFetch keeps independent refresh attempts for concurrent protected 401s", async (t) => {
  const refreshes = [];
  let resourceAttempts = 0;
  const { dom, win } = setup(async (url) => {
    if (url === "/api/auth/refresh") {
      return new Promise((resolve) => refreshes.push(resolve));
    }
    resourceAttempts += 1;
    return jsonResponse(resourceAttempts <= 2 ? 401 : 200);
  });
  t.after(() => dom.window.close());

  const first = win.eval('apiFetch("/api/bookings")');
  const second = win.eval('apiFetch("/api/team/members")');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(refreshes.length, 2, "no new refresh single-flight policy is introduced");
  refreshes.forEach((resolve) => resolve(jsonResponse(200)));

  const responses = await Promise.all([first, second]);
  assert.deepEqual(responses.map((response) => response.status), [200, 200]);
});

test("an in-flight logout still invalidates and publishes the existing cross-tab marker on success", async (t) => {
  let finishRequest;
  const { dom, win } = setup(() => new Promise((resolve) => { finishRequest = resolve; }));
  t.after(() => dom.window.close());

  const logout = win.eval("faiLogout()");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(win.document.body.classList.contains("authenticated"), true);
  finishRequest(jsonResponse(200));
  assert.equal(await logout, true);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
  assert.match(win.localStorage.getItem("melpis_auth_logout"), /^\d+:/);
});

test("a private response already in flight during logout remains a raw transport response", async (t) => {
  let resolvePrivateResponse;
  const { dom, win } = setup((url) => {
    if (url === "/api/auth/logout") return Promise.resolve(jsonResponse(200));
    return new Promise((resolve) => { resolvePrivateResponse = resolve; });
  });
  t.after(() => dom.window.close());

  const privateRequest = win.eval('apiFetch("/api/team/members")');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(typeof resolvePrivateResponse, "function");
  assert.equal(await win.eval("faiLogout()"), true);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
  assert.equal(win.sessionForTest(), null);

  resolvePrivateResponse(jsonResponse(200, { members: ["private-data"] }));
  const response = await privateRequest;
  assert.equal(response.status, 200);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
  assert.equal(win.sessionForTest(), null);
});

test("a second protected 401 after refresh hides private content and redirects once", async (t) => {
  const { dom, win } = setup(async (url) => jsonResponse(url.includes("/refresh") ? 200 : 401));
  t.after(() => dom.window.close());
  await win.eval('apiFetch("/api/team/members")');
  assert.equal(win.authRedirects, 1);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
});

test("a network failure during auth refresh hides private content and redirects once", async (t) => {
  const { dom, win } = setup(async (url) => {
    if (url.includes("/refresh")) throw new Error("network unavailable");
    return jsonResponse(401);
  });
  t.after(() => dom.window.close());
  await assert.rejects(win.eval('apiFetch("/api/team/members")'));
  assert.equal(win.authRedirects, 1);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
});

test("failed logout does not falsely report a completed sign-out", async (t) => {
  const { dom, win } = setup(async () => jsonResponse(503));
  t.after(() => dom.window.close());
  assert.equal(await win.eval("faiLogout()"), false);
  assert.equal(win.localStorage.getItem("melpis_auth_logout"), null);
});

test("successful logout removes private content before navigating", async (t) => {
  const { dom, win } = setup(async () => jsonResponse(200));
  t.after(() => dom.window.close());
  assert.equal(await win.eval("faiLogout()"), true);
  assert.equal(win.document.body.classList.contains("authenticated"), false);
  assert.equal(win.overviewInvalidations, 1);
  assert.equal(win.reviewsInvalidations, 1);
  assert.match(win.localStorage.getItem("melpis_auth_logout"), /^\d+:/);
});
