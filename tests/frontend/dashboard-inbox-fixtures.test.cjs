const { test } = require("node:test");
const assert = require("node:assert/strict");
const { ORG_A, ORG_B } = require("./fixtures/dashboard-visual-api.cjs");
const { reply: baselineReply } = require("./fixtures/dashboard-visual-api.cjs");
const { viewReply, SCENARIOS } = require("./fixtures/dashboard-views-qa.cjs");
const { safeRequests, orgStates } = require("./fixtures/dashboard-inbox-qa.cjs");
const { interactionReply, resolveAssetRoot, defaultRoot } = require("../../scripts/dashboard-baseline-server.cjs");

const claimed = "synthetic-inbox-claimed";
const pending = "synthetic-inbox-pending";
const resolved = "synthetic-inbox-resolved";
const get = (path, scenario = "populated", org = ORG_A) => viewReply("GET", path, scenario, org);
const post = (path, body, scenario = "populated", org = ORG_A) => viewReply("POST", path, scenario, org, JSON.stringify(body));

test("QA asset override is explicit, local-only and disabled for immutable baseline mode", () => {
  assert.equal(resolveAssetRoot("untrusted-relative", false), defaultRoot);
  assert.equal(resolveAssetRoot(defaultRoot, true), defaultRoot);
  assert.throws(() => resolveAssetRoot("relative", true));
  assert.throws(() => resolveAssetRoot("//host/share", true));
  assert.throws(() => resolveAssetRoot("\\\\host\\share", true));
});

test("Neutral visual fixture leaves populated escalation reproduction intact", () => {
  const neutral = get("/api/inbox/tickets", "inbox-visual").body.tickets;
  assert.equal(neutral.length, 3);
  assert.ok(neutral.every((ticket) => !ticket.assigned_nome && !ticket.escalation_failed));
  const populated = get("/api/inbox/tickets").body.tickets;
  assert.equal(populated[0].assigned_nome, "Operatore QA");
  assert.equal(populated[1].escalation_failed, true);
});

test("Default baseline mode remains the original static fixture response", () => {
  for (const [method, path] of [["GET", "/api/auth/me"], ["GET", "/api/dashboard"], ["GET", "/api/inbox/tickets"], ["POST", "/api/inbox/claim/synthetic-inbox-pending"]]) {
    assert.deepEqual(interactionReply(method, path, "", false), baselineReply(method, path));
  }
});

test("Inbox QA cases and per-org reads contain only deterministic synthetic state", () => {
  for (const scenario of ["populated", "empty", "error", "loading", "denied", "staff", "inbox-ambiguous", "inbox-timeout", "inbox-reply-error"]) assert.ok(SCENARIOS.has(scenario));
  const a = get("/api/inbox/tickets?limit=100").body.tickets;
  const b = get("/api/inbox/tickets?limit=100", "populated", ORG_B).body.tickets;
  assert.equal(a.length, 3); assert.equal(b.length, 3);
  assert.deepEqual(a.map((item) => item.ticket_status), ["CLAIMED", "PENDING_STAFF", "RESOLVED"]);
  assert.equal(a[0].assigned_to, "synthetic-inbox-owner");
  assert.equal(a[1].priorita, "alta"); assert.equal(a[1].escalation_failed, true);
  assert.notEqual(a[0].organization_id, b[0].organization_id);
  assert.match(a[1].phone_number, /dashboard-qa\.invalid$/);
  assert.equal(get("/api/inbox/tickets?limit=100", "empty").body.tickets.length, 0);
  assert.equal(get("/api/inbox/tickets?limit=100", "error").status, 500);
  assert.equal(get("/api/inbox/tickets?limit=100", "denied").status, 403);
  assert.equal(get(`/api/inbox/tickets/${resolved}/messages?limit=200`).body.messages.length, 0);
  const message = get(`/api/inbox/tickets/${claimed}/messages?limit=200`).body.messages[0];
  assert.match(message.content_text, /ignora istruzioni/);
  assert.match(message.content_text, /example\.invalid/);
});

