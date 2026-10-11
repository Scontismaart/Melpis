const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.resolve(__dirname, "../../web/app.js"), "utf8").replace(/\r\n/g, "\n");
const start = app.indexOf("async function caricaSessione()");
const end = app.indexOf("\n}\n", start);
assert.ok(start >= 0 && end > start, "actual caricaSessione implementation must remain in app.js");
const source = app.slice(start, end + 2);

function makeBootstrap(fetchImpl, refreshImpl = async () => ({ ok: false })) {
  const storage = new Map();
  const context = {
    API_BASE: "",
    fetch: fetchImpl,
    tentaRefresh: refreshImpl,
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
    },
    updates: 0,
    window: {},
  };
  context.window.sessionForTest = () => context.sessione;
  context.window.selectedOrgForTest = () => storage.get("melpis_selected_organization") || null;
  context.sessione = null;
  context.aggiornaBottoneAccesso = () => { context.updates += 1; };
  vm.createContext(context);
  vm.runInContext(`let sessione = null; window.sessionForTest = () => sessione; ${source}`, context);
  return { context, load: () => vm.runInContext("caricaSessione()", context) };
}

const response = (status, body = {}) => ({
  status,
  ok: status >= 200 && status < 300,
  json: async () => body,
});

test("real session bootstrap accepts only an authenticated organization session", async () => {
  const f = makeBootstrap(async () => response(200, {
    user_id: "user-1", organization_id: "org-1", ruolo: "owner",
  }));

  assert.equal(await f.load(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.context.window.sessionForTest())), {
    user_id: "user-1", organization_id: "org-1", ruolo: "owner",
  });
  assert.equal(f.context.updates, 1);
});

test("revoked and anonymous sessions fail closed after one refresh attempt", async () => {
  let refreshes = 0;
  const f = makeBootstrap(async () => response(401), async () => {
    refreshes += 1;
    return response(401);
  });

  assert.equal(await f.load(), false);
  assert.equal(refreshes, 1);
  assert.equal(f.context.window.sessionForTest(), null);
  assert.equal(f.context.updates, 1);
});

test("organization fallback selects the first authorized organization and retries /me", async () => {
  const calls = [];
  const f = makeBootstrap(async (url, options = {}) => {
    calls.push({ url, headers: options.headers || {} });
    if (url === "/api/auth/me" && calls.length === 1) return response(403);
    if (url === "/api/team/organizations") {
      return response(200, { organizations: [{ id: "org-fallback" }] });
    }
    return response(200, { user_id: "user-1", organization_id: "org-fallback", ruolo: "owner" });
  });

  assert.equal(await f.load(), true);
  assert.equal(f.context.window.selectedOrgForTest(), "org-fallback");
  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/auth/me", "/api/team/organizations", "/api/auth/me",
  ]);
  assert.equal(calls[2].headers["X-Organization-Id"], "org-fallback");
});

test("a deferred /me response keeps the existing bootstrap settlement semantics", async () => {
  let resolveMe;
  const f = makeBootstrap(() => new Promise((resolve) => { resolveMe = resolve; }));
  const pending = f.load();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(typeof resolveMe, "function");

  resolveMe(response(200, { user_id: "user-1", organization_id: "org-1", ruolo: "owner" }));
  assert.equal(await pending, true);
  assert.equal(f.context.window.sessionForTest().organization_id, "org-1");
});
