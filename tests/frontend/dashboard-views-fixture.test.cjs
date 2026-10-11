const { test } = require("node:test");
const assert = require("node:assert/strict");
const { viewReply } = require("./fixtures/dashboard-views-qa.cjs");
const { ORG_A, ORG_B } = require("./fixtures/dashboard-visual-api.cjs");
const { reply } = require("./fixtures/dashboard-visual-api.cjs");
const { interactionReply } = require("../../scripts/dashboard-baseline-server.cjs");

test("view QA cases are explicit, synthetic and scoped; unknown cases/writes have no reply", () => {
  assert.equal(viewReply("GET", "/api/dashboard", "unknown"), null);
  assert.equal(viewReply("POST", "/api/arbitrary-write", "populated"), null);
  assert.equal(viewReply("POST", "/api/recensioni/real-record/approva", "populated"), null);
  assert.equal(viewReply("GET", "/api/dashboard", "populated", "foreign").status, 403);
  assert.match(viewReply("GET", "/api/dashboard", "populated", ORG_A).body[0].testo_originale, /QA A/);
  assert.match(viewReply("GET", "/api/dashboard", "populated", ORG_B).body[0].testo_originale, /QA B/);
});
test("view QA supplies observable loading, empty, error, denied and non-operational provider states", () => {
  assert.equal(viewReply("GET", "/api/recensioni", "loading").delayMs, 3000);
  assert.deepEqual(viewReply("GET", "/api/recensioni", "empty").body, { recensioni: [] });
  assert.equal(viewReply("GET", "/api/recensioni", "error").status, 500);
  assert.equal(viewReply("GET", "/api/recensioni", "denied").status, 403);
  assert.deepEqual(viewReply("GET", "/api/reviews/google/status", "populated").body.operational, false);
});

test("Settings and AI Settings scenarios remain synthetic, org-specific, and fail closed by role", () => {
  assert.deepEqual(viewReply("GET", "/api/impostazioni/organizzazione", "settings-populated").body.timezone_disponibili,
    ["Europe/Rome", "Europe/London", "America/New_York"]);
  assert.equal(viewReply("PUT", "/api/impostazioni/organizzazione", "settings-populated").status, 200);
  assert.equal(viewReply("GET", "/api/billing/subscription", "settings-staff").status, 403);
  assert.equal(viewReply("GET", "/api/billing/subscription", "settings-populated").body.plan, "pro");
  assert.equal(viewReply("GET", "/api/impostazioni/organizzazione", "settings-error").status, 500);
  assert.equal(viewReply("GET", "/api/impostazioni/organizzazione", "settings-denied").status, 403);
  assert.equal(viewReply("GET", "/api/onboarding/profilo", "ai-settings-populated", ORG_A).body.profilo.nome_attivita, "Ristorante QA A");
  assert.equal(viewReply("GET", "/api/onboarding/profilo", "ai-settings-org-b", ORG_B).body.profilo.nome_attivita, "Ristorante QA B");
  assert.equal(viewReply("POST", "/api/onboarding/profilo", "ai-settings-staff").status, 403);
  assert.equal(viewReply("GET", "/api/onboarding/profilo", "ai-settings-error").status, 500);
  assert.equal(viewReply("GET", "/api/onboarding/profilo", "ai-settings-denied").status, 403);
  assert.equal(viewReply("POST", "/api/onboarding/profilo", "ai-settings-populated").body.profilo.nome_attivita, "Ristorante QA A");
});

test("connected Settings dialog fixture accepts only the fixed synthetic Calendar disconnect", () => {
  assert.deepEqual(viewReply("GET", "/api/calendar/status", "settings-connected").body, {
    connected: true, calendar_id: "synthetic-calendar-qa@dashboard-qa.invalid", sync_enabled: true,
  });
  assert.deepEqual(viewReply("DELETE", "/api/calendar/disconnect", "settings-connected").body, { ok: true, disconnected: true });
  assert.equal(viewReply("DELETE", "/api/calendar/disconnect", "settings-populated"), null);
  assert.equal(viewReply("DELETE", "/api/calendar/arbitrary", "settings-connected"), null);
});

