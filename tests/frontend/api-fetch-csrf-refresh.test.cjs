const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const app = fs.readFileSync(path.resolve(__dirname, "../../web/app.js"), "utf8").replace(/\r\n/g, "\n");

function functionSource(name) {
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
  win.fetch = fetchImpl;
  win.segnalaErroreRete = () => {};
  win.segnaReteOk = () => {};
  win.aggiornaBottoneAccesso = () => {};
  win.authRedirects = 0;
  win.vaiAdAccesso = () => { win.authRedirects += 1; };
  win.document.body.classList.add("authenticated");
  win.toast = () => {};
  win.eval(`
    const API_BASE = "";
    let sessione = { user_id: "disposable-user" };
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
    ${functionSource("leggiCookie")}
    ${functionSource("csrfToken")}
    ${functionSource("tentaRefresh")}
    ${functionSource("invalidaSessione")}
    ${functionSource("apiFetch")}
    ${functionSource("faiLogout")}
  `);
  return { dom, win };
}

function jsonResponse(status) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => null },
    clone() { return this; },
    json: async () => ({}),
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
