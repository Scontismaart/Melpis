const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const integrations = fs.readFileSync(path.resolve(__dirname, "../../web/dashboard-integrations.js"), "utf8").replace(/\r\n/g, "\n");
const selectedOrg = "11111111-1111-1111-1111-111111111111";
const providers = [
  { id: "integ-calendar-connect", pathname: "/api/calendar/auth" },
  { id: "integ-reviews-connect", pathname: "/api/reviews/google/auth" },
];

function connectHandlerSource(id) {
  const marker = `document.getElementById("${id}")?.addEventListener("click", async () => {`;
  const start = integrations.indexOf(marker);
  assert.notEqual(start, -1, `missing click handler for ${id}`);
  const end = integrations.indexOf("\n});", start + marker.length);
  assert.notEqual(end, -1, `missing end of click handler for ${id}`);
  return integrations.slice(start, end + 4);
}

function setup(provider, preflight) {
  const calls = [];
  const listeners = new Map();
  const window = { location: { origin: "https://melpis.test", href: "https://melpis.test/app/settings" } };
  const document = {
    getElementById(id) {
      return { addEventListener(type, handler) { listeners.set(`${id}:${type}`, handler); } };
    },
  };
  const apiFetch = async (...args) => {
    calls.push(args);
    if (preflight instanceof Error) throw preflight;
    return preflight;
  };
  vm.runInNewContext(
    `const API_BASE = "";\nfunction captureIntegrationContext() { return {}; }\nfunction claimAction() { return () => {}; }\nfunction isIntegrationContextCurrent() { return true; }\n${connectHandlerSource(provider.id)}`,
    { document, window, localStorage: { getItem: () => selectedOrg }, URL, apiFetch },
  );
  return { calls, window, click: listeners.get(`${provider.id}:click`) };
}

for (const provider of providers) {
  test(`${provider.id} keeps the selected organization in preflight and navigation`, async () => {
    const { calls, window, click } = setup(provider, { status: 307 });

    await click();

    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].method, "GET");
    assert.equal(calls[0][1].redirect, "manual");
    const target = new URL(calls[0][0]);
    assert.equal(target.pathname, provider.pathname);
    assert.equal(target.searchParams.get("organization_id"), selectedOrg);
    assert.equal(window.location.href, target.href);
  });

  test(`${provider.id} does not navigate when owner preflight is denied`, async () => {
    const { calls, window, click } = setup(provider, { status: 403 });
    const originalLocation = window.location.href;

    await click();

    assert.equal(calls.length, 1);
    assert.equal(window.location.href, originalLocation);
  });

  test(`${provider.id} does not navigate when owner preflight fails on the network`, async () => {
    const { calls, window, click } = setup(provider, new Error("network unavailable"));
    const originalLocation = window.location.href;

    await click();

    assert.equal(calls.length, 1);
    assert.equal(window.location.href, originalLocation);
  });
}
