const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");
const fixture = require("../core/bookings/fixtures/catalogs_v2.json").ristorante;
const data = { revision: 1, draft: fixture, published: null, published_revision: null, runtime_enabled: false, organization_sector: "ristorante", capabilities: { can_save: true, can_publish: true } };
const button = (h, text) => [...h.document.querySelectorAll("#booking-catalog-editor button")].find((b) => b.textContent === text);

test("booking view loads the real guided editor and clears it on exit", async (t) => {
  const h = createDashboardHarness({ overrides: { "/api/bookings/catalog": data } }); t.after(() => h.dispose());
  await h.ready(); await h.navigate("prenotazioni");
  await h.waitFor(() => button(h, "Salva bozza") && !button(h, "Salva bozza").hidden, "catalog did not load");
  assert.match(h.document.getElementById("booking-catalog-editor").textContent, /motore automatico non è attivo/);
  assert.ok([...h.document.querySelectorAll("#booking-catalog-editor input")].some((input) => input.value === "standard"));
  await h.navigate("panoramica"); assert.equal(h.document.getElementById("booking-catalog-editor").textContent, "");
});

test("a catalog response after leaving cannot restore private editor content", async (t) => {
  let resolve; const pending = new Promise((done) => { resolve = done; });
  const h = createDashboardHarness({ overrides: { "/api/bookings/catalog": () => pending } }); t.after(() => h.dispose());
  await h.ready(); await h.navigate("prenotazioni"); await h.waitFor(() => h.requests.some((r) => r.pathname === "/api/bookings/catalog"), "request not made");
  await h.navigate("panoramica"); resolve({ ok: true, status: 200, json: async () => data }); await h.settle();
  assert.equal(h.document.getElementById("booking-catalog-editor").textContent, "");
});

test("owner explicitly enables a sector only against current published revision", async (t) => {
  const hair = require("../core/bookings/fixtures/catalogs_v2.json").parrucchiere;
  const catalog = { revision: 2, draft: hair, published: hair, published_revision: 2, runtime_enabled: false,
    organization_sector: "parrucchiere", capabilities: { can_save: true, can_publish: true } };
  const h = createDashboardHarness({ overrides: {
    "/api/bookings/catalog": catalog,
    "/api/bookings/catalog/sector-admission": { enabled: false, eligible: true, published_revision: 2 },
    "PUT /api/bookings/catalog/sector-admission": (request, response) => response({ enabled: true, published_revision: 2, replayed: false }),
  } }); t.after(() => h.dispose());
  await h.ready(); await h.navigate("prenotazioni");
  await h.waitFor(() => button(h, "Attiva conferma automatica") && !button(h, "Attiva conferma automatica").disabled, "owner opt-in did not load");
  button(h, "Attiva conferma automatica").click();
  h.document.getElementById("confirm-ok-btn").click();
  await h.waitFor(() => h.requests.some((r) => r.pathname === "/api/bookings/catalog/sector-admission" && r.method === "PUT"), "owner activation was not sent");
  const mutation = h.requests.find((r) => r.pathname === "/api/bookings/catalog/sector-admission" && r.method === "PUT");
  assert.ok(mutation);
  const body = JSON.parse(mutation.options.body);
  assert.equal(body.enabled, true); assert.equal(body.expected_published_revision, 2);
  assert.match(body.request_id, /^[0-9a-f-]{36}$/i);
});