test("Knowledge view reads are synthetic, organization-scoped, and only fixed actions are accepted", () => {
  const summary = viewReply("GET", "/api/conoscenza/summary", "populated");
  assert.equal(summary.body.faq.totale, 1);
  assert.equal(summary.body.documenti.totale, 1);
  assert.match(viewReply("GET", "/api/documenti/elenco?tipo=faq", "populated", ORG_A).body.documenti[0].id, /^synthetic-/);
  assert.match(viewReply("GET", "/api/documenti/elenco?tipo=web", "populated", ORG_B).body.documenti[0].nome, /B/);
  assert.equal(viewReply("POST", "/api/conoscenza/faq", "populated").status, 201);
  assert.equal(viewReply("POST", "/api/documenti/carica", "populated").body.id, "synthetic-document-qa");
  assert.equal(viewReply("POST", "/api/documenti/carica-file", "populated").status, 201);
  assert.equal(viewReply("POST", "/api/conoscenza/web", "populated").body.id, "synthetic-web-qa");
  const answer = viewReply("POST", "/api/documenti/chiedi", "populated");
  assert.equal(answer.status, 200);
  assert.deepEqual(Object.keys(answer.body.fonti[0]).sort(), ["documento", "priorita", "score", "stato", "tipo"]);
  assert.equal(viewReply("PUT", "/api/conoscenza/dati-struttura", "populated").status, 200);
  assert.equal(viewReply("POST", "/api/conoscenza/dati-struttura", "populated"), null);
  assert.equal(viewReply("DELETE", "/api/conoscenza/faq/arbitrary-id", "populated"), null);
  assert.equal(viewReply("PATCH", "/api/documenti/arbitrary-id/toggle", "populated"), null);
  assert.equal(viewReply("POST", "/api/arbitrary-write", "populated"), null);
  assert.equal(viewReply("POST", "/api/conoscenza/faq", "staff").status, 403);
  assert.equal(viewReply("POST", "/api/conoscenza/faq", "mfa-required").body.code, "mfa_required");
  assert.deepEqual(viewReply("GET", "/api/documenti/elenco", "empty").body.documenti, []);
  assert.equal(viewReply("GET", "/api/conoscenza/summary", "loading").delayMs, 3000);
  assert.equal(viewReply("GET", "/api/conoscenza/summary", "error").status, 500);
  assert.equal(viewReply("GET", "/api/conoscenza/summary", "denied").status, 403);
});

test("Team view has capacity metadata and fixed synthetic member/invitation actions", () => {
  const team = viewReply("GET", "/api/team/members", "populated").body;
  assert.equal(team.total, 2);
  assert.equal(team.users_limit, 3);
  assert.equal(team.can_add_more, true);
  assert.ok(team.members.every((member) => member.user_id.startsWith("synthetic-")));
  assert.match(viewReply("GET", "/api/team/invitations", "populated").body.invitations[0].email, /\.invalid$/);
  const invite = viewReply("POST", "/api/team/members", "populated");
  assert.equal(invite.body.id, "synthetic-invitation-qa");
  assert.match(invite.body.email, /\.invalid$/);
  assert.equal(viewReply("POST", "/api/team/invitations/synthetic-invitation-qa/resend", "populated").status, 200);
  assert.equal(viewReply("DELETE", "/api/team/invitations/synthetic-invitation-qa", "populated").status, 200);
  assert.equal(viewReply("PATCH", "/api/team/members/synthetic-member-qa", "populated").status, 200);
  assert.equal(viewReply("DELETE", "/api/team/members/synthetic-member-qa", "populated").status, 200);
  assert.equal(viewReply("DELETE", "/api/team/members/arbitrary-user", "populated"), null);
  for (const scenario of ["staff", "mfa-required"]) {
    assert.equal(viewReply("POST", "/api/team/members", scenario).status, 403);
    assert.equal(viewReply("DELETE", "/api/team/invitations/synthetic-invitation-qa", scenario).status, 403);
  }
  assert.equal(viewReply("GET", "/api/auth/me", "staff").body.ruolo, "staff");
  assert.equal(viewReply("GET", "/api/auth/me", "mfa-required").body.aal, "aal1");
  assert.equal(viewReply("GET", "/api/team/members", "empty").body.total, 0);
  assert.equal(viewReply("GET", "/api/team/members", "loading").delayMs, 3000);
  assert.equal(viewReply("GET", "/api/team/members", "error").status, 500);
});

test("simulator returns canned normal, hostile, error and timeout outcomes without a provider", () => {
  const normal = viewReply("POST", "/api/messaggio", "populated");
  assert.match(normal.body.risposta, /nessun modello o servizio esterno/);
  const hostile = viewReply("POST", "/api/messaggio", "simulator-hostile");
  assert.equal(hostile.body.richiede_umano, true);
  assert.equal(hostile.body.risposta, "<img src=x onerror=alert(1)><script>QA_HOSTILE</script>");
  assert.equal(viewReply("POST", "/api/messaggio", "simulator-error").status, 503);
  const timeout = viewReply("POST", "/api/messaggio", "simulator-timeout");
  assert.equal(timeout.status, 504);
  assert.equal(timeout.delayMs, 3000);
  assert.match(timeout.body.detail, /Timeout/);
  assert.equal(viewReply("POST", "/api/messaggio", "loading").delayMs, 3000);
  assert.equal(viewReply("POST", "/api/messaggio/other", "populated"), null);
});

