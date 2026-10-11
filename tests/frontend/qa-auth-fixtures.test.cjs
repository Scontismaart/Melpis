const { test } = require("node:test");
const assert = require("node:assert/strict");
const { ORG_A, ORG_B } = require("./fixtures/dashboard-visual-api.cjs");
const { SCENARIOS, viewReply } = require("./fixtures/dashboard-views-qa.cjs");
const { interactionReply } = require("../../scripts/dashboard-baseline-server.cjs");

test("loopback login fixture accepts only the fixed synthetic account and clears signed-out state", () => {
  assert.equal(SCENARIOS.has("auth-success"), true);
  assert.deepEqual(interactionReply(
    "POST", "/api/auth/login", "dashboard_qa_signed_out=1", true,
    JSON.stringify({ email: "owner@dashboard-qa.invalid", password: "qa-only-password" }),
  ), { status: 200, body: { ok: true }, signedIn: true });
  assert.equal(interactionReply(
    "POST", "/api/auth/login", "", true,
    JSON.stringify({ email: "owner@dashboard-qa.invalid", password: "wrong" }),
  ).status, 401);
});

test("expired-session fixture fails closed for both session lookup and refresh", () => {
  assert.equal(SCENARIOS.has("auth-expired"), true);
  assert.equal(viewReply("GET", "/api/auth/me", "auth-expired").status, 401);
  assert.equal(viewReply("POST", "/api/auth/refresh", "auth-expired").status, 401);
});

test("org-switch fixture resolves the authorized organization from the request context", () => {
  assert.equal(SCENARIOS.has("settings-org-switch"), true);
  const orgA = viewReply("GET", "/api/auth/me", "settings-org-switch", ORG_A);
  const orgB = viewReply("GET", "/api/auth/me", "settings-org-switch", ORG_B);
  assert.equal(orgA.body.organization_id, ORG_A);
  assert.equal(orgB.body.organization_id, ORG_B);
});