test("Inbox QA rejects unknown organizations, tickets, methods, IDs, and malformed action payloads", () => {
  assert.equal(get("/api/inbox/tickets?limit=100", "populated", "org-real").status, 403);
  assert.equal(get("/api/inbox/tickets/real-ticket/messages?limit=200").status, 404);
  assert.equal(viewReply("PUT", `/api/inbox/resolve/${claimed}`, "populated", ORG_A).status, 404);
  assert.equal(post(`/api/inbox/claim/${pending}`, { expected_version: 99 }).status, 404);
  assert.equal(post(`/api/inbox/assign/${claimed}`, { assigned_to: "real-user", expected_version: 1 }).status, 404);
  assert.equal(post(`/api/inbox/resolve/${claimed}`, { force: true }).status, 404);
  assert.equal(post("/api/inbox/reply/real-ticket", { content: "hi", message_type: "text", idempotency_key: "real-key-123" }).status, 404);
});

test("Inbox writes obey action payloads, authorization fixture roles, and organization scope", () => {
  assert.equal(post(`/api/inbox/claim/${pending}`, { expected_version: 1 }).status, 200);
  assert.equal(get("/api/inbox/tickets?limit=100").body.tickets.find((item) => item.id === pending).ticket_status, "CLAIMED");
  assert.equal(get("/api/inbox/tickets?limit=100", "populated", ORG_B).body.tickets.find((item) => item.id === pending).ticket_status, "PENDING_STAFF");
  assert.equal(post(`/api/inbox/assign/${claimed}`, { assigned_to: "synthetic-inbox-member", expected_version: 1 }, "staff").status, 404);
  assert.equal(post(`/api/inbox/claim/${pending}`, { expected_version: 1 }, "staff", ORG_B).status, 200);
  assert.equal(post(`/api/inbox/assign/${claimed}`, { assigned_to: "synthetic-inbox-member", expected_version: 1 }, "denied").status, 403);
  const after = get("/api/inbox/tickets?limit=100", "populated", ORG_A).body.tickets.find((item) => item.id === claimed);
  assert.equal(after.assigned_to, "synthetic-inbox-owner");
});

test("Inbox replies return outcome classes and append exactly one message per idempotency key", () => {
  const body = { content: "Synthetic reply", message_type: "text", idempotency_key: "qa-key-00000000" };
  const first = post(`/api/inbox/reply/${claimed}`, body);
  const duplicate = post(`/api/inbox/reply/${claimed}`, body);
  assert.deepEqual(first.body, { message_id: "synthetic-out-1", status: "sent" });
  assert.deepEqual(duplicate.body, first.body);
  const thread = get(`/api/inbox/tickets/${claimed}/messages?limit=200`).body.messages;
  assert.equal(thread.filter((message) => message.idempotency_key === body.idempotency_key).length, 1);
  assert.equal(thread.at(-1).id, first.body.message_id);
  assert.equal(thread.at(-1).status, first.body.status);
  assert.equal(post(`/api/inbox/reply/${claimed}`, { ...body, idempotency_key: "synthetic-key-ambiguous" }, "inbox-ambiguous").body.status, "sending_ambiguous");
  assert.equal(post(`/api/inbox/reply/${claimed}`, { ...body, idempotency_key: "synthetic-key-failure" }, "inbox-reply-error").status, 503);
  assert.equal(post(`/api/inbox/reply/${claimed}`, { ...body, idempotency_key: "synthetic-key-timeout" }, "inbox-timeout").status, 504);
});

test("Inbox request observations retain only safe action metadata", () => {
  post(`/api/inbox/assign/${claimed}`, { assigned_to: "synthetic-inbox-member", expected_version: 1 });
  const last = safeRequests.at(-1);
  assert.deepEqual(Object.keys(last).sort(), ["body_fields", "method", "organization_id", "path", "status"]);
  assert.deepEqual(last.body_fields, ["assigned_to", "expected_version"]);
  assert.ok(!JSON.stringify(last).includes("Synthetic reply"));
  assert.ok(!JSON.stringify(last).includes("cookie"));
  assert.ok(orgStates.get(ORG_A).tickets.every((item) => item.organization_id === ORG_A));
});