test("bookings QA is opt-in and keeps the ordinary baseline booking fixture unchanged", () => {
  assert.deepEqual(interactionReply("GET", "/api/bookings", "", false), reply("GET", "/api/bookings"));
  assert.equal(viewReply("GET", "/api/bookings", undefined), null);
  const populated = viewReply("GET", "/api/bookings", "populated", ORG_A);
  assert.equal(populated.status, 200);
  assert.equal(populated.body.length, 2);
  assert.deepEqual(populated.body.map((item) => [item.data, item.ora, item.coperti, item.stato]), [
    ["2026-10-08", "20:00", 2, "in_attesa"],
    ["2026-10-08", "20:00", 2, "confermata"],
  ]);
  assert.notEqual(populated.body[0].id, viewReply("GET", "/api/bookings", "populated", ORG_B).body[0].id);
  assert.match(populated.body[0].nome_cliente, /QA A/);
  assert.match(viewReply("GET", "/api/bookings", "populated", ORG_B).body[0].nome_cliente, /QA B/);
  assert.deepEqual(viewReply("GET", "/api/bookings", "empty").body, []);
  assert.equal(viewReply("GET", "/api/bookings", "loading").delayMs, 3000);
  assert.equal(viewReply("GET", "/api/bookings", "error").status, 500);
  assert.equal(viewReply("GET", "/api/bookings", "booking-timeout").status, 504);
  assert.equal(viewReply("GET", "/api/bookings/settings", "populated").body.capienze_orarie["20:00"], 12);
  assert.equal(viewReply("GET", "/api/bookings/semaforo?data=2026-10-08", "populated").body[0].coperti_liberi, 8);
  assert.deepEqual(viewReply("GET", "/api/bookings/semaforo?data=2026-10-09", "populated").body, []);
});

test("booking QA only accepts fixed synthetic create/update, capacity and status actions", () => {
  const prefix = "/api/bookings/synthetic-booking-pending-A/";
  for (const [action, status] of Object.entries({
    confirm: "confermata", reject: "rifiutata", cancel: "cancellata",
    "mark-no-show": "no_show", "mark-completed": "completata",
  })) {
    const result = viewReply("POST", `${prefix}${action}`, "populated", ORG_A);
    assert.equal(result.status, 200);
    assert.equal(result.body.stato, status);
    assert.match(result.body.id, /^synthetic-booking-/);
  }
  assert.equal(viewReply("POST", "/api/bookings", "populated").status, 201);
  assert.equal(viewReply("PUT", "/api/bookings/synthetic-booking-pending-A", "populated").status, 200);
  assert.equal(viewReply("PUT", "/api/bookings/settings", "populated").status, 200);
  assert.equal(viewReply("POST", "/api/bookings/settings", "populated"), null);
  assert.equal(viewReply("POST", "/api/bookings/synthetic-booking-pending-A/erase", "populated"), null);
  assert.equal(viewReply("POST", "/api/bookings/real-id/confirm", "populated"), null);
  assert.equal(viewReply("PATCH", "/api/bookings/synthetic-booking-pending-A", "populated"), null);
  assert.equal(viewReply("POST", "/api/bookings/synthetic-booking-pending-B/confirm", "populated", ORG_A), null);
  assert.equal(viewReply("POST", `${prefix}confirm`, "staff").status, 403);
  assert.equal(viewReply("GET", "/api/bookings", "staff").status, 200);
  assert.equal(viewReply("GET", "/api/bookings/semaforo?data=2026-10-08", "staff").status, 200);
  assert.equal(viewReply("GET", "/api/bookings/settings", "staff").status, 403);
  assert.equal(viewReply("PUT", "/api/bookings/settings", "staff").status, 403);
  assert.equal(viewReply("GET", "/api/auth/me", "staff").body.ruolo, "staff");
  assert.equal(viewReply("POST", "/api/bookings", "staff").status, 403);
  assert.equal(viewReply("PUT", "/api/bookings/synthetic-booking-pending-A", "staff").status, 403);
  assert.equal(viewReply("POST", `${prefix}confirm`, "mfa-required").body.code, "mfa_required");
  assert.equal(viewReply("GET", "/api/bookings", "mfa-required").status, 200);
  assert.equal(viewReply("GET", "/api/bookings/settings", "mfa-required").status, 200);
  assert.equal(viewReply("PUT", "/api/bookings/settings", "mfa-required").body.code, "mfa_required");
  assert.equal(viewReply("GET", "/api/bookings/synthetic-booking-pending-A", "populated"), null);
});
